"""
social_poster.py — Login-only social media posting. No API keys, no developer
portals, no paid services. Each platform authenticates with your account
username and password only.

Platform   Library          Credentials
──────────────────────────────────────────────────────────────────
Bluesky    atproto          handle + app-password (Settings → App Passwords)
Mastodon   Mastodon.py      instance URL + email + password
Twitter/X  twikit           username + email + password  (unofficial)
Instagram  instagrapi       username + password           (unofficial)
Threads    threads-net      Instagram username + password (unofficial)
Reddit     requests         username + password only (old.reddit.com cookie login)
LinkedIn   linkedin-api     email + password              (unofficial voyager API)
TikTok     tiktok-uploader  email + password (Selenium)

NOTE: twikit, instagrapi, threads-net, and linkedin-api use unofficial /
reverse-engineered APIs. They work without paid API registration but may
violate each platform's ToS and can break if internals change.

TikTok: tiktok-uploader drives a headless browser and logs in with your email
and password. Chrome + chromedriver (or Firefox + geckodriver) must be
installed locally.
"""

import asyncio
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
            f"Install missing packages: pip install {' '.join(missing)}"
        )


# ---------------------------------------------------------------------------
# Bluesky (AT Protocol) — https://docs.bsky.app
# No API registration. Create an App Password at:
#   bsky.app → Settings → Privacy and Security → App Passwords
# App passwords are scoped account passwords — not a developer API credential.
# ---------------------------------------------------------------------------

def post_bluesky(text: str, image_path: Optional[str] = None) -> dict:
    """Post to Bluesky using your handle and an app-specific password."""
    _check_deps("atproto")
    from atproto import Client

    client = Client()
    client.login(
        _env("BLUESKY_HANDLE", required=True),
        _env("BLUESKY_APP_PASSWORD", required=True),
    )

    if image_path:
        with open(image_path, "rb") as fh:
            blob = client.upload_blob(fh.read()).blob
        embed = {
            "$type": "app.bsky.embed.images",
            "images": [{"image": blob, "alt": ""}],
        }
        resp = client.send_post(text=text, embed=embed)
    else:
        resp = client.send_post(text=text)

    log.info("Bluesky post created: %s", resp.uri)
    return {"platform": "bluesky", "uri": resp.uri}


# ---------------------------------------------------------------------------
# Mastodon — https://docs.joinmastodon.org/api/
# No pre-registration needed. The app registers itself on the instance the
# first time using Mastodon.create_app(), then logs in with your credentials.
# ---------------------------------------------------------------------------

def post_mastodon(text: str, image_path: Optional[str] = None) -> dict:
    """Post to Mastodon using your instance URL, email, and password."""
    _check_deps("mastodon")
    from mastodon import Mastodon

    instance_url = _env("MASTODON_INSTANCE_URL", required=True)
    email = _env("MASTODON_EMAIL", required=True)
    password = _env("MASTODON_PASSWORD", required=True)

    # Register the app on the instance (returns client_id, client_secret)
    client_id, client_secret = Mastodon.create_app(
        "una-social-poster",
        api_base_url=instance_url,
        scopes=["read", "write"],
    )

    mastodon = Mastodon(
        client_id=client_id,
        client_secret=client_secret,
        api_base_url=instance_url,
    )
    access_token = mastodon.log_in(email, password, scopes=["read", "write"])

    client = Mastodon(access_token=access_token, api_base_url=instance_url)

    media_ids = []
    if image_path:
        media = client.media_post(image_path)
        for _ in range(10):
            info = client.media(media["id"])
            if info.get("url"):
                break
            time.sleep(2)
        media_ids = [media["id"]]

    status = client.status_post(text, media_ids=media_ids or None)
    log.info("Mastodon toot posted: %s", status["url"])
    return {"platform": "mastodon", "url": status["url"], "id": status["id"]}


