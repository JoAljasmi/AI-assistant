"""Background reminder loop.

Wakes every `interval` seconds, asks tasks.due_reminders() what's due, and for
each one sends a message and marks it reminded — so each reminder fires exactly
once.

Transport-agnostic on purpose: it's handed send(channel_id, text) and
get_channel_id() by whoever starts it (the Discord bot). It never imports
discord, so it can be tested on its own.
"""
from ..skills import tasks


def run_scheduler(send, get_channel_id, stop_event, interval=60):
    """Loop until stop_event is set.

    send(channel_id, text): deliver a reminder to the user.
    get_channel_id() -> id | None: where to send (None = nowhere known yet).
    stop_event: threading.Event; setting it ends the loop promptly.
    interval: seconds between checks.
    """
    # stop_event.wait(interval) sleeps for `interval` and returns True early if
    # the event is set, so shutdown doesn't wait out a full interval.
    while not stop_event.wait(interval):
        channel_id = get_channel_id()
        if channel_id is None:
            # No channel known yet (e.g. just after a restart, before the user
            # has said anything). Skip without marking — the reminder waits.
            continue

        for task in tasks.due_reminders():
            when = f"at {task['due_time']}" if task["due_time"] else "today"
            text = f"Reminder — {task['description']} ({when})."
            try:
                send(channel_id, text)
            except Exception as e:
                # Leave it un-marked so it retries next cycle.
                print(f"[scheduler] couldn't send reminder #{task['id']}: {e}")
                continue
            tasks.mark_reminded(task["id"])
            print(f"[scheduler] reminded #{task['id']}: {task['description']}")