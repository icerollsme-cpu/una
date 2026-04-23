"""
content_agent.py — Generates platform-ready content assets (text captions,
images, graphs, short videos) based on insights from scraper_agent.py.

Content types:
  text   → AI-generated captions / post copy / video scripts (per-platform rules)
  image  → AI-generated visuals via Pollinations.ai (100% free, no API key)
  graph  → Data charts via matplotlib (dark-themed, social-media ready)
  video  → Slideshow video assembled from images via moviepy + Pillow overlays

Usage:
    from content_agent import create_content_package
    package = create_content_package("tiktok", "3d printing", insights, briefs)
    # package["files"] → {"text": "...", "image": "...", "graph": "...", "video": "..."}

CLI:
    python content_agent.py tiktok "3d printing" --insights-file insights.json
    python content_agent.py instagram coffee --types text image
"""

import json
import logging
import os
import textwrap
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
from openai import OpenAI

log = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.getenv("CONTENT_OUTPUT_DIR", "output/content"))


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


def _ai(system: str, user: str, temperature: float = 0.7) -> str:
    resp = _ai_client().chat.completions.create(
        model=_env("KIMI_MODEL") or "moonshotai/kimi-k2-instruct",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=1024,
    )
    return resp.choices[0].message.content.strip()


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


def _out(filename: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / filename


# ---------------------------------------------------------------------------
# Text generation
# ---------------------------------------------------------------------------

_PLATFORM_RULES = {
    "tiktok":    "max 150 chars, punchy hook first line, 3-5 trending hashtags, energetic tone",
    "instagram": "150-300 chars, storytelling tone, 10-20 hashtags, emoji encouraged, CTA at end",
    "twitter":   "under 280 chars, conversational, 1-2 hashtags max, question or CTA at end",
    "reddit":    "title under 100 chars, no hashtags, community tone, informative",
    "youtube":   "SEO title under 70 chars, keyword-rich description, timestamps if applicable",
    "linkedin":  "professional, 300-500 chars, 3-5 hashtags, insight or story-driven",
    "threads":   "casual, conversational, under 500 chars, 1-3 hashtags or none",
    "bluesky":   "concise, community-focused, under 300 chars, optional hashtags",
    "facebook":  "conversational, 100-250 chars, question or CTA at end",
    "tiktok_video_script": "30-60 sec spoken script, hook in first 3 sec, clear pacing, CTA at end",
}


def generate_text(
    platform: str,
    topic: str,
    insights: dict,
    brief: Optional[dict] = None,
) -> dict:
    """
    Generate a platform-optimised caption, hashtags, and video script.

    Returns:
        {
          "caption": str,
          "hashtags": str,
          "script": str | None,
          "full_post": str,       ← caption + hashtags, ready to paste
          "file": str             ← path to saved JSON
        }
    """
    rules = _PLATFORM_RULES.get(platform.lower(), "engaging and platform-appropriate")
    brief_str = json.dumps(brief, indent=2) if brief else "none"
    insights_subset = {
        k: insights.get(k)
        for k in ["key_patterns", "caption_style", "hashtag_strategy",
                  "hooks", "recommended_structure"]
    }

    raw = _ai(
        system=(
            "You are an expert social media copywriter. "
            "Return ONLY valid JSON — no markdown, no extra text."
        ),
        user=f"""Write a {platform} post about "{topic}".

Platform rules: {rules}

Insights from top-performing posts:
{json.dumps(insights_subset, indent=2)}

Content brief:
{brief_str}

Return JSON with exactly these keys:
{{
  "caption": "<main caption text>",
  "hashtags": "<space-separated hashtags>",
  "script": "<30-60 sec video script if this is a video brief, otherwise null>",
  "full_post": "<caption + hashtags combined, ready to copy-paste>"
}}""",
    )

    result = _parse_json(raw)
    slug = topic.replace(" ", "_")[:30]
    out_path = _out(f"{platform}_{slug}_text.json")
    out_path.write_text(json.dumps(result, indent=2))
    log.info("Text saved: %s", out_path)
    result["file"] = str(out_path)
    return result


# ---------------------------------------------------------------------------
# Image generation — Pollinations.ai (completely free, no API key required)
# ---------------------------------------------------------------------------

_PLATFORM_SIZES = {
    "tiktok":    (1080, 1920),
    "instagram": (1080, 1080),
    "twitter":   (1200, 675),
    "linkedin":  (1200, 628),
    "facebook":  (1200, 628),
    "youtube":   (1280, 720),
    "threads":   (1080, 1080),
    "bluesky":   (1200, 675),
    "pinterest": (1000, 1500),
    "reddit":    (1200, 628),
}


def generate_image(
    prompt: str,
    filename: str,
    width: int = 1080,
    height: int = 1080,
    model: str = "flux",
) -> str:
    """
    Generate an image via Pollinations.ai and save it.

    Models: flux (best quality, default), turbo (fastest), gptimage
    Returns the saved file path.
    """
    url = (
        f"https://image.pollinations.ai/prompt/{quote(prompt)}"
        f"?width={width}&height={height}&model={model}&nologo=true&enhance=true"
    )
    log.info("Generating image (%dx%d): %s…", width, height, prompt[:60])
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()

    out_path = _out(filename)
    out_path.write_bytes(resp.content)
    log.info("Image saved: %s", out_path)
    return str(out_path)


def generate_platform_image(
    platform: str,
    topic: str,
    style_notes: str = "",
    brief: Optional[dict] = None,
) -> str:
    """Generate an image correctly sized for the target platform."""
    w, h = _PLATFORM_SIZES.get(platform.lower(), (1080, 1080))
    slug = topic.replace(" ", "_")[:30]

    # Ask the AI to write a good image generation prompt
    image_prompt = _ai(
        system="You are a visual art director. Write a concise image generation prompt.",
        user=(
            f"Write an image generation prompt for a {platform} post about '{topic}'. "
            f"Style notes: {style_notes or 'modern, professional, eye-catching'}. "
            f"Brief context: {json.dumps(brief or {}, indent=2)}. "
            "Return only the prompt text — no quotes, no explanation."
        ),
        temperature=0.8,
    )

    return generate_image(
        prompt=image_prompt,
        filename=f"{platform}_{slug}_image.jpg",
        width=w,
        height=h,
    )


# ---------------------------------------------------------------------------
# Graph / data visualisation — matplotlib (dark-themed)
# ---------------------------------------------------------------------------

def generate_graph(
    data: dict,
    chart_type: str,
    title: str,
    filename: str,
    xlabel: str = "",
    ylabel: str = "",
    color_scheme: str = "plasma",
) -> str:
    """
    Generate a chart and save as PNG.

    Args:
        data:         {"labels": [...], "values": [...]}
        chart_type:   "bar" | "horizontal_bar" | "line" | "area" | "pie"
        title:        Chart title
        filename:     Output filename (saved in OUTPUT_DIR)
        color_scheme: matplotlib colormap name

    Returns the saved file path.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = data.get("labels", [])
    values = data.get("values", [])
    n = max(len(labels), 1)

    fig, ax = plt.subplots(figsize=(12, 7))
    fig.patch.set_facecolor("#0d0d0d")
    ax.set_facecolor("#1a1a1a")

    cmap = plt.get_cmap(color_scheme)
    colors = [cmap(i / n) for i in range(n)]

    if chart_type == "bar":
        bars = ax.bar(labels, values, color=colors, edgecolor="none", width=0.6)
        ax.bar_label(bars, fmt="%.0f", padding=5, color="white", fontsize=10)
        ax.set_xticks(range(n))
        ax.set_xticklabels(labels, rotation=20, ha="right", color="white")
    elif chart_type == "horizontal_bar":
        bars = ax.barh(labels, values, color=colors, edgecolor="none", height=0.6)
        ax.tick_params(colors="white")
    elif chart_type == "line":
        ax.plot(range(n), values, color=colors[0], linewidth=2.5, marker="o", markersize=7)
        ax.fill_between(range(n), values, alpha=0.15, color=colors[0])
        ax.set_xticks(range(n))
        ax.set_xticklabels(labels, rotation=20, ha="right", color="white")
    elif chart_type == "area":
        ax.fill_between(range(n), values, alpha=0.4, color=colors[0])
        ax.plot(range(n), values, color=colors[0], linewidth=2)
        ax.set_xticks(range(n))
        ax.set_xticklabels(labels, rotation=20, ha="right", color="white")
    elif chart_type == "pie":
        wedges, texts, autotexts = ax.pie(
            values, labels=labels, autopct="%1.1f%%",
            colors=colors, startangle=140,
        )
        for el in texts + autotexts:
            el.set_color("white")

    ax.set_title(title, color="white", fontsize=16, pad=16, fontweight="bold")
    if xlabel:
        ax.set_xlabel(xlabel, color="#aaaaaa", fontsize=12)
    if ylabel:
        ax.set_ylabel(ylabel, color="#aaaaaa", fontsize=12)
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_edgecolor("#333333")
    if chart_type != "pie":
        ax.grid(axis="y", color="#2a2a2a", linestyle="--", linewidth=0.6)

    plt.tight_layout()
    out_path = _out(filename)
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    log.info("Graph saved: %s", out_path)
    return str(out_path)


def generate_insights_graph(platform: str, topic: str, insights: dict) -> str:
    """
    Auto-build an engagement-by-format bar chart from scraped insights.
    The AI generates realistic relative scores for each content format found.
    """
    formats = insights.get("content_formats", [])
    if not formats:
        formats = ["Video", "Image", "Text", "Carousel", "Story"]

    raw = _ai(
        system="You are a social media analyst. Return only valid JSON.",
        user=(
            f"For {platform} content about '{topic}', assign a relative engagement "
            f"score (0-100) to each of these content formats: {formats}. "
            "Base scores on known platform-specific engagement patterns. "
            f"Return JSON: {{\"scores\": {{\"<format>\": <score>, ...}}}}"
        ),
        temperature=0.2,
    )
    scores_data = _parse_json(raw).get("scores", {})
    labels = list(scores_data.keys()) or formats
    values = [scores_data.get(l, 50) for l in labels]

    slug = topic.replace(" ", "_")[:30]
    return generate_graph(
        data={"labels": labels, "values": values},
        chart_type="bar",
        title=f"Content Format Engagement — {topic.title()} on {platform.title()}",
        filename=f"{platform}_{slug}_graph.png",
        xlabel="Content Format",
        ylabel="Relative Engagement Score",
        color_scheme="plasma",
    )


# ---------------------------------------------------------------------------
# Video generation — moviepy + Pillow (no ImageMagick required)
# ---------------------------------------------------------------------------

def _text_frame(text: str, size: tuple, bg: str = "#111111") -> str:
    """Render a text card to a temp PNG using Pillow. Returns the file path."""
    import tempfile
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", size, color=bg)
    draw = ImageDraw.Draw(img)

    margin = size[0] // 10
    char_width = max(10, (size[0] - margin * 2) // 28)
    lines = textwrap.wrap(text, width=char_width)

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=52
        )
    except Exception:
        font = ImageFont.load_default()

    line_h = 70
    total_h = len(lines) * line_h
    y = (size[1] - total_h) // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (size[0] - (bbox[2] - bbox[0])) // 2
        draw.text((x + 3, y + 3), line, font=font, fill="#000000")  # shadow
        draw.text((x, y), line, font=font, fill="#FFFFFF")
        y += line_h

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name)
    return tmp.name


def _add_text_bar(image_path: str, text: str, size: tuple) -> str:
    """Add a semi-transparent bottom caption bar to an image. Returns new temp path."""
    import tempfile
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(image_path).convert("RGBA").resize(size)
    bar_h = size[1] // 7
    bar = Image.new("RGBA", (size[0], bar_h), (0, 0, 0, 175))
    draw = ImageDraw.Draw(bar)

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=38
        )
    except Exception:
        font = ImageFont.load_default()

    for i, line in enumerate(textwrap.wrap(text, width=38)[:3]):
        draw.text((24, 12 + i * 46), line, font=font, fill="white")

    img.paste(bar, (0, size[1] - bar_h), bar)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.convert("RGB").save(tmp.name)
    return tmp.name


def generate_video(
    slides: list[dict],
    filename: str,
    platform: str = "tiktok",
    fps: int = 24,
) -> str:
    """
    Assemble a short video from a list of slides.

    Each slide is a dict:
        image_path: str | None  — local image (None → renders a text card)
        text:       str | None  — overlay text (or card text if no image)
        duration:   int         — seconds per slide (default 3)

    Returns the path to the output MP4.
    """
    from moviepy.editor import ImageClip, concatenate_videoclips

    w, h = _PLATFORM_SIZES.get(platform.lower(), (1080, 1080))
    size = (w, h)
    clips = []
    tmp_files = []

    for slide in slides:
        duration = slide.get("duration", 3)
        img_path = slide.get("image_path")
        text = slide.get("text", "")

        if img_path and os.path.exists(img_path):
            if text:
                framed = _add_text_bar(img_path, text, size)
            else:
                from PIL import Image as PILImage
                framed = _text_frame("", size)  # dummy; replaced below
                img = PILImage.open(img_path).convert("RGB").resize(size)
                import tempfile
                tmp_f = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                img.save(tmp_f.name)
                framed = tmp_f.name
        else:
            framed = _text_frame(text or "…", size)

        tmp_files.append(framed)
        clips.append(ImageClip(framed, duration=duration))

    final = concatenate_videoclips(clips, method="compose")
    out_path = _out(filename)
    final.write_videofile(
        str(out_path), fps=fps, codec="libx264",
        audio=False, logger=None, preset="ultrafast",
    )
    final.close()

    for t in tmp_files:
        try:
            os.remove(t)
        except Exception:
            pass

    log.info("Video saved: %s", out_path)
    return str(out_path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def create_content_package(
    platform: str,
    topic: str,
    insights: dict,
    content_briefs: Optional[list] = None,
    content_types: Optional[list[str]] = None,
) -> dict:
    """
    Generate a full set of content assets for one platform and topic.

    Args:
        platform:       Target platform
        topic:          Content topic
        insights:       scrape_and_analyze()["insights"]
        content_briefs: scrape_and_analyze()["content_briefs"] (optional)
        content_types:  Which assets to create. Default: all four types.

    Returns:
        {
          "platform": str,
          "topic": str,
          "files": {"text": path, "image": path, "graph": path, "video": path},
          "output_dir": str,
        }
    """
    if content_types is None:
        content_types = ["text", "image", "graph", "video"]

    briefs = content_briefs or []
    primary_brief = briefs[0] if briefs else {}
    slug = topic.replace(" ", "_")[:30]
    files: dict = {}

    # ── Text ──────────────────────────────────────────────────────────────────
    if "text" in content_types:
        text_result = generate_text(platform, topic, insights, primary_brief)
        files["text"] = text_result["file"]
        files["_text_data"] = text_result

    # ── Image ─────────────────────────────────────────────────────────────────
    if "image" in content_types:
        style = primary_brief.get("format_notes", "")
        files["image"] = generate_platform_image(platform, topic, style, primary_brief)

    # ── Graph ─────────────────────────────────────────────────────────────────
    if "graph" in content_types:
        files["graph"] = generate_insights_graph(platform, topic, insights)

    # ── Video ─────────────────────────────────────────────────────────────────
    if "video" in content_types:
        text_data = files.get("_text_data", {})
        caption = text_data.get("caption") or f"Top content about {topic}"
        script = text_data.get("script") or ""
        script_lines = [l.strip() for l in script.split("\n") if l.strip()][:3]
        hook = primary_brief.get("hook", f"Everything about {topic} 👇")
        cta = primary_brief.get("cta", "Follow for more!")

        slides: list[dict] = [{"text": hook, "duration": 3}]

        if "image" in files:
            slides.append({
                "image_path": files["image"],
                "text": topic.title(),
                "duration": 4,
            })

        for line in script_lines:
            slides.append({"text": line, "duration": 3})

        if "graph" in files:
            slides.append({
                "image_path": files["graph"],
                "text": "Engagement by Format",
                "duration": 4,
            })

        slides.append({"text": cta, "duration": 3})

        files["video"] = generate_video(
            slides=slides,
            filename=f"{platform}_{slug}_video.mp4",
            platform=platform,
        )

    files.pop("_text_data", None)

    log.info(
        "Content package ready — %s / %s: %s",
        platform, topic, list(files.keys()),
    )
    return {
        "platform": platform,
        "topic": topic,
        "files": files,
        "output_dir": str(OUTPUT_DIR),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Generate social media content assets")
    parser.add_argument("platform", help="Target platform")
    parser.add_argument("topic", help="Content topic")
    parser.add_argument(
        "--insights-file", "-i",
        help="JSON file produced by scraper_agent.py (optional)",
    )
    parser.add_argument(
        "--types", nargs="+",
        default=["text", "image", "graph", "video"],
        choices=["text", "image", "graph", "video"],
        help="Content types to generate",
    )
    args = parser.parse_args()

    insights: dict = {}
    content_briefs: list = []
    if args.insights_file:
        with open(args.insights_file) as fh:
            data = json.load(fh)
        insights = data.get("insights", data)
        content_briefs = data.get("content_briefs", [])

    package = create_content_package(
        args.platform, args.topic, insights, content_briefs, args.types
    )

    print(f"\nGenerated files in {package['output_dir']}:")
    for t, path in package["files"].items():
        print(f"  {t:10s} → {path}")
