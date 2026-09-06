"""Files handed to the panel from a browser.

The folder door assumed the images were already somewhere you could type the
path of. From the browser it was built for, with a shop's exports in a
download folder or still zipped, there was no way to put them anywhere — so
the door could not be used at all.
"""

import io
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from stockforge.ui import uploads


def _jpg(w=400, h=560) -> bytes:
    ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), 230, np.uint8))
    assert ok
    return buf.tobytes()


def _png() -> bytes:
    ok, buf = cv2.imencode(".png", np.full((100, 100, 3), 200, np.uint8))
    return buf.tobytes()


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


def _multipart(files: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    """What a browser sends for <input type=file multiple>."""
    boundary = "----stockforgetest"
    out = io.BytesIO()
    for name, content in files:
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="files"; '
                  f'filename="{name}"\r\n'.encode())
        out.write(b"Content-Type: application/octet-stream\r\n\r\n")
        out.write(content)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


# --- reading what the browser sent ---------------------------------------

def test_several_files_are_read_out_of_one_upload():
    body, ctype = _multipart([("a.jpg", _jpg()), ("b.png", _png())])
    parts = uploads.parse_multipart(body, ctype)
    assert [n for n, _ in parts] == ["a.jpg", "b.png"]
    assert parts[0][1] == _jpg()


def test_a_filename_cannot_climb_out_of_the_folder():
    """A browser sends whatever the file was called and an archive can hold
    anything at all, including ../../."""
    body, ctype = _multipart([("../../../etc/passwd", b"x"),
                              ("..\\\\..\\\\windows\\\\system32\\\\a.jpg", _jpg())])
    names = [n for n, _ in uploads.parse_multipart(body, ctype)]
    assert names == ["passwd", "a.jpg"]
    assert not any("/" in n or "\\" in n or ".." in n for n in names)


# --- what lands, and what is said about it -------------------------------

def test_every_extension_the_program_reads_is_accepted(tmp_path):
    batch = uploads.receive(
        [("a.jpg", _jpg()), ("b.jpeg", _jpg()), ("c.png", _png()),
         ("d.webp", _png()), ("e.svg", SVG)], tmp_path / "in")
    assert batch.images == 5, [f.reason for f in batch.files if not f.ok]
    assert set(batch.as_dict()["by_extension"]) == {".jpg", ".jpeg", ".png",
                                                    ".webp", ".svg"}


def test_a_file_that_is_not_an_image_is_refused_and_named(tmp_path):
    """"12 of 50 uploaded" with no list of the other 38 is the failure this
    whole project keeps running into."""
    batch = uploads.receive([("notes.txt", b"hello"), ("good.jpg", _jpg())],
                            tmp_path / "in")
    result = batch.as_dict()
    assert result["accepted"] == 1
    assert result["rejected"] == 1
    bad = next(f for f in result["files"] if not f["ok"])
    assert bad["name"] == "notes.txt"
    assert ".txt" in bad["reason"] and ".jpg" in bad["reason"], bad["reason"]


def test_an_empty_file_is_caught_rather_than_saved(tmp_path):
    batch = uploads.receive([("empty.jpg", b"")], tmp_path / "in")
    assert batch.images == 0
    assert "empty" in batch.files[0].reason


def test_two_files_with_one_name_do_not_overwrite_each_other(tmp_path):
    batch = uploads.receive([("a.jpg", _jpg()), ("a.jpg", _jpg(200, 200))],
                            tmp_path / "in")
    assert batch.images == 2
    assert len(list((tmp_path / "in").glob("*.jpg"))) == 2


# --- archives -------------------------------------------------------------

def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries:
            zf.writestr(name, content)
    return buf.getvalue()


def test_a_zip_is_unpacked_and_counted(tmp_path):
    """Exports arrive zipped. Asking someone to unpack it first is asking them
    to do the program's job."""
    blob = _zip([("designs/a.jpg", _jpg()), ("designs/b.png", _png()),
                 ("designs/c.svg", SVG)])
    batch = uploads.receive([("designs.zip", blob)], tmp_path / "in")

    result = batch.as_dict()
    archive = result["files"][0]
    assert archive["kind"] == "archive"
    assert archive["ok"] is True
    assert archive["extracted"] == 3
    assert sorted(p.name for p in (tmp_path / "in").iterdir()) == \
        ["a.jpg", "b.png", "c.svg"]


def test_a_zip_keeps_nested_folders_flat_without_losing_files(tmp_path):
    blob = _zip([("one/a.jpg", _jpg()), ("two/a.jpg", _jpg(300, 300))])
    batch = uploads.receive([("d.zip", blob)], tmp_path / "in")
    assert batch.files[0].extracted == 2, "one overwrote the other"


def test_the_junk_windows_and_macos_put_in_a_zip_is_ignored(tmp_path):
    blob = _zip([("__MACOSX/._a.jpg", b"junk"), (".DS_Store", b"junk"),
                 ("Thumbs.db", b"junk"), ("a.jpg", _jpg())])
    batch = uploads.receive([("d.zip", blob)], tmp_path / "in")
    assert batch.files[0].extracted == 1
    assert [p.name for p in (tmp_path / "in").iterdir()] == ["a.jpg"]


def test_a_zip_says_what_it_left_out(tmp_path):
    blob = _zip([("a.jpg", _jpg()), ("readme.txt", b"x"), ("prices.csv", b"x")])
    batch = uploads.receive([("d.zip", blob)], tmp_path / "in")
    assert batch.files[0].extracted == 1
    assert "2 file(s)" in batch.files[0].reason


def test_a_zip_with_no_images_says_so_rather_than_looking_successful(tmp_path):
    blob = _zip([("readme.txt", b"x")])
    batch = uploads.receive([("d.zip", blob)], tmp_path / "in")
    assert batch.files[0].ok is False
    assert "no images" in batch.files[0].reason


def test_something_that_is_not_really_a_zip_is_reported(tmp_path):
    batch = uploads.receive([("broken.zip", b"this is not a zip")], tmp_path / "in")
    assert batch.files[0].ok is False
    assert "zip" in batch.files[0].reason
    assert not list((tmp_path / "in").glob("*.zip")), "the bad archive was kept"


def test_a_zip_cannot_write_outside_the_folder(tmp_path):
    """A zip can name its entries anything, including a path upwards."""
    blob = _zip([("../../escaped.jpg", _jpg())])
    uploads.receive([("d.zip", blob)], tmp_path / "in")
    assert (tmp_path / "in" / "escaped.jpg").is_file()
    assert not (tmp_path / "escaped.jpg").exists()
