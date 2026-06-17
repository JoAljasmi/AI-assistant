"""Conversational agent loop for the personal assistant.

A single user message becomes a "turn": the message is appended to the running
history, the model is called, any tools it requests are run and fed back, and
the loop repeats until the model returns a plain-text reply. The history lives
on the Conversation object, so it survives across turns and (via sessions.py)
across restarts.

Cost control lives in the loop. Each turn starts on the cheapest model in a
ModelLadder and climbs only when the model gets stuck — either it calls the
`escalate` tool itself, or it errors several times in a row and the loop bumps
it. The ladder resets every turn, so easy turns stay cheap. maybe_compact()
trims old history so long turns don't overflow the context window.

VERBOSE=1 also dumps raw model messages and tool results for debugging.
"""
import json
import os
from datetime import datetime

from provider import chat, ModelLadder
from sandbox import run_bash, edit_file
from config import SYSTEM_PROMPT, MAX_ITERATIONS, TOOLS, MODEL_LADDER
from sessions import session_path_for, load_session, save_session
from context import maybe_compact
from tasks import add_task, list_tasks, complete_task, delete_task


VERBOSE = os.environ.get("VERBOSE", "").lower() in ("1", "true", "yes")

# Climb to the next model after this many tool errors in a row within one turn.
ESCALATE_AFTER_ERRORS = 2


def _summarize_tool_call(tc):
    """One-line preview of a tool call for the log."""
    name = tc["function"]["name"]
    args_json = tc["function"]["arguments"]
    try:
        args = json.loads(args_json)
    except json.JSONDecodeError:
        return f"{name}(<unparseable args>)"

    if name == "bash":
        cmd = args.get("command", "")
        first_line = cmd.splitlines()[0] if cmd else ""
        if len(first_line) > 90:
            first_line = first_line[:87] + "..."
        return f"bash -> {first_line}"
    if name == "edit_file":
        path = args.get("path", "?")
        old_len = len(args.get("old_text", ""))
        new_len = len(args.get("new_text", ""))
        return f"edit_file -> {path}  (replace {old_len} -> {new_len} chars)"
    return f"{name}(...)"


def _summarize_tool_result(name, result):
    """One-line preview of a tool result; truncates noisy output."""
    if not isinstance(result, str):
        return f"{name} -> <non-string result>"
    one_line = " ".join(result.split())
    if len(one_line) > 120:
        one_line = one_line[:117] + "..."
    return f"{name} <- {one_line}"


def execute_tool_call(tool_call, budget=None, ladder=None):
    """Run a single tool call and return its result as a string.

    `ladder` is the conversation's ModelLadder, needed so the `escalate` tool
    can move the agent up a rung.
    """
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

    if name == "add_task":
        return add_task(args.get("description"), args.get("due_date"), args.get("due_time"))
    if name == "list_tasks":
        return list_tasks(args.get("status", "pending"))
    if name == "complete_task":
        return complete_task(args.get("task_id"))
    if name == "delete_task":
        return delete_task(args.get("task_id"))

    if name == "escalate":
        # The model judged this task too hard for the current model. Climb.
        if ladder is None:
            return "[error: escalation isn't available here]"
        stronger = ladder.escalate()
        if stronger:
            return f"[escalated to a stronger model ({stronger}). Re-approach the task.]"
        return "[already on the strongest model available — no higher rung.]"

    return f"[error: unknown tool '{name}']"


