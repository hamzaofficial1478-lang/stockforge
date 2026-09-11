# stockforge — build log and current state

Written 5 September 2026. Branch `claude/image-to-editable-pdf-0exjzg`, head `ec0d1e3`.

This is the record of what has been built, how it was built, what was found
along the way, and what is left. It is meant to be read alongside `HANDOFF.md`,
which explains what the program is for and why it is shaped the way it is. This
file answers a narrower question: where does the work actually stand.

---

## 1. Where it started

The project arrived architecturally complete and never once run. Five commits,
45 files, about 6,000 lines: schema, providers, five analysis passes, OCR,
compose, derive, critique, render, export, three sources, publish, a worker, a
control panel, health checks and 26 tests. Every stage was written, commented
and plausible. No two of them had ever run in sequence, no model server had ever
been connected, and nothing had ever been rendered from a real design.

That is the important thing about the starting point. The problem was never
missing code. It was that nothing had been *exercised*, and unexercised code
that looks right is indistinguishable from code that is right.

---

## 2. How the work was done

The same loop, every time.

**Find something that has never run.** A stage with no test, a field that is
written and never read, a path that only exists in a docstring.

**Prove the fault before fixing it.** Not "this looks wrong" — a script that
demonstrates the wrong behaviour, with numbers. Three designs returning zero
donors. A page moving 0 bits when the margins double. An address read as
`3678 Haunted Hollow, Salm` when the card says `5678 ... Salem`.

**Fix it, then test it.** Every fix gets a test that describes the failure in
the terms it actually mattered — not `test_reflow_works` but
`test_widening_the_margins_actually_moves_the_page`.

**Mutation-test the fix.** Revert the change, confirm the new test fails, put it
back. A test that passes with and without the fix proves nothing. This caught a
real problem once: a mutation that silently failed to apply, leaving the suite
green and me believing a test was stronger than it was. It was redone properly
and failed four tests.

**Prefer the real thing to a mock.** The FTP tests run against a real FTP
server. The Etsy tests run against a real HTTP server that paginates and rate
limits. The export tests run through real Inkscape. OCR runs through real
tesseract. Every one of those found something a mock would have agreed with.

### The pattern that kept paying

Six times now, the fault has been a value that was computed, stored, and never
read by anything:

| what was measured | what it should have decided | what actually happened |
|---|---|---|
| `Grid.margin_x/y` | how much the page breathes | derivation widened margins every round; the renderer never read the grid, so the page never moved |
| `assets.phash` | nothing yet | was dead; now used for near-duplicate detection |
| `assets.is_mockup` | which image to read the design from | ignored; a listing whose photograph was its largest file was read through the photograph |
| `Line.confidence` | which OCR lines to trust | never passed to the model, which was told to trust OCR regardless |
| `Surface.image_index` | which image each surface came from | discarded; every surface was compared against the first image of the listing |
| `Canvas.bleed_mm` | the margin printers trim into | ignored; every file was cut to the trim exactly, so any wander in a guillotine showed white |

If something in this codebase is measured and nothing reads it, the behaviour
that depended on it is silently missing. It is the most productive place to
look, and the sweep in section 5 lists what is left.

---

## 3. What was built

Twelve commits on top of the original five. 53 files touched, about 5,500 lines
added and 350 removed. Roughly thirty distinct defects found and fixed; the
significant ones are listed under each build.

### `1e80c2d` — Motifs could not be drawn at all

`library_id` is what the renderer needs to place a decorative element, and it
was read in three places and written in none. Not one motif could ever be
placed, however many drawings were in the folder — so every design with any
decoration went to review and `ready` was unreachable.

Built the matcher: plain-English descriptions matched against metadata carried
inside each SVG, falling back to the filename so an untagged folder still works.
Anything below a threshold is refused rather than approximated, because a hole
you can see beats a wrong pumpkin that ships.

