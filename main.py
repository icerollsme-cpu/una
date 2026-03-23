#!/usr/bin/env python3
"""
SEO Optimization Agent — Entry Point

Powered by: Kimi K2 (NVIDIA NIM) · SerpAPI · scrape.do · scrapestack · ScrapeGraphAI

Usage:
    python main.py input.csv
    python main.py input.csv -o my_report.xlsx
    python main.py input.csv --delay 3 --pages 1,3,5  # only pages 1, 3, 5

Input CSV columns (order-independent, case-sensitive headers):
    Page URL
    Current Position (avg)
    Target Keyword (primary)
    Monthly Search Volume
    Keyword Difficulty (0-100)
    Page Type   (blog / landing / product / category)

Output:
    Excel workbook with a Summary sheet + one detailed sheet per keyword.
"""

import argparse
import csv
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from seo_agent import SEOAgent
from output_generator import OutputGenerator

load_dotenv()  # load ANTHROPIC_API_KEY from .env if present


# ── CSV loader ────────────────────────────────────────────────────────────────

def load_csv(filepath: str) -> list[dict]:
    rows = []
    with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


# ── Pretty printing ───────────────────────────────────────────────────────────

def banner(msg: str, width: int = 62):
    print("\n" + "=" * width)
    print(f"  {msg}")
    print("=" * width)


def page_header(idx: int, total: int, url: str, keyword: str):
    print(f"\n{'─' * 62}")
    print(f"  [{idx}/{total}]  {keyword or '(no keyword)'}")
    print(f"  URL: {url or '(no URL)'}")
    print("─" * 62)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SEO Optimization Agent — produces a full audit Excel report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Input CSV file path")
    parser.add_argument(
        "-o", "--output",
        default="seo_report.xlsx",
        help="Output Excel file path (default: seo_report.xlsx)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to wait between pages to avoid rate limits (default: 2)",
    )
    parser.add_argument(
        "--pages",
        type=str,
        default="",
        help="Comma-separated 1-based page indices to process (e.g. 1,3,5). Default: all",
    )
    args = parser.parse_args()

    # ── Validate input file
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"✗  Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    pages = load_csv(args.input)
    if not pages:
        print("✗  Input CSV is empty.", file=sys.stderr)
        sys.exit(1)

    # ── Apply page filter
    if args.pages.strip():
        try:
            indices = {int(i.strip()) for i in args.pages.split(",") if i.strip()}
            pages = [p for idx, p in enumerate(pages, 1) if idx in indices]
        except ValueError:
            print("✗  --pages must be comma-separated integers (e.g. 1,3,5)", file=sys.stderr)
            sys.exit(1)

    total = len(pages)
    banner(f"SEO Optimization Agent  ·  {total} page{'s' if total != 1 else ''} to analyze")
    print(f"  Input : {args.input}")
    print(f"  Output: {args.output}")
    print(f"  Delay : {args.delay}s between pages")

    agent     = SEOAgent()
    generator = OutputGenerator(args.output)
    results   = []

    for idx, page in enumerate(pages, 1):
        url     = page.get("Page URL", "").strip()
        keyword = page.get("Target Keyword (primary)", "").strip()
        page_header(idx, total, url, keyword)

        result = agent.analyze_page(page)
        results.append(result)

        if result.get("error"):
            print(f"\n  ⚠  {result['error']}")
        else:
            opp    = result.get("priority_score", {}).get("opportunity_size", "")
            effort = result.get("priority_score", {}).get("estimated_effort", "")
            qw     = result.get("priority_score", {}).get("quick_win_available")
            print(f"\n  ✓  Done — Opportunity: {opp}  Effort: {effort}  Quick Win: {'Yes' if qw else 'No'}")

        if idx < total:
            print(f"\n  ⏱  Waiting {args.delay}s...")
            time.sleep(args.delay)

    # ── Write Excel report
    banner("Generating Excel report…")
    generator.generate(results)
    print(f"\n  ✓  Report saved → {args.output}")
    print()

    # ── Summary stats
    errors  = sum(1 for r in results if r.get("error"))
    success = total - errors
    print(f"  Pages analyzed : {success}/{total}")
    if errors:
        print(f"  Errors         : {errors} page(s) had issues — check the 'Flags' column")
    print()


if __name__ == "__main__":
    main()
