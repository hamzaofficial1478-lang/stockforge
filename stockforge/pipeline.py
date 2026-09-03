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

import json
import logging
from pathlib import Path

from .config import Settings, settings as default_settings
from .db import Store
from .schema import DesignSpec
from .sources import Design, Source
from .stages import critique as critique_stage
from .stages import derive as derive_stage
from .stages import export as export_stage
from .stages import ingest as ingest_stage
from .stages.analyse import analyse
from .stages.render import render, write_svg

log = logging.getLogger("stockforge")


class Pipeline:
    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or default_settings
        self.cfg.ensure_dirs()
        self.store = Store(self.cfg.db_path)

    # -- 1. pull designs in -----------------------------------------------

    def pull(self, source: Source) -> int:
        """Flatten every image of every design and record it. No models yet."""
        count = 0
        for design in source.designs():
            flats: list[Path] = []
            for image in design.images:
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
        self.store.save_spec(design_id, spec.model_dump(mode="json"))
        self.store.set_design_state(design_id, "analysed", spec.provenance.stock_safe)

        if not spec.pages:
            self.store.queue_review(design_id, "no printed surface identified", 0.0)
            return "review"

        # --- make it our own ------------------------------------------
        source_flat = images[0]
        derived = spec
        distinct = 0.0

        for round_ in range(1, self.cfg.max_derive_rounds + 1):
            strength = self.cfg.derive_strength * round_
            derived = derive_stage.derive(spec, strength=strength, seed=round_)
            preview = self._render_preview(derived, design_id, page=0)
            if preview is None:
                self.store.queue_review(design_id, "could not render page 0", 0.0)
                return "review"

            check = derive_stage.check(source_flat, preview)
            distinct = check.distinct
            log.info("[%s] round %d: distinct=%.2f family=%.2f -> %s",
                     design_id[:8], round_, check.distinct, check.same_family, check.verdict)

            if check.verdict == "too_far":
                derived = derive_stage.derive(spec, strength=strength * 0.5, seed=round_)
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

        for i, page in enumerate(derived.pages):
            result = render(derived, self.cfg.fonts_dir, self.cfg.motifs_dir, page_index=i)
            svg = write_svg(result, self.cfg.root / "renders" / f"{design_id[:16]}-{page.name}.svg")
            holes.extend(result.missing_motifs)
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

        if holes:
            self.store.queue_review(
                design_id, "no library match for: " + "; ".join(sorted(set(holes))[:5]), distinct
            )
            self.store.set_design_state(design_id, "review")
            return "review"

        state = "master_only" if not derived.publishable else "ready"
        self.store.set_design_state(design_id, state)
        if state == "master_only":
            log.info("[%s] editable master only — %s", design_id[:8], derived.provenance.reason)
        return state

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
            "images": one("SELECT COUNT(*) n FROM assets"),
            "designs": one("SELECT COUNT(*) n FROM designs"),
            "ready_to_publish": one("SELECT COUNT(*) n FROM designs WHERE state='ready'"),
            "editable_master_only": one("SELECT COUNT(*) n FROM designs WHERE state='master_only'"),
            "awaiting_review": len(self.store.pending_review()),
            "failed": one("SELECT COUNT(*) n FROM designs WHERE state='failed'"),
        }
