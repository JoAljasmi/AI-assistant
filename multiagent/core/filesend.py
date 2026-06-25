"""Lets a tool hand a real file (an image, a game's .html, …) back to the user
through whatever transport is running — mirrors approval.py's per-thread
injection.

send_file accepts EITHER:
  - a local sandbox path (e.g. /workspace/snake.html), or
  - an http(s) URL, which it downloads itself and sends as an attachment.

The URL path means the model never has to curl-then-send (no shell command, no
approval prompt) — it just calls send_file(url) and real bytes get uploaded,
which is far more reliable than pasting a hotlinked URL into chat.

Path translation: the model writes files into the sandbox at /workspace/... , but
the Discord bot runs on the HOST, where that bind-mounted folder lives at
<repo>/workspace. So a /workspace path is mapped to its real host path first.
"""
import os
import shutil
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import requests

# /workspace in the sandbox is bind-mounted to <repo>/workspace on the host.
# This file is <repo>/multiagent/core/filesend.py, so three parents up is <repo>.
_HOST_WORKSPACE = Path(__file__).resolve().parent.parent.parent / "workspace"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-assistant/1.0)"}
_EXT_BY_TYPE = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/webp": ".webp", "image/bmp": ".bmp",
}

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


def _download(url):
    """Download a URL to a fresh temp file on the host. Returns the file path.
    The caller cleans up the temp directory afterward."""
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()

    # Pick a filename with a sensible extension so Discord previews it correctly.
    name = os.path.basename(urlparse(url).path) or "download"
    if "." not in name:
        ctype = resp.headers.get("content-type", "").split(";")[0].strip()
        name += _EXT_BY_TYPE.get(ctype, ".bin")

    tmpdir = tempfile.mkdtemp(prefix="send_")
    tmp = os.path.join(tmpdir, name)
    with open(tmp, "wb") as f:
        f.write(resp.content)
    return tmp


def send_file(path, caption=None):
    """Tool entry point: send a file to the user, given a /workspace path OR an
    http(s) URL. Returns a status string for the model."""
    if not path:
        return "[error: send_file needs a file path or an image URL]"

    cleanup_dir = None
    try:
        if path.startswith(("http://", "https://")):
            host_path = _download(path)            # fetch the bytes ourselves
            cleanup_dir = os.path.dirname(host_path)
        else:
            host_path = _to_host_path(path)
            if not host_path.is_file():
                return f"[error: no file at {path} (looked on host at {host_path})]"

        sender = getattr(_io_local, "send", None)
        if sender is None:
            # No transport here can attach files (e.g. terminal) — just report it.
            return f"[no file transport here — the file is at {host_path}]"

        sender(str(host_path), caption)
        return f"[sent {Path(host_path).name}]"
    except Exception as e:
        return f"[error sending file: {e}]"
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)