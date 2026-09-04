# stockforge

Reads your own design images, works out how each design was built, and produces
**editable vector files** — plus new designs mixed from your own back catalogue,
with metadata and delivery to both contributor sites.

Runs entirely on your own hardware. No paid API anywhere in it.

```bash
pip install -e .
stockforge ui
```

That opens the control panel, which is where everything happens. The command
line does the same jobs if you prefer it.

---

## The control panel

Five screens, in the order you actually need them.

**Setup** — every check with a red, amber or green light, and for anything not
green, the exact command or setting that fixes it. Model server not running, no
usable fonts, Inkscape missing: it says so, and it says what to do. You should
never have to read the source to work out why nothing is happening. The model,
mixing and delivery settings are edited here and written to `.env`.

**Sources** — the three doors in, with a count-first button so you can see how
big a job is before you start it.

**Queue** — the worker: start, pause, resume, stop, and a slider for how long
it rests between designs. Live counts of done, to-review and failed, a rate per
hour, and a rolling log of what just happened.

**Review** — your design and the rebuild side by side, worst first, with three
buttons: clear it, run it again, or keep it as a master only.

**Deliver** — dry run writes both agencies' CSVs and tells you the file count
without uploading. Then send.

It binds to `127.0.0.1` and has no login, because it can read your catalogue,
write your `.env` and start uploads. Don't put it on a public port.

---

## Three doors in

```bash
stockforge pull shop   your-shop-name        # walks the whole Etsy catalogue
stockforge pull links  ./urls.txt            # bulk listing links
stockforge pull folder ~/exports             # images already on disk
```

The shop door answers the question you actually asked — *how many uploads do I
have?* Put an Etsy API key from etsy.com/developers in Setup and it reads the
count straight from Etsy, then walks every listing with its images, title and
tags. Without a key it falls back to parsing public pages, which is slower and
breaks whenever Etsy changes their markup.

`stockforge count shop your-shop-name` answers it without pulling anything.

### One listing is one design

A listing carries four to six images of the same product: one or two flat
artwork files and the rest staged photographs and marketing frames. They're
grouped as one design and read together, so the mockups become extra evidence
rather than four extra jobs.

And a design can have several printed surfaces. A greeting card is a front and
an inside. A wedding suite is an invitation, an RSVP and a details card. Each
becomes its own file, sharing one palette and one type system — which is how a
print shop wants them anyway.

---

## Reading a design, without overloading anything

A hosted frontier model will fill a hundred-field nested schema in one shot. A
model on your own card will not, and pretending otherwise produces confident
rubbish. So no module is ever handed more than it can comfortably do:

| pass | what it does | who does the work |
|---|---|---|
| survey | how many surfaces, which image is which, real print dimensions | model |
| palette | the colours | **k-means**, model only assigns roles |
| typography | the text | **OCR** for the strings, model for the letterforms |
| structure | frames, rules, and every decorative element | model |
| provenance | flags third-party and raster content | model advises |

Two of those take work off the model entirely. A model asked to eyeball a hex
value guesses; k-means measures it. A model asked to transcribe an address gets
it nearly right, and nearly right is wrong on something someone prints.

Any OpenAI-compatible server works — NVIDIA NIM, vLLM, Ollama, LM Studio. Two
settings and it runs:

```bash
SF_VISION_BASE_URL=http://localhost:8000/v1
SF_VISION_MODEL=nvidia/llama-3.2-90b-vision-instruct
```

Local models wrap their JSON in prose, leave trailing commas and occasionally
drop a brace. The provider layer extracts, validates, and hands the validation
errors straight back for a repair round. Two retries fixes almost everything.

### The worker is paced on purpose

One design at a time, with a breather between each, adjustable while it runs.
It's meant to be left going all day and forgotten about — not to peg the GPU and
make the rest of the machine unusable. Every step commits to the database, so
stopping is never a loss; the next start picks up where it left off.

---

## Making it a new design, not an edited one

Two mechanisms, and the first does the heavier lifting.

**Mixing** takes the grid from one of your designs, the palette from another,
the type from a third and the decoration from a fourth. The result has no single
original. That's a stronger position than editing one design, and it's closer to
how designers actually work.

The borrow that changes a piece most while breaking it least: keep the base
design's motif **positions**, take a donor's motif **content**. The composition
still holds, because the boxes are the ones the layout was built around — but
what sits in them is entirely different.

Donors are only ever your own analysed designs, and only ones for the same
occasion. Shared style tags aren't enough to admit one — half a catalogue ends
up tagged "minimal", and a wedding card is the wrong place to borrow a Halloween
palette from. Tags rank donors; occasion admits them.

Every output records its recipe — which design gave which ingredient. That's how
you answer "where did this come from" in one look six months later, and how you
re-run a mix you liked with one ingredient swapped.

**Deriving** then moves that result further on its own: new copy, a whole-palette
rotation that keeps the colour relationships intact, a type shift, and a layout
shift. Rotating the palette together matters — shifting each colour
independently is what makes a recolour look wrong, because the accent stops
being an accent.

Then it looks at source and result together and scores two things that pull
against each other: distinct enough to stand alone, and still recognisably the
same family. Too close goes round again. Too far gets dialled back.

`SF_MIX` and `SF_DERIVE_STRENGTH` control how hard each lever is pulled. Both
are sliders on the Setup screen.

---

## Two piles

Every design produces an **editable master** — layered, editable text, yours to
change. That works on everything in your catalogue, whatever it was originally
built with, and it's the half of this project that gets your lost files back.

Separately, the provenance pass flags designs that lean on third-party library
content or photographic artwork. By default those stop at the master and don't
enter a delivery queue, because contributor agreements at both agencies ask you
to hold redistribution rights to every element in a submitted file, and the
penalty lands on the account rather than on the file.