class Conversation:
    """Holds the persistent message history and model ladder for one chat.

    Create one per conversation (e.g. per Discord channel) and call run_turn()
    once per incoming message. History is restored from disk on creation, so the
    conversation survives restarts.
    """

    def __init__(self, budget=None, conversation_id="default"):
        today = datetime.now().strftime("%A, %Y-%m-%d")
        system = SYSTEM_PROMPT + f"\n\nToday's date: {today}."
        self.budget = budget
        self.ladder = ModelLadder(MODEL_LADDER)
        self.session_path = session_path_for(conversation_id)

        saved = load_session(self.session_path)
        if saved:
            self.messages = saved
            # The saved system prompt has an old date baked in — refresh it.
            self.messages[0] = {"role": "system", "content": system}
            print(f"[agent] resumed {self.session_path.name} ({len(self.messages)} msgs)")
        else:
            self.messages = [{"role": "system", "content": system}]
            print(f"[agent] new session {self.session_path.name}")

    def run_turn(self, user_message, deliver):
        """Handle one user message: run the tool loop until a plain-text reply,
        deliver it, and leave the history intact for next time."""
        self.messages.append({"role": "user", "content": user_message})
        self.ladder.reset()           # every turn starts on the cheapest model
        consecutive_errors = 0

        for iteration in range(MAX_ITERATIONS):
            # Trim old history if it's grown too long (no-op while short). Must
            # reassign onto self so the conversation keeps the compacted list.
            self.messages, did_compact, info = maybe_compact(self.messages, budget=self.budget)
            if did_compact:
                print(f"[compact] {info}")

            assistant_msg = chat(
                self.messages, tools=TOOLS, budget=self.budget,
                model=self.ladder.current(),
            )

            if VERBOSE:
                print(f"\n--- iteration {iteration} (model: {self.ladder.current()}) ---")
                print("MODEL:", assistant_msg)

            self.messages.append(assistant_msg)

            tool_calls = assistant_msg.get("tool_calls")
            if not tool_calls:
                # Plain-text reply -> this turn is done.
                save_session(self.session_path, self.messages)
                content = (assistant_msg.get("content") or "").strip()
                if content:
                    deliver(content)
                return content

            for tool_call in tool_calls:
                print(f"[iter {iteration}] {_summarize_tool_call(tool_call)}")

                result = execute_tool_call(tool_call, budget=self.budget, ladder=self.ladder)
                tool_name = tool_call["function"]["name"]

                if VERBOSE:
                    print(f"TOOL RESULT ({tool_name}):", result)
                else:
                    print(f"[iter {iteration}] {_summarize_tool_result(tool_name, result)}")

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result,
                })

                # Track tool errors for auto-escalation; any success clears it.
                if isinstance(result, str) and result.startswith("[error"):
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0

            # If the cheap model keeps fumbling, climb the ladder and nudge it.
            if consecutive_errors >= ESCALATE_AFTER_ERRORS:
                stronger = self.ladder.escalate()
                if stronger:
                    print(f"[escalate] {consecutive_errors} tool errors in a row -> {stronger}")
                    self.messages.append({
                        "role": "user",
                        "content": (
                            f"(System: you've been switched to a stronger model — {stronger}. "
                            f"Re-approach the step that kept failing.)"
                        ),
                    })
                    consecutive_errors = 0

            save_session(self.session_path, self.messages)

        # Per-turn iteration cap hit without a reply: force a short summary, but
        # keep the conversation alive for the next message.
        save_session(self.session_path, self.messages)
        print("[agent] turn hit iteration limit; forcing a summary reply")
        self.messages.append({
            "role": "user",
            "content": (
                "You've hit the per-turn tool limit. Stop calling tools. In ONE "
                "short message, tell me what you got done and what's left. "
                "Reply with text only — no tool calls."
            ),
        })
        final = chat(self.messages, tools=None, budget=self.budget, model=self.ladder.current())
        content = (final.get("content") or "").strip()
        self.messages.append(final)
        save_session(self.session_path, self.messages)
        if not content:
            content = "[hit the tool limit without finishing that one — try narrowing it.]"
        deliver(content)
        return content


if __name__ == "__main__":
    # Terminal REPL for testing without Discord.
    from budget import Budget
    from config import MAX_TOKENS_DEFAULT, MAX_REQUESTS_PER_MINUTE_DEFAULT

    budget = Budget(MAX_TOKENS_DEFAULT, MAX_REQUESTS_PER_MINUTE_DEFAULT)
    convo = Conversation(budget=budget)

    print("\nAssistant ready. Type a message, or 'quit' to exit.\n")
    while True:
        try:
            user_message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if not user_message:
            continue
        if user_message.lower() in ("quit", "exit"):
            print("bye")
            break
        convo.run_turn(user_message, deliver=lambda text: print(f"assistant> {text}\n"))