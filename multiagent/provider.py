"""LLM provider client — one thin function over the chat-completions endpoint.

Everything in the agent speaks the OpenAI-style message format, and we send that
format to OpenRouter (which is OpenAI-compatible). Because the whole codebase
goes through this single `chat()` function, swapping the model or vendor is a
config change, not a code change.

This file also holds the ModelLadder: an ordered list of models, cheapest first,
that the agent climbs only when it gets stuck — so most turns run on the cheap
model and only hard ones reach for a stronger one.
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from config import PROVIDER_URL, MODEL

# The API key lives in a .env file one directory up — never hard-coded.
load_dotenv(Path(__file__).parent.parent / ".env")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


class ModelLadder:
    """An ordered list of models, cheapest first (e.g. [haiku, sonnet, opus]).

    The agent starts each turn at the bottom and climbs one rung when it gets
    stuck, so the common case stays cheap and only genuinely hard turns spend
    more. An empty ladder means "no escalation" — provider.chat falls back to
    the configured default model.
    """

    def __init__(self, models):
        self.models = list(models) if models else []
        self.index = 0

    def current(self):
        """The model to use right now, or None if the ladder is empty."""
        return self.models[self.index] if self.models else None

    def escalate(self):
        """Climb one rung. Returns the new model name, or None if already at the
        top (nowhere higher to go)."""
        if self.index < len(self.models) - 1:
            self.index += 1
            return self.models[self.index]
        return None

    def reset(self):
        """Drop back to the cheapest model — called at the start of each turn."""
        self.index = 0


def chat(messages, tools=None, temperature=None, budget=None, model=None):
    """Send one chat-completion request and return the model's reply message.

    Args:
        messages: the full conversation so far (the model is stateless, so the
            whole history is re-sent every call).
        tools: optional list of tool/function schemas the model may call.
        temperature: optional sampling temperature.
        budget: optional shared Budget; if given, gates the call and records usage.
        model: which model to call. Defaults to the configured MODEL when None,
            so callers without a ladder still work.

    Returns:
        The reply message dict (text, tool_calls, or both).

    Raises:
        RuntimeError: if the budget blocks the call, or the API returns non-200.
    """
    # Budget gate: refuse *before* spending if we're over a cap.
    if budget is not None:
        allowed, reason = budget.check_and_record()
        if not allowed:
            raise RuntimeError(f"[budget] blocked: {reason}")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {"model": model or MODEL, "messages": messages}
    if tools is not None:
        body["tools"] = tools
    if temperature is not None:
        body["temperature"] = temperature

    response = requests.post(PROVIDER_URL, headers=headers, json=body)
    if response.status_code != 200:
        raise RuntimeError(
            f"OpenRouter call failed: HTTP {response.status_code}\n{response.text}"
        )

    payload = response.json()

    # Record what this call actually cost so the budget stays accurate.
    if budget is not None:
        usage = payload.get("usage", {})
        budget.add_usage(usage.get("total_tokens", 0))

    return payload["choices"][0]["message"]