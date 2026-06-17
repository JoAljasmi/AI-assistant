import os
import subprocess
import re
from config import CONTAINER_NAME, TIMEOUT_SECONDS, MAX_OUTPUT_CHARS

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
    # Piping remote scripts into a shell
    r"\bcurl\s+[^\|]*\|\s*(bash|sh|zsh)\b",
    r"\bwget\s+[^\|]*\|\s*(bash|sh|zsh)\b",
    # Shutdown / reboot / halt
    r"\b(shutdown|reboot|halt|poweroff)\b",
    # Writing to system files
    r">\s*/etc/",
    r">\s*/dev/sd",
]

# Commands that are truly read-only and side-effect-free.
# These auto-approve so the user is not prompted for every `ls` or `cat`.
# Anything not on this list — including pip, python, pytest, find, sed, awk —
# requires explicit user approval. The cost is a few extra prompts per demo;
# the benefit is that ALL arbitrary-code-execution paths (python -c, pytest
# running user code, pip pulling packages, sed -i, find -delete, find -exec)
# go through a human decision.
#
# A command is "safe" if it consists ONLY of safe sub-commands chained by
# safe operators (| && ;). A single non-safe token taints the whole command.
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

# Patterns that cancel "safe" status anywhere in the command — these write,
# install, delete, or otherwise mutate state. Belt-and-suspenders: even if a
# SAFE_COMMAND is used (e.g. `cat`), a redirect (`cat foo > bar`) makes it
# a write, so we taint the whole command.
UNSAFE_TOKEN_PATTERNS = [
    r">\s*\S",          # output redirect to a file
    r">>\s*\S",         # append redirect
    r"\btee\b",         # tee writes
    r"\brm\b",          # any rm (dangerous variants are hard-blocked above)
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
    """Hard-block check. These can never be approved."""
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return True, pattern
    return False, None


def is_safe(command):
    """Auto-approve check. Read-only commands skip the approval prompt.

    Returns True only when the command (a) does not contain any unsafe token
    pattern AND (b) its primary command is in SAFE_COMMANDS.
    """
    # Any unsafe token taints the whole command (catches `cat foo > bar`).
    for pattern in UNSAFE_TOKEN_PATTERNS:
        if re.search(pattern, command):
            return False

    # Split on pipes/&&/;/|| and check every sub-command's first token.
    # If any sub-command starts with something not in SAFE_COMMANDS,
    # the whole chain is not safe.
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
    """Truncate text to MAX_OUTPUT_CHARS characters, with a visible marker if cut off"""
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
    dangerous, pattern = is_dangerous(command)
    if dangerous:
        msg = f"[blocked: command matches the dangerous pattern: {pattern}]"
        print(msg)
        return msg

    # Tier 2: auto-approve safe (read-only) commands unless approval is
    # forced off. This is the default. Set REQUIRE_APPROVAL=0 to skip all
    # prompts (useful for unattended runs); set REQUIRE_APPROVAL=all to
    # prompt on every command including read-only ones.
    approval_mode = os.environ.get("REQUIRE_APPROVAL", "default").lower()
    needs_prompt = True
    if approval_mode in ("0", "false", "off", "none"):
        needs_prompt = False
    elif approval_mode == "all":
        needs_prompt = True
    else:
        # Default: prompt only for non-safe commands.
        needs_prompt = not is_safe(command)

    # Tier 3: prompt for explicit approval on writes, installs, deletes, etc.
    if needs_prompt:
        # Lazy imports to avoid circular dependencies (workers imports agent,
        # agent imports sandbox).
        from approval import request_approval
    
        worker_id = "assistant"

        def print_block(wid, cmd):
            """Full bordered approval block for interactive mode. Called
            under the approval lock, so it can't interleave with another
            worker's prompt."""
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
            """One-liner used in auto-approve mode so the user can still see
            what ran without being prompted."""
            # First line only, truncated. Full command is in the session log.
            first = cmd.splitlines()[0] if cmd else ""
            if len(first) > 80:
                first = first[:77] + "..."
            print(f"[{wid}] auto-approved: {first}")

        approved = request_approval(worker_id, command, print_block, print_auto_line)
        if not approved:
            return "[user denied command execution]"

    #using docker to run the command
    try:
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"[error: command timed out after {TIMEOUT_SECONDS} seconds]"
        #returning readable outputs
    formatted = (
        f"[exit code: {result.returncode}]\n"
        f"[stdout]\n{result.stdout}\n"
        f"[stderr]\n{result.stderr}"
        )
    return truncate_output(formatted)


def edit_file(path, old_text, new_text):
    """Replace exactly one occurrence of old_text with new_text in the file at path.
    Path must be inside /workspace."""
    
    if not path.startswith("/workspace/") and path != "/workspace":
        return f"[error: edit_file refused, path must be under /workspace, got: {path}]"
    
    read = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "cat", path],
        capture_output=True, text=True,
    )
    if read.returncode != 0:
        return f"[error: could not read {path}: {read.stderr.strip()}]"

    content = read.stdout

    count = content.count(old_text)
    if count == 0:
        return f"[error: old_text not found in {path}]"
    if count > 1:
        return (
            f"[error: old_text appears {count} times in {path},"
            f"must appear exactly once. make old_text appear more specific]"
        )
    new_content = content.replace(old_text, new_text)

    # writing back with stdin to avoid shell escape
    write = subprocess.run(
        ["docker", "exec", "-i", CONTAINER_NAME, "tee", path],
        input=new_content, text=True, capture_output=True,
    )
    if write.returncode != 0:
        return f"[error: could not write {path}: {write.stderr.strip()}]"
    
    return f"[edit_file ok: replaced 1 occurence in {path}]"