"""FTP delivery.

Both agencies take submissions over FTP, which is the only sane way to move a
few thousand files. Credentials come from the environment and never from the
database — an FTP password in a SQLite file that gets copied around is a bad
day waiting to happen.

Uploads are resumable at the batch level: anything already on the server at the
right size is skipped, so a run that dies at file 400 of 900 picks up at 401.
"""

from __future__ import annotations

import ftplib
import logging
import os
import ssl
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("stockforge.publish.ftp")


@dataclass
class FTPTarget:
    name: str
    host: str
    username: str
    password: str
    directory: str = "/"
    use_tls: bool = True
    port: int = 21

    @classmethod
    def from_env(cls, name: str) -> "FTPTarget":
        """SF_FTP_ADOBE_HOST, SF_FTP_ADOBE_USER, SF_FTP_ADOBE_PASS, ..."""
        p = f"SF_FTP_{name.upper()}"
        missing = [k for k in ("HOST", "USER", "PASS") if not os.environ.get(f"{p}_{k}")]
        if missing:
            raise RuntimeError(f"{name}: set {', '.join(f'{p}_{k}' for k in missing)}")
        return cls(
            name=name,
            host=os.environ[f"{p}_HOST"],
            username=os.environ[f"{p}_USER"],
            password=os.environ[f"{p}_PASS"],
            directory=os.environ.get(f"{p}_DIR", "/"),
            use_tls=os.environ.get(f"{p}_TLS", "1") != "0",
            port=int(os.environ.get(f"{p}_PORT", 21)),
        )


def connect(target: FTPTarget, timeout: int = 60) -> ftplib.FTP:
    if target.use_tls:
        ftp = ftplib.FTP_TLS(timeout=timeout)
        ftp.connect(target.host, target.port)
        ftp.login(target.username, target.password)
        ftp.prot_p()
    else:
        ftp = ftplib.FTP(timeout=timeout)
        ftp.connect(target.host, target.port)
        ftp.login(target.username, target.password)
    if target.directory and target.directory != "/":
        try:
            ftp.cwd(target.directory)
        except ftplib.error_perm:
            ftp.mkd(target.directory)
            ftp.cwd(target.directory)
    return ftp


def _remote_sizes(ftp: ftplib.FTP) -> dict[str, int]:
    sizes: dict[str, int] = {}
    try:
        for name, facts in ftp.mlsd():
            if facts.get("type") == "file":
                sizes[name] = int(facts.get("size", 0))
    except (ftplib.error_perm, AttributeError):
        for name in ftp.nlst():
            try:
                sizes[name] = ftp.size(name) or 0
            except ftplib.error_perm:
                sizes[name] = 0
    return sizes


def upload_batch(target: FTPTarget, files: list[Path], retries: int = 3) -> dict[str, str]:
    """Returns {filename: uploaded | skipped | failed: reason}.

    `ftplib.all_errors` is itself a tuple, so the handler below has to unpack
    it. Nesting one tuple inside another is a TypeError in Python 3, which
    meant the retry never ran: the first transient error on a long upload
    raised out of here instead, taking the result of every file that had
    already gone with it. On nine hundred files a transient error is not a
    possibility, it is a certainty.
    """
    result: dict[str, str] = {}
    ftp = connect(target)
    try:
        existing = _remote_sizes(ftp)

        for path in files:
            if not path.exists():
                result[path.name] = "failed: missing locally"
                continue
            if existing.get(path.name, -1) == path.stat().st_size:
                result[path.name] = "skipped"
                continue

            for attempt in range(1, retries + 1):
                try:
                    with path.open("rb") as fh:
                        ftp.storbinary(f"STOR {path.name}", fh, blocksize=1 << 20)
                    result[path.name] = "uploaded"
                    log.info("[%s] %s", target.name, path.name)
                    break
                except (*ftplib.all_errors, OSError) as exc:
                    if attempt == retries:
                        result[path.name] = f"failed: {exc}"
                        log.warning("[%s] %s failed: %s", target.name, path.name, exc)
                    else:
                        time.sleep(2 ** attempt)
                        try:
                            ftp.quit()
                        except Exception:
                            pass
                        ftp = connect(target)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
    return result
