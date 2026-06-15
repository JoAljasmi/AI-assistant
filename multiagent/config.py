"""Load config.json + .env and expose typed constants."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

CONFIG_PATH = Path(__file__).parent / "config.json"
load_dotenv(Path(__file__).parent.parent / ".env")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    _config = json.load(f)

# Provider
PROVIDER_URL = _config["provider"]["url"]
MODEL = _config["provider"]["model"]

# Agent
MAX_ITERATIONS = _config["agent"]["max_iterations"]
MAX_TOKENS_DEFAULT = _config["agent"]["max_tokens_default"]
MAX_REQUESTS_PER_MINUTE_DEFAULT = _config["agent"]["max_requests_per_minute_default"]

# Sandbox
CONTAINER_NAME = _config["sandbox"]["container_name"]
TIMEOUT_SECONDS = _config["sandbox"]["timeout_seconds"]
MAX_OUTPUT_CHARS = _config["sandbox"]["max_output_chars"]

# Tools
TOOLS = _config["tools"]

# System prompt lives in its own file so it stays readable and needs no JSON escaping.
PROMPT_PATH = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT_RAW = PROMPT_PATH.read_text(encoding="utf-8")
SYSTEM_PROMPT = SYSTEM_PROMPT_RAW.replace("{max_output_chars}", str(MAX_OUTPUT_CHARS))