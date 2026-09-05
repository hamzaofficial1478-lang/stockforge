# stockforge — project handoff

**Attach this file at the start of a new chat.** It carries everything an
assistant needs to pick the project up cold: what it is, why it is built this
way, what is done, what is not, and the decisions already settled so they are
not relitigated.

- **Repository:** `https://github.com/hamzaofficial1478-lang/stockforge` (private)
- **Branch:** `claude/image-to-editable-pdf-0exjzg`
- **Status:** architecture complete, 26 tests passing, never yet run against a
  real design — no model server has been connected to it.
- **Owner:** runs an Etsy shop with 5,000+ design assets. Sole developer.

---

## 0. Ground rules for whoever picks this up

1. **Start the session in the stockforge repo.** Not in `glasswork`. glasswork
   is a completely unrelated business project representing months of work and it
   is not to be read, edited, committed to, or pushed. An earlier session was
   launched from that directory, which caused its `PreToolUse` hook to inspect
   every command in the session and produce confusing false positives. That is
   the only reason it was ever mentioned. Nothing in glasswork was changed.
2. **British English.** Plain, human, no corporate filler.
3. **Keep it minimal.** The owner has asked repeatedly for simple and efficient
   over clever and layered. Do not add abstraction that is not earning its place.
4. **No paid APIs.** The whole pipeline runs on the owner's own hardware. There
   is no Anthropic/OpenAI dependency anywhere and there should not be one.

---

## 1. What the program is for

