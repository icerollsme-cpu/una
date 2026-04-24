"""
Sitemap crawler — discovers all URLs on a site via XML sitemaps.

Discovery order:
  1. Parse robots.txt for Sitemap: directives
  2. Try common sitemap locations: /sitemap.xml, /sitemap_index.xml, /sitemap/
  3. Recursively resolve sitemap index files
  4. Return deduplicated list of URLEntry objects with optional metadata
"""

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SEOCrawler/1.0; +https://github.com/seo-agent)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TIMEOUT = 20
MAX_SITEMAP_URLS = 10_000  # safety cap per sitemap file

# XML namespace used by standard sitemaps
_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@dataclass
class URLEntry:
    url: str
    lastmod: str = ""
    changefreq: str = ""
    priority: str = ""
    source_sitemap: str = ""


# ── Public API ────────────────────────────────────────────────────────────────

def discover_urls(site_url: str, max_urls: int = 5000, delay: float = 0.5) -> list[URLEntry]:
    """
    Discover all page URLs for `site_url` via its XML sitemaps.

    Returns a deduplicated list of URLEntry objects sorted by priority desc.
    `max_urls` caps total pages returned (0 = unlimited up to MAX_SITEMAP_URLS).
    """
    origin = _origin(site_url)
    sitemap_urls = _find_sitemaps(origin)

    if not sitemap_urls:
        print(f"  [sitemap] No sitemaps found for {origin}")
        return []

    print(f"  [sitemap] Found {len(sitemap_urls)} sitemap location(s) to crawl")

    seen_urls: set[str] = set()
    seen_sitemaps: set[str] = set()
    entries: list[URLEntry] = []

    def _crawl(sm_url: str, depth: int = 0):
        if sm_url in seen_sitemaps or depth > 5:
            return
        seen_sitemaps.add(sm_url)

        if max_urls and len(entries) >= max_urls:
            return

        if delay and depth > 0:
            time.sleep(delay)

        xml = _fetch_xml(sm_url)
        if xml is None:
            return

        # Sitemap index → recurse
        if _is_index(xml):
            child_urls = _parse_index(xml, sm_url)
            print(f"  [sitemap] Index at {sm_url[:70]} → {len(child_urls)} child sitemaps")
            for child in child_urls:
                _crawl(child, depth + 1)
            return

        # Regular sitemap → extract URLs
        new_entries = _parse_sitemap(xml, sm_url)
        for e in new_entries:
            if e.url not in seen_urls:
                seen_urls.add(e.url)
                entries.append(e)
                if max_urls and len(entries) >= max_urls:
                    break

        print(f"  [sitemap] {sm_url[:70]} → {len(new_entries)} URLs ({len(entries)} total)")

    for sm_url in sitemap_urls:
        _crawl(sm_url)
        if max_urls and len(entries) >= max_urls:
            break

    # Sort by priority descending, then stable order
    entries.sort(key=lambda e: float(e.priority) if e.priority else 0.5, reverse=True)

    print(f"  [sitemap] Total unique URLs discovered: {len(entries)}")
    return entries


# ── Sitemap discovery ─────────────────────────────────────────────────────────

def _find_sitemaps(origin: str) -> list[str]:
    """Return ordered list of sitemap URLs to try."""
    candidates: list[str] = []

    # 1. Parse robots.txt
    robots_sitemaps = _sitemaps_from_robots(origin)
    candidates.extend(robots_sitemaps)

    # 2. Common fallback paths (only add if not already found)
    fallbacks = [
        "/sitemap.xml",
        "/sitemap_index.xml",
        "/sitemap/sitemap.xml",
        "/sitemap/",
        "/wp-sitemap.xml",       # WordPress
        "/news-sitemap.xml",
        "/product-sitemap.xml",
    ]
    for path in fallbacks:
        url = origin + path
        if url not in candidates:
            candidates.append(url)

    # Filter to only reachable ones (HEAD check)
    reachable = []
    for url in candidates:
        if _head_ok(url):
            reachable.append(url)
            if len(reachable) >= 5:  # don't over-probe
                break

    return reachable if reachable else candidates[:1]  # at least try the first


def _sitemaps_from_robots(origin: str) -> list[str]:
    """Fetch robots.txt and extract Sitemap: directives."""
    try:
        resp = requests.get(
            f"{origin}/robots.txt",
            headers=_HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        if not resp.ok:
            return []
        urls = []
        for line in resp.text.splitlines():
            if line.lower().startswith("sitemap:"):
                url = line.split(":", 1)[1].strip()
                if url.startswith("http"):
                    urls.append(url)
        return urls
    except Exception:
        return []


def _head_ok(url: str) -> bool:
    try:
        r = requests.head(url, headers=_HEADERS, timeout=10, allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False


# ── XML fetch & parse ─────────────────────────────────────────────────────────

def _fetch_xml(url: str) -> ET.Element | None:
    try:
        resp = requests.get(
            url, headers=_HEADERS, timeout=TIMEOUT, allow_redirects=True
        )
        if not resp.ok:
            print(f"  [sitemap] HTTP {resp.status_code} for {url[:70]}")
            return None
        content = resp.content
        # Some sitemaps are gzip-compressed
        if url.endswith(".gz") or resp.headers.get("Content-Type", "").startswith("application/x-gzip"):
            import gzip
            content = gzip.decompress(content)
        return ET.fromstring(content)
    except ET.ParseError as e:
        print(f"  [sitemap] XML parse error for {url[:70]}: {e}")
        return None
    except Exception as e:
        print(f"  [sitemap] Fetch error for {url[:70]}: {e}")
        return None


def _is_index(root: ET.Element) -> bool:
    tag = root.tag.lower()
    return "sitemapindex" in tag


def _parse_index(root: ET.Element, base_url: str) -> list[str]:
    urls = []
    # Try with namespace first, then without
    for sitemap in root.findall("sm:sitemap", _NS) or root.findall("sitemap"):
        loc = sitemap.find("sm:loc", _NS) or sitemap.find("loc")
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls[:200]  # cap child sitemaps


def _parse_sitemap(root: ET.Element, source_url: str) -> list[URLEntry]:
    entries = []
    # Try namespace-aware, then bare tag names
    urls_elem = root.findall("sm:url", _NS) or root.findall("url")
    for url_elem in urls_elem[:MAX_SITEMAP_URLS]:
        loc = url_elem.find("sm:loc", _NS) or url_elem.find("loc")
        if loc is None or not loc.text:
            continue
        href = loc.text.strip()
        if not href.startswith("http"):
            continue

        lm  = url_elem.find("sm:lastmod", _NS) or url_elem.find("lastmod")
        cf  = url_elem.find("sm:changefreq", _NS) or url_elem.find("changefreq")
        pri = url_elem.find("sm:priority", _NS) or url_elem.find("priority")

        entries.append(URLEntry(
            url=href,
            lastmod=lm.text.strip()  if lm  and lm.text  else "",
            changefreq=cf.text.strip() if cf  and cf.text  else "",
            priority=pri.text.strip() if pri and pri.text else "",
            source_sitemap=source_url,
        ))
    return entries


# ── Utility ───────────────────────────────────────────────────────────────────

def _origin(url: str) -> str:
    """Return scheme + netloc, e.g. 'https://example.com'."""
    p = urlparse(url if "://" in url else "https://" + url)
    return f"{p.scheme}://{p.netloc}"
