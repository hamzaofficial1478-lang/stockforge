"""Asking a model to draw a picture.

Not for anything that ships. There is exactly one job here: the motif gaps —
decoration the analyser found in your own designs that the library has no
drawing for. Those stop a design dead, and there were seven of them in one
Halloween card. A generated picture is something to trace from, in the same way
a crop harvested out of your own artwork is something to trace from, and it
lands in the same sort of place: a folder the matcher never looks in.

The reason it is drawn that way round is rights, not taste. A generated
illustration is not a thing you own outright, and the whole point of matching
to your own library is that every glyph and every curve in a delivered file is
yours to sell. So this produces reference, the reference gets traced, and the
trace is yours.

Servers disagree about almost everything here — where the image comes back,
what separates the width from the height — so the dialect handling lives in one
place and is shared with the connection test on the Setup screen.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request

from .base import ProviderError

log = logging.getLogger("stockforge.providers.images")

_EXPECTED_SIZE = re.compile(r"<\s*width\s*>\s*(.)\s*<\s*height\s*>", re.I)


def size_separator(detail: str) -> str:
    """The character a server wants between width and height, read from its own
    complaint.

    Qwen answers 512x512 with `Expected format: '<width>*<height>'`, OpenAI's
    images API wants the x. Rather than keeping a list of which vendor uses
    which — a list that is wrong the moment somebody adds a third — the message
    names the separator and this takes it from there.
    """
    found = _EXPECTED_SIZE.search(detail or "")
    return found.group(1) if found else ""


def find_image(body) -> str:
    """The url or base64 of the first image anywhere in a reply.

    Servers disagree about where to put it — choices[].message.images[], a
    top-level data[], output.results[] — so this looks rather than assumes,
    which is cheaper than one branch per vendor and does not rot.
    """
    seen: list[str] = []

    def walk(node):
        if seen:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("url", "b64_json", "image_url", "image", "base64") \
                        and isinstance(value, str) and value:
                    seen.append(value)
                    return
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str) and (node.startswith("http")
                                        or node.startswith("data:image")):
            seen.append(node)

    walk(body)
    return seen[0] if seen else ""


def _request(url: str, api_key: str, payload: dict, timeout: int):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {api_key}"} if api_key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def _replay(exc: urllib.error.HTTPError, detail: str) -> urllib.error.HTTPError:
    """The same error again, with its body still readable — an HTTPError's is a
    stream and reading it once empties it."""
    import io

    return urllib.error.HTTPError(exc.url, exc.code, exc.reason, exc.headers,
                                  io.BytesIO(detail.encode()))


def post_image(base: str, api_key: str, payload: dict, timeout: int = 120):
    """One generation request, retried once with the separator the server asked
    for. Costs nothing when the first guess was right."""
    base = base.rstrip("/")
    try:
        return _request(f"{base}/chat/completions", api_key, payload, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
        detail = exc.read().decode(errors="replace")
        wanted = size_separator(detail)
        size = str(payload.get("size") or "")
        if not wanted or not size or wanted in size:
            raise _replay(exc, detail)
        retried = json.loads(json.dumps(payload))
        retried["size"] = re.sub(r"[^0-9]", wanted, size, count=1)
        log.info("retrying with %s, which is the size format it asked for",
                 retried["size"])
        return _request(f"{base}/chat/completions", api_key, retried, timeout)


def fetch(found: str, timeout: int = 60) -> bytes:
    """The image itself, whether it came back as bytes or as somewhere to go."""
    if found.startswith("data:"):
        _, _, encoded = found.partition(",")
        return base64.b64decode(encoded)
    if found.startswith("http"):
        with urllib.request.urlopen(found, timeout=timeout) as response:
            return response.read()
    try:
        return base64.b64decode(found, validate=True)
    except Exception as exc:
        raise ProviderError(f"the reply was not an image: {found[:80]}") from exc


class ImageProvider:
    """Anything that draws on an OpenAI-shaped chat endpoint."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 size: int = 1024, timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.size = size
        self.timeout = timeout
        self.name = f"{model}@{self.base_url}"

    def draw(self, prompt: str, size: int | None = None) -> bytes:
        edge = int(size or self.size)
        payload = {
            "model": self.model,
            "size": f"{edge}x{edge}",      # the separator is corrected on a 400
            "n": 1,
            # Content as a list of parts, not a bare string. Qwen's image models
            # reject a string outright, and both this and the Setup probe had
            # that fault once.
            "messages": [{"role": "user",
                          "content": [{"type": "text", "text": prompt}]}],
        }
        try:
            body = post_image(self.base_url, self.api_key, payload, self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise ProviderError(f"{self.name} HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"{self.name} unreachable: {exc.reason}") from exc

        found = find_image(body)
        if not found:
            raise ProviderError(
                f"{self.name} answered without an image in it: {str(body)[:200]}")
        return fetch(found)


def from_env(prefix: str = "SF_IMAGE") -> ImageProvider:
    base = os.environ.get(f"{prefix}_BASE_URL", "")
    model = os.environ.get(f"{prefix}_MODEL", "")
    if not base or not model:
        raise ProviderError(
            "No drawing model is set up. Add one on Setup with the role "
            "\"Drawing artwork\" — Qwen's image models work, and the address is "
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    return ImageProvider(
        base_url=base, model=model,
        api_key=os.environ.get(f"{prefix}_API_KEY"),
        size=int(os.environ.get(f"{prefix}_SIZE", 1024) or 1024),
        timeout=int(os.environ.get(f"{prefix}_TIMEOUT", 180) or 180),
    )
