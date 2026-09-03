"""The orchestrator.

Runs fully automatically. You are not in the loop per design — you are in the
loop at the end, working a queue sorted worst-first. With 5,000 assets that is
the only review model that survives contact with reality: at ten seconds a file
you would spend fourteen hours just clicking "fine".

Every stage is resumable. Ctrl-C at any point and re-run; work already done is
skipped, not repeated.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import anthropic

from .config import Settings, settings as default_settings
from .db import Store
from .schema import Critique, DesignSpec
from .stages import cluster as cluster_stage
from .stages import critique as critique_stage
from .stages import export as export_stage
from .stages import ingest as ingest_stage
from .stages.analyse import analyse
from .stages.render import render, write_svg

log = logging.getLogger("stockforge")


class BudgetExceeded(RuntimeError):
    pass


class Pipeline:
    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or default_settings
        self.cfg.ensure_dirs()
        self.store = Store(self.cfg.db_path)
        self.client = anthropic.Anthropic()

    # -- guard ------------------------------------------------------------

    def _check_budget(self) -> None:
        spent = self.store.spend_today()
        if spent >= self.cfg.daily_usd_cap:
            raise BudgetExceeded(
                f"daily cap reached: ${spent:.2f} of ${self.cfg.daily_usd_cap:.2f}. "
                f"Raise SF_DAILY_USD_CAP or come back tomorrow."
            )

    def _spend(self, stage: str, cid: str | None, usage: dict, usd: float) -> None:
        self.store.record_spend(stage, cid, usage, usd)

    # -- 1. ingest --------------------------------------------------------

    def ingest(self, folder: Path) -> int:
        paths = ingest_stage.walk(folder)
        log.info("found %d images under %s", len(paths), folder)
        added = 0
        for path in paths:
            try:
                flat = ingest_stage.flatten_image(path, self.cfg.root / "flats")
            except Exception as exc:
                log.warning("skipped %s: %s", path.name, exc)
                continue
            if self.store.add_asset(
                id=flat.asset_id, src_path=str(path), flat_path=str(flat.flat_path),
                width=flat.width, height=flat.height, aspect=flat.aspect,
                phash=flat.phash, is_mockup=int(flat.is_mockup), state="ingested",
            ):
                added += 1
        log.info("ingested %d new assets (%d duplicates skipped)", added, len(paths) - added)
        return added

    # -- 2. cluster -------------------------------------------------------

    def cluster(self) -> int:
        rows = self.store.assets()
        members = [
            cluster_stage.Member(r["id"], r["phash"], r["aspect"], r["width"])
            for r in rows if r["phash"]
        ]
        groups = cluster_stage.cluster(
            members, self.cfg.phash_distance, self.cfg.aspect_tolerance
        )
        log.info("%d assets collapsed into %d design families", len(members), len(groups))

        with self.store.tx() as c:
            for cid, group in groups.items():
                rep = cluster_stage.representative(group)
                self.store.upsert_cluster(cid, rep.asset_id, len(group))
                for m in group:
                    c.execute("UPDATE assets SET cluster_id=? WHERE id=?", (cid, m.asset_id))
        return len(groups)

    # -- 3-5. build one family --------------------------------------------

    def build_cluster(self, cid: str) -> str:
        """analyse -> render -> critique -> patch -> repeat -> export.

        Returns the final state: shipped, review, or failed.
        """
        self._check_budget()
        row = self.store.conn.execute(
            "SELECT * FROM assets WHERE id=(SELECT representative FROM clusters WHERE id=?)", (cid,)
        ).fetchone()
        if row is None:
            return "failed"

        source = Path(row["flat_path"])

        draft, usage, usd = analyse([source], client=self.client)
        self._spend("analyse", cid, usage, usd)

        spec_dict = draft.model_dump(mode="json")
        spec_dict.update(schema_version=1, source_asset_id=row["id"], cluster_id=cid)
        spec = DesignSpec.model_validate(spec_dict)
        self.store.save_spec(cid, spec_dict, round_=0)

        crit: Critique | None = None
        render_png = self.cfg.root / "renders" / f"{cid[:16]}.png"
        svg_path = self.cfg.root / "renders" / f"{cid[:16]}.svg"

        for round_ in range(1, self.cfg.max_critique_rounds + 1):
            result = render(spec, self.cfg.fonts_dir, self.cfg.motifs_dir)
            write_svg(result, svg_path)
            export_stage.svg_to_png(svg_path, render_png, width=900)

            # cheap gate first — never pay a model to look at a broken render
            structural = critique_stage.ssim(source, render_png)
            if structural < 0.15:
                self.store.queue_review(cid, "render looks broken (ssim < 0.15)", structural)
                return "review"

            self._check_budget()
            crit, usage, usd = critique_stage.critique(source, render_png, spec, client=self.client)
            self._spend("critique", cid, usage, usd)

            score = min(crit.similarity, crit.polish)
            self.store.save_spec(
                cid, spec_dict, round_=round_,
                similarity=crit.similarity, polish=crit.polish, verdict=crit.verdict,
            )

            if crit.verdict == "escalate" or score < self.cfg.escalate_threshold:
                self.store.queue_review(cid, crit.commentary or "critic escalated", score)
                return "review"
            if crit.verdict == "ship" or score >= self.cfg.ship_threshold:
                break
            if not crit.patches:
                break

            spec_dict, failed = critique_stage.apply_patches(spec_dict, crit)
            for f in failed:
                log.warning("[%s] patch did not apply — %s", cid[:8], f)
            try:
                spec = DesignSpec.model_validate(spec_dict)
            except Exception as exc:
                self.store.queue_review(cid, f"patched spec became invalid: {exc}", score)
                return "review"

        # anything the renderer could not draw is a human's problem, not a
        # reason to ship a hole
        final = render(spec, self.cfg.fonts_dir, self.cfg.motifs_dir)
        write_svg(final, svg_path)
        if final.missing_motifs:
            self.store.queue_review(
                cid,
                "no library match for: " + "; ".join(final.missing_motifs[:5]),
                crit.similarity if crit else 0.0,
            )
            return "review"
        if final.worst_font_score < 0.55:
            self.store.queue_review(cid, "no good font match in the library", final.worst_font_score)
            return "review"

        exported = export_stage.export_all(
            svg_path, self.cfg.root / "out" / cid[:16], stem=cid[:16],
            preview_px=self.cfg.preview_px,
        )
        with self.store.tx() as c:
            c.execute(
                "INSERT INTO builds (cluster_id, asset_id, svg_path, pdf_path, preview_path, "
                "state, created_at) VALUES (?,?,?,?,?,?,strftime('%s','now'))",
                (cid, row["id"], str(svg_path),
                 str(exported.master_pdf) if exported.master_pdf else None,
                 str(exported.preview_jpg) if exported.preview_jpg else None,
                 "shipped"),
            )
        self.store.set_cluster_state(cid, "shipped")
        return "shipped"

    # -- run everything ---------------------------------------------------

    def run(self, limit: int | None = None) -> dict[str, int]:
        tally = {"shipped": 0, "review": 0, "failed": 0}
        pending = self.store.clusters(state="pending")
        if limit:
            pending = pending[:limit]

        for i, c in enumerate(pending, 1):
            cid = c["id"]
            try:
                state = self.build_cluster(cid)
            except BudgetExceeded:
                log.error("stopping: daily spend cap reached")
                break
            except Exception as exc:
                log.exception("[%s] failed", cid[:8])
                self.store.set_cluster_state(cid, "failed")
                self.store.queue_review(cid, f"exception: {exc}", 0.0)
                state = "failed"
            tally[state] = tally.get(state, 0) + 1
            log.info("[%d/%d] %s -> %s (spent $%.2f today)",
                     i, len(pending), cid[:8], state, self.store.spend_today())
        return tally

    # -- reporting --------------------------------------------------------

    def status(self) -> dict:
        c = self.store.conn
        return {
            "assets": c.execute("SELECT COUNT(*) n FROM assets").fetchone()["n"],
            "families": c.execute("SELECT COUNT(*) n FROM clusters").fetchone()["n"],
            "shipped": c.execute("SELECT COUNT(*) n FROM clusters WHERE state='shipped'").fetchone()["n"],
            "awaiting_review": len(self.store.pending_review()),
            "spent_today_usd": round(self.store.spend_today(), 2),
        }
