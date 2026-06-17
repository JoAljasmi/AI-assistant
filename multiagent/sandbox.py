"""Sandboxed shell + file-edit tools, with a three-tier safety model.

All commands run inside a Docker container (never the host directly), and every
command is classified before it runs:

  Tier 1 — DANGEROUS: hard-blocked, can never be approved (rm -rf /, fork bombs,
           piping the internet into a shell, formatting disks, ...).
  Tier 2 — SAFE: read-only commands (ls, cat, grep, ...) auto-run with no prompt.
  Tier 3 — EVERYTHING ELSE: writes, installs, deletes, and any arbitrary code
           execution require an explicit human y/n approval.

The guiding idea: reads are free, the truly catastrophic is impossible, and
anything that can change state goes through a person. `edit_file` is separately
constrained to paths under /workspace.
"""
import os
import subprocess
import re
from config import CONTAINER_NAME, TIMEOUT_SECONDS, MAX_OUTPUT_CHARS

# Tier 1 — patterns that are NEVER allowed, approval or not. These are the
# commands that could wreck the host or the container irrecoverably.
DANGEROUS_PATTERNS = [
    # rm -rf on roots, home, or absolute paths near root
    r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+(/|~|/\*|\.\./)",
    # rm of system directories
    r"\brm\s+-r?f?\s+/(etc|usr|var|bin|sbin|boot|lib|lib64|sys|proc|dev|home|root)\b",
    # Fork bomb
    r":\(\)\s*\{.*\|.*\&\s*\}\s*;\s*:",
    # dd writing to a device
    r"\bdd\s+.*of=/dev/",
    # Formatting filesystems
    r"\bmkfs(\.\w+)?\s+/dev/",
    # Broad chmod/chown on root or home
    r"\bchmod\s+-R\s+\d+\s+(/|~)",
    r"\bchown\s+-R\s+.*\s+(/|~)",
    # Piping a remote script straight into a shell
    r"\bcurl\s+[^\|]*\|\s*(bash|sh|zsh)\b",
    r"\bwget\s+[^\|]*\|\s*(bash|sh|zsh)\b",
    # Shutdown / reboot / halt
    r"\b(shutdown|reboot|halt|poweroff)\b",
    # Writing to system files
    r">\s*/etc/",
    r">\s*/dev/sd",
]

# Tier 2 — commands that are genuinely read-only and side-effect-free, so they
# auto-approve (no prompt for every `ls` or `cat`). Anything NOT here — pip,
# python, pytest, find, sed, awk — falls through to Tier 3 and needs approval,
# because each of those can execute arbitrary code or mutate state.
#
# A command counts as safe only if EVERY sub-command (split on | && ; ||) starts
# with one of these AND it contains no unsafe token below. One bad token taints
# the whole chain.
SAFE_COMMANDS = {
    # File inspection (read-only)
    "ls", "cat", "head", "tail", "wc", "file", "stat", "tree",
    # Text search (read-only — note: NOT sed or awk, which can write)
    "grep", "egrep", "fgrep", "rg",
    # Text transforms that only emit to stdout
    "sort", "uniq", "cut", "diff", "cmp",
    # System info (read-only)
    "pwd", "whoami", "id", "uname", "hostname", "date",
    # Shell built-ins / introspection
    "echo", "printf", "which", "type", "command",
}

# Tokens that cancel "safe" status anywhere in a command — they write, install,
# delete, or reach the network. Belt-and-suspenders: even a safe command like
# `cat` becomes a write via redirection (`cat foo > bar`), so a stray `>` taints
# the whole line.
UNSAFE_TOKEN_PATTERNS = [
    r">\s*\S",          # output redirect to a file
    r">>\s*\S",         # append redirect
    r"\btee\b",         # tee writes
    r"\brm\b",          # any rm (the catastrophic variants are hard-blocked above)
    r"\bmv\b",          # any mv
    r"\bcp\b",          # any cp
    r"\bmkdir\b",       # making dirs
    r"\brmdir\b",
    r"\btouch\b",       # creating files
    r"\bchmod\b",
    r"\bchown\b",
    r"\bln\b",
    r"\bdd\b",
    r"\bcurl\b", r"\bwget\b",   # network
    r"\bgit\s+(push|commit|reset|checkout|clean)\b",
]


def is_dangerous(command):
    """Tier 1 check. Returns (True, matched_pattern) if the command can never be
    allowed, else (False, None)."""
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return True, pattern
    return False, None


def is_safe(command):
    """Tier 2 check. True only when the command is purely read-only: it contains
    no unsafe token AND every sub-command's first word is in SAFE_COMMANDS."""
    # Any unsafe token taints the whole command (catches e.g. `cat foo > bar`).
    for pattern in UNSAFE_TOKEN_PATTERNS:
        if re.search(pattern, command):
            return False

    # Split on pipes / && / ; / || and check each sub-command's first token.
    subs = re.split(r"\|\||&&|;|\|", command)
    for sub in subs:
        sub = sub.strip()
        if not sub:
            continue
        first = sub.split()[0] if sub.split() else ""
        if first not in SAFE_COMMANDS:
            return False
    return True


