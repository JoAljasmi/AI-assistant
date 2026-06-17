"""LLM provider client — one thin function over the chat-completions endpoint.

Everything in the agent speaks the OpenAI-style message format, and we send that
format to OpenRouter (which is OpenAI-compatible). Because the whole codebase
goes through this single `chat()` function, swapping the model or vendor is a
config change, not a code change — nothing else knows or cares what's behind it.

The one extra job here is budget accounting: ask the shared Budget for
permission before spending, and record the tokens actually used afterward.
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from config import PROVIDER_URL, MODEL

# The API key lives in a .env file one directory up — never hard-coded.
load_dotenv(Path(__file__).parent.parent / ".env")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


def chat(messages, tools=None, temperature=None, budget=None):
    """Send one chat-completion request and return the model's reply message.

    Args:
        messages: the full conversation so far. The model is stateless, so the
            entire history is re-sent on every call.
        tools: optional list of tool/function schemas the model may call.
        temperature: optional sampling temperature.
        budget: optional shared Budget. If given, the call is gated by the spend
            cap and its token usage is recorded.

    Returns:
        The reply message dict (which may contain text, tool_calls, or both).

    Raises:
        RuntimeError: if the budget blocks the call, or the API returns non-200.
    """
    # Budget gate: refuse *before* spending if we're over a cap. The caller
    # (main.py / discord_bot.py) catches this RuntimeError and reports it cleanly.
    if budget is not None:
        allowed, reason = budget.check_and_record()
        if not allowed:
            raise RuntimeError(f"[budget] blocked: {reason}")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {"model": MODEL, "messages": messages}
    # Only attach optional fields when set, so we don't override API defaults.
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

    # The reply is the first choice's message — text and/or tool calls.
    return payload["choices"][0]["message"]