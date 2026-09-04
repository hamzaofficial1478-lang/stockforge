"""The control panel.

Standard library only — no Flask, no FastAPI, no npm. One file of Python, one
file of HTML, and `stockforge ui` opens it. Nothing to install and nothing to
keep updated.

Binds to 127.0.0.1 by design. This panel can read your catalogue, write your
.env and start uploads; it has no login because it is not meant to be reachable
by anything but your own browser. Do not put it behind a public port.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import Settings, env_file, settings as default_settings
from ..health import report as health_report
from ..pipeline import Pipeline
from ..sources import open_source
from ..worker import get_worker

log = logging.getLogger("stockforge.ui")

HERE = Path(__file__).parent

# Keys we will write to .env from the panel. Anything not listed is ignored —
# the browser does not get to set arbitrary environment variables.
EDITABLE = {
    "SF_VISION_BASE_URL", "SF_VISION_MODEL", "SF_VISION_API_KEY",
    "SF_REASON_BASE_URL", "SF_REASON_MODEL", "SF_REASON_API_KEY",
    "SF_ETSY_API_KEY", "SF_ROOT", "SF_FONTS", "SF_MOTIFS",
    "SF_DERIVE_STRENGTH", "SF_DISTINCT_THRESHOLD", "SF_DERIVE_ROUNDS",
    "SF_CRITIQUE_ROUNDS", "SF_MIX", "SF_MOTIF_THRESHOLD",
    "SF_PUBLISH", "SF_PUBLISH_ALL",
    "SF_FTP_ADOBE_HOST", "SF_FTP_ADOBE_USER", "SF_FTP_ADOBE_PASS",
    "SF_FTP_SHUTTERSTOCK_HOST", "SF_FTP_SHUTTERSTOCK_USER", "SF_FTP_SHUTTERSTOCK_PASS",
}
SECRET = {k for k in EDITABLE if k.endswith(("_KEY", "_PASS"))}


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Rewrite .env, preserving comments and the order of what is already there.

    Written to the same place `config.load_env` reads from, which was the other
    half of the problem: settings were saved to a file nothing ever loaded.
    """
    existing = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []

    for line in existing:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.partition("=")[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)

    fresh = [k for k in updates if k not in seen]
    if fresh:
        out.append("")
        for k in fresh:
            out.append(f"{k}={updates[k]}")

    path.write_text("\n".join(out).rstrip() + "\n")
    for k, v in updates.items():          # take effect without a restart
        os.environ[k] = v


