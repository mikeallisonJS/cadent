import sqlite3
import time

from cadent.history import History


def _backdate(h: History, row_id: int, days: float) -> None:
    h.conn.execute("UPDATE dictations SET ts = ? WHERE id = ?",
                   (time.time() - days * 86_400, row_id))
    h.conn.commit()


def test_add_and_search(tmp_path):
    h = History(tmp_path / "history.db")
    h.add("hello world raw", "Hello world.", 2.5, "code.exe", "flow")
    h.add("another entry", None, 1.0, "chrome.exe", "raw")
    assert len(h.search()) == 2
    rows = h.search("hello")
    assert len(rows) == 1
    assert rows[0]["cleaned_text"] == "Hello world."
    h.purge()
    assert h.search() == []


def test_outcome_lifecycle(tmp_path):
    h = History(tmp_path / "history.db")
    row_id = h.add("hello", None, 1.0, "code.exe", "raw")
    assert h.search()[0]["outcome"] == "pending"
    h.set_outcome(row_id, "inserted")
    assert h.search()[0]["outcome"] == "inserted"


def test_set_cleaned_records_inserted_text(tmp_path):
    h = History(tmp_path / "history.db")
    row_id = h.add("my email sig", None, 1.0, "code.exe", "raw")
    h.set_cleaned(row_id, "Best,\nMike")
    row = h.search()[0]
    assert row["raw_text"] == "my email sig"
    assert row["cleaned_text"] == "Best,\nMike"


def test_delete_removes_only_that_entry(tmp_path):
    h = History(tmp_path / "history.db")
    keep_id = h.add("keep me", None, 1.0, "code.exe", "raw")
    drop_id = h.add("drop me", None, 1.0, "code.exe", "raw")
    h.delete(drop_id)
    rows = h.search()
    assert [r["id"] for r in rows] == [keep_id]


def test_prune_deletes_entries_older_than_retention(tmp_path):
    h = History(tmp_path / "history.db")
    old_id = h.add("forty days old", None, 1.0, "code.exe", "raw")
    _backdate(h, old_id, 40)
    fresh_id = h.add("from today", None, 1.0, "code.exe", "raw")
    h.prune(30)
    assert [r["id"] for r in h.search()] == [fresh_id]


def test_prune_zero_retention_keeps_forever(tmp_path):
    h = History(tmp_path / "history.db")
    ancient_id = h.add("three years old", None, 1.0, "code.exe", "raw")
    _backdate(h, ancient_id, 3 * 365)
    h.prune(0)
    assert [r["id"] for r in h.search()] == [ancient_id]


def test_scaffold_era_db_migrates_without_data_loss(tmp_path):
    db = tmp_path / "history.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE dictations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL, raw_text TEXT NOT NULL, cleaned_text TEXT,
            duration_s REAL, app_name TEXT, mode TEXT);
        INSERT INTO dictations (ts, raw_text, cleaned_text, duration_s, app_name, mode)
        VALUES (1700000000.0, 'old entry', NULL, 2.0, 'notepad.exe', 'raw');
    """)
    conn.commit()
    conn.close()

    h = History(db)
    rows = h.search()
    assert len(rows) == 1
    assert rows[0]["raw_text"] == "old entry"
    assert rows[0]["outcome"] is None
    row_id = h.add("new entry", None, 1.0, "code.exe", "raw")
    h.set_outcome(row_id, "inserted")
    assert len(h.search()) == 2
