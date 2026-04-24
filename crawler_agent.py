"""
SEO Crawler Agent — powered by Kimi K2 via NVIDIA NIM API.

Pipeline per site:
  1. Discover all pages via XML sitemap  → sitemap_crawler.py
  2. Scrape each page                    → scraper.py
  3. Score opportunity locally (fast rules-based check)
  4. Send to Kimi K2 for deep analysis:
       • Identify topic & main keyword
       • Suggest optimized title  (≤ TITLE_MAX chars)
       • Suggest optimized meta   (≤ META_MAX  chars)
  5. Return structured list of PageResult dicts
"""

import json
import os
import re
import time

from openai import OpenAI, RateLimitError, APIStatusError

from scraper import scrape_page
from sitemap_crawler import discover_urls, URLEntry

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
KIMI_MODEL     = os.getenv("KIMI_MODEL", "moonshotai/kimi-k2-instruct")
NVIDIA_BASE    = "https://integrate.api.nvidia.com/v1"

TITLE_MAX = 60   # Google truncates at ~60 chars
META_MAX  = 160  # Google shows ~160 chars in SERPs

SYSTEM_PROMPT = f"""You are a senior SEO specialist producing concise, actionable metadata recommendations.
You receive scraped page data and produce structured JSON with:
  1. The page topic and primary keyword
  2. An opportunity score (High / Med / Low / None)
  3. An optimized title tag (STRICTLY ≤ {TITLE_MAX} characters)
  4. An optimized meta description (STRICTLY ≤ {META_MAX} characters)

Character limit rules — these are hard constraints, not suggestions:
  - Title:            between 40 and {TITLE_MAX} characters inclusive
  - Meta description: between 120 and {META_MAX} characters inclusive
  - Count EVERY character including spaces and punctuation
  - If a title naturally fits in fewer chars, keep it — do not pad
  - Never truncate mid-word — rewrite to fit

Keyword rules:
  - Place the primary keyword as early in the title as naturally possible
  - Include the primary keyword or a close variant in the meta description
  - Write for humans first; keyword placement second

Scoring rules (opportunity_score):
  - High: ≥ 1 of: missing meta, title > {TITLE_MAX} chars, keyword absent from title, no H1, title is just the brand name
  - Med:  title slightly off (wrong focus, weak CTA) or meta slightly long/weak
  - Low:  minor phrasing improvements only
  - None: title and meta are already well-optimized

Return ONLY a single valid JSON object. No markdown, no text before or after."""


class CrawlerAgent:
    def __init__(self):
        if not NVIDIA_API_KEY:
            raise EnvironmentError("NVIDIA_API_KEY is not set. Add it to your .env file.")
        self.client = OpenAI(base_url=NVIDIA_BASE, api_key=NVIDIA_API_KEY)

    # ── Public API ────────────────────────────────────────────────────────────

    def crawl_site(
        self,
        site_url: str,
        max_pages: int = 200,
        scrape_delay: float = 1.0,
        ai_delay: float = 1.0,
        only_opportunities: bool = False,
    ) -> list[dict]:
        """
        Crawl `site_url`, analyze every page, return list of result dicts.

        Args:
            site_url:           Homepage or any URL on the target site.
            max_pages:          Maximum pages to analyze (0 = unlimited).
            scrape_delay:       Seconds between page scrapes.
            ai_delay:           Seconds between Kimi API calls.
            only_opportunities: If True, skip pages that score 'None'.
        """
        print(f"\n[Crawler] Discovering pages via sitemap…")
        url_entries = discover_urls(site_url, max_urls=max_pages or 5000)

        if not url_entries:
            print("[Crawler] No URLs discovered — aborting")
            return []

        if max_pages:
            url_entries = url_entries[:max_pages]

        total = len(url_entries)
        print(f"[Crawler] {total} pages to analyze\n")

        results = []
        for idx, entry in enumerate(url_entries, 1):
            url = entry.url
            print(f"  [{idx}/{total}] {url[:80]}")

            # Step 1 — scrape
            print(f"    Scraping…")
            scrape = scrape_page(url)
            if scrape.get("scrape_status") != "success":
                print(f"    ⚠  Scrape failed — skipping")
                results.append(_scrape_error(entry, scrape))
                continue

            # Step 2 — fast rules-based pre-score (decides whether to call AI)
            pre_score = _rules_score(scrape)
            if only_opportunities and pre_score == "None":
                print(f"    ✓ No opportunity detected — skipping (--only-opportunities)")
                continue

            # Step 3 — Kimi K2 analysis
            print(f"    Sending to Kimi K2…")
            result = self._analyze_page(entry, scrape)
            results.append(result)

            status = result.get("opportunity_score", "?")
            stitle = result.get("suggested_title", "")
            print(f"    ✓ Opportunity: {status} | Suggested title ({len(stitle)} chars): {stitle[:60]}")

            if idx < total:
                if scrape_delay:
                    time.sleep(scrape_delay)
                elif ai_delay:
                    time.sleep(ai_delay)

        return results

    # ── Per-page analysis ─────────────────────────────────────────────────────

    def _analyze_page(self, entry: URLEntry, scrape: dict) -> dict:
        context = _build_context(entry, scrape)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": context},
        ]

        full_text = ""
        for attempt in range(4):
            try:
                stream = self.client.chat.completions.create(
                    model=KIMI_MODEL,
                    messages=messages,
                    max_tokens=1200,
                    temperature=0.1,
                    stream=True,
                )
                for chunk in stream:
                    full_text += chunk.choices[0].delta.content or ""
                break

            except RateLimitError:
                wait = 2 ** (attempt + 1)
                print(f"\n    [Kimi] Rate limited — waiting {wait}s…")
                time.sleep(wait)
                full_text = ""

            except APIStatusError as e:
                print(f"\n    [Kimi] API error {e.status_code}: {e.message}")
                return _ai_error(entry, scrape, str(e))

            except Exception as e:
                print(f"\n    [Kimi] Unexpected error: {e}")
                return _ai_error(entry, scrape, str(e))

        if not full_text.strip():
            return _ai_error(entry, scrape, "Empty response from Kimi")

        return _parse_response(full_text, entry, scrape)