Also fixed: a library id was joined onto the motifs folder unchecked, and the
analyser is shown that field so it can invent one; motif titles, descriptions
and comments were being copied into delivered artwork; the body was extracted by
splitting on the first `>` in the file.

### `33f09c2` — Type ran off the page, in the wrong font

Two halves of one gap: the renderer named a font it had chosen but never opened,
so it neither drew that font nor knew how wide anything was.

Neither Inkscape nor cairo takes a font *file* — both resolve a family name
through fontconfig, which knew nothing about `assets/fonts`. So the matcher's
choice was a suggestion and the system substituted DejaVu Sans, silently, for
everything. Proven by running the same SVG through Inkscape with and without the
generated config: three correct faces versus DejaVu for every line.

And nothing was measured. `size_ratio` is a cap height and 0.70 of the em was
assumed for every face; line widths were never computed, so any title longer
than the canvas simply overhung both edges. The matched file is now opened for
its real cap height and advance widths, and a line that will not fit is set
smaller — shrinking rather than rewrapping, because rewrapping changes what the
analyser read and overflowing is not an option at all.

### `23691b9` — The cheap check the docstring promised did not exist

`ssim` and `palette_distance` were written, documented and called by nothing.
The module docstring described a gate — numbers first, eyes only if the numbers
are sane — and there was no gate. A blank render went straight to the vision
model, which would describe it back at the cost of a GPU minute and might well
score it as pleasingly distinct.

`signals()` now runs cheapest first and stops at the first fault: is anything
drawn on the page (edge density, not variance, because a gradient background has
plenty of variance and no content), is it the right shape, is it merely the
source image again. The pipeline runs it before the *first* model call of a
build, not just before the critic.

### `6fe730c` — The stages had never run in sequence

Chained the whole pipeline with only the provider scripted, through the
`set_provider` seam the code already documented as being there for this. It
finishes with a PDF, an EPS and a preview on disk.

It found three bugs on the first run. **A second `pull` threw away everything
already done** — `add_design` used `INSERT OR REPLACE`, so re-running a pull
after adding ten listings put every finished design back to `pending` and erased
its provenance verdict. On five thousand designs that is days of GPU time,
silently repeated, and it contradicts what `db.py` says about itself. **A second
pull re-flattened every image it already held**, because the duplicate row was
skipped only after the image had been decoded, dewarped and rewritten.
**Placeholder rewrites had no length limit**, so a name half again as long as
the one it replaced would be shrunk by the fitter and the piece would come out
with a title half the size the page was built around.

### `f2871d7` — The review queue could not be worked from

Building the motif library from the review queue was not possible as the queue
stood: a missing motif was an English sentence on a review row, truncated at
five, one row per design. Across a real catalogue that is thousands of sentences
describing perhaps forty drawings, with no way to tell which is holding up eight
hundred designs.

`stockforge motifs todo` reads the specs instead, clusters the wordings that
mean the same thing, and ranks them by how many designs are waiting.
`--scaffold` writes a tagged stub for each into a folder the matcher cannot see.
Eleven motifs drawn and shipped — structural geometry, not illustration.

Also found: **mixing was keyed on the wrong thing** (see `1a9e399`, where it
surfaced properly), and **a stroked motif could not be recoloured**, because the
renderer set `fill` on the motif group and nothing else — in a library built
entirely around colour roles. The shipped eucalyptus had the bug in its own stem.

### `e847d49` — The delivery half had never run

**The upload retry had never executed once.** `except (ftplib.all_errors,
OSError)` nests one tuple inside another, which is a `TypeError` in Python 3, not
a handler. The first transient error on a long upload raised out of
`upload_batch` instead of retrying, taking the record of every file already sent
with it. Across nine hundred files a transient error is a certainty.

**`SF_PUBLISH_ALL` did nothing on the command line.** The pipeline marks a
flagged design `ready` when it is set, and `cmd_publish` then skipped exactly
those designs. The panel had no such check and would have sent them, so the two
paths disagreed about the setting's entire purpose. One `Pipeline.deliverable()`
now serves both.

