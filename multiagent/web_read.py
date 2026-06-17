"""Read-a-URL skill — fetch a web page and return its readable text.

requests fetches the page; BeautifulSoup strips the HTML down to text so the
model gets the article, not the markup. Output is capped so a huge page can't
blow the context window. Install: pip install beautifulsoup4
"""
import requests
from bs4 import BeautifulSoup

# A browser-ish User-Agent; some sites reject the default requests one.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-assistant/1.0)"}
_MAX_CHARS = 6000


def _extract_text(html):
    """Turn an HTML document into readable plain text: drop scripts/styles/
    chrome, collapse blank lines. Split out so it's testable without a network."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def read_url(url):
    """Fetch a URL and return its readable text (truncated if very long)."""
    if not url or not url.strip():
        return "[error: read_url needs a url]"
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        return f"[error: couldn't fetch {url}: {e}]"

    text = _extract_text(resp.text)
    if not text:
        return f"[fetched {url} but found no readable text]"
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f"\n[...truncated at {_MAX_CHARS} characters]"
    return text
