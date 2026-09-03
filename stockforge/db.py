"""State. Plain SQLite — 5,000 assets does not need Postgres, and a single
file makes the whole run trivially resumable and inspectable.

Every stage is idempotent: it reads rows in one state, writes rows in the next.
Kill the process at any point and re-running picks up exactly where it stopped.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id            TEXT PRIMARY KEY,      -- sha256 of the original bytes
    src_path      TEXT NOT NULL,
    flat_path     TEXT,                  -- normalised, de-mockupped artwork
    width         INTEGER,
    height        INTEGER,
    aspect        REAL,
    phash         TEXT,
    is_mockup     INTEGER DEFAULT 0,
    cluster_id    TEXT,
    state         TEXT NOT NULL DEFAULT 'ingested',
    error         TEXT,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS clusters (
    id            TEXT PRIMARY KEY,
    representative TEXT,                 -- asset id we actually analyse
    member_count  INTEGER DEFAULT 0,
    state         TEXT NOT NULL DEFAULT 'pending',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS specs (
    cluster_id    TEXT PRIMARY KEY,
    spec_json     TEXT NOT NULL,
    round         INTEGER DEFAULT 0,
    similarity    REAL,
    polish        REAL,
    verdict       TEXT,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS builds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id    TEXT NOT NULL,
    asset_id      TEXT,                  -- variants build per member asset
    svg_path      TEXT,
    pdf_path      TEXT,
    preview_path  TEXT,
    state         TEXT NOT NULL DEFAULT 'built',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS review (
    cluster_id    TEXT PRIMARY KEY,
    reason        TEXT,
    score         REAL,
    decision      TEXT,                  -- NULL until a human touches it
    decided_at    REAL
);

CREATE TABLE IF NOT EXISTS spend (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    day           TEXT NOT NULL,
    stage         TEXT NOT NULL,
    cluster_id    TEXT,
    input_tokens  INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    usd           REAL DEFAULT 0,
    at            REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assets_state   ON assets(state);
CREATE INDEX IF NOT EXISTS idx_assets_cluster ON assets(cluster_id);
CREATE INDEX IF NOT EXISTS idx_clusters_state ON clusters(state);
CREATE INDEX IF NOT EXISTS idx_spend_day      ON spend(day);
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

    # -- clusters --------------------------------------------------------

    def upsert_cluster(self, cid: str, representative: str, member_count: int) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO clusters (id, representative, member_count, created_at) "
                "VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "representative=excluded.representative, member_count=excluded.member_count",
                (cid, representative, member_count, time.time()),
            )

    def clusters(self, state: str | None = None) -> list[sqlite3.Row]:
        if state:
            return self.conn.execute("SELECT * FROM clusters WHERE state=?", (state,)).fetchall()
        return self.conn.execute("SELECT * FROM clusters").fetchall()

    def set_cluster_state(self, cid: str, state: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE clusters SET state=? WHERE id=?", (state, cid))

    # -- specs -----------------------------------------------------------

    def save_spec(self, cid: str, spec: Any, round_: int = 0, **scores: Any) -> None:
        payload = spec if isinstance(spec, str) else json.dumps(spec, default=str)
        with self.tx() as c:
            c.execute(
                "INSERT INTO specs (cluster_id, spec_json, round, similarity, polish, verdict, updated_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(cluster_id) DO UPDATE SET "
                "spec_json=excluded.spec_json, round=excluded.round, similarity=excluded.similarity, "
                "polish=excluded.polish, verdict=excluded.verdict, updated_at=excluded.updated_at",
                (
                    cid, payload, round_,
                    scores.get("similarity"), scores.get("polish"),
                    scores.get("verdict"), time.time(),
                ),
            )

    def get_spec(self, cid: str) -> dict | None:
        row = self.conn.execute("SELECT spec_json FROM specs WHERE cluster_id=?", (cid,)).fetchone()
        return json.loads(row["spec_json"]) if row else None

    # -- review ----------------------------------------------------------

    def queue_review(self, cid: str, reason: str, score: float) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO review (cluster_id, reason, score) VALUES (?,?,?) "
                "ON CONFLICT(cluster_id) DO UPDATE SET reason=excluded.reason, score=excluded.score",
                (cid, reason, score),
            )

    def pending_review(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM review WHERE decision IS NULL ORDER BY score ASC"
        ).fetchall()

    # -- spend -----------------------------------------------------------

    def record_spend(self, stage: str, cid: str | None, usage: dict, usd: float) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO spend (day, stage, cluster_id, input_tokens, cached_tokens, "
                "output_tokens, usd, at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    time.strftime("%Y-%m-%d"), stage, cid,
                    usage.get("input_tokens", 0),
                    usage.get("cache_read_input_tokens", 0),
                    usage.get("output_tokens", 0),
                    usd, time.time(),
                ),
            )

    def spend_today(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(usd), 0) AS t FROM spend WHERE day=?",
            (time.strftime("%Y-%m-%d"),),
        ).fetchone()
        return float(row["t"])
