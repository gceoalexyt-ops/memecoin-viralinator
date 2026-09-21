"""SQLite persistence: posts, metrics, format scores, spend ledger.

One file, committed nowhere (see .gitignore). In GitHub Actions the database
is restored from and saved to the workflow cache, so learned weights and the
spend ledger survive across runs.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import REPO_ROOT

DEFAULT_DB = REPO_ROOT / "data" / "viralinator.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    format_key    TEXT NOT NULL,
    body          TEXT NOT NULL,
    status        TEXT NOT NULL,            -- draft|rejected|posted|failed
    reject_reason TEXT,
    x_post_id     TEXT UNIQUE,
    posted_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_posted_at ON posts(posted_at);

CREATE TABLE IF NOT EXISTS metrics (
    post_id     INTEGER NOT NULL REFERENCES posts(id),
    measured_at TEXT NOT NULL,
    impressions INTEGER DEFAULT 0,
    likes       INTEGER DEFAULT 0,
    reposts     INTEGER DEFAULT 0,
    replies     INTEGER DEFAULT 0,
    quotes      INTEGER DEFAULT 0,
    bookmarks   INTEGER DEFAULT 0,
    PRIMARY KEY (post_id, measured_at)
);

CREATE TABLE IF NOT EXISTS format_scores (
    format_key TEXT PRIMARY KEY,
    n          INTEGER NOT NULL DEFAULT 0,
    mean_score REAL    NOT NULL DEFAULT 0.0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS spend (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    service TEXT NOT NULL,                  -- x|anthropic
    kind    TEXT NOT NULL,
    usd     REAL NOT NULL,
    note    TEXT
);

CREATE INDEX IF NOT EXISTS idx_spend_at ON spend(at);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PostRow:
    id: int
    created_at: str
    format_key: str
    body: str
    status: str
    reject_reason: str | None
    x_post_id: str | None
    posted_at: str | None


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # --- posts -----------------------------------------------------------------

    def record_draft(self, format_key: str, body: str) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO posts (created_at, format_key, body, status) "
                "VALUES (?, ?, ?, 'draft')",
                (utcnow(), format_key, body),
            )
        return int(cur.lastrowid)

    def mark_rejected(self, post_id: int, reason: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE posts SET status='rejected', reject_reason=? WHERE id=?",
                (reason, post_id),
            )

    def mark_posted(self, post_id: int, x_post_id: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE posts SET status='posted', x_post_id=?, posted_at=? WHERE id=?",
                (x_post_id, utcnow(), post_id),
            )

    def mark_failed(self, post_id: int, reason: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE posts SET status='failed', reject_reason=? WHERE id=?",
                (reason, post_id),
            )

    def last_posted_at(self) -> datetime | None:
        row = self._conn.execute(
            "SELECT posted_at FROM posts WHERE status='posted' "
            "ORDER BY posted_at DESC LIMIT 1"
        ).fetchone()
        if not row or not row["posted_at"]:
            return None
        return datetime.fromisoformat(row["posted_at"])

    def posted_since(self, since_iso: str) -> list[PostRow]:
        rows = self._conn.execute(
            "SELECT * FROM posts WHERE status='posted' AND posted_at >= ? "
            "ORDER BY posted_at",
            (since_iso,),
        ).fetchall()
        return [PostRow(**dict(r)) for r in rows]

    def posts_awaiting_measurement(self, older_than_iso: str) -> list[PostRow]:
        """Posted items with no metrics row yet, old enough to have settled."""
        rows = self._conn.execute(
            "SELECT p.* FROM posts p "
            "LEFT JOIN metrics m ON m.post_id = p.id "
            "WHERE p.status='posted' AND p.posted_at <= ? AND m.post_id IS NULL",
            (older_than_iso,),
        ).fetchall()
        return [PostRow(**dict(r)) for r in rows]

    # --- metrics ---------------------------------------------------------------

    def record_metrics(self, post_id: int, **counts: int) -> None:
        fields = ("impressions", "likes", "reposts", "replies", "quotes", "bookmarks")
        values = [int(counts.get(f, 0)) for f in fields]
        with self._tx() as c:
            c.execute(
                f"INSERT OR REPLACE INTO metrics (post_id, measured_at, {', '.join(fields)}) "
                f"VALUES (?, ?, {', '.join('?' * len(fields))})",
                (post_id, utcnow(), *values),
            )

    def latest_metrics(self, post_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM metrics WHERE post_id=? ORDER BY measured_at DESC LIMIT 1",
            (post_id,),
        ).fetchone()

    # --- format scores ---------------------------------------------------------

    def get_format_scores(self) -> dict[str, tuple[int, float]]:
        rows = self._conn.execute("SELECT format_key, n, mean_score FROM format_scores").fetchall()
        return {r["format_key"]: (r["n"], r["mean_score"]) for r in rows}

    def update_format_score(self, format_key: str, score: float) -> None:
        """Running mean — each new observation nudges the format's score."""
        with self._tx() as c:
            row = c.execute(
                "SELECT n, mean_score FROM format_scores WHERE format_key=?", (format_key,)
            ).fetchone()
            if row:
                n, mean = row["n"] + 1, row["mean_score"]
                new_mean = mean + (score - mean) / n
                c.execute(
                    "UPDATE format_scores SET n=?, mean_score=?, updated_at=? WHERE format_key=?",
                    (n, new_mean, utcnow(), format_key),
                )
            else:
                c.execute(
                    "INSERT INTO format_scores (format_key, n, mean_score, updated_at) "
                    "VALUES (?, 1, ?, ?)",
                    (format_key, score, utcnow()),
                )

    # --- spend -----------------------------------------------------------------

    def record_spend(self, service: str, kind: str, usd: float, note: str = "") -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO spend (at, service, kind, usd, note) VALUES (?, ?, ?, ?, ?)",
                (utcnow(), service, kind, usd, note),
            )

    def spend_since(self, since_iso: str) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(usd), 0.0) AS total FROM spend WHERE at >= ?",
            (since_iso,),
        ).fetchone()
        return float(row["total"])

    def spend_breakdown_since(self, since_iso: str) -> list[tuple[str, str, float]]:
        rows = self._conn.execute(
            "SELECT service, kind, SUM(usd) AS total FROM spend WHERE at >= ? "
            "GROUP BY service, kind ORDER BY total DESC",
            (since_iso,),
        ).fetchall()
        return [(r["service"], r["kind"], float(r["total"])) for r in rows]
