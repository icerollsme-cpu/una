"""
orchestrator.py — End-to-end autonomous content marketing pipeline.

Input:  A website or app URL
Output: Traffic-driving posts across all platforms, tracked by GA4

Pipeline stages:
  1. site_analyzer   → detect topics, products, services, target audience
  2. scraper_agent   → scrape top-performing competitor posts per topic
  3. content_agent   → generate text/image/graph/video assets
  4. social_poster   → post with UTM tracking links back to the site
  5. analytics_agent → log posts, later pull GA4 data, get improvement brief
  6. (loop)          → feed improvement brief back into stage 2

Usage examples:

  # Full pipeline from a website URL
  python orchestrator.py run https://mysite.com --platforms tiktok instagram youtube

  # Generate + post for a specific topic only
  python orchestrator.py run https://mysite.com --topics "3d printing" --post

  # Pull performance report and improvement recommendations
  python orchestrator.py report https://mysite.com --days 14

  # Full autonomous loop: run pipeline, wait 48h, analyse, re-run improved
  python orchestrator.py loop https://mysite.com --platforms tiktok youtube --iterations 3
"""

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from analytics_agent import (
    PostLog, GA4Client, build_utm_url, get_performance_report,
)
from content_agent import create_content_package
from scraper_agent import scrape_and_analyze, SCRAPERS
from site_analyzer import analyze_site
from social_poster import post_to_platforms

log = logging.getLogger(__name__)

DEFAULT_PLATFORMS = ["tiktok", "instagram", "youtube", "twitter", "linkedin",
                     "reddit", "bluesky", "threads"]


# ---------------------------------------------------------------------------
# Single-platform, single-topic run
# ---------------------------------------------------------------------------

def run_topic(
    platform: str,
    topic: str,
    site_brief: dict,
    content_types: list[str],
    post: bool,
    improvement_brief: Optional[dict] = None,
    subreddit: Optional[str] = None,
) -> dict:
    """
    Run the full pipeline for one platform + topic combination.

    Returns a result dict with generated files and post outcome.
    """
    site_url = site_brief.get("url", "")
    utm_campaign = f"{topic.replace(' ', '_')[:30]}_{datetime.utcnow().strftime('%Y%m%d')}"

    # ── Stage 1: Scrape competitor posts for this topic ───────────────────────
    log.info("[%s / %s] Scraping top posts…", platform, topic)
    try:
        scrape_result = scrape_and_analyze(
            platform=platform,
            topic=topic,
            limit=10,
            subreddit=subreddit,
        )
        insights = scrape_result.get("insights", {})
        content_briefs = scrape_result.get("content_briefs", [])
    except Exception as exc:
        log.warning("Scrape failed for %s/%s: %s — using site brief only", platform, topic, exc)
        insights = {}
        content_briefs = []

    # Inject site-level context into insights so content drives traffic
    insights["site_name"] = site_brief.get("site_name", "")
    insights["site_url"] = site_url
    insights["target_audience"] = site_brief.get("target_audience", "")
    insights["unique_selling_points"] = site_brief.get("unique_selling_points", [])
    insights["cta_phrases"] = site_brief.get("cta_phrases", [])

    # Apply improvement brief if available (feedback loop)
    if improvement_brief:
        platform_insight = improvement_brief.get("platform_insights", {}).get(platform, "")
        if platform_insight:
            insights["improvement_notes"] = platform_insight
        recs = improvement_brief.get("recommendations", [])
        if recs:
            insights["improvement_recommendations"] = recs

    # ── Stage 2: Generate content ─────────────────────────────────────────────
    log.info("[%s / %s] Generating content (%s)…", platform, topic,
             ", ".join(content_types))

    # Build UTM-tagged site link and inject into briefs
    utm_link = build_utm_url(site_url, platform, topic, utm_campaign) if site_url else ""
    for brief in content_briefs:
        if utm_link:
            brief["site_link"] = utm_link
            brief["traffic_hook"] = (
                f"Drive traffic to {site_brief.get('site_name', 'the site')} — "
                f"link in bio/description: {utm_link}"
            )

    content = create_content_package(
        platform=platform,
        topic=topic,
        insights=insights,
        content_briefs=content_briefs,
        content_types=content_types,
    )

    # Inject UTM link into the generated text post
    text_file = content["files"].get("text")
    post_text = f"Check out {site_brief.get('site_name', topic)}: {utm_link}"
    if text_file and Path(text_file).exists():
        text_data = json.loads(Path(text_file).read_text())
        full_post = text_data.get("full_post", "")
        if utm_link and utm_link not in full_post:
            full_post = f"{full_post}\n\n🔗 {utm_link}"
        post_text = full_post or post_text

    # ── Stage 3: Post ─────────────────────────────────────────────────────────
    post_results: list[dict] = []
    if post:
        log.info("[%s / %s] Posting…", platform, topic)
        image_path = content["files"].get("image")
        video_path = content["files"].get("video")

        post_kwargs: dict = {}
        if video_path and platform in ("tiktok", "instagram", "youtube"):
            post_kwargs["video_path"] = video_path
        if platform == "youtube":
            post_kwargs["title"] = f"{topic.title()} — {site_brief.get('site_name', '')}"
        if platform == "reddit":
            post_kwargs["subreddit"] = subreddit or "general"
            post_kwargs["title"] = topic[:100]

        try:
            post_results = post_to_platforms(
                text=post_text,
                platforms=[platform],
                image_path=image_path if not video_path else None,
                **post_kwargs,
            )
        except Exception as exc:
            log.error("[%s / %s] Post failed: %s", platform, topic, exc)
            post_results = [{"platform": platform, "error": str(exc)}]

    # ── Stage 4: Log the post ─────────────────────────────────────────────────
    post_log = PostLog()
    for result in post_results:
        if "error" not in result:
            post_log.log(
                platform=platform,
                topic=topic,
                site_url=site_url,
                utm_campaign=utm_campaign,
                post_result=result,
                content_type="video" if content["files"].get("video") else "image",
                files=content["files"],
            )

    return {
        "platform": platform,
        "topic": topic,
        "utm_campaign": utm_campaign,
        "utm_link": utm_link,
        "generated_files": content["files"],
        "post_results": post_results,
    }


