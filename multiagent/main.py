"""Entry point for the personal assistant.

Run with:  
    python -m multiagent            # terminal console (default)
    python -m multiagent terminal   # same thing, explicit
    python -m multiagent discord    # bring the Discord bot online

Three things run at once:
  - the MAIN thread just waits for shutdown,
  - a CONSOLE thread owns stdin and routes each line: an approval answer, a
    budget command, or (default) a message for the assistant,
  - an AGENT thread pulls messages off a queue and runs one conversation turn
    for each.

Why the agent runs on its OWN thread: stdin can only be read safely by one
thread (your console thread). If the agent ran on that same thread, then while
it was busy running a turn it couldn't read your y/n approval — and since the
turn is *blocked waiting* for that approval, you'd deadlock. Putting the turn
on a separate thread keeps the console thread free to feed it the answer.

(You could instead go single-threaded and have approvals read stdin directly,
but that means editing approval.py's synchronization. This way your approval
machinery stays untouched — the console thread still owns stdin exactly as it
did before; it just learned one new trick: unrecognized lines are chat.)
"""
import queue
import threading

from .core.agent import Conversation
from .core.budget import Budget
from .config import MAX_TOKENS_DEFAULT, MAX_REQUESTS_PER_MINUTE_DEFAULT
from .transports.console_control import run_console


def agent_loop(convo, chat_queue, stop_event):
    """Pull user messages off the queue and run one conversation turn each."""
    while not stop_event.is_set():
        message = chat_queue.get()      # blocks until a message (or sentinel) arrives
        if message is None:             # sentinel from the console thread = shut down
            return
        try:
            convo.run_turn(message, deliver=lambda text: print(f"\nassistant> {text}\n"))
        except RuntimeError as e:
            # Budget hard cap or rate limit tripped midturn. Report it and keep
            print(f"\n[main] turn halted: {e}")
            print(f"[main] budget: {convo.budget.snapshot()}")


def main():
    budget = Budget(
        max_tokens=MAX_TOKENS_DEFAULT,
        max_requests_per_minute=MAX_REQUESTS_PER_MINUTE_DEFAULT,
    )
    print(f"[main] budget: {budget.snapshot()}")

    convo = Conversation(budget=budget)
    chat_queue = queue.Queue()
    stop_event = threading.Event()

    #runs conversation turns off the queue.
    agent_thread = threading.Thread(
        target=agent_loop,
        args=(convo, chat_queue, stop_event),
        daemon=True,
    )
    agent_thread.start()

    # routes lines (approvals / commands / chat).
    console_thread = threading.Thread(
        target=run_console,
        args=(budget, stop_event, chat_queue),
        daemon=True,
    )
    console_thread.start()

    print("\n=== ASSISTANT READY ===")
    print("Type a message and press enter. 'help' for commands, 'quit' to exit.\n")

    try:
        # Main thread idles until something sets the stop event.
        while not stop_event.is_set():
            stop_event.wait(timeout=0.5)
    except KeyboardInterrupt:
        print("\n[main] interrupted")
    finally:
        stop_event.set()
        chat_queue.put(None)  # ensure the agent thread can wake up and exit
        print(f"[main] final budget: {budget.snapshot()}")


if __name__ == "__main__":
    main()