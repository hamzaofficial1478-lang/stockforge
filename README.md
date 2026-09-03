# stockforge

Reads a design image, works out what the design actually *is*, and rebuilds it
from scratch as a clean, editable vector file.

Built for one job: recovering a large back catalogue of Etsy artwork whose
source files are gone, and turning it into files that are good enough to submit
to Adobe Stock and Shutterstock — and good enough to edit again.

---

## The one idea this rests on

**It does not trace.**

Run a PNG through Illustrator's Image Trace and you get thousands of jagged
sub-paths, muddy anti-aliased edges and text that is no longer text. Adobe Stock
and Shutterstock both reject auto-traced vectors, and a reviewer spots one in
seconds.

So the program reads the artwork the way a designer would — format, grid,
palette, type hierarchy, decoration — writes that down as a structured spec, and
then **draws a fresh piece from that spec** using our own licensed fonts and our
own drawn motifs. Same idea, same layout, same mood, better execution. A recipe,
not a photograph.

That is also why the output is legitimately ours to submit: nothing from the
source file survives into the rebuild except the design thinking, which was
already the owner's.

---

## How it runs

```
ingest    5,000 images  ->  flat artwork      no model, minutes
cluster                 ->  ~1,200 families   no model, seconds
analyse   one per family->  DesignSpec        one Opus 5 vision call
render                  ->  layered SVG       deterministic
critique  render vs src ->  patches           vision call, up to 3 rounds
export                  ->  PDF / EPS / JPEG  Inkscape
review    worst first   ->  you               only what needs you
```

Fully automatic. You are not in the loop per design — you are in the loop at the
end, working a queue sorted worst-first. At 5,000 files that is the only review
model that survives contact with reality.

### Clustering is the whole economy of this thing

A shop with 5,000 uploads does not have 5,000 designs. It has a few hundred
templates, each shipped in six colourways, three sizes, with the names swapped.
Perceptual-hash clustering collapses that before a single token is spent, and
then one spec generates the whole family — different palette, different text,
same grammar. Which is precisely what an editable vector file is *for*.

Skip this step and you pay full price to learn the same layout forty times.

---

## Setup

```bash
pip install -e .
cp .env.example .env          # add your ANTHROPIC_API_KEY
apt install inkscape          # needed for live-text PDF and EPS export
```

Then fill `assets/fonts/` with families **you hold redistribution rights to**
(SIL OFL, or purchased with an extended licence) and bootstrap the manifest:

```bash
stockforge fonts scan
```

Open `assets/fonts/manifest.json`, correct the tags, and set `embeddable: true`
only where you have actually checked the licence. Nothing is used until you do.
It is a one-off afternoon that pays back across the whole catalogue.

## Use

```bash
stockforge ingest ~/etsy-exports
stockforge cluster
stockforge run --limit 20        # start small, look at the output
stockforge status
stockforge review
```

Every stage is resumable. Ctrl-C and re-run; finished work is skipped, not
repeated.

---

## What it costs

Roughly **$0.30–0.40 per design family** on Opus 5 at high effort — one analysis
call plus two critique rounds, with the system prompts cached.

For 5,000 assets that clusters down to ~1,200 families, call it **$350–500** for
the whole catalogue. Without clustering the same job is nearer $1,800. On
Sonnet 5 it is about 40% of those figures, at some cost in read quality.

`SF_DAILY_USD_CAP` is a hard stop, enforced before every model call. Measure the
real number on your first fifty families before turning the rest loose.

---

## What it will not do

Worth being straight about the edges:

- **Photographs, foil, emboss and die-cuts.** These are flagged, not faked. They
  go to the review queue.
- **Exact font recovery.** Deliberately never attempted. The analyser describes
  letterforms and the matcher picks the nearest thing we are allowed to embed.
- **Complex illustration.** A hand-drawn floral wreath with sixty petals is not
  a spec, it is an illustration. It gets flagged, and the fix is to add a good
  one to the motif library once and reuse it everywhere.
- **Judging its own taste.** The critique loop scores against the source and
  against basic craft. It does not know your market. That is what the review
  queue is for.

## Formats

Two exports from the same SVG, for two different audiences:

- **`*-master.pdf`** — layered, **live text**. This is your file, the one you
  lost. Open it, change the names, re-export.
- **`*.eps` + `*-preview.jpg`** — text outlined, no font references. This is
  what stock sites ingest; neither takes PDF as a vector submission.

Contributor requirements change. Check each site's current guidelines before a
large upload rather than trusting these notes.
