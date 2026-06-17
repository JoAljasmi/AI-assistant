"""Freeform note storage — durable memory for arbitrary facts the user wants
remembered, separate from dated tasks (preferences, passwords, "where I left X",
ideas). Same SQLite pattern as tasks.py: one local file, each tool returns a
string for the model.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "notes.db"


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


init_db()


def _coerce_id(note_id):
    try:
        return int(note_id)
    except (TypeError, ValueError):
        return None


def save_note(content):
    """Store a freeform note."""
    if not content or not content.strip():
        return "[error: a note needs some content]"
    now = datetime.now().isoformat(timespec="seconds")
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO notes (content, created_at) VALUES (?, ?)",
            (content.strip(), now),
        )
        note_id = cur.lastrowid
    return f"[saved note #{note_id}]"


def search_notes(query=""):
    """Return notes containing `query` (case-insensitive), or all notes if the
    query is empty. Newest first."""
    with _db() as conn:
        if query and query.strip():
            rows = conn.execute(
                "SELECT * FROM notes WHERE content LIKE ? ORDER BY id DESC",
                (f"%{query.strip()}%",),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM notes ORDER BY id DESC").fetchall()

    if not rows:
        return "[no matching notes]" if query else "[no notes saved yet]"
    return "\n".join(f"#{r['id']}: {r['content']}" for r in rows)


def delete_note(note_id):
    """Delete a note by id."""
    note_id = _coerce_id(note_id)
    if note_id is None:
        return "[error: note id must be a number]"
    with _db() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        deleted = cur.rowcount
    if deleted == 0:
        return f"[error: no note with id #{note_id}]"
    return f"[deleted note #{note_id}]"
