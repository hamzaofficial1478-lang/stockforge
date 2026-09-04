"""State. Plain SQLite — 5,000 assets does not need Postgres, and a single
file makes the whole run trivially resumable and inspectable.

Every stage is idempotent: it reads rows in one state, writes rows in the next.
Kill the process at any point and re-running picks up exactly where it stopped.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("stockforge.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id            TEXT NOT NULL,         -- sha256 of the original bytes
    src_path      TEXT NOT NULL,
    flat_path     TEXT,                  -- normalised, de-mockupped artwork
    width         INTEGER,
    height        INTEGER,
    aspect        REAL,
    phash         TEXT,
    is_mockup     INTEGER DEFAULT 0,
    design_id     TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'ingested',
    error         TEXT,
    created_at    REAL NOT NULL,
    -- One row per image per design, not one per image. A shop reuses the same
    -- size chart, the same 'instant download' graphic and the same mockup
    -- backdrop across every listing it has. Keying on the bytes alone gave
    -- such an image to whichever listing was pulled first and quietly took it
    -- from the rest; a listing whose images were all shared ended up with
    -- none at all and failed to build.
    PRIMARY KEY (id, design_id)
);

CREATE TABLE IF NOT EXISTS designs (
    id            TEXT PRIMARY KEY,      -- stable hash of the source's design id
    design_key    TEXT,                  -- listing id, url or folder name
    title         TEXT,
    tags          TEXT,
    listing_url   TEXT,
    source        TEXT,
    image_count   INTEGER DEFAULT 0,
    page_count    INTEGER DEFAULT 0,
    stock_safe    INTEGER,               -- NULL until provenance runs
    state         TEXT NOT NULL DEFAULT 'pending',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS specs (
    design_id     TEXT PRIMARY KEY,
    spec_json     TEXT NOT NULL,
    round         INTEGER DEFAULT 0,
    similarity    REAL,
    polish        REAL,
    distinctness  REAL,
    verdict       TEXT,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS builds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id     TEXT NOT NULL,
    page_name     TEXT,
    svg_path      TEXT,
    pdf_path      TEXT,
    preview_path  TEXT,
    state         TEXT NOT NULL DEFAULT 'built',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS review (
    design_id     TEXT PRIMARY KEY,
    reason        TEXT,
    score         REAL,
    decision      TEXT,                  -- NULL until a human touches it
    decided_at    REAL
);


CREATE INDEX IF NOT EXISTS idx_assets_state   ON assets(state);
CREATE INDEX IF NOT EXISTS idx_assets_design  ON assets(design_id);
CREATE INDEX IF NOT EXISTS idx_designs_state  ON designs(state);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Bring an older workspace up to date. There is one migration so far."""
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='assets'"
        ).fetchone()
        if not row or "PRIMARY KEY (id, design_id)" in row["sql"]:
            return

        # assets used to be keyed on the image bytes alone, so an image used
        # by two listings belonged to only one of them.
        log.info("migrating assets so a shared image can belong to every design that uses it")
        with self.tx() as c:
            c.execute("ALTER TABLE assets RENAME TO assets_v1")
            c.executescript(SCHEMA)
            c.execute(
                "INSERT OR IGNORE INTO assets (id, src_path, flat_path, width, height, "
                "aspect, phash, is_mockup, design_id, state, error, created_at) "
                "SELECT id, src_path, flat_path, width, height, aspect, phash, "
                "is_mockup, COALESCE(design_id, ''), state, error, created_at "
                "FROM assets_v1"
            )
            c.execute("DROP TABLE assets_v1")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- assets ----------------------------------------------------------

    def add_asset(self, **row: Any) -> bool:
        """Returns False if we've seen these exact bytes before. Etsy exports
        are full of duplicates; paying to analyse the same file twice is the
        easiest money in the world to waste."""
        row.setdefault("created_at", time.time())
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cur = self.conn.execute(
            f"INSERT OR IGNORE INTO assets ({cols}) VALUES ({marks})", tuple(row.values())
        )
        self.conn.commit()
        return cur.rowcount > 0

    def assets(self, state: str | None = None) -> list[sqlite3.Row]:
        if state:
            return self.conn.execute("SELECT * FROM assets WHERE state=?", (state,)).fetchall()
        return self.conn.execute("SELECT * FROM assets").fetchall()

    def set_asset_state(self, asset_id: str, state: str, error: str | None = None) -> None:
        with self.tx() as c:
            c.execute("UPDATE assets SET state=?, error=? WHERE id=?", (state, error, asset_id))

    # -- designs ---------------------------------------------------------

    # What a source is allowed to refresh on a design we have already seen.
    # Everything else — state, stock_safe, created_at — belongs to the work
    # done on that design and is not the source's to overwrite.
    REFRESHABLE = ("design_key", "title", "tags", "listing_url", "source",
                   "image_count", "page_count")

    def add_design(self, **row) -> None:
        """Record a design, or update what the source knows about a known one.

        Pulling the same folder or shop twice is the most ordinary thing there
        is — you add ten listings and re-run it. Replacing the row outright put
        every finished design back to `pending` and erased its provenance
        verdict, so the next run re-analysed the whole catalogue. On five
        thousand designs against a local card that is days.
        """
        row.setdefault("created_at", time.time())
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        updates = ", ".join(f"{c}=excluded.{c}" for c in self.REFRESHABLE if c in row)
        sql = (f"INSERT INTO designs ({cols}) VALUES ({marks}) ON CONFLICT(id) DO "
               + (f"UPDATE SET {updates}" if updates else "NOTHING"))
        with self.tx() as c:
            c.execute(sql, tuple(row.values()))

    def known_asset(self, asset_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone() is not None

    def designs(self, state: str | None = None) -> list[sqlite3.Row]:
        if state:
            return self.conn.execute("SELECT * FROM designs WHERE state=?", (state,)).fetchall()
        return self.conn.execute("SELECT * FROM designs").fetchall()

    def set_design_state(self, did: str, state: str, stock_safe: bool | None = None) -> None:
        with self.tx() as c:
            if stock_safe is None:
                c.execute("UPDATE designs SET state=? WHERE id=?", (state, did))
            else:
                c.execute("UPDATE designs SET state=?, stock_safe=? WHERE id=?",
                          (state, int(stock_safe), did))

    # -- specs -----------------------------------------------------------

    def save_spec(self, did: str, spec: Any, round_: int = 0, **scores: Any) -> None:
        payload = spec if isinstance(spec, str) else json.dumps(spec, default=str)
        with self.tx() as c:
            c.execute(
                "INSERT INTO specs (design_id, spec_json, round, similarity, polish, distinctness, "
                "verdict, updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(design_id) DO UPDATE SET "
                "spec_json=excluded.spec_json, round=excluded.round, similarity=excluded.similarity, "
                "polish=excluded.polish, distinctness=excluded.distinctness, verdict=excluded.verdict, "
                "updated_at=excluded.updated_at",
                (
                    did, payload, round_,
                    scores.get("similarity"), scores.get("polish"),
                    scores.get("distinct"), scores.get("verdict"), time.time(),
                ),
            )

    def get_spec(self, did: str) -> dict | None:
        row = self.conn.execute("SELECT spec_json FROM specs WHERE design_id=?", (did,)).fetchone()
        return json.loads(row["spec_json"]) if row else None

    # -- review ----------------------------------------------------------

    def queue_review(self, did: str, reason: str, score: float) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO review (design_id, reason, score) VALUES (?,?,?) "
                "ON CONFLICT(design_id) DO UPDATE SET reason=excluded.reason, score=excluded.score",
                (did, reason, score),
            )

    def pending_review(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM review WHERE decision IS NULL ORDER BY score ASC"
        ).fetchall()
