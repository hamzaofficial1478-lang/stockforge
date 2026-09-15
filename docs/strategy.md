# What this program is for, and what it is not

Written down because it was worked out once, in conversation, and would
otherwise be lost. Every number here was measured rather than estimated, and
the measurements are reproducible from the code.

## The target

**Forty-eight designs in one to two hours.** Not forty-eight a day. The owner's
alternative is driving an AI by hand, which produces about twenty editable
designs in thirty minutes, so anything slower than that is worse than not using
this program at all.

## Where the time actually went

A full build with the model answering instantly takes **3.1 seconds** — flatten,
compose, derive, render, export to SVG and PDF. The code is not slow.

What was slow is the shape. One design was eight separate model calls, strictly
one after another:

| | calls | what it asked |
|---|---|---|
| survey | 1 | which images, which are staged photographs |
| palette | 1 | the colours and their roles |
| provenance | 1 | is any of this a photograph |
| typography | 1 | the type, against OCR |
| structure | 1 | shapes, artwork, where everything sits |
| new copy | 1 | fresh wording — **sends no images** |
| distinctiveness | 1 | is the result different enough |
| critique | 1 | is the layout any good |

At the ~3.7 minutes a call a hosted reasoning model takes, that is thirty
minutes. Forty-eight of those is three hundred and eighty-four calls — about
twenty-four requests running at once, continuously, for an hour. No hosted
endpoint will give you that, so the old shape could not be tuned into the
target. It had to change.

## The split that fixes it

**Reading** a design needs a model to look at it. Five calls. Unavoidable, and
only ever paid **once per source, ever**.

**Making** a design from a reading you already have needs no model at all,
except the wording — and the wording call sends no images, so one request can
write for a whole run.

So a batch of forty-eight is: one copy call, then forty-eight lots of local
drawing. Measured on a ten-design pool: **one model call, about a second a
design.**

## The two piles, and which path each takes

**Pile A — recovering the 5,000 originals.** Every one has to be looked at,
because the whole point is reading a specific design whose file is lost. Five
calls each, no way round it. Parallelised across lanes it is roughly thirty an
hour, so the backlog is about a week of continuous running. It is a one-off and
it is meant to be left alone.

**Pile B — new designs for the agencies.** Read fifty sources once, then make
from the pool. This is where forty-eight in an hour lives, and it is minutes
rather than hours.

## The honest limit

Designs out of pile B are **your own back catalogue recombined** — a grid from
one, a palette from another, decoration from a third, new wording on top. That
is precisely what the program was specified to do and every ingredient is
yours, so every output is safe to sell.

What it is not is forty-eight freshly invented designs. There is no model
inventing a layout here; there is a recombiner working from what you already
own. **If what you want is novelty, driving an AI by hand is the better tool
and always will be.** Keep both. Use this for volume from your own material and
for the recovery backlog; use your hands for the pieces that need a new idea.

## Why a batch does not come out looking like one design

This is the failure mode that would make the whole path worthless, so it is
defended three ways.

A mix is a small set of choices — whose layout, whose palette, whose type,
whose decoration, whose grid — and a pool holds only so many of them. Draw
forty-eight at random from fifty designs and the same combination turns up five
or six times.

1. **Every recipe is fingerprinted and written to a ledger.** A combination that
   has ever shipped cannot ship again, and the claim is written when the recipe
   is chosen rather than when the design finishes, so two lanes cannot both take
   the last free one.
2. **Donors are weighted by how often they have already been drawn on.** Without
   it, one or two favourites supply a third of every batch. Weighted rather than
   strictly rotated, because a rotation is its own kind of sameness.
3. **A design that comes out looking like one you already have is thrown away
   and replaced**, not queued for review. Handing back a pile of near-identical
   designs and asking which to keep is the work this path exists to remove.

When the pool genuinely cannot give what was asked, it says so and says why,
rather than padding the run with repeats.

## Where an image model fits — and it is one place only

The **motif gaps**: decoration the analyser found in your own designs that the
library has no drawing for. Seven of them in one Halloween card, each one
stopping its design dead. Harvesting cuts those out of your own artwork where
they exist; drawing is for the ones that do not.

