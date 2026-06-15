"""ReAct loop for the test-writer agent.

By default each iteration prints a single short line ("[iter N] tool -> name(args)")
plus the tool result one-liner. Set VERBOSE=1 to also dump the raw model
message and the raw tool result — useful for debugging, noisy for demos.

The approval block in sandbox.run_bash always shows the FULL command no matter
what VERBOSE is set to, so the y/n decision is never blind.
"""
import json
import os

from provider import chat
from sandbox import run_bash, edit_file
from config import SYSTEM_PROMPT, MAX_ITERATIONS, TOOLS
from sessions import start_session, save_session
from context import maybe_compact


VERBOSE = os.environ.get("VERBOSE", "").lower() in ("1", "true", "yes")


def _summarize_tool_call(tc):
    """One-line preview of a tool call. The approval block shows the full
    command separately, so this just needs to give a glance-level summary."""
    name = tc["function"]["name"]
    args_json = tc["function"]["arguments"]
    try:
        args = json.loads(args_json)
    except json.JSONDecodeError:
        return f"{name}(<unparseable args>)"

    if name == "bash":
        cmd = args.get("command", "")
        # First line only, truncated. The approval block shows the rest.
        first_line = cmd.splitlines()[0] if cmd else ""
        if len(first_line) > 90:
            first_line = first_line[:87] + "..."
        return f"bash -> {first_line}"
    if name == "edit_file":
        path = args.get("path", "?")
        old_len = len(args.get("old_text", ""))
        new_len = len(args.get("new_text", ""))
        return f"edit_file -> {path}  (replace {old_len} -> {new_len} chars)"
    if name == "spawn_workers":
        tasks = args.get("tasks", [])
        return f"spawn_workers -> {len(tasks)} parallel tasks"
    return f"{name}(...)"


def _summarize_tool_result(name, result):
    """One-line preview of a tool result. Truncates noisy stdout."""
    if not isinstance(result, str):
        return f"{name} -> <non-string result>"
    # Compress whitespace, take first ~120 chars.
    one_line = " ".join(result.split())
    if len(one_line) > 120:
        one_line = one_line[:117] + "..."
    return f"{name} <- {one_line}"


def execute_tool_call(tool_call, budget=None):
    """Execute a tool call and return the output as a string."""
    name = tool_call["function"]["name"]
    args_json = tool_call["function"]["arguments"]

    try:
        args = json.loads(args_json)
    except json.JSONDecodeError as e:
        return f"[error: invalid JSON in tool arguments: {e}]"

    if name == "bash":
        command = args.get("command", "")
        if not command:
            return "[error: bash called with no command]"
        return run_bash(command)

    if name == "edit_file":
        path = args.get("path")
        old_text = args.get("old_text")
        new_text = args.get("new_text")
        if path is None or old_text is None or new_text is None:
            return "[error: edit_file requires path, old_text, and new_text]"
        return edit_file(path, old_text, new_text)

    if name == "spawn_workers":
        # Imported lazily to avoid a circular import: workers.py imports
        # run_agent from this module.
        from workers import spawn_workers
        tasks = args.get("tasks")
        if not tasks or not isinstance(tasks, list):
            return "[error: spawn_workers requires a non-empty list of task strings]"
        results = spawn_workers(tasks, budget=budget)
        # Return a compact JSON string so the main model can read each
        # worker's outcome and decide what to do next.
        return json.dumps(results, ensure_ascii=False, indent=2)

    return f"[error: unknown tool '{name}']"

def run_agent(user_goal, deliver, budget=None):
    """
    Run a ReAct turn. Calls deliver(text) once when the model produces a final
    user-facing reply. Does not call deliver for internal/harness states.
    Returns None.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_goal},
    ]
    session_path = start_session(user_goal)
    print(f"[agent] session log: {session_path}")

    for iteration in range(MAX_ITERATIONS):
        # Context engineering: if messages has grown past the threshold,
        # summarize the older middle so we don't blow the context window.
        # No-op when the conversation is still short.
        messages, did_compact, info = maybe_compact(messages, budget=budget)
        if did_compact:
            print(f"[compact] {info}")

        assistant_msg = chat(messages, tools=TOOLS, budget=budget)

        if VERBOSE:
            print(f"\n--- iteration {iteration} ---")
            print("MODEL:", assistant_msg)

        messages.append(assistant_msg)

        tool_calls = assistant_msg.get("tool_calls")
        if not tool_calls:
            save_session(session_path, messages)
            content = assistant_msg.get("content") or ""
            content = content.strip()
            if content:
                deliver(content)
            return content

        for tool_call in tool_calls:
            # One-liner showing what the model decided. The approval block
            # in run_bash will print the full command if it needs y/n.
            print(f"[iter {iteration}] {_summarize_tool_call(tool_call)}")

            result = execute_tool_call(tool_call, budget=budget)
            tool_name = tool_call['function']['name']

            if VERBOSE:
                print(f"TOOL RESULT ({tool_name}):", result)
            else:
                print(f"[iter {iteration}] {_summarize_tool_result(tool_name, result)}")

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": result,
            })

        save_session(session_path, messages)

    save_session(session_path, messages)
    print("[agent] max iterations reached, no reply delivered")

    messages.append({
        "role": "user",
        "content": (
            "You've hit the iteration limit. Stop calling tools. "
            "In ONE short chat message, summarize what you did, what works, "
            "and what (if anything) is unfinished. Reply with text only — no tool calls."
        ),
    })
    final = chat(messages, tools=None, budget=budget)
    content = (final.get("content") or "").strip()
    if content:
        deliver(content)
        return content
    else:
        fallback = "[agent] hit iteration limit without producing a summary."
        deliver(fallback)
        return fallback


if __name__ == "__main__":
    goal = input("What should the agent do? ")
    print("\n=== AGENT REPLY ===")
    run_agent(goal, deliver=print)