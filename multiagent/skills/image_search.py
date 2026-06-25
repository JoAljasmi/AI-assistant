"""Image search skill — finds actual images via the `ddgs` library's image
search. This is a different method from web_search's text search: web_search
returns pages, image_search returns pictures with direct URLs.

Returns up to `max_results` results, each with the image's direct URL, so the
model can either post the URL (Discord embeds it) or — more reliably — download
it into /workspace and send_file it as an attachment, since hotlinked URLs can
404 or be hotlink-blocked.
"""
from ddgs import DDGS


def image_search(query, max_results=3):
    """Search for images. Returns lines of 'title — direct_image_url'."""
    if not query or not query.strip():
        return "[error: image search needs a query]"

    # Clamp whatever the model passes to a sane range.
    try:
        max_results = min(max(int(max_results), 1), 10)
    except (TypeError, ValueError):
        max_results = 3

    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query.strip(), max_results=max_results))
    except Exception as e:
        return f"[error: image search failed: {e}]"

    if not results:
        return ("[no images found — try different keywords. (DuckDuckGo may also "
                "be rate-limiting; wait a few seconds and retry.)]")

    lines = []
    for r in results:
        # 'image' is the direct full-size URL; 'thumbnail' is a smaller fallback;
        # 'url' is the source page (not the image itself), so we don't use it here.
        img = r.get("image") or r.get("thumbnail") or ""
        if not img:
            continue
        title = (r.get("title") or "").strip()
        lines.append(f"{title} — {img}" if title else img)

    return "\n".join(lines) if lines else "[no usable image URLs in results]"