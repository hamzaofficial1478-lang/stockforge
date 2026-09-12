"""Saved model connections.

The Setup screen had four bare text boxes and no way to find out whether what
you typed into them worked. You discovered a wrong base URL or a model name
your server does not serve on the first design of a five thousand design run.

So: keep a list of connections, ask a server what it actually serves, send one
real request to the one you picked, and only then make it the live one.
"""

from __future__ import annotations

import json
import re
import base64
import io
import secrets
import logging
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from ..providers.openai_compat import model_options
from ..providers.base import extract_json

log = logging.getLogger("stockforge.ui.models")

FILE = "models.json"


@dataclass
class Connection:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    label: str = ""
    base_url: str = "http://localhost:8000/v1"
    model: str = ""
    api_key: str = ""
    # Which backend talks to it. "openai" wants a base URL and maybe a key;
    # "claude" wants neither, because it uses the account you signed in with.
    # Anything else is an import path to someone's own backend.
    backend: str = "openai"
    # vision  reads designs and returns words
    # text    writes titles and keywords
    # image   draws pictures — a different job with a different reply shape,
    #         which is why testing one as though it were a chat model reports
    #         "an odd reply": an image model answers with an image, and the
    #         message it comes back in has no text content at all.
    role: str = "vision"           # vision | text | image
    active: bool = False
    last_tested: float = 0.0
    last_result: str = ""

    def public(self) -> dict:
        """Never hand a key back to the browser — only whether there is one."""
        out = asdict(self)
        out["api_key"] = ""
        out["has_key"] = bool(self.api_key)
        return out


def _path(root: Path) -> Path:
    return root / FILE