`stockforge motifs draw`, or the button on Review. It asks for flat vector
style, one subject, solid colours, plain white ground, and rules out text,
gradients and drop shadows — because the job is something that traces cleanly,
not something that looks finished. A soft painterly render is prettier and
useless.

**The output is reference, never a deliverable**, and that is about rights
rather than taste. A generated illustration is not a thing you own outright,
and the reason every delivered file matches against your own library is that
every curve in it is yours to sell. So three things hold that line, and each
has a test that fails when it is removed:

- The files land in `assets/motifs/_drawn/` as PNGs, and the library scan only
  ever globs `*.svg`. A generated picture cannot be picked up as a motif and
  placed into a design.
- A JSON note sits beside each one recording the prompt, the model and the
  date. Six months on, "did I draw this or did a model?" is not a question to
  answer by squinting at it.
- One model refusing a prompt does not lose the rest of the batch. Six of seven
  beats none.

Trace what you like to SVG on a 0..100 square, put the trace in the library, and
the trace is yours.

## Done since

**The four independent reading calls now run together.** Palette, provenance,
typography and structure depend only on which image the survey picked, not on
each other. Five waits became two. Measured with a fixed one-second model:
6.1 s down to 3.0 s, so on a hosted model that is roughly eighteen minutes of
reading down to seven. `SF_VISION_CONCURRENCY` caps how many are in the air at
once — four covers a single-page card exactly, and the cap is there so a
twelve-page wedding suite does not open twenty-six connections and get rate
limited for it.

The provider is captured and handed to each worker rather than looked up inside
it. A lane installs its model for its own thread only, so a thread that asked
`providers.vision()` would get whatever the environment says instead — correct
looking output from the wrong endpoint, which is the worst shape of bug there
is. There is a test named after it.

**A retry no longer re-reads.** `build()` kept the analyser's read and threw it
away on the next build, so pressing Run again on a design in Review spent five
model calls arriving at an answer already in the database. Measured: a retry
went from 8 calls to 3. The reading is what a model saw in the artwork, and the
artwork has not changed; what a retry is actually retrying is everything after
it.

Two buttons in Review now, because they are different jobs. **Run again** mixes
and draws from the reading on file. **Read it again** looks at the artwork from
scratch — for when the flattening or the prompts have changed underneath a
stored read. A design that failed *during* reading has nothing stored, so it
reads again on its own without being asked.

## What may lend an ingredient, and what may not

Two rules, and both decide whether a delivered file is safe to sell.

**Only a design somebody actually looked at.** A design this program made has a
spec but no reading. Mixing from those compounds: the third run is a mix of
mixes of mixes and drifts away from anything anybody chose. It was doing exactly
that — measured, thirty seed designs became thirty-six donors after one run of
six. Donors now require `read_json`, so the pool is the designs that were read
and stays that size.

Which is also the answer to "do I have to hand the designs over again?" — **no**.
Twenty-four designs go in, get read once, and every run afterwards reuses that
reading. There is a test that watches which passes run and fails if a batch
reads anything.

**Only a design that is yours.** Every ingredient in a delivered file being your
own is the entire reason the file is safe to sell — it is the premise the
program was built on. So designs come in marked one way or the other:

- **Mine** — from your own shop, your own folder, your own uploads, or a link
  you say is yours. These lend palettes, grids, type and decoration.
- **Reference** — anything from somewhere else. Read, listed, flattened, there
  to look at. It never lends anything to anything that gets made.

The links door asks, and the safe answer is the default: a pasted link is
reference unless you say it is yours. The unsafe direction needs a deliberate
act, which is the right way round for a rule nobody can see working.

Your own designs listed on other platforms are still yours — marking them so is
all it takes. What this will not do is turn somebody else's stock listing into
an ingredient of something you then sell, and that is not a technical limit. A
mix of other people's work sold as stock is takedowns, a closed account, and a
real cost to the artist it came from.

## Niches: keeping one catalogue out of another

