# stockforge

Reads your own design images, works out how each design was built, and produces
two things: **an editable vector master** you can open and change, and — where
the licensing allows it — **a new design in the same style**, ready to submit to
a stock agency.

Runs entirely on your own hardware. No paid API anywhere in the pipeline.

---

## Read this part first

The program does two different jobs and it is important not to confuse them.

**Getting your files back.** You lost the source files for a catalogue you
built. stockforge reads each design and rebuilds it as a clean layered vector
you can edit — change the names, resize it, make variants, keep selling it. This
works on **everything**, whatever the design was originally made with. You own
the right to use those designs; nobody is arguing otherwise.

**Making new files to submit.** A stock agency requires you to hold
redistribution rights to *every element* in a file you submit. Redrawing does
not change where a composition came from. So a design assembled from a template
library's stock art cannot go to Adobe Stock or Shutterstock, however much of it
gets rebuilt.

So the pipeline checks each design and sorts it into one of the two piles. Both
piles get an editable master. Only the cleared pile ever reaches an upload
queue. That gate lives in the code, not in a config file, because the cost of
getting it wrong lands on your contributor account rather than on one file.

---

## How it runs

```
source  -> shop | links | folder
flatten -> mockup detection, perspective correction, colour balance
analyse -> five small passes: survey, palette, type, structure, provenance
derive  -> new copy, new palette, new type, new rhythm
check   -> does it stand on its own, or still read as a copy?
render  -> layered SVG, one per printed surface
critique-> compare, patch, repeat
export  -> editable PDF master + outlined EPS + JPEG preview
publish -> FTP to both agencies, cleared designs only
```

### Three doors in

```bash
stockforge pull shop   your-shop-name        # walks the whole Etsy catalogue
stockforge pull links  ./urls.txt            # bulk listing links
stockforge pull folder ~/exports             # images already on disk
```

The shop door answers the question you actually asked — *how many uploads do I
have?* Set `SF_ETSY_API_KEY` from etsy.com/developers and it reads the listing
count straight from Etsy, then walks every listing with its images, title and
tags. Without a key it falls back to parsing public pages, which is slower and
breaks whenever Etsy changes their markup.

`stockforge count shop your-shop-name` answers it without pulling anything.

### One listing is one design

A listing carries four to six images of the same product: one or two flat
artwork files and the rest staged photographs and marketing frames. They are
grouped as one design and read together, so the mockups become extra evidence
rather than four extra jobs.

And a design can have several printed surfaces. A greeting card is a front and
an inside. A wedding suite is an invitation, an RSVP and a details card. Each
becomes its own file, sharing one palette and one type system — which is how a
print shop wants them anyway.

---

## Analysis, built for local models

A hosted frontier model will fill a hundred-field nested schema in one shot. A
model on your own card will not, and pretending otherwise produces confident
rubbish. So the read is split into five small passes, each with a schema a local
model can actually hit:

| pass | what it does | who does the work |
|---|---|---|
| survey | how many surfaces, which image is which, real print dimensions | model |
| palette | the colours | **k-means**, model only assigns roles |
| typography | the text | **OCR** for the strings, model for the letterforms |
| structure | frames, rules, and every decorative element | model |
| provenance | can this be published, or is it master-only | model advises, **code decides** |

Two of those deliberately take work off the model. A model asked to eyeball a
hex value guesses; k-means measures it. A model asked to transcribe an address
gets it nearly right, and nearly right is wrong on something someone prints.

Any OpenAI-compatible server works — NVIDIA NIM, vLLM, Ollama, LM Studio. Two
environment variables and you are running:

```bash
SF_VISION_BASE_URL=http://localhost:8000/v1
SF_VISION_MODEL=nvidia/llama-3.2-90b-vision-instruct
```

Local models wrap their JSON in prose, leave trailing commas and occasionally
drop a brace. The provider layer extracts, validates and hands the validation
errors straight back for a repair round. Two retries fixes almost everything.

---

## Derivation — the part that decides if this is worth doing

The source design tells us a *style that sold*: this palette temperature, this
type hierarchy, this density of decoration. We keep the style and build a new
piece in it. That is what a design series is, and coordinated series are exactly
what stock buyers search for.

What does **not** work is nudging a hue, swapping one font and calling it new.
Reviewers see hundreds of those a day and agencies run similarity matching on
submission — a near-duplicate flagged against something already in the library
takes the whole batch down with it.

So there are four levers, in order of how much they change how a piece reads:

- **content** — new names, dates, venues. Cosmetic on its own, necessary anyway.
- **colour** — the whole palette rotates *together*, so the relationships
  survive. Shifting each colour independently is what makes a recolour look
  wrong.
- **type** — a different pairing with the same voice.
- **layout** — margins, rhythm, the spread of the type hierarchy. This is the
  one that actually makes it a different design.

Then it looks at both and scores two things that pull against each other: is it
distinct enough to stand alone, and does it still belong to the same family? A
result that is still too close goes round again. One that has lost the character
gets dialled back.

---

## Setup

```bash
pip install -e .
cp .env.example .env
apt install inkscape tesseract-ocr        # export and OCR
```

Fonts are the one manual step, and it is worth the hour:

```bash
stockforge fonts scan
```

Drop families you hold redistribution rights to into `assets/fonts/`, run the
scan, then open `assets/fonts/manifest.json` and set `embeddable: true` only
where you have checked the licence. Nothing is used until you do. Google Fonts
and anything under the SIL OFL are the easy wins here.

## Use

```bash
stockforge count  shop your-shop-name
stockforge pull   shop your-shop-name --limit 20
stockforge run    --limit 20
stockforge status
stockforge review
stockforge publish --dry-run
```

Every stage is resumable. Ctrl-C and re-run; finished work is skipped.

---

## What it will not do

- **Rebuild a photograph.** A photorealistic or AI-generated scene used as the
  artwork is flagged, not faked. It gets an editable master with the scene left
  as a placed image, and it never goes near a publish queue.
- **Recover an exact font.** Deliberately never attempted. The analyser
  describes letterforms; the matcher picks the nearest thing you are allowed to
  embed.
- **Invent a motif library.** A detailed painted character is not a spec, it is
  an illustration. It gets flagged, and the fix is to add a good one to
  `assets/motifs/` once and reuse it across hundreds of files. That library is
  what decides whether output looks professional.
- **Judge your market.** The loops score against craft and against
  distinctiveness. They do not know what sells. That is what the review queue is
  for.

## Formats

Two exports from the same SVG, for two different jobs:

- **`*-master.pdf`** — layered, with editable text. Yours, for changing.
- **`*.eps` + `*-preview.jpg`** — text outlined, no font references. What the
  agencies ingest; neither takes PDF as a vector submission.

Agency requirements and CSV layouts change. Check the current contributor
documentation before a large upload rather than trusting these notes.
