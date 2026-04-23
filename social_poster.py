"""
social_poster.py — Free multi-platform social media posting module.

Supported platforms and their free-tier libraries:
  - Bluesky       (AT Protocol — zero cost, no approval needed)
  - Mastodon      (fully open, self-hostable)
  - X / Twitter   (Tweepy — 500 posts/month free)
  - Reddit        (PRAW — free for non-commercial)
  - LinkedIn      (REST API — free with "Share on LinkedIn" product approval)
  - Facebook Page (Graph API — free with App Review)
  - Instagram     (Graph API — free with App Review)
  - Threads       (Graph API — free, 250 posts/day)

All credentials are loaded from environment variables. See .env.example for
the full list of keys required per platform.
"""

import os
import time
import logging
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(key: str, required: bool = False) -> Optional[str]:
    val = os.getenv(key)
    if required and not val:
        raise EnvironmentError(f"Missing required env var: {key}")
    return val


def _check_deps(*packages: str) -> None:
    import importlib
    missing = [p for p in packages if not importlib.util.find_spec(p)]
    if missing:
        raise ImportError(
            f"Install missing packages first: pip install {' '.join(missing)}"
        )


# ---------------------------------------------------------------------------
# Bluesky (AT Protocol) — https://docs.bsky.app
# Free: unlimited, no API key, no approval required.
# Auth: App Password created at bsky.social → Settings → App Passwords
# ---------------------------------------------------------------------------

def post_bluesky(text: str, image_path: Optional[str] = None) -> dict:
    """Post to Bluesky. Returns the created post URI."""
    _check_deps("atproto")
    from atproto import Client, client_utils

    handle = _env("BLUESKY_HANDLE", required=True)
    app_password = _env("BLUESKY_APP_PASSWORD", required=True)

    client = Client()
    client.login(handle, app_password)

    if image_path:
        with open(image_path, "rb") as f:
            img_data = f.read()
        upload = client.upload_blob(img_data)
        embed = {
            "$type": "app.bsky.embed.images",
            "images": [{"image": upload.blob, "alt": ""}],
        }
        response = client.send_post(text=text, embed=embed)
    else:
        response = client.send_post(text=text)

    log.info("Bluesky post created: %s", response.uri)
    return {"platform": "bluesky", "uri": response.uri}


# ---------------------------------------------------------------------------
# Mastodon — https://docs.joinmastodon.org/api/
# Free: 300 req/5 min, open source, self-hostable.
# Auth: OAuth 2.0; register your app on your instance to get access token.
# ---------------------------------------------------------------------------

def post_mastodon(text: str, image_path: Optional[str] = None) -> dict:
    """Post a toot to Mastodon. Returns the status URL."""
    _check_deps("mastodon")
    from mastodon import Mastodon

    instance_url = _env("MASTODON_INSTANCE_URL", required=True)
    access_token = _env("MASTODON_ACCESS_TOKEN", required=True)

    mastodon = Mastodon(access_token=access_token, api_base_url=instance_url)

    media_ids = []
    if image_path:
        media = mastodon.media_post(image_path)
        # Wait for media processing
        for _ in range(10):
            info = mastodon.media(media["id"])
            if info.get("url"):
                break
            time.sleep(2)
        media_ids = [media["id"]]

    status = mastodon.status_post(text, media_ids=media_ids or None)
    log.info("Mastodon toot posted: %s", status["url"])
    return {"platform": "mastodon", "url": status["url"], "id": status["id"]}


# ---------------------------------------------------------------------------
# X / Twitter — https://docs.x.com
# Free: 500 tweets/month, 17 POST req/24h per user token (v2).
# Auth: OAuth 2.0 PKCE (user-context) or OAuth 1.0a.
# ---------------------------------------------------------------------------

def post_twitter(text: str, image_path: Optional[str] = None) -> dict:
    """Post a tweet via Tweepy (OAuth 1.0a user auth). Returns tweet ID."""
    _check_deps("tweepy")
    import tweepy

    api_key = _env("TWITTER_API_KEY", required=True)
    api_secret = _env("TWITTER_API_SECRET", required=True)
    access_token = _env("TWITTER_ACCESS_TOKEN", required=True)
    access_secret = _env("TWITTER_ACCESS_TOKEN_SECRET", required=True)

    # v1.1 client used only for media upload; v2 client for tweets
    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
    api_v1 = tweepy.API(auth)
    client_v2 = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )

    media_ids = []
    if image_path:
        media = api_v1.media_upload(image_path)
        media_ids = [media.media_id]

    response = client_v2.create_tweet(
        text=text, media_ids=media_ids if media_ids else None
    )
    tweet_id = response.data["id"]
    log.info("Tweet posted: https://x.com/i/web/status/%s", tweet_id)
    return {
        "platform": "twitter",
        "tweet_id": tweet_id,
        "url": f"https://x.com/i/web/status/{tweet_id}",
    }


