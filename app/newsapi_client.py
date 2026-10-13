"""One NewsAPI request per pull. Nothing here loops or paginates — the quota is precious."""
import re

import requests

from . import config

BASE = "https://newsapi.org/v2"


class NewsAPIError(Exception):
    pass


def fetch(mode):
    """mode: 'top', 'everything' or 'backfill:YYYY-MM-DD'. Returns a list of normalized article dicts."""
    if mode == "top":
        url = f"{BASE}/top-headlines"
        params = {"country": config.NEWSAPI_COUNTRY, "pageSize": 100}
    elif mode.startswith("backfill:"):
        # The wealth query for one UTC day, ranked by relevance so the 100 results are the
        # strongest matches across the whole day rather than its last hour.
        day = mode.split(":", 1)[1]
        url = f"{BASE}/everything"
        params = {"q": config.EVERYTHING_QUERY, "language": "en", "sortBy": "relevancy", "pageSize": 100,
                  "from": f"{day}T00:00:00", "to": f"{day}T23:59:59"}
    else:
        url = f"{BASE}/everything"
        params = {"q": config.EVERYTHING_QUERY, "language": "en", "sortBy": "publishedAt", "pageSize": 100}

    resp = requests.get(url, params=params, headers={"X-Api-Key": config.NEWSAPI_KEY}, timeout=30)
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code != 200 or data.get("status") != "ok":
        raise NewsAPIError(f"{resp.status_code} {data.get('code', '')}: {data.get('message', resp.text[:200])}")

    return [a for a in (normalize(x) for x in data.get("articles", [])) if a]


def normalize(raw):
    title = (raw.get("title") or "").strip()
    url = raw.get("url")
    if not title or not url or title == "[Removed]":
        return None
    source = (raw.get("source") or {}).get("name") or ""
    # NewsAPI headlines usually end with " - Source Name"; strip it for a cleaner table.
    if source and title.endswith(f" - {source}"):
        title = title[: -len(source) - 3].strip()
    else:
        title = re.sub(r"\s+[-|]\s+[^-|]{2,40}$", "", title) if " - " in title else title
    return {
        "url": url,
        "title": title,
        "description": (raw.get("description") or "").strip() or None,
        "source": source,
        "author": raw.get("author"),
        "image_url": raw.get("urlToImage"),
        "published_at": raw.get("publishedAt"),
    }