**Metadata was redrafted on every run** — a model call per file, twice, for the
dry run and the real one.

### `fcaae6a` — Settings were not real

**`.env` was never read by anything.** The Setup screen wrote your model URL,
your Etsy key, your FTP credentials and every slider into a file that nothing
loaded, and set them on the running process so they appeared to work — until you
restarted, when everything reverted and Setup went red with nothing to explain
why. The README tells you to `cp .env.example .env` on the reasonable assumption
that something reads it.

Underneath that, **every scalar setting was frozen at import**: they were plain
dataclass defaults, which Python evaluates once when the class is defined. So the
sliders were inert even within a single run.

Also: the motif work list reached the Review screen; the traversal guard on
`/file` got a test instead of a claim in a document; the worker's start, pause,
resume, stop and limit ran for the first time; `cluster.py` was deleted.

### `2ac0b87` — The shop crawl could not survive a real shop

`_get` was a bare `urlopen` with no error handling of any kind, and it is the
call behind the entire Etsy API crawl. Walking five thousand listings is hours of
requests; the first rate limit or bad gateway ended the pull with a traceback.

`sources/http.py` retries what is worth retrying — 429 honouring `Retry-After`,
5xx, dropped connections — and refuses to retry a 404, because it will not become
a 200 by being asked again. A listing whose images all failed used to vanish from
the pull with nothing said; the crawl now reports what it walked and what it lost.

### `1a9e399` — Nothing checked design 400 against design 12

`derive.check` asks whether a rebuild reads as a copy of *its own source*.
Nothing compared one finished design to another, and that is the comparison an
agency makes on submission, by rejecting the batch. Mixing draws from one pool
and derivation applies one family of moves, so two unrelated sources can land in
the same place.

The threshold was measured rather than chosen, and the measurement mattered: at
64 bits four genuinely different designs sat 6 to 17 bits apart while a duplicate
sat at 0 — overlapping bands, no usable threshold. At 256 bits the same four sit
40 to 74 apart, a re-encoded copy at 0, a recolour at 4.

Two things fell out. **Mixing had switched itself off**: `eligible_donors` keyed
a design's identity on the hash of its largest image, and once a shared image
could belong to several designs, a shop's wide "instant download" banner became
the largest image of every listing — so every design reported the same identity
and none was eligible to lend to any other. Three different designs, zero donors.
**The margin lever moved nothing**: derivation widened the margins every round
and the renderer has never read the grid, so the lever its own docstring calls
the one that actually changes how a design reads was three-quarters inert — and
the critic was being told in its prompt to patch a field that could not have any
effect.

### `a181614` — You could not see what the analyser understood

The handoff's own next step says to read the specs and tune the prompts against
them, and calls it the real test. There was no way to do it. `stockforge spec`
now shows what it understood: what it took the piece to be, the colours measured
and what each is doing, every line of type with the letterforms described *and*
the font that description matched, every decorative element and whether the
library could answer it.

It shows the read rather than the finished spec, and that needed the read to
exist: analysis wrote the spec, then mixing, derivation and the critic replaced
the same row, so the thing the prompts are judged on was destroyed by the build.

Writing it exposed two faults immediately. **Every swatch reported nought per
cent coverage** — coverage was looked up by exact string match, so a model that
altered a hex, which the prompt forbids and a local model does anyway, produced a
colour not in the artwork and a coverage of zero. **Mockup detection had never
counted for anything**, so a listing whose staged photograph was its largest file
was read through the photograph with a flat export sitting beside it.

### `277beb5` — OCR had never run

Not once: tesseract was not installed anywhere this had been developed, and
`read` returns an empty list on any problem, so a broken OCR stage looked exactly
like a missing one. Installed and run, the parsing is sound — five lines of five,
correct positions.