The owner works a month on Halloween cards and then moves to business cards.
Both live in the same program and the same database, and a palette borrowed
across that line is not a new design — it is a mistake everybody notices.

There was a wall before and it was not one. Donors were matched on the
`occasion` string the model wrote, and **when nothing matched it widened to the
whole pool**. So a niche with only a few designs in it borrowed from every other
niche. That is the failure at its very worst: it happens exactly when the new
niche is small, and that is exactly when nobody is checking.

A niche is now named by the owner and never inferred.

- **Nothing is made without one.** The Make button is unavailable until a niche
  is chosen, and `make()` refuses with a reason rather than guessing.
- **Donors come only from inside it.** The pool is a join against the niche, not
  a filter applied afterwards, so an unfiled design cannot leak in.
- **A niche needs 24 read designs before it can be mixed from.** Below that the
  combinations available are so few that a run repeats itself whatever the
  ledger does — the mixer is drawing from too small a bag. `SF_SEED_DESIGNS`.
- **The ledger is per niche**, so starting a new one does not begin
  half-exhausted, and clearing one does not clear the others.
- **Files land in `out/<niche>/`.** "Are they mixed?" is a question answered by
  opening a folder, not by running a query.
- **The choice survives a restart** — it is in `.env` as `SF_COLLECTION`, so
  coming back the next morning cannot quietly file into a different niche.

Typing a name asks one question: *have you built these before?* Saying yes
looks it up and refuses to invent one; saying no creates it and refuses to
quietly merge into an existing one. A near match is **offered and never taken** —
choosing one on the owner's behalf is precisely how a month of business cards
ends up filed under Halloween. "halloween cards" suggests "Halloween
invitations"; "business cards" suggests nothing at all, which is the half that
matters.

Designs from before any of this go to a niche called `unfiled`, rather than
being guessed at or silently joining whatever is started next.

## A smaller model for the easy questions

Three of the five reading passes do not need a big model. The palette is already
measured off the pixels and the model only names the roles; provenance is "is
any of this a photograph"; the survey is "which of these images are pages".
Those three go to `SF_QUICK_MODEL` where one is configured, and because
everything runs side by side the two hard passes — type and structure — set the
pace rather than the sum of all five. Leave it unset and the reading model does
all of it exactly as before.

## Getting to twenty-four when the reading keeps failing

A real run, two lanes, three designs, two of them dead:

    13:17:44 lane 2 failed: could not produce valid PaletteRead:
             no parseable JSON in response

Both models in the chain answered the palette in prose and the whole design was
lost with them. Which is a bad trade — the design had already been downloaded,
flattened and half read, and what went unanswered was **which colour is the
paper**, on a pass whose colours are measured off the artwork before a model is
involved at all.

So the reading passes are split by whether a design can be built without them:

- **Type and structure are the design.** No type and no shapes means nothing to
  draw, and failing is the only honest outcome.
- **The palette and the provenance are not.** A failed palette falls back to a
  rule — biggest area is the paper, darkest is the ink, most saturated of the
  rest is the accent — and the design carries a note saying so. A failed
  provenance holds the design back from stock rather than losing it, because
  "we could not check" has to mean master-only when getting it wrong costs the
  contributor account.

And the twenty-four gate needed a door in it. Three failed reads and the count
simply stopped going up with nothing saying why. Now the wait names what is
stuck — *"3 failed — retry them rather than pulling more in"* — and there is one
action that puts every failed design back in the queue, because the alternative
is finding them by eye in a list of five thousand, which nobody does.

Two more things the error messages needed. A failure now carries **what the
model actually said**: "no parseable JSON in response" describes empty, refused,
prose and truncated equally, and those want four different answers.

### Two things the same run showed next

With the designs no longer dying, what they did instead was visible — and both
were the program blaming a design for something it had not done.

**A photograph is not something to measure a rebuild against.**

    'cover': the rebuild is the wrong shape — 0.71 against the source's
    1.00, so the trim was misread

