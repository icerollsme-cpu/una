"""
site_analyzer.py — Scrapes a website or app URL and extracts a full content
marketing brief: main topics, products, services, target audience, USPs,
keywords, CTAs, and suggested content angles that drive traffic back to the site.

The brief is used by orchestrator.py to guide scraper_agent and content_agent
so every piece of content serves a clear traffic goal.

Usage:
    from site_analyzer import analyze_site
    brief = analyze_site("https://example.com")

CLI:
    python site_analyzer.py https://example.com --output brief.json
"""

import json
import logging
import os
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _env(key: str, required: bool = False) -> Optional[str]:
    val = os.getenv(key)
    if required and not val:
        raise EnvironmentError(f"Missing required env var: {key}")
    return val


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _fetch_html(url: str) -> str:
    """Fetch a URL, falling back to scrape.do for JS-heavy pages."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        # If we got a near-empty body it's probably a JS SPA — try scrape.do
        if len(resp.text) < 500 and _env("SCRAPE_DO_TOKEN"):
            raise ValueError("Suspiciously short response — trying JS render")
        return resp.text
    except Exception as e:
        token = _env("SCRAPE_DO_TOKEN")
        if not token:
            raise
        log.info("Falling back to scrape.do for %s (%s)", url, e)
        r = requests.get(
            "https://api.scrape.do/",
            params={"token": token, "url": url, "render": "true"},
            timeout=60,
        )
        r.raise_for_status()
        return r.text


def _clean(html: str, max_chars: int = 6000) -> str:
    """Strip noise and return meaningful text from HTML."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(
        ["script", "style", "nav", "footer", "aside", "iframe", "noscript", "svg"]
    ):
        tag.decompose()

    parts: list[str] = []

    # Meta description / keywords
    for meta in soup.find_all("meta", attrs={"name": True, "content": True}):
        if meta["name"].lower() in ("description", "keywords"):
            parts.append(meta["content"])

    # Page title
    if soup.title:
        parts.append(f"Title: {soup.title.get_text(strip=True)}")

    # Headings
    for h in soup.find_all(["h1", "h2", "h3"]):
        t = h.get_text(separator=" ", strip=True)
        if t:
            parts.append(t)

    # Structured content blocks
    for sel in [
        "main", "article",
        '[class*="product"]', '[class*="service"]',
        '[class*="feature"]', '[class*="about"]',
        '[class*="hero"]', "section",
    ]:
        for el in soup.select(sel)[:4]:
            t = el.get_text(separator=" ", strip=True)
            if len(t) > 40:
                parts.append(t[:600])

    # Body paragraphs
    for p in soup.find_all("p"):
        t = p.get_text(strip=True)
        if len(t) > 40:
            parts.append(t)

    return " | ".join(parts)[:max_chars]


def _sitemap_urls(base_url: str, limit: int = 12) -> list[str]:
    """Parse sitemap.xml and return prioritised inner page URLs."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    for sitemap_path in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap/"]:
        try:
            resp = requests.get(root + sitemap_path, headers=_HEADERS, timeout=10)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "xml")
            locs = [loc.get_text().strip() for loc in soup.find_all("loc")]
            priority_kw = ["product", "service", "about", "feature",
                           "pricing", "solution", "blog", "shop"]
            priority = [u for u in locs if any(k in u.lower() for k in priority_kw)]
            rest = [u for u in locs if u not in priority and u != base_url]
            return (priority + rest)[:limit]
        except Exception:
            pass
    return []


def _key_pages(base_url: str) -> list[str]:
    """Return URLs to scan (sitemap + common fallback paths)."""
    urls = _sitemap_urls(base_url)
    if not urls:
        parsed = urlparse(base_url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        for path in ["/products", "/services", "/about", "/features",
                     "/pricing", "/blog", "/shop"]:
            urls.append(urljoin(root, path))
    return urls[:6]


# ---------------------------------------------------------------------------
# AI analysis
# ---------------------------------------------------------------------------

def _ai_brief(homepage_text: str, inner_text: str, url: str) -> dict:
    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=_env("NVIDIA_API_KEY", required=True),
    )
    prompt = f"""Analyse this website and produce a content marketing brief.

Homepage content:
{homepage_text}

Inner pages:
{inner_text[:3000]}

URL: {url}

Return ONLY a JSON object with these exact keys — no markdown, no commentary:
{{
  "site_name": "<brand name>",
  "industry": "<industry/niche>",
  "topics": ["<topic1>", "<topic2>", "<topic3>"],
  "products": [
    {{"name": "<product>", "description": "<one line>", "keywords": ["kw1", "kw2"]}}
  ],
  "services": [
    {{"name": "<service>", "description": "<one line>", "keywords": ["kw1", "kw2"]}}
  ],
  "target_audience": "<who the ideal customer is>",
  "unique_selling_points": ["<USP1>", "<USP2>", "<USP3>"],
  "keywords": ["<keyword1>", "<keyword2>"],
  "cta_phrases": ["<CTA1>", "<CTA2>"],
  "content_angles": [
    {{
      "angle": "<angle title>",
      "description": "<what content to make and why it attracts the target audience>",
      "platforms": ["tiktok", "instagram"],
      "traffic_hook": "<how this angle drives clicks back to the site>"
    }}
  ]
}}"""

    resp = client.chat.completions.create(
        model=_env("KIMI_MODEL") or "moonshotai/kimi-k2-instruct",
        messages=[
            {
                "role": "system",
                "content": "You are a content marketing strategist. Return only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=2000,
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyze_site(url: str, deep: bool = True) -> dict:
    """
    Analyze a website or app URL to produce a content marketing brief.

    Args:
        url:   Homepage URL, e.g. "https://mystore.com"
        deep:  If True, also fetches key inner pages for richer analysis

    Returns dict with keys: site_name, industry, topics, products, services,
    target_audience, unique_selling_points, keywords, cta_phrases,
    content_angles, url
    """
    log.info("Analyzing site: %s", url)

    homepage_text = _clean(_fetch_html(url))

    inner_parts: list[str] = []
    if deep:
        for inner_url in _key_pages(url):
            try:
                text = _clean(_fetch_html(inner_url), max_chars=800)
                if text:
                    inner_parts.append(f"[{inner_url}]\n{text}")
            except Exception as exc:
                log.debug("Skipped %s: %s", inner_url, exc)

    brief = _ai_brief(homepage_text, "\n\n".join(inner_parts), url)
    brief["url"] = url

    log.info(
        "Site brief ready — %d topics, %d products, %d services, %d angles",
        len(brief.get("topics", [])),
        len(brief.get("products", [])),
        len(brief.get("services", [])),
        len(brief.get("content_angles", [])),
    )
    return brief


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Analyse a website and produce a content marketing brief"
    )
    parser.add_argument("url", help="Website URL, e.g. https://example.com")
    parser.add_argument("--output", "-o", help="Save JSON brief to this file")
    parser.add_argument("--no-deep", action="store_true",
                        help="Skip inner page scanning (homepage only)")
    args = parser.parse_args()

    brief = analyze_site(args.url, deep=not args.no_deep)

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(brief, fh, indent=2)
        print(f"Brief saved to {args.output}")
    else:
        print(json.dumps(brief, indent=2))
