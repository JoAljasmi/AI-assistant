"""Scans outgoing chat messages for likely secret leaks.
Run unconditionally on every post; refuses messages that look like they
contain credentials, .env contents, or sensitive file paths."""

import os
import re

# Patterns for things that look like real secrets.
SECRET_PATTERNS = [
    # API key shapes
    (r"sk-or-v1-[a-zA-Z0-9]{20,}", "OpenRouter key shape"),
    (r"sk-ant-[a-zA-Z0-9_-]{20,}", "Anthropic key shape"),
    (r"sk-proj-[a-zA-Z0-9_-]{20,}", "OpenAI project key shape"),
    (r"sk-[a-zA-Z0-9]{40,}", "Long sk- key shape"),
    # .env-style KEY=value with sensitive names
    (r"(?i)\b(API[_-]?KEY|SECRET|TOKEN|PASSWORD|PRIVATE[_-]?KEY)\s*=\s*\S{10,}", "env-style assignment"),
    # JSON-shaped credentials
    (r'(?i)"(api[_-]?key|password|secret|token)"\s*:\s*"[^"]{10,}"', "JSON credential field"),
    # SSH private key markers
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key block"),
    # File paths to typical secret stores
    (r"(?i)(^|\s|/)(\.env|id_rsa|credentials\.json|\.aws/credentials)\b", "sensitive file reference"),
]


def scan_for_secrets(text):
    """Return (is_safe, reason). If is_safe is False, reason explains the match."""
    if not text:
        return True, ""

    # Check patterns
    for pattern, label in SECRET_PATTERNS:
        match = re.search(pattern, text)
        if match:
            snippet = match.group(0)[:30]
            return False, f"{label} (matched: {snippet!r})"

    # Check for literal hub password if it's in env
    hub_password = os.environ.get("HUB_PASSWORD", "")
    if hub_password and len(hub_password) >= 6 and hub_password in text:
        return False, "literal hub password"

    return True, ""