"""Single-owner stdin reader.

This thread is the ONLY thing that reads stdin during a run. That solves
two problems at once:

1. When parallel workers all need y/n approval, they'd otherwise race for
   stdin. Now they each put their command on `approval.pending` and wait;
   the user's reply on this thread gets routed back via `approval.responses`.

2. Budget commands (status / tokens N / rate N) are processed here too.

Routing rule: if there's an approval waiting, the next line is treated as
the approval answer. Otherwise it's a console command.
"""
from approval import is_pending, submit_response, toggle_auto_approve, is_auto_approve


APPROVAL_ANSWERS = {"y", "yes", "n", "no"}


def run_console(budget, stop_event):
    auto_state = "ON" if is_auto_approve() else "OFF"
    print(f"[console] type 'help' for commands; approval prompts auto-route (auto-approve: {auto_state})")
    while not stop_event.is_set():
        try:
            line = input().strip()
        except EOFError:
            return

        if not line:
            continue

        # If a worker is waiting for approval, route this line as the answer.
        if is_pending():
            submit_response(line)
            continue

        # No approval pending — treat as a console command.
        if line == "help":
            print("[console] commands: status | tokens N | rate N | auto | help")
        elif line == "status":
            auto_state = "ON" if is_auto_approve() else "OFF"
            print(f"[console] budget: {budget.snapshot()} | auto-approve: {auto_state}")
        elif line == "auto":
            new_state = toggle_auto_approve()
            if new_state:
                print("[console] auto-approve ON — approvals will be skipped (danger filter still active)")
            else:
                print("[console] auto-approve OFF — approvals will be requested again")
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
        elif line.lower() in APPROVAL_ANSWERS:
            # User typed y/n but no approval was pending — gentle nudge, don't error.
            print(f"[console] no approval pending, ignoring {line!r}")
        else:
            print(f"[console] unknown command: {line!r} (type 'help')")