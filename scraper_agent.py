"""
scraper_agent.py — Scrapes top-performing posts from any social platform for a
given topic, analyses their format/structure via AI, and returns structured
content briefs for content_agent.py to act on.

Supported platforms: tiktok, instagram, twitter, reddit, youtube, bluesky

Usage:
    from scraper_agent import scrape_and_analyze
    result = scrape_and_analyze("tiktok", "3d printing", limit=10)
    # result["insights"]  → patterns, hooks, recommended structure
    # result["content_briefs"] → ready-to-use briefs for content_agent

CLI:
    python scraper_agent.py tiktok "3d printing" --limit 15 --output insights.json
"""

import asyncio
import json
import logging
import os
from typing import Optional

from openai import OpenAI

log = logging.getLogger(__name__)


def _env(key: str, required: bool = False) -> Optional[str]:
    val = os.getenv(key)
    if required and not val:
        raise EnvironmentError(f"Missing required env var: {key}")
    return val


def _ai_client() -> OpenAI:
    return OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=_env("NVIDIA_API_KEY", required=True),
    )


def _ai_analyze(prompt: str) -> str:
    client = _ai_client()
    resp = client.chat.completions.create(
        model=_env("KIMI_MODEL") or "moonshotai/kimi-k2-instruct",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a social media content strategist. "
                    "Analyse post data and return ONLY valid JSON — no markdown fences, "
                    "no commentary, no extra text."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=2048,
    )
    return resp.choices[0].message.content.strip()


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Platform scrapers
# Each returns a list of normalised dicts (metadata only — no media download).
# ---------------------------------------------------------------------------

async def _tiktok_async(topic: str, limit: int) -> list[dict]:
    from TikTokApi import TikTokApi

    posts = []
    async with TikTokApi() as api:
        await api.create_sessions(num_sessions=1, sleep_after=3, headless=True)
        tag = topic.lstrip("#")
        async for video in api.hashtag(name=tag).videos(count=limit):
            d = video.as_dict
            posts.append({
                "id": d.get("id"),
                "description": d.get("desc", ""),
                "duration_sec": d.get("video", {}).get("duration", 0),
                "likes": d.get("stats", {}).get("diggCount", 0),
                "comments": d.get("stats", {}).get("commentCount", 0),
                "shares": d.get("stats", {}).get("shareCount", 0),
                "plays": d.get("stats", {}).get("playCount", 0),
                "hashtags": [h.get("hashtagName", "") for h in d.get("challenges", [])],
                "music_title": d.get("music", {}).get("title", ""),
                "author_followers": d.get("authorStats", {}).get("followerCount", 0),
            })
    return posts


def _scrape_tiktok(topic: str, limit: int) -> list[dict]:
    return asyncio.run(_tiktok_async(topic, limit))


def _scrape_instagram(topic: str, limit: int) -> list[dict]:
    from instagrapi import Client

    cl = Client()
    cl.login(
        _env("INSTAGRAM_USERNAME", required=True),
        _env("INSTAGRAM_PASSWORD", required=True),
    )
    tag = topic.lstrip("#")
    medias = cl.hashtag_medias_top(tag, amount=limit)
    posts = []
    for m in medias:
        posts.append({
            "id": str(m.id),
            "caption": (m.caption_text or "")[:300],
            "media_type": {1: "photo", 2: "video", 8: "carousel"}.get(m.media_type, "unknown"),
            "likes": m.like_count,
            "comments": m.comment_count,
            "video_duration": getattr(m, "video_duration", None),
            "hashtags": [t.name for t in (m.usertags or [])],
        })
    return posts