The source is a square listing photograph that was never cropped to the card.
The rebuild is 0.71 because that is what a 5x7 card is — it was right, and the
reason pointed at the wrong end of the problem. The shape check now runs only
when the source really is the artwork; the trim state was already recorded and
nothing had asked it.

**A variation needs something to vary from.**

    round 1: worst distinct=0.10 -> derive_further
    round 2: worst distinct=0.10 -> derive_further
    ac131054 -> review

The second round borrowed every ingredient it could and the score did not move,
because every ingredient came from the same single donor. Three model calls and
four minutes to learn something countable in advance.

### The learning phase

The fix above was a floor of four donors, and four donors is not a catalogue
either. The owner put it plainly: the first twenty-four designs are the program
learning, and the twenty-fifth is the first design worth looking at.

So that is now the whole rule, and there is one number rather than two.
`SF_SEED_DESIGNS` (twenty-four) governs a single design and a batch of
forty-eight alike. Until a niche has that many read in, every design that comes
through is read properly, filed, and handed back as an editable master — and
nothing is varied, nothing is scored for distinctiveness, and nothing goes to
review for being too close to its source, because of course it is.

What that buys, beyond correctness: the seeding run is now about four minutes a
design instead of five and a bit, because the two compose-derive-score rounds
never happen. Twenty-four designs of reading on two lanes is under an hour, and
it is a one-off per niche.

A gap in our own library stops queueing a review during that phase. Twenty-four
designs each saying "no library match for a jack-o'-lantern" is twenty-four rows
saying one thing, and that one thing is already on the motif-gaps list, ranked
by how many designs are waiting on it. Drawing from that list is the work;
clearing the rows is not. What still queues is anything meaning the *read* is
wrong — a photo we could not find the artwork in, a surface that came back as
placed pixels, type that would not fit the box. Those poison the pool, and the
pool is the entire point of reading these in.

### Twenty-four bad reads is not a catalogue

One more thing the same log showed, sitting under the others:

    the artwork was not found inside the listing photo — it looks like a
    photo of the design rather than the design itself

A read taken that way measured the table as well as the card. Its grid, its
margins and a good part of its palette belong to somebody's kitchen worktop. The
program said so at the time, queued the design for review — and then counted it
towards the twenty-four anyway, and let it lend ingredients like any other.

There is now one definition of a design fit to learn from (`FIT_TO_LEARN_FROM`
in `db.py`), used by the donor pool and by every place that counts how far a
niche has got, because a gate that opens on a number the screen does not show is
not a gate. Such a design can still be listed, looked at, and recovered as an
editable master. It is not one of the twenty-four, and it lends nothing.

The count stalling is then something the screen has to explain, or it is the
wall-with-no-door failure in its third costume — so `ready()` says how many were
read through a photograph and what to do about it (crop to the artwork and pull
in again, or use the flat file).

### A description is not nothing

Three designs of seven died in one run, both models in the chain the same way:

    could not produce valid TypeRead: no parseable JSON in response
    It said: The image depicts a Halloween-themed invitation, featuring a
    white background with a purple border and a central illustration of a
    haunted house ... * A ghost * Bats * A jack-o'-lantern * A crescent moon

That is the design. It looked, it got it right, and it wrote prose. Retrying
does not help: a small vision model asked for JSON about a picture is doing two
hard things at once and the one it drops is always the formatting.

But the looking is the expensive half and it has already happened. So the last
thing `structured()` tries is handing those words back with **no image
attached** and asking for the schema — which turns the job into transcription,
which is the half these models are fine at. It only runs on the failure path, it
says so on the design when it fires, and a model that genuinely cannot answer
still fails with what it actually said in the message.

### How long twenty-four actually takes

Timed off a two-lane run of seven designs, stage by stage:

| stage | lane time |
|---|---|
| read (4 questions, in parallel) | about 2 minutes |
| compose, derive, score, critique | about 1 minute |
| a design that failed all three attempts | 3m 45s |

So a seed design, which now stops after the read, is roughly two minutes of lane
time — and the failures, which were the most expensive thing in the run, are
mostly rescued rather than repeated.