# ── Context builder ───────────────────────────────────────────────────────────

def _build_context(entry: URLEntry, scrape: dict) -> str:
    title      = scrape.get("title_tag") or ""
    meta       = scrape.get("meta_description") or ""
    h1         = scrape.get("h1") or ""
    h2s        = scrape.get("h2s") or []
    h3s        = scrape.get("h3s") or []
    word_count = scrape.get("word_count", 0)
    page_type  = scrape.get("page_type_inferred", "unknown")
    schema     = scrape.get("schema_markup", "none")

    lines = [
        "## PAGE DATA",
        f"URL:              {entry.url}",
        f"Sitemap Priority: {entry.priority or 'not set'}",
        f"Last Modified:    {entry.lastmod or 'unknown'}",
        "",
        "## CURRENT ON-PAGE METADATA",
        f"Title Tag ({len(title)} chars):        {title or '[empty]'}",
        f"Meta Description ({len(meta)} chars):  {meta or '[empty]'}",
        f"H1:               {h1 or '[empty]'}",
        f"Page Type:        {page_type}",
        f"Schema Markup:    {schema}",
        f"Word Count:       ~{word_count}",
    ]

    if h2s:
        lines.append("H2s: " + " | ".join(h2s[:10]))
    if h3s:
        lines.append("H3s: " + " | ".join(h3s[:8]))

    lines += [
        "",
        "## YOUR TASK",
        "Analyse the page data above and return exactly this JSON (no other text):",
        "",
        "{",
        f'  "url": "{entry.url}",',
        '  "topic": "One sentence describing what this page is about",',
        '  "main_keyword": "The primary search query this page should rank for",',
        '  "secondary_keywords": ["kw1", "kw2", "kw3"],',
        '  "page_type": "blog|landing|product|category|home|about|contact|other",',
        "",
        '  "current_title": "Exact current title or empty string",',
        f'  "current_title_length": {len(title)},',
        '  "current_meta": "Exact current meta description or empty string",',
        f'  "current_meta_length": {len(meta)},',
        "",
        f'  "opportunity_score": "High|Med|Low|None",',
        '  "opportunity_reasons": [',
        '    "Specific reason 1 (e.g. Missing meta description)",',
        '    "Specific reason 2 (e.g. Title is 74 chars — exceeds 60-char limit)"',
        '  ],',
        "",
        f'  // Title MUST be between 40 and {TITLE_MAX} characters',
        '  "suggested_title": "Your optimized title here",',
        '  "suggested_title_length": 55,',
        "",
        f'  // Meta MUST be between 120 and {META_MAX} characters',
        '  "suggested_meta": "Your optimized meta description here",',
        '  "suggested_meta_length": 148,',
        "",
        '  "title_change_needed": true,',
        '  "meta_change_needed": true,',
        '  "quick_fix": "One-line action summary, e.g. Add missing meta and shorten title"',
        "}",
        "",
        f"CRITICAL: suggested_title length MUST be ≤ {TITLE_MAX}. Count every character.",
        f"CRITICAL: suggested_meta  length MUST be ≤ {META_MAX}. Count every character.",
    ]

    return "\n".join(lines)


