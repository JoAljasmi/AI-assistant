"""History compaction for the agent loop.

The Problem
-----------
The model is stateless, so every chat() call re-sends the entire `messages`
list. A long session balloons that list: after 30 iterations of bash, edit,
tool results, you've got 80+ messages and tens of thousands of tokens.
You blow the context window OR keep paying for tokens the model doesn't
strictly need.

The Fix
-------
When `messages` grows past a threshold, summarize the older middle of the
conversation into a single short synthetic message. Keep:
  - the system prompt (always)
  - the original user task (always)
  - the most recent N messages verbatim (recent context the model needs)
Drop everything between, replaced by one "earlier work summary" message.

This is one half of the context-engineering strategy; the other half is
tool-output truncation in sandbox.truncate_output.

API
---
maybe_compact(messages, budget) -> (new_messages, did_compact, info_str)

Call once per iteration before the model is invoked. If no compaction is
needed, returns (messages, False, ""). If compaction happens, returns
(new_messages, True, "<from N msgs to M msgs, saved ~K tokens>").
"""
from provider import chat


# Compaction triggers when len(messages) > MIN_BEFORE_COMPACT.
# Tuned for demos: low enough to fire in a normal-length test session,
# high enough that short sessions don't compact unnecessarily.
MIN_BEFORE_COMPACT = 30

# How many of the most recent messages to keep verbatim. The model needs
# enough recent context to reason about what it's currently doing.
KEEP_RECENT = 10

# The first K messages are always preserved (system prompt, original task).
KEEP_HEAD = 2

SUMMARIZE_PROMPT = (
    "You are summarizing the middle portion of an autonomous agent's work log. "
    "The agent has been working on a coding task. Below are the messages "
    "exchanged so far. Produce a concise summary (under 250 words) of what "
    "the agent has done: which files were read, which commands were run, "
    "what was learned, what files were written, and any errors encountered. "
    "Skip pleasantries and reasoning narration; capture only the facts the "
    "agent needs to continue. Do not include verbatim quotes or long code "
    "blocks. The summary will replace these messages in the agent's context, "
    "so the agent needs to know the state of the world but not every step."
)


def _est_tokens(messages):
    """Rough token count: 4 chars per token is a standard approximation."""
    total_chars = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        # tool_calls field on assistant messages contains the function args
        for tc in (m.get("tool_calls") or []):
            args = tc.get("function", {}).get("arguments", "")
            total_chars += len(args) if isinstance(args, str) else 0
    return total_chars // 4


def _safe_split(messages):
    """Return (head, middle, tail) such that:
      head = first KEEP_HEAD messages (system + initial user)
      tail = last KEEP_RECENT messages
      middle = everything else, properly cut so we don't split a tool_call
               from its corresponding tool response.

    Returns None if middle would be empty or too small to bother with.
    """
    if len(messages) <= KEEP_HEAD + KEEP_RECENT:
        return None  # nothing meaningful to compact

    head = messages[:KEEP_HEAD]
    tail = messages[-KEEP_RECENT:]
    middle = messages[KEEP_HEAD:-KEEP_RECENT]

    if not middle:
        return None

    # Don't cut a tool_call/tool_response pair. If the FIRST message in tail
    # is a tool response, walk backwards and pull its assistant message into
    # tail too (otherwise the API rejects an orphan tool response).
    while tail and tail[0].get("role") == "tool" and middle:
        tail.insert(0, middle.pop())

    # And if the LAST message in middle has tool_calls, walk forward — its
    # tool responses must travel with it. Pull them in from tail.
    if middle and middle[-1].get("tool_calls"):
        # collect IDs that need responses
        pending_ids = {tc["id"] for tc in middle[-1]["tool_calls"]}
        while pending_ids and tail and tail[0].get("role") == "tool":
            tid = tail[0].get("tool_call_id")
            if tid in pending_ids:
                middle.append(tail.pop(0))
                pending_ids.discard(tid)
            else:
                break

    if len(middle) < 4:
        return None  # not worth the summary cost

    return head, middle, tail


def maybe_compact(messages, budget=None):
    """Compact the message list if it has grown past the threshold.

    Returns (new_messages, did_compact, info_str).
    """
    if len(messages) <= MIN_BEFORE_COMPACT:
        return messages, False, ""

    split = _safe_split(messages)
    if split is None:
        return messages, False, ""

    head, middle, tail = split

    # Build a compact text representation of the middle to summarize.
    # We don't send the raw message objects to the summarizer because the
    # tool_call shapes are noisy; flatten to plain text.
    middle_text_lines = []
    for m in middle:
        role = m.get("role", "?")
        content = m.get("content") or ""
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                middle_text_lines.append(
                    f"[{role} called {fn.get('name')}]: {fn.get('arguments', '')[:300]}"
                )
        if content:
            # Truncate huge tool results so the summary call doesn't blow up.
            short = content if len(content) < 800 else content[:800] + " ...[truncated]"
            middle_text_lines.append(f"[{role}]: {short}")
    middle_text = "\n".join(middle_text_lines)

    # Estimated savings before/after, just for the demo log.
    tokens_before = _est_tokens(messages)

    # Single non-tool call to summarize. Use the same chat function so the
    # budget tracks this cost. If the budget rejects, give up gracefully —
    # compaction is best-effort, not load-bearing.
    summary_messages = [
        {"role": "system", "content": SUMMARIZE_PROMPT},
        {"role": "user", "content": middle_text},
    ]
    try:
        reply = chat(summary_messages, tools=None, budget=budget)
    except Exception as e:
        return messages, False, f"compact skipped: {e}"

    summary = (reply.get("content") or "").strip()
    if not summary:
        return messages, False, "compact skipped: empty summary"

    synthetic = {
        "role": "user",
        "content": (
            f"[Earlier work summary — the following replaces {len(middle)} "
            f"older messages so the context stays manageable]\n\n{summary}"
        ),
    }

    new_messages = head + [synthetic] + tail
    tokens_after = _est_tokens(new_messages)
    saved = tokens_before - tokens_after
    info = (
        f"{len(messages)} -> {len(new_messages)} messages, "
        f"~{tokens_before} -> ~{tokens_after} tokens "
        f"(saved ~{saved})"
    )
    return new_messages, True, info