That default is a setting, not a wall:

```bash
SF_PUBLISH_ALL=1     # send flagged designs too — your catalogue, your call
```

Either way the flag and its reason stay recorded on every design, so you can
always see what went out and what it was marked as. `stockforge review` and the
Review screen show the reason for each one.

---

## Setup

```bash
pip install -e .
cp .env.example .env
apt install inkscape tesseract-ocr        # export and OCR
```

Fonts are the one manual step, and it's worth the hour. Drop families you can
use into `assets/fonts/`, then:

```bash
stockforge fonts scan
```

Open `assets/fonts/manifest.json` and set `embeddable: true` where you've
checked. Nothing is used until you do. Google Fonts and anything under the SIL
OFL are the easy wins — publicly available, and since text is outlined on
export, no font file ever travels inside a delivered file.

`fonts scan` also writes `fontconfig.conf` next to the manifest. Neither
Inkscape nor cairo takes a font *file* — both look a family name up through
fontconfig — so without that config the family the matcher chose goes into the
SVG and whatever the system happens to have gets drawn instead. The config puts
your folder on their search path and installs nothing. Re-run `fonts scan`
after adding a family.

### Type is measured, not assumed

The matched file is opened and asked for its real cap height and its real
advance widths. `size_ratio` is a cap height, so a face with a small cap has to
be set at a larger em to put the same amount of ink on the page — assuming
0.70 for everything gets that wrong for every face that isn't 0.70.

Every line is then measured against its box, and set smaller if it would
overflow. Shrinking keeps the design's structure where rewrapping would change
what the analyser read, and running off the page isn't an option. A line that
loses more than a third of its intended size has stopped being a fitting
problem and become a size the analyser misread, so it goes to Review — the
critic can't fix that one, because it would ask for bigger type and get it
shrunk straight back.

The **motif library** in `assets/motifs/` is the thing that decides whether
output looks professional. Motifs are plain SVG authored on a 0–100 square;
there's one in there as a worked example.

The analyser describes a decorative element in words — "grinning carved
jack-o-lantern, three-quarter view" — and the matcher turns that into one of
your drawings, or refuses. Refusing is the point: an unmatched element leaves a
hole, sends its design to Review, and puts its description on the list of what
to draw next. A wrong match is worse than a hole, because a hole you can see
and a wrong pumpkin ships.

What the matcher reads rides inside the file, so a motif stays one file:

```svg
<svg viewBox="0 0 100 100" data-kind="botanical"
     data-tags="eucalyptus, sprig, leaves, greenery, wedding">
  <title>Eucalyptus sprig</title>
  <desc>slender stem with paired oval leaves</desc>
```

None of it is required. Without it the filename is used, so
`sprig-eucalyptus-01.svg` still answers to "eucalyptus sprig" — a folder of
untagged drawings works the moment you drop it in, and tagging only sharpens
the match. `data-kind` must be one of the motif kinds in `schema.py`.

```bash
stockforge motifs list
stockforge motifs todo --scaffold
stockforge motifs match "grinning carved pumpkin" --kind seasonal
```

`todo` is how the library actually gets built. It reads every decorative
element nothing could answer, across the whole catalogue, clusters the
descriptions that mean the same thing — a catalogue words one pumpkin a dozen
ways — and ranks them by how many designs are waiting on each. Eight hundred
designs blocked on one drawing is a morning's work; the review queue can only
tell you that as eight hundred separate sentences. `--scaffold` writes a
tagged stub SVG for each one, into `assets/motifs/todo/`, which the matcher
deliberately cannot see. Draw into it and move the file up a level.

`match` shows every candidate and its score, so when something wasn't placed
you can see whether it wanted a tag or a new drawing. `SF_MOTIF_THRESHOLD` is
how close is close enough, and it's a slider on the Setup screen.

Eleven motifs ship with it — rules, frames, an arch, a corner flourish, a
laurel, three sprigs, a burst and a chevron band. They're the structural pieces
every occasion needs, and they're geometry rather than illustration, which is
the line: a pumpkin, a ghost or a painted character is a drawing somebody has
to make, and the review queue is what tells you which ones are worth making.

Draw with filled shapes wherever you can — they inherit the group's fill and so
recolour with the design. Where you genuinely want a line, `stroke="currentColor"`
picks up the same colour. Never hard-code one.

## Use

```bash
stockforge ui                                # everything, in a browser
stockforge count  shop your-shop-name
stockforge pull   shop your-shop-name --limit 20
stockforge run    --limit 20
stockforge status
stockforge motifs list
stockforge review
stockforge publish --dry-run
```

Every stage is resumable. Ctrl-C and re-run; finished work is skipped.

---

## What it won't do

- **Rebuild a photograph.** A photorealistic or generated scene used as the
  artwork is flagged, not faked. The master keeps it as a placed image.
- **Recover an exact font.** Never attempted. The analyser describes
  letterforms; the matcher picks the nearest thing in your library.
- **Invent a motif library.** A detailed painted character is an illustration,
  not a spec. Add a good one once and reuse it across hundreds of files.
- **Judge your market.** It scores craft and distinctiveness. It doesn't know
  what sells. That's what Review is for.

## Formats

Two exports from the same SVG, for two different jobs:

- **`*-master.pdf`** — layered, editable text. Yours, for changing.
- **`*.eps` + `*-preview.jpg`** — text outlined, no font references. What the
  agencies ingest; neither takes PDF as a vector submission.

Agency requirements and CSV layouts change. Check the current contributor
documentation before a large upload rather than trusting these notes.
