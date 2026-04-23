"""
social_poster.py — Login-only social media posting. No API keys, no developer
portals, no paid services. Each platform authenticates with your account
username and password only.

Platform   Library        Credentials
────────────────────────────────────────────────────────
Bluesky    atproto        handle + app-password (Settings → App Passwords)
Mastodon   Mastodon.py    instance URL + email + password
Twitter/X  twikit         username + email + password  (unofficial cookie session)
Instagram  instagrapi     username + password           (unofficial mobile API)
Threads    threads-net    Instagram username + password (unofficial)

NOTE: twikit, instagrapi, and threads-net use unofficial / reverse-engineered
APIs. They work without any API registration but may violate each platform's
ToS and can break if the platform changes its internals.
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
    parser.add_argument("--video", dest="video_path", help="Local video file (Instagram only)")
    args = parser.parse_args()

    kwargs = {}
    if args.video_path:
        kwargs["video_path"] = args.video_path

    results = post_to_platforms(args.text, args.platforms, args.image_path, **kwargs)
    print(json.dumps(results, indent=2))
