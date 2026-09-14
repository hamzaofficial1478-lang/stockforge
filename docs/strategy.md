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

## What would move the needle next

- **Measure the real per-call time per model.** Everything above is arithmetic
  on one measured figure; the next honest gain needs to know which of the two
  hard passes is actually the slow one.
- **Drop the palette call entirely.** The colours are already measured from the
  pixels. Naming their roles could be a rule rather than a request.
