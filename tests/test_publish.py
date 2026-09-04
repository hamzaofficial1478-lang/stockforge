"""The delivery half — metadata, the agency CSVs, and the upload itself.

The upload runs against a real FTP server rather than a mock, because what
went wrong here was in the parts a mock would have agreed with.
"""

import csv
import ftplib
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from stockforge.publish.ftp import FTPTarget, upload_batch
from stockforge.publish.metadata import Metadata, adobe_csv, shutterstock_csv, write_metadata


@pytest.fixture
def ftp_server(tmp_path):
    """A real FTP server on a spare port, serving a directory we can inspect."""
    root = tmp_path / "remote"
    root.mkdir()

    authorizer = DummyAuthorizer()
    authorizer.add_user("stock", "secret", str(root), perm="elradfmwMT")

    handler = type("Handler", (FTPHandler,), {"authorizer": authorizer})
    server = FTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"timeout": 0.1},
                              daemon=True)
    thread.start()
    try:
        yield FTPTarget(name="test", host="127.0.0.1", port=server.address[1],
                        username="stock", password="secret", use_tls=False), root
    finally:
        server.close_all()


def _files(folder: Path, *names: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i, name in enumerate(names):
        p = folder / name
        p.write_bytes(b"%!PS-Adobe-3.0 EPSF-3.0\n" + bytes([65 + i]) * 500)
        out.append(p)
    return out


# --- the upload -----------------------------------------------------------

def test_files_actually_arrive(ftp_server, tmp_path):
    target, remote = ftp_server
    files = _files(tmp_path / "out", "one.eps", "two.eps")

    result = upload_batch(target, files)

    assert result == {"one.eps": "uploaded", "two.eps": "uploaded"}
    assert sorted(p.name for p in remote.iterdir()) == ["one.eps", "two.eps"]
    assert (remote / "one.eps").read_bytes() == files[0].read_bytes()


def test_a_run_that_died_picks_up_where_it_stopped(ftp_server, tmp_path):
    """Resumable at the batch level: anything already there at the right size
    is left alone."""
    target, remote = ftp_server
    files = _files(tmp_path / "out", "one.eps", "two.eps", "three.eps")

    upload_batch(target, files[:1])
    result = upload_batch(target, files)

    assert result["one.eps"] == "skipped"
    assert result["two.eps"] == "uploaded"
    assert result["three.eps"] == "uploaded"


def test_a_half_written_file_is_sent_again(ftp_server, tmp_path):
    target, remote = ftp_server
    files = _files(tmp_path / "out", "one.eps")
    (remote / "one.eps").write_bytes(b"truncated")

    assert upload_batch(target, files) == {"one.eps": "uploaded"}
    assert (remote / "one.eps").read_bytes() == files[0].read_bytes()


def test_a_missing_local_file_is_reported_not_raised(ftp_server, tmp_path):
    target, _ = ftp_server
    files = _files(tmp_path / "out", "one.eps")
    result = upload_batch(target, files + [tmp_path / "out" / "gone.eps"])
    assert result["one.eps"] == "uploaded"
    assert result["gone.eps"].startswith("failed")


# --- the retry that never ran ---------------------------------------------

class _Flaky:
    """Fails the first n attempts, then behaves."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.attempts = 0
        self.stored: list[str] = []

    def mlsd(self):
        return []

    def storbinary(self, cmd, fh, blocksize=0):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise ftplib.error_temp("451 requested action aborted")
        self.stored.append(cmd.split(" ", 1)[1])

    def quit(self):
        pass


def test_a_transient_error_is_retried_rather_than_raised(tmp_path):
    """`ftplib.all_errors` is itself a tuple, and nesting one tuple inside
    another is a TypeError in Python 3 — so the retry never ran and the first
    hiccup on a long upload took the whole batch with it."""
    target = FTPTarget(name="t", host="h", username="u", password="p")
    files = _files(tmp_path / "out", "one.eps")
    flaky = _Flaky(fail_times=1)

    with patch("stockforge.publish.ftp.connect", return_value=flaky):
        result = upload_batch(target, files, retries=3)

    assert result == {"one.eps": "uploaded"}
    assert flaky.stored == ["one.eps"]


def test_a_file_that_keeps_failing_does_not_take_the_batch_with_it(tmp_path):
    target = FTPTarget(name="t", host="h", username="u", password="p")
    files = _files(tmp_path / "out", "one.eps")
    with patch("stockforge.publish.ftp.connect", return_value=_Flaky(fail_times=99)):
        result = upload_batch(target, files, retries=2)
    assert result["one.eps"].startswith("failed")


# --- the CSVs -------------------------------------------------------------

def _meta(name: str) -> Metadata:
    return Metadata(filename=name,
                    title="Halloween party invitation with haunted house",
                    keywords=["halloween", "invitation", "pumpkin"],
                    category="Graphic Resources",
                    description="Halloween party invitation with haunted house")


def test_the_agency_csvs_carry_the_columns_they_say_they_do(tmp_path):
    rows = [_meta("one.eps"), _meta("two.eps")]

    with adobe_csv(rows, tmp_path / "adobe.csv").open(newline="") as fh:
        adobe = list(csv.reader(fh))
    assert adobe[0] == ["Filename", "Title", "Keywords", "Category", "Releases"]
    assert adobe[1][0] == "one.eps"
    assert adobe[1][2] == "halloween, invitation, pumpkin"

    with shutterstock_csv(rows, tmp_path / "ss.csv").open(newline="") as fh:
        shutter = list(csv.reader(fh))
    assert shutter[0][0] == "Filename"
    assert "Description" in shutter[0]
    assert len(shutter) == 3


def test_both_csvs_are_written_side_by_side(tmp_path):
    paths = write_metadata([_meta("one.eps")], tmp_path)
    assert set(paths) == {"adobe", "shutterstock"}
    assert all(p.is_file() for p in paths.values())
