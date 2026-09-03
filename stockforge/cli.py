"""Command line. Deliberately small — the pipeline runs itself."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import settings
from .pipeline import Pipeline
from .stages import fonts as fonts_stage


def _log(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stockforge")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="normalise a folder of images into flat artwork")
    s.add_argument("folder", type=Path)

    sub.add_parser("cluster", help="collapse assets into design families")

    s = sub.add_parser("run", help="analyse, rebuild, critique and export every pending family")
    s.add_argument("--limit", type=int, default=None)

    s = sub.add_parser("build", help="run a single family end to end")
    s.add_argument("cluster_id")

    sub.add_parser("status", help="where everything is up to")
    sub.add_parser("review", help="list what is waiting on you, worst first")

    f = sub.add_parser("fonts", help="manage the font library")
    f.add_argument("action", choices=["scan", "list"])

    args = p.parse_args(argv)
    _log(args.verbose)

    if args.cmd == "fonts":
        if args.action == "scan":
            entries = fonts_stage.scan(settings.fonts_dir)
            path = fonts_stage.write_manifest(settings.fonts_dir, entries)
            print(f"wrote {len(entries)} fonts to {path}")
            print("Now open it and set `embeddable` true only for fonts you hold "
                  "redistribution rights to. Nothing is used until you do.")
        else:
            for e in fonts_stage.load_manifest(settings.fonts_dir):
                flag = "ok " if e.embeddable else "-- "
                print(f"{flag} {e.family:<30} {e.category:<12} {e.weight:<4} {e.licence}")
        return 0

    pipe = Pipeline(settings)

    if args.cmd == "ingest":
        pipe.ingest(args.folder)
    elif args.cmd == "cluster":
        pipe.cluster()
    elif args.cmd == "run":
        print(json.dumps(pipe.run(limit=args.limit), indent=2))
    elif args.cmd == "build":
        print(pipe.build_cluster(args.cluster_id))
    elif args.cmd == "status":
        print(json.dumps(pipe.status(), indent=2))
    elif args.cmd == "review":
        rows = pipe.store.pending_review()
        if not rows:
            print("nothing waiting — everything shipped clean.")
        for r in rows:
            print(f"{r['score']:.2f}  {r['cluster_id'][:12]}  {r['reason'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
