import json
import os

from provider import chat
from sandbox import run_bash, edit_file
from config import SYSTEM_PROMPT, MAX_ITERATIONS, TOOLS
from sessions import session_path_for, load_session, save_session
from context import maybe_compact
from tasks import add_task, list_tasks, complete_task, delete_task
from datetime import datetime


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
    """One-line preview of a tool result. Truncates noisy stdout."""
    if not isinstance(result, str):
        return f"{name} -> <non-string result>"
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

    if name == "add_task":
        return add_task(args.get("description"), args.get("due_date"), args.get("due_time"))
    if name == "list_tasks":
        return list_tasks(args.get("status", "pending"))
    if name == "complete_task":
        return complete_task(args.get("task_id"))
    if name == "delete_task":
        return delete_task(args.get("task_id"))

    return f"[error: unknown tool '{name}']"


class Conversation:
    """Holds the persistent message history for one ongoing conversation.

    Create one of these and call run_turn() once per incoming user message.
    The history lives on self.messages and survives across turns that's the
    whole point. Later, with Discord, you'll keep one Conversation per channel
    (a dict of {channel_id: Conversation}) so each chat remembers its own
    thread.
    """

    def __init__(self, budget=None, conversation_id="default"):
        today = datetime.now().strftime("%A, %Y-%m-%d")
        system = SYSTEM_PROMPT + f"\n\nToday's date: {today}."
        # Seed with just the system prompt.
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.budget = budget
        # ONE session log for the whole conversation
        self.session_path = session_path_for(conversation_id)

        saved = load_session(self.session_path)
        if saved:
            self.messages = saved
            # Refresh the system prompt.
            self.messages[0] = {"role": "system", "content": system}
            print(f"[agent] resumed {self.session_path.name} ({len(self.messages)} msgs)")
        else:
            self.messages = [{"role": "system", "content": system}]
            print(f"[agent] new session {self.session_path.name}")


    def run_turn(self, user_message, deliver):
        """Handle one user message.

        Appends the message, runs the tool loop until the model returns a
        final text reply (no tool calls), delivers that reply via deliver(),
        and returns it. The history is left intact for the next turn.
        """
        self.messages.append({"role": "user", "content": user_message})

        for iteration in range(MAX_ITERATIONS):
            # Compact the messages if they have grown past the threshold
            self.messages, did_compact, info = maybe_compact(
                self.messages, budget=self.budget
            )
            if did_compact:
                print(f"[compact] {info}")

            assistant_msg = chat(self.messages, tools=TOOLS, budget=self.budget)

            if VERBOSE:
                print(f"\n--- iteration {iteration} ---")
                print("MODEL:", assistant_msg)

            self.messages.append(assistant_msg)

            tool_calls = assistant_msg.get("tool_calls")
            if not tool_calls:
                # No tools requested -> this is the user-facing reply. Turn done.
                save_session(self.session_path, self.messages)
                content = (assistant_msg.get("content") or "").strip()
                if content:
                    deliver(content)
                return content

            for tool_call in tool_calls:
                print(f"[iter {iteration}] {_summarize_tool_call(tool_call)}")

                result = execute_tool_call(tool_call, budget=self.budget)
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

            save_session(self.session_path, self.messages)

        # Hit the per-TURN iteration cap without a text reply. Force one short
        # reply for this turn, but keep the conversation alive for the next one.
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
        final = chat(self.messages, tools=None, budget=self.budget)
        content = (final.get("content") or "").strip()
        self.messages.append(final)
        save_session(self.session_path, self.messages)
        if not content:
            content = "[hit the tool limit without finishing that one — try narrowing it.]"
        deliver(content)
        return content


if __name__ == "__main__":
    # Read the system prompt from the file
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