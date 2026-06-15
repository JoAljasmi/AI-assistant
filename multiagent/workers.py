"""Sub-agent spawner.

The main agent can call `spawn_workers(tasks, budget)` to run several
sub-agents in parallel. Each sub-agent:
  - is just another invocation of run_agent (same loop, same tools)
  - has its own isolated `messages` list (no shared context)
  - shares the parent's Budget (so the global cost cap still applies)
  - is identified by a worker_id, injected into its prompt and log lines
  - sets a thread-local worker_id so run_bash can label approval prompts

Returns a list of result dicts in the same order as the input tasks:
    [{"worker_id": "worker_1", "task": "...", "result": "...", "error": None}, ...]

If a worker raises, its result dict has error set and result is None.
This way one broken worker doesn't bring the whole batch down.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from agent import run_agent


# Thread-local identity. Each worker sets its worker_id at the start of its
# thread; tools (e.g. sandbox.run_bash) read this to label approval prompts.
# The main agent reads "main" because it never sets this.
_thread_local = threading.local()


def current_worker_id():
    """Returns the worker_id of the current thread, or 'main' if unset."""
    return getattr(_thread_local, "worker_id", "main")


def _run_one_worker(worker_id, task, budget):
    """Run a single sub-agent. Returns its final text or raises."""
    # Tag this thread with the worker_id so tools can identify the caller.
    _thread_local.worker_id = worker_id

    # Prefix the task so the worker knows its identity. Useful for log
    # output and prevents collisions when several workers touch files.
    framed_task = (
        f"You are {worker_id}, one of several parallel workers.\n"
        f"Your assigned task:\n{task}\n"
        f"Work only on this task. When done, return a short summary "
        f"of what you did."
    )

    # Per-worker deliver function: prefixes every line with the worker id
    # so concurrent output stays readable in the terminal.
    def deliver(text):
        for line in text.splitlines() or [""]:
            print(f"[{worker_id}] {line}")

    # run_agent now returns the final text (we modified it for exactly this).
    return run_agent(framed_task, deliver=deliver, budget=budget)


def spawn_workers(tasks, budget=None, max_parallel=4):
    """Run `tasks` in parallel sub-agents. Returns results in input order.

    tasks: list of task description strings.
    budget: shared Budget instance (or None).
    max_parallel: cap on concurrent workers.
    """
    if not tasks:
        return []

    results = [None] * len(tasks)

    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        # Submit all tasks. future_to_idx remembers which input index each
        # future belongs to, so we can place results back in order.
        future_to_idx = {}
        for idx, task in enumerate(tasks):
            worker_id = f"worker_{idx + 1}"
            future = pool.submit(_run_one_worker, worker_id, task, budget)
            future_to_idx[future] = (idx, worker_id, task)

        # Collect results as they finish (not in submission order).
        for future in as_completed(future_to_idx):
            idx, worker_id, task = future_to_idx[future]
            try:
                final_text = future.result()
                results[idx] = {
                    "worker_id": worker_id,
                    "task": task,
                    "result": final_text,
                    "error": None,
                }
                print(f"[{worker_id}] DONE")
            except Exception as e:
                # One worker crashing should not kill the whole batch.
                # Capture the error and let the main agent decide.
                results[idx] = {
                    "worker_id": worker_id,
                    "task": task,
                    "result": None,
                    "error": f"{type(e).__name__}: {e}",
                }
                print(f"[{worker_id}] FAILED: {results[idx]['error']}")

    return results


if __name__ == "__main__":
    # Smoke test: two trivial tasks in parallel.
    tasks = [
        "Run `echo hello from worker 1` and report the output.",
        "Run `echo hello from worker 2` and report the output.",
    ]
    results = spawn_workers(tasks)
    print("\n=== RESULTS ===")
    for r in results:
        print(r)