# ---------------------------------------------------------------------------
# Reddit — https://www.reddit.com/dev/api/
# Free for non-commercial: 100 req/min (authenticated).
# Auth: OAuth 2.0; script-type app for personal bots.
# ---------------------------------------------------------------------------

def post_reddit(
    subreddit: str,
    title: str,
    text: Optional[str] = None,
    url: Optional[str] = None,
    flair_id: Optional[str] = None,
) -> dict:
    """Submit a post to a subreddit (text OR link). Returns submission URL."""
    _check_deps("praw")
    import praw

    reddit = praw.Reddit(
        client_id=_env("REDDIT_CLIENT_ID", required=True),
        client_secret=_env("REDDIT_CLIENT_SECRET", required=True),
        username=_env("REDDIT_USERNAME", required=True),
        password=_env("REDDIT_PASSWORD", required=True),
        user_agent=_env("REDDIT_USER_AGENT") or "una-social-poster/1.0",
    )

    sub = reddit.subreddit(subreddit)
    kwargs = {"title": title, "flair_id": flair_id}

    if url:
        submission = sub.submit_link(**kwargs, url=url)
    else:
        submission = sub.submit(**kwargs, selftext=text or "")

    log.info("Reddit post submitted: %s", submission.shortlink)
    return {
        "platform": "reddit",
        "url": submission.shortlink,
        "id": submission.id,
    }


# ---------------------------------------------------------------------------
# LinkedIn — https://learn.microsoft.com/en-us/linkedin/
# Free with "Share on LinkedIn" product (requires manual LinkedIn approval).
# Auth: OAuth 2.0; scopes: w_member_social or w_organization_social.
# ---------------------------------------------------------------------------

def post_linkedin(text: str, organization_id: Optional[str] = None) -> dict:
    """Post to LinkedIn personal profile or organization page."""
    import requests

    access_token = _env("LINKEDIN_ACCESS_TOKEN", required=True)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    # Determine author URN
    if organization_id:
        author = f"urn:li:organization:{organization_id}"
    else:
        person_id = _env("LINKEDIN_PERSON_ID", required=True)
        author = f"urn:li:person:{person_id}"

    payload = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }

    resp = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    post_id = resp.headers.get("x-restli-id", "")
    log.info("LinkedIn post created: %s", post_id)
    return {"platform": "linkedin", "post_id": post_id}


# ---------------------------------------------------------------------------
# Facebook Page — https://developers.facebook.com/docs/graph-api/
# Free with App Review; 200 API calls/hour per account.
# Auth: Page Access Token (long-lived).
# ---------------------------------------------------------------------------

def post_facebook(text: str, image_path: Optional[str] = None) -> dict:
    """Post to a Facebook Page. Returns the post ID."""
    import requests

    page_id = _env("FACEBOOK_PAGE_ID", required=True)
    page_token = _env("FACEBOOK_PAGE_ACCESS_TOKEN", required=True)

    if image_path:
        with open(image_path, "rb") as f:
            resp = requests.post(
                f"https://graph.facebook.com/v21.0/{page_id}/photos",
                data={"caption": text, "access_token": page_token},
                files={"source": f},
                timeout=60,
            )
    else:
        resp = requests.post(
            f"https://graph.facebook.com/v21.0/{page_id}/feed",
            data={"message": text, "access_token": page_token},
            timeout=30,
        )

    resp.raise_for_status()
    post_id = resp.json().get("id", "")
    log.info("Facebook post created: %s", post_id)
    return {"platform": "facebook", "post_id": post_id}


# ---------------------------------------------------------------------------
# Instagram — https://developers.facebook.com/docs/instagram-platform/
# Free with App Review; requires Business/Creator account linked to FB Page.
# 100 API-published posts per 24-hour rolling window.
# Auth: Instagram User Access Token with instagram_content_publish scope.
# ---------------------------------------------------------------------------

def post_instagram(
    caption: str,
    image_url: str,
    is_reel: bool = False,
    video_url: Optional[str] = None,
) -> dict:
    """
    Post an image or Reel to Instagram via Graph API.

    For images, pass a publicly accessible image_url.
    For Reels, set is_reel=True and pass video_url instead.
    """
    import requests

    ig_id = _env("INSTAGRAM_ACCOUNT_ID", required=True)
    ig_token = _env("INSTAGRAM_ACCESS_TOKEN", required=True)
    base = f"https://graph.facebook.com/v21.0/{ig_id}"

    # Step 1: Create media container
    container_params = {"caption": caption, "access_token": ig_token}
    if is_reel and video_url:
        container_params["media_type"] = "REELS"
        container_params["video_url"] = video_url
    else:
        container_params["image_url"] = image_url

    resp = requests.post(f"{base}/media", data=container_params, timeout=60)
    resp.raise_for_status()
    container_id = resp.json()["id"]

    # Step 2: Poll until container is ready (video processing can take minutes)
    for attempt in range(20):
        status = requests.get(
            f"https://graph.facebook.com/v21.0/{container_id}",
            params={"fields": "status_code", "access_token": ig_token},
            timeout=30,
        ).json()
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram container error: {status}")
        time.sleep(15)
    else:
        raise TimeoutError("Instagram media container did not finish processing")

    # Step 3: Publish
    pub = requests.post(
        f"{base}/media_publish",
        data={"creation_id": container_id, "access_token": ig_token},
        timeout=30,
    )
    pub.raise_for_status()
    media_id = pub.json()["id"]
    log.info("Instagram post published: %s", media_id)
    return {"platform": "instagram", "media_id": media_id}