Two things around it were not. **Small artwork was read at its own size**: a
127mm card at 420 pixels is about 100 dpi where tesseract wants 300, and on this
project's own render it returned `3678 Haunted Hollow, Salm, TX 78555,` for a
card that says `5678 ... Salem`. That is precisely the failure the README rejects,
arriving from the component meant to prevent it. Anything under 1600 pixels is
upscaled first. **The confidence never left the module**, while the prompt told
the model to trust OCR over its own reading with no way to tell which lines had
earned it.

### `36838c7` — Only the first surface was ever judged

A card is a front and an inside; a suite is five cards. The schema has said so
since the first commit and no test had ever built one, so only the first surface
was checked for distinctness or critiqued — the rest were rendered, exported,
fingerprinted and shipped unexamined. And `Page` did not record which image it
came from, so there was nothing to judge the others *against*.

Every surface is now checked and critiqued against its own image, and one
surface failing holds the whole design.

### `ec0d1e3` — Bleed, and the backgrounds that were never drawn

Two findings from the sweep that produced this document, fixed the same day.

**Bleed existed only as a number.** `Canvas.bleed_mm` has defaulted to 3mm since
the first commit and nothing read it. The renderer set every file to exactly the
trim size, so every piece produced so far had no bleed at all — and any wander
in a printer's guillotine shows as a white sliver down one edge. Delivered files
now carry it: the sheet grows on every side, the ground runs into it, and the
artwork does not move, because geometry is normalised to the trim and only the
sheet around it grows. Previews stay at the trim, since they are compared
against the original artwork and should be the same view of the piece.

**Two background treatments were accepted and never drawn.**
`Background.treatment` allows `panel` and `texture`; the renderer handled solid
and the two gradients and let the other two fall through to flat colour,
silently. `panel` is now drawn — a ground with a second colour inset on the
design's own margins. `texture` is not faked: the base colour goes down and the
piece goes to Review carrying the `texture_hint` that describes what it wanted.
Same rule as an unmatched motif, for the same reason.

---

## 4. What state it is in now

**184 tests** across fourteen files, all passing, every fix mutation-checked.

| area | tests | what is genuinely verified |
|---|---|---|
| `test_core` | 20 | schema round-trip, the publishing gate, derivation levers, seeding, JSON recovery |
| `test_ui` | 20 | `.env`, live settings, every endpoint, the traversal guard, worker lifecycle |
| `test_motifs` | 17 | matching, refusing, the gap list, scaffolding |
| `test_compose` | 13 | donor eligibility, mixing, provenance inheritance, reproducibility |
| `test_analyse` | 13 | colour snapping, mockup preference, the read surviving, the inspector |
| `test_sources` | 12 | the Etsy crawl against a server that paginates and rate limits |
| `test_type` | 12 | metrics from the real file, fitting, fontconfig |
| `test_duplicates` | 11 | fingerprinting, the aspect gate, catching a converged design |
| `test_ocr` | 11 | TSV parsing, upscaling, confidence reaching the model |
| `test_pipeline` | 10 | the whole thing end to end, re-pull safety, shared images |
| `test_multipage` | 9 | two surfaces, each judged against its own image |
| `test_publish` | 8 | a real FTP server: upload, resume, retry, the CSVs |
| `test_critique` | 7 | the numeric gate before a model call |
| `test_render` | 10 | bleed on the sheet, panels drawn, textures reported |

**Verified against the real thing, not a mock:** Inkscape 1.2.2 (live-text PDF
with our own fonts embedded, outlined EPS with no font references), tesseract
5.3.4, a real FTP server, a real HTTP server behaving as Etsy does, Chromium
driving the control panel.

**Still true, and unchanged since the beginning:** no model server has ever been
connected, and no real listing image has ever been through it.

---

## 5. What is remaining

### Found and not yet fixed

These came out of a sweep for the pattern in section 2, run against the current
head. They are real and they are small.

