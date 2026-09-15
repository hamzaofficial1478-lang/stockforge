"""The orchestrator.

One design at a time, all the way through:

    source -> flatten -> analyse -> derive -> render -> critique -> export

Resumable at every step. Kill it and re-run; finished designs are skipped.

Two gates decide where a design ends up, and neither is a setting you can turn
off:

  provenance   a design assembled from third-party library content produces an
               editable master and nothing else. It never reaches a publish
               queue. You get your file back; the agency never sees it.

  distinctness a derived design that still reads as a copy of the one it learned
               from goes back for another round, and then to you. Near-duplicates
               are what get contributor accounts closed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from collections.abc import Callable
from dataclasses import replace

from .config import Settings, settings as default_settings
from .db import FIT_TO_LEARN_FROM, Store
from pydantic import ValidationError

from .schema import DesignSpec
from .sources import Design, Source
from .stages import critique as critique_stage
from . import collections as collections_stage
from .stages import batch as batch_stage
from .stages import compose as compose_stage
from .stages import derive as derive_stage
from .stages import export as export_stage
from .stages import fonts as fonts_stage
from .stages import duplicates as duplicates_stage
from .stages import ingest as ingest_stage
from .stages import invent as invent_stage
from .stages import motifs as motifs_stage
from .stages.analyse import analyse
from .stages.render import render, write_svg

log = logging.getLogger("stockforge")

# Below this much of its intended size, a line has stopped being a fitting
# problem and become a design fault. See where it is used.
CRAMPED = 0.65


def _seed(*parts: object) -> int:
    """A seed that is the same next week as it is today.

    Python salts str hashing per process, so `hash(design_id)` gives a
    different number every run. Compose records a recipe so you can re-run a
    mix you liked with one ingredient swapped — that promise needs a seed that
    does not move when the interpreter restarts.
    """
    blob = ":".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(blob).digest()[:4], "big")


def out_dir_for(root: Path, design_id: str, niche: str | None = None) -> Path:
    """Where a design's finished files live.

    Under out/<niche>/<id> now, and under out/<id> before niches existed. One
    definition because four places looked for them and a build that wrote to a
    path nothing read from is indistinguishable from a build that produced
    nothing.
    """
    legacy = root / "out" / design_id[:16]
    if niche:
        return root / "out" / niche / design_id[:16]
    hit = next((p for p in sorted((root / "out").glob(f"*/{design_id[:16]}"))
                if p.is_dir()), None)
    return hit or legacy


def _new_id(kind: str, fingerprint: str) -> str:
    """An id for a design the program made rather than read.

    Derived from the recipe, so the same combination would land on the same id
    — which is a second line of defence: even if the ledger were lost, a repeat
    would collide rather than quietly become a new row.
    """
    return hashlib.sha256(f"{kind}:{fingerprint}".encode()).hexdigest()[:24]


class Pipeline:
    def __init__(self, cfg: Settings | None = None, on_progress: Callable[[str], None] | None = None):
        self.on_progress = on_progress or (lambda step: None)
        self.cfg = cfg or default_settings
        self.cfg.ensure_dirs()
        # Inkscape and cairo find a font by family name through fontconfig, so
        # our own folder has to be on its search path before anything renders.
        fonts_stage.activate(self.cfg.fonts_dir)
        self.store = Store(self.cfg.db_path)

    # -- 1. pull designs in -----------------------------------------------

    def pull(self, source: Source, owned: bool = True) -> int:
        """Flatten every image of every design and record it. No models yet.

        `owned` is whether these are the owner's own designs. Only what is
        yours may lend an ingredient to anything made: every ingredient in a
        delivered file being your own is precisely what makes the file safe to
        sell. Anything else comes in as reference — read, listed, looked at,
        and never mixed into a design.
        """
        count = 0
        for design in source.designs():
            flats: list[Path] = []
            for image in design.images:
                # Hashing the bytes is far cheaper than decoding, warping and
                # rewriting an image we already hold, and a re-pull is mostly
                # images we already hold.
                try:
                    known = self.store.conn.execute(
                        "SELECT * FROM assets WHERE id=? LIMIT 1",
                        (ingest_stage.sha256_file(image),)).fetchone()
                except OSError as exc:
                    log.warning("skipped %s: %s", image.name, exc)
                    continue
                if known and known["flat_path"] and Path(known["flat_path"]).exists():
                    # Flattened already, for this design or for another one.
                    # Record that it belongs to this design as well: a shared
                    # size chart or mockup backdrop belongs to every listing
                    # that uses it, not just the first one pulled.
                    self.store.add_asset(
                        id=known["id"], src_path=str(image), flat_path=known["flat_path"],
                        width=known["width"], height=known["height"],
                        aspect=known["aspect"], phash=known["phash"],
                        is_mockup=known["is_mockup"], design_id=design.stable_id,
                        trim=known["trim"], trim_note=known["trim_note"],
                        state="ingested",
                    )
                    flats.append(Path(known["flat_path"]))
                    continue

                try:
                    flat = ingest_stage.flatten_image(image, self.cfg.root / "flats")
                except Exception as exc:
                    log.warning("skipped %s: %s", image.name, exc)
                    continue
                self.store.add_asset(
                    id=flat.asset_id, src_path=str(image), flat_path=str(flat.flat_path),
                    width=flat.width, height=flat.height, aspect=flat.aspect,
                    phash=flat.phash, is_mockup=int(flat.is_mockup),
                    trim=flat.trim, trim_note=flat.note,
                    design_id=design.stable_id, state="ingested",
                )
                flats.append(flat.flat_path)

            if not flats:
                continue
            self.store.add_design(
                id=design.stable_id, design_key=design.design_id, title=design.title,
                tags=json.dumps(design.tags), listing_url=design.listing_url,
                source=design.source, image_count=len(flats), state="pending",
                collection=self.cfg.collection or "unfiled",
                owned=1 if owned else 0,
            )
            count += 1
            log.info("pulled %s (%d images)", design.design_id[:60], len(flats))
        return count

    # -- 2-6. work one design ----------------------------------------------

    def build(self, design_id: str, reread: bool = False) -> str:
        """One design, end to end.

        The analyser's read is kept and reused. It used to be re-made on every
        build, so pressing Run again on a design in Review spent five model
        calls repeating work already sitting in the database — about twenty
        minutes, to arrive at the answer it already had. The read is what a
        model saw in the artwork, and the artwork has not changed; what a retry
        is actually retrying is everything after it.

        `reread` forces a fresh one, for when the flattening or the prompts
        have changed underneath a stored read.
        """
        self.on_progress("Preparing source images")
        row = self.store.conn.execute(
            "SELECT * FROM designs WHERE id=?", (design_id,)
        ).fetchone()
        if row is None:
            return "failed"

        # True flat exports first, then the ones recovered from a staged
        # photograph, each group largest first. Ingest works out which is which
        # and nothing has ever used the answer — so a listing whose photograph
        # happened to be the biggest file was read through the photograph.
        assets = self.store.conn.execute(
            "SELECT * FROM assets WHERE design_id=? ORDER BY is_mockup ASC, width DESC",
            (design_id,),
        ).fetchall()
        usable = [a for a in assets if a["flat_path"]]
        images = [Path(a["flat_path"]) for a in usable]
        if not images:
            self.store.queue_review(design_id, "no usable source images", 0.0)
            self.store.set_design_state(design_id, "failed")
            return "failed"
        mockups = {i for i, a in enumerate(usable) if a["is_mockup"]}

        # --- read it --------------------------------------------------
        spec = None
        if not reread:
            kept = self.store.get_read(design_id)
            if kept:
                try:
                    spec = DesignSpec.model_validate(kept)
                    self.on_progress("Using the reading already on file — no model needed")
                    log.info("[%s] reusing the stored read", design_id[:8])
                except ValidationError as exc:
                    log.info("[%s] the stored read no longer fits the schema, "
                             "reading again: %s", design_id[:8], exc)
        if spec is None:
            spec = analyse(
                images, asset_id=usable[0]["id"], design_id=design_id,
                listing_url=row["listing_url"], mockups=mockups,
                on_progress=self.on_progress,
            )
        # Point every decorative element at a drawing in our own library. What
        # nothing matches stays unresolved on purpose — the renderer reports it
        # as a hole and the description tells you what to draw next.
        self.on_progress("Matching artwork to the motif library")
        holes = motifs_stage.resolve(spec, self.cfg.motifs_dir, self.cfg.motif_threshold)
        if holes:
            log.info("[%s] no motif for: %s", design_id[:8], "; ".join(sorted(set(holes))[:3]))

        read = spec.model_dump(mode="json")
        self.store.save_spec(design_id, read)
        # And keep it, because everything below replaces it and the read is
        # what the analysis prompts are judged on.
        self.store.save_read(design_id, read)
        self.store.set_design_state(design_id, "analysed", spec.provenance.stock_safe)

        if not spec.pages:
            self.store.queue_review(design_id, "no printed surface identified", 0.0)
            return "review"

        if self.cfg.preserve_original:
            # Recovery exports the analyser's read without rewriting names,
            # borrowing decoration, or rejecting it for resembling its source.
            return self._export(spec, design_id, distinct=0.0, master_only=True)

        # --- make it our own ------------------------------------------
        # Mix first, then derive. Mixing takes the grid from one of your
        # designs, the palette from another and the decoration from a third,
        # so the result has no single original; deriving then moves that
        # result on its own terms. Mixing does the heavier lifting.
        source_flat = images[0]
        pool = self._spec_pool(exclude=design_id)

        # --- the learning phase ---------------------------------------
        # A niche is read before it is drawn from. Until it holds a catalogue,
        # every design that comes in is an ingredient and nothing else: read it
        # properly, file its palette, its grid and its type, note the motifs we
        # cannot draw yet, hand the owner their editable master, and stop.
        #
        # This used to start varying at design one. With two read, every round
        # borrowed from the same single donor, so the second draft was the
        # first with its hue nudged — a real run scored 0.10 twice and went to
        # review saying it still read as a copy. That was true, and no number
        # of rounds was going to change it. The cost was three model calls and
        # four minutes per design, and a review queue filled with designs whose
        # only fault was that there had been nothing to build them from.
        #
        # So: twenty-four read, and the twenty-fifth is the first design worth
        # looking at. The count is the pool rather than the niche total, which
        # is the same rule read from the other end — the twenty-fifth design is
        # exactly the first one with twenty-four others behind it.
        if len(pool) < self.cfg.seed_designs:
            niche = self._niche_folder(design_id)
            read_in = self._collection_counts(niche)["read"]
            short = self.cfg.seed_designs - len(pool)
            log.info("[%s] learning — %d read in %s, %d to mix from, %d short",
                     design_id[:8], read_in, niche, len(pool), short)
            spec.warnings.append(
                f"read and filed, not varied — this is the learning phase. "
                f"{read_in} of the {self.cfg.seed_designs} designs this niche needs "
                f"have been read. You have the editable master of this one. Read "
                f"{short} more and the program starts making designs.")
            done = self._export(spec, design_id, distinct=0.0,
                                master_only=True, learning=True)
            self._harvest_if_seeded(niche, read_in)
            return done

        derived = spec
        distinct = 0.0
        recipe = None

        # Per design, falling back to the global setting. One number for five
        # thousand designs means the careful ones and the throwaway ones get
        # the same treatment, and there was no way to say "leave this one
        # alone" short of changing the setting between runs.
        mix = row["mix"] if row["mix"] is not None else self.cfg.mix
        derive_strength = (row["derive"] if row["derive"] is not None
                           else self.cfg.derive_strength)
        log.info("[%s] borrowing %.0f%%, shifting %.0f%%",
                 design_id[:8], mix * 100, derive_strength * 100)

        for round_ in range(1, self.cfg.max_derive_rounds + 1):
            self.on_progress(
                f"Creating and checking variation {round_} "
                f"({mix:.0%} borrowed, {derive_strength:.0%} shifted)")
            strength = derive_strength * round_
            base = spec
            if pool and mix > 0:
                base, recipe = compose_stage.compose(
                    spec, pool,
                    mix=min(1.0, mix * round_),
                    seed=_seed("mix", design_id, round_),
                )
                log.info("[%s] mixed: %s", design_id[:8], recipe.summary())
            # Seeded from the design, not just the round. Seeding on the
            # round alone gave every design in the catalogue the identical
            # derivation — the same hue rotation, the same weight jitter — so
            # five thousand pieces got one transformation between them, which
            # is the opposite of what this stage is for.
            derived = derive_stage.derive(base, strength=strength,
                                          seed=_seed("derive", design_id, round_))

            distinct, verdict, fault = self._check_every_surface(
                derived, design_id, source_flat)
            if fault:
                log.warning("[%s] %s", design_id[:8], fault)
                self.store.queue_review(design_id, fault, 0.0)
                return "review"
            log.info("[%s] round %d: worst distinct=%.2f -> %s",
                     design_id[:8], round_, distinct, verdict)

            if verdict == "too_far":
                derived = derive_stage.derive(
                    base, strength=strength * 0.5, seed=_seed("derive", design_id, round_))
                break
            if verdict == "ship":
                break
        else:
            self.store.queue_review(
                design_id, "still reads as a copy after every derivation round", distinct
            )
            return "review"

        # --- polish it -------------------------------------------------
        for round_ in range(self.cfg.max_critique_rounds):
            self.on_progress(f"Checking layout quality, round {round_ + 1}")
            patches, escalated = [], None
            # Every surface again. A wedding suite is five separate files and
            # the critic had only ever seen the first of them.
            for index, page in enumerate(derived.pages):
                preview = self._render_preview(derived, design_id, page=index)
                if preview is None:
                    continue
                crit = critique_stage.critique(
                    self._source_for(page, source_flat), preview, derived, page_index=index)
                if crit.verdict == "escalate":
                    escalated = (crit.commentary or f"critic escalated on '{page.name}'",
                                 min(crit.similarity, crit.polish))
                    break
                if crit.verdict != "ship":
                    patches.extend(crit.patches)

            if escalated:
                self.store.queue_review(design_id, *escalated)
                return "review"
            if not patches:
                break

            patched, failed = critique_stage.apply_patches(
                derived.model_dump(mode="json"), patches)
            for f in failed:
                log.warning("[%s] patch did not apply — %s", design_id[:8], f)
            try:
                derived = DesignSpec.model_validate(patched)
            except Exception as exc:
                log.warning("[%s] patched spec invalid, keeping previous: %s", design_id[:8], exc)
                break

        return self._export(derived, design_id, distinct)

    def _export(self, derived: DesignSpec, design_id: str, distinct: float,
                master_only: bool = False, on_twin: str = "review",
                learning: bool = False) -> str:
        """Export every page, including imperfect recoveries that need editing.

        `learning` marks a design being read in to seed a niche rather than
        shipped. A gap in our own library is a finding then, not a fault —
        see where it is used.
        """
        out_dir = out_dir_for(self.cfg.root, design_id, self._niche_folder(design_id))
        holes: list[str] = []
        typeless: list[str] = []
        toothless: list[str] = []
        cramped: list[str] = []
        unrendered: list[str] = []
        twins: list[duplicates_stage.Twin] = []
        # A format that did not come out. Not a judgement call for the review
        # queue — the tool that writes it is missing — so it fails the design
        # rather than asking a human to look at a file that is not there.
        undeliverable: list[str] = []
        not_for_stock: list[str] = []

        used_names: set[str] = set()
        for i, page in enumerate(derived.pages):
            self.on_progress(f"Matching fonts and drawing page {i + 1}/{len(derived.pages)}: {page.name}")
            # Model-generated labels are display text, never filesystem paths.
            name = re.sub(r"[^\w-]+", "-", page.name, flags=re.ASCII).strip("-")[:70] or "page"
            unique = name
            suffix = 2
            while unique.casefold() in used_names:
                unique = f"{name}-{suffix}"
                suffix += 1
            used_names.add(unique.casefold())
            stem = f"{design_id[:16]}-{unique}"
            # The delivered file carries the bleed; the previews above did not,
            # because a preview is judged against the source artwork and should
            # be the same view of the piece.
            result = render(derived, self.cfg.fonts_dir, self.cfg.motifs_dir,
                            page_index=i, bleed_mm=page.canvas.bleed_mm)
            svg = write_svg(result, self.cfg.root / "renders" / f"{stem}.svg")
            holes.extend(result.missing_motifs)
            # Type nothing in the library could answer. The SVG carries the
            # generic "serif" for it, so whatever the machine happens to have
            # gets drawn — which is not a design anyone chose.
            typeless.extend(result.unmatched_fonts)
            toothless.extend(result.missing_glyphs)
            unrendered.extend(f"'{page.name}': {u}" for u in result.unrendered)
            for label, scale in result.refits:
                log.info("[%s] %s set at %.0f%% to fit its box",
                         design_id[:8], label, scale * 100)
                # A line that had to lose a third of its size is not a fitting
                # problem any more, it is a size the analyser misread. The
                # critic cannot fix it either — it would ask for bigger type
                # and get it shrunk straight back — so it wants a human.
                if scale < CRAMPED:
                    cramped.append(f"{label} at {scale:.0%} of its intended size")
            self.on_progress(f"Exporting editable PDF, SVG and preview for page {i + 1}/{len(derived.pages)}")
            exported = export_stage.export_all(
                svg, out_dir, stem=stem,
                preview_px=self.cfg.preview_px)

            # Does this look like something we already made? The distinctness
            # check earlier only ever compared this design to its own source.
            # Nothing has compared design four hundred to design twelve, and a
            # batch flagged as a near-duplicate on submission is rejected as a
            # batch.
            # The preview is drawn by cairo and turns up whatever happens, so
            # reading only the preview made a failed export look like a good
            # one. Check the files the design actually exists to produce.
            why = "; ".join(exported.failures) or "no reason given"
            if exported.master_pdf is None:
                undeliverable.append(f"'{page.name}': {why}")
            elif exported.stock_eps is None:
                not_for_stock.append(f"'{page.name}': {why}")

            twin = None if master_only else self._check_for_a_twin(design_id, page.name, exported.preview_jpg)
            if twin:
                twins.append(twin)
            with self.store.tx() as c:
                # The columns for these have existed since the first schema
                # and nothing ever filled them, so the database knew a design
                # had been built and not where anything it produced had gone.
                c.execute(
                    "INSERT INTO builds (design_id, page_name, svg_path, pdf_path, "
                    "preview_path, state, created_at) "
                    "VALUES (?,?,?,?,?,?,strftime('%s','now'))",
                    (design_id, page.name, str(svg),
                     str(exported.master_pdf) if exported.master_pdf else None,
                     str(exported.preview_jpg) if exported.preview_jpg else None,
                     "built"),
                )

        self.store.save_spec(design_id, derived.model_dump(mode="json"),
                             round_=1, distinct=distinct, verdict="built")

        # Nothing to hand over means the run did not succeed, whatever the
        # rest of it says. Left as "ready" this counted towards
        # ready_to_publish and published nothing, because publish looks for
        # files on disk and there were none.
        if undeliverable:
            why = "export produced no file — " + "; ".join(undeliverable[:3])
            self.store.queue_review(design_id, why, distinct)
            self.store.set_design_state(design_id, "failed")
            log.error("[%s] %s", design_id[:8], why)
            return "failed"

        reasons: list[str] = []

        # While a niche is being read in, a gap in our library is a finding and
        # not a fault. Twenty-four designs each queueing "no library match for
        # a jack-o'-lantern" is twenty-four rows saying one thing, and that one
        # thing is already on the motif-gaps list ranked by how many designs
        # are waiting on it. Drawing from that list is the work; clearing the
        # rows is not, and a review queue that long is read by nobody.
        #
        # What still queues is anything that means the READ is wrong — a photo
        # we could not find the artwork in, a surface that came back as placed
        # pixels, type that would not fit the box it was given. Those are the
        # ones that poison the pool, and the pool is the entire point of
        # reading these in.
        if learning and (holes or typeless or toothless or unrendered):
            log.info("[%s] library gaps noted while learning: %s", design_id[:8],
                     "; ".join(sorted(set(holes + typeless + toothless))[:5]) or "—")
            holes, typeless, toothless, unrendered = [], [], [], []

        # A photo we could not find the artwork inside. Everything after this
        # point measured itself against the whole photograph — the trim, the
        # text positions, the aspect check — so it is the first thing to say,
        # because every other complaint about this design is downstream of it.
        unsure = self.store.conn.execute(
            "SELECT trim_note FROM assets WHERE design_id=? AND trim='unsure' "
            "AND trim_note IS NOT NULL AND trim_note != '' LIMIT 1", (design_id,)
        ).fetchone()
        if unsure:
            reasons.append("the artwork was not found inside the listing photo — "
                           + unsure["trim_note"])

        # The failure that looks most like success: the model reads the surface
        # as one photographic area, the renderer places the original pixels
        # faithfully, and the text is set beside them. What comes out is the
        # source image with words next to it. Every other check passes, because
        # nothing else asks whether anything was actually redrawn.
        photocopied = derived.photocopied_pages(self.cfg.max_raster)
        if photocopied:
            worst = max(share for _, share in photocopied)
            reasons.append(
                f"not rebuilt — {worst:.0%} of "
                + ("this surface is" if len(photocopied) == 1
                   else f"{len(photocopied)} surfaces are")
                + " placed photograph, so what came out is the original picture "
                  "with the text set beside it, not a redrawn design"
            )
        if holes:
            reasons.append("no library match for: " + "; ".join(sorted(set(holes))[:5]))
        if typeless:
            reasons.append("no font in the library for: "
                           + "; ".join(sorted(set(typeless))[:5]))
        if toothless:
            reasons.append("the font has no glyph for: "
                           + "; ".join(sorted(set(toothless))[:3]))
        if cramped:
            reasons.append("type does not fit its box: " + "; ".join(cramped[:3]))
        if unrendered:
            reasons.append("nothing here can draw " + "; ".join(unrendered[:3]))
        if twins:
            # A batch throws a near-repeat away and tries another combination.
            # Queueing it would hand back a pile of things that look the same
            # and ask the owner to sort it out, which is the job.
            if on_twin == "discard":
                log.info("[%s] discarded as a near-repeat: %s",
                         design_id[:8], twins[0].line())
                return "twin"
            reasons.append("; ".join(t.line() for t in twins[:3]))
        if reasons:
            self.store.queue_review(design_id, " — ".join(reasons), distinct)
            self.store.set_design_state(design_id, "review")
            return "review"

        # No EPS is no stock submission: the agencies take EPS, and publish
        # gathers what to send by globbing for one.
        state = "ready" if not master_only and (derived.publishable or self.cfg.publish_all) else "master_only"
        if not_for_stock and state == "ready":
            state = "master_only"
            log.warning("[%s] master only — %s", design_id[:8], "; ".join(not_for_stock[:2]))
        self.store.set_design_state(design_id, state)
        if state == "master_only":
            log.info("[%s] editable master only — %s", design_id[:8], derived.provenance.reason)
        elif not derived.publishable:
            log.info("[%s] flagged (%s) but cleared by SF_PUBLISH_ALL",
                     design_id[:8], derived.provenance.reason)
        return state

    def _specs(self, exclude: str | None = None, cap: int | None = None,
               collection: str | None = None, donors_only: bool = False) -> list[DesignSpec]:
        """Every design already read, newest first.

        A spec that will not load is said out loud. Swallowing it silently is
        how an empty donor pool looks exactly like a catalogue of one.

        `collection` is the wall between niches. It is a join rather than a
        filter applied afterwards, so a design with no niche on it cannot leak
        into one — an unfiled Halloween card lending its palette to a business
        card is the exact mistake this exists to stop.
        """
        params: list = []
        where: list[str] = []
        joined = bool(collection or donors_only)
        if collection:
            where.append("d.collection = ?")
            params.append(collection)
        if donors_only:
            # Two rules, and both are about what an ingredient may be.
            #
            # It has to be a design somebody actually looked at. A design this
            # program made has a spec but no reading, and mixing from those
            # compounds: the third run is a mix of mixes of mixes and drifts
            # away from anything you chose. Measured — thirty seeds became
            # thirty-six donors after one run of six.
            #
            # And it has to be yours. Every ingredient in a delivered file
            # being your own is the reason the file is safe to sell, so a
            # design brought in for reference can be read and listed and looked
            # at, and never lends anything to anything.
            #
            # The first of those is FIT_TO_LEARN_FROM, which also rules out a
            # read taken through an uncropped photograph — see its definition.
            where.append(FIT_TO_LEARN_FROM)
            where.append("d.owned = 1")
        prefix = "s." if joined else ""
        if exclude:
            where.append(f"{prefix}design_id != ?")
            params.append(exclude)

        if joined:
            sql = "SELECT s.design_id FROM specs s JOIN designs d ON d.id = s.design_id"
        else:
            sql = "SELECT design_id FROM specs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {prefix}updated_at DESC"
        params = tuple(params)
        if cap:
            sql += f" LIMIT {int(cap)}"

        out: list[DesignSpec] = []
        for row in self.store.conn.execute(sql, params).fetchall():
            raw = self.store.get_spec(row["design_id"])
            if not raw:
                continue
            try:
                out.append(DesignSpec.model_validate(raw))
            except Exception as exc:
                log.warning("stored spec for %s will not load: %s",
                            row["design_id"][:8], str(exc)[:200])
        return out

    def _source_for(self, page, fallback: Path) -> Path:
        """The image this surface was actually read from.

        The survey knows which image showed which surface and the spec used to
        throw it away, so every check compared every surface against the first
        image of the listing — the inside of a card judged against a photograph
        of its front.
        """
        if page.source_image:
            candidate = Path(page.source_image)
            if candidate.is_file():
                return candidate
        return fallback

    def _check_every_surface(self, derived: DesignSpec, design_id: str,
                             fallback: Path) -> tuple[float, str, str]:
        """Is every printed surface far enough from the one it was read from?

        Every surface, because each becomes its own file and is submitted on
        its own. Only the first was ever looked at: on a greeting card the
        inside went out unexamined, and on a wedding suite four of the five did.

        Returns the worst distinctness, a verdict for the design as a whole,
        and a fault when something is broken rather than merely too close. It
        stops at the first surface that fails, since one failure sends the
        whole design round again and there is nothing to gain by paying for
        the rest.
        """
        worst, verdict = 1.0, "ship"

        for index, page in enumerate(derived.pages):
            preview = self._render_preview(derived, design_id, page=index)
            if preview is None:
                return 0.0, "fault", f"could not render '{page.name}'"

            source = self._source_for(page, fallback)
            # Arithmetic before eyes: this is the first model call of the
            # build, so a render that failed outright is caught here rather
            # than described back to us at the cost of a GPU minute.
            # Only compare shapes when the source really is the artwork. An
            # uncropped square listing photo is 1.00 against a 5x7 rebuild's
            # 0.71, and calling that a misread trim blames the rebuild for what
            # the source did.
            numbers = critique_stage.signals(
                source, preview, compare_shape=self._is_cropped(source))
            if numbers.fault:
                return 0.0, "fault", f"'{page.name}': {numbers.fault}"

            check = derive_stage.check(source, preview)
            log.info("[%s] %s: distinct=%.2f family=%.2f -> %s", design_id[:8],
                     page.name, check.distinct, check.same_family, check.verdict)
            worst = min(worst, check.distinct)

            if check.verdict == "too_far":
                return worst, "too_far", ""
            if check.verdict != "ship" and check.distinct < self.cfg.distinct_threshold:
                verdict = "derive_further"
                break

        return worst, verdict, ""

    def _is_cropped(self, source: Path) -> bool:
        """Was this image actually cut down to the artwork?

        `flat` means it arrived as the artwork; `cropped` means we found the
        card and warped to it. `unsure` means we could not find it and read the
        photograph whole, and everything measured against it is measured
        against a table.
        """
        row = self.store.conn.execute(
            "SELECT trim FROM assets WHERE flat_path=? LIMIT 1", (str(source),)).fetchone()
        return (row["trim"] if row else "flat") in ("flat", "cropped")

    def _check_for_a_twin(self, design_id: str, page_name: str,
                          preview: Path | None) -> duplicates_stage.Twin | None:
        """Compare a finished page against every page we have finished before.

        A perceptual hash against every previous page is five thousand integer
        comparisons — less work than reading the file we just wrote. Cheap
        enough to do on every page, and the only thing that catches two
        unrelated sources converging on the same result.
        """
        if preview is None:
            return None
        taken = duplicates_stage.fingerprint(preview)
        if taken is None:
            return None

        phash, aspect = taken
        twin = duplicates_stage.nearest(
            phash, aspect, self.store.fingerprints(exclude=design_id),
            self.cfg.duplicate_distance)
        if twin:
            log.warning("[%s] %s %s", design_id[:8], page_name, twin.line())
        self.store.save_fingerprint(design_id, page_name, phash, aspect)
        return twin

    # ------------------------------------------------------------------
    # making from a brief, with nothing read
    # ------------------------------------------------------------------

    def invent(self, count: int, brief: invent_stage.Brief,
               on_each=None) -> dict:
        """Designs written from a brief instead of mixed from the catalogue.

        The other way to make something, and the one that needs no seeding: no
        twenty-four, no donor pool, no reading. Use it to start a niche with
        nothing in it, or to go somewhere the catalogue cannot reach.

        It still goes out through the same exporter as everything else, so it
        gets the same files, the same niche folder, and the same twin check —
        a design that comes out looking like one you already have is thrown
        away and another is asked for, which is what `make` does too. Handing
        back a pile of near-identical designs is the work this removes.

        One model call per design, against the batch path's one for
        forty-eight, so this is not the way to produce a thousand a month. It
        is the way to produce the first one.
        """
        niche = brief.niche or self.cfg.collection
        if not niche:
            return {"made": 0, "asked": count, "discarded": 0, "designs": [],
                    "failed": [], "collection": "",
                    "note": "Choose which niche you are working on first. Even a "
                            "design written from scratch has to be filed somewhere."}
        record = self.store.collection(niche)
        if record is None:
            return {"made": 0, "asked": count, "discarded": 0, "designs": [],
                    "failed": [], "collection": niche,
                    "note": f"There is no niche called {niche!r}. Create it first."}

        # What we can actually draw. Asking a model for a design without this
        # produces a beautiful brief full of motifs the library has never heard
        # of, and every one of them is an empty space and a trip to review.
        available = invent_stage.available_motifs(self.cfg.motifs_dir)
        if not available:
            log.info("no motifs in the library — invented designs will carry "
                     "type, colour and shape only")
        # A design to work in the spirit of. Given a design id rather than a
        # file, its flattened artwork is used — which is what makes "pull this
        # link, then make me designs like it" one continuous thing rather than
        # a hunt through the workspace for the file it landed in.
        like = self._picture_for(brief.like) if brief.like else None
        brief = replace(brief, niche=niche, available=available, like=like)
        made: list[dict] = []
        failed: list[str] = []
        repeats = 0
        # What the shop already has, told to the model so it does not have to
        # be discovered after the drawing. The twin check still stands behind
        # it — this is the cheap half, not the guarantee.
        already = self._made_already(niche)

        # Twice the asked-for number of attempts, because a discarded twin has
        # to be replaced rather than quietly subtracted from the total.
        for attempt in range(count * 2):
            if len(made) >= count:
                break
            self.on_progress(f"Writing design {len(made) + 1} of {count} from the brief")
            try:
                spec = invent_stage.invent(replace(brief, avoid=already))
            except Exception as exc:
                log.warning("could not write a design: %s", str(exc)[:200])
                failed.append(str(exc)[:200])
                continue

            # Bring any oversized line down to what will actually fit, using
            # the face the renderer will use. Without it the renderer shrinks
            # it instead and reports "type does not fit its box", which is a
            # review for something nobody needs to look at.
            invent_stage.fit_type(spec, self.cfg.fonts_dir)

            # Point every motif at a drawing we hold. `build` does this after
            # reading and this path did not, so every invented design came out
            # with library_id unset and the renderer reported a hole for a
            # eucalyptus that was sitting in the library all along.
            holes = motifs_stage.resolve(spec, self.cfg.motifs_dir,
                                         self.cfg.motif_threshold)
            if holes:
                log.info("invented design wants %d drawing(s) we have not got: %s",
                         len(holes), "; ".join(sorted(set(holes))[:3]))

            design_id = _new_id("invent", f"{niche}:{attempt}:{spec.dna.category}:"
                                          f"{'|'.join(t.content for t in spec.texts())[:200]}")
            try:
                self.store.add_made_design(
                    design_id, f"written from a brief: {brief.category}", design_id,
                    collection=niche)
                self.store.save_spec(design_id, spec.model_dump(mode="json"))
                self.on_progress(f"Drawing and exporting {len(made) + 1} of {count}")
                outcome = self._export(spec, design_id, distinct=1.0, on_twin="discard")
                if outcome == "twin":
                    repeats += 1
                    self.store.remove_design(design_id)
                    continue
                headline = next((t.content for t in spec.texts()), spec.dna.category)
                already.append(headline[:60])
                made.append({"design_id": design_id, "state": outcome,
                             "recipe": f"written from a brief — {headline[:40]}"})
            except Exception as exc:
                log.exception("could not build an invented design")
                failed.append(str(exc)[:200])
            if on_each:
                on_each(len(made), count)

        return {"made": len(made), "asked": count, "discarded": repeats,
                "designs": made, "failed": failed, "collection": niche,
                "note": "" if len(made) >= count else
                        (f"Wrote {len(made)} of {count}. "
                         + (f"{repeats} came out too close to something you already "
                            f"have and were thrown away. " if repeats else "")
                         + ("The model refused or failed on the rest."
                            if failed else "Ask again for more."))}

    def _picture_for(self, what) -> "Path | None":
        """The picture to work in the spirit of, from a file or a design id.

        A path is taken as it is. Anything else is looked up as a design —
        whole id or the start of one — and its flattened artwork is used, so a
        link pulled in five minutes ago can be referred to by the id the
        catalogue shows rather than by hunting for the file on disk.
        """
        if what is None:
            return None
        candidate = Path(str(what)).expanduser()
        if candidate.is_file():
            return candidate
        row = self.store.conn.execute(
            "SELECT flat_path FROM assets WHERE design_id LIKE ? AND flat_path IS NOT NULL "
            "ORDER BY is_mockup ASC, width DESC LIMIT 1", (f"{what}%",)).fetchone()
        if row and Path(row["flat_path"]).is_file():
            return Path(row["flat_path"])
        log.warning("nothing to work from: %r is neither a file nor a design "
                    "that has been read", str(what)[:60])
        return None

    def _made_already(self, niche: str, cap: int = 12) -> list[str]:
        """Headlines of what is already in this niche, newest first.

        Told to the model so a repeat is avoided before it is drawn rather than
        detected after. Cheap, and not relied on: the perceptual twin check in
        `_export` is what actually stops one shipping.
        """
        out: list[str] = []
        for spec in self._specs(cap=cap, collection=niche):
            line = next((t.content for t in spec.texts() if t.content.strip()), "")
            if line:
                out.append(line[:60])
        return out

    # ------------------------------------------------------------------
    # making many at once
    # ------------------------------------------------------------------

    def make(self, count: int, mix: float | None = None, strength: float | None = None,
             seed: int = 0, on_each=None, collection: str | None = None) -> dict:
        """New designs from what has already been read. No reading, no model
        call per design.

        This is the path the arithmetic needs. Reading a design costs five
        model calls because a model has to look at it; making one from a
        reading you already have costs a handful of choices and three seconds
        of code. The old route paid the reading price for every output, so
        forty-eight designs meant three hundred and eighty-four round trips.
        Here a whole run costs one request — the copy, which never needed an
        image — and the rest is local.

        Asked for forty-eight, it delivers forty-eight or says why not. A
        combination that comes out looking like something you already have is
        thrown away and replaced, not queued for review: handing back a pile of
        near-identical designs and asking which to keep is the work this is
        supposed to remove.
        """
        # A niche is not optional and is never guessed. Making business cards
        # out of Halloween cards is one instruction away from happening, and it
        # is not the kind of mistake you spot in a batch of forty-eight.
        niche = collection or self.cfg.collection
        if not niche:
            return {"made": 0, "asked": count, "discarded": 0, "designs": [],
                    "failed": [], "collection": "",
                    "note": "Choose which niche you are working on first. Nothing is "
                            "made until you do — mixing across niches is the one "
                            "mistake that ruins a whole run."}

        record = self.store.collection(niche)
        if record is None:
            return {"made": 0, "asked": count, "discarded": 0, "designs": [],
                    "failed": [], "collection": niche,
                    "note": f"There is no niche called {niche!r}. Create it first, or "
                            f"pick one that exists."}

        enough, why = collections_stage.ready(
            {**record, **self._collection_counts(niche)}, self.cfg.seed_designs)
        if not enough:
            return {"made": 0, "asked": count, "discarded": 0, "designs": [],
                    "failed": [], "collection": niche, "note": why}

        pool = self._specs(exclude="", cap=200, collection=niche, donors_only=True)
        if len(pool) < 2:
            return {"made": 0, "asked": count, "discarded": 0, "designs": [], "failed": [],
                    "collection": niche,
                    "note": f"{record['name']} has nothing readable to mix from yet. "
                            f"Pull some in and run the queue once, then come back."}

        mix = self.cfg.mix if mix is None else mix
        strength = self.cfg.derive_strength if strength is None else strength

        made, failed, repeats = [], [], 0
        wave, exhausted, note = 0, False, ""
        # Waves rather than one pass, because a design is only known to be a
        # repeat after it has been drawn — so a run that discards a third of
        # its batch has to go back for more combinations, not hand back a third
        # fewer designs than asked for.
        while len(made) < count and wave < 6 and not exhausted:
            wave += 1
            short = count - len(made)
            self.on_progress(
                f"Choosing {short} combination{'' if short == 1 else 's'} "
                f"that have not been made before")
            chosen = batch_stage.plan(
                pool, short, mix=mix, strength=strength,
                seen=self.store.recipe_seen,
                used=self.store.ingredient_use(niche), seed=seed + wave)
            exhausted = chosen.exhausted
            note = chosen.note or note
            if not chosen.made:
                break

            # Claim them before building. Two lanes planning at the same moment
            # would otherwise both take the last free combination.
            kept = [pl for pl in chosen.made
                    if self.store.record_recipe(pl.fingerprint, pl.recipe.base,
                                                pl.recipe.as_dict(), collection=niche)]
            if not kept:
                break

            self.on_progress(f"Deriving {len(kept)} design{'' if len(kept) == 1 else 's'}")
            derived = [derive_stage.derive(pl.spec, strength=pl.strength,
                                           seed=pl.seed, rewrite_copy=False)
                       for pl in kept]

            # One request for the whole wave, because the copy call sends no
            # images and never needed to be per-design.
            self.on_progress(f"Writing fresh wording for all {len(derived)} in one request")
            try:
                derive_stage.rewrite_placeholders_batch(derived)
            except Exception as exc:
                log.warning("batched copy failed, keeping the originals: %s", exc)

            for planned, spec in zip(kept, derived):
                design_id = _new_id("make", planned.fingerprint)
                self.on_progress(f"Drawing and exporting {len(made) + 1} of {count}")
                try:
                    self.store.add_made_design(design_id, planned.recipe.summary(),
                                               planned.fingerprint, collection=niche)
                    self.store.save_spec(design_id, spec.model_dump(mode="json"))
                    outcome = self._export(spec, design_id, distinct=1.0, on_twin="discard")
                    if outcome == "twin":
                        # Different ingredients, same picture. The recipe stays
                        # claimed so nothing tries it again.
                        repeats += 1
                        self.store.remove_design(design_id)
                        continue
                    made.append({"design_id": design_id, "state": outcome,
                                 "recipe": planned.recipe.summary()})
                except Exception as exc:
                    log.exception("could not build a made design")
                    failed.append(str(exc)[:200])
                if on_each:
                    on_each(len(made), count)
                if len(made) >= count:
                    break

        if len(made) < count:
            note = (note + " " if note else "") + (
                f"Made {len(made)} of the {count} asked for. "
                + ("Everything else came out looking like a design you already have. "
                   if repeats else "")
                + "Read a wider spread of your catalogue in and there is more to mix.")
        elif repeats:
            note = (f"{repeats} came out looking like something you already have and "
                    f"were replaced rather than sent to review.")
        return {"made": len(made), "designs": made, "failed": failed,
                "discarded": repeats, "asked": count, "collection": niche,
                "collection_name": record["name"], "note": note.strip()}

    def _niche_folder(self, design_id: str) -> str:
        """Which folder under out/ this design's files belong in.

        On disk as well as in the database, because "keep the niches apart" is
        something the owner checks by opening a folder, not by running a query.
        """
        row = self.store.conn.execute(
            "SELECT collection FROM designs WHERE id=?", (design_id,)).fetchone()
        return (row["collection"] if row and row["collection"] else "unfiled")

    def _collection_counts(self, slug: str) -> dict:
        """How much is in a niche, how much was read, and what got stuck.

        `failed` is here because without it the wait for twenty-four designs is
        a wall with no door in it: three failed reads and the count simply
        stops going up, with nothing on screen saying why or what to do.
        """
        row = self.store.conn.execute(f"""
            SELECT COUNT(*) AS designs,
                   SUM(CASE WHEN {FIT_TO_LEARN_FROM} THEN 1 ELSE 0 END) AS read,
                   SUM(CASE WHEN d.state = 'failed' THEN 1 ELSE 0 END) AS failed,
                   SUM(CASE WHEN d.state = 'pending' THEN 1 ELSE 0 END) AS waiting,
                   SUM(CASE WHEN s.read_json IS NOT NULL AND NOT ({FIT_TO_LEARN_FROM})
                            THEN 1 ELSE 0 END) AS unsure
              FROM designs d LEFT JOIN specs s ON s.design_id = d.id
             WHERE d.collection = ?""", (slug,)).fetchone()
        return {"designs": row["designs"] or 0, "read": row["read"] or 0,
                "failed": row["failed"] or 0, "waiting": row["waiting"] or 0,
                "unsure": row["unsure"] or 0}

    def retry_failed(self, collection: str | None = None) -> dict:
        """Put every failed design back in the queue.

        One action, because the alternative is finding them by eye in a list of
        five thousand. A design that failed while being read has nothing stored
        to reuse, so it reads again on its own — there is nothing to clear.
        """
        sql = "SELECT id FROM designs WHERE state='failed'"
        args: tuple = ()
        if collection:
            sql += " AND collection=?"
            args = (collection,)
        ids = [r["id"] for r in self.store.conn.execute(sql, args)]
        for did in ids:
            self.store.set_design_state(did, "pending")
        with self.store.tx() as c:
            for did in ids:
                c.execute("DELETE FROM review WHERE design_id=?", (did,))
        return {"queued": len(ids), "design_ids": ids}

    def _spec_pool(self, exclude: str, cap: int = 60) -> list[DesignSpec]:
        """Other designs of yours available to mix from.

        Only from the same niche. Capped because a pool of five thousand adds
        nothing over a pool of sixty — donors are drawn at random from whatever
        matches the family.
        """
        row = self.store.conn.execute(
            "SELECT collection FROM designs WHERE id=?", (exclude,)).fetchone()
        niche = row["collection"] if row else None
        return self._specs(exclude=exclude, cap=cap, collection=niche,
                           donors_only=True)

    def _harvest_if_seeded(self, niche: str, read_in: int) -> None:
        """When the last seed design lands, cut out what the library is missing.

        The owner asked for this in the same breath as the learning phase —
        read the twenty-four, and dig the assets out of them ready to use. It
        runs once, on the read that completes the niche, and not on each of the
        twenty-four: by then every sighting of a given motif is available, so
        it picks the best one rather than the first one.

        Costs nothing — no model, just OpenCV cutting the owner's own artwork —
        and the cutouts land in `_harvested/` as PNG, which the library scan
        cannot pick up because it only ever globs *.svg. They are reference to
        trace, not motifs. Failing here must not fail a design that is already
        read, exported and filed, so it is caught and reported.

        `>=` rather than `==`, because two lanes saving their reads a moment
        apart can step from twenty-three straight to twenty-five and no single
        design ever sees the number it was waiting for. Running again is free
        and overwrites the same filenames, and a later run has more sightings
        to choose the best one from — so repeating is the cheap mistake and
        skipping is the expensive one.
        """
        if read_in < self.cfg.seed_designs:
            return
        try:
            gaps = motifs_stage.gaps(
                self._specs(collection=niche), self.cfg.motifs_dir,
                self.cfg.motif_threshold)
            cut = motifs_stage.harvest_all(gaps, self.cfg.motifs_dir)
        except Exception as exc:
            log.warning("could not collect the artwork for %s: %s", niche, exc)
            return
        self.on_progress(
            f"{niche} is read in — collected {len(cut)} piece"
            f"{'' if len(cut) == 1 else 's'} of artwork from your own designs")
        log.info("[%s] seeded; harvested %d of %d gaps into %s",
                 niche, len(cut), len(gaps), self.cfg.motifs_dir / motifs_stage.HARVEST_DIR)

    def motif_gaps(self, limit: int | None = None) -> list[motifs_stage.Gap]:
        """What to draw next, ranked by how many designs are waiting on it.

        Read from the specs rather than from the review rows: a review row
        holds the reason as a sentence with at most five descriptions in it,
        which is fine to read and useless to work from.
        """
        found = motifs_stage.gaps(self._specs(), self.cfg.motifs_dir,
                                  self.cfg.motif_threshold)
        return found[:limit] if limit else found

    def _render_preview(self, spec: DesignSpec, design_id: str, page: int) -> Path | None:
        try:
            result = render(spec, self.cfg.fonts_dir, self.cfg.motifs_dir, page_index=page)
            svg = write_svg(result, self.cfg.root / "renders" / f"{design_id[:16]}-p{page}.svg")
            png = self.cfg.root / "renders" / f"{design_id[:16]}-p{page}.png"
            return export_stage.svg_to_png(svg, png, width=900)
        except Exception as exc:
            log.warning("[%s] render failed: %s", design_id[:8], exc)
            return None

    # -- run everything ----------------------------------------------------

    def run(self, limit: int | None = None) -> dict[str, int]:
        tally: dict[str, int] = {}
        pending = self.store.designs(state="pending")
        if limit:
            pending = pending[:limit]

        for i, row in enumerate(pending, 1):
            did = row["id"]
            try:
                state = self.build(did)
            except Exception as exc:
                log.exception("[%s] failed", did[:8])
                self.store.set_design_state(did, "failed")
                self.store.queue_review(did, f"exception: {exc}", 0.0)
                state = "failed"
            tally[state] = tally.get(state, 0) + 1
            log.info("[%d/%d] %s -> %s", i, len(pending), did[:8], state)
        return tally

    # -- delivery ----------------------------------------------------------

    def deliverable(self) -> tuple[list, list[Path]]:
        """Everything cleared to send, with its metadata. Returns (rows, files).

        One implementation, because the command line and the panel had one
        each and they had already drifted apart: the panel would send a design
        the command line refused. What they disagreed about was the whole
        point of the setting — a design flagged by the provenance pass is held
        back unless SF_PUBLISH_ALL says otherwise, and the command line was
        refusing those even when it did.
        """
        from .publish.metadata import Metadata
        from .publish.metadata import build as build_metadata

        rows: list[Metadata] = []
        files: list[Path] = []

        for row in self.store.designs(state="ready"):
            raw = self.store.get_spec(row["id"])
            if not raw:
                log.warning("[%s] is ready but has no spec", row["id"][:8])
                continue
            spec = DesignSpec.model_validate(raw)
            if not (spec.publishable or self.cfg.publish_all):
                log.info("[%s] held back — %s", row["id"][:8], spec.provenance.reason)
                continue

            out_dir = out_dir_for(self.cfg.root, row["id"])
            for eps in sorted(out_dir.glob("*.eps")):
                cached = self.store.get_metadata(row["id"], eps.name)
                if cached:
                    meta = Metadata(**cached)
                else:
                    preview = eps.with_name(eps.stem + "-preview.jpg")
                    meta = build_metadata(spec, eps.name,
                                          preview if preview.exists() else None)
                    self.store.save_metadata(row["id"], meta)
                rows.append(meta)
                files.append(eps)

        return rows, files

    def held_back(self) -> int:
        """Designs with an editable master that will never be sent."""
        return len(self.store.designs(state="master_only"))

    # -- reporting ---------------------------------------------------------

    def status(self) -> dict:
        c = self.store.conn
        one = lambda q: c.execute(q).fetchone()["n"]
        return {
            "images": one("SELECT COUNT(DISTINCT id) n FROM assets"),
            "designs": one("SELECT COUNT(*) n FROM designs"),
            "ready_to_publish": one("SELECT COUNT(*) n FROM designs WHERE state='ready'"),
            "editable_master_only": one("SELECT COUNT(*) n FROM designs WHERE state='master_only'"),
            "awaiting_review": len(self.store.pending_review()),
            "failed": one("SELECT COUNT(*) n FROM designs WHERE state='failed'"),
        }
