"""Task + deadline storage for the assistant, backed by a local SQLite file.

Now time-aware: a task can have a due_date AND a due_time, and a `reminded`
flag so the scheduler (built next) fires each reminder exactly once.

Each function returns a plain string for the model, same convention as before
— except the scheduler helpers (due_reminders / mark_reminded), which return
data because the scheduler is code, not the model.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "tasks.db"

# Date-only tasks (no specific time) get reminded at this hour on their due day,
# so a dateless reminder doesn't fire at midnight.
DEFAULT_REMINDER_TIME = "09:00"


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
    """Create the table for a fresh DB, and migrate an older one in place by
    adding any missing columns. Safe to call every import; never drops data."""
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                description  TEXT NOT NULL,
                due_date     TEXT,                              -- 'YYYY-MM-DD' or NULL
                due_time     TEXT,                              -- 'HH:MM' or NULL
                status       TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'done'
                created_at   TEXT NOT NULL,
                completed_at TEXT,
                reminded     INTEGER NOT NULL DEFAULT 0         -- 0 = not yet reminded
            )
            """
        )
        # Migrate older databases that predate due_time / reminded.
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "due_time" not in existing:
            conn.execute("ALTER TABLE tasks ADD COLUMN due_time TEXT")
        if "reminded" not in existing:
            conn.execute("ALTER TABLE tasks ADD COLUMN reminded INTEGER NOT NULL DEFAULT 0")


init_db()


def _coerce_id(task_id):
    try:
        return int(task_id)
    except (TypeError, ValueError):
        return None


def _when_str(due_date, due_time):
    if not due_date:
        return ""
    return f" (due {due_date}" + (f" {due_time}" if due_time else "") + ")"


def add_task(description, due_date=None, due_time=None):
    """Add a task. due_date is 'YYYY-MM-DD', due_time is 'HH:MM' (24-hour); both
    optional. A time without a date is assumed to mean today."""
    if not description or not description.strip():
        return "[error: a task needs a description]"

    if due_date:
        try:
            datetime.strptime(due_date, "%Y-%m-%d")
        except ValueError:
            return (f"[error: due_date must be 'YYYY-MM-DD', got {due_date!r}. "
                    f"Work out the actual date from today and pass it that way.]")

    if due_time:
        try:
            datetime.strptime(due_time, "%H:%M")
        except ValueError:
            return (f"[error: due_time must be 'HH:MM' 24-hour, got {due_time!r}. "
                    f"Convert e.g. '7pm' to '19:00'.]")
        # A time with no date means today.
        if not due_date:
            due_date = datetime.now().strftime("%Y-%m-%d")

    now = datetime.now().isoformat(timespec="seconds")
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (description, due_date, due_time, created_at) "
            "VALUES (?, ?, ?, ?)",
            (description.strip(), due_date, due_time, now),
        )
        task_id = cur.lastrowid

    return f"[added task #{task_id}: {description.strip()}{_when_str(due_date, due_time)}]"


def list_tasks(status="pending"):
    """List tasks. status is 'pending', 'done', or 'all'. Soonest first."""
    if status not in ("pending", "done", "all"):
        return f"[error: status must be 'pending', 'done', or 'all', got {status!r}]"

    query = "SELECT * FROM tasks"
    params = ()
    if status != "all":
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY due_date IS NULL, due_date, due_time IS NULL, due_time, id"

    with _db() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return "[no tasks yet]" if status == "all" else f"[no {status} tasks]"

    lines = []
    for r in rows:
        mark = "x" if r["status"] == "done" else " "
        lines.append(f"[{mark}] #{r['id']}: {r['description']}{_when_str(r['due_date'], r['due_time'])}")
    return "\n".join(lines)


def complete_task(task_id):
    """Mark a task done, by id."""
    task_id = _coerce_id(task_id)
    if task_id is None:
        return "[error: task id must be a number]"

    now = datetime.now().isoformat(timespec="seconds")
    with _db() as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return f"[error: no task with id #{task_id}]"
        if row["status"] == "done":
            return f"[task #{task_id} was already done]"
        conn.execute(
            "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
            (now, task_id),
        )
    return f"[completed task #{task_id}]"


def delete_task(task_id):
    """Delete a task, by id."""
    task_id = _coerce_id(task_id)
    if task_id is None:
        return "[error: task id must be a number]"

    with _db() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        deleted = cur.rowcount
    if deleted == 0:
        return f"[error: no task with id #{task_id}]"
    return f"[deleted task #{task_id}]"


# --- Scheduler-facing helpers (used by the reminder loop, not the model) ---

def due_reminders(now=None):
    """Return a list of dicts for pending, not-yet-reminded tasks whose due
    moment has arrived. Date-only tasks use DEFAULT_REMINDER_TIME."""
    if now is None:
        now = datetime.now()

    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks "
            "WHERE status = 'pending' AND reminded = 0 AND due_date IS NOT NULL"
        ).fetchall()

    out = []
    for r in rows:
        time_part = r["due_time"] or DEFAULT_REMINDER_TIME
        try:
            due_at = datetime.strptime(f"{r['due_date']} {time_part}", "%Y-%m-%d %H:%M")
        except ValueError:
            continue  # malformed date/time; skip rather than crash the loop
        if due_at <= now:
            out.append({
                "id": r["id"],
                "description": r["description"],
                "due_date": r["due_date"],
                "due_time": r["due_time"],
            })
    return out


def mark_reminded(task_id):
    """Flag a task as already reminded, so it doesn't fire again."""
    task_id = _coerce_id(task_id)
    if task_id is None:
        return
    with _db() as conn:
        conn.execute("UPDATE tasks SET reminded = 1 WHERE id = ?", (task_id,))