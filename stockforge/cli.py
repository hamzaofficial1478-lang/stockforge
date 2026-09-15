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
import time
import textwrap
from pathlib import Path

from .config import env_file, settings
from .pipeline import Pipeline
from .sources import open_source
from .stages import fonts as fonts_stage


def _bench(pipe, settings, count: int, keep: bool) -> int:
    """Time a batch against the models actually configured here.

    Nobody else can run this — it needs the endpoints and the keys, and the
    only number that settles an argument about speed is the one measured on the
    hardware the work will run on. Everything it reports is counted rather than
    estimated: the model calls are counted by wrapping the provider, and the
    seconds by a clock.

    It undoes itself by default. Benchmarking should not quietly fill a niche
    with twelve designs nobody asked for, and it should not burn combinations
    out of the ledger either.
    """
    import time as _time

    from . import providers as registry

    niche = settings.collection
    if not niche:
        print("Choose a niche first: stockforge niche \"Halloween cards\"")
        return 1

    calls = {"n": 0}
    real = registry.reason()

    class Counting:
        name = getattr(real, "name", "the writing model")

        def __getattr__(self, item):
            return getattr(real, item)

        def structured(self, *a, **kw):
            calls["n"] += 1
            return real.structured(*a, **kw)

        def chat(self, *a, **kw):
            calls["n"] += 1
            return real.chat(*a, **kw)

    registry.use_in_this_thread("reason", Counting())
    print(f"making {count} in {niche} with {Counting.name}\n")
    started = _time.monotonic()
    try:
        result = pipe.make(count, on_each=lambda n, total: print(
            f"\r  {n}/{total}", end="", flush=True))
    finally:
        registry.use_in_this_thread("reason", None)
    took = _time.monotonic() - started

    made = result["made"]
    print(f"\r{'':<20}")
    print(f"  made         {made} of {result['asked']}")
    print(f"  model calls  {calls['n']}"
          + (f"  ({calls['n'] / made:.2f} per design)" if made else ""))
    print(f"  wall clock   {took:.1f}s"
          + (f"  ({took / made:.1f}s per design)" if made else ""))
    if made:
        print(f"  an hour of this would be about {int(3600 / (took / made))} designs")
    if result.get("discarded"):
        print(f"  {result['discarded']} thrown away as too close to something you have")
    if result.get("note"):
        print(f"  {result['note']}")

    if not keep and result.get("designs"):
        for entry in result["designs"]:
            pipe.store.remove_design(entry["design_id"])
        pipe.store.forget_recipes(niche)
        print(f"\n  undone — {len(result['designs'])} design(s) removed and the "
              f"combinations released. Pass --keep to keep them.")
    return 0 if made else 1


def _draw_motifs(settings, gaps, size) -> int:
    """Ask a drawing model for the motifs the library has not got.

    Reference to trace, never a deliverable — which is said here rather than
    only in the docs, because this is where somebody will be standing when they
    decide what to do with the files.
    """
    from .providers.base import ProviderError
    from .stages import motifs as motifs_stage

    if not gaps:
        print("nothing missing — there is nothing to draw.")
        return 0
    try:
        from .providers.images import from_env
        provider = from_env()
    except ProviderError as exc:
        print(exc)
        return 1

    print(f"asking {provider.name} for {len(gaps)} drawing(s)\n")

    def tick(done, total, what):
        print(f"\r  {done}/{total}  {what[:56]:<56}", end="", flush=True)

    drawn, trouble = motifs_stage.draw_all(gaps, settings.motifs_dir, provider,
                                           size=size, on_each=tick)
    folder = settings.motifs_dir / motifs_stage.DRAWN_DIR
    print(f"\r{len(drawn)} of {len(gaps)} drawn into {folder}" + " " * 20)
    width = max((len(d.path.name) for d in drawn), default=20)
    for made in drawn:
        print(f"  {made.path.name:<{width}}  {made.gap.designs:>2} design(s)"
              f"{'  ' + made.note if made.note else ''}")
    for line in trouble:
        print(f"  could not draw {line}")
    if drawn:
        print("\nGenerated reference, not artwork you own. Trace what you like to "
              "\nSVG on a 0..100 square and put the trace in the library; the PNGs "
              "\nstay where they are and are never placed in a design.")
    return 0 if drawn else 1


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
    if args.action == "harvest":
        return cmd_motifs_harvest(args, pipe)

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


