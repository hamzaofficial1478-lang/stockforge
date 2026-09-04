"""The Etsy door — the one that runs first, on five thousand listings.

Everything here talks to a local HTTP server that behaves the way Etsy does on
a long crawl: it paginates, it rate limits, and now and then a gateway gives up.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from stockforge.config import settings
from stockforge.sources import open_source
from stockforge.sources import shop as shop_module
from stockforge.sources.http import Unavailable, download, get


class _Fake:
    """State the handler reads. Fault injection lives here."""

    def __init__(self):
        self.listings = 0
        self.page_size = 100
        self.hits: list[str] = []
        self.fail_next: dict[str, list[int]] = {}      # path -> codes to serve
        self.retry_after: str | None = None


STATE = _Fake()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if code == 429 and STATE.retry_after is not None:
            self.send_header("Retry-After", STATE.retry_after)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        route, query = url.path, parse_qs(url.query)
        STATE.hits.append(route)

        queued = STATE.fail_next.get(route)
        if queued:
            code = queued.pop(0)
            return self._send(code, b'{"error":"go away"}')

        if route == "/v3/application/shops":
            name = query.get("shop_name", [""])[0]
            if name == "nobody":
                return self._send(200, json.dumps({"results": []}).encode())
            return self._send(200, json.dumps({"results": [
                {"shop_id": 42, "shop_name": name,
                 "listing_active_count": STATE.listings}]}).encode())

        if route == "/v3/application/shops/42":
            return self._send(200, json.dumps(
                {"shop_id": 42, "shop_name": "by-id",
                 "listing_active_count": STATE.listings}).encode())

        if route == "/v3/application/shops/42/listings/active":
            offset = int(query.get("offset", ["0"])[0])
            limit = min(int(query.get("limit", ["100"])[0]), STATE.page_size)
            results = [{
                "listing_id": 1000 + i,
                "title": f"Listing {i}",
                "tags": ["wedding", "invitation"],
                "url": f"https://www.etsy.com/listing/{1000 + i}/x",
                "images": [{"url_fullxfull": f"http://{self.headers['Host']}/img/{i}-0.jpg"},
                           {"url_fullxfull": f"http://{self.headers['Host']}/img/{i}-1.jpg"}],
            } for i in range(offset, min(offset + limit, STATE.listings))]
            return self._send(200, json.dumps({"results": results}).encode())

        if route.startswith("/img/"):
            return self._send(200, b"\xff\xd8\xff" + route.encode(), "image/jpeg")

        return self._send(404, b'{"error":"not found"}')


@pytest.fixture
def etsy(monkeypatch):
    """A stand-in Etsy, wired into the module under test."""
    monkeypatch.setenv("SF_HTTP_BACKOFF", "1.0")     # 1**n == 1s, kept short
    monkeypatch.setenv("SF_HTTP_RETRIES", "3")
    settings.reload()

    STATE.__init__()
    STATE.listings = 5
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(shop_module, "API", f"{base}/v3/application")
    monkeypatch.setenv("SF_ETSY_API_KEY", "test-key")
    try:
        yield base, STATE
    finally:
        server.shutdown()
        server.server_close()


# --- the fetcher ----------------------------------------------------------

def test_a_rate_limit_is_waited_out_not_given_up_on(etsy):
    base, state = etsy
    state.retry_after = "0"
    state.fail_next["/img/x.jpg"] = [429, 429]

    body = get(f"{base}/img/x.jpg")
    assert body.startswith(b"\xff\xd8\xff")
    assert state.hits.count("/img/x.jpg") == 3


def test_a_bad_gateway_is_waited_out_too(etsy):
    base, state = etsy
    state.fail_next["/img/x.jpg"] = [502]
    assert get(f"{base}/img/x.jpg").startswith(b"\xff\xd8\xff")


def test_asking_a_404_again_is_pointless_so_it_does_not(etsy):
    """A pause does not make a missing listing appear. Retrying it just makes
    a long crawl longer."""
    base, state = etsy
    with pytest.raises(Unavailable):
        get(f"{base}/nothing/here")
    assert state.hits.count("/nothing/here") == 1


def test_it_gives_up_eventually_and_says_so(etsy):
    base, state = etsy
    state.retry_after = "0"
    state.fail_next["/img/x.jpg"] = [429] * 20

    with pytest.raises(Unavailable) as exc:
        get(f"{base}/img/x.jpg")
    assert "gave up" in str(exc.value)
    assert state.hits.count("/img/x.jpg") == settings.http_retries + 1


def test_a_download_that_fails_is_reported_not_raised(etsy, tmp_path):
    base, _ = etsy
    assert download(f"{base}/nothing/here", tmp_path / "a.jpg") is None


def test_an_image_already_on_disk_is_not_fetched_again(etsy, tmp_path):
    base, state = etsy
    dest = tmp_path / "a.jpg"
    assert download(f"{base}/img/1-0.jpg", dest) == dest
    before = len(state.hits)
    assert download(f"{base}/img/1-0.jpg", dest) == dest
    assert len(state.hits) == before, "it went back for a file it already had"


# --- the shop door --------------------------------------------------------

def test_a_shop_is_found_by_name_and_by_id(etsy):
    source = open_source("shop", "my-shop")
    assert source.count() == 5
    assert open_source("shop", "42").count() == 5
    assert open_source("shop", "https://www.etsy.com/shop/my-shop").count() == 5


def test_a_shop_that_is_not_there_says_so(etsy):
    assert open_source("shop", "nobody").count() is None


def test_the_whole_catalogue_is_walked_a_page_at_a_time(etsy, tmp_path):
    base, state = etsy
    state.listings = 7
    state.page_size = 3                       # forces three pages

    designs = list(open_source("shop", "my-shop", cache_dir=tmp_path).designs())
    assert len(designs) == 7
    assert designs[0].title == "Listing 0"
    assert designs[0].tags == ["wedding", "invitation"]
    assert designs[0].listing_url.endswith("/1000/x")
    assert len(designs[0].images) == 2
    assert all(p.exists() for d in designs for p in d.images)


def test_a_limit_stops_it_early(etsy, tmp_path):
    designs = list(open_source("shop", "my-shop", cache_dir=tmp_path, limit=2).designs())
    assert len(designs) == 2


def test_a_rate_limit_partway_through_does_not_end_the_crawl(etsy, tmp_path):
    """The point of all of the above. A five-thousand listing pull will meet a
    429; before this, the first one ended it."""
    base, state = etsy
    state.listings = 4
    state.retry_after = "0"
    state.fail_next["/img/2-0.jpg"] = [429]
    state.fail_next["/v3/application/shops/42/listings/active"] = [503]

    designs = list(open_source("shop", "my-shop", cache_dir=tmp_path).designs())
    assert len(designs) == 4
    assert all(len(d.images) == 2 for d in designs)


def test_a_listing_whose_images_all_fail_is_counted_not_lost_in_silence(etsy, tmp_path, caplog):
    """It never reaches the pipeline, so if nothing says so it simply is not
    there — and across five thousand listings you want the number."""
    import logging

    base, state = etsy
    state.listings = 3
    state.fail_next["/img/1-0.jpg"] = [404]
    state.fail_next["/img/1-1.jpg"] = [404]

    with caplog.at_level(logging.WARNING, logger="stockforge.sources.shop"):
        designs = list(open_source("shop", "my-shop", cache_dir=tmp_path).designs())

    assert [d.design_id for d in designs] == ["1000", "1002"]
    assert "not one image could be fetched" in caplog.text
    assert "1 had no usable image" in caplog.text