# ---------------------------------------------------------------------------
# Twitter / X — twikit (unofficial, cookie-based session)
# No API keys. Logs in with username + email + password.
# GitHub: https://github.com/d60/twikit  Stars: 4,300+  License: MIT
# ---------------------------------------------------------------------------

async def _twikit_post(
    username: str, email: str, password: str,
    text: str, image_path: Optional[str],
) -> str:
    from twikit import Client

    client = Client("en-US")
    await client.login(auth_info_1=username, auth_info_2=email, password=password)

    media_ids = []
    if image_path:
        with open(image_path, "rb") as fh:
            data = fh.read()
        ext = os.path.splitext(image_path)[1].lower()
        mime = "image/gif" if ext == ".gif" else "image/png" if ext == ".png" else "image/jpeg"
        media = await client.upload_media(data, media_type=mime)
        media_ids = [media.id]

    tweet = await client.create_tweet(text=text, media_ids=media_ids or None)
    return tweet.id


def post_twitter(text: str, image_path: Optional[str] = None) -> dict:
    """Post to Twitter/X using username, email, and password (no API key)."""
    _check_deps("twikit")
    tweet_id = asyncio.run(
        _twikit_post(
            _env("TWITTER_USERNAME", required=True),
            _env("TWITTER_EMAIL", required=True),
            _env("TWITTER_PASSWORD", required=True),
            text,
            image_path,
        )
    )
    log.info("Tweet posted: https://x.com/i/web/status/%s", tweet_id)
    return {
        "platform": "twitter",
        "tweet_id": tweet_id,
        "url": f"https://x.com/i/web/status/{tweet_id}",
    }


# ---------------------------------------------------------------------------
# Instagram — instagrapi (unofficial, Instagram private mobile API)
# No API keys. Logs in with username + password.
# GitHub: https://github.com/subzeroid/instagrapi  License: MIT
# NOTE: Instagram feed posts require an image or video — text-only is not
# supported by the platform itself.
# ---------------------------------------------------------------------------

def post_instagram(
    caption: str,
    image_path: str,
    video_path: Optional[str] = None,
) -> dict:
    """Post a photo or video to Instagram using username and password."""
    _check_deps("instagrapi")
    from instagrapi import Client

    if not image_path and not video_path:
        raise ValueError("Instagram requires an image or video — provide image_path or video_path")

    cl = Client()
    cl.login(
        _env("INSTAGRAM_USERNAME", required=True),
        _env("INSTAGRAM_PASSWORD", required=True),
    )

    if video_path:
        media = cl.video_upload(video_path, caption)
    else:
        media = cl.photo_upload(image_path, caption)

    url = f"https://www.instagram.com/p/{media.code}/"
    log.info("Instagram post published: %s", url)
    return {"platform": "instagram", "media_id": str(media.id), "url": url}


# ---------------------------------------------------------------------------
# Threads — threads-net (unofficial, uses Instagram credentials)
# No API keys. Uses the same username + password as Instagram.
# PyPI: https://pypi.org/project/threads-net/  License: MIT
# ---------------------------------------------------------------------------

def post_threads(text: str, image_path: Optional[str] = None) -> dict:
    """Post to Threads using your Instagram username and password."""
    _check_deps("threads")
    from threads import Threads

    username = _env("INSTAGRAM_USERNAME", required=True)
    password = _env("INSTAGRAM_PASSWORD", required=True)

    client = Threads(username=username, password=password)

    if image_path:
        post_id = client.private_api.create_thread_item(
            caption=text,
            image_path=image_path,
        )
    else:
        post_id = client.private_api.create_thread_item(caption=text)

    log.info("Threads post created: %s", post_id)
    return {"platform": "threads", "post_id": str(post_id)}


# ---------------------------------------------------------------------------
# Reddit — pure requests, cookie-based login (no OAuth app, no client_id)
# Uses old.reddit.com/api/login to get a session cookie + modhash, then
# submits via reddit.com/api/submit. Only your username and password needed.
# ---------------------------------------------------------------------------

