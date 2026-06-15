"""Entry point for the test-writer agent.

Run with:
    python main.py

Optional env flags:
    REQUIRE_APPROVAL=1   prompt y/n before every bash command (slows demos but
                         shows the safety gate; off by default because it
                         doesn't compose with parallel workers).
"""
import sys
import threading

from agent import run_agent
from budget import Budget
from config import MAX_TOKENS_DEFAULT, MAX_REQUESTS_PER_MINUTE_DEFAULT
from console_control import run_console


def main():
    # Global budget shared across the main agent and any sub-agents it spawns.
    budget = Budget(
        max_tokens=MAX_TOKENS_DEFAULT,
        max_requests_per_minute=MAX_REQUESTS_PER_MINUTE_DEFAULT,
    )
    print(f"[main] budget: {budget.snapshot()}")

    # Get the user's task BEFORE starting the console thread — otherwise the
    # console thread would steal the input() call.
    try:
        goal = input("\nWhat should the agent do?\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n[main] no task given, exiting")
        return
    if not goal:
        print("[main] empty task, exiting")
        return

    # Now start the console thread for live budget tuning during the run.
    stop_event = threading.Event()
    console_thread = threading.Thread(
        target=run_console,
        args=(budget, stop_event),
        daemon=True,
    )
    console_thread.start()

    print("\n=== AGENT RUNNING ===\n")

    try:
        run_agent(goal, deliver=print, budget=budget)
    except RuntimeError as e:
        # The budget raises RuntimeError when the hard cap or rate limit hits.
        # That's the intended VG.3 stop behavior — show it cleanly.
        print(f"\n[main] agent halted: {e}")
        print(f"[main] final budget: {budget.snapshot()}")
    except KeyboardInterrupt:
        print("\n[main] interrupted by user")
    else:
        print(f"\n[main] final budget: {budget.snapshot()}")
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()