async def _twitter_async(topic: str, limit: int) -> list[dict]:
    from twikit import Client

    client = Client("en-US")
    await client.login(
        auth_info_1=_env("TWITTER_USERNAME", required=True),
        auth_info_2=_env("TWITTER_EMAIL", required=True),
        password=_env("TWITTER_PASSWORD", required=True),
    )
    results = await client.search_tweet(topic, "Top", count=limit)
    posts = []
    for t in results:
        posts.append({
            "id": t.id,
            "text": t.text,
            "likes": t.favorite_count,
            "retweets": t.retweet_count,
            "replies": t.reply_count,
            "views": getattr(t, "view_count", None),
            "has_media": bool(t.media),
            "hashtags": [w for w in t.text.split() if w.startswith("#")],
            "char_count": len(t.text),
        })
    return posts


def _scrape_twitter(topic: str, limit: int) -> list[dict]:
    return asyncio.run(_twitter_async(topic, limit))


def _scrape_reddit(topic: str, limit: int, subreddit: Optional[str] = None) -> list[dict]:
    import requests

    headers = {"User-Agent": "una-scraper/1.0"}
    if subreddit:
        url = f"https://www.reddit.com/r/{subreddit}/top.json"
        params = {"t": "month", "limit": limit}
    else:
        url = "https://www.reddit.com/search.json"
        params = {"q": topic, "sort": "top", "t": "month", "limit": limit}

    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    posts = []
    for child in resp.json()["data"]["children"]:
        d = child["data"]
        posts.append({
            "id": d.get("id"),
            "title": d.get("title", ""),
            "body": (d.get("selftext", "") or "")[:400],
            "subreddit": d.get("subreddit"),
            "score": d.get("score", 0),
            "upvote_ratio": d.get("upvote_ratio", 0),
            "comments": d.get("num_comments", 0),
            "awards": d.get("total_awards_received", 0),
            "is_video": d.get("is_video", False),
            "has_image": d.get("post_hint") == "image",
            "flair": d.get("link_flair_text"),
            "title_length": len(d.get("title", "")),
        })
    return posts


def _scrape_youtube(topic: str, limit: int) -> list[dict]:
    import yt_dlp

    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
    posts = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch{limit}:{topic}", download=False)
        for entry in result.get("entries") or []:
            posts.append({
                "id": entry.get("id"),
                "title": entry.get("title", ""),
                "description": (entry.get("description") or "")[:400],
                "duration_sec": entry.get("duration"),
                "view_count": entry.get("view_count"),
                "like_count": entry.get("like_count"),
                "comment_count": entry.get("comment_count"),
                "channel": entry.get("channel"),
                "tags": (entry.get("tags") or [])[:10],
                "upload_date": entry.get("upload_date"),
            })
    return posts


def _scrape_bluesky(topic: str, limit: int) -> list[dict]:
    from atproto import Client

    client = Client()
    client.login(
        _env("BLUESKY_HANDLE", required=True),
        _env("BLUESKY_APP_PASSWORD", required=True),
    )
    result = client.app.bsky.feed.search_posts({"q": topic, "limit": limit})
    posts = []
    for post in result.posts:
        rec = post.record
        posts.append({
            "uri": post.uri,
            "text": getattr(rec, "text", ""),
            "char_count": len(getattr(rec, "text", "")),
            "likes": post.like_count,
            "replies": post.reply_count,
            "reposts": post.repost_count,
            "has_embed": bool(getattr(rec, "embed", None)),
        })
    return posts


SCRAPERS = {
    "tiktok": _scrape_tiktok,
    "instagram": _scrape_instagram,
    "twitter": _scrape_twitter,
    "reddit": _scrape_reddit,
    "youtube": _scrape_youtube,
    "bluesky": _scrape_bluesky,
}


# ---------------------------------------------------------------------------
# AI analysis
# ---------------------------------------------------------------------------

