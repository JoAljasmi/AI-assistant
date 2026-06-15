import os
from dotenv import load_dotenv
import requests
from config import PROVIDER_URL, MODEL
from pathlib import Path


load_dotenv(Path(__file__).parent.parent / ".env")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


def chat(messages, tools=None, temperature=None, budget=None):

    if budget is not None:
        allowed, reason = budget.check_and_record()
        if not allowed:
            raise RuntimeError(f"[budget] blocked: {reason}")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MODEL,
        "messages": messages,
    }
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
    if budget is not None:
        usage = payload.get("usage", {})
        total = usage.get("total_tokens", 0)
        budget.add_usage(total)
        
    return payload["choices"][0]["message"]