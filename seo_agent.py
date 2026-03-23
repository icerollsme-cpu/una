"""
SEO Optimization Agent
Uses Claude claude-opus-4-6 with web_search and web_fetch server-side tools to
perform complete 5-section SEO audits for each page in the input sheet.
"""

import anthropic
import json
import re
import time
from typing import Optional

SYSTEM_PROMPT = """You are a senior SEO strategist. You perform comprehensive SEO audits by:
1. Fetching live pages with web_fetch to extract exact on-page elements
2. Researching SERPs with web_search to understand intent and competition
3. Analyzing the top 5 competitor pages via web_fetch
4. Producing structured, actionable recommendations

Rules:
- ALWAYS use web_fetch and web_search before making any recommendations
- Be specific — never say "improve keyword density"; say exactly where and what to change
- Rationale must reference: search intent alignment, E-E-A-T signal, keyword placement, CTR improvement, or featured snippet eligibility
- All recommendations must be executable by a content editor; mark developer tasks [DEV]
- Return ONLY a single valid JSON object — no text before or after
"""


class SEOAgent:
    def __init__(self):
        self.client = anthropic.Anthropic()
        self.model = "claude-opus-4-6"
        self.tools = [
            {"type": "web_search_20260209", "name": "web_search"},
            {"type": "web_fetch_20260209", "name": "web_fetch"},
        ]

    def analyze_page(self, page_data: dict) -> dict:
        """Run a full 5-section SEO audit for a single page."""
        url = page_data.get("Page URL", "").strip()
        keyword = page_data.get("Target Keyword (primary)", "").strip()
        position = page_data.get("Current Position (avg)", "N/A")
        volume = page_data.get("Monthly Search Volume", "N/A")
        difficulty = page_data.get("Keyword Difficulty (0-100)", "N/A")
        page_type = page_data.get("Page Type", "").strip()

        flags = []
        if not url:
            flags.append("MISSING: Page URL")
        if not keyword:
            flags.append("MISSING: Target Keyword")
        if not page_type:
            flags.append("MISSING: Page Type (will infer from content)")

        if not url and not keyword:
            return {
                "url": url,
                "target_keyword": keyword,
                "flags": flags,
                "error": "Skipped: both URL and Target Keyword are missing",
                "_raw_data": page_data,
            }

        prompt = self._build_prompt(url, keyword, position, volume, difficulty, page_type, flags)
        messages = [{"role": "user", "content": prompt}]

        final_message = None
        response_text = ""
        max_iterations = 6  # handle pause_turn continuations

        for iteration in range(max_iterations):
            try:
                with self.client.messages.stream(
                    model=self.model,
                    max_tokens=16000,
                    thinking={"type": "adaptive"},
                    tools=self.tools,
                    messages=messages,
                    system=SYSTEM_PROMPT,
                ) as stream:
                    print()
                    for chunk in stream.text_stream:
                        print(chunk, end="", flush=True)
                        response_text += chunk
                    final_message = stream.get_final_message()

                if final_message.stop_reason == "pause_turn":
                    # Server-side tool loop hit its iteration limit — re-send to continue
                    messages.append({"role": "assistant", "content": final_message.content})
                    response_text = ""  # reset; we'll accumulate the continuation
                    print(f"\n[Continuing after pause_turn, iteration {iteration + 2}...]")
                    continue
                else:
                    break

            except anthropic.RateLimitError:
                wait = 2 ** (iteration + 1)
                print(f"\n[Rate limited — waiting {wait}s before retry...]")
                time.sleep(wait)
            except anthropic.BadRequestError as e:
                print(f"\n[Bad request: {e}]")
                return {
                    "url": url,
                    "target_keyword": keyword,
                    "flags": flags + [f"API BadRequest: {e}"],
                    "error": str(e),
                    "_raw_data": page_data,
                }
            except anthropic.APIError as e:
                print(f"\n[API error: {e}]")
                break

        if not final_message:
            return {
                "url": url,
                "target_keyword": keyword,
                "flags": flags + ["FAILED: No API response received"],
                "error": "No API response received after retries",
                "_raw_data": page_data,
            }

        # Extract full text from the final response (skip thinking blocks)
        full_text = ""
        for block in final_message.content:
            if getattr(block, "type", None) == "text":
                full_text += block.text

        return self._parse_response(full_text, url, keyword, flags, page_data)

    # ── Private helpers ──────────────────────────────────────────────────────

    def _parse_response(
        self,
        text: str,
        url: str,
        keyword: str,
        flags: list,
        page_data: dict,
    ) -> dict:
        if not text.strip():
            return {
                "url": url,
                "target_keyword": keyword,
                "flags": flags + ["EMPTY response from model"],
                "error": "Empty response",
                "_raw_data": page_data,
            }

        # 1. Strip markdown code fences if present
        code_fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.DOTALL)
        if code_fence:
            json_str = code_fence.group(1).strip()
        else:
            # 2. Extract outermost JSON object
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end > start:
                json_str = text[start : end + 1]
            else:
                json_str = text

        # 3. Attempt parse, then try light fixes
        for attempt_str in [json_str, self._repair_json(json_str)]:
            try:
                result = json.loads(attempt_str)
                result.setdefault("_flags", flags)
                result["_raw_data"] = page_data
                return result
            except (json.JSONDecodeError, ValueError):
                continue

        return {
            "url": url,
            "target_keyword": keyword,
            "flags": flags + ["JSON parse error"],
            "error": "Could not parse JSON response",
            "raw_response_snippet": text[:800],
            "_raw_data": page_data,
        }

    @staticmethod
    def _repair_json(s: str) -> str:
        """Remove trailing commas before } or ] (common Claude mistake)."""
        return re.sub(r",\s*([}\]])", r"\1", s)

    def _build_prompt(
        self,
        url: str,
        keyword: str,
        position,
        volume,
        difficulty,
        page_type: str,
        flags: list,
    ) -> str:
        flag_note = ""
        if flags:
            flag_note = "\n**⚠️ FLAGS:** " + " | ".join(flags) + "\n"

        url_instruction = (
            f"Use web_fetch to retrieve: {url}"
            if url
            else "No URL provided — skip the live page fetch; set scrape_status to 'no_url'"
        )

        kw_instruction = (
            f'Use web_search to search: {keyword}'
            if keyword
            else "No keyword provided — skip SERP analysis; flag as MISSING"
        )

        return f"""Perform a complete SEO audit using the data below. Follow every step in order.

## PAGE INPUT DATA
- **URL:** {url or "[NOT PROVIDED]"}
- **Target Keyword:** {keyword or "[NOT PROVIDED]"}
- **Current Position (avg):** {position}
- **Monthly Search Volume:** {volume}
- **Keyword Difficulty:** {difficulty}/100
- **Page Type:** {page_type or "Unknown (infer from URL/content)"}
{flag_note}
---

## RESEARCH STEPS — COMPLETE ALL IN ORDER

**STEP 1 — Live Page Audit**
{url_instruction}
Extract EXACTLY:
- The <title> tag content
- The <meta name="description"> content
- The <h1> content
- All <h2> tags (list them all)
- All <h3> tags (list them all)
- Approximate word count from body text length
- Any JSON-LD or microdata schema markup detected
If fetch fails, set scrape_status to "failed" and note it in flags.

**STEP 2 — SERP Analysis**
{kw_instruction}
From the results page, identify:
- Dominant search intent (informational / navigational / commercial / transactional)
- SERP features visible (Featured Snippet, People Also Ask, Image Pack, Video Carousel, Local Pack)
- The average title tag pattern of the top 10 results
- The winning content format (listicle, how-to guide, comparison, tool/calculator, video)

**STEP 3 — Competitor Content Analysis**
Use web_fetch on the top 3–5 ranking URLs (from Step 2 results).
For each competitor page, note their H2 structure and main subtopics covered.
Identify subtopics/angles that appear in 3+ competitors but are NOT covered on the target page.
Also note any Reddit/Quora/gov/edu sources in the top 20 results — these reveal unmet audience questions.

**STEP 4 — Keyword Expansion**
Based on SERP data and competitor pages:
- Identify 5–10 semantic/LSI keywords to weave into existing content
- Collect 3–5 PAA questions shown on the SERP (use the EXACT phrasing Google shows)
- Identify the best featured snippet opportunity: the query + best answer format

**STEP 5 — Schema & Technical Audit**
- Recommend schema types appropriate for this page type
- List 3–5 specific FAQ Q&A pairs to mark up with FAQPage schema
- Suggest 2–3 other pages on the site that should link TO this page (describe them by topic/type and provide anchor text)
- Calculate the character length of current title tag and meta description; flag if title > 60 chars or meta > 160 chars

---

## OUTPUT

Return a SINGLE valid JSON object. No text before or after it.
Fill every field with real data from your research. Do not use placeholder values.

{{
  "url": "{url}",
  "target_keyword": "{keyword}",
  "flags": [],
  "scrape_status": "success",
  "audit": {{
    "title_tag": "exact scraped title here",
    "meta_description": "exact scraped meta description here",
    "h1": "exact scraped H1 here",
    "h2s": ["H2 number one", "H2 number two", "H2 number three"],
    "h3s": ["H3 number one", "H3 number two"],
    "word_count": 1500,
    "page_type_inferred": "blog",
    "schema_markup": "none"
  }},
  "on_page": {{
    "search_intent": "informational",
    "serp_features": ["People Also Ask", "Featured Snippet"],
    "title_tag_pattern": "Describe the pattern: e.g. [Number] Best [Keyword] for [Year] | [Brand]",
    "winning_content_format": "listicle",
    "recommendations": [
      {{
        "element": "Title Tag",
        "current": "current title here",
        "recommended": "new optimized title here",
        "rationale": "Keyword placement: primary keyword moved to first 5 words; CTR improvement: adds year and number hook matching SERP pattern"
      }},
      {{
        "element": "Meta Description",
        "current": "current meta here",
        "recommended": "new meta with CTA and secondary keyword — under 160 chars",
        "rationale": "CTR improvement: adds action verb, addresses search intent, includes secondary keyword [X]"
      }},
      {{
        "element": "H1",
        "current": "current H1 here",
        "recommended": "new H1 here",
        "rationale": "Search intent alignment: matches informational intent; keyword placement in first 3 words"
      }},
      {{
        "element": "H2s",
        "current": "Current H2 1 | Current H2 2 | Current H2 3",
        "recommended": "New H2 1 | New H2 2 | New H2 3 | New H2 4",
        "rationale": "Subtopic coverage: adds [missing topic X] and [missing topic Y] covered by 4+ competitors; E-E-A-T signal: adds expert comparison section"
      }},
      {{
        "element": "H3s",
        "current": "Current H3 1 | Current H3 2",
        "recommended": "New H3 1 | New H3 2 | New H3 3",
        "rationale": "Featured snippet eligibility: restructured to directly answer PAA question [exact question]"
      }}
    ]
  }},
  "content_gaps": [
    {{
      "missing_subtopic": "Name the missing topic or angle",
      "covered_by": "https://competitor1.com/page OR 'Reddit thread: [title]' OR '3+ top competitors'",
      "suggested_section_title": "Exact H2 title to add to the page",
      "priority": "High"
    }},
    {{
      "missing_subtopic": "Another missing topic",
      "covered_by": "1-2 competitors",
      "suggested_section_title": "Exact H2 title",
      "priority": "Med"
    }}
  ],
  "keyword_expansion": {{
    "semantic_keywords": [
      {{
        "keyword": "semantic keyword phrase",
        "where_to_place": "H2 introduction paragraph / Body section 2 / Conclusion",
        "intent": "informational"
      }}
    ],
    "paa_targets": [
      {{
        "question": "Exact People Also Ask question from Google SERP",
        "recommended_heading": "H2 or H3",
        "answer_format": "paragraph"
      }}
    ],
    "featured_snippet_opportunity": {{
      "trigger_query": "exact query that shows a featured snippet",
      "current_status": "not in snippet / ranking position X",
      "recommended_format": "numbered list",
      "implementation": "Add a 40–60 word numbered list directly answering [query] under H2 [title]. Label the H3 as 'How to [X]: Step-by-Step' to match snippet format."
    }}
  }},
  "schema_technical": {{
    "recommended_schema_types": ["FAQPage", "Article", "BreadcrumbList"],
    "faq_qa_pairs": [
      {{
        "question": "Question from the page content",
        "answer": "Concise answer under 50 words, suitable for FAQ schema markup"
      }}
    ],
    "internal_linking": [
      {{
        "source_description": "Your [page type] about [topic] — e.g. Your buying guide for coffee grinders",
        "anchor_text": "descriptive anchor text to use",
        "reason": "Why this link is topically relevant and benefits both pages"
      }}
    ],
    "length_check": {{
      "title_length": 55,
      "title_ok": true,
      "title_issue": "",
      "meta_length": 148,
      "meta_ok": true,
      "meta_issue": ""
    }}
  }},
  "priority_score": {{
    "current_position": "{position}",
    "opportunity_size": "High",
    "opportunity_reason": "Explain why: e.g. Position {position} with {volume}/mo volume — top-3 achievable with targeted optimizations",
    "estimated_effort": "M",
    "effort_reason": "S = title+meta+H-tags only | M = content additions needed (500–1000 words) | L = significant rebuild required",
    "quick_win_available": true,
    "quick_win_description": "One specific action: e.g. Move primary keyword to first 5 words of title tag — currently at position 9",
    "recommended_action": "Optimize existing",
    "action_detail": "Specific next steps: e.g. (1) Rewrite title tag. (2) Add H2 section on [topic]. (3) Insert FAQ schema. Estimated time: 2 hours."
  }}
}}
"""
