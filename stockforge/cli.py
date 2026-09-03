"""Command line.

    stockforge pull shop   infinitedesignhive     # door 1 — a whole Etsy shop
    stockforge pull links  ./listing-urls.txt     # door 2 — bulk links
    stockforge pull folder ~/etsy-exports         # door 3 — images on disk

    stockforge run --limit 5
    stockforge status
    stockforge review
    stockforge publish --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import settings
from .pipeline import Pipeline
from .sources import open_source
from .stages import fonts as fonts_stage


def _log(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_publish(pipe: Pipeline, dry_run: bool) -> None:
    """Only ever sees designs whose provenance check passed."""
    from .publish import FTPTarget, upload_batch, write_metadata
    from .publish.metadata import build as build_metadata
    from .schema import DesignSpec

    ready = pipe.store.designs(state="ready")
    if not ready:
        print("nothing ready to publish.")
        blocked = pipe.store.designs(state="master_only")
        if blocked:
            print(f"({len(blocked)} designs have editable masters but are held back — "
                  f"third-party content. `stockforge review` explains each one.)")
        return

    rows, files = [], []
    for row in ready:
        spec = DesignSpec.model_validate(pipe.store.get_spec(row["id"]))
        if not spec.publishable:                 # belt and braces
            continue
        out_dir = settings.root / "out" / row["id"][:16]
        for eps in sorted(out_dir.glob("*.eps")):
            preview = eps.with_name(eps.stem + "-preview.jpg")
            rows.append(build_metadata(spec, eps.name, preview if preview.exists() else None))
            files.append(eps)

    if not files:
        print("nothing to send.")
        return

    paths = write_metadata(rows, settings.root / "out")
    print(f"{len(files)} files, metadata written to:")
    for name, path in paths.items():
        print(f"  {name}: {path}")

    if dry_run or not settings.publish_enabled:
        print("\ndry run — nothing uploaded. Set SF_PUBLISH=1 and drop --dry-run to send.")
        return

    for name in ("adobe", "shutterstock"):
        try:
            target = FTPTarget.from_env(name)
        except RuntimeError as exc:
            print(f"skipping {name}: {exc}")
            continue
        result = upload_batch(target, files)
        done = sum(1 for v in result.values() if v == "uploaded")
        print(f"{name}: {done} uploaded, {len(result) - done} skipped or failed")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stockforge")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("pull", help="bring designs in from a shop, a link list, or a folder")
    s.add_argument("kind", choices=["shop", "links", "folder"])
    s.add_argument("target")
    s.add_argument("--limit", type=int, default=None)

    s = sub.add_parser("count", help="how many designs a source holds, without pulling them")
    s.add_argument("kind", choices=["shop", "links", "folder"])
    s.add_argument("target")

    s = sub.add_parser("run", help="analyse, derive, rebuild and export pending designs")
    s.add_argument("--limit", type=int, default=None)

    s = sub.add_parser("build", help="run a single design end to end")
    s.add_argument("design_id")

    sub.add_parser("status", help="where everything is up to")
    sub.add_parser("review", help="what is waiting on you, worst first")

    s = sub.add_parser("publish", help="send cleared files to the agencies")
    s.add_argument("--dry-run", action="store_true")

    f = sub.add_parser("fonts", help="manage the font library")
    f.add_argument("action", choices=["scan", "list"])

    args = p.parse_args(argv)
    _log(args.verbose)

    if args.cmd == "fonts":
        if args.action == "scan":
            entries = fonts_stage.scan(settings.fonts_dir)
            path = fonts_stage.write_manifest(settings.fonts_dir, entries)
            print(f"wrote {len(entries)} fonts to {path}")
            print("Set `embeddable` true only for fonts you hold redistribution "
                  "rights to. Nothing is used until you do.")
        else:
            for e in fonts_stage.load_manifest(settings.fonts_dir):
                print(f"{'ok ' if e.embeddable else '-- '} {e.family:<30} "
                      f"{e.category:<12} {e.weight:<4} {e.licence}")
        return 0

    if args.cmd == "count":
        source = open_source(args.kind, args.target)
        n = source.count()
        print(n if n is not None else "unknown — set SF_ETSY_API_KEY for an exact count")
        return 0

    pipe = Pipeline(settings)

    if args.cmd == "pull":
        source = open_source(args.kind, args.target,
                             cache_dir=settings.root / "downloads", limit=args.limit)
        print(f"pulled {pipe.pull(source)} designs")
    elif args.cmd == "run":
        print(json.dumps(pipe.run(limit=args.limit), indent=2))
    elif args.cmd == "build":
        print(pipe.build(args.design_id))
    elif args.cmd == "status":
        print(json.dumps(pipe.status(), indent=2))
    elif args.cmd == "review":
        rows = pipe.store.pending_review()
        if not rows:
            print("nothing waiting.")
        for r in rows:
            print(f"{r['score']:.2f}  {r['design_id'][:12]}  {r['reason'][:90]}")
    elif args.cmd == "publish":
        cmd_publish(pipe, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