| lanes | 24 designs, seeding |
|---|---|
| 1 | about 50 minutes |
| 2 | about 25 minutes |
| 4 | about 15 minutes |

It is a one-off per niche, and it is the only expensive part. The forty-eight
designs that come afterwards are the batch path, which is one model call and
under a minute for the lot.

## The crop, measured on real listings

Eight real Etsy Halloween invitations, pulled from the shop and run through the
detector. Seven came back:

    the artwork was not found inside the listing photo

with every candidate rectangle scored at exactly zero. Not a near miss — a hard
reject, on cards sitting in plain view in the middle of the frame. Two bugs,
both in the ranking rather than the detection.

**A real design is not two flat colours.** The gate was
`flat_colours(quad) >= 4`. Those cards needed eleven to sixteen: a rendered
skull, spilled wine, blood splatter, cobwebs and four weights of type is not
two flat colours. The threshold was measured on this project's own fixtures,
which are simple, and a real shop is not. It is the same lesson the type signal
already carries and it is worth writing twice: **a cheap measure may choose
between candidates, and must never reject one.** Flatness is now judged
relative to the photograph the rectangle sits in, which needs no tuning and
reads a two-colour card the same way as a twenty-colour one.

**The type signal was voting for the tablecloth.** Ranking used
`text_held` — how much of the page's type falls inside this box — which only
ever goes *up* as the box grows. The rectangle that had swallowed the card, the
table and the candle scored a perfect 1.00; the card itself scored 0.57. The
one signal meant to find the design was systematically choosing the biggest
rectangle on every photograph in the shop. It is ink *density* now, which
cannot be won by growing: taking in more table adds area and no ink.

Result on those eight photos: nothing usable before, and afterwards all eight
find the card — five clean crops, two that clip an edge, one wrong (a card
half-behind a kraft envelope, which is hard for a person too).

**And the doubt survives — on the aspect, not the confidence.** Finding a card
is not the same as framing it right, and cropping confidently would have been a
quiet downgrade: a wrong crop that nothing flags is worse than no crop, because
everything downstream then measures itself against the wrong rectangle and says
nothing.

The first attempt was a confidence bar. On those eight photos it looked clean —
five good crops at 0.89 to 1.00, two clipped ones at 0.77 and 0.80 — and then
this project's own fixtures were measured against it. A white card on a white
backdrop, which is the commonest photo in the shop and the hardest case here,
crops to an aspect error of 0.003 and scores **0.81**. The bands overlap.
Confidence measures how clear-cut the *detection* was, not whether it was right,
and hard-but-correct scores the same as easy-but-wrong. The bar was dropped and
there is a test standing over the hole so it is not put back.

What does separate them is the shape. A printed card is 5x7, or A6, or 4x6, or
square; the clean crops came out at 0.665 to 0.766 and the two clipped ones at
0.855 and 0.859, which is near no trim anybody prints. So a crop that lands
between sizes is still made and still used — it beats the whole photograph
either way — and the design is held out of the donor pool and shown for
checking.

## A dead key must not cost a design

    "each time one or 2 keys remains unanswerable, that making program failure"

Correct, and it was one line. `_worth_moving_on` treated HTTP 401 and 403 the
same as 400 — raise, do not walk down the chain — on the reasoning that a
refused request is refused everywhere. That reasoning holds for 400 and does
not hold for the other two: they are not about the request at all, they are
about *this endpoint's key*, and the next model in the chain is a different
endpoint with a different key in a different environment variable. One expired
or over-quota key killed the design while a model perfectly able to answer it
was never asked.

A chain exists so that a dead credential costs a second of latency, not a
design. 400 stays where it was.

## The second way to make something: written from a brief

    "if we are having a good vision llm model i think there will be no any
     need to make program firstly read 24 designs"

Half right, and the half that is right matters.

The twenty-four exist because the mixer builds a design out of *parts of
designs it has read* — a grid from one, a palette from another, decoration from
a third. Take the bag of ingredients away and there is nothing to mix; that is
the whole reason a pool of two produced the original with its hue nudged.

