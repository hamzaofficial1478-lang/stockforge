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

from .config import Settings, settings as default_settings
from .db import Store
from .schema import DesignSpec
from .sources import Design, Source
from .stages import critique as critique_stage
from .stages import compose as compose_stage
from .stages import derive as derive_stage
from .stages import export as export_stage
from .stages import fonts as fonts_stage
from .stages import duplicates as duplicates_stage
from .stages import ingest as ingest_stage
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

    def pull(self, source: Source) -> int:
        """Flatten every image of every design and record it. No models yet."""
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
            )
            count += 1
            log.info("pulled %s (%d images)", design.design_id[:60], len(flats))
        return count

    # -- 2-6. work one design ----------------------------------------------

    def build(self, design_id: str) -> str:
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
                master_only: bool = False) -> str:
        """Export every page, including imperfect recoveries that need editing."""
        out_dir = self.cfg.root / "out" / design_id[:16]
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

    def _specs(self, exclude: str | None = None, cap: int | None = None) -> list[DesignSpec]:
        """Every design already read, newest first.

        A spec that will not load is said out loud. Swallowing it silently is
        how an empty donor pool looks exactly like a catalogue of one.
        """
        sql = "SELECT design_id FROM specs"
        params: tuple = ()
        if exclude:
            sql += " WHERE design_id != ?"
            params = (exclude,)
        sql += " ORDER BY updated_at DESC"
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
            numbers = critique_stage.signals(source, preview)
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

    def _spec_pool(self, exclude: str, cap: int = 60) -> list[DesignSpec]:
        """Other designs of yours available to mix from.

        Capped because a pool of five thousand adds nothing over a pool of
        sixty — donors are drawn at random from whatever matches the family.
        """
        return self._specs(exclude=exclude, cap=cap)

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

            out_dir = self.cfg.root / "out" / row["id"][:16]
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
