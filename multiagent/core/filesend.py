"""Lets a tool hand a real file (an image, a game's .html, …) back to the user
through whatever transport is running — mirrors approval.py's per-thread
injection.

A transport (e.g. the Discord bot) binds a sender on the worker thread before a
turn; the send_file tool picks it up. If nothing is bound (e.g. the terminal,
which can't attach files), send_file just reports where the file is.

Path translation
----------------
The model writes files into the sandbox at /workspace/... , but the Discord bot
runs on the HOST, where that same bind-mounted folder lives at <repo>/workspace.
So before sending, we map a /workspace path to its real host path.
"""
import os
import threading
from pathlib import Path

# /workspace in the sandbox is bind-mounted to <repo>/workspace on the host.
# This file is at <repo>/multiagent/core/filesend.py, so three parents up is <repo>.
_HOST_WORKSPACE = Path(__file__).resolve().parent.parent.parent / "workspace"

# Per-thread file sender, bound by the transport on the worker thread.
_io_local = threading.local()


def bind_file_sender(send_fn):
    """Bind the current thread's file sender.

    send_fn(host_path, caption): deliver the file at host_path to the user, with
    an optional caption. Called by a transport on the worker thread before a turn.
    """
    _io_local.send = send_fn


def _to_host_path(path):
    """Map a sandbox path the model used (/workspace/...) to the real host path."""
    if path == "/workspace":
        return _HOST_WORKSPACE
    if path.startswith("/workspace/"):
        return _HOST_WORKSPACE / path[len("/workspace/"):]
    return Path(path)


def send_file(path, caption=None):
    """Tool entry point: send a file from disk to the user. Returns a status
    string for the model."""
    if not path:
        return "[error: send_file needs a path]"

    host_path = _to_host_path(path)
    if not host_path.is_file():
        return f"[error: no file at {path} (looked on host at {host_path})]"

    sender = getattr(_io_local, "send", None)
    if sender is None:
        # No transport here can attach files (e.g. terminal) — just report it.
        return f"[no file transport here — the file is at {host_path}]"

    try:
        sender(str(host_path), caption)
    except Exception as e:
        return f"[error sending file: {e}]"
    return f"[sent {host_path.name}]"