# ---------------------------------------------------------------------------
# Threads — https://developers.facebook.com/docs/threads/
# Completely free; 250 posts/day; no per-call charges.
# Auth: OAuth 2.0 via Meta Developer platform.
# ---------------------------------------------------------------------------

def post_threads(text: str, image_url: Optional[str] = None) -> dict:
    """Post to Threads. Returns the published media ID."""
    import requests

    threads_id = _env("THREADS_USER_ID", required=True)
    threads_token = _env("THREADS_ACCESS_TOKEN", required=True)
    base = f"https://graph.threads.net/v1.0/{threads_id}"

    # Step 1: Create container
    container_params = {
        "text": text,
        "access_token": threads_token,
        "media_type": "IMAGE" if image_url else "TEXT",
    }
    if image_url:
        container_params["image_url"] = image_url

    resp = requests.post(f"{base}/threads", data=container_params, timeout=30)
    resp.raise_for_status()
    container_id = resp.json()["id"]

    # Brief wait for container to be ready
    time.sleep(3)

    # Step 2: Publish
    pub = requests.post(
        f"{base}/threads_publish",
        data={"creation_id": container_id, "access_token": threads_token},
        timeout=30,
    )
    pub.raise_for_status()
    media_id = pub.json()["id"]
    log.info("Threads post published: %s", media_id)
    return {"platform": "threads", "media_id": media_id}


# ---------------------------------------------------------------------------
# Convenience: post to all configured platforms at once
# ---------------------------------------------------------------------------

PLATFORM_MAP = {
    "bluesky": lambda text, image, **kw: post_bluesky(text, image),
    "mastodon": lambda text, image, **kw: post_mastodon(text, image),
    "twitter": lambda text, image, **kw: post_twitter(text, image),
    "reddit": lambda text, image, **kw: post_reddit(
        kw["subreddit"], kw["title"], text
    ),
    "linkedin": lambda text, image, **kw: post_linkedin(text),
    "facebook": lambda text, image, **kw: post_facebook(text, image),
    "instagram": lambda text, image, **kw: post_instagram(
        text, kw["image_url"], kw.get("is_reel", False), kw.get("video_url")
    ),
    "threads": lambda text, image, **kw: post_threads(
        text, kw.get("image_url")
    ),
}


def post_to_platforms(
    text: str,
    platforms: list[str],
    image_path: Optional[str] = None,
    **kwargs,
) -> list[dict]:
    """
    Post text (and optionally an image) to multiple platforms.

    Args:
        text:      The post body.
        platforms: List of platform names to target, e.g. ["bluesky", "mastodon"].
        image_path: Local file path to an image (optional; not all platforms accept it).
        **kwargs:  Platform-specific args — see individual post_* functions above.
                   e.g. subreddit="python", title="...", image_url="https://..."

    Returns:
        List of result dicts from each platform, each containing at minimum
        {"platform": "<name>", ...} or {"platform": "<name>", "error": "..."}.
    """
    results = []
    for platform in platforms:
        fn = PLATFORM_MAP.get(platform.lower())
        if not fn:
            log.warning("Unknown platform '%s' — skipping.", platform)
            results.append({"platform": platform, "error": "unsupported platform"})
            continue
        try:
            result = fn(text, image_path, **kwargs)
            results.append(result)
        except Exception as exc:
            log.error("Failed to post to %s: %s", platform, exc)
            results.append({"platform": platform, "error": str(exc)})
    return results


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Post to social media platforms")
    parser.add_argument("text", help="Post body text")
    parser.add_argument(
        "--platforms",
        nargs="+",
        default=["bluesky"],
        choices=list(PLATFORM_MAP.keys()),
        metavar="PLATFORM",
        help=f"Platforms to post to. Choices: {', '.join(PLATFORM_MAP)}",
    )
    parser.add_argument("--image", help="Local image file path (optional)")
    parser.add_argument("--subreddit", help="Subreddit name (Reddit only)")
    parser.add_argument("--title", help="Post title (Reddit only)")
    parser.add_argument("--image-url", dest="image_url", help="Public image URL (Instagram/Threads)")
    args = parser.parse_args()

    kwargs = {}
    if args.subreddit:
        kwargs["subreddit"] = args.subreddit
    if args.title:
        kwargs["title"] = args.title
    if args.image_url:
        kwargs["image_url"] = args.image_url

    results = post_to_platforms(args.text, args.platforms, args.image, **kwargs)
    print(json.dumps(results, indent=2))