But the renderer has never cared where a spec came from. A model can write one
from a brief, and then the same drawing, the same fonts, the same exporter and
the same twin check all apply. So `stockforge invent` — and the "Write new
designs from a brief" card in the panel — makes designs with **nothing read at
all**. No twenty-four, no donor pool, no gate.

What it costs, and it is worth saying plainly because it is the whole trade:

  - the result is not derived from the shop's catalogue, so it carries the
    shop's style only as far as the brief described it;
  - it is one model call per design, where the batch path does forty-eight in
    one request.

So it is the way to *start* a niche, not the way to fill one. Write four, read
those four back in if you like them, and the mixer has something to work with.

**The line that made it usable.** The first run produced three designs and all
three went to review: *"no library match for a sprig of eucalyptus"* — for a
eucalyptus that was sitting in the library under a different form of words. Two
causes. The invent path never called `motifs.resolve`, which `build` does after
reading, so every motif came out with `library_id` unset. And the model was
being asked to design in the dark. It is now told exactly what the library can
draw, in the library's own wording:

    Drawings available to you, and ONLY these: Arched frame; Thin rule;
    Eucalyptus sprig

Asking for a motif in the words the library already uses scores 1.00 against
0.84 for a good paraphrase, and against nothing at all for a reasonable request
the library has never heard of. With an empty library it is told to use no
motifs and carry the design on type, colour and shape, which is a real design;
a page full of holes is not.

**Why this may still go to an agency.** The layout comes from a model, and
every mark on the page is set in our own fonts and drawn from our own motif
library. Nothing third-party is embedded, which is the same footing a mixed
design stands on. Anything that does not resolve is reported as a hole and the
design goes to a human — the existing behaviour, and the reason provenance can
be set here rather than guessed at.

### Measured against real Gemini

The invent path was built against a scripted provider and then run against real
Gemini through a local bridge, which is where it stopped being theory. Three
rounds, and each one found something a fixture could not.

**Round one — it designed with what we have.** Told the library holds an arched
frame, a thin rule and a eucalyptus sprig, it used exactly those three, and
wrote copy nobody would be embarrassed by: *A WICKED NIGHT — ANNUAL HALLOWEEN
MASQUERADE — BLACKWOOD MANOR, 1313 CEMETERY LANE — RSVP IF YOU DARE.* But it
went to review: a bullet character the face had no glyph for, and a title
shrunk to **43%** of its asked-for size to fit its box.

**Round two — the prompt was given the arithmetic, and it still did not work.**
The bullet went away. The title was still at 43%, and the model was following
the rule correctly: every line it wrote satisfied the formula it had been
given. The formula was wrong. `size_ratio` is a cap height as a fraction of the
canvas HEIGHT, the em is that over the face's own cap ratio, and the box is a
fraction of the WIDTH — so converting between them needs the page aspect and
the metrics of a font file nobody has opened. A prompt cannot carry a font's
metrics, and no amount of rewording was going to fix that.

**Round three — measured in code.** `fit_type` opens the same face the renderer
will use, measures with the same `measure`, and brings any oversized line down
before the design is drawn. Three for three came out `ready`.

The lesson is the one this project keeps relearning from the other direction:
when a rule needs a number the model cannot see, stop explaining and go and
measure it.

Twenty-two seconds a design through the bridge, single file.

## Pointing the program at a local bridge

Any server that speaks OpenAI's `/v1/chat/completions` is a first-class model
here — that is how NIM, vLLM, Ollama and LM Studio are run, and it is equally
how a web-to-API bridge is run. Verified end to end against a stand-in server
that speaks `/v1` and nothing else: saved, tested, made live, and a real design
sent through it carrying its images.

Setup → Models → **Add a connection**, base URL `http://127.0.0.1:8000/v1`,
the model name the bridge serves, key blank. Test it, then Make live.

That form used to live inside a collapsed `<details>` on a screen that said
"no connections yet" directly above it, which is why it read as "the feature is
there but does nothing". It opens by itself now when there is nothing set up.

The thing the panel cannot do is *be* the bridge. Pointing it at an address
where nothing is listening fails the Test button, correctly.

