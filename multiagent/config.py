"""Central configuration loader.

Reads config.json (+ .env for secrets) once at import and exposes plain typed
constants — MODEL, MAX_ITERATIONS, TOOLS, and so on. Every other module imports
these names instead of reaching into a raw JSON blob, so there's a single place
to see and change how the app is wired.
"""
import json
from pathlib import Path

from dotenv import load_dotenv

CONFIG_PATH = Path(__file__).parent / "config.json"

# Secrets (API keys) live in .env one directory up and load into the environment.
load_dotenv(Path(__file__).parent.parent / ".env")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    _config = json.load(f)

# --- Provider: which endpoint and model to call ---
PROVIDER_URL = _config["provider"]["url"]
MODEL = _config["provider"]["model"]
MODEL_LADDER = _config["provider"].get("model_ladder", [MODEL])

# --- Agent loop limits ---
MAX_ITERATIONS = _config["agent"]["max_iterations"]
MAX_TOKENS_DEFAULT = _config["agent"]["max_tokens_default"]
MAX_REQUESTS_PER_MINUTE_DEFAULT = _config["agent"]["max_requests_per_minute_default"]

# --- Sandbox (Docker) settings ---
CONTAINER_NAME = _config["sandbox"]["container_name"]
TIMEOUT_SECONDS = _config["sandbox"]["timeout_seconds"]
MAX_OUTPUT_CHARS = _config["sandbox"]["max_output_chars"]

# --- Tool schemas the model is allowed to call ---
TOOLS = _config["tools"]

# --- System prompt ---
# Kept in its own file (system_prompt.md) rather than inside config.json, so it
# stays readable and editable without JSON escaping. Any {max_output_chars}
# placeholder in the prompt is filled in from the sandbox setting above.
PROMPT_PATH = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT_RAW = PROMPT_PATH.read_text(encoding="utf-8")
SYSTEM_PROMPT = SYSTEM_PROMPT_RAW.replace("{max_output_chars}", str(MAX_OUTPUT_CHARS))