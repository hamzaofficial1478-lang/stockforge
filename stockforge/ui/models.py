"""Saved model connections.

The Setup screen had four bare text boxes and no way to find out whether what
you typed into them worked. You discovered a wrong base URL or a model name
your server does not serve on the first design of a five thousand design run.

So: keep a list of connections, ask a server what it actually serves, send one
real request to the one you picked, and only then make it the live one.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("stockforge.ui.models")

FILE = "models.json"


@dataclass
class Connection:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    label: str = ""
    base_url: str = "http://localhost:8000/v1"
    model: str = ""
    api_key: str = ""
    role: str = "vision"           # vision | text
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
    for key in ("label", "base_url", "model", "role"):
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


def activate(root: Path, connection_id: str) -> Connection | None:
    """Make one connection the live one for its role."""
    connections = load(root)
    chosen = next((c for c in connections if c.id == connection_id), None)
    if chosen is None:
        return None
    for c in connections:
        if c.role == chosen.role:
            c.active = c.id == chosen.id
    save(root, connections)
    return chosen


def env_for(connection: Connection) -> dict[str, str]:
    """The settings that make this connection the one the pipeline uses."""
    prefix = "SF_VISION" if connection.role == "vision" else "SF_REASON"
    return {f"{prefix}_BASE_URL": connection.base_url,
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


def test(base_url: str, model: str, api_key: str = "", timeout: int = 90) -> dict:
    """Send one real request and report what came back.

    Not a ping. A server can be up, and reachable, and still not serve the
    model you named — which is a thing worth finding out now rather than on the
    first design of five thousand.
    """
    if not model:
        return {"ok": False, "error": "no model named"}
    base = base_url.rstrip("/")
    payload = {"model": model, "max_tokens": 512, "temperature": 0,
               "messages": [{"role": "user",
                             "content": "Reply with the single word: ready"}]}
    started = time.time()
    try:
        body = _request(f"{base}/chat/completions", api_key, payload, timeout)
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
    return {"ok": True, "said": str(said).strip()[:200], "seconds": round(took, 1)}
