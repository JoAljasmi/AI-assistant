"""Serializes approval prompts across all worker threads.

The Problem
-----------
With parallel workers, three threads might each call run_bash at almost the
same instant. If each one independently puts a request on a queue, prints
its prompt, and waits, the prints interleave and the user's `y` answer can
get routed to the wrong worker (or no worker at all).

The Fix
-------
A single global lock serializes the entire prompt-and-wait sequence. Only
one approval is "active" at a time. The others queue up and are handled
one after another — clearly labeled with their worker_id, in order.

Auto-Approve Mode
-----------------
When the user types `auto` in the console (or sets AUTO_APPROVE=1), the
prompt is skipped and the command auto-approves. The DANGER filter in
sandbox.py is unaffected — auto mode never bypasses hard-blocks.
"""
import os
import queue
import threading

# Held for the entire prompt-print + queue-put + queue-get cycle.
# Workers calling request_approval will queue up on this lock and be
# handled one at a time.
_approval_lock = threading.Lock()

# Single response queue — the console thread puts the user's reply here.
_responses = queue.Queue()

# An event the console thread checks to know if an approval is pending.
_pending = threading.Event()

# Auto-approve state. Toggled by the console `auto` command. Initial value
# can be set via env: AUTO_APPROVE=1 starts in auto mode.
_auto_approve = threading.Event()
if os.environ.get("AUTO_APPROVE", "").lower() in ("1", "true", "yes"):
    _auto_approve.set()

# Lock guarding _auto_approve toggle (so prints don't race the state read).
_auto_lock = threading.Lock()


def is_pending():
    """Used by console_control to decide whether the next stdin line is
    an approval answer (True) or a console command (False)."""
    return _pending.is_set()


def submit_response(text):
    """Called by console_control when it sees an approval-answer line."""
    _responses.put(text)


def is_auto_approve():
    """Read current auto-approve state."""
    return _auto_approve.is_set()


def toggle_auto_approve():
    """Flip auto-approve on/off. Returns the new state (True = on)."""
    with _auto_lock:
        if _auto_approve.is_set():
            _auto_approve.clear()
            return False
        else:
            _auto_approve.set()
            return True


def request_approval(worker_id, command, print_block, print_auto_line):
    """Block until the user types y or n for this command.

    Returns True if approved, False otherwise.

    print_block(worker_id, command):  print the full bordered approval block.
    print_auto_line(worker_id, command):  print the one-liner used in auto mode.

    Both are passed in (instead of being built here) so sandbox.py owns the
    look and feel and approval.py owns only the synchronization.
    """
    # Fast path: if auto-approve is on, skip the prompt entirely. We still
    # print a one-liner so the user can see what ran (no hidden state).
    if _auto_approve.is_set():
        # Light lock so the auto line doesn't interleave with another worker.
        with _approval_lock:
            print_auto_line(worker_id, command)
        return True

    # Normal path: serialize, prompt, wait for the user's answer.
    with _approval_lock:
        print_block(worker_id, command)
        _pending.set()
        try:
            answer = _responses.get()
        finally:
            _pending.clear()
        return answer.strip().lower() in ("y", "yes")