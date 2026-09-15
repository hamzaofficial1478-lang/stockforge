"""Niches, and the wall between them.

The owner works on Halloween cards for a month and then moves to business
cards. Both are in the same program and the same database, and a palette
borrowed across that line is not a new design — it is a mistake nobody would
ship and everybody would notice.

There was a wall before this and it was not one. Donors were matched on the
`occasion` string the model wrote, and when nothing matched it widened to the
whole pool. So a niche with only a few designs in it borrowed from every other
niche, which is the failure at its worst: it happens exactly when the new niche
is small, and that is exactly when nobody is checking.

A niche is named by the owner, never inferred. Nothing is made until one is
chosen, and everything made is filed under it.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("stockforge.collections")

# How many designs a niche needs before it can be mixed from. Below this the
# combinations available are so few that a run repeats itself whatever the
# ledger does — the mixer is drawing ingredients from too small a bag.
SEED_DESIGNS = 24

# Words that say nothing about which niche this is. "Halloween cards" and
# "Halloween card designs" are the same niche and should not become two.
NOISE = {"design", "designs", "card", "cards", "template", "templates",
         "printable", "printables", "invite", "invites", "set", "pack",
         "collection", "the", "a", "an", "and", "for", "my", "new"}


def slug(name: str) -> str:
    """A stable id for a name somebody typed.

    Punctuation and case go, because "Halloween Cards", "halloween cards" and
    "Halloween-Cards" are one niche and making three of them is the mess this
    is here to avoid.
    """
    out = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return out[:60] or "unnamed"


def _words(name: str) -> set[str]:
    """The words that actually identify a niche."""
    found = {w for w in re.split(r"[^a-z0-9]+", (name or "").lower()) if w}
    meaningful = found - NOISE
    return meaningful or found


@dataclass
class Match:
    """What was found for a name somebody typed."""

    slug: str = ""
    name: str = ""
    exact: bool = False
    near: list[dict] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.slug)


def look_up(name: str, existing: list[dict]) -> Match:
    """Find the niche a typed name means, and say how sure that is.

    Exact on the slug is certain. Anything else is a suggestion and is returned
    as one: picking a near match on the owner's behalf is how a month of
    business cards ends up filed under Halloween.
    """
    want = slug(name)
    if not existing:
        return Match()

    by_slug = {c["id"]: c for c in existing}
    if want in by_slug:
        hit = by_slug[want]
        return Match(slug=hit["id"], name=hit["name"], exact=True)

    wanted_words = _words(name)
    scored: list[tuple[float, dict]] = []
    for c in existing:
        theirs = _words(c["name"]) | _words(c["id"])
        shared = wanted_words & theirs
        # A shared identifying word is the strong signal — "halloween" in both
        # means both are about Halloween, whatever else was typed around it.
        score = len(shared) / max(1, len(wanted_words | theirs))
        score = max(score, difflib.SequenceMatcher(None, want, c["id"]).ratio() * 0.9)
        if shared:
            score += 0.4
        if score >= 0.45:
            scored.append((score, c))

    scored.sort(key=lambda pair: -pair[0])
    return Match(near=[c for _, c in scored[:5]])


def ready(collection: dict | None, least: int = SEED_DESIGNS) -> tuple[bool, str]:
    """Has this niche enough read designs to mix from, and if not, what to do.

    `read` rather than `designs`, because a row that has been pulled in but
    never looked at contributes nothing to a mix. The distinction matters: "you
    have 30" when none of them have been read is the sort of encouragement that
    wastes an afternoon.
    """
    if not collection:
        return False, "No niche chosen. Pick one before making anything."
    have = int(collection.get("read") or 0)
    if have >= least:
        return True, ""
    pulled = int(collection.get("designs") or 0)
    failed = int(collection.get("failed") or 0)
    unsure = int(collection.get("unsure") or 0)
    waiting = max(0, pulled - have - failed - unsure)
    fix = (f"{collection['name']} has {have} design{'' if have == 1 else 's'} read "
           f"in, and needs {least} before it can be mixed from.")
    # Naming the stuck ones is the difference between a wall and a door. Three
    # failed reads and the count simply stops going up, with nothing on screen
    # saying why or what to do about it.
    if failed:
        fix += (f" {failed} failed — retry {'it' if failed == 1 else 'them'} "
                f"rather than pulling more in.")
    if unsure:
        # These read, and they read the photograph. Silently not counting them
        # is the same wall with no door the failures used to be — the number
        # stops moving and the screen says nothing about why.
        fix += (f" {unsure} read through the listing photo without finding the "
                f"artwork in it, so {'it does' if unsure == 1 else 'they do'} not "
                f"count — crop {'it' if unsure == 1 else 'them'} to the artwork "
                f"and pull in again, or use the flat file if you have one.")
    if waiting:
        fix += (f" {waiting} more {'is' if waiting == 1 else 'are'} pulled in but "
                f"not read yet — run the queue.")
    if not failed and not waiting and not unsure:
        fix += f" Pull in {least - have} more and run the queue."
    return False, fix
