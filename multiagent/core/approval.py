"""Serializes approval prompts and routes the user's y/n answer back.

Terminal mode (default): the prompt is printed and the answer comes from the
console thread reading stdin (via submit_response). Unchanged from before.

Other transports (e.g. the Discord bot) can override HOW the prompt is shown
and HOW the answer is obtained, per-thread, by calling bind_approval_io()
on the worker thread before running a turn. If nothing is bound, we fall back
to the console path below — so the terminal behaves exactly as it always has.

Auto-Approve Mode
-----------------
When the user types `auto` (or sets AUTO_APPROVE=1), the prompt is skipped and
the command auto-approves. The DANGER filter in sandbox.py is unaffected — auto
mode never bypasses hard-blocks. (Tip: keep auto OFF on Discord so writes stay
visible and gated.)
"""
import os
import queue
import threading

# Held for the entire prompt-print + queue cycle in the CONSOLE path.
_approval_lock = threading.Lock()

# Console response queue — the console thread puts the user's stdin reply here.
_responses = queue.Queue()

# Set while a console-path approval is waiting, so console_control knows the
# next stdin line is an approval answer rather than a command.
_pending = threading.Event()

# Auto-approve state.
_auto_approve = threading.Event()
if os.environ.get("AUTO_APPROVE", "").lower() in ("1", "true", "yes"):
    _auto_approve.set()

_auto_lock = threading.Lock()

# Per-thread approval I/O override. A transport binds these on the worker
# thread that will run the turn; request_approval picks them up.
_io_local = threading.local()


def bind_approval_io(prompt_fn, wait_fn):
    """Bind the current thread's approval I/O.

    prompt_fn(worker_id, command): show the approval request to the user.
    wait_fn() -> str: block until the user's answer arrives, return it.

    Called by a transport (e.g. the Discord bot) on the worker thread before
    running a turn. If never called on this thread, request_approval uses the
    console path instead.
    """
    _io_local.prompt = prompt_fn
    _io_local.wait = wait_fn


def is_pending():
    """Console path: is a stdin approval answer expected next?"""
    return _pending.is_set()


def submit_response(text):
    """Console path: console_control calls this with an approval-answer line."""
    _responses.put(text)


def is_auto_approve():
    return _auto_approve.is_set()


def toggle_auto_approve():
    with _auto_lock:
        if _auto_approve.is_set():
            _auto_approve.clear()
            return False
        else:
            _auto_approve.set()
            return True


def request_approval(worker_id, command, print_block, print_auto_line):
    """Block until the user approves or denies this command. Returns bool.

    print_block / print_auto_line are the console-path display functions passed
    by sandbox.py. They're only used when no per-thread I/O is bound.
    """
    # Fast path: auto-approve skips the prompt (danger filter already applied).
    if _auto_approve.is_set():
        with _approval_lock:
            print_auto_line(worker_id, command)
        return True

    # If a transport bound its own I/O on this thread (e.g. Discord), use it.
    prompt = getattr(_io_local, "prompt", None)
    wait = getattr(_io_local, "wait", None)
    if prompt is not None and wait is not None:
        prompt(worker_id, command)
        answer = wait()
        return answer.strip().lower() in ("y", "yes")

    # Console path (terminal): print the block, then wait on the stdin queue.
    with _approval_lock:
        print_block(worker_id, command)
        _pending.set()
        try:
            answer = _responses.get()
        finally:
            _pending.clear()
        return answer.strip().lower() in ("y", "yes")