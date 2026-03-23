"""
SEO Optimization Agent — powered by Kimi K2 via NVIDIA NIM API.

Pipeline per page:
  1. Scrape the live page   → scraper.py       (scrape.do → scrapestack → direct)
  2. Fetch SERP data        → serp_client.py   (SerpAPI)
  3. Scrape top competitors → scrapegraph_client.py (scrapegraphai → BS4 fallback)
  4. Analyse everything     → Kimi K2 via NVIDIA OpenAI-compatible API
  5. Return structured JSON matching the output_generator schema
"""

import json
import os
import re
import time

from openai import OpenAI, RateLimitError, APIStatusError

from scraper import scrape_page
from serp_client import get_serp_data
from scrapegraph_client import scrape_competitors

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
KIMI_MODEL     = os.getenv("KIMI_MODEL", "moonshotai/kimi-k2-instruct")
NVIDIA_BASE    = "https://integrate.api.nvidia.com/v1"

SYSTEM_PROMPT = """You are a senior SEO strategist producing structured, actionable SEO briefs.
You receive real scraped data — page content, SERP results, competitor analysis — and produce
detailed, specific recommendations a content editor can execute immediately.

Rules:
- Be specific. Never say "improve keyword density"; say exactly where and what to change.
- Rationale must reference: search intent alignment, E-E-A-T signal, keyword placement,
  CTR improvement, or featured snippet eligibility.
- All recommendations must be executable by a content editor. Mark developer tasks [DEV].
- Do not contradict current Google guidelines (no keyword stuffing, no exact-match over-optimisation).
- Return ONLY a single valid JSON object. No text before or after it."""


class SEOAgent:
    def __init__(self):
        if not NVIDIA_API_KEY:
            raise EnvironmentError("NVIDIA_API_KEY is not set. Add it to your .env file.")
        self.client = OpenAI(
            base_url=NVIDIA_BASE,
            api_key=NVIDIA_API_KEY,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze_page(self, page_data: dict) -> dict:
        """Run the full 5-section SEO audit for one page."""
        url        = page_data.get("Page URL", "").strip()
        keyword    = page_data.get("Target Keyword (primary)", "").strip()
        position   = page_data.get("Current Position (avg)", "N/A")
        volume     = page_data.get("Monthly Search Volume", "N/A")
        difficulty = page_data.get("Keyword Difficulty (0-100)", "N/A")
        page_type  = page_data.get("Page Type", "").strip()

        flags = []
        if not url:      flags.append("MISSING: Page URL")
        if not keyword:  flags.append("MISSING: Target Keyword")
        if not page_type: flags.append("MISSING: Page Type (will infer)")

        if not url and not keyword:
            return {
                "url": url, "target_keyword": keyword,
                "flags": flags,
                "error": "Skipped: both URL and keyword are missing",
                "_raw_data": page_data,
            }

        # ── Step 1: Scrape the target page
        print(f"  [1/4] Scraping page…")
        audit = scrape_page(url) if url else _empty_audit()
        if audit.get("scrape_status") != "success":
            flags.append(f"SCRAPE FAILED: {audit.get('error', 'unknown error')}")
            print(f"  ⚠  Scrape failed — continuing with empty audit data")

        # ── Step 2: SERP data
        print(f"  [2/4] Fetching SERP data for: {keyword}")
        serp = get_serp_data(keyword) if keyword else _empty_serp()
        if serp.get("error"):
            flags.append(f"SERP WARNING: {serp['error']}")

        # ── Step 3: Competitor analysis
        comp_urls = serp.get("top_urls", [])[:5]
        competitors = []
        if comp_urls:
            print(f"  [3/4] Scraping {len(comp_urls)} competitor pages…")
            competitors = scrape_competitors(comp_urls)
        else:
            print(f"  [3/4] No competitor URLs — skipping")

        # ── Step 4: Kimi analysis
        print(f"  [4/4] Sending to Kimi K2 for analysis…")
        context = _build_context(
            url, keyword, position, volume, difficulty, page_type,
            audit, serp, competitors, flags,
        )
        return self._call_kimi(context, url, keyword, flags, page_data)

    # ── Kimi call ─────────────────────────────────────────────────────────────

    def _call_kimi(
        self, context: str, url: str, keyword: str,
        flags: list, page_data: dict,
    ) -> dict:
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
                    max_tokens=8000,
                    temperature=0.2,
                    stream=True,
                )
                print()
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    print(delta, end="", flush=True)
                    full_text += delta
                print()
                break  # success

            except RateLimitError:
                wait = 2 ** (attempt + 1)
                print(f"\n  [Kimi] Rate limited — waiting {wait}s…")
                time.sleep(wait)
                full_text = ""

            except APIStatusError as e:
                print(f"\n  [Kimi] API error {e.status_code}: {e.message}")
                return _api_error(url, keyword, flags, page_data, str(e))

            except Exception as e:
                print(f"\n  [Kimi] Unexpected error: {e}")
                return _api_error(url, keyword, flags, page_data, str(e))

        if not full_text.strip():
            return _api_error(url, keyword, flags, page_data, "Empty response from Kimi")

        return _parse_response(full_text, url, keyword, flags, page_data)