**`Grid.symmetry` is unread**, the same way the margins were before `1a9e399`.
Centred, left, right, asymmetric and split are recorded and change nothing.

**`builds.pdf_path` and `builds.preview_path`** are columns that are never
written or read, so you cannot find a design's output files from the database.

**The links door has no tests.** `sources/links.py` — bulk listing URLs from a
file — is the one input path never exercised. The public-page scraper in
`shop.py` is also untested, but that is fragile by design and documented as
such; testing it against a fixture proves little.

### Only you can do these

**Connect a model server and run real listings.** This is the one that matters
and nothing else substitutes for it. Everything around the analysis now has a
test that fails when it breaks; what none of it can tell you is whether the
prompts read a real Etsy listing well.

**Install fonts and complete the manifest.** The plumbing works end to end —
scan, mark embeddable, fontconfig, metrics, embedding in the PDF — but the
folder ships empty and nothing is used until you mark it usable.

**Draw the motif library.** Eleven structural pieces ship. The pictorial half —
pumpkins, ghosts, holly, characters — is drawing, not code, and
`stockforge motifs todo` will tell you which ones to draw first once real
designs have been through.

**Check the agency CSV layouts.** They are written from general knowledge. The
code produces them correctly and consistently; whether the columns match what
Adobe and Shutterstock want this month is a question for their current
contributor documentation.

---

## 6. How much is left

**On the code side, very little.** Bleed and the background treatments are done.
What is left is one small build: the links door needs tests, `Grid.symmetry` is
unread the way the margins were, and two columns on the `builds` table are never
written. After that the productive seam — code that has never been exercised —
is exhausted, and further work would be speculative polish rather than fixing
things that are actually wrong.

**On your side, that is where nearly all the remaining value is.** The next
session with a GPU attached will teach you more about this program than the last
twelve commits did. The loop is now complete and takes about ten minutes:

```bash
stockforge count shop your-shop-name     # how big is the job
stockforge pull folder ~/a-few-listings  # or: pull shop your-shop-name --limit 5
stockforge run --limit 3
stockforge spec <id>                     # and read it
```

That last line is the whole point, and it did not exist a day ago.

**The honest summary:** the program is finished as a program and unproven as a
tool. Everything it does has been made to work and made to stay working. Whether
what it does is any good on your actual catalogue is a question no amount of
further code will answer.

---

## 7. The endpoint 500, and a second brain

Written after the first real run against a hosted endpoint failed.

### What happened

```
failed
exception: meta/muse-glimmer-30b@https://integrate.api.nvidia.com/v1 HTTP 500:
{"type":"urn:nvcf-worker-service:problem-details:internal-server-error",
 "title":"Internal Server Error","status":500,
 "detail":"Internal error while making inference request"}
```

The design went to `failed` and stopped there. Three things were wrong with
that, and only the first is about NVIDIA.

**Nothing enforced the endpoint's image limit.** NVIDIA's hosted NIM accepts an
inline base64 image up to 180 kB and fails the whole request above it — with a
500 from the worker rather than a 413, so nothing in the error says size. A
design at 1280px measures around 148 kB when it is mostly flat colour and goes
well over when it is busy, which is why this failed on some listings and not
others. `encode_image` now takes a byte budget and steps quality down, then
dimensions, until it fits.

**Nothing retried.** A 500 here almost always means the worker behind the
endpoint fell over on that one request. The code retried a truncated reply and
retried a 400 that mentioned `response_format`, but the one failure most likely
to be transient went straight to `failed`. It now retries with backoff, and
honours `Retry-After` so a 429 does not turn into a ban.

**Nothing got smaller.** After the retries, the request steps down a rung at a
time: `response_format` goes, then `model_options` — which for Muse Glimmer
sends `reasoning_effort`, exactly the kind of extra a hosted worker can choke on
— and finally the image shrinks. A 400 is still answered straight away, because
a 400 is a real answer and stepping down would only obscure it.