def truncate_output(text):
    """Cap output at MAX_OUTPUT_CHARS, with a visible marker if it was cut, so a
    huge stdout can't blow up the model's context window."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    kept = text[:MAX_OUTPUT_CHARS]
    dropped = len(text) - MAX_OUTPUT_CHARS
    marker = (
        f"\n[output truncated: {dropped} characters dropped, "
        f"limit is {MAX_OUTPUT_CHARS}. "
        f"Use a more targeted command (head, tail, grep, or specific paths) "
        f"to see what you need.]"
    )
    return kept + marker


def run_bash(command):
    """Run a shell command in the sandbox container, after the three-tier check."""
    # Tier 1: hard-block dangerous commands. No approval can override this.
    dangerous, pattern = is_dangerous(command)
    if dangerous:
        msg = f"[blocked: command matches the dangerous pattern: {pattern}]"
        print(msg)
        return msg

    # Tier 2: auto-approve read-only commands (the default). The REQUIRE_APPROVAL
    # env var can force-off all prompts ("0"/"none") or force them on for every
    # command ("all"); otherwise only non-safe commands prompt.
    approval_mode = os.environ.get("REQUIRE_APPROVAL", "default").lower()
    if approval_mode in ("0", "false", "off", "none"):
        needs_prompt = False
    elif approval_mode == "all":
        needs_prompt = True
    else:
        needs_prompt = not is_safe(command)

    # Tier 3: prompt for explicit human approval on writes, installs, deletes,
    # and any arbitrary code execution.
    if needs_prompt:
        # Imported here (not at top) to avoid a circular import at load time.
        from approval import request_approval

        # Single-agent build: no parallel workers, so the caller is just the
        # assistant. (This label only appears in the approval prompt.)
        worker_id = "assistant"

        def print_block(wid, cmd):
            """Print the full, bordered approval block. Called under the approval
            lock, so it can't interleave with another prompt."""
            border = "=" * 70
            print()
            print(border)
            print(f" APPROVAL NEEDED  ({wid})")
            print(border)
            print(" classification: write / install / delete / arbitrary code")
            print(" command:")
            for cmd_line in cmd.splitlines() or [cmd]:
                print(f"   {cmd_line}")
            print(border)
            print(" y/n? > ", flush=True, end="")

        def print_auto_line(wid, cmd):
            """One-liner shown in auto-approve mode, so the user still sees what
            ran without being prompted."""
            first = cmd.splitlines()[0] if cmd else ""
            if len(first) > 80:
                first = first[:77] + "..."
            print(f"[{wid}] auto-approved: {first}")

        approved = request_approval(worker_id, command, print_block, print_auto_line)
        if not approved:
            return "[user denied command execution]"

    # Run the command inside the sandbox container.
    try:
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"[error: command timed out after {TIMEOUT_SECONDS} seconds]"

    # Return a readable, truncated summary of exit code + stdout + stderr.
    formatted = (
        f"[exit code: {result.returncode}]\n"
        f"[stdout]\n{result.stdout}\n"
        f"[stderr]\n{result.stderr}"
    )
    return truncate_output(formatted)


def edit_file(path, old_text, new_text):
    """Replace exactly one occurrence of old_text with new_text in a file.

    Constrained to /workspace, and deliberately fails if old_text is missing or
    ambiguous (appears more than once) — so an edit is always precise and never
    silently changes the wrong place.
    """
    if not path.startswith("/workspace/") and path != "/workspace":
        return f"[error: edit_file refused, path must be under /workspace, got: {path}]"

    # Read the current contents out of the container.
    read = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "cat", path],
        capture_output=True, text=True,
    )
    if read.returncode != 0:
        return f"[error: could not read {path}: {read.stderr.strip()}]"

    content = read.stdout

    # Require exactly one match, so the edit is unambiguous.
    count = content.count(old_text)
    if count == 0:
        return f"[error: old_text not found in {path}]"
    if count > 1:
        return (
            f"[error: old_text appears {count} times in {path},"
            f"must appear exactly once. make old_text appear more specific]"
        )
    new_content = content.replace(old_text, new_text)

    # Write back via stdin (tee) to avoid any shell-escaping issues.
    write = subprocess.run(
        ["docker", "exec", "-i", CONTAINER_NAME, "tee", path],
        input=new_content, text=True, capture_output=True,
    )
    if write.returncode != 0:
        return f"[error: could not write {path}: {write.stderr.strip()}]"

    return f"[edit_file ok: replaced 1 occurence in {path}]"