"""
SERP data client — powered by SerpAPI.

Fetches Google search results for a keyword and extracts:
  - Top 10 organic results (title, URL, snippet)
  - People Also Ask questions
  - SERP features present (Featured Snippet, PAA, Image Pack, etc.)
  - Featured snippet content
  - Related searches (useful for semantic keyword ideas)
"""

import os
import requests

SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
SERPAPI_URL = "https://serpapi.com/search"
TIMEOUT     = 30


# ── Public API ────────────────────────────────────────────────────────────────

def get_serp_data(keyword: str, country: str = "us", language: str = "en") -> dict:
    """
    Query SerpAPI for the given keyword.
    Returns a structured dict with everything the SEO agent needs.
    """
    if not SERPAPI_KEY:
        return _empty_result("SERPAPI_KEY not configured")
    if not keyword:
        return _empty_result("No keyword provided")

    raw = _call_serpapi(keyword, country, language)
    if "error" in raw:
        return _empty_result(f"SerpAPI error: {raw['error']}")

    return _extract_insights(raw, keyword)


# ── SerpAPI call ──────────────────────────────────────────────────────────────

def _call_serpapi(keyword: str, country: str, language: str) -> dict:
    try:
        resp = requests.get(
            SERPAPI_URL,
            params={
                "q":       keyword,
                "api_key": SERPAPI_KEY,
                "num":     10,
                "hl":      language,
                "gl":      country,
                "engine":  "google",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


# ── Insight extraction ────────────────────────────────────────────────────────

def _extract_insights(data: dict, keyword: str) -> dict:
    organic = data.get("organic_results") or []

    # Top 10 organic results
    top_results = [
        {
            "position": r.get("position", i + 1),
            "title":    r.get("title", ""),
            "url":      r.get("link", ""),
            "snippet":  r.get("snippet", ""),
            "date":     r.get("date", ""),
        }
        for i, r in enumerate(organic[:10])
    ]

    # Top 5 competitor URLs (for scraping)
    top_urls = [r["url"] for r in top_results[:5] if r["url"]]

    # People Also Ask
    paa = [
        q.get("question", "")
        for q in (data.get("related_questions") or [])[:8]
        if q.get("question")
    ]

    # SERP features
    features = _detect_features(data)

    # Featured snippet
    fs = _extract_featured_snippet(data)

    # Related searches → useful for semantic keywords
    related = [
        r.get("query", "")
        for r in (data.get("related_searches") or [])[:10]
        if r.get("query")
    ]

    # Title patterns from top 10 (for pattern analysis)
    title_patterns = [r["title"] for r in top_results if r["title"]]

    return {
        "keyword":         keyword,
        "top_results":     top_results,
        "top_urls":        top_urls,
        "paa_questions":   paa,
        "serp_features":   features,
        "featured_snippet": fs,
        "related_searches": related,
        "title_patterns":  title_patterns,
        "total_results":   data.get("search_information", {}).get("total_results", "unknown"),
    }


def _detect_features(data: dict) -> list[str]:
    features = []
    if data.get("answer_box"):
        features.append("Featured Snippet")
    if data.get("related_questions"):
        features.append("People Also Ask")
    if data.get("inline_images") or data.get("images_results"):
        features.append("Image Pack")
    if data.get("local_results") or data.get("local_map"):
        features.append("Local Pack")
    if data.get("knowledge_graph"):
        features.append("Knowledge Graph")
    if data.get("videos") or data.get("video_results"):
        features.append("Video Carousel")
    if data.get("shopping_results"):
        features.append("Shopping Ads")
    if data.get("top_stories"):
        features.append("Top Stories")
    return features


def _extract_featured_snippet(data: dict) -> dict | None:
    ab = data.get("answer_box")
    if not ab:
        return None
    return {
        "type":    ab.get("type", ""),
        "title":   ab.get("title", ""),
        "answer":  ab.get("answer") or ab.get("snippet") or ab.get("result", ""),
        "url":     ab.get("link", ""),
        "list":    ab.get("list", []),
        "table":   ab.get("table", {}),
    }


def _empty_result(reason: str) -> dict:
    return {
        "keyword":          "",
        "top_results":      [],
        "top_urls":         [],
        "paa_questions":    [],
        "serp_features":    [],
        "featured_snippet": None,
        "related_searches": [],
        "title_patterns":   [],
        "total_results":    "unknown",
        "error":            reason,
    }