## What a web bridge can and cannot carry

Measured, because the source code said one thing and the program said another.

A local Gemini bridge answers **text** calls well: 13 seconds for a connection
test, 22 seconds a design, three of three `ready` writing from a brief. It
answers **image** calls not at all. It accepts the picture, uploads it to
Google, returns `200`, and never sends a body.

Three configurations, all identical — the full 1588px listing photo, the same
shrunk to 640px, and again with `httpx` installed so the bridge does true
streaming rather than buffering. Every one: `sent nothing for 180s`, twice per
design, nothing written.

The control is what makes that a conclusion rather than a guess. A text-only
call to the same bridge, in the same session, moments after an image call had
failed, came back in 13 seconds. Not throttling, not an exhausted anonymous
quota. Image calls.

Two things worth keeping from getting there. The first version of the bridge
page said reading designs through it works, because the bridge's source really
does decode the base64 stockforge sends and its log really does say `Image
uploaded` — but decoding and uploading is not answering, and a claim read off
source code is not a measurement. The second: the first two runs were made
against a bridge with no `httpx`, in buffered mode, which is not the setup the
page tells the owner to build. Testing a configuration you did not recommend
proves nothing about the one you did.

## Closing the motif loop: the trace

The chain was complete except for one link, and without it the whole thing did
nothing. A design asks for a carved pumpkin; the library has not got one;
`motifs draw` asks an image model for a picture of one and writes it to
`_drawn/` with a note saying

    "Reference only. Trace it to SVG before using it in a design."

and there it stopped, because the library only ever globs `*.svg`. Asking a
model for a pumpkin produced homework. The same was true of every cutout
`harvest` took out of the owner's own artwork.

So: `stages/trace.py`, and `motifs.adopt` on top of it. **Not potrace** — that
is a binary dependency for a project whose whole shape is "standard library,
OpenCV, nothing to install" — but OpenCV, which is here already and is well
suited because the job is deliberately easy. The prompt that generates these
asks for *flat vector style, solid colours, clean even outlines, no gradient,
no photographic texture*, which is a picture made of a few flat regions.
Quantise, find the regions, follow their edges, write the paths.

`motifs draw` now traces by default, so a drawn motif is usable on the next
run. `motifs trace` does the same for pictures already on disk — everything
`harvest` cut out, and anything drawn back when nothing could trace it.

### Three artefacts, each found by looking at the output

**Faceted curves.** `approxPolyDP` gives straight segments, and a pumpkin in
forty straight segments reads as a pumpkin drawn by a committee. The same
points run through Catmull-Rom and converted to cubics follow the same outline
and arrive smooth.

**White seams.** Each colour is traced separately, so the anti-aliased pixels
along a boundary belong to neither side — the first trace had a white gap
around the eyes and mouth. Growing each region by a pixel closes them, and
costs nothing because regions are painted largest first, so a neighbour's
overspill ends up underneath.

**A pale halo right round the body.** k-means will happily spend a whole colour
on the soft pixels along a boundary, and traced, that colour comes back as a
ring around the shape it borders. The test that catches it needs no threshold
on colour: *a shape has an inside, a rim does not.* Erode it; if almost nothing
survives, it was never a shape. The same test applied per contour as well as
per colour, because the green of a stalk also picked up the rim around two
eyes — solid enough as a colour to pass, and painting green rings when drawn.

### What a trace is, and is not

It is a redrawing: fewer colours, simplified edges, specks dropped. That is the
right trade for something printed at 40mm beside a line of type, and it is why
what goes in the library is the owner's own paths rather than a model's raster.
The PNG stays beside it as the reference it always was and is never placed in a
design. Where a drawing came from is written **inside** the SVG, not in a file
next to it, because a file next to it gets separated from it the first time
somebody tidies up — and six months on, "did I draw this or did a model?" is a
question about whether it may be sold.

## Measuring it on your own models