def load(root: Path) -> list[Connection]:
    path = _path(root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read %s: %s", path, exc)
        return []
    out = []
    for row in raw if isinstance(raw, list) else []:
        try:
            out.append(Connection(**{k: v for k, v in row.items()
                                     if k in Connection.__dataclass_fields__}))
        except TypeError:
            continue
    return out


def save(root: Path, connections: list[Connection]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _path(root).write_text(json.dumps([asdict(c) for c in connections], indent=2), encoding="utf-8")


def upsert(root: Path, data: dict) -> Connection:
    """Add a connection, or update the one with this id."""
    connections = load(root)
    existing = next((c for c in connections if c.id == data.get("id")), None)
    if existing is None:
        existing = Connection(id=data.get("id") or uuid.uuid4().hex[:12])
        connections.append(existing)
    for key in ("label", "base_url", "model", "role", "backend"):
        if data.get(key) is not None:
            setattr(existing, key, str(data[key]).strip())
    # An empty key means "leave it alone", because the browser is never sent
    # the real one and would otherwise blank it on every edit.
    if data.get("api_key"):
        existing.api_key = str(data["api_key"]).strip()
    if not existing.label:
        existing.label = existing.model or existing.base_url
    save(root, connections)
    return existing


def remove(root: Path, connection_id: str) -> bool:
    connections = load(root)
    kept = [c for c in connections if c.id != connection_id]
    if len(kept) == len(connections):
        return False
    save(root, kept)
    return True


#: Roles where more than one model can be live at once. Reading designs is the
#: one that benefits: the lanes hand a different model to each, so two live
#: readers is two designs at a time. Writing and drawing stay exclusive —
#: nothing splits that work, so a second one would only be ambiguous.
MULTIPLE_ALLOWED = {"vision"}


def activate(root: Path, connection_id: str, exclusive: bool | None = None) -> Connection | None:
    """Make a connection live.

    A reading model joins the others rather than replacing them, because that
    is what lets a second lane exist — one live model meant the second lane had
    nothing of its own to use and the whole thing bought nothing. Writing and
    drawing keep the old behaviour, where making one live stands the others
    down.
    """
    connections = load(root)
    chosen = next((c for c in connections if c.id == connection_id), None)
    if chosen is None:
        return None
    if exclusive is None:
        exclusive = chosen.role not in MULTIPLE_ALLOWED

    chosen.active = True
    if exclusive:
        for c in connections:
            if c.role == chosen.role and c.id != chosen.id:
                c.active = False
    save(root, connections)
    return chosen


def deactivate(root: Path, connection_id: str) -> Connection | None:
    """Stand a connection down without deleting it. Only reachable for roles
    that allow several, since standing down the only writing model would leave
    the panel with no way to say which one to use."""
    connections = load(root)
    chosen = next((c for c in connections if c.id == connection_id), None)
    if chosen is None:
        return None
    chosen.active = False
    save(root, connections)
    return chosen


def live(root: Path, role: str) -> list[Connection]:
    """Every model currently live for a role, in the order they were added."""
    return [c for c in load(root) if c.role == role and c.active]


def env_for(connection: Connection) -> dict[str, str]:
    """The settings that make this connection the one the pipeline uses."""
    prefix = {"vision": "SF_VISION", "image": "SF_IMAGE"}.get(connection.role, "SF_REASON")
    backend = connection.backend or "openai"
    if backend != "openai":
        # A signed-in backend has no server to point at, and writing a stale
        # key here would shadow the sign-in — which is the whole thing it
        # exists to avoid. Both are cleared rather than left lying around.
        return {f"{prefix}_BACKEND": backend,
                f"{prefix}_MODEL": connection.model,
                f"{prefix}_BASE_URL": "",
                f"{prefix}_API_KEY": ""}
    return {f"{prefix}_BACKEND": "openai",
            f"{prefix}_BASE_URL": connection.base_url,
            f"{prefix}_MODEL": connection.model,
            f"{prefix}_API_KEY": connection.api_key}


# --------------------------------------------------------------------------
# talking to the server
# --------------------------------------------------------------------------

def _request(url: str, api_key: str, payload: dict | None, timeout: int):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {api_key}"} if api_key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch(base_url: str, api_key: str = "", timeout: int = 20) -> dict:
    """Ask a server what it serves.

    Every OpenAI-compatible server answers GET /models, so you pick from a list
    rather than typing a name from memory and finding out on design one.
    """
    base = base_url.rstrip("/")
    try:
        body = _request(f"{base}/models", api_key, None, timeout)
    except urllib.error.HTTPError as exc:
        return {"error": f"the server answered {exc.code} — {exc.reason}"}
    except urllib.error.URLError as exc:
        return {"error": f"nothing answered at {base} — {exc.reason}"}
    except Exception as exc:
        return {"error": f"could not read the reply from {base}: {exc}"}

    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return {"error": f"{base} answered, but not with a list of models"}
    names = sorted({str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")})
    return {"models": names}


def _wants_a_list(detail: str) -> bool:
    """Is this 400 the server asking for content as parts rather than a string?"""
    lowered = detail.lower()
    return "messages.0.content" in lowered and ("valid list" in lowered or "list" in lowered)


def _post_chat(base: str, api_key: str, payload: dict, timeout: int):
    """One chat request, retried once as content-parts if the server insists.

    Most servers take `content` as a plain string. Some — Qwen's among them —
    require the list-of-parts form and answer a string with a 400 that talks
    about JSON shape rather than about anything a person did. Sending parts is
    valid everywhere, so the retry is free and the message never has to be
    explained.
    """
    try:
        return _request(f"{base}/chat/completions", api_key, payload, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
        detail = exc.read().decode(errors="replace")
        first = payload.get("messages", [{}])[0].get("content")
        if not (_wants_a_list(detail) and isinstance(first, str)):
            raise _replay(exc, detail)
        retried = json.loads(json.dumps(payload))
        retried["messages"][0]["content"] = [{"type": "text", "text": first}]
        return _request(f"{base}/chat/completions", api_key, retried, timeout)


def _replay(exc: urllib.error.HTTPError, body: str) -> urllib.error.HTTPError:
    """Hand the error back with its body intact — it has already been read once,
    and a caller that reads it again gets nothing."""
    return urllib.error.HTTPError(exc.url, exc.code, exc.reason, exc.headers,
                                  io.BytesIO(body.encode()))


def looks_like_an_image_model(model: str) -> bool:
    """Is this a model that draws rather than writes?

    A name check, deliberately. Asking the server costs a request and most do
    not say; the names are unambiguous in practice — qwen-image-3.0,
    qwen-image-edit-plus, wan2.7-image-pro, z-image-turbo. A false positive
    only ever produces a message telling you to pick a different role, which
    is cheap to ignore and right far more often than not.
    """
    name = (model or "").lower()
    if "vl" in name.split("-") or "ocr" in name:        # qwen-vl reads, not draws
        return False
    return any(mark in name for mark in ("-image", "image-", "t2i", "text2image"))


def _test_image(base: str, model: str, api_key: str, timeout: int) -> dict:
    """Ask an image model for one small picture and check a picture came back.

    An image model answers on the same endpoint as a chat model and in almost
    the same envelope, but the message carries no text — which is why testing
    one as a chat model reports "an odd reply" while the call itself was a
    perfectly good 200. The image arrives as a url or as base64, depending on
    the server, so both are accepted.
    """
    # Content as a list of parts, not a bare string. Qwen's image models reject
    # a string outright — "Input should be a valid list: input.messages.0.content"
    # — and this probe had the same fault it exists to catch.
    payload = {"model": model, "size": "512x512", "n": 1,   # separator learned below
               "messages": [{"role": "user", "content": [
                   {"type": "text",
                    "text": "a single plain black circle centred on a white background"}]}]}
    started = time.time()
    try:
        body = _post_image(base, api_key, payload, timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        return {"ok": False, "error": f"HTTP {exc.code} — {detail or exc.reason}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"nothing answered at {base} — {exc.reason}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}

    found = _find_image(body)
    took = round(time.time() - started, 1)
    if not found:
        return {"ok": False, "took": took,
                "error": f"it answered in {took}s but there was no image in the reply: "
                         f"{str(body)[:200]}"}
    return {"ok": True, "took": took, "model": model,
            "scope": f"Draws pictures. Returned an image in {took}s.",
            "reply": found[:80]}


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


def _post_image(base: str, api_key: str, payload: dict, timeout: int):
    """One generation request, retried once with the separator the server asked
    for. Costs nothing when the first guess was right."""
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
        return _request(f"{base}/chat/completions", api_key, retried, timeout)


def _find_image(body) -> str:
    """The url or base64 of the first image anywhere in a reply.

    Servers disagree about where to put it — choices[].message.images[],
    a top-level data[], output.results[] — so this looks rather than assumes,
    which is cheaper than one branch per vendor and does not rot.
    """
    seen: list[str] = []

    def walk(node):
        if len(seen) > 0:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("url", "b64_json", "image_url", "image", "base64") and isinstance(value, str) and value:
                    seen.append(value)
                    return
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str) and (node.startswith("http") or node.startswith("data:image")):
            seen.append(node)

    walk(body)
    return seen[0] if seen else ""


def test(base_url: str, model: str, api_key: str = "", timeout: int = 90,
         role: str = "text") -> dict:
    """Send one real request and report what came back.

    Not a ping. A server can be up, and reachable, and still not serve the
    model you named — which is a thing worth finding out now rather than on the
    first design of five thousand.
    """
    if not model:
        return {"ok": False, "error": "no model named"}
    base = base_url.rstrip("/")
    if role == "image":
        return _test_image(base, model, api_key, timeout)
    if looks_like_an_image_model(model):
        # Saved under the wrong "Used for". Worth catching before the request
        # rather than after, because the server's complaint is about JSON shape
        # and says nothing about the actual mistake — and a live text role
        # pointed at an image model sends every title and keyword to something
        # that answers in pictures.
        return {"ok": False,
                "error": f"{model} draws pictures, but this connection is set to "
                         f"\"{'reading designs' if role == 'vision' else 'titles and keywords'}\". "
                         f"Change Used for to \"Drawing artwork (makes pictures)\" and test again."}
    payload = {"model": model, "max_tokens": 512, "temperature": 0,
               "messages": [{"role": "user",
                             "content": "Reply with the single word: ready"}]}
    expected = None
    if role == "vision":
        from PIL import Image, ImageDraw, ImageFont
        color, rgb = secrets.choice([("red", "#dd3030"), ("green", "#168540"), ("blue", "#2464c8")])
        digits = str(secrets.randbelow(90000) + 10000)
        img = Image.new("RGB", (320, 160), rgb)
        ImageDraw.Draw(img).text((70, 55), digits, fill="white", font=ImageFont.load_default(size=42))
        encoded = io.BytesIO()
        img.save(encoded, format="PNG")
        expected = {"color": color, "text": digits}
        payload.update(max_tokens=4096, response_format={"type": "json_object"})
        payload["messages"][0]["content"] = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(encoded.getvalue()).decode()}},
            {"type": "text", "text": 'Read the image. Return JSON with "color" (the background color name) and "text" (the printed digits as a string).'},
        ]
    payload.update(model_options(model))
    started = time.time()
    try:
        body = _post_chat(base, api_key, payload, timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        return {"ok": False, "error": f"HTTP {exc.code} — {detail or exc.reason}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"nothing answered at {base} — {exc.reason}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}

    took = time.time() - started
    try:
        said = body["choices"][0]["message"]["content"]
        if not isinstance(said, str) or not said.strip():
            return {"ok": False, "error": "The model returned no answer. Check its token budget and reasoning settings.",
                    "seconds": round(took, 1)}
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "error": f"an odd reply: {str(body)[:200]}",
                "seconds": round(took, 1)}
    if expected is not None:
        try:
            observed = extract_json(said)
            if (str(observed.get("color", "")).lower() != expected["color"]
                    or str(observed.get("text", "")) != expected["text"]):
                raise ValueError("The model did not correctly read the test image's color and digits.")
        except (RuntimeError, ValueError, AttributeError) as exc:
            return {"ok": False, "error": str(exc), "seconds": round(took, 1)}
    scope = ("Image color, text and JSON verified. Full designs need several larger requests."
             if expected else "Text connection verified; image reading and conversion speed are not tested.")
    return {"ok": True, "said": str(said).strip()[:200], "seconds": round(took, 1), "scope": scope}
