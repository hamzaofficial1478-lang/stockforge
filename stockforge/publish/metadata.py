"""Titles, keywords and the CSVs each agency wants alongside the files.

Metadata is not an afterthought here — on a stock site it is most of your
discoverability, and it is the one part of a submission a model is genuinely
good at. The spec already knows the occasion, the category, the style tags and
the motif vocabulary, so the keywording is grounded in what is actually in the
file rather than guessed from a thumbnail.

Column layouts change. Check each agency's current contributor documentation
before a large batch rather than trusting these comments.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from ..providers import VisionProvider, reason
from ..schema import DesignSpec

log = logging.getLogger("stockforge.publish.metadata")


@dataclass
class Metadata:
    filename: str
    title: str
    keywords: list[str] = field(default_factory=list)
    category: str = ""
    description: str = ""


class MetadataDraft(BaseModel):
    title: str = Field(description="one line, 60-180 characters, plain descriptive English")
    keywords: list[str] = Field(description="30-45 single words or short phrases, most important first")
    category: str = ""


SYSTEM = """You are writing the listing metadata for a vector design being \
submitted to a stock agency.

The title is a plain description of what the file IS, written for search rather \
than for poetry. "Halloween party invitation template with haunted house and \
jack o lanterns, purple and orange" beats "Spooky Night". No shop name, no \
emoji, no marketing language, no ALL CAPS.

Keywords carry the search. Work outwards in rings: what the piece is (invitation, \
card, template, printable), the occasion (halloween, party, autumn), what is in \
it (pumpkin, ghost, haunted house, bat), the style (spooky, vintage, watercolour, \
minimalist), the colours, and finally how it is used (party invite, save the date, \
social media). Most important first — agencies weight early keywords more \
heavily. Single words beat phrases. No repetition, no brand names, no keywords \
for things that are not visibly in the file."""


def draft(spec: DesignSpec, preview: Path | None = None,
          provider: VisionProvider | None = None) -> MetadataDraft:
    provider = provider or reason()

    text_content = " / ".join(t.content for t in spec.texts()[:8])
    facts = (
        f"category: {spec.dna.category}\n"
        f"occasion: {spec.dna.occasion}\n"
        f"style: {', '.join(spec.dna.style_tags) or 'unspecified'}\n"
        f"elements in the design: {', '.join(spec.dna.motif_vocabulary) or 'none recorded'}\n"
        f"colours: {', '.join(s.hex for s in spec.dna.palette.swatches)}\n"
        f"surfaces: {', '.join(p.name for p in spec.pages)}\n"
        f"text on the piece: {text_content}"
    )
    return provider.structured(
        SYSTEM,
        f"Facts about the file:\n{facts}\n\nWrite its title and keywords.",
        [preview] if preview else [],
        MetadataDraft,
    )


def build(spec: DesignSpec, filename: str, preview: Path | None = None,
          provider: VisionProvider | None = None) -> Metadata:
    d = draft(spec, preview, provider)
    return Metadata(
        filename=filename,
        title=d.title.strip()[:200],
        keywords=[k.strip().lower() for k in d.keywords if k.strip()][:49],
        category=d.category,
        description=d.title.strip()[:200],
    )


# --------------------------------------------------------------------------
# the CSVs
# --------------------------------------------------------------------------

def adobe_csv(rows: list[Metadata], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Filename", "Title", "Keywords", "Category", "Releases"])
        for m in rows:
            w.writerow([m.filename, m.title, ", ".join(m.keywords), m.category, ""])
    return path


def shutterstock_csv(rows: list[Metadata], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Filename", "Description", "Keywords", "Categories",
                    "Editorial", "Mature content", "Illustration"])
        for m in rows:
            w.writerow([m.filename, m.description, ", ".join(m.keywords),
                        m.category, "no", "no", "yes"])
    return path


def write_metadata(rows: list[Metadata], out_dir: Path) -> dict[str, Path]:
    return {
        "adobe": adobe_csv(rows, out_dir / "adobe-stock.csv"),
        "shutterstock": shutterstock_csv(rows, out_dir / "shutterstock.csv"),
    }