# ── Rules-based pre-scorer ────────────────────────────────────────────────────

def _rules_score(scrape: dict) -> str:
    """Fast local check — returns 'None' only if everything looks clean."""
    title = scrape.get("title_tag") or ""
    meta  = scrape.get("meta_description") or ""
    h1    = scrape.get("h1") or ""

    issues = []
    if not meta:
        issues.append("missing meta")
    if not title:
        issues.append("missing title")
    if len(title) > TITLE_MAX:
        issues.append(f"title too long ({len(title)} chars)")
    if len(meta) > META_MAX:
        issues.append(f"meta too long ({len(meta)} chars)")
    if not h1:
        issues.append("missing H1")

    if len(issues) >= 1:
        return "High"
    return "check"  # may still be Low/Med — let AI decide


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_response(text: str, entry: URLEntry, scrape: dict) -> dict:
    # Strip markdown fences
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.DOTALL)
    json_str = m.group(1).strip() if m else text

    if not m:
        start = json_str.find("{")
        end   = json_str.rfind("}")
        if start != -1 and end > start:
            json_str = json_str[start:end + 1]

    for candidate in [json_str, _repair_json(json_str)]:
        try:
            result = json.loads(candidate)
            # Enforce character limits — clamp if AI exceeded them
            result = _enforce_limits(result)
            result["scrape_status"] = scrape.get("scrape_status", "success")
            result["sitemap_priority"] = entry.priority
            result["sitemap_lastmod"]  = entry.lastmod
            return result
        except (json.JSONDecodeError, ValueError):
            continue

    return _ai_error(entry, scrape, "JSON parse error", raw=text[:500])


def _enforce_limits(result: dict) -> dict:
    """Clamp suggested_title and suggested_meta to hard character limits."""
    title = result.get("suggested_title", "")
    if len(title) > TITLE_MAX:
        # Trim at last word boundary within limit
        title = title[:TITLE_MAX].rsplit(" ", 1)[0]
        result["suggested_title"] = title
        result["_title_clamped"] = True
    result["suggested_title_length"] = len(title)

    meta = result.get("suggested_meta", "")
    if len(meta) > META_MAX:
        meta = meta[:META_MAX].rsplit(" ", 1)[0]
        result["suggested_meta"] = meta
        result["_meta_clamped"] = True
    result["suggested_meta_length"] = len(meta)

    return result


def _repair_json(s: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", s)


# ── Error helpers ─────────────────────────────────────────────────────────────

def _scrape_error(entry: URLEntry, scrape: dict) -> dict:
    return {
        "url": entry.url,
        "scrape_status": "failed",
        "error": scrape.get("error", "Scrape failed"),
        "topic": "", "main_keyword": "", "secondary_keywords": [],
        "page_type": "unknown",
        "current_title": "", "current_title_length": 0,
        "current_meta": "",  "current_meta_length": 0,
        "opportunity_score": "N/A", "opportunity_reasons": [],
        "suggested_title": "", "suggested_title_length": 0,
        "suggested_meta": "",  "suggested_meta_length": 0,
        "title_change_needed": False, "meta_change_needed": False,
        "quick_fix": "",
        "sitemap_priority": entry.priority,
        "sitemap_lastmod":  entry.lastmod,
    }


def _ai_error(entry: URLEntry, scrape: dict, msg: str, raw: str = "") -> dict:
    return {
        "url": entry.url,
        "scrape_status": scrape.get("scrape_status", "unknown"),
        "error": msg,
        "raw_response_snippet": raw,
        "topic": "", "main_keyword": "", "secondary_keywords": [],
        "page_type": scrape.get("page_type_inferred", "unknown"),
        "current_title": scrape.get("title_tag", ""),
        "current_title_length": len(scrape.get("title_tag") or ""),
        "current_meta": scrape.get("meta_description", ""),
        "current_meta_length": len(scrape.get("meta_description") or ""),
        "opportunity_score": "N/A", "opportunity_reasons": [],
        "suggested_title": "", "suggested_title_length": 0,
        "suggested_meta": "",  "suggested_meta_length": 0,
        "title_change_needed": False, "meta_change_needed": False,
        "quick_fix": "",
        "sitemap_priority": entry.priority,
        "sitemap_lastmod":  entry.lastmod,
    }
