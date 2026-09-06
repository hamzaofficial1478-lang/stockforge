"""Model backends.

The pipeline does not care which model reads an image. It asks a provider for
structured data and gets a validated object back, or an error. That keeps a
local NVIDIA model, a hosted endpoint and a stub for tests interchangeable.

Two things make local models workable where a hosted one would just do it:

  small schemas   A 7B-70B model will not reliably fill a hundred-field nested
                  object in one shot. So we never ask it to. Analysis is split
                  into four small passes, each with a schema that fits in a
                  model's head.

  repair loop     Local models emit prose around their JSON, trailing commas,
                  and the occasional missing brace. We extract, validate, and
                  hand the validation errors straight back with the original
                  question. Two retries fixes almost everything.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger("stockforge.providers")

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    pass


def encode_image(path: Path, max_edge: int = 1280) -> tuple[str, str]:
    """Return (base64, mime). Downscaled — a 4000px listing image costs a local
    model a lot of latency and tells it nothing a 1280px one does not."""
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ProviderError(f"unreadable image: {path}")
    h, w = img.shape[:2]
    if max(h, w) > max_edge:
        s = max_edge / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        raise ProviderError(f"could not encode: {path}")
    return base64.standard_b64encode(buf.tobytes()).decode(), "image/jpeg"


# --------------------------------------------------------------------------
# getting JSON out of a model that was not asked nicely
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Pull the first JSON value out of a model response.

    Handles fenced blocks, leading prose ("Here is the specification:"),
    trailing commentary, and trailing commas. Deliberately forgiving — being
    strict here just means more round trips.
    """
    candidates: list[str] = []

    for m in _FENCE.finditer(text):
        candidates.append(m.group(1))
    candidates.append(text)

    for blob in candidates:
        blob = blob.strip()
        for opener, closer in (("{", "}"), ("[", "]")):
            start = blob.find(opener)
            if start == -1:
                continue
            depth, in_str, esc = 0, False, False
            for i in range(start, len(blob)):
                ch = blob[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        chunk = blob[start:i + 1]
                        chunk = re.sub(r",(\s*[}\]])", r"\1", chunk)  # trailing commas
                        try:
                            return json.loads(chunk)
                        except json.JSONDecodeError:
                            break
    raise ProviderError("no parseable JSON in response")


SCHEMA_BUDGET = 6000


def _drop_titles(node):
    """Pydantic writes a "title" for every field, restating its own name. It is
    a third of the schema and tells a model nothing it cannot see."""
    if isinstance(node, dict):
        return {k: _drop_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_drop_titles(v) for v in node]
    return node


def schema_hint(model: type[BaseModel]) -> str:
    """A compact schema for the prompt. The full JSON Schema pydantic emits is
    enormous and mostly $refs; local models do better with something they can
    actually read.

    It is never cut short. This used to end with `[:6000]`, which silently sent
    StructureRead — 7466 characters, and the pass that decides everything drawn
    on the page — as JSON chopped off mid-object. The model was told to satisfy
    a schema it could not parse. Shrinking it is fine; truncating it is not, so
    if it will not fit even compacted it goes whole and long.
    """
    schema = model.model_json_schema()
    compact = json.dumps(schema, separators=(",", ":"))
    if len(compact) <= SCHEMA_BUDGET:
        return compact
    return json.dumps(_drop_titles(schema), separators=(",", ":"))


# --------------------------------------------------------------------------

class VisionProvider(ABC):
    """Anything that can look at images and return structured data."""

    name: str = "provider"
    max_repairs: int = 2

    @abstractmethod
    def chat(self, system: str, user_text: str, images: list[Path], **kw: Any) -> str:
        """One turn. Returns raw text."""

    def structured(
        self,
        system: str,
        user_text: str,
        images: list[Path],
        model: type[T],
        **kw: Any,
    ) -> T:
        """Ask, parse, validate, and repair on failure."""
        prompt = (
            f"{user_text}\n\n"
            f"Reply with JSON only — no prose, no explanation, no markdown fence.\n"
            f"It must validate against this schema:\n{schema_hint(model)}"
        )
        last_error = ""

        for attempt in range(self.max_repairs + 1):
            if attempt:
                prompt = (
                    f"{user_text}\n\nYour previous reply was not valid. "
                    f"These are the exact errors:\n{last_error}\n\n"
                    f"Return corrected JSON only, matching:\n{schema_hint(model)}"
                )
            raw = self.chat(system, prompt, images, **kw)
            try:
                return model.model_validate(extract_json(raw))
            except (ProviderError, ValidationError) as exc:
                last_error = str(exc)[:1500]
                log.debug("[%s] attempt %d failed: %s", self.name, attempt + 1, last_error[:200])

        raise ProviderError(f"{self.name} could not produce valid {model.__name__}: {last_error}")
