import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "tasks.db"


@contextmanager
def _db():
    """Open a connection, commit on clean exit, always close.

    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create the tasks table if it doesn't exist. Safe to call repeatedly."""
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                description  TEXT NOT NULL,
                due_date     TEXT,                              -- 'YYYY-MM-DD' or NULL
                status       TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'done'
                created_at   TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )


# Ensure the table exists as soon as the module is imported.
init_db()


def _coerce_id(task_id):
    """Models sometimes send the id as a string. Return an int or None."""
    try:
        return int(task_id)
    except (TypeError, ValueError):
        return None


def add_task(description, due_date=None):
    """Add a task. due_date is an ISO 'YYYY-MM-DD' string, or None."""
    if not description or not description.strip():
        return "[error: a task needs a description]"

    if due_date:
        try:
            datetime.strptime(due_date, "%Y-%m-%d")
        except ValueError:
            return (f"[error: due_date must be 'YYYY-MM-DD', got {due_date!r}. "
                    f"Work out the actual date from today and pass it in that format.]")

    now = datetime.now().isoformat(timespec="seconds")
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (description, due_date, created_at) VALUES (?, ?, ?)",
            (description.strip(), due_date, now),
        )
        task_id = cur.lastrowid

    due_str = f" (due {due_date})" if due_date else ""
    return f"[added task #{task_id}: {description.strip()}{due_str}]"


def list_tasks(status="pending"):
    """List tasks. status is 'pending', 'done', or 'all'.

    Sorted so the most urgent surface first: by due date, with undated tasks
    last, then by id.
    """
    if status not in ("pending", "done", "all"):
        return f"[error: status must be 'pending', 'done', or 'all', got {status!r}]"

    query = "SELECT * FROM tasks"
    params = ()
    if status != "all":
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY due_date IS NULL, due_date, id"  

    with _db() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return "[no tasks yet]" if status == "all" else f"[no {status} tasks]"

    lines = []
    for r in rows:
        mark = "x" if r["status"] == "done" else " "
        due = f"  (due {r['due_date']})" if r["due_date"] else ""
        lines.append(f"[{mark}] #{r['id']}: {r['description']}{due}")
    return "\n".join(lines)


def complete_task(task_id):
    """Mark a task done, by id."""
    task_id = _coerce_id(task_id)
    if task_id is None:
        return "[error: task id must be a number]"

    now = datetime.now().isoformat(timespec="seconds")
    with _db() as conn:
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
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