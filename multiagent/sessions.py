"""Per-conversation history persistence.

The agent keeps its message history in memory; that's gone the moment the
process stops. To survive restarts we save the whole history to one stable
file per conversation (keyed by id — e.g. a Discord channel id) after every
turn, and load it back when a Conversation for that id is created.
"""
import json
from pathlib import Path

SESSIONS_DIR = Path(__file__).parent / "sessions"


def session_path_for(conversation_id):
    """The stable file for one conversation. Same id -> same file across runs,
    so history persists instead of starting a fresh timestamped file each time."""
    SESSIONS_DIR.mkdir(exist_ok=True)
    safe = str(conversation_id).replace("/", "_").replace("\\", "_")
    return SESSIONS_DIR / f"session_{safe}.json"


def load_session(path):
    """Return the saved messages list, or None if there's nothing to resume."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable -> start fresh rather than crash the bot.
        return None


def save_session(path, messages):
    """Overwrite the conversation's file with the full current history."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2, ensure_ascii=False)