def cmd_motifs_harvest(args, pipe: Pipeline) -> int:
    """Cut every missing motif out of the design it already appears in.

    The first answer to "the library has no drawing for this" is not to draw
    one and not to generate one — it is that the drawing already exists, in the
    card that sold, and the analyser wrote down exactly where. This costs
    nothing, calls nothing, and gives back the owner's own artwork rather than
    something that merely resembles it.
    """
    from .stages import motifs as motifs_stage

    found = pipe.motif_gaps()
    if not found:
        total = len(pipe.store.designs())
        print("nothing missing." if total else
              "no designs have been read yet, so there is nothing to cut out. "
              "`stockforge pull` and `stockforge run` first.")
        return 0

    if args.action == "draw":
        return _draw_motifs(settings, found[:args.limit], args.size)

    cut = motifs_stage.harvest_all(found[:args.limit], settings.motifs_dir)
    if not cut:
        print(f"{len(found)} motifs are missing, but none could be cut out — "
              f"the flattened images they were read from are gone, so there is "
              f"nothing to crop. Re-run those designs, or draw them.")
        return 0

    folder = settings.motifs_dir / motifs_stage.HARVEST_DIR
    print(f"cut {len(cut)} of {len(found)} into {folder}\n")
    width = max((len(g.path.name) for g in cut), default=20)
    for got in cut:
        print(f"  {got.path.name:<{width}}  {got.width:>4}x{got.height:<4} "
              f"{got.coverage:>4.0%}  {got.gap.designs:>2} design(s)"
              f"{'  ' + got.note if got.note else ''}")

    flags = {got.note for got in cut if got.note}
    if flags:
        print()
    if "thin" in flags:
        print("  thin   almost nothing left once the paper came off. Check these first.")
    if "solid" in flags:
        print("  solid  nothing came off — likely a photograph, not a drawing.")

    missed = len(found[:args.limit]) - len(cut)
    if missed:
        print(f"\n{missed} had no usable sighting and still need drawing.")
    print("\nPictures, not vectors. Keep the good ones and trace them to SVG "
          "on a 0..100 square.")
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
    s.add_argument("--reread", action="store_true",
                   help="read the artwork again rather than reusing the reading "
                        "already on file")

    s = sub.add_parser("niche", help="which niche you are working on")
    s.add_argument("name", nargs="?", help="leave blank to list them")
    s.add_argument("--new", action="store_true",
                   help="say this niche is new; without it, an existing one is expected")

    s = sub.add_parser("retry", help="put everything that failed back in the queue")
    s.add_argument("--all-niches", action="store_true",
                   help="not just the one you are working on")

    s = sub.add_parser("bench", help="time the batch path against your real models")
    s.add_argument("--count", type=int, default=12, help="how many to make")
    s.add_argument("--keep", action="store_true",
                   help="keep what it makes; by default the run is undone afterwards")

    s = sub.add_parser("make", help="new designs from what has already been read")
    s.add_argument("count", type=int, help="how many to make")
    s.add_argument("--mix", type=float, default=None,
                   help="how much to borrow from your other designs, 0..1")
    s.add_argument("--strength", type=float, default=None,
                   help="how far to push each one after the borrowing, 0..1")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--forget", action="store_true",
                   help="clear the record of combinations already made, so old "
                        "ones can be revisited")

    s = sub.add_parser("invent", help="new designs written from a brief, with nothing read")
    s.add_argument("count", type=int, help="how many to write")
    s.add_argument("--category", default="invitation", help="invitation, greeting card, poster, ...")
    s.add_argument("--occasion", default="", help="halloween, wedding, christmas, ...")
    s.add_argument("--style", default="", help="vintage, botanical, minimal, ...")
    s.add_argument("--trim", default="5x7in", help="5x7in, A5, 4x6in, square, ...")
    s.add_argument("--wording", default="", help="words to work into the design")
    s.add_argument("--niche", default="", help="which niche to file them under")

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
    m.add_argument("action", choices=["list", "match", "todo", "harvest", "draw"])
    m.add_argument("description", nargs="?", help="for `match` — what the analyser saw")
    m.add_argument("--kind", default="icon",
                   help="for `match` — botanical, seasonal, frame, ...")
    m.add_argument("--limit", type=int, default=20, help="for `todo` — how many to show")
    m.add_argument("--size", type=int, default=None,
                   help="for `draw` — pixels down each side, default 1024")
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
            def say(done: int, total: int, name: str) -> None:
                print(f"\r  {done}/{total}  {name[:56]:<56}", end="", flush=True)

            try:
                entries = download_starter(settings.fonts_dir, on_progress=say)
                families = len({e.family for e in entries})
                print(f"\rFont library ready: {len(entries)} faces across "
                      f"{families} families; upstream licenses included."
                      f"{' ' * 20}")
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
        print(pipe.build(args.design_id, reread=args.reread))
    elif args.cmd == "niche":
        from . import collections as niches
        from .ui.server import write_env

        have = pipe.store.collections()
        if not args.name:
            if not have:
                print("No niches yet. Start one with: stockforge niche \"Halloween cards\" --new")
                return 1
            for c in have:
                mark = "*" if c["id"] == settings.collection else " "
                ready, why = niches.ready(c, settings.seed_designs)
                print(f" {mark} {c['id']:<28} {c['name']:<28} "
                      f"{c['read']:>4} read  {c['made']:>4} made  "
                      f"{'' if ready else 'needs ' + str(settings.seed_designs)}")
            print(f"\n* is the one in use. Switch with: stockforge niche <name>")
            return 0

        found = niches.look_up(args.name, have)
        if args.new:
            if found.exact:
                print(f"{found.name!r} already exists — drop --new to carry on with it.")
                return 1
            want = niches.slug(args.name)
            pipe.store.ensure_collection(want, args.name)
            if found.near:
                print("Note: these already exist and look similar — "
                      + ", ".join(c["name"] for c in found.near))
        elif found.exact:
            want = found.slug
        elif found.near:
            print(f"Nothing is called {args.name!r}. Did you mean: "
                  + ", ".join(f"{c['name']} ({c['id']})" for c in found.near))
            return 1
        else:
            print(f"Nothing here is called {args.name!r} or anything like it. "
                  f"Add --new to start it.")
            return 1

        write_env(env_file(), {"SF_COLLECTION": want})
        settings.reload()
        record = pipe.store.collection(want)
        counts = pipe._collection_counts(want)
        ready, why = niches.ready({**record, **counts}, settings.seed_designs)
        print(f"working on: {record['name']} ({want})")
        print("  ready to mix from." if ready else f"  {why}")
        return 0

    elif args.cmd == "retry":
        done = pipe.retry_failed(None if args.all_niches else (settings.collection or None))
        print(f"{done['queued']} design(s) put back in the queue."
              + (" Run `stockforge run` to work them." if done["queued"] else ""))
        return 0

    elif args.cmd == "bench":
        return _bench(pipe, settings, args.count, args.keep)

    elif args.cmd == "make":
        if args.forget:
            gone = pipe.store.forget_recipes()
            print(f"forgot {gone} combination{'' if gone == 1 else 's'}; "
                  f"they can be made again")
        started = time.monotonic()

        def tick(done: int, total: int) -> None:
            print(f"\r  {done}/{total}", end="", flush=True)

        result = pipe.make(args.count, mix=args.mix, strength=args.strength,
                           seed=args.seed, on_each=tick)
        took = time.monotonic() - started
        print(f"\r{result['made']} design(s) in {took:.0f}s"
              + (f", {took / result['made']:.1f}s each" if result["made"] else "")
              + " " * 12)
        if result["discarded"]:
            print(f"  {result['discarded']} thrown away as too close to something "
                  f"you already have")
        if result["note"]:
            print(f"  {result['note']}")
        for made in result["designs"][:10]:
            print(f"    {made['design_id'][:12]}  {made['state']:<12} {made['recipe']}")
        if len(result["designs"]) > 10:
            print(f"    … and {len(result['designs']) - 10} more")
        return 0 if result["made"] else 1
    elif args.cmd == "invent":
        from .stages.invent import Brief

        started = time.monotonic()

        def tick(done: int, total: int) -> None:
            print(f"\r  {done}/{total}", end="", flush=True)

        result = pipe.invent(args.count, Brief(
            niche=args.niche or settings.collection,
            category=args.category, occasion=args.occasion, style=args.style,
            trim=args.trim, wording=args.wording), on_each=tick)
        took = time.monotonic() - started
        print(f"\r{result['made']} design(s) written in {took:.0f}s"
              + (f", {took / result['made']:.1f}s each" if result["made"] else "")
              + " " * 12)
        if result["discarded"]:
            print(f"  {result['discarded']} thrown away as too close to something "
                  f"you already have")
        if result["note"]:
            print(f"  {result['note']}")
        for made in result["designs"][:10]:
            print(f"    {made['design_id'][:12]}  {made['state']:<12} {made['recipe']}")
        for why in result["failed"][:3]:
            print(f"    failed: {why}")
        return 0 if result["made"] else 1

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
