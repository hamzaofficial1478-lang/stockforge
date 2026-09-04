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
- `stages/cluster.py` — union-find over phash gated on aspect ratio.
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
repurposed to group the 4–6 images belonging to one listing.

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
