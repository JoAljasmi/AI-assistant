# TestWriter Agent

A specialized coding agent that reads Python source code and produces a passing pytest suite.
Built as a VG submission for Applicerad AI (TH25), demonstrating a small but complete
"Claude Code / Codex CLI"-style system: a model running in a tool-use loop with
parallel sub-agents, context engineering, cost control, and tiered safety.

---

## What it does

You point it at a Python module or a small project. It explores the code, plans
test coverage, optionally fans out parallel sub-agents (one per file), writes
tests with `pytest` conventions, and iterates until the suite passes — all from
inside a sandboxed Docker container.

A typical session: `cat` and `grep` to understand the code, `spawn_workers` to
launch 3 workers in parallel, each `touch` + `edit_file` to create a test file,
then `pytest` to verify, then a final summary.

---

## Quickstart

You need: Python 3.10+, Docker, and an OpenRouter API key.

```bash
# 1. Clone and install dependencies
git clone <this repo>
cd <project>
pip install -r requirements.txt

# 2. Set up secrets
cp .env.example ../.env       # note: parent directory, not project root
# edit ../.env and paste in your OPENROUTER_API_KEY

# 3. Start the sandbox container (one-time setup)
docker run -d --name agent-sandbox -v "$(pwd)/workspace:/workspace" \
    python:3.11-slim sleep infinity
docker exec agent-sandbox pip install pytest

# 4. Run the agent
python main.py
```

When prompted, type a coding task. Example:

> Write pytest tests for the three independent files in /workspace/src/ (calculator.py, strings.py, lists.py). Use spawn_workers to test them in parallel.

The agent will explore, fan out to 3 parallel workers, write the tests, and
verify them. Approval prompts appear for every write/install/delete — type
`y` or `n` per command, or type `auto` to disable prompts for the rest of the run.

---

## Architecture in one paragraph

`main.py` builds a `Budget`, starts the `console_control` thread (for live
budget tuning), prompts for a task, and calls `run_agent`. `run_agent` (in
`agent.py`) is the core ReAct loop: it calls `provider.chat()`, checks the
model's reply for `tool_calls`, dispatches each to `sandbox.run_bash` or
`sandbox.edit_file` or `workers.spawn_workers`, appends results to the message
history, and loops until the model returns final text. Sub-agents are just
parallel calls to `run_agent` with isolated histories and a shared `Budget`.
`context.maybe_compact` summarizes older history when the loop runs long.
Approval is serialized across workers by a global lock in `approval.py`, so
multiple workers asking at once queue up cleanly instead of racing.

---

## File layout

| File | Purpose |
|---|---|
| `main.py` | Entry point: budget, console thread, agent invocation. |
| `agent.py` | The ReAct loop. Decides tool-call vs. yield per iteration. |
| `workers.py` | Parallel sub-agent spawner. Each worker = isolated `run_agent`. |
| `sandbox.py` | `run_bash` and `edit_file`. Danger filter + 3-tier approval. |
| `budget.py` | Token/rate caps with soft warning + hard stop. |
| `provider.py` | OpenRouter HTTP client. |
| `context.py` | History compaction so long sessions don't blow the window. |
| `approval.py` | Serialized cross-thread approval handshake. |
| `console_control.py` | Background thread for live budget commands. |
| `sessions.py` | JSON session logs (full transcripts for debugging). |
| `config.py` / `config.json` | All settings (tools, prompts, caps, container). |

---

## Configuration

All non-secret configuration lives in `config.json`. Secrets live in `../.env`.

### `config.json`

| Section | Key | Default | What it does |
|---|---|---|---|
| `provider` | `url` | OpenRouter URL | Where to send chat completions. |
| `provider` | `model` | (configurable) | Which model to use. |
| `agent` | `max_iterations` | 30 | Max loop turns before forced summary. |
| `agent` | `max_tokens_default` | 500000 | Hard token cap for the session. |
| `agent` | `max_requests_per_minute_default` | 60 | Hard rate cap. |
| `sandbox` | `container_name` | agent-sandbox | Docker container name for `docker exec`. |
| `sandbox` | `timeout_seconds` | 60 | Per-command timeout. |
| `sandbox` | `max_output_chars` | 4000 | Tool-output truncation limit (VG.2). |
| `tools` | (list) | bash, edit_file, spawn_workers | Tool schemas exposed to the model. |
| `system_prompt` | (list of lines) | TestWriter prompt | Joined with newlines at load time. |

### Environment variables

| Variable | Where | What |
|---|---|---|
| `OPENROUTER_API_KEY` | required | Provider auth. Never goes in config files. |
| `AUTO_APPROVE` | optional | `1` = skip approval prompts for the session. |
| `VERBOSE` | optional | `1` = print full MODEL dumps + raw tool results. |

---

## Live runtime commands

While the agent runs, type at the terminal:

| Command | Effect |
|---|---|
| `status` | Print current budget snapshot. |
| `tokens N` | Raise/lower the token cap to N (re-arms soft warning). |
| `rate N` | Raise/lower the per-minute request cap. |
| `auto` | Toggle session-wide auto-approve (danger filter still active). |
| `help` | List commands. |
| `y` / `yes` / `n` / `no` | Routed to the currently-pending approval prompt. |

---

## How VG requirements map to code

