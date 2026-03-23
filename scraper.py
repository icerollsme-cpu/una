"""
Page scraper — fetches live URLs and extracts SEO elements.

Primary:  scrape.do   (handles JS-rendered pages, rotating proxies)
Fallback: scrapestack (proxy-based simple HTML)
Last:     direct requests (no proxy, may be blocked)

Parses with BeautifulSoup (lxml parser, html.parser fallback).
"""

import json
import os
import re
import time

import requests
from bs4 import BeautifulSoup

SCRAPE_DO_TOKEN   = os.getenv("SCRAPE_DO_TOKEN", "")
SCRAPESTACK_KEY   = os.getenv("SCRAPESTACK_KEY", "")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

TIMEOUT = 30


# ── Public API ────────────────────────────────────────────────────────────────

def scrape_page(url: str) -> dict:
    """
    Fetch a URL and extract all SEO-relevant on-page elements.
    Returns a dict compatible with the seo_agent audit schema.
    """
    if not url:
        return _error_result("No URL provided")

    html, method = _fetch_html(url)
    if not html:
        return _error_result(f"All fetch methods failed for {url}")

    parsed = _parse_html(html, url)
    parsed["fetch_method"] = method
    return parsed


def scrape_competitor(url: str) -> dict:
    """
    Lightweight competitor scrape — only extracts heading structure
    and a content summary. Used for content gap analysis.
    """
    if not url:
        return {"url": url, "error": "No URL"}

    html, _ = _fetch_html(url)
    if not html:
        return {"url": url, "error": "Fetch failed"}

    soup = _make_soup(html)
    return {
        "url": url,
        "title": _get_title(soup),
        "h1": _get_h1(soup),
        "h2s": [h.get_text(strip=True) for h in soup.find_all("h2")][:15],
        "h3s": [h.get_text(strip=True) for h in soup.find_all("h3")][:20],
        "word_count": _word_count(soup),
    }


# ── Fetch layer ───────────────────────────────────────────────────────────────

def _fetch_html(url: str) -> tuple[str | None, str]:
    """Try each fetch method in order. Returns (html, method_name)."""

    # 1. scrape.do
    if SCRAPE_DO_TOKEN:
        html = _fetch_scrapedo(url)
        if html:
            return html, "scrape.do"

    # 2. scrapestack
    if SCRAPESTACK_KEY:
        html = _fetch_scrapestack(url)
        if html:
            return html, "scrapestack"

    # 3. Direct request (last resort)
    html = _fetch_direct(url)
    if html:
        return html, "direct"

    return None, "failed"


def _fetch_scrapedo(url: str) -> str | None:
    try:
        resp = requests.get(
            "https://api.scrape.do/",
            params={"token": SCRAPE_DO_TOKEN, "url": url, "render": "true"},
            timeout=TIMEOUT,
        )
        if resp.ok and len(resp.text) > 500:
            return resp.text
        if resp.status_code == 402:
            print("  [scrape.do] Credit limit reached — skipping")
    except Exception as e:
        print(f"  [scrape.do] Error: {e}")
    return None


def _fetch_scrapestack(url: str) -> str | None:
    try:
        resp = requests.get(
            "http://api.scrapestack.com/scrape",
            params={"access_key": SCRAPESTACK_KEY, "url": url},
            timeout=TIMEOUT,
        )
        if resp.ok and len(resp.text) > 500:
            return resp.text
    except Exception as e:
        print(f"  [scrapestack] Error: {e}")
    return None


def _fetch_direct(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=TIMEOUT)
        if resp.ok and len(resp.text) > 500:
            return resp.text
    except Exception as e:
        print(f"  [direct] Error: {e}")
    return None


# ── Parse layer ───────────────────────────────────────────────────────────────

def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _get_title(soup: BeautifulSoup) -> str:
    tag = soup.find("title")
    return tag.get_text(strip=True) if tag else ""


def _get_meta_description(soup: BeautifulSoup) -> str:
    for attrs in [{"name": "description"}, {"property": "og:description"}]:
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _get_h1(soup: BeautifulSoup) -> str:
    tag = soup.find("h1")
    return tag.get_text(strip=True) if tag else ""


def _word_count(soup: BeautifulSoup) -> int:
    """Count words in main content area, excluding nav/footer/aside."""
    # Prefer main content containers
    content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find("div", class_=re.compile(r"content|post|article|entry", re.I))
        or soup.find("body")
    )
    if not content:
        return 0
    # Remove noise tags
    for tag in content.find_all(["nav", "footer", "aside", "script", "style", "header"]):
        tag.decompose()
    text = content.get_text(separator=" ", strip=True)
    return len(text.split())


def _detect_schema(soup: BeautifulSoup) -> str:
    types_found = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            # handle @graph arrays
            if "@graph" in data:
                for item in data["@graph"]:
                    t = item.get("@type")
                    if t:
                        types_found.append(t if isinstance(t, str) else t[0])
            else:
                t = data.get("@type")
                if t:
                    types_found.append(t if isinstance(t, str) else t[0])
        except Exception:
            pass
    # Also check microdata
    for tag in soup.find_all(attrs={"itemtype": True}):
        itype = tag.get("itemtype", "")
        name = itype.split("/")[-1]
        if name:
            types_found.append(name)

    unique = list(dict.fromkeys(types_found))  # preserve order, deduplicate
    return ", ".join(unique) if unique else "none"


def _infer_page_type(url: str, soup: BeautifulSoup) -> str:
    url_lower = url.lower()
    if any(x in url_lower for x in ["/blog/", "/post/", "/article/", "/news/"]):
        return "blog"
    if any(x in url_lower for x in ["/product/", "/item/", "/p/"]):
        return "product"
    if any(x in url_lower for x in ["/category/", "/cat/", "/collection/"]):
        return "category"
    if any(x in url_lower for x in ["/landing", "/lp/", "/offer/"]):
        return "landing"
    return "unknown"


def _parse_html(html: str, url: str) -> dict:
    soup = _make_soup(html)
    title   = _get_title(soup)
    meta    = _get_meta_description(soup)
    h1      = _get_h1(soup)
    h2s     = [h.get_text(strip=True) for h in soup.find_all("h2")][:20]
    h3s     = [h.get_text(strip=True) for h in soup.find_all("h3")][:30]
    wc      = _word_count(soup)
    schema  = _detect_schema(soup)
    pt      = _infer_page_type(url, soup)

    return {
        "scrape_status": "success",
        "title_tag": title,
        "meta_description": meta,
        "h1": h1,
        "h2s": h2s,
        "h3s": h3s,
        "word_count": wc,
        "page_type_inferred": pt,
        "schema_markup": schema,
    }


def _error_result(reason: str) -> dict:
    return {
        "scrape_status": "failed",
        "error": reason,
        "title_tag": "",
        "meta_description": "",
        "h1": "",
        "h2s": [],
        "h3s": [],
        "word_count": 0,
        "page_type_inferred": "unknown",
        "schema_markup": "none",
    }