The error text now says the failure was the endpoint's rather than the design's,
and that Review is where to pick it back up. That matters more than it sounds:
the old message read like the design was at fault.

Checked by mutation: putting 500 back outside the transient set, removing the
byte budget, and defaulting the budget high all fail these tests. The budget
test caught a mistake of its own on the first pass — it set the limit explicitly
and so never checked the default, which is the thing that has to be right for
someone who has never heard of the limit.

### A second brain

The owner asked whether they could sign in with Claude instead of holding an API
key. They can, and it needed almost nothing: the Anthropic SDK already resolves
credentials in an order where an API key is only the first option and a profile
from `ant auth login` is another. `Anthropic()` with no arguments picks that up.
So `providers/claude.py` deliberately passes **no** `api_key`, because passing
one would shadow the profile and defeat the point — there is a test that fails
if anyone adds it back.

`SF_VISION_BACKEND` chooses; a model id starting `claude-` chooses it for you,
because someone who typed that has already said what they meant. Effort defaults
to `low` against five thousand designs. A refusal is caught before the content
is read, or it looks like an empty reply and the repair loop spends three more
calls learning the same thing.

This breaks ground rule 4 in the handoff — no paid APIs — on the owner's direct
request. The local backend is untouched and still the default. The README says
plainly that this is a paid API and that signing in saves the key handling, not
the bill, because the question was asked in terms of "my Claude tokens" and the
honest answer is that those are not the same thing.

### State

415 tests pass with Inkscape and Tesseract present. The 22 that fail without
them are the export and OCR paths, and they fail the same way on a clean
checkout — nothing to do with these changes.

---

## 8. Backends became a registry

The owner's objection, and it was the right one: the program should not be
hardcoded to Anthropic, and adding GPT or Hermes or anything else should not
mean editing stockforge.

The switch was an `if backend == "claude"` with an `else`. Fine for two, wrong
for three, and it quietly said this was an OpenAI program with Anthropic bolted
on — not what you want from something meant to outlive whichever model is best
this month.

A backend is now a small object: what it is called, how to build it, whether it
can run, and which settings it reads. The two that ship register themselves the
same way a stranger's would, and nothing in the lookup privileges them. Adding
one takes no change to any file here — write a module with a `BACKEND` in it and
put its import path where the backend name goes.

The first attempt got the rule wrong: only names with a dot in them were tried
as modules, so a top-level `acme_gemini` fell through to "no such backend". An
arbitrary rule for someone to trip over. Anything not already registered is now
tried as an import, dotted or not, and the error says what it tried and why it
failed.

Worth stating plainly because the owner named them: **GPT and Hermes are models,
not backends.** Both already worked through the OpenAI-compatible backend — GPT
at `api.openai.com`, Hermes on Ollama or OpenRouter. What was missing was any
way to find that out, so the backend now carries presets: eight addresses with a
button that fills one in. Only addresses, no capability claims — the Test button
sends a real image and finds out, which beats a table that goes stale.

The health check no longer names Claude. It asks the selected backend whether it
is ready and shows what it says, with a deeper check for the two built-ins that
can offer one. A custom backend gets reported from its own `ready()`, which is
why that returns a fix string rather than a bool.

Two things the tests earned. `settings` on a backend is not decoration: a
provider is built once and rebuilt when a watched setting changes, so a backend
reading a setting it did not declare means changing that setting does nothing at
all, silently. There is a test that fails if the declaration stops being honoured.
And `docs/backends.md`'s worked example is executed by the suite — a backend
example that does not run is worse than none, because someone copies it, it
fails, and they conclude the extension point is broken.

One thing came out rather than in. The cache fingerprint had the resolved
backend name folded into it as belt and braces; a mutation test showed nothing
depended on it, because `BACKEND` is itself a watched setting and changing it
moves the fingerprint anyway. A line that looks like it is doing safety work and
is not is worse than no line, so it went, and the test now documents which
mechanism actually provides the guarantee.

430 tests pass.