| VG req | Mechanism | Where to look |
|---|---|---|
| VG.1 sub-agents | `spawn_workers` tool calls parallel `run_agent` via `ThreadPoolExecutor` | `workers.py` |
| VG.2 context engineering | Tool-output truncation + history compaction | `sandbox.truncate_output`, `context.maybe_compact` |
| VG.3 cost monitoring | Live `status`, soft warning at 80%, hard cap raises `RuntimeError` | `budget.py`, `main.py` |
| VG.4 safety | Danger filter + 3-tier approval (safe / prompt / blocked) inside a Docker sandbox | `sandbox.py`, `approval.py` |
| VG.5 bash | `run_bash` via `docker exec` | `sandbox.py` |
| VG.6 partial file editing | `edit_file` does single-occurrence str-replace under `/workspace` | `sandbox.py` |
| VG.7 packaging | This README + `requirements.txt` + `Dockerfile` (optional) | repo root |
| VG.8 config + secrets | `config.json` for settings, `.env` for secrets, never mixed | `config.py` |
| VG.9 autonomy | Loop yields on no `tool_calls`, continues otherwise — model decides | `agent.py` line `if not tool_calls:` |

---

## Demo recipe (red → green)

This is the demo flow used in the VG presentation.

```bash
# Prepare three small independent files in the sandbox
docker exec agent-sandbox mkdir -p /workspace/src /workspace/tests
docker cp examples/calculator.py agent-sandbox:/workspace/src/
docker cp examples/strings.py    agent-sandbox:/workspace/src/
docker cp examples/lists.py      agent-sandbox:/workspace/src/

# Run the agent
python main.py
```

At the prompt:

> Write pytest tests for the three independent files in /workspace/src/ (calculator.py, strings.py, lists.py). Use spawn_workers to test them in parallel.

What you'll see:

1. Main agent reads all three files with `cat` (auto-approved, no prompt).
2. Main agent calls `spawn_workers` with 3 task descriptions.
3. Three workers run in parallel. Each prompts for `touch` and `edit_file`.
4. Main agent runs `pytest` to verify. Suite passes (red → green).
5. Final summary printed.

To demo the hard cap mid-run, type `tokens 5000` after a few iterations — the
soft warning fires immediately, and within a few more calls the hard cap
trips and the run stops cleanly with `[main] agent halted: token cap reached`.

---

## Troubleshooting

**`docker: command not found`** — Install Docker Desktop (Windows/Mac) or
docker.io (Linux). The agent runs commands via `docker exec` and has no
fallback to native execution by design.

**`pytest: command not found` inside the sandbox** — Run
`docker exec agent-sandbox pip install pytest`. Pytest is not in the base
python image. (For a more reproducible setup, build a custom sandbox image —
see "Building a custom sandbox image" below.)

**Approval prompts overlap on screen with multiple workers** — They shouldn't,
because `approval.py` serializes them with a global lock. If you see this,
check that you have the latest `approval.py` and `sandbox.py`.

**`y` answer gets "unknown command" response** — Means the approval lock isn't
set when you typed. Either the worker hasn't asked yet, or it's already
been answered. Wait for the next bordered `APPROVAL NEEDED` block before typing.

**Agent burns a lot of tokens reading the same file repeatedly** — Means the
4000-char truncation is firing and the model is re-fetching instead of
narrowing. The system prompt nudges it toward `grep`/`head` for narrowing,
but on some models it takes a few tries. Lowering `max_iterations` or raising
`max_output_chars` are the levers.

**`[budget] BUDGET WARNING` fires too early** — Adjust `warn_threshold` in
`Budget()` construction in `main.py`. Default is `0.8` (80% of cap).

---

## Building a custom sandbox image

The default sandbox is a vanilla `python:3.11-slim` container with pytest
installed at runtime. For a reproducible demo, bake pytest into a custom
image:

```dockerfile
# sandbox.Dockerfile
FROM python:3.11-slim
RUN pip install --no-cache-dir pytest
WORKDIR /workspace
CMD ["sleep", "infinity"]
```

```bash
docker build -f sandbox.Dockerfile -t testwriter-sandbox .
docker run -d --name agent-sandbox -v "$(pwd)/workspace:/workspace" testwriter-sandbox
```

---

## Running the agent itself in Docker (optional)

The agent has been developed and tested on a host Python install. A Dockerfile
is provided as a packaging convenience, but the agent calls `docker exec` to
reach its sandbox, which means containerizing the agent requires either
mounting the host's Docker socket or using docker-in-docker. The simpler
deployment pattern is "agent on host, sandbox in Docker."

```bash
# Build
docker build -t testwriter-agent .

# Run (mounts the host Docker socket so the agent can exec into the sandbox)
docker run --rm -it \
    -v /var/run/docker.sock:/var/run/docker.sock \
    --env-file ../.env \
    testwriter-agent
```

This works on Linux/Mac but is finicky on Windows.

---

## What this is *not*

- Not a general-purpose Claude Code clone. It's specialized for writing tests.
- Not an editor integration. CLI only.
- Not a benchmark tool. Token cost is monitored, not optimized — the goal is
  correct behavior and clear architecture, not minimum tokens per test.
- Not unattended by default. Approvals are gated; the user is in the loop
  unless they explicitly opt into `AUTO_APPROVE=1` or `auto`.

---

## Acknowledgements

Built on top of the agent loop + sandbox + budget infrastructure from
Assignment 2 (within the course's 50% reuse allowance). The new work for the
VG is: parallel sub-agent orchestration (`workers.py`), history compaction
(`context.py`), risk-tiered approval (`sandbox.py` + `approval.py`), and the
TestWriter specialization itself (the system prompt + tool schemas).