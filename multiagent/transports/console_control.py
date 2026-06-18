"""Single-owner stdin reader.

This thread is the ONLY thing that reads stdin while the assistant runs. Each
line you type is routed one of three ways:

  1. If a tool is waiting on a y/n approval, the line is the approval answer.
  2. If it's a budget command (status / tokens N / rate N / auto / help / quit),
     it's handled here.
  3. Otherwise it's a message for the assistant, dropped on chat_queue for the
     agent thread to pick up and run.

Keeping stdin under a single owner is what lets your typing and the agent's
approval prompts coexist without racing for the same input.
"""
from ..core.approval import is_pending, submit_response, toggle_auto_approve, is_auto_approve


def run_console(budget, stop_event, chat_queue):
    auto_state = "ON" if is_auto_approve() else "OFF"
    print("[console] commands: status | tokens N | rate N | auto | help | quit")
    print(f"[console] anything else is sent to the assistant (auto-approve: {auto_state})")

    while not stop_event.is_set():
        try:
            line = input().strip()
        except EOFError:
            stop_event.set()
            chat_queue.put(None)  # wake the agent thread so it can exit cleanly
            return

        if not line:
            continue

        # 1. waiting for approval
        if is_pending():
            submit_response(line)
            continue

        # 2. Quit.
        if line.lower() in ("quit", "exit"):
            stop_event.set()
            chat_queue.put(None)
            return

        # 3. Budget / console commands.
        if line == "help":
            print("[console] commands: status | tokens N | rate N | auto | help | quit")
        elif line == "status":
            auto_state = "ON" if is_auto_approve() else "OFF"
            print(f"[console] budget: {budget.snapshot()} | auto-approve: {auto_state}")
        elif line == "auto":
            new_state = toggle_auto_approve()
            if new_state:
                print("[console] auto-approve ON — approvals skipped (danger filter still active)")
            else:
                print("[console] auto-approve OFF — approvals requested again")
        elif line.startswith("tokens "):
            try:
                value = int(line.split()[1])
                budget.set_max_tokens(value)
                print(f"[console] max_tokens set to {value}")
            except (ValueError, IndexError):
                print("[console] usage: tokens N")
        elif line.startswith("rate "):
            try:
                value = int(line.split()[1])
                budget.set_rate_limit(value)
                print(f"[console] max_requests_per_minute set to {value}")
            except (ValueError, IndexError):
                print("[console] usage: rate N")
        else:
            chat_queue.put(line)