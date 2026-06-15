import json
from datetime import datetime
from pathlib import Path

SESSIONS_DIR = Path(__file__).parent / "sessions"

def start_session(user_goal=""):
    """Creating the sessions dir if needed, returning the session file path for this log"""
    SESSIONS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    
    # short slug from goal for findability
    slug = ""
    if user_goal:
        slug_chars = []
        for c in user_goal[:40]:
            if c.isalnum():
                slug_chars.append(c)
            elif slug_chars and slug_chars[-1] != "_":
                slug_chars.append("_")
        slug = "".join(slug_chars).strip("_").lower()

    filename = f"session_{timestamp}_{slug}.json" if slug else f"session_{timestamp}.json"
    return SESSIONS_DIR / filename

def save_session(path, messages):
    """automatically dump the messages to a list in the session file"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2, ensure_ascii=False)