#!/usr/bin/env python3
"""
SEO Crawler Agent — Entry Point

Crawls any website via its XML sitemap, identifies every page's topic and
primary keyword, scores SEO opportunities, and suggests optimized titles and
meta descriptions within Google's character limits (title ≤ 60, meta ≤ 160).

Usage:
    python crawler_main.py https://example.com
    python crawler_main.py https://example.com -o report.xlsx
    python crawler_main.py https://example.com --max-pages 50 --delay 1.5
    python crawler_main.py https://example.com --only-opportunities
    python crawler_main.py https://example.com --min-score Med

Output:
    Excel workbook with:
      • Dashboard    — all pages, color-coded by opportunity score
      • High Opportunities — filtered quick-action list
      • Per-page sheets    — detailed analysis for each High-score page
"""

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from crawler_agent import CrawlerAgent
from crawler_output import CrawlerOutputGenerator

load_dotenv()

_SCORE_ORDER = {"High": 0, "Med": 1, "Low": 2, "None": 3, "N/A": 4}


def banner(msg: str, width: int = 66):
    print("\n" + "=" * width)
    print(f"  {msg}")
    print("=" * width)


def main():
    parser = argparse.ArgumentParser(
        description="SEO Crawler Agent — title & meta optimization for any site",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "site_url",
        help="The website to crawl (homepage URL or any URL on the site)",
    )
    parser.add_argument(
        "-o", "--output",
        default="crawler_report.xlsx",
        help="Output Excel file path (default: crawler_report.xlsx)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=200,
        metavar="N",
        help="Maximum number of pages to analyze (default: 200, 0 = unlimited)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECS",
        help="Seconds to wait between page requests (default: 1.0)",
    )
    parser.add_argument(
        "--only-opportunities",
        action="store_true",
        help="Skip pages with no detected opportunity (faster run, smaller report)",
    )
    parser.add_argument(
        "--min-score",
        choices=["High", "Med", "Low"],
        default=None,
        metavar="SCORE",
        help="Filter output to pages at or above this opportunity level (High/Med/Low)",
    )
    parser.add_argument(
        "--title-max",
        type=int,
        default=60,
        metavar="CHARS",
        help="Title character limit for suggestions (default: 60)",
    )
    parser.add_argument(
        "--meta-max",
        type=int,
        default=160,
        metavar="CHARS",
        help="Meta description character limit for suggestions (default: 160)",
    )

    args = parser.parse_args()

    # Propagate custom limits to the agent module
    if args.title_max != 60 or args.meta_max != 160:
        import crawler_agent as _ca
        _ca.TITLE_MAX = args.title_max
        _ca.META_MAX  = args.meta_max
        import crawler_output as _co
        _co.TITLE_MAX = args.title_max
        _co.META_MAX  = args.meta_max

    # ── Validate output path
    output_path = Path(args.output)
    if output_path.suffix.lower() != ".xlsx":
        output_path = output_path.with_suffix(".xlsx")

    banner(
        f"SEO Crawler Agent  ·  {args.site_url}"
    )
    print(f"  Output      : {output_path}")
    print(f"  Max pages   : {args.max_pages or 'unlimited'}")
    print(f"  Delay       : {args.delay}s between requests")
    print(f"  Title limit : ≤ {args.title_max} chars")
    print(f"  Meta limit  : ≤ {args.meta_max} chars")
    if args.only_opportunities:
        print(f"  Mode        : opportunities only (skipping clean pages)")
    if args.min_score:
        print(f"  Min score   : {args.min_score}")

    start = time.time()

    # ── Run the crawler
    try:
        agent = CrawlerAgent()
    except EnvironmentError as e:
        print(f"\n✗  {e}", file=sys.stderr)
        sys.exit(1)

    results = agent.crawl_site(
        site_url=args.site_url,
        max_pages=args.max_pages,
        scrape_delay=args.delay,
        ai_delay=args.delay,
        only_opportunities=args.only_opportunities,
    )

    if not results:
        print("\n✗  No results — check the site URL and try again.")
        sys.exit(1)

    # ── Apply min-score filter (for output only, not analysis)
    filtered = results
    if args.min_score:
        min_idx = _SCORE_ORDER[args.min_score]
        filtered = [
            r for r in results
            if _SCORE_ORDER.get(r.get("opportunity_score", "N/A"), 99) <= min_idx
        ]
        print(f"\n  Filtered to {len(filtered)}/{len(results)} pages at score ≥ {args.min_score}")

    # ── Generate Excel report
    banner("Generating Excel report…")
    gen = CrawlerOutputGenerator(str(output_path))
    gen.generate(filtered, site_url=args.site_url)

    elapsed = time.time() - start
    banner(f"Done  ·  {elapsed:.0f}s")

    # ── Summary stats
    total  = len(results)
    high   = sum(1 for r in results if r.get("opportunity_score") == "High")
    med    = sum(1 for r in results if r.get("opportunity_score") == "Med")
    low    = sum(1 for r in results if r.get("opportunity_score") == "Low")
    none_  = sum(1 for r in results if r.get("opportunity_score") == "None")
    errors = sum(1 for r in results if r.get("error"))

    print(f"  Pages analyzed  : {total}")
    print(f"  High opportunity: {high}  (immediate action recommended)")
    print(f"  Med opportunity : {med}")
    print(f"  Low opportunity : {low}")
    print(f"  No change needed: {none_}")
    if errors:
        print(f"  Errors          : {errors}  (check Dashboard sheet)")
    print(f"\n  Report saved → {output_path}")
    print()


if __name__ == "__main__":
    main()