def post_reddit(
    subreddit: str,
    title: str,
    text: Optional[str] = None,
    link_url: Optional[str] = None,
) -> dict:
    """Submit a text or link post to Reddit using username and password only."""
    import requests as _req

    username = _env("REDDIT_USERNAME", required=True)
    password = _env("REDDIT_PASSWORD", required=True)

    session = _req.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    })

    # Step 1: cookie-based login — no OAuth app or client_id required
    login = session.post(
        "https://old.reddit.com/api/login",
        data={"user": username, "passwd": password, "api_type": "json", "rem": "false"},
        timeout=30,
    )
    login.raise_for_status()
    login_json = login.json().get("json", {})
    if login_json.get("errors"):
        raise RuntimeError(f"Reddit login failed: {login_json['errors']}")
    modhash = login_json["data"]["modhash"]

    # Step 2: submit the post
    submit = session.post(
        "https://www.reddit.com/api/submit",
        data={
            "sr": subreddit,
            "kind": "link" if link_url else "self",
            "title": title,
            "url": link_url or "",
            "text": text or "",
            "uh": modhash,
            "api_type": "json",
            "resubmit": "true",
        },
        timeout=30,
    )
    submit.raise_for_status()
    submit_json = submit.json().get("json", {})
    if submit_json.get("errors"):
        raise RuntimeError(f"Reddit submit failed: {submit_json['errors']}")

    post_url = submit_json["data"]["url"]
    log.info("Reddit post submitted: %s", post_url)
    return {"platform": "reddit", "url": post_url}


# ---------------------------------------------------------------------------
# LinkedIn — linkedin-api (unofficial, email + password login)
# After login the library exposes an authenticated requests.Session which is
# used here to POST directly to LinkedIn's internal voyager/ugcPosts endpoint.
# GitHub: https://github.com/tomquirk/linkedin-api  License: MIT
# ---------------------------------------------------------------------------

