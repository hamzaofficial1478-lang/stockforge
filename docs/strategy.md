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

## What would move the needle next

- **Measure the real per-call time per model.** Everything above is arithmetic
  on one measured figure; the next honest gain needs to know which of the two
  hard passes is actually the slow one.
- **Drop the palette call entirely.** The colours are already measured from the
  pixels. Naming their roles could be a rule rather than a request.
