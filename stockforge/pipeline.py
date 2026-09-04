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
from pathlib import Path

from .config import Settings, settings as default_settings
from .db import Store
from .schema import DesignSpec
from .sources import Design, Source
from .stages import critique as critique_stage
from .stages import compose as compose_stage
from .stages import derive as derive_stage
from .stages import export as export_stage
from .stages import fonts as fonts_stage
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
    def __init__(self, cfg: Settings | None = None):
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
        row = self.store.conn.execute(
            "SELECT * FROM designs WHERE id=?", (design_id,)
        ).fetchone()
        if row is None:
            return "failed"

        assets = self.store.conn.execute(
            "SELECT * FROM assets WHERE design_id=? ORDER BY width DESC", (design_id,)
        ).fetchall()
        images = [Path(a["flat_path"]) for a in assets if a["flat_path"]]
        if not images:
            return "failed"

        # --- read it --------------------------------------------------
        spec = analyse(
            images, asset_id=assets[0]["id"], design_id=design_id,
            listing_url=row["listing_url"],
        )
        # Point every decorative element at a drawing in our own library. What
        # nothing matches stays unresolved on purpose — the renderer reports it
        # as a hole and the description tells you what to draw next.
        holes = motifs_stage.resolve(spec, self.cfg.motifs_dir, self.cfg.motif_threshold)
        if holes:
            log.info("[%s] no motif for: %s", design_id[:8], "; ".join(sorted(set(holes))[:3]))

        self.store.save_spec(design_id, spec.model_dump(mode="json"))
        self.store.set_design_state(design_id, "analysed", spec.provenance.stock_safe)

        if not spec.pages:
            self.store.queue_review(design_id, "no printed surface identified", 0.0)
            return "review"

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

        for round_ in range(1, self.cfg.max_derive_rounds + 1):
            strength = self.cfg.derive_strength * round_
            base = spec
            if pool and self.cfg.mix > 0:
                base, recipe = compose_stage.compose(
                    spec, pool,
                    mix=min(1.0, self.cfg.mix * round_),
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
            preview = self._render_preview(derived, design_id, page=0)
            if preview is None:
                self.store.queue_review(design_id, "could not render page 0", 0.0)
                return "review"

            # Arithmetic before eyes. This is the first model call of the
            # build, so a render that failed outright is caught here rather
            # than being described back to us at the cost of a GPU minute.
            numbers = critique_stage.signals(source_flat, preview)
            if numbers.fault:
                log.warning("[%s] %s", design_id[:8], numbers.fault)
                self.store.queue_review(design_id, numbers.fault, 0.0)
                return "review"

            check = derive_stage.check(source_flat, preview)
            distinct = check.distinct
            log.info("[%s] round %d: distinct=%.2f family=%.2f -> %s",
                     design_id[:8], round_, check.distinct, check.same_family, check.verdict)

            if check.verdict == "too_far":
                derived = derive_stage.derive(
                    base, strength=strength * 0.5, seed=_seed("derive", design_id, round_))
                break
            if check.verdict == "ship" or check.distinct >= self.cfg.distinct_threshold:
                break
        else:
            self.store.queue_review(
                design_id, "still reads as a copy after every derivation round", distinct
            )
            return "review"

        # --- polish it -------------------------------------------------
        for round_ in range(self.cfg.max_critique_rounds):
            preview = self._render_preview(derived, design_id, page=0)
            if preview is None:
                break
            crit = critique_stage.critique(source_flat, preview, derived, page_index=0)
            if crit.verdict == "escalate":
                self.store.queue_review(design_id, crit.commentary or "critic escalated",
                                        min(crit.similarity, crit.polish))
                return "review"
            if crit.verdict == "ship" or not crit.patches:
                break
            patched, failed = critique_stage.apply_patches(derived.model_dump(mode="json"), crit)
            for f in failed:
                log.warning("[%s] patch did not apply — %s", design_id[:8], f)
            try:
                derived = DesignSpec.model_validate(patched)
            except Exception as exc:
                log.warning("[%s] patched spec invalid, keeping previous: %s", design_id[:8], exc)
                break

        # --- ship it ---------------------------------------------------
        out_dir = self.cfg.root / "out" / design_id[:16]
        holes: list[str] = []
        cramped: list[str] = []

        for i, page in enumerate(derived.pages):
            result = render(derived, self.cfg.fonts_dir, self.cfg.motifs_dir, page_index=i)
            svg = write_svg(result, self.cfg.root / "renders" / f"{design_id[:16]}-{page.name}.svg")
            holes.extend(result.missing_motifs)
            for label, scale in result.refits:
                log.info("[%s] %s set at %.0f%% to fit its box",
                         design_id[:8], label, scale * 100)
                # A line that had to lose a third of its size is not a fitting
                # problem any more, it is a size the analyser misread. The
                # critic cannot fix it either — it would ask for bigger type
                # and get it shrunk straight back — so it wants a human.
                if scale < CRAMPED:
                    cramped.append(f"{label} at {scale:.0%} of its intended size")
            export_stage.export_all(svg, out_dir, stem=f"{design_id[:16]}-{page.name}",
                                    preview_px=self.cfg.preview_px)
            with self.store.tx() as c:
                c.execute(
                    "INSERT INTO builds (design_id, page_name, svg_path, state, created_at) "
                    "VALUES (?,?,?,?,strftime('%s','now'))",
                    (design_id, page.name, str(svg), "built"),
                )

        self.store.save_spec(design_id, derived.model_dump(mode="json"),
                             round_=1, distinct=distinct, verdict="built")

        reasons: list[str] = []
        if holes:
            reasons.append("no library match for: " + "; ".join(sorted(set(holes))[:5]))
        if cramped:
            reasons.append("type does not fit its box: " + "; ".join(cramped[:3]))
        if reasons:
            self.store.queue_review(design_id, " — ".join(reasons), distinct)
            self.store.set_design_state(design_id, "review")
            return "review"

        state = "ready" if (derived.publishable or self.cfg.publish_all) else "master_only"
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
