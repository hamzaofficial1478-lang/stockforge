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

## Where an image model fits

Nothing here generates artwork, and it should not: a generated illustration is
not something you own the rights to sell without care. The one place an image
model earns its place is the **motif gaps** — decoration the analyser found in
your designs that your library has no drawing for. Those currently stop a
design dead. A generated motif would be a starting point to trace, in the same
way a harvested crop is. That is the only slot it should be given, and the
output should be treated as reference rather than as a deliverable.

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

## What would move the needle next

- **Take the reasoning effort off the cheap passes.** Palette barely needs a
  model at all — the colours are already measured from the pixels and only the
  roles need assigning.
- **A faster model for the reading passes.** Five calls at 3.7 minutes is the
  remaining cost of the recovery backlog, and most of those questions are not
  hard enough to need a 30B reasoning model.