# ── Context builder ───────────────────────────────────────────────────────────

def _build_context(
    url, keyword, position, volume, difficulty, page_type,
    audit: dict, serp: dict, competitors: list, flags: list,
) -> str:
    lines = []

    # ── Page metadata
    lines += [
        "## TARGET PAGE DATA",
        f"URL:                {url or '[not provided]'}",
        f"Target Keyword:     {keyword or '[not provided]'}",
        f"Current Position:   {position}",
        f"Monthly Volume:     {volume}",
        f"Keyword Difficulty: {difficulty}/100",
        f"Page Type:          {page_type or 'unknown'}",
    ]
    if flags:
        lines.append(f"Flags:              {' | '.join(flags)}")
    lines.append("")

    # ── Scraped page audit
    lines.append("## SCRAPED PAGE CONTENT")
    lines.append(f"Scrape Status:      {audit.get('scrape_status', 'unknown')}")
    lines.append(f"Title Tag:          {audit.get('title_tag') or '[empty]'}")
    lines.append(f"Title Length:       {len(audit.get('title_tag') or '')} chars")
    lines.append(f"Meta Description:   {audit.get('meta_description') or '[empty]'}")
    lines.append(f"Meta Length:        {len(audit.get('meta_description') or '')} chars")
    lines.append(f"H1:                 {audit.get('h1') or '[empty]'}")
    lines.append(f"Word Count:         ~{audit.get('word_count', 0)} words")
    lines.append(f"Page Type Inferred: {audit.get('page_type_inferred', 'unknown')}")
    lines.append(f"Schema Markup:      {audit.get('schema_markup', 'none')}")

    h2s = audit.get("h2s") or []
    if h2s:
        lines.append("H2s on page:")
        for h in h2s:
            lines.append(f"  - {h}")

    h3s = audit.get("h3s") or []
    if h3s:
        lines.append("H3s on page:")
        for h in h3s[:15]:
            lines.append(f"  - {h}")
    lines.append("")

    # ── SERP data
    lines.append("## SERP DATA")
    features = serp.get("serp_features") or []
    lines.append(f"SERP Features:      {', '.join(features) or 'none detected'}")
    lines.append(f"Total Results:      {serp.get('total_results', 'unknown')}")

    fs = serp.get("featured_snippet")
    if fs:
        lines.append(f"Featured Snippet:   type={fs.get('type','')}, answer={fs.get('answer','')[:200]}")

    paa = serp.get("paa_questions") or []
    if paa:
        lines.append("People Also Ask (exact phrasing from Google):")
        for q in paa:
            lines.append(f"  - {q}")

    related = serp.get("related_searches") or []
    if related:
        lines.append(f"Related Searches:   {' | '.join(related[:8])}")
    lines.append("")

    # ── Top 10 results
    top = serp.get("top_results") or []
    if top:
        lines.append("## TOP 10 SERP RESULTS")
        for r in top:
            lines.append(
                f"  #{r['position']}  {r['title']}\n"
                f"       {r['url']}\n"
                f"       {r.get('snippet','')[:200]}"
            )
        lines.append("")

    # ── Competitor content structures
    if competitors:
        lines.append("## COMPETITOR CONTENT STRUCTURES")
        lines.append("(Use these to identify content gaps — topics they cover that the target page does not.)")
        for comp in competitors:
            lines.append(f"\n--- {comp.get('url','')}")
            lines.append(f"Title: {comp.get('title','')}")
            comp_h2s = comp.get("h2s") or []
            if comp_h2s:
                lines.append("H2s: " + " | ".join(comp_h2s[:12]))
            comp_h3s = comp.get("h3s") or []
            if comp_h3s:
                lines.append("H3s: " + " | ".join(comp_h3s[:10]))
            topics = comp.get("main_topics") or []
            if topics:
                lines.append("Topics: " + ", ".join(topics))
        lines.append("")

    # ── Instruction + JSON schema
    lines += [
        "## YOUR TASK",
        "Using ONLY the data above, produce a complete 5-section SEO brief.",
        "Base all recommendations on the actual scraped data and SERP data provided.",
        "Do not invent page content — if a field was empty in the scrape, say so.",
        "",
        "Return ONLY this JSON (no text before or after):",
        "",
        "{",
        f'  "url": "{url}",',
        f'  "target_keyword": "{keyword}",',
        f'  "flags": {json.dumps(flags)},',
        '  "scrape_status": "success|failed|partial",',
        "",
        "  // SECTION 0 — exact values from the scraped data above",
        '  "audit": {',
        '    "title_tag": "...",',
        '    "meta_description": "...",',
        '    "h1": "...",',
        '    "h2s": ["...", "..."],',
        '    "h3s": ["...", "..."],',
        '    "word_count": 0,',
        '    "page_type_inferred": "blog|landing|product|category",',
        '    "schema_markup": "..."',
        '  },',
        "",
        "  // SECTION 1 — on-page recommendations based on SERP analysis",
        '  "on_page": {',
        '    "search_intent": "informational|navigational|commercial|transactional",',
        '    "serp_features": ["..."],',
        '    "title_tag_pattern": "Describe the pattern seen in the top 10 titles",',
        '    "winning_content_format": "listicle|guide|comparison|tool|video",',
        '    "recommendations": [',
        '      { "element": "Title Tag",        "current": "...", "recommended": "...", "rationale": "keyword placement: primary keyword moved to first 5 words; CTR improvement: ..." },',
        '      { "element": "Meta Description", "current": "...", "recommended": "...", "rationale": "CTR improvement: adds action verb and addresses [intent]" },',
        '      { "element": "H1",               "current": "...", "recommended": "...", "rationale": "search intent alignment: ..." },',
        '      { "element": "H2s",              "current": "H2 1 | H2 2", "recommended": "New H2 1 | New H2 2 | New H2 3", "rationale": "subtopic coverage: adds [X] covered by 3+ competitors" },',
        '      { "element": "H3s",              "current": "H3 1 | H3 2", "recommended": "New H3 1 | New H3 2", "rationale": "featured snippet eligibility: directly answers PAA [question]" }',
        '    ]',
        '  },',
        "",
        "  // SECTION 2 — content gaps from competitor comparison",
        '  "content_gaps": [',
        '    { "missing_subtopic": "...", "covered_by": "https://competitor.com/page OR multiple competitors", "suggested_section_title": "Exact H2 to add", "priority": "High|Med|Low" }',
        '  ],',
        "",
        "  // SECTION 3 — keyword expansion",
        '  "keyword_expansion": {',
        '    "semantic_keywords": [',
        '      { "keyword": "...", "where_to_place": "H2 intro|Body paragraph 2|Conclusion", "intent": "..." }',
        '    ],',
        '    "paa_targets": [',
        '      { "question": "Exact PAA question from the SERP data above", "recommended_heading": "H2|H3", "answer_format": "paragraph|list|table" }',
        '    ],',
        '    "featured_snippet_opportunity": {',
        '      "trigger_query": "...",',
        '      "current_status": "not ranking|position X",',
        '      "recommended_format": "paragraph|numbered list|table",',
        '      "implementation": "Add a 40-60 word [format] directly answering [query] under H2 [title]"',
        '    }',
        '  },',
        "",
        "  // SECTION 4 — schema and technical",
        '  "schema_technical": {',
        '    "recommended_schema_types": ["FAQPage", "Article", "BreadcrumbList"],',
        '    "faq_qa_pairs": [',
        '      { "question": "...", "answer": "Concise answer under 50 words" }',
        '    ],',
        '    "internal_linking": [',
        '      { "source_description": "Your [page type] about [topic]", "anchor_text": "...", "reason": "..." }',
        '    ],',
        '    "length_check": {',
        f'      "title_length": {len(audit.get("title_tag") or "")},',
        f'      "title_ok": {str(len(audit.get("title_tag") or "") <= 60).lower()},',
        '      "title_issue": "Too long — trim to 60 chars" or "",',
        f'      "meta_length": {len(audit.get("meta_description") or "")},',
        f'      "meta_ok": {str(len(audit.get("meta_description") or "") <= 160).lower()},',
        '      "meta_issue": "Too long — trim to 160 chars" or ""',
        '    }',
        '  },',
        "",
        "  // SECTION 5 — priority scoring",
        '  "priority_score": {',
        f'    "current_position": "{position}",',
        '    "opportunity_size": "High|Med|Low",',
        '    "opportunity_reason": "Explain: e.g. Position X + volume Y/mo = top-3 achievable with on-page fix",',
        '    "estimated_effort": "S|M|L",',
        '    "effort_reason": "S=title+meta+H-tags only | M=content additions | L=page rebuild",',
        '    "quick_win_available": true,',
        '    "quick_win_description": "One specific action: e.g. Move keyword to first 5 words of title",',
        '    "recommended_action": "Optimize existing|Expand content|Create new supporting page",',
        '    "action_detail": "Numbered steps with time estimate"',
        '  }',
        "}",
    ]

    return "\n".join(lines)


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_response(text: str, url, keyword, flags, page_data) -> dict:
    # Strip markdown code fences
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.DOTALL)
    json_str = m.group(1).strip() if m else text

    # Extract outermost JSON object
    if not m:
        start = json_str.find("{")
        end   = json_str.rfind("}")
        if start != -1 and end > start:
            json_str = json_str[start:end + 1]

    for candidate in [json_str, _repair_json(json_str)]:
        try:
            result = json.loads(candidate)
            result.setdefault("_flags", flags)
            result["_raw_data"] = page_data
            return result
        except (json.JSONDecodeError, ValueError):
            continue

    return {
        "url": url, "target_keyword": keyword,
        "flags": flags + ["JSON parse error"],
        "error": "Could not parse Kimi response as JSON",
        "raw_response_snippet": text[:800],
        "_raw_data": page_data,
    }


def _repair_json(s: str) -> str:
    """Remove trailing commas before } or ] — common LLM mistake."""
    return re.sub(r",\s*([}\]])", r"\1", s)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _empty_audit() -> dict:
    return {
        "scrape_status": "skipped",
        "title_tag": "", "meta_description": "", "h1": "",
        "h2s": [], "h3s": [], "word_count": 0,
        "page_type_inferred": "unknown", "schema_markup": "none",
    }


def _empty_serp() -> dict:
    return {
        "keyword": "", "top_results": [], "top_urls": [],
        "paa_questions": [], "serp_features": [],
        "featured_snippet": None, "related_searches": [],
        "title_patterns": [], "total_results": "unknown",
        "error": "No keyword provided",
    }


def _api_error(url, keyword, flags, page_data, msg) -> dict:
    return {
        "url": url, "target_keyword": keyword,
        "flags": flags + [f"API ERROR: {msg}"],
        "error": msg,
        "_raw_data": page_data,
    }
