"""
ScrapeGraphAI client — AI-powered content extraction for competitor pages.

Uses the ScrapeGraphAI SmartScraper API to intelligently extract structured
content from competitor URLs. Falls back to the regular scraper if the API
call fails or the key is not configured.
"""

import os
import requests
from scraper import scrape_competitor

SGAI_KEY  = os.getenv("SCRAPEGRAPHAI_KEY", "")
SGAI_URL  = "https://api.scrapegraphai.com/v1/smartscraper"
TIMEOUT   = 60

_COMPETITOR_PROMPT = (
    "Extract the following information from this page as JSON:\n"
    "1. page_title: the page title\n"
    "2. h1: the main H1 heading\n"
    "3. h2s: a list of all H2 headings\n"
    "4. h3s: a list of all H3 headings\n"
    "5. main_topics: the 3-5 core topics/subtopics this page covers\n"
    "6. content_type: article | list | guide | comparison | product | other\n"
    "7. word_count_estimate: approximate number of words\n"
    "Return only valid JSON with these exact keys."
)


# ── Public API ────────────────────────────────────────────────────────────────

def scrape_competitors(urls: list[str], max_ai: int = 3) -> list[dict]:
    """
    Scrape a list of competitor URLs.
    First `max_ai` are processed via ScrapeGraphAI (richer extraction).
    Remaining URLs fall back to the regular HTML scraper.
    """
    results = []
    for i, url in enumerate(urls):
        if not url:
            continue
        print(f"  [competitor {i+1}/{len(urls)}] {url[:70]}")
        if i < max_ai and SGAI_KEY:
            data = _sgai_scrape(url)
        else:
            data = scrape_competitor(url)
        data["url"] = url
        results.append(data)
    return results


# ── ScrapeGraphAI call ────────────────────────────────────────────────────────

def _sgai_scrape(url: str) -> dict:
    try:
        resp = requests.post(
            SGAI_URL,
            headers={
                "SGAI-APIKEY":  SGAI_KEY,
                "Content-Type": "application/json",
            },
            json={
                "website_url": url,
                "user_prompt": _COMPETITOR_PROMPT,
            },
            timeout=TIMEOUT,
        )

        if resp.ok:
            payload = resp.json()
            # ScrapeGraphAI wraps the result in a "result" key
            result = payload.get("result") or payload
            if isinstance(result, dict):
                return _normalise_sgai(result)
            # Sometimes it's a string — parse it
            if isinstance(result, str):
                import json as _json
                try:
                    return _normalise_sgai(_json.loads(result))
                except Exception:
                    pass

        # API responded but unusable — fall through to regular scraper
        print(f"    [sgai] Non-OK response ({resp.status_code}), falling back")

    except Exception as e:
        print(f"    [sgai] Error: {e}, falling back")

    return scrape_competitor(url)


def _normalise_sgai(data: dict) -> dict:
    """Map ScrapeGraphAI keys → internal schema."""
    return {
        "scrape_method": "scrapegraphai",
        "title":         data.get("page_title") or data.get("title", ""),
        "h1":            data.get("h1", ""),
        "h2s":           _ensure_list(data.get("h2s")),
        "h3s":           _ensure_list(data.get("h3s")),
        "main_topics":   _ensure_list(data.get("main_topics")),
        "content_type":  data.get("content_type", ""),
        "word_count":    data.get("word_count_estimate") or data.get("word_count", 0),
    }


def _ensure_list(val) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val:
        return [val]
    return []
