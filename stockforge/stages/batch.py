"""Making many designs at once, without making the same one twice.

The reason this module exists is arithmetic. Reading a design costs five model
calls because a model has to look at it; making a new design from one already
read costs a handful of choices and three seconds of code. The old pipeline
paid the reading price for every output, so forty-eight designs meant three
hundred and eighty-four model calls and a day of waiting. Here the reading is
already done and the making is nearly free.

Which moves the difficulty somewhere else. A mix is a small set of choices —
whose layout, whose palette, whose type, whose decoration, whose grid — and a
pool of fifty designs holds only so many of them. Sample forty-eight at random
and you get the same combination five or six times over, and the batch reads as
one design with the words changed. That is the failure this module is built
against, and it is the one the owner will notice first.

Three things stop it. A recipe is fingerprinted and written to a ledger, so a
combination that has ever shipped cannot ship again. Donors are weighted by how
often they have already been drawn on, so no favourite supplies half the batch.
And where a combination genuinely has to be revisited, it is revisited at a
different derive seed and strength, so what comes out is not what came out
before.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Callable

from ..schema import DesignSpec
from . import compose as compose_stage

log = logging.getLogger("stockforge.batch")

# How many seeds to try before accepting that a base has no unused combinations
# left. Small because the space is small: past this the honest answer is that
# the pool is exhausted, not that the search needs longer.
TRIES_PER_BASE = 24


def fingerprint(recipe: compose_stage.Recipe, strength_band: int = 0) -> str:
    """What makes two designs the same design.

    The ingredients, not the seed: two runs that borrow the same palette from
    the same design onto the same layout have made the same thing, whatever
    random number led them there. The strength band is in it so that the same
    ingredients pushed hard and pushed gently count as two designs, which they
    visibly are.
    """
    parts = {
        "base": recipe.base,
        "palette": recipe.palette_from,
        "type": recipe.type_from,
        "motifs": recipe.motifs_from,
        "background": recipe.background_from,
        "notes": sorted(recipe.notes),
        "band": strength_band,
    }
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True).encode()).hexdigest()[:24]


@dataclass
class Planned:
    """One design the batch intends to make."""

    base: DesignSpec
    spec: DesignSpec
    recipe: compose_stage.Recipe
    fingerprint: str
    seed: int
    strength: float


@dataclass
class Plan:
    made: list[Planned] = field(default_factory=list)
    exhausted: bool = False
    note: str = ""

    def __len__(self) -> int:
        return len(self.made)


def _band(strength: float) -> int:
    """Derive strength in coarse steps. Two designs a hair apart in strength are
    the same design; a third of the way apart are not."""
    return int(round(strength * 3))


def plan(
    pool: list[DesignSpec],
    count: int,
    mix: float = 0.5,
    strength: float = 0.5,
    seen: Callable[[str], bool] | None = None,
    used: dict[str, int] | None = None,
    seed: int = 0,
    strict_family: bool = True,
) -> Plan:
    """Choose `count` combinations that have not been made before.

    `seen(fingerprint)` says whether a combination has already shipped; `used`
    is how often each design has been borrowed from. Both come from the ledger
    in the database, and both are optional so this can be reasoned about on its
    own.
    """
    import random

    out = Plan()
    if not pool or count <= 0:
        out.note = "nothing to work from" if not pool else ""
        return out

    rng = random.Random(seed)
    seen = seen or (lambda _f: False)
    tally = dict(used or {})
    taken: set[str] = set()

    # Bases in turn rather than at random: forty-eight designs off one layout is
    # the most obvious way for a batch to look like one design.
    order = sorted(pool, key=lambda s: tally.get(compose_stage._identity(s), 0))
    attempts = 0
    while len(out.made) < count and attempts < count * TRIES_PER_BASE:
        base = order[len(out.made) % len(order)]
        found = False
        for _ in range(TRIES_PER_BASE):
            attempts += 1
            this_seed = rng.randrange(1 << 30)
            # Strength varies across the batch on purpose. The same ingredients
            # pushed by different amounts are different designs, and it is the
            # cheapest variety there is — no model, no extra donor.
            this_strength = max(0.05, min(1.0, strength * rng.uniform(0.6, 1.4)))
            spec, recipe = compose_stage.compose(
                base, pool, mix=mix, seed=this_seed,
                strict_family=strict_family, used=tally)
            mark = fingerprint(recipe, _band(this_strength))
            if mark in taken or seen(mark):
                continue
            taken.add(mark)
            for who in (recipe.base, recipe.palette_from, recipe.type_from,
                        recipe.motifs_from, recipe.background_from):
                if who:
                    tally[who] = tally.get(who, 0) + 1
            out.made.append(Planned(base=base, spec=spec, recipe=recipe,
                                    fingerprint=mark, seed=this_seed,
                                    strength=this_strength))
            found = True
            break
        if not found and attempts >= count * TRIES_PER_BASE:
            break

    if len(out.made) < count:
        out.exhausted = True
        out.note = (
            f"asked for {count} and found {len(out.made)} that have not been made "
            f"before. {len(pool)} design{'' if len(pool) == 1 else 's'} in the pool "
            f"only holds so many combinations — read more of your catalogue in, or "
            f"clear the ledger to revisit old ones.")
        log.info("%s", out.note)
    return out