def analyze_posts(platform: str, topic: str, posts: list[dict]) -> dict:
    """
    Send scraped post metadata to AI and get back structured insights +
    content briefs.
    """
    sample = posts[:15]
    prompt = f"""Analyse these top-performing {platform} posts about "{topic}":

{json.dumps(sample, indent=2)}

Return a JSON object with EXACTLY these keys — no other keys, no markdown:
{{
  "key_patterns": ["<pattern1>", "<pattern2>", ...],
  "content_formats": ["<format1>", ...],
  "avg_engagement_notes": "<what drives engagement here>",
  "caption_style": "<tone, length, emoji usage, CTA style>",
  "hashtag_strategy": "<how hashtags are used>",
  "hooks": ["<hook style 1>", "<hook style 2>", "<hook style 3>"],
  "recommended_structure": "<step-by-step content structure for {platform}>",
  "content_briefs": [
    {{
      "type": "<video|image|graph|text>",
      "title": "<content title>",
      "hook": "<opening line or frame>",
      "body": "<body outline or script>",
      "cta": "<call to action>",
      "format_notes": "<platform-specific format tips, dimensions, length>"
    }},
    {{
      "type": "<video|image|graph|text>",
      "title": "<second idea title>",
      "hook": "<opening line>",
      "body": "<body outline>",
      "cta": "<call to action>",
      "format_notes": "<format tips>"
    }},
    {{
      "type": "<video|image|graph|text>",
      "title": "<third idea title>",
      "hook": "<opening line>",
      "body": "<body outline>",
      "cta": "<call to action>",
      "format_notes": "<format tips>"
    }}
  ]
}}"""

    return _parse_json(_ai_analyze(prompt))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape_and_analyze(
    platform: str,
    topic: str,
    limit: int = 10,
    subreddit: Optional[str] = None,
) -> dict:
    """
    Scrape top posts for a topic on a platform, analyse with AI, and return
    structured insights + content briefs.

    Args:
        platform:  One of: tiktok, instagram, twitter, reddit, youtube, bluesky
        topic:     Search keyword or hashtag, e.g. "3d printing" or "#printing"
        limit:     Number of posts to scrape (default 10)
        subreddit: For Reddit — specific subreddit name (optional)

    Returns:
        {
          "platform": str,
          "topic": str,
          "post_count": int,
          "posts": [...],       ← raw scraped metadata
          "insights": {...},    ← AI analysis
          "content_briefs": [...] ← ready-to-use briefs for content_agent
        }
    """
    scraper = SCRAPERS.get(platform.lower())
    if not scraper:
        raise ValueError(
            f"Unsupported platform '{platform}'. Choose from: {', '.join(SCRAPERS)}"
        )

    log.info("Scraping %d posts from %s for topic '%s'…", limit, platform, topic)
    kwargs = {"subreddit": subreddit} if subreddit and platform == "reddit" else {}
    posts = scraper(topic, limit, **kwargs)

    if not posts:
        log.warning("No posts returned for %s / %s", platform, topic)
        return {
            "platform": platform, "topic": topic,
            "post_count": 0, "posts": [], "insights": {}, "content_briefs": [],
        }

    log.info("Analysing %d posts with AI…", len(posts))
    insights = analyze_posts(platform, topic, posts)
    content_briefs = insights.pop("content_briefs", [])

    return {
        "platform": platform,
        "topic": topic,
        "post_count": len(posts),
        "posts": posts,
        "insights": insights,
        "content_briefs": content_briefs,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Scrape + AI-analyse top social posts for a topic"
    )
    parser.add_argument("platform", choices=list(SCRAPERS), help="Platform to scrape")
    parser.add_argument("topic", help='Topic or hashtag, e.g. "3d printing"')
    parser.add_argument("--limit", type=int, default=10, help="Number of posts to scrape")
    parser.add_argument("--subreddit", help="Specific subreddit (Reddit only)")
    parser.add_argument("--output", "-o", help="Save JSON output to this file")
    args = parser.parse_args()

    result = scrape_and_analyze(
        args.platform, args.topic, args.limit, subreddit=args.subreddit
    )

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"Saved to {args.output}")
    else:
        # Print insights + briefs without the raw posts noise
        summary = {k: v for k, v in result.items() if k != "posts"}
        print(json.dumps(summary, indent=2))