def post_linkedin(text: str) -> dict:
    """Post to your LinkedIn profile feed using email and password."""
    _check_deps("linkedin_api")
    from linkedin_api import Linkedin

    api = Linkedin(
        _env("LINKEDIN_EMAIL", required=True),
        _env("LINKEDIN_PASSWORD", required=True),
    )

    # Resolve the current user's numeric person ID from the voyager /me endpoint
    me = api.client.session.get(
        "https://www.linkedin.com/voyager/api/me",
        headers={"accept": "application/vnd.linkedin.normalized+json+2.1"},
    )
    me.raise_for_status()
    person_id = me.json().get("plainId")
    if not person_id:
        raise RuntimeError("Could not resolve LinkedIn person ID from session")

    # JSESSIONID (without surrounding quotes) serves as the CSRF token
    csrf = api.client.session.cookies.get("JSESSIONID", "").strip('"')

    payload = {
        "author": f"urn:li:person:{person_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }

    resp = api.client.session.post(
        "https://www.linkedin.com/voyager/api/ugcPosts",
        json=payload,
        headers={
            "X-RestLi-Protocol-Version": "2.0.0",
            "csrf-token": csrf,
        },
    )
    resp.raise_for_status()
    post_id = resp.headers.get("x-restli-id", "")
    log.info("LinkedIn post created: %s", post_id)
    return {"platform": "linkedin", "post_id": post_id}


# ---------------------------------------------------------------------------
# TikTok — tiktok-uploader (Selenium, email + password login)
# No API registration. Drives a headless browser and logs in with your normal
# TikTok email and password.
# Requires: Chrome or Firefox + matching chromedriver/geckodriver installed.
# GitHub: https://github.com/wkaisertexas/tiktok-uploader  License: MIT
# NOTE: TikTok only supports video posts — images/text-only are not available.
# ---------------------------------------------------------------------------

def post_tiktok(description: str, video_path: Optional[str] = None) -> dict:
    """Upload a video to TikTok using email and password."""
    _check_deps("tiktok_uploader")
    from tiktok_uploader.upload import upload_video

    if not video_path:
        raise ValueError("TikTok requires a video file — pass video_path or --video")

    failed = upload_video(
        filename=video_path,
        description=description,
        username=_env("TIKTOK_EMAIL", required=True),
        password=_env("TIKTOK_PASSWORD", required=True),
        browser=_env("TIKTOK_BROWSER") or "chrome",
        headless=True,
        num_retries=2,
    )

    if failed:
        raise RuntimeError(f"TikTok upload failed: {failed}")

    log.info("TikTok video uploaded: %s", video_path)
    return {"platform": "tiktok", "video": video_path, "status": "uploaded"}


# ---------------------------------------------------------------------------
# Convenience: broadcast to multiple platforms at once
# ---------------------------------------------------------------------------

PLATFORM_MAP = {
    "bluesky": lambda text, img, **kw: post_bluesky(text, img),
    "mastodon": lambda text, img, **kw: post_mastodon(text, img),
    "twitter": lambda text, img, **kw: post_twitter(text, img),
    "instagram": lambda text, img, **kw: post_instagram(
        text, kw.get("image_path") or img, kw.get("video_path")
    ),
    "threads": lambda text, img, **kw: post_threads(text, img),
    "reddit": lambda text, img, **kw: post_reddit(
        kw.get("subreddit") or _env("REDDIT_DEFAULT_SUBREDDIT") or "test",
        kw.get("title") or text[:100],
        text,
        kw.get("link_url"),
    ),
    "linkedin": lambda text, img, **kw: post_linkedin(text),
    "tiktok": lambda text, img, **kw: post_tiktok(
        text, kw.get("video_path") or img
    ),
}


def post_to_platforms(
    text: str,
    platforms: list[str],
    image_path: Optional[str] = None,
    **kwargs,
) -> list[dict]:
    """
    Post to one or more platforms in sequence.

    Args:
        text:       Post body.
        platforms:  Platform names, e.g. ["bluesky", "mastodon", "twitter"].
        image_path: Local image file (optional; Instagram requires one).
        **kwargs:   Extra per-platform args, e.g. video_path="clip.mp4".

    Returns:
        List of result dicts: [{"platform": "...", ...}] or [..., "error": "..."}].
    """
    results = []
    for platform in platforms:
        fn = PLATFORM_MAP.get(platform.lower())
        if not fn:
            log.warning("Unknown platform '%s' — skipping.", platform)
            results.append({"platform": platform, "error": "unsupported"})
            continue
        try:
            results.append(fn(text, image_path, **kwargs))
        except Exception as exc:
            log.error("Failed to post to %s: %s", platform, exc)
            results.append({"platform": platform, "error": str(exc)})
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Post to social media using account logins only — no API keys required."
    )
    parser.add_argument("text", help="Post body text")
    parser.add_argument(
        "--platforms",
        nargs="+",
        default=["bluesky"],
        choices=list(PLATFORM_MAP),
        metavar="PLATFORM",
        help=f"Platforms to post to. Choices: {', '.join(PLATFORM_MAP)}",
    )
    parser.add_argument("--image", dest="image_path", help="Local image file (optional)")
    parser.add_argument("--video", dest="video_path", help="Local video file (Instagram, TikTok)")
    parser.add_argument("--subreddit", help="Subreddit to post to (Reddit)")
    parser.add_argument("--title", help="Post title (Reddit; defaults to first 100 chars of text)")
    parser.add_argument("--link-url", dest="link_url", help="Link URL for Reddit link posts")
    args = parser.parse_args()

    kwargs = {}
    if args.video_path:
        kwargs["video_path"] = args.video_path
    if args.subreddit:
        kwargs["subreddit"] = args.subreddit
    if args.title:
        kwargs["title"] = args.title
    if args.link_url:
        kwargs["link_url"] = args.link_url

    results = post_to_platforms(args.text, args.platforms, args.image_path, **kwargs)
    print(json.dumps(results, indent=2))
