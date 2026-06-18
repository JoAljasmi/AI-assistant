"""Tiny persistent settings store for the bot — a single JSON file so runtime
toggles (like mention-only mode) survive restarts.

Reads happen on the Discord event loop (on_message) and writes happen on the
agent worker thread, so set_setting writes atomically (temp file + os.replace)
to avoid a torn read.
"""
import json
import os
from pathlib import Path

from ..paths import SETTINGS_FILE as _PATH


def _load():
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_setting(key, default=None):
    return _load().get(key, default)


def set_setting(key, value):
    data = _load()
    data[key] = value
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _PATH)          # atomic on the same filesystem
    return value


def _as_bool(v):
    """Coerce the tool argument to a bool — models sometimes send "true"/"false"
    as strings rather than a real JSON boolean."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


def set_mention_mode(enabled):
    """Tool: turn mention-only mode on/off for server channels."""
    enabled = _as_bool(enabled)
    set_setting("mention_only", enabled)
    if enabled:
        return "[mention-only mode ON — in servers I'll only reply when you @ me]"
    return "[mention-only mode OFF — I'll reply to every message]"