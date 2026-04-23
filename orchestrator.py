"""
orchestrator.py — End-to-end content pipeline coordinator.

  Stage 1 (scraper_agent)  → Scrape top posts for a topic on any platform
  Stage 2 (content_agent)  → Generate text, image, graph, and/or video assets
  Stage 3 (social_poster)  → Optionally post the generated content

Usage examples:

  # Scrape + generate content only (no posting)
  python orchestrator.py tiktok "3d printing"

  # Generate only text and images, then post
  python orchestrator.py instagram coffee --types text image --post

  # Scrape a specific subreddit, generate everything, save insights
  python orchestrator.py reddit "woodworking" --subreddit woodworking \\
      --save-insights woodwork_insights.json --post

  # Re-use saved insights to regenerate content without re-scraping
  python orchestrator.py tiktok "woodworking" \\
      --insights-file woodwork_insights.json --skip-scrape

  # Run for multiple platforms at once
  python orchestrator.py ALL "coffee brewing" --types text image --post
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from scraper_agent import scrape_and_analyze, SCRAPERS
from content_agent import create_content_package
from social_poster import post_to_platforms

log = logging.getLogger(__name__)


def run_pipeline(
    platform: str,
    topic: str,
    scrape_limit: int = 10,
    content_types: Optional[list[str]] = None,
    post: bool = False,
    save_insights: Optional[str] = None,
    insights_file: Optional[str] = None,
    subreddit: Optional[str] = None,
) -> dict:
    """
    Full pipeline for a single platform: scrape → analyse → create → post.

    Args:
        platform:       Platform name (tiktok, instagram, twitter, reddit, youtube, bluesky)
        topic:          Topic/keyword to research and create content for
        scrape_limit:   How many posts to scrape for analysis
        content_types:  Which assets to generate: text, image, graph, video
        post:           Whether to post generated content after creation
        save_insights:  File path to save raw scrape+insights JSON
        insights_file:  Load pre-saved insights instead of scraping (skips Stage 1)
        subreddit:      Reddit-only — target a specific subreddit

    Returns:
        Summary dict with scraped post count, insights, generated files, post results.
    """
    if content_types is None:
        content_types = ["text", "image", "graph", "video"]

    # ── Stage 1: Scrape & analyse ─────────────────────────────────────────────
    if insights_file:
        log.info("Loading pre-saved insights from %s (skipping scrape)", insights_file)
        with open(insights_file) as fh:
            scrape_result = json.load(fh)
        insights = scrape_result.get("insights", {})
        content_briefs = scrape_result.get("content_briefs", [])
        post_count = scrape_result.get("post_count", 0)
    else:
        log.info("=== Stage 1: Scraping %s for '%s' ===", platform, topic)
        scrape_result = scrape_and_analyze(
            platform=platform,
            topic=topic,
            limit=scrape_limit,
            subreddit=subreddit,
        )
        insights = scrape_result.get("insights", {})
        content_briefs = scrape_result.get("content_briefs", [])
        post_count = scrape_result.get("post_count", 0)

        if save_insights:
            with open(save_insights, "w") as fh:
                json.dump(scrape_result, fh, indent=2)
            log.info("Insights saved → %s", save_insights)

    if not insights:
        log.warning("No insights available — content generation may be generic.")

    # ── Stage 2: Generate content ─────────────────────────────────────────────
    log.info("=== Stage 2: Generating content (%s) ===", ", ".join(content_types))
    content = create_content_package(
        platform=platform,
        topic=topic,
        insights=insights,
        content_briefs=content_briefs,
        content_types=content_types,
    )

    # ── Stage 3: Post (optional) ──────────────────────────────────────────────
    post_results: list = []
    if post:
        log.info("=== Stage 3: Posting to %s ===", platform)

        text_data: dict = {}
        text_file = content["files"].get("text")
        if text_file:
            with open(text_file) as fh:
                text_data = json.load(fh)

        post_text = text_data.get("full_post") or f"Check out this about {topic}!"
        image_path = content["files"].get("image")
        video_path = content["files"].get("video")

        post_kwargs: dict = {}
        if video_path and platform in ("tiktok", "instagram"):
            post_kwargs["video_path"] = video_path
        if subreddit:
            post_kwargs["subreddit"] = subreddit
            post_kwargs["title"] = topic

        post_results = post_to_platforms(
            text=post_text,
            platforms=[platform],
            image_path=image_path if not video_path else None,
            **post_kwargs,
        )

    return {
        "platform": platform,
        "topic": topic,
        "scraped_posts": post_count,
        "insights_summary": {
            k: insights.get(k)
            for k in ["key_patterns", "content_formats", "recommended_structure"]
        },
        "content_briefs": content_briefs,
        "generated_files": content["files"],
        "output_dir": content["output_dir"],
        "post_results": post_results,
    }


def run_all_platforms(
    topic: str,
    platforms: Optional[list[str]] = None,
    **kwargs,
) -> list[dict]:
    """Run the pipeline for multiple platforms and return all results."""
    targets = platforms or list(SCRAPERS.keys())
    results = []
    for platform in targets:
        log.info("\n" + "=" * 60)
        log.info("Pipeline: %s / %s", platform, topic)
        log.info("=" * 60)
        try:
            result = run_pipeline(platform, topic, **kwargs)
            results.append(result)
        except Exception as exc:
            log.error("Pipeline failed for %s: %s", platform, exc)
            results.append({"platform": platform, "topic": topic, "error": str(exc)})
    return results


def _print_summary(result: dict) -> None:
    platform = result.get("platform", "?")
    topic = result.get("topic", "?")

    print(f"\n{'=' * 60}")
    print(f"  {platform.upper()} / {topic}")
    print("=" * 60)

    if "error" in result:
        print(f"  ERROR: {result['error']}")
        return

    print(f"  Posts analysed : {result.get('scraped_posts', 0)}")

    patterns = result.get("insights_summary", {}).get("key_patterns") or []
    if patterns:
        print("\n  Key patterns:")
        for p in patterns[:5]:
            print(f"    • {p}")

    briefs = result.get("content_briefs") or []
    if briefs:
        print(f"\n  Content briefs generated: {len(briefs)}")
        for b in briefs[:3]:
            print(f"    [{b.get('type', '?')}] {b.get('title', '')}")

    files = result.get("generated_files") or {}
    if files:
        print(f"\n  Generated files (in {result.get('output_dir', 'output/content')}):")
        for ftype, fpath in files.items():
            print(f"    {ftype:10s} → {fpath}")

    posts = result.get("post_results") or []
    if posts:
        print("\n  Post results:")
        for r in posts:
            print(f"    {r}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    platform_choices = list(SCRAPERS.keys()) + ["ALL"]

    parser = argparse.ArgumentParser(
        description="Scrape → analyse → create content → post",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "platform",
        choices=platform_choices,
        help=f"Platform to target, or ALL to run all. Choices: {', '.join(platform_choices)}",
    )
    parser.add_argument("topic", help='Topic to research, e.g. "3d printing"')
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Number of posts to scrape for analysis (default 10)",
    )
    parser.add_argument(
        "--types", nargs="+",
        default=["text", "image", "graph", "video"],
        choices=["text", "image", "graph", "video"],
        help="Content types to generate (default: all)",
    )
    parser.add_argument(
        "--post", action="store_true",
        help="Post generated content to the platform after creation",
    )
    parser.add_argument(
        "--save-insights", metavar="FILE",
        help="Save raw scrape + insights JSON to this file",
    )
    parser.add_argument(
        "--insights-file", metavar="FILE",
        help="Load pre-saved insights JSON — skips the scraping stage",
    )
    parser.add_argument(
        "--subreddit",
        help="Target a specific subreddit (Reddit only)",
    )
    parser.add_argument(
        "--platforms", nargs="+", choices=list(SCRAPERS.keys()),
        help="When using ALL, restrict to these platforms only",
    )
    args = parser.parse_args()

    pipeline_kwargs = dict(
        scrape_limit=args.limit,
        content_types=args.types,
        post=args.post,
        save_insights=args.save_insights,
        insights_file=args.insights_file,
        subreddit=args.subreddit,
    )

    if args.platform == "ALL":
        results = run_all_platforms(
            topic=args.topic,
            platforms=args.platforms,
            **pipeline_kwargs,
        )
        for r in results:
            _print_summary(r)
    else:
        result = run_pipeline(
            platform=args.platform,
            topic=args.topic,
            **pipeline_kwargs,
        )
        _print_summary(result)
