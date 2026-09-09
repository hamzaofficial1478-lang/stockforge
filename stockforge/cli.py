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
import os
import logging
import sys
import textwrap
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


def cmd_check() -> int:
    """The Setup screen, without the browser.

    It is the first thing anyone needs on a new machine and it was only
    reachable through the control panel, which is an odd place to have to go to
    find out why the control panel is red.
    """
    from .health import report

    r = report(settings)
    mark = {"ok": "  ok  ", " warn": "", "warn": " warn ", "fail": " FAIL "}
    for c in r.checks:
        tail = "" if c.required else "   (optional)"
        print(f"[{mark.get(c.state, c.state):^6}] {c.name:<16} {c.detail}{tail}")
        if c.state != "ok" and c.fix:
            for line in textwrap.wrap(c.fix, 74):
                print(f"{'':>10}{line}")
        if c.state != "ok":
            print()

    if r.workable:
        print("Ready to run. `stockforge ui` for the control panel.")
        return 0
    print("Not ready yet: " + ", ".join(r.blocking))
    return 1


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


def _find_design(pipe: Pipeline, needle: str):
    """Accept a prefix. The ids are twenty-four hex characters and nobody is
    going to type one out."""
    rows = pipe.store.designs()
    exact = [r for r in rows if r["id"] == needle]
    if exact:
        return exact[0]
    matches = [r for r in rows
               if r["id"].startswith(needle) or (r["design_key"] or "").endswith(needle)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        print(f"no design starts with {needle!r}. `stockforge spec` lists them.")
        return None
    print(f"{needle!r} matches {len(matches)} designs:")
    for r in matches[:10]:
        print(f"  {r['id'][:16]}  {r['design_key'] or ''}")
    return None


def _font_wanted(font) -> str:
    bits = [font.category, str(font.weight), f"{font.contrast} contrast"]
    if font.width != "normal":
        bits.append(font.width)
    if font.mood:
        bits.append("/".join(font.mood[:3]))
    return ", ".join(bits)


def cmd_spec(args, pipe: Pipeline) -> int:
    """What the analyser understood, in a form you can judge.

    This is the step the handoff calls the real test — read the specs and tune
    the prompts against them — and there was no way to do it. The spec is JSON
    in a SQLite blob; short of opening the database by hand you could not see
    whether the survey found the right surfaces or whether the letterform
    descriptions were worth anything.
    """
    import json as _json

    from .schema import DesignSpec, MotifElement, ShapeElement, TextElement
    from .stages import fonts as fonts_stage

    if not args.design_id:
        rows = pipe.store.designs()
        if not rows:
            print("nothing pulled in yet.")
            return 0
        print(f"{'design':<18}{'state':<14}{'read':<6}what it came from")
        for r in sorted(rows, key=lambda r: r["state"]):
            has = "yes" if pipe.store.get_spec(r["id"]) else "-"
            print(f"{r['id'][:16]:<18}{r['state']:<14}{has:<6}"
                  f"{(r['title'] or r['design_key'] or '')[:44]}")
        print(f"\n{len(rows)} designs. `stockforge spec <id>` reads one.")
        return 0

    row = _find_design(pipe, args.design_id)
    if row is None:
        return 2
    # The read by default: the built spec has been mixed, derived and patched,
    # so it says very little about how well the analyser did.
    raw = pipe.store.get_spec(row["id"]) if args.built else pipe.store.get_read(row["id"])
    which = "as built" if args.built else "as read"
    if not raw and not args.built:
        raw, which = pipe.store.get_spec(row["id"]), "as built (no read was kept)"
    if not raw:
        print(f"{row['id'][:16]} has not been read yet — `stockforge build {row['id']}`.")
        return 1
    if args.json:
        print(_json.dumps(raw, indent=2))
        return 0

    spec = DesignSpec.model_validate(raw)
    dna = spec.dna
    library = fonts_stage.load_manifest(settings.fonts_dir)

    print(f"design {row['id'][:16]}   {row['design_key'] or ''}   [{which}]")
    if row["listing_url"]:
        print(f"  listing     {row['listing_url']}")
    print(f"  state       {row['state']}")
    print(f"  read as     {dna.occasion} {dna.category}"
          f"{' · ' + ', '.join(dna.style_tags) if dna.style_tags else ''}"
          f"   confidence {spec.confidence:.2f}")
    if spec.notes:
        print(f"  the model's own note: {spec.notes[:120]}")

    verdict = ("publishable" if spec.provenance.stock_safe
               else "editable master only")
    print(f"  provenance  {verdict}"
          f"{' — ' + spec.provenance.reason if spec.provenance.reason else ''}")
    if spec.provenance.built_with:
        print(f"              looks built with {spec.provenance.built_with}")

    print(f"\npalette   {dna.palette.temperature}, {dna.palette.contrast} contrast")
    for sw in dna.palette.swatches:
        print(f"  {sw.role.value:<12}{sw.hex}   {sw.coverage:5.1%} of the canvas")

    for i, page in enumerate(spec.pages):
        print(f"\nsurface {i}: {page.name}   "
              f"{page.canvas.width_mm:.0f} x {page.canvas.height_mm:.0f} mm")
        if page.source_image:
            # Which image the survey said showed this surface. Worth checking:
            # a suite mapped onto the wrong images reads plausibly and is wrong.
            print(f"  read from  {Path(page.source_image).name}")

        texts = [e for e in page.elements if isinstance(e, TextElement)]
        if texts:
            print("  type")
        for el in texts:
            entry, score = fonts_stage.match(el.font, library)
            matched = f"{entry.family} ({score:.2f})" if entry else "no font matched"
            flag = "  [placeholder]" if el.placeholder else ""
            print(f"    {el.role.value:<10}{el.size_ratio:.3f}  "
                  f"{el.content[:46]!r}{flag}")
            print(f"    {'':<10}wants {_font_wanted(el.font)}  ->  {matched}")

        motifs = [e for e in page.elements if isinstance(e, MotifElement)]
        if motifs:
            print("  decoration")
        for el in motifs:
            got = (f"{el.library_id} ({el.match_score:.2f})" if el.library_id
                   else "nothing in the library matched")
            print(f"    {el.motif.value:<10}{el.description[:52]!r}")
            print(f"    {'':<10}->  {got}")

        shapes = [e for e in page.elements if isinstance(e, ShapeElement)]
        if shapes:
            kinds = {}
            for el in shapes:
                kinds[el.primitive] = kinds.get(el.primitive, 0) + 1
            print("  geometry  " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))

    if spec.warnings:
        print("\nwarnings")
        for w in spec.warnings:
            print(f"  {w}")
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

    sub.add_parser("check", help="what is ready and what is not, with the fix for each")
    sub.add_parser("status", help="where everything is up to")

    sp = sub.add_parser("spec", help="what the analyser understood about a design")
    sp.add_argument("design_id", nargs="?", help="an id or the start of one")
    sp.add_argument("--json", action="store_true", help="the raw spec instead")
    sp.add_argument("--built", action="store_true",
                    help="the finished spec — mixed, derived and patched — "
                         "rather than what the analyser read")
    sub.add_parser("review", help="what is waiting on you, worst first")

    s = sub.add_parser("publish", help="send cleared files to the agencies")
    s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("ui", help="open the control panel in a browser")
    s.add_argument("--port", type=int, default=8770)
    s.add_argument("--no-browser", action="store_true")

    f = sub.add_parser("fonts", help="manage the font library")
    f.add_argument("action", choices=["scan", "list", "install", "download"])

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
        if args.action == "download":
            from .stages.font_download import download_starter
            try:
                entries = download_starter(settings.fonts_dir)
                print(f"Font library ready: {len(entries)} faces; upstream licenses included.")
                if fonts_stage.ON_WINDOWS:
                    for name, what in fonts_stage.install_for_windows(settings.fonts_dir):
                        print(f"  {name}: {what}")
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"Font setup failed: {exc}")
                return 1
        elif args.action == "scan":
            entries = fonts_stage.scan(settings.fonts_dir)
            path = fonts_stage.write_manifest(settings.fonts_dir, entries)
            conf = fonts_stage.write_fontconfig(settings.fonts_dir)
            print(f"wrote {len(entries)} fonts to {path}")
            print(f"wrote {conf} so Inkscape and cairo can find them by name")
            print("Set `embeddable` true only for fonts you hold redistribution "
                  "rights to. Nothing is used until you do.")
            # fontconfig is a Unix mechanism. On Windows that file achieves
            # nothing, and the family named in the SVG resolves to whatever the
            # machine happens to have — so finish the job here rather than
            # leaving a second step nobody knows to take.
            if fonts_stage.ON_WINDOWS:
                print()
                for name, what in fonts_stage.install_for_windows(settings.fonts_dir):
                    print(f"  {name:<34} {what}")
                print("\nInstalled for your user, so Inkscape can find them by name.")
        elif args.action == "install":
            try:
                done = fonts_stage.install_for_windows(settings.fonts_dir)
            except RuntimeError as exc:
                print(exc)
                return 0
            for name, what in done:
                print(f"  {name:<34} {what}")
            print(f"\n{len(done)} font file(s) installed for your user — no "
                  f"administrator needed, and nothing outside your account changed.")
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

    if args.cmd == "check":
        return cmd_check()

    if args.cmd == "spec":
        return cmd_spec(args, pipe)

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
