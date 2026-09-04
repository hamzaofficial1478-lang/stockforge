"""Fetching things over HTTP, with the patience a long crawl needs.

Pulling a five-thousand listing shop is hours of requests. Somewhere in there
Etsy will rate limit you, a CDN will hand back a 502, and a connection will
drop. None of that is exceptional; it is what a crawl of that length looks
like. What matters is that it costs a pause rather than the whole pull.

The distinction worth making is between waiting and giving up. A 429 or a 503
says come back shortly. A 404 says the thing is not there and asking again
politely will not change that.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..config import settings

log = logging.getLogger("stockforge.sources.http")

UA = "Mozilla/5.0 (compatible; stockforge/1.0; own-catalogue-recovery)"

# However long a server asks us to wait, stop pretending it is worth it.
MAX_WAIT = 300.0


class Unavailable(RuntimeError):
    """The other end could not be reached, and trying again did not help."""


def _wait_for(exc: urllib.error.HTTPError, attempt: int) -> float | None:
    """Seconds to wait before trying again, or None when it is pointless."""
    if exc.code == 429:
        after = (exc.headers or {}).get("Retry-After", "").strip()
        if after.isdigit():
            return min(float(after), MAX_WAIT)
        return min(settings.http_backoff ** attempt, MAX_WAIT)
    if exc.code >= 500:
        return min(settings.http_backoff ** attempt, MAX_WAIT)
    return None                    # 401, 403, 404 — a pause will not fix it


def get(url: str, headers: dict | None = None, timeout: int = 45) -> bytes:
    """GET a URL, retrying the failures that are worth retrying."""
    attempts = max(1, settings.http_retries + 1)
    reason = ""

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url, headers={"User-Agent": UA, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            reason = f"HTTP {exc.code}"
            wait = _wait_for(exc, attempt)
            if wait is None:
                detail = exc.read()[:200].decode(errors="replace")
                raise Unavailable(f"{url} — {reason}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = str(getattr(exc, "reason", exc))
            wait = min(settings.http_backoff ** attempt, MAX_WAIT)

        if attempt == attempts:
            break
        log.warning("%s — %s; waiting %.0fs then trying again (%d of %d)",
                    url, reason, wait, attempt, attempts - 1)
        time.sleep(wait)

    raise Unavailable(f"{url} — gave up after {attempts} attempts, last was {reason}")


def download(url: str, dest: Path, timeout: int = 60) -> Path | None:
    """Save a URL to a file. None when it could not be had.

    Already there and not empty means already done — which is what makes a
    crawl that died at listing three thousand cheap to resume.
    """
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        body = get(url, timeout=timeout)
    except Unavailable as exc:
        log.warning("could not fetch %s: %s", url, exc)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return dest