# ---------------------------------------------------------------------------
# Full site pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    site_url: str,
    platforms: Optional[list[str]] = None,
    content_types: Optional[list[str]] = None,
    topics_override: Optional[list[str]] = None,
    max_topics: int = 3,
    post: bool = False,
    improvement_brief: Optional[dict] = None,
    site_brief: Optional[dict] = None,
    subreddit: Optional[str] = None,
) -> dict:
    """
    Full pipeline starting from a website URL.

      1. Analyse the site (or use cached brief)
      2. For each topic × each platform: scrape → create → post → log
      3. Return summary of all results

    Args:
        site_url:          The website/app to drive traffic to
        platforms:         Which platforms to post on (default: all)
        content_types:     text, image, graph, video (default: all)
        topics_override:   Skip site analysis and use these topics directly
        max_topics:        Max topics to process per run (default 3)
        post:              Whether to actually post (False = dry run)
        improvement_brief: Output from a previous get_performance_report() call
        site_brief:        Pre-computed site brief (skips site analysis)
        subreddit:         For Reddit posts

    Returns:
        Summary dict with site_brief, all results, and total posts made.
    """
    if content_types is None:
        content_types = ["text", "image", "graph", "video"]
    if platforms is None:
        platforms = DEFAULT_PLATFORMS

    # ── Stage 0: Analyse the site ─────────────────────────────────────────────
    if site_brief is None:
        log.info("=== Stage 0: Analysing site %s ===", site_url)
        site_brief = analyze_site(site_url)

    # Determine topics to cover
    if topics_override:
        topics = topics_override[:max_topics]
    else:
        # Use content angles first, then fall back to topics list
        angles = site_brief.get("content_angles", [])
        angle_topics = [a["angle"] for a in angles[:max_topics]]
        if angle_topics:
            topics = angle_topics
        else:
            topics = site_brief.get("topics", [])[:max_topics]

    if not topics:
        topics = [site_brief.get("site_name", "general")]

    log.info("Topics to cover: %s", topics)
    log.info("Platforms: %s", platforms)

    # ── Stages 1–4: per topic × per platform ─────────────────────────────────
    all_results: list[dict] = []
    for topic in topics:
        for platform in platforms:
            try:
                result = run_topic(
                    platform=platform,
                    topic=topic,
                    site_brief=site_brief,
                    content_types=content_types,
                    post=post,
                    improvement_brief=improvement_brief,
                    subreddit=subreddit,
                )
                all_results.append(result)
            except Exception as exc:
                log.error("run_topic failed [%s/%s]: %s", platform, topic, exc)
                all_results.append({
                    "platform": platform, "topic": topic, "error": str(exc)
                })

    total_posted = sum(
        1 for r in all_results
        if r.get("post_results") and not any(
            "error" in p for p in r["post_results"]
        )
    )

    return {
        "site_url": site_url,
        "site_brief": site_brief,
        "topics": topics,
        "platforms": platforms,
        "results": all_results,
        "total_posted": total_posted,
        "ran_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Improvement loop — run → wait → analyse → run improved
# ---------------------------------------------------------------------------

def run_loop(
    site_url: str,
    platforms: Optional[list[str]] = None,
    content_types: Optional[list[str]] = None,
    iterations: int = 3,
    wait_hours: int = 48,
    post: bool = True,
    subreddit: Optional[str] = None,
) -> list[dict]:
    """
    Autonomous improvement loop:
      Iteration 1: Run pipeline (no prior data)
      Wait N hours for posts to accumulate traffic
      Iteration 2+: Pull GA4 data → get improvement brief → run pipeline again

    Args:
        iterations:  Number of pipeline runs (default 3)
        wait_hours:  Hours to wait between runs (default 48)
        post:        Whether to actually post

    Returns list of per-iteration summaries.
    """
    all_runs: list[dict] = []
    site_brief: Optional[dict] = None
    improvement_brief: Optional[dict] = None

    for i in range(iterations):
        log.info("\n%s\nIteration %d / %d\n%s", "=" * 60, i + 1, iterations, "=" * 60)

        result = run_pipeline(
            site_url=site_url,
            platforms=platforms,
            content_types=content_types,
            post=post,
            improvement_brief=improvement_brief,
            site_brief=site_brief,
            subreddit=subreddit,
        )

        # Cache site_brief so we don't re-scrape on every iteration
        site_brief = result["site_brief"]
        all_runs.append(result)

        if i < iterations - 1:
            log.info(
                "Waiting %d hours before next iteration (time for posts to accumulate "
                "traffic)…", wait_hours
            )
            time.sleep(wait_hours * 3600)

            log.info("Pulling performance report…")
            report = get_performance_report(site_url=site_url, days=wait_hours // 24 + 2)
            improvement_brief = report.get("recommendations", {})
            log.info(
                "Improvement brief ready: %d recommendations",
                len(improvement_brief.get("recommendations", []))
            )

    return all_runs


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _print_run_summary(result: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Site: {result.get('site_url', '?')}")
    brief = result.get("site_brief", {})
    print(f"  Brand: {brief.get('site_name', '?')} ({brief.get('industry', '?')})")
    print(f"  Topics: {', '.join(result.get('topics', []))}")
    print(f"  Platforms: {', '.join(result.get('platforms', []))}")
    print(f"  Posts made: {result.get('total_posted', 0)}")
    print("=" * 60)

    for r in result.get("results", []):
        platform = r.get("platform", "?")
        topic = r.get("topic", "?")
        if "error" in r:
            print(f"  ✗ [{platform}/{topic}] {r['error']}")
            continue
        files = r.get("generated_files", {})
        post_res = r.get("post_results", [])
        status = "posted" if post_res and "error" not in post_res[0] else "generated only"
        print(f"  ✓ [{platform}/{topic}] {status} — {list(files.keys())}")
        if r.get("utm_link"):
            print(f"      UTM: {r['utm_link']}")


def _print_report_summary(report: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Performance Report — {report.get('site_url', '?')}")
    print(f"  Period: last {report.get('period_days', '?')} days")
    print("=" * 60)
    print(f"  Posts analysed : {report.get('post_count', 0)}")
    print(f"  GA4 sessions   : {report.get('total_ga_sessions', 'N/A')}")
    print(f"  GA4 conversions: {report.get('total_ga_conversions', 'N/A')}")

    recs = report.get("recommendations", {})
    top = recs.get("top_performers", [])
    if top:
        print("\n  Top performers:")
        for p in top[:3]:
            print(f"    [{p.get('platform')}/{p.get('topic')}] {p.get('reason', '')}")

    for rec in recs.get("recommendations", [])[:5]:
        print(f"  → {rec}")

    pages = report.get("top_landing_pages", [])
    if pages:
        print("\n  Top landing pages from social:")
        for pg in pages[:5]:
            print(f"    {pg.get('page')} — {pg.get('sessions')} sessions "
                  f"({pg.get('source')})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    platform_choices = list(SCRAPERS.keys()) + ["youtube"]

    parser = argparse.ArgumentParser(
        description="Autonomous content marketing pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── run ──────────────────────────────────────────────────────────────────
    run_p = sub.add_parser("run", help="Run the full pipeline once")
    run_p.add_argument("site_url", help="Website/app URL to drive traffic to")
    run_p.add_argument("--platforms", nargs="+", choices=platform_choices,
                       default=DEFAULT_PLATFORMS, metavar="PLATFORM")
    run_p.add_argument("--topics", nargs="+", dest="topics_override",
                       metavar="TOPIC",
                       help="Override auto-detected topics")
    run_p.add_argument("--max-topics", type=int, default=3)
    run_p.add_argument("--types", nargs="+",
                       default=["text", "image", "graph", "video"],
                       choices=["text", "image", "graph", "video"])
    run_p.add_argument("--post", action="store_true",
                       help="Actually post (default: dry run)")
    run_p.add_argument("--subreddit", help="Subreddit for Reddit posts")
    run_p.add_argument("--site-brief", metavar="FILE",
                       help="Load pre-saved site brief JSON")
    run_p.add_argument("--improvement-brief", metavar="FILE",
                       help="Load pre-saved improvement brief JSON")
    run_p.add_argument("--output", "-o", metavar="FILE",
                       help="Save results JSON to file")

    # ── report ───────────────────────────────────────────────────────────────
    rep_p = sub.add_parser("report", help="Pull GA4 data and get improvement recs")
    rep_p.add_argument("site_url")
    rep_p.add_argument("--days", type=int, default=14)
    rep_p.add_argument("--no-ga", action="store_true",
                       help="Skip GA4 fetch (use cached data)")
    rep_p.add_argument("--output", "-o", metavar="FILE")

    # ── loop ─────────────────────────────────────────────────────────────────
    loop_p = sub.add_parser("loop", help="Run pipeline repeatedly, improving each time")
    loop_p.add_argument("site_url")
    loop_p.add_argument("--platforms", nargs="+", choices=platform_choices,
                        default=DEFAULT_PLATFORMS, metavar="PLATFORM")
    loop_p.add_argument("--types", nargs="+",
                        default=["text", "image", "graph", "video"],
                        choices=["text", "image", "graph", "video"])
    loop_p.add_argument("--iterations", type=int, default=3)
    loop_p.add_argument("--wait-hours", type=int, default=48)
    loop_p.add_argument("--post", action="store_true")
    loop_p.add_argument("--subreddit")

    args = parser.parse_args()

    if args.command == "run":
        site_brief = None
        improvement_brief = None
        if getattr(args, "site_brief", None):
            with open(args.site_brief) as fh:
                site_brief = json.load(fh)
        if getattr(args, "improvement_brief", None):
            with open(args.improvement_brief) as fh:
                improvement_brief = json.load(fh)

        result = run_pipeline(
            site_url=args.site_url,
            platforms=args.platforms,
            content_types=args.types,
            topics_override=getattr(args, "topics_override", None),
            max_topics=args.max_topics,
            post=args.post,
            improvement_brief=improvement_brief,
            site_brief=site_brief,
            subreddit=getattr(args, "subreddit", None),
        )
        _print_run_summary(result)
        if args.output:
            with open(args.output, "w") as fh:
                # Don't serialise large file paths into the output
                out = {k: v for k, v in result.items() if k != "site_brief"}
                json.dump(out, fh, indent=2)
            print(f"\nResults saved to {args.output}")

    elif args.command == "report":
        report = get_performance_report(
            site_url=args.site_url,
            days=args.days,
            update_ga=not args.no_ga,
        )
        _print_report_summary(report)
        if args.output:
            with open(args.output, "w") as fh:
                json.dump(report, fh, indent=2)
            print(f"\nReport saved to {args.output}")

    elif args.command == "loop":
        all_runs = run_loop(
            site_url=args.site_url,
            platforms=args.platforms,
            content_types=args.types,
            iterations=args.iterations,
            wait_hours=args.wait_hours,
            post=args.post,
            subreddit=getattr(args, "subreddit", None),
        )
        for i, run in enumerate(all_runs, 1):
            print(f"\n{'─' * 40}  Iteration {i}  {'─' * 40}")
            _print_run_summary(run)