The owner built and sold 5,000+ designs on Etsy — invitations, greeting cards,
wedding suites, seasonal cards (Halloween, Christmas, Easter, Valentine's),
banners, plus corporate and business material. **The source files are gone.**
Only the listing images survive.

The program reads those images and produces two things:

**A — Editable vector masters.** Recover a usable, layered, editable file for
every design, so the owner can change names, resize, make variants, and keep
selling. This works on every design regardless of how it was originally made.

**B — New designs for stock agencies.** Mix ingredients from the owner's own
designs into new pieces, generate title/keyword metadata, and deliver them by
FTP to Adobe Stock and Shutterstock.

---

## 2. The single most important design decision

**It does not trace.**

Auto-tracing a raster image produces thousands of jagged sub-paths, muddy
anti-aliased edges, and text that is no longer text. Both agencies reject
auto-traced vectors on sight.

So the program **reads the design as a set of decisions** — format, grid,
palette, type hierarchy, decoration — writes that down as a structured spec,
and then **draws a fresh piece from that spec** using licensed fonts and drawn
motifs. A recipe, not a photograph.

Everything else in the architecture follows from this.

---

## 3. Architecture

```
source   ─ shop | links | folder            three doors in
flatten  ─ mockup detection, perspective correction, white balance
analyse  ─ five small passes → DesignSpec
compose  ─ mix ingredients from the owner's other designs
derive   ─ shift copy, palette, type, layout
check    ─ does it stand on its own?  too close → loop
render   ─ layered SVG, one per printed surface
critique ─ compare to source, patch, repeat
export   ─ editable-text PDF master + outlined EPS + JPEG preview
publish  ─ metadata CSVs + FTP to both agencies
```

### `schema.py` — the core

`DesignSpec` is the product of this project. Get it right and everything else is
plumbing. Two deliberately separated layers:

- **`DesignDNA`** — the reusable grammar: grid, background, palette, type
  pairing, motif vocabulary, occasion, style tags.
- **`pages: list[Page]`** — one concrete arrangement per printed surface.

Key properties:

- **All geometry is normalised 0..1** of the canvas, so one spec renders at 5×7,
  A4 or Instagram square without touching a number.
- **Colours are referenced by role** (`background`, `ink`, `accent`, `line`…),
  never by literal hex outside the palette. A whole design recolours by swapping
  one palette.
- **Fonts are described, never named.** `FontClass` records category, weight,
  contrast, width, mood. The matcher picks the nearest face in the owner's own
  library. This is deliberate and should not be "improved" into font
  identification.
- **Multi-page.** A greeting card is front + inside. A wedding suite is
  invitation + RSVP + details. They share one DNA.
- **`Provenance`** records whether a design leans on third-party library content.

### `providers/` — model backends

- `base.py` — `VisionProvider` ABC, image encoding, and the JSON recovery loop.
- `openai_compat.py` — one class covering **NVIDIA NIM, vLLM, Ollama and
  LM Studio**, since they all speak OpenAI-compatible chat completions.

Two things make local models workable:

- **Small schemas.** A 7B–90B model will not fill a hundred-field nested object.
  Analysis is split into five passes, each with a schema a local model can hit.
- **A repair loop.** `extract_json` handles fenced blocks, surrounding prose and
  trailing commas; validation errors are handed straight back for up to two
  repair rounds.

Configure with two env vars:

```
SF_VISION_BASE_URL=http://localhost:8000/v1
SF_VISION_MODEL=<whatever your server serves>
```

### `stages/analyse.py` — the five passes

| pass | job | who does the work |
|---|---|---|
| survey | how many surfaces, which image is which, real print dimensions in mm | model |
| palette | the colours | **k-means**; model only assigns roles |
| typography | the text | **OCR** for strings; model for letterforms |
| structure | frames, rules, panels, and every decorative element | model |
| provenance | flags third-party and raster content | model advises, code decides |

Two passes deliberately take work off the model. A model asked to eyeball a hex
value guesses; k-means measures it. A model asked to transcribe an address gets
it nearly right, and nearly right is wrong on something someone prints.

### `stages/compose.py` — mixing (the primary mechanism)

Builds a new design from several of the owner's own: grid from one, palette from
another, type from a third, decoration from a fourth. **The result has no single
original.**

The best borrow: **keep the base's motif positions, take a donor's motif
content.** The composition still holds because the boxes are the ones the layout
was built around, but what sits in them is entirely different.

- Donors are **admitted by occasion**, ranked by style affinity. Shared style
  tags alone are not enough — a test caught that letting tags admit donors
  allowed a wedding card to lend its palette to a Halloween card.
- Every output records a `Recipe` naming which design gave which ingredient.
- A mix inherits the **most cautious provenance** of everything in it.

### `stages/derive.py` — moving it further

Four levers, in order of how much they change how a piece reads:

1. **content** — new sample names, dates, venues.
2. **colour** — the whole palette **rotates together**, so relationships survive.
   Shifting each colour independently is what makes a recolour look wrong.
3. **type** — a different pairing with the same voice.
4. **layout** — margins, rhythm, the spread of the type hierarchy. The one that
   actually makes it a different design.

`check()` then scores two things that pull against each other: **distinct**
(does it stand alone?) and **same_family** (does it still belong?). Too close
goes round again; too far is dialled back.

### `worker.py` — paced execution

One design at a time with an adjustable breather between each. The owner
specifically asked for load balancing: this is meant to be left running all day,
not to peg the GPU. Start / pause / resume / stop, progress, rate per hour,
rolling log. Every step commits to the database, so stopping is never a loss.

### `ui/` — the control panel

`stockforge ui`. **Standard library only** — one Python module, one HTML file,
no Flask, no npm, no CDN. Binds to `127.0.0.1`, no login, so it must not be
exposed publicly. File serving is confined to the workspace (path traversal is
refused; verified).

Five screens: **Setup** (readiness checks with the exact fix for anything not
green, plus settings written back to `.env`), **Sources**, **Queue**,
**Review** (source vs rebuild side by side), **Deliver**.

### `sources/` — three doors in

1. **`shop`** — an Etsy shop name or URL. Uses the **Etsy Open API v3** when
   `SF_ETSY_API_KEY` is set (reliable, gives an exact listing count — this
   answers the owner's question "how many uploads do I actually have?"), and
   falls back to rate-limited public page parsing without one.
2. **`links`** — bulk listing URLs from a file or pasted list.
3. **`folder`** — images on disk, grouped by filename stem or parent folder.

**One listing is one design.** A listing carries four to six images of the same
product — flats plus staged mockups. They are grouped and read together.

### `publish/` — delivery

`metadata.py` drafts titles and keywords grounded in the spec (so keywords
describe what is actually in the file), and writes Adobe Stock and Shutterstock
CSVs. `ftp.py` uploads with resume and retry; credentials come only from the
environment, never the database.

### Other modules

- `db.py` — SQLite. Resumable and idempotent throughout; every stage reads rows
  in one state and writes them in the next.
- `health.py` — the readiness checks behind the Setup screen.
- `stages/ingest.py` — mockup detection, perspective correction, grey-world
  white balance, perceptual hashing.
- `stages/render.py` — spec → layered SVG, one call per page.
- `stages/export.py` — Inkscape for editable-text PDF and EPS; cairosvg fallback.
- `stages/fonts.py` — manifest-driven matching; refuses any face not explicitly
  marked `embeddable`.

---

## 4. Decisions already settled — do not reopen

**Licensing.** An assistant raised that designs assembled from template-library
content (a Canva badge was visible on one of the owner's own listing images)
cannot be submitted to stock agencies, since contributor agreements require
redistribution rights to every element. **The owner considered this and decided
to proceed.** The outcome:

- Provenance is a **flag, not a wall**. Flagged designs default to
  editable-master-only; `SF_PUBLISH_ALL=1` sends them anyway.
- The flag and its reason stay recorded on every design either way.
- **This is the owner's call and has been made. Do not relitigate it.** State
  facts once if directly relevant, then get on with the work.

**Fonts.** The owner is correct that publicly available fonts used as *text* are
fine. The only issue was ever the font file travelling inside a delivered file,
and export outlines all text, so no font file ever travels. Settled.

**Clustering.** An early assumption that 5,000 files meant far fewer unique
designs was **wrong** — they are 5,000 distinct designs. Clustering was
repurposed to group the 4–6 images belonging to one listing, and `group_by_stem`
in `sources/base.py` does that on filenames alone, so `stages/cluster.py` has
been deleted. The perceptual hash it worked on is still computed and stored on
every asset; nothing reads it yet.

---

## 5. Current state

**Done:** schema, providers, all five analysis passes, OCR, compose, derive,
critique, render, export, three sources, publish, worker, control panel, health
checks, 58 passing tests.

The critique gate the module docstring describes now exists. `ssim` and
`palette_distance` were written and never called, so nothing stood between a
failed render and a model call. `signals()` runs cheapest-first — is anything
drawn on the page, is it the right shape, is it merely the source image again —
and returns a fault instead of spending a model. The pipeline runs it before
`derive.check()` too, which is the first model call of a build.

**Verified working:** the panel serves, all endpoints respond, the traversal
guard refuses paths outside the workspace, the health screen correctly reports
what is missing.

The delivery half has now been run too, against a real FTP server rather than
a mock: build, metadata, both agency CSVs, upload, and a second run correctly
skipping what was already there. That found three more bugs.

- **The upload retry had never run.** `except (ftplib.all_errors, OSError)`
  nests one tuple inside another, which is a TypeError in Python 3 — so the
  first transient error on a long upload raised out of `upload_batch` instead
  of retrying, taking the result of every file already sent with it. Across
  nine hundred files a transient error is a certainty, not a possibility.
- **`SF_PUBLISH_ALL` did nothing on the command line.** The pipeline marks a
  flagged design `ready` when it is set, and then `cmd_publish` skipped exactly
  those designs with `if not spec.publishable`. The panel had no such check, so
  the two paths disagreed about the setting's whole purpose. There is now one
  implementation of what may be delivered, used by both.
- **Metadata was redrafted on every run.** `publish --dry-run` then `publish`
  is the normal way to use this, and each pass spent a model call per file
  writing the same title and keywords again. They are kept in the database now.

Still not checked against the agencies themselves: the CSV column layouts are
written from general knowledge, and contributor requirements change. Read the
current documentation before a large upload.

The panel, the settings and the worker now have tests too, and that found the
last big one: **`.env` was never read.** Nothing in the codebase loaded it.
The Setup screen wrote your model URL, your Etsy key, your FTP credentials and
every slider into a file, set them on the running process so they appeared to
work, and lost the lot on restart — Setup back to red with nothing to explain
why. `config.load_env()` runs at import now, before anything reads a setting,
and an exported variable still wins over the file.

Underneath that was a second layer: every scalar setting was a plain dataclass
default, which Python evaluates once when the class is defined. So a value put
into the environment after the first `import stockforge.config` could never
take effect, and the sliders were inert even within a single run. They are
`default_factory` now, and `Settings.reload()` refreshes the shared instance in
place — in place because the pipeline, the worker and the panel all hold the
same one.

The traversal guard on `/file` has a test now rather than a claim, and the
worker's start, pause, resume, stop and limit are exercised for the first
time.

The Etsy door has tests too, against a local server that behaves the way Etsy
does on a long crawl. It needed them: `_get` was a bare `urlopen` with no error
handling of any kind, so the first rate limit or bad gateway anywhere in a
five-thousand listing pull raised and ended it. `sources/http.py` now retries
what is worth retrying — 429 honouring `Retry-After`, 5xx, dropped connections
— and does not retry what is not, since a 404 will not become a 200 by asking
again. `SF_HTTP_RETRIES` and `SF_HTTP_BACKOFF` tune it.

A listing whose images all failed used to vanish from the pull with nothing
said; the crawl now reports how many listings it walked and how many had no
usable image.

**Multi-surface designs had never been built.** The schema has described them
since the first commit — a card is a front and an inside, a suite is five
cards — and every test used one surface, so nothing exercised the rest. Only
the first was ever checked for distinctness or critiqued; the others were
rendered, exported, fingerprinted and shipped unexamined. And `Page` did not
record which image it had been read from, so the survey's `image_index` was
used and discarded — the fifth field in this codebase computed and thrown
away — meaning there was nothing to judge the second surface *against*. A page
carries its source now, every surface is checked and critiqued against its own
image, and one surface failing holds the whole design.

**OCR had never run.** Not once — tesseract was not installed anywhere this
was developed, and `read` returns an empty list on any problem, so a broken OCR
stage was indistinguishable from a missing one. Run against real renders it is
sound: five lines of five, correct positions, one slip in a script face at a
correctly lowered confidence. Two things were wrong around it.

Small artwork was read at its own size. A 127mm card at 420 pixels across is
about 100 dpi and tesseract wants nearer 300; it returned "THER FAMLES Haw,
Sun" for a line it reads exactly when the file is doubled. Flats recovered from
a staged photograph are routinely that small, since they are only the part of
the frame the artwork filled. Anything under 1600 pixels is upscaled first, and
the boxes are normalised against the size tesseract actually saw.

The confidence was measured, stored and never passed on — the fourth field in
this codebase to be computed and thrown away, after the grid, the perceptual
hash and `is_mockup`. Meanwhile the typography prompt told the model to trust
OCR over its own reading, with nothing to say which lines deserved it. Each
line now carries its confidence and the prompt says what to do with it.

**Mockup detection now counts.** Ingest measured `is_mockup` on every asset
and the survey pass reported `mockup_indices`; both were written and neither
was ever read — so a listing whose staged photograph happened to be its largest
file was read through the photograph. Flats are ordered first, the palette is
measured off one where a flat exists, and a surface that could only be read
from a photograph says so in the spec's warnings.

**The palette pass no longer loses colours.** Coverage was looked up by exact
string match against the measured hexes, so a model that altered one — which
the prompt forbids and a local model does anyway — produced a colour that is
not in the artwork and a coverage of zero. Returned colours are snapped to the
nearest measured one.

**Near-duplicates across the catalogue** are now checked, which nothing did.
`derive.check` only ever asked whether a rebuild reads as a copy of its own
source; nothing compared design four hundred to design twelve, and that is the
comparison an agency makes on submission. Every finished page is fingerprinted
and matched against every page built before it, different trims excluded. The
threshold came from measurement, not taste: at 64 bits four different designs
sat 6 to 17 apart and a duplicate at 0 — overlapping bands, no usable
threshold — so the hash is 256 bits, where the same four sit 40 to 74 apart, a
re-encoded copy at 0 and a recolour at 4.

Two things that fell out of building it:

- **Mixing had switched itself off.** `eligible_donors` keyed a design's
  identity on `source_asset_id`, the hash of its largest image. Once a shared
  image could belong to several designs — which was itself a fix — a shop's
  "instant download" banner, being wide, became the largest image of every
  listing, so every design reported the same identity and none was eligible to
  lend to any other. Proven with three genuinely different designs returning
  zero donors. It keys on `design_id` now.
- **The margin lever moved nothing.** `shift_layout` widened `dna.grid.margin_x`
  every round, and the renderer has never read the grid — geometry is
  normalised to the canvas, which is what the schema promises. So the strongest
  lever of derivation was three-quarters inert, and the critic was being told
  in its prompt to patch `dna.grid.margin_*`, which could not have any effect.
  Changing the margins now reflows the element boxes; a full-strength layout
  shift moved a page 0 bits before and 14 after.

The export path has since been run for real against Inkscape 1.2.2, not just
reasoned about. From one spec: `*-master.pdf` comes out with live, extractable
text and our own three faces subset-embedded; `*.eps` carries no font
reference at all; `*-outlined.svg` has no `<text>` left in it. Running the same
SVG through Inkscape without the generated `fontconfig.conf` embeds DejaVu Sans
for every line instead — which is what the whole catalogue would have been set
in.

**Not yet done — and this is the real gap:**

1. **Never run against a real design.** No model server has been connected, and
   no real listing image has been through it. That still stands, and is still
   the next job: run it on 3–5 real listings and read the specs it produces.

   What has changed is that the stages have now been *chained*. Until this
   point every stage worked alone and no two of them had ever run in sequence.
   `tests/test_pipeline.py` runs the whole thing — folder source, flattening,
   the five analysis passes, motif and font matching, compose, derive, the
   critique gate, SVG, and a real Inkscape export — with only the provider
   scripted, through the `providers.set_provider` seam that was already there
   for exactly this. It ends with a PDF, an EPS and a preview on disk and the
   design in `ready`. Running it found three real bugs, all now fixed:

   - **A second `pull` threw away everything already done.** `add_design` used
     `INSERT OR REPLACE`, so re-running `stockforge pull folder ...` after
     adding ten listings put every finished design back to `pending` and erased
     its provenance verdict. On five thousand designs against a local card that
     is days of work, silently repeated. A source may now refresh only what a
     source knows — title, tags, url, image count — never `state` or
     `stock_safe`.
   - **A second `pull` re-flattened every image it already held.** Hashing the
     bytes is far cheaper than decoding and warping one, so a known image is
     now skipped before that work happens rather than after it.
   - **An image used by two listings belonged to only one of them.** `assets`
     was keyed on the image bytes alone, and a shop reuses the same size chart,
     the same "instant download" graphic and the same mockup backdrop across
     every listing it has. Whichever listing was pulled first took the image;
     a listing whose images were all shared ended up with none and failed to
     build outright. Assets are now keyed on the image *and* the design, with a
     migration for an existing workspace.
   - **Every design got an identical derivation.** `build` seeded `derive` on
     the round number, so round one was seed one for the whole catalogue: the
     same hue rotation and the same weight jitter on all five thousand pieces,
     which is the opposite of what the stage is for. Compose was already seeded
     per design; derive had been missed. Both now take their own generator
     rather than reseeding the process — reseeding the module-wide one made
     every caller's randomness a function of ours — and the seed is a stable
     hash, because Python salts `hash()` per process and compose promises you
     can re-run a recipe you liked.
   - **Placeholder rewrites had no length limit.** The prompt asks for similar
     lengths and a local model will not always oblige; a name half again as
     long as the one it replaced would be shrunk by the fitter, and the piece
     would come out with a title set half the size the page was built around.
     A replacement that will not fit is refused and the original kept.
2. **The motif library is effectively empty** — one eucalyptus sprig as a worked
   example. This is the single thing that decides whether output looks
   professional or looks like a wireframe. Every unmatched motif sends its design
   to review, which is how you learn what to draw. It cannot be designed in the
   abstract; it needs real images.

   Eleven motifs now ship — rules, frames, an arch, a corner flourish, a
   laurel, three sprigs, a burst and a chevron band — all structural geometry
   rather than illustration. The seasonal and pictorial half is still the
   owner's to draw, and `stockforge motifs todo` is how they find out which
   ones to draw first: it gathers every unanswered element across the
   catalogue, clusters the wordings that mean the same thing, and ranks them by
   how many designs are held up. `--scaffold` writes a tagged stub for each,
   into a folder the matcher cannot see.

   The *matcher* now exists (`stages/motifs.py`). Before it, `library_id` was
   read in three places and written in none, so no motif could ever be placed
   however many drawings sat in the folder. Descriptions are matched against
   metadata carried inside each SVG, falling back to the filename; anything
   below `SF_MOTIF_THRESHOLD` is refused and reported rather than approximated.
   `stockforge motifs match "..."` shows why something did or did not match.
3. **No fonts installed.** `assets/fonts/` is empty. Google Fonts API integration
   was discussed but not built.

   The plumbing behind it now works end to end. `fonts scan` writes a
   `fontconfig.conf` alongside the manifest and the pipeline puts it on
   Inkscape's and cairo's search path, so the family the matcher picks is the
   one that actually gets drawn — before this the name went into the SVG and
   the system font was substituted silently. The renderer also opens the
   matched file for its real cap height and advance widths, and sets a line
   smaller rather than letting it run off the page. Drop in families, run the
   scan, mark them embeddable, and it works.
4. **Stock-image APIs** (Pexels, Pixabay) were requested but not built. Note for
   the owner: those licences permit use but not resale as stock, so they suit
   pile A (own products) rather than pile B (agency submission).
5. **Metadata CSV layouts** are written from general knowledge and should be
   checked against each agency's current contributor documentation before a large
   upload.

---

## 6. Setup

```bash
pip install -e .
cp .env.example .env
apt install inkscape tesseract-ocr
stockforge fonts scan          # after putting fonts in assets/fonts/
stockforge ui
```

Key environment variables (all in `.env.example`):

| var | purpose |
|---|---|
| `SF_VISION_BASE_URL` / `SF_VISION_MODEL` | the local model server |
| `SF_ETSY_API_KEY` | reliable shop crawling and exact listing counts |
| `SF_MIX` | how much is borrowed from other designs (0–1) |
| `SF_DERIVE_STRENGTH` | how hard the layout/type/colour shift is pushed |
| `SF_DISTINCT_THRESHOLD` | score a result must reach to be accepted |
| `SF_PUBLISH` / `SF_PUBLISH_ALL` | delivery on; send flagged designs too |
| `SF_FTP_ADOBE_*` / `SF_FTP_SHUTTERSTOCK_*` | delivery credentials |

---

## 7. Suggested next steps, in order

1. Connect a local vision model and confirm the Setup screen goes green.
2. Run `stockforge pull folder <a few real designs>` then `stockforge run --limit 3`.
3. **Read the specs it produces.** That is the real test — is the survey pass
   identifying surfaces correctly? Is the typography pass describing letterforms
   usefully? Tune the prompts in `stages/analyse.py` against real output.
   `stockforge spec <id>` is the tool for it; there was none, and the spec is a
   JSON blob in SQLite, so this step was not actually doable. It shows the
   analyser's own read rather than the finished spec, which needed the read to
   be kept — the built spec overwrote the row, so what the prompts are judged
   on was being destroyed by the build that followed.
4. Build out the motif library from what the review queue reports as unmatched.
   `stockforge motifs match "<the description from review>"` says whether a
   drawing you already have just needs tagging, or whether you need a new one.
5. Install fonts and complete the manifest.
6. Only then worry about scale and delivery.

---

## 8. Commit history

```
04a93e5  Allow python3 and git without a permission prompt
ecc3e47  Add the control panel, design mixing, and a paced worker
725adfc  Rebuild for local models, three input doors, and a submission gate
7f50cf1  Scaffold stockforge: image -> design spec -> rebuilt editable vector
```

Every commit message carries the reasoning behind its changes; `git log` is a
genuine second source of context if this document is not enough.
