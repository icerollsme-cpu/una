"""
make_rebo_tiktok.py — Standalone script to create a TikTok short for rebosafari.com.

Generates a 1080x1920 MP4 (~30s) using:
  - Pillow for all frames (gradient backgrounds, silhouettes, text overlays)
  - moviepy for video assembly

Fully offline — no API keys, no external services required.

Run:  python make_rebo_tiktok.py
"""

import os
import math
import textwrap
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUTPUT_DIR = Path("output/content")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = 1080, 1920          # TikTok vertical format
FPS = 24
FONT_PATH_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


# ---------------------------------------------------------------------------
# Slide definitions — each slide has theme colors, icon keyword, text
# ---------------------------------------------------------------------------

SLIDES = [
    {
        "type": "graphic",
        "theme": "hook",
        "headline": "You can look a mountain gorilla in the eye",
        "caption": "",
        "emoji": "🦍",
        "colors": ("#0a1a0a", "#1a4a1a", "#2d7a2d"),
        "accent": "#FFD700",
        "icon": "ape",
        "duration": 3,
        "filename": "rebo_slide_1.png",
    },
    {
        "type": "graphic",
        "theme": "scene",
        "headline": "Bwindi Impenetrable Forest",
        "caption": "UNESCO World Heritage · Uganda",
        "emoji": "🌿",
        "colors": ("#041404", "#0d2e0d", "#1a5a1a"),
        "accent": "#7FFF00",
        "icon": "forest",
        "duration": 4,
        "filename": "rebo_slide_2.png",
    },
    {
        "type": "graphic",
        "theme": "scene",
        "headline": "Kibale Forest",
        "caption": "Highest chimpanzee density on Earth",
        "emoji": "🐒",
        "colors": ("#1a0d04", "#3d2010", "#6b3a1a"),
        "accent": "#FFA500",
        "icon": "chimp",
        "duration": 4,
        "filename": "rebo_slide_3.png",
    },
    {
        "type": "graphic",
        "theme": "scene",
        "headline": "Tree-climbing lions of Ishasha",
        "caption": "Queen Elizabeth National Park",
        "emoji": "🦁",
        "colors": ("#1a1000", "#3d2c00", "#8B6914"),
        "accent": "#FFD700",
        "icon": "lion",
        "duration": 3,
        "filename": "rebo_slide_4.png",
    },
    {
        "type": "graphic",
        "theme": "scene",
        "headline": "Kazinga Channel",
        "caption": "Hippos · Elephants · 600+ bird species",
        "emoji": "🦛",
        "colors": ("#00101a", "#002d4a", "#005580"),
        "accent": "#00BFFF",
        "icon": "water",
        "duration": 4,
        "filename": "rebo_slide_5.png",
    },
    {
        "type": "graphic",
        "theme": "scene",
        "headline": "Nile Safari Lodge",
        "caption": "Luxury on the banks of the Victoria Nile",
        "emoji": "🌙",
        "colors": ("#04060a", "#0d1a2e", "#1a2e4a"),
        "accent": "#FFD700",
        "icon": "stars",
        "duration": 3,
        "filename": "rebo_slide_6.png",
    },
    {
        "type": "cta",
        "headline": "Private Uganda safaris — fully tailored",
        "subtext": "rebosafari.com",
        "emoji": "🌍",
        "colors": ("#0a1a0a", "#1a4a1a", "#2d7a2d"),
        "accent": "#FFD700",
        "duration": 4,
        "filename": "rebo_slide_7.png",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = FONT_PATH_BOLD if bold else FONT_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _draw_gradient(img: Image.Image, colors: tuple) -> None:
    """Draw a vertical multi-stop gradient onto the image."""
    draw = ImageDraw.Draw(img)
    c1 = tuple(int(colors[0].lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    c2 = tuple(int(colors[1].lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    c3 = tuple(int(colors[2].lstrip("#")[i:i+2], 16) for i in (0, 2, 4))

    half = H // 2
    for y in range(half):
        t = y / half
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    for y in range(half, H):
        t = (y - half) / half
        r = int(c2[0] + (c3[0] - c2[0]) * t)
        g = int(c2[1] + (c3[1] - c2[1]) * t)
        b = int(c2[2] + (c3[2] - c2[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def _hex_to_rgb(hex_color: str, alpha: int = 255) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4)) + (alpha,)


def _draw_decorative_circles(draw: ImageDraw.Draw, accent: str) -> None:
    """Draw faint concentric circles for visual depth."""
    ar, ag, ab = (int(accent.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    cx, cy = W // 2, H // 2
    for radius in [600, 750, 900]:
        for i in range(360):
            angle = math.radians(i)
            x = cx + radius * math.cos(angle)
            y = cy + radius * math.sin(angle)
            draw.ellipse([(x-2, y-2), (x+2, y+2)], fill=(ar, ag, ab, 15))


def _draw_icon(img: Image.Image, icon: str, accent: str, cx: int, cy: int, size: int = 300) -> None:
    """Draw a simple vector-style icon onto the RGBA image."""
    draw = ImageDraw.Draw(img)
    ac = _hex_to_rgb(accent, 60)  # faint fill
    ac_solid = _hex_to_rgb(accent, 200)

    if icon == "ape":
        # Silhouette: head + shoulders shape
        draw.ellipse([(cx - size//3, cy - size//2), (cx + size//3, cy + size//8)], fill=ac)
        draw.ellipse([(cx - size//2, cy + size//8), (cx + size//2, cy + size//2)], fill=ac)

    elif icon == "forest":
        # Triangle trees
        for ox in [-size//2, 0, size//2]:
            pts = [(cx + ox, cy - size//2 + abs(ox)//2),
                   (cx + ox - size//4, cy + size//4),
                   (cx + ox + size//4, cy + size//4)]
            draw.polygon(pts, fill=ac)

    elif icon == "chimp":
        # Circular body with arm arcs
        draw.ellipse([(cx - size//3, cy - size//3), (cx + size//3, cy + size//3)], fill=ac)
        draw.arc([(cx - size//2, cy - size//2), (cx + size//2, cy + size//2)],
                 start=200, end=340, fill=ac_solid, width=12)

    elif icon == "lion":
        # Mane circle + inner face
        draw.ellipse([(cx - size//2, cy - size//2), (cx + size//2, cy + size//2)], fill=ac)
        draw.ellipse([(cx - size//3, cy - size//3), (cx + size//3, cy + size//3)],
                     fill=_hex_to_rgb(accent, 100))

    elif icon == "water":
        # Wave lines
        for i, y_off in enumerate([-size//4, 0, size//4]):
            pts = []
            for x in range(cx - size//2, cx + size//2, 10):
                wave_y = cy + y_off + int(20 * math.sin((x - cx) * 0.04 + i))
                pts.append((x, wave_y))
            if len(pts) > 1:
                draw.line(pts, fill=ac_solid, width=6)

    elif icon == "stars":
        # Scattered star dots
        import random
        random.seed(42)
        for _ in range(60):
            sx = random.randint(cx - size, cx + size)
            sy = random.randint(cy - size, cy + size)
            r = random.randint(2, 6)
            draw.ellipse([(sx - r, sy - r), (sx + r, sy + r)], fill=ac_solid)


def _draw_text_centered(draw: ImageDraw.Draw, text: str, y: int,
                         font: ImageFont.FreeTypeFont, color: str,
                         shadow: bool = True, max_width: int = 20) -> int:
    """Draw centered wrapped text, return y position after last line."""
    wrapped = textwrap.wrap(text, width=max_width)
    for line in wrapped:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        x = (W - lw) // 2
        if shadow:
            draw.text((x + 3, y + 3), line, font=font, fill="#00000099")
        draw.text((x, y), line, font=font, fill=color)
        y += (bbox[3] - bbox[1]) + 16
    return y


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------

def make_graphic_slide(slide: dict) -> str:
    """Build a themed graphic slide. Returns PNG path."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    _draw_gradient(img, slide["colors"])

    draw = ImageDraw.Draw(img)

    # Decorative geometric ring
    accent = slide["accent"]
    ar, ag, ab = (int(accent.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    for r in range(380, 480, 20):
        draw.ellipse([(W//2 - r, H//2 - r), (W//2 + r, H//2 + r)],
                     outline=(ar, ag, ab, 18), width=2)

    # Icon / silhouette in center
    icon = slide.get("icon", "")
    if icon:
        _draw_icon(img, icon, accent, W // 2, H // 2)

    draw = ImageDraw.Draw(img)

    # Accent bars top and bottom
    draw.rectangle([(0, 0), (W, 10)], fill=accent)
    draw.rectangle([(0, H - 10), (W, H)], fill=accent)

    # Emoji large, centered above text
    font_emoji = _load_font(140, bold=True)
    emoji = slide.get("emoji", "")
    if emoji:
        bbox = draw.textbbox((0, 0), emoji, font=font_emoji)
        ex = (W - (bbox[2] - bbox[0])) // 2
        draw.text((ex, H // 2 - 340), emoji, font=font_emoji, fill=accent)

    # Headline
    font_h = _load_font(80, bold=True)
    y = _draw_text_centered(draw, slide["headline"], H // 2 + 120, font_h, accent, max_width=16)

    # Caption
    caption = slide.get("caption", "")
    if caption:
        font_c = _load_font(52, bold=False)
        y += 20
        _draw_text_centered(draw, caption, y, font_c, "#FFFFFFCC", max_width=24)

    # Watermark
    font_wm = _load_font(30, bold=False)
    draw.text((40, H - 70), "rebosafari.com", font=font_wm, fill="#55555588")

    out = OUTPUT_DIR / slide["filename"]
    img.convert("RGB").save(str(out), quality=95)
    return str(out)


def make_cta_slide(slide: dict) -> str:
    """Build a CTA slide. Returns PNG path."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    _draw_gradient(img, slide["colors"])

    draw = ImageDraw.Draw(img)
    accent = slide["accent"]
    ar, ag, ab = (int(accent.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))

    # Large filled circle background
    draw.ellipse([(W//2 - 480, H//2 - 480), (W//2 + 480, H//2 + 480)],
                 fill=(ar, ag, ab, 18))
    for r in [350, 400, 450]:
        draw.ellipse([(W//2 - r, H//2 - r), (W//2 + r, H//2 + r)],
                     outline=(ar, ag, ab, 30), width=3)

    # Accent bars
    draw.rectangle([(0, 0), (W, 12)], fill=accent)
    draw.rectangle([(0, H - 12), (W, H)], fill=accent)

    # Emoji
    font_emoji = _load_font(180, bold=True)
    emoji = slide.get("emoji", "🌍")
    bbox = draw.textbbox((0, 0), emoji, font=font_emoji)
    ex = (W - (bbox[2] - bbox[0])) // 2
    draw.text((ex, H // 2 - 420), emoji, font=font_emoji, fill=accent)

    # Headline
    font_h = _load_font(72, bold=True)
    y = _draw_text_centered(draw, slide["headline"], H // 2 + 60, font_h, "#FFFFFF", max_width=18)

    # URL — prominent
    y += 50
    font_url = _load_font(88, bold=True)
    bbox = draw.textbbox((0, 0), slide["subtext"], font=font_url)
    ux = (W - (bbox[2] - bbox[0])) // 2
    draw.text((ux + 3, y + 3), slide["subtext"], font=font_url, fill="#000000AA")
    draw.text((ux, y), slide["subtext"], font=font_url, fill=accent)

    # Tagline
    y += 120
    font_tag = _load_font(44, bold=False)
    _draw_text_centered(draw, "Link in bio  ·  Book your journey", y, font_tag, "#FFFFFFAA", max_width=30)

    out = OUTPUT_DIR / slide["filename"]
    img.convert("RGB").save(str(out), quality=95)
    return str(out)


# ---------------------------------------------------------------------------
# Main video assembly
# ---------------------------------------------------------------------------

def build_video(output_filename: str = "rebo_safari_tiktok.mp4") -> str:
    from moviepy import ImageClip, concatenate_videoclips

    print("\n== Building Rebo Safari TikTok ==")
    clips = []

    for i, slide in enumerate(SLIDES):
        print(f"  Slide {i+1}/{len(SLIDES)}: {slide.get('headline', '')[:40]}")

        if slide["type"] == "cta":
            frame = make_cta_slide(slide)
        else:
            frame = make_graphic_slide(slide)

        dur = slide["duration"]
        clip = ImageClip(frame).with_duration(dur)

        # Subtle zoom-in effect (Ken Burns)
        clip = clip.resized(lambda t, d=dur: 1 + 0.04 * t / d)
        clip = clip.with_position("center")

        clips.append(clip)

    print("\nAssembling video…")
    final = concatenate_videoclips(clips, method="compose")
    out_path = OUTPUT_DIR / output_filename
    final.write_videofile(
        str(out_path),
        fps=FPS,
        codec="libx264",
        audio=False,
        logger="bar",
        preset="fast",
    )
    final.close()

    total_dur = sum(s["duration"] for s in SLIDES)
    size_kb = out_path.stat().st_size // 1024
    print(f"\n✓ Video ready: {out_path}")
    print(f"  Duration : {total_dur}s")
    print(f"  Format   : {W}x{H} (TikTok vertical)")
    print(f"  Size     : {size_kb} KB")
    return str(out_path)


if __name__ == "__main__":
    build_video()