class Handler(BaseHTTPRequestHandler):
    server_version = "stockforge"
    cfg: Settings = default_settings

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, default=str).encode())

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}

    # -- GET --------------------------------------------------------------

    def do_GET(self) -> None:
        url = urlparse(self.path)
        route, query = url.path, parse_qs(url.query)

        if route in ("/", "/index.html"):
            page = HERE / "app.html"
            return self._send(200, page.read_bytes(), "text/html; charset=utf-8")

        if route == "/api/health":
            return self._json(health_report(self.cfg).as_dict())

        if route == "/api/config":
            env = read_env(env_file())
            merged = {k: os.environ.get(k, env.get(k, "")) for k in EDITABLE}
            return self._json({
                "values": {k: ("••••••••" if k in SECRET and v else v)
                           for k, v in merged.items()},
                "secret_keys": sorted(SECRET),
                "set": sorted(k for k, v in merged.items() if v),
            })

        if route == "/api/status":
            return self._json(Pipeline(self.cfg).status())

        if route == "/api/worker":
            return self._json(get_worker(self.cfg).progress.as_dict())

        if route == "/api/designs":
            pipe = Pipeline(self.cfg)
            state = (query.get("state") or [None])[0]
            limit = int((query.get("limit") or [200])[0])
            rows = pipe.store.designs(state=state)[:limit]
            return self._json([dict(r) for r in rows])

        if route == "/api/motif-gaps":
            pipe = Pipeline(self.cfg)
            limit = int((query.get("limit") or [12])[0])
            return self._json([
                {"description": g.description, "kind": g.kind, "designs": g.designs,
                 "seen": g.seen, "variants": g.variants,
                 "nearest_id": g.nearest_id, "nearest_score": g.nearest_score}
                for g in pipe.motif_gaps(limit=limit)
            ])

        if route == "/api/review":
            pipe = Pipeline(self.cfg)
            out = []
            for r in pipe.store.pending_review():
                did = r["design_id"]
                design = pipe.store.conn.execute(
                    "SELECT * FROM designs WHERE id=?", (did,)).fetchone()
                asset = pipe.store.conn.execute(
                    "SELECT flat_path FROM assets WHERE design_id=? ORDER BY width DESC LIMIT 1",
                    (did,)).fetchone()
                renders = sorted((self.cfg.root / "renders").glob(f"{did[:16]}*.png"))
                out.append({
                    "design_id": did,
                    "reason": r["reason"],
                    "score": r["score"],
                    "title": design["title"] if design else None,
                    "listing_url": design["listing_url"] if design else None,
                    "state": design["state"] if design else None,
                    "source_image": str(asset["flat_path"]) if asset else None,
                    "rebuilds": [str(p) for p in renders],
                })
            return self._json(out)

        if route == "/file":
            return self._serve_file((query.get("path") or [""])[0])

        return self._json({"error": "not found"}, 404)

    def _serve_file(self, raw: str) -> None:
        """Only ever from inside the workspace. A panel that will hand out any
        file on the machine is a hole, however local it is."""
        if not raw:
            return self._json({"error": "path required"}, 400)
        try:
            path = Path(raw).resolve()
            root = self.cfg.root.resolve()
            path.relative_to(root)
        except (ValueError, OSError):
            return self._json({"error": "outside the workspace"}, 403)
        if not path.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), ctype)

    # -- POST -------------------------------------------------------------

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        body = self._body()

        if route == "/api/config":
            updates = {k: str(v) for k, v in body.items()
                       if k in EDITABLE and v not in ("", "••••••••", None)}
            if not updates:
                return self._json({"saved": 0})
            path = env_file()
            write_env(path, updates)
            # The pipeline and the worker hold this object. Without refreshing
            # it the sliders wrote a file and changed nothing that was running.
            self.cfg.reload()
            return self._json({"saved": len(updates), "keys": sorted(updates),
                               "file": str(path)})

        if route == "/api/pull":
            kind, target = body.get("kind"), body.get("target")
            if kind not in ("shop", "links", "folder") or not target:
                return self._json({"error": "kind and target are required"}, 400)
            limit = body.get("limit") or None
            try:
                source = open_source(kind, target, cache_dir=self.cfg.root / "downloads",
                                     limit=int(limit) if limit else None)
                pulled = Pipeline(self.cfg).pull(source)
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"pulled": pulled})

        if route == "/api/count":
            kind, target = body.get("kind"), body.get("target")
            try:
                return self._json({"count": open_source(kind, target).count()})
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)

        if route == "/api/worker":
            worker = get_worker(self.cfg)
            action = body.get("action")
            if "pace" in body:
                worker.set_pace(float(body["pace"]))
            if action == "start":
                limit = body.get("limit")
                worker.start(int(limit) if limit else None)
            elif action == "pause":
                worker.pause()
            elif action == "resume":
                worker.resume()
            elif action == "stop":
                worker.stop()
            return self._json(worker.progress.as_dict())

        if route == "/api/decide":
            did, decision = body.get("design_id"), body.get("decision")
            if not did or decision not in ("approve", "reject", "retry"):
                return self._json({"error": "design_id and a decision are required"}, 400)
            pipe = Pipeline(self.cfg)
            with pipe.store.tx() as c:
                c.execute("UPDATE review SET decision=?, decided_at=strftime('%s','now') "
                          "WHERE design_id=?", (decision, did))
            pipe.store.set_design_state(
                did, {"approve": "ready", "reject": "master_only", "retry": "pending"}[decision]
            )
            return self._json({"design_id": did, "decision": decision})

        if route == "/api/publish":
            from ..publish import FTPTarget, upload_batch, write_metadata

            pipe = Pipeline(self.cfg)
            rows, files = pipe.deliverable()

            if not files:
                return self._json({"error": "nothing cleared for delivery yet"}, 400)

            paths = write_metadata(rows, self.cfg.root / "out")
            result = {"files": len(files),
                      "metadata": {k: str(v) for k, v in paths.items()}}

            if body.get("dry_run", True):
                result["uploaded"] = False
                return self._json(result)

            result["targets"] = {}
            for name in ("adobe", "shutterstock"):
                try:
                    target = FTPTarget.from_env(name)
                except RuntimeError as exc:
                    result["targets"][name] = {"skipped": str(exc)}
                    continue
                sent = upload_batch(target, files)
                result["targets"][name] = {
                    "uploaded": sum(1 for v in sent.values() if v == "uploaded"),
                    "skipped": sum(1 for v in sent.values() if v == "skipped"),
                    "failed": [k for k, v in sent.items() if v.startswith("failed")],
                }
            result["uploaded"] = True
            return self._json(result)

        return self._json({"error": "not found"}, 404)


def serve(cfg: Settings | None = None, port: int = 8770, open_browser: bool = True) -> None:
    Handler.cfg = cfg or default_settings
    Handler.cfg.ensure_dirs()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"stockforge control panel: {url}")
    print("Ctrl-C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping.")
        get_worker(Handler.cfg).stop()
        httpd.shutdown()