`stockforge bench --count 12` times a batch against the endpoints actually
configured, and counts the model calls by wrapping the provider rather than
estimating. It reports calls per design, seconds per design, and what an hour of
that would come to. It undoes itself afterwards unless you pass `--keep`:
benchmarking should not quietly fill a niche with designs nobody asked for, nor
burn combinations out of the ledger.

Nobody else can run it. It needs the endpoints and the keys, and the only figure
that settles an argument about speed is one measured on the hardware the work
will actually run on.

## A drawing through the type

Four invitations came out of the invent path and were filed `ready`: files
exported, PDF and EPS written, nothing queued. Every one of them had the
headline sliced by an arch and a spider's web drawn across the opening line.

Type and decoration are placed from the same spec and neither has ever known
the other is there. Every check that runs on a finished design asks about one
element at a time — does this line fit its box, did this motif resolve, does
this face have that glyph — and a page can pass all of them and still be
unsellable, because the fault is in the relationship between two elements that
each passed. The one check that looks at the page as a picture is the critic,
and the critic needs a model that can see. Through the local bridge there
isn't one, so nothing was looking.

It is measurable without a model. The renderer already emits the page in
layers — background, artwork, structure, decoration, type — so the drawings
and the words rasterise apart and their ink intersects. That is not an
estimate of what the page looks like. It is what the page looks like.

**Not all overlap is a fault**, and getting that distinction right is the
whole of it. Type set on a filled banner is ordinary design and the drawing is
under every letter; type with a rule through it is broken and the drawing
takes a slice. So what gets flagged is the band between: measured on those
four cards, as a share of each line's own ink,

    0.0%                    clear of everything
    0.6%  0.8%              a serif kissing an arch leg
    2.4%                    a web strand through the B of BY ORDER
    7.5% – 10.3%            a leg through the last letter
    16.6%  30.5%  39.8%     the line is wrecked

A twenty-character line is about five percent of its ink per letter, so the
floor sits at two percent — half a letter struck through. Ink covered nearly
end to end is left alone as a ground.

Two things this needed that are worth naming. The check works from the ink
each line actually landed on, not from `el.box`: the box is where a line was
*allowed* to go, and the renderer fits, anchors and centres it inside that, so
a short centred line's box is mostly empty paper. And a rotated line gets no
rectangle rather than a wrong one — the honest answer to "where is it" is then
the whole page, and guessing would report a strike wherever the drawing is.

A placed picture is a ground too, whatever share of a line it covers, and it
is not judged at all. A photographic vignette and a painted object adopted
into the library are areas rather than strokes, and setting type across the
edge of one is a thing designers do on purpose — it is also what the analyser
reads back off a listing the owner already sells. The first full run of this
check sent a design with a small photo in it, and a design carrying a big
painted ghost, straight to the review queue. Both were ordinary work, and the
tests that said so had been written long before this existed. The pictures are
dropped before the drawn layer is rasterised rather than by leaving a whole
layer out, because a motif in this library may be a drawing or a picture and
only one of those can be a line through the words.

**What it costs.** 394ms a page, measured, against 14ms to render the page in
the first place — almost all of it the two Inkscape process spawns, not the
pixels. Over five thousand designs that is about half an hour of CPU on a job
whose time goes on model calls, so it is affordable and it is not free. One
pass would do instead of two if the layers were forced to separate colour
channels through CSS, but Inkscape and cairosvg do not agree closely enough on
CSS for that to be worth trusting a correctness check to — and a check that
quietly measures the wrong thing on somebody else's machine is worse than one
that costs 200ms more.

The model was told as well, and the telling has a cost worth being honest
about. Given "an arch is not a hollow shape you may set type inside", it
stopped asking for arches at all. The four cards that came back were clean and
they were also plainer. Catching the fault after the fact is what keeps the
arch usable; the prompt alone trades the problem for a smaller vocabulary.

## What would move the needle next

- **Measure the real per-call time per model.** Everything above is arithmetic
  on one measured figure; the next honest gain needs to know which of the two
  hard passes is actually the slow one.
- **Drop the palette call entirely.** The colours are already measured from the
  pixels. Naming their roles could be a rule rather than a request.
