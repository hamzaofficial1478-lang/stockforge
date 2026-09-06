"""Door 2 — a pasted list of listing URLs.

The one door that had never been executed. The shop door has a dozen tests
against a fake Etsy and the folder door is exercised through the pipeline; this
one was written, wired into the CLI and the panel, and never run once.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np
import pytest

from stockforge.sources import open_source
from stockforge.sources.links import LinksSource, read_links


@pytest.fixture(autouse=True)
def no_politeness_pause(monkeypatch):
    """The source sleeps a second between listing pages to be a good citizen.
    Right in a crawl, pointless in a test."""
    import stockforge.sources.links as links_module
    monkeypatch.setattr(links_module.time, "sleep", lambda *_: None)


@pytest.fixture
def site(tmp_path):
    """Serves two images and one listing page that references them."""
    art = tmp_path / "art.jpg"
    cv2.imwrite(str(art), np.full((900, 600, 3), 230, np.uint8))
    blob = art.read_bytes()          # also read back by the handover test

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            state["hits"].append(path)
            # Only what is actually there. Serving every .jpg would make a
            # dead link indistinguishable from a live one.
            if path in ("/a.jpg", "/b.jpg"):
                return self._send(200, blob, "image/jpeg")
            self._send(404, b"nope", "text/plain")

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    state = {"hits": [], "base": f"http://127.0.0.1:{server.server_address[1]}"}
    threading.Thread(target=server.serve_forever,
                     kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


# --- reading the list itself ---------------------------------------------

def test_a_file_of_urls_one_per_line_is_read(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("https://a.example/1\nhttps://a.example/2\n")
    assert read_links(str(f)) == ["https://a.example/1", "https://a.example/2"]


def test_a_comma_separated_string_works_too():
    assert read_links("https://a.example/1,https://a.example/2") == [
        "https://a.example/1", "https://a.example/2"]


def test_blank_lines_and_stray_text_are_ignored(tmp_path):
    """People paste out of a spreadsheet. Half of what arrives is not a URL."""
    f = tmp_path / "urls.txt"
    f.write_text("\n  \nhttps://a.example/1\nnot a url\n\nhttps://a.example/2\n#done\n")
    assert read_links(str(f)) == ["https://a.example/1", "https://a.example/2"]


def test_counting_does_not_download_anything(tmp_path, site):
    f = tmp_path / "urls.txt"
    f.write_text(f"{site['base']}/a.jpg\n{site['base']}/b.jpg\n")
    assert open_source("links", str(f)).count() == 2
    assert site["hits"] == [], "counting fetched the images"


# --- pulling ---------------------------------------------------------------

def test_a_link_straight_to_an_image_is_one_design(tmp_path, site):
    f = tmp_path / "urls.txt"
    f.write_text(f"{site['base']}/a.jpg\n")
    designs = list(LinksSource(str(f), cache_dir=tmp_path / "cache").designs())

    assert len(designs) == 1
    assert len(designs[0].images) == 1
    assert designs[0].images[0].is_file()
    assert designs[0].source == "links"
    assert designs[0].listing_url == f"{site['base']}/a.jpg"


def test_a_link_to_a_listing_page_is_handed_to_the_listing_reader(tmp_path, site,
                                                                  monkeypatch):
    """The whole point of the door: paste the listing, not each image. The page
    itself is read by the shop scraper, so this checks the handover — that a
    link which is not an image goes there, and what comes back is yielded."""
    import stockforge.sources.shop as shop_module
    from stockforge.sources.base import Design

    asked: list[str] = []

    def _fake(url, dest, max_images=8):
        asked.append(url)
        dest.mkdir(parents=True, exist_ok=True)
        one = dest / "img.jpg"
        one.write_bytes((tmp_path / "art.jpg").read_bytes())
        return Design(design_id=url, images=[one], listing_url=url, source="links")

    monkeypatch.setattr(shop_module, "listing_images", _fake)
    f = tmp_path / "urls.txt"
    f.write_text(f"{site['base']}/listing/123\n")
    designs = list(LinksSource(str(f), cache_dir=tmp_path / "cache").designs())

    assert asked == [f"{site['base']}/listing/123"], \
        "a listing page was not handed to the listing reader"
    assert len(designs) == 1
    assert designs[0].images[0].is_file()


def test_the_listing_page_reader_only_knows_etsy(tmp_path):
    """Worth stating plainly rather than discovering on a batch: the image
    pattern matches Etsy's CDN and nothing else, so a link to a listing page
    anywhere else comes back with no images and is skipped. Direct links to
    image files work everywhere."""
    from stockforge.sources.shop import _IMG

    assert _IMG.findall('<img src="https://example.com/a.jpg">') == []
    assert _IMG.findall('<img src="https://i.etsystatic.com/1/il_570xN.123.jpg">')


def test_each_link_is_kept_apart_from_the_others(tmp_path, site):
    """Two designs sharing a cache folder would overwrite each other's images
    and silently become one."""
    f = tmp_path / "urls.txt"
    f.write_text(f"{site['base']}/a.jpg\n{site['base']}/a.jpg\n")
    designs = list(LinksSource(str(f), cache_dir=tmp_path / "cache").designs())

    assert len(designs) == 2
    a, b = designs[0].images[0], designs[1].images[0]
    assert a != b, "both links wrote to the same file"
    assert a.is_file() and b.is_file()


def test_a_limit_stops_it_early(tmp_path, site):
    f = tmp_path / "urls.txt"
    f.write_text("\n".join(f"{site['base']}/a.jpg" for _ in range(5)))
    designs = list(LinksSource(str(f), cache_dir=tmp_path / "cache", limit=2).designs())
    assert len(designs) == 2


def test_a_dead_link_does_not_stop_the_rest(tmp_path, site):
    """In a pasted list of five thousand, some are gone. Losing the other four
    thousand nine hundred to one 404 is not acceptable."""
    f = tmp_path / "urls.txt"
    f.write_text(f"{site['base']}/missing.jpg\n{site['base']}/a.jpg\n")
    designs = list(LinksSource(str(f), cache_dir=tmp_path / "cache").designs())

    assert len(designs) == 1, "the dead link took the live one with it"
    assert designs[0].listing_url.endswith("/a.jpg")


def test_a_listing_that_yields_no_images_is_skipped(tmp_path, site, monkeypatch):
    """A design with no images cannot be analysed, and one that reaches the
    pipeline empty just fails there instead of here."""
    import stockforge.sources.shop as shop_module
    from stockforge.sources.base import Design

    monkeypatch.setattr(shop_module, "listing_images",
                        lambda url, dest, max_images=8: Design(
                            design_id=url, listing_url=url, source="links"))
    f = tmp_path / "urls.txt"
    f.write_text(f"{site['base']}/listing/1\n{site['base']}/a.jpg\n")
    designs = list(LinksSource(str(f), cache_dir=tmp_path / "cache").designs())

    assert len(designs) == 1
    assert designs[0].images


# --- through the pipeline --------------------------------------------------

def test_the_door_works_end_to_end(tmp_path, site):
    """open_source is what the CLI and the panel both call. A direct image link
    needs nothing Etsy-specific, so this runs the real thing all the way."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import build_font_library, build_motif_library
    from test_pipeline import ScriptedProvider

    from stockforge import providers
    from stockforge.config import Settings
    from stockforge.pipeline import Pipeline

    f = tmp_path / "urls.txt"
    f.write_text(f"{site['base']}/a.jpg\n{site['base']}/b.jpg\n")

    build_font_library(tmp_path / "fonts")
    build_motif_library(tmp_path / "motifs")
    cfg = Settings(root=tmp_path / "work", fonts_dir=tmp_path / "fonts",
                   motifs_dir=tmp_path / "motifs")
    provider = ScriptedProvider()
    providers.set_provider("vision", provider)
    providers.set_provider("reason", provider)

    pipe = Pipeline(cfg)
    assert pipe.pull(open_source("links", str(f),
                                 cache_dir=cfg.root / "downloads")) == 2
    assert pipe.status()["designs"] == 2
