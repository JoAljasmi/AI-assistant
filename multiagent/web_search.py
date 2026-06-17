"""Web search skill — keyless web search via the `ddgs` library (DuckDuckGo and
other backends). Returns a short list of results (title, url, snippet) for the
model to read.

ddgs returns snippets, not full page text — to actually read a result, the model
follows up with the read_url skill. Install: pip install ddgs
"""
from ddgs import DDGS


def web_search(query, max_results=5):
    """Search the web and return up to `max_results` results as text."""
    if not query or not query.strip():
        return "[error: search needs a query]"

    # Clamp to a sane range whatever the model passes.
    try:
        max_results = min(max(int(max_results), 1), 10)
    except (TypeError, ValueError):
        max_results = 5

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query.strip(), max_results=max_results))
    except Exception as e:
        return f"[error: search failed: {e}]"

    if not results:
        return ("[no results — try different keywords. (DuckDuckGo may also be "
                "rate-limiting; wait a few seconds and retry.)]")

    lines = []
    for r in results:
        title = r.get("title", "(no title)")
        url = r.get("href") or r.get("url") or ""
        snippet = (r.get("body") or "").strip()
        if len(snippet) > 200:
            snippet = snippet[:197] + "..."
        lines.append(f"- {title}\n  {url}\n  {snippet}")
    return "\n".join(lines)
