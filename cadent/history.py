"""Dictation history in SQLite.

Transcript safety: callers write the row (outcome "pending") BEFORE attempting
insertion, then record the outcome — a crash mid-injection never loses text.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    raw_text TEXT NOT NULL,
    cleaned_text TEXT,
    duration_s REAL,
    app_name TEXT,
    mode TEXT,
    outcome TEXT
);
CREATE INDEX IF NOT EXISTS idx_dictations_ts ON dictations(ts);
"""


class History:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(dictations)")}
        if "outcome" not in cols:  # scaffold-era database
            self.conn.execute("ALTER TABLE dictations ADD COLUMN outcome TEXT")
            self.conn.commit()

    def add(self, raw: str, cleaned: str | None, duration_s: float,
            app_name: str, mode: str, outcome: str = "pending") -> int:
        cur = self.conn.execute(
            "INSERT INTO dictations (ts, raw_text, cleaned_text, duration_s, app_name, "
            "mode, outcome) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), raw, cleaned, duration_s, app_name, mode, outcome),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def set_cleaned(self, row_id: int, cleaned: str) -> None:
        """Record what was actually inserted when it differs from raw
        (snippet replacement or LLM cleanup)."""
        self.conn.execute("UPDATE dictations SET cleaned_text = ? WHERE id = ?",
                          (cleaned, row_id))
        self.conn.commit()

    def set_outcome(self, row_id: int, outcome: str) -> None:
        self.conn.execute("UPDATE dictations SET outcome = ? WHERE id = ?",
                          (outcome, row_id))
        self.conn.commit()

    def search(self, query: str = "", limit: int = 100) -> list[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        if query:
            like = f"%{query}%"
            cur = self.conn.execute(
                "SELECT * FROM dictations WHERE raw_text LIKE ? OR cleaned_text LIKE ? "
                "ORDER BY ts DESC LIMIT ?", (like, like, limit))
        else:
            cur = self.conn.execute(
                "SELECT * FROM dictations ORDER BY ts DESC LIMIT ?", (limit,))
        return cur.fetchall()

    def delete(self, row_id: int) -> None:
        self.conn.execute("DELETE FROM dictations WHERE id = ?", (row_id,))
        self.conn.commit()

    def prune(self, retention_days: int) -> None:
        """Enforce the retention setting; 0 means keep forever (PRD 5.6)."""
        if retention_days <= 0:
            return
        cutoff = time.time() - retention_days * 86_400
        self.conn.execute("DELETE FROM dictations WHERE ts < ?", (cutoff,))
        self.conn.commit()

    def purge(self) -> None:
        self.conn.execute("DELETE FROM dictations")
        self.conn.commit()
