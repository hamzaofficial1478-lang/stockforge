"""Command line.

Everything here is also in the control panel, which is the easier way in:

    stockforge ui                                 # start here

    stockforge pull shop   your-shop-name         # door 1 — a whole Etsy shop
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
    """Only ever sees designs the provenance check cleared, or that
    SF_PUBLISH_ALL has explicitly let through."""
    from .publish import FTPTarget, upload_batch, write_metadata

    rows, files = pipe.deliverable()
    if not files:
        print("nothing ready to publish.")
        blocked = pipe.held_back()
        if blocked:
            print(f"({blocked} designs have editable masters but are held back — "
                  f"third-party content. `stockforge review` explains each one, and "
                  f"SF_PUBLISH_ALL=1 sends them anyway.)")
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


def cmd_motifs(args, pipe: Pipeline) -> int:
    """`list` shows the library, `todo` says what to draw next and why, and
    `match` explains a single description that did or did not find a home."""
    from .schema import Box, MotifElement, MotifKind
    from .stages import motifs as motifs_stage

    if args.action == "todo":
        return cmd_motifs_todo(args, pipe)

    library = motifs_stage.load(settings.motifs_dir)
    if not library:
        print(f"no motifs in {settings.motifs_dir} — every decorative element "
              f"will be left as a hole and sent to review.")
        return 0

    if args.action == "list":
        for e in library:
            print(f"{'ok ' if e.kind else '-- '}{e.library_id:<34} "
                  f"{e.kind or 'untagged':<11} {', '.join(e.tags[:6])}")
        print(f"\n{len(library)} motifs, {sum(1 for e in library if e.kind)} tagged.")
        return 0

    if not args.description:
        print('say what to match, e.g. stockforge motifs match "carved pumpkin" '
              '--kind seasonal')
        return 2
    try:
        kind = MotifKind(args.kind)
    except ValueError:
        print(f"unknown kind {args.kind!r} — one of: "
              f"{', '.join(k.value for k in MotifKind)}")
        return 2

    el = MotifElement(motif=kind, description=args.description,
                      box=Box(x=0, y=0, w=1, h=1))
    for entry, score in motifs_stage.rank(el, library)[:8]:
        placed = score >= settings.motif_threshold
        print(f"{'-> ' if placed else '   '}{score:.3f}  {entry.library_id}")
    print(f"\nthreshold {settings.motif_threshold:.2f} — anything below it is left "
          f"as a hole, and its description goes to review as something to draw.")
    return 0


def cmd_motifs_todo(args, pipe: Pipeline) -> int:
    """The work list. Every decorative element nothing in the library could
    answer, gathered across the whole catalogue and ranked by how many designs
    are held up by each — because eight hundred designs waiting on one pumpkin
    is a morning's work, and the review queue cannot tell you that."""
    from .stages import motifs as motifs_stage

    found = pipe.motif_gaps()
    if not found:
        total = len(pipe.store.designs())
        print("nothing missing." if total else
              "no designs have been read yet, so nothing can be missing. "
              "`stockforge pull` and `stockforge run` first.")
        return 0

    blocked = sum(g.designs for g in found)
    print(f"{len(found)} motifs to draw, {blocked} design-slots waiting on them.\n")
    for gap in found[:args.limit]:
        print(gap.line())
        for other in gap.variants[:2]:
            print(f"{'':>16}also seen as: {other[:70]}")
    if len(found) > args.limit:
        print(f"\n… and {len(found) - args.limit} more. --limit to see them.")

    if args.scaffold:
        print()
        for gap in found[:args.limit]:
            print(f"  wrote {motifs_stage.scaffold(gap, settings.motifs_dir)}")
        print("\nEach stub carries its own tags and description. Draw on the "
              "0..100 square, leave the fill alone, then move the file up into "
              f"{settings.motifs_dir} — nothing in the todo folder is matched.")
    return 0


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

    s = sub.add_parser("ui", help="open the control panel in a browser")
    s.add_argument("--port", type=int, default=8770)
    s.add_argument("--no-browser", action="store_true")

    f = sub.add_parser("fonts", help="manage the font library")
    f.add_argument("action", choices=["scan", "list"])

    m = sub.add_parser("motifs", help="inspect the motif library and grow it")
    m.add_argument("action", choices=["list", "match", "todo"])
    m.add_argument("description", nargs="?", help="for `match` — what the analyser saw")
    m.add_argument("--kind", default="icon",
                   help="for `match` — botanical, seasonal, frame, ...")
    m.add_argument("--limit", type=int, default=20, help="for `todo` — how many to show")
    m.add_argument("--scaffold", action="store_true",
                   help="for `todo` — write a tagged stub SVG for each one")

    args = p.parse_args(argv)
    _log(args.verbose)

    if args.cmd == "ui":
        from .ui.server import serve
        serve(settings, port=args.port, open_browser=not args.no_browser)
        return 0

    if args.cmd == "fonts":
        if args.action == "scan":
            entries = fonts_stage.scan(settings.fonts_dir)
            path = fonts_stage.write_manifest(settings.fonts_dir, entries)
            conf = fonts_stage.write_fontconfig(settings.fonts_dir)
            print(f"wrote {len(entries)} fonts to {path}")
            print(f"wrote {conf} so Inkscape and cairo can find them by name")
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

    if args.cmd == "motifs":
        return cmd_motifs(args, pipe)

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
