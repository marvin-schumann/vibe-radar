"""Shareable card image generator for Frequenz.

Generates Instagram-story-sized PNG cards (1080x1920) from taste DNA data.
Renders at 2x (2160x3840) and downscales with LANCZOS for anti-aliasing.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

BG = (12, 12, 12)
LIME = (196, 241, 53)
ORANGE = (255, 92, 53)
WHITE = (245, 245, 245)
MUTED = (160, 160, 160)

# Canvas at 2x for anti-aliasing
W, H = 2160, 3840
FINAL_W, FINAL_H = 1080, 1920

# Safe zone (scaled to 2x)
SAFE_TOP = 500
SAFE_BOT = 500
CONTENT_TOP = SAFE_TOP + 40
CONTENT_BOT = H - SAFE_BOT - 40

FONTS_DIR = Path(__file__).parent.parent / "web" / "static" / "fonts"

# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a cached font. name is one of: bebas, space, space_bold, dm, dm_italic."""
    key = (name, size)
    if key not in _font_cache:
        mapping = {
            "bebas": "BebasNeue-Regular.ttf",
            "space": "SpaceMono-Regular.ttf",
            "space_bold": "SpaceMono-Bold.ttf",
            "dm": "DMSans-Regular.ttf",
            "dm_italic": "DMSans-Italic.ttf",
        }
        path = FONTS_DIR / mapping[name]
        _font_cache[key] = ImageFont.truetype(str(path), size)
    return _font_cache[key]


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


def _new_canvas() -> Image.Image:
    """Create a 2x canvas with the background color."""
    return Image.new("RGBA", (W, H), BG + (255,))


def _radial_glow(
    img: Image.Image,
    cx: int,
    cy: int,
    radius: int,
    color: tuple[int, int, int],
    peak_alpha: int = 50,
) -> None:
    """Draw a soft radial glow centered at (cx, cy) using NumPy."""
    # Work on a bounding box for efficiency
    x0 = max(cx - radius, 0)
    y0 = max(cy - radius, 0)
    x1 = min(cx + radius, W)
    y1 = min(cy + radius, H)
    if x1 <= x0 or y1 <= y0:
        return

    xs = np.arange(x0, x1) - cx
    ys = np.arange(y0, y1) - cy
    xx, yy = np.meshgrid(xs, ys)
    dist = np.sqrt(xx * xx + yy * yy)

    alpha = np.clip(1.0 - dist / radius, 0.0, 1.0) ** 2
    alpha = (alpha * peak_alpha).astype(np.uint8)

    glow = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    glow_arr = np.array(glow)
    glow_arr[:, :, 0] = color[0]
    glow_arr[:, :, 1] = color[1]
    glow_arr[:, :, 2] = color[2]
    glow_arr[:, :, 3] = alpha
    glow = Image.fromarray(glow_arr, "RGBA")
    img.alpha_composite(glow, (x0, y0))


def _diagonal_gradient(
    img: Image.Image,
    color: tuple[int, int, int],
    peak_alpha: int = 30,
    direction: str = "tl_br",
) -> None:
    """Apply a subtle diagonal gradient overlay."""
    arr = np.zeros((H, W, 4), dtype=np.uint8)
    xs = np.linspace(0, 1, W)
    ys = np.linspace(0, 1, H)
    xx, yy = np.meshgrid(xs, ys)

    if direction == "tl_br":
        factor = (xx + yy) / 2.0
    elif direction == "tr_bl":
        factor = ((1 - xx) + yy) / 2.0
    elif direction == "bl_tr":
        factor = (xx + (1 - yy)) / 2.0
    else:
        factor = ((1 - xx) + (1 - yy)) / 2.0

    arr[:, :, 0] = color[0]
    arr[:, :, 1] = color[1]
    arr[:, :, 2] = color[2]
    arr[:, :, 3] = (factor * peak_alpha).astype(np.uint8)

    overlay = Image.fromarray(arr, "RGBA")
    img.alpha_composite(overlay)


def _draw_branding(draw: ImageDraw.ImageDraw) -> None:
    """Draw 'frequenz.live' branding at bottom center."""
    font = _font("space", 40)
    text = "frequenz.live"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (W - tw) // 2
    y = H - SAFE_BOT + 80
    draw.text((x, y), text, fill=MUTED, font=font)


def _draw_rounded_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    fill: tuple[int, ...],
    radius: int = 16,
) -> None:
    """Draw a rounded rectangle bar."""
    draw.rounded_rectangle(
        [x, y, x + width, y + height],
        radius=radius,
        fill=fill,
    )


def _lerp_color(
    c1: tuple[int, int, int],
    c2: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    """Linearly interpolate between two colors."""
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _finalize(img: Image.Image) -> bytes:
    """Downscale 2x canvas to 1x and return PNG bytes."""
    final = img.convert("RGB").resize((FINAL_W, FINAL_H), Image.LANCZOS)
    buf = io.BytesIO()
    final.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Card 1: Taste DNA Summary
# ---------------------------------------------------------------------------


def generate_taste_dna_card(data: dict[str, Any]) -> bytes:
    """Hero card: top 5 genres as horizontal bars, total artists, underground depth."""
    img = _new_canvas()
    draw = ImageDraw.Draw(img)

    top_genres = data.get("top_genres", [])[:5]
    total_artists = data.get("total_artists", 0)

    # Radial glow behind genre bars area
    _radial_glow(img, W // 2, H // 2 - 200, 900, LIME, peak_alpha=40)

    # Draw on top of glow
    draw = ImageDraw.Draw(img)

    # Title
    title_font = _font("bebas", 140)
    draw.text((120, CONTENT_TOP), "YOUR TASTE DNA", fill=WHITE, font=title_font)

    # Subtitle
    sub_font = _font("dm", 48)
    draw.text(
        (120, CONTENT_TOP + 160),
        f"{total_artists} artists analysed",
        fill=MUTED,
        font=sub_font,
    )

    # Genre bars
    bar_start_y = CONTENT_TOP + 320
    bar_max_w = W - 240
    max_pct = max((g.get("percentage", g.get("count", 1)) for g in top_genres), default=1)

    for i, genre_item in enumerate(top_genres):
        y = bar_start_y + i * 200
        genre_name = genre_item.get("genre", genre_item.get("name", ""))
        pct = genre_item.get("percentage", 0)

        # Bar width proportional to max
        bar_w = max(int(bar_max_w * pct / max(max_pct, 1)), 80)
        t = i / max(len(top_genres) - 1, 1)
        bar_color = _lerp_color(LIME, ORANGE, t)

        _draw_rounded_bar(draw, 120, y + 60, bar_w, 60, bar_color, radius=30)

        # Genre name
        name_font = _font("bebas", 72)
        draw.text((120, y), genre_name.upper(), fill=WHITE, font=name_font)

        # Percentage
        pct_font = _font("space_bold", 56)
        pct_text = f"{pct}%"
        draw.text((bar_w + 160, y + 62), pct_text, fill=WHITE, font=pct_font)

    # Tagline at bottom
    tagline_y = CONTENT_BOT - 200
    tagline_font = _font("dm_italic", 52)
    draw.text(
        (120, tagline_y),
        "Your music fingerprint, decoded.",
        fill=MUTED,
        font=tagline_font,
    )

    # Underground depth percentile (based on genre diversity)
    depth_font = _font("space", 44)
    depth_pct = min(95, max(5, 100 - int(total_artists / 2)))
    draw.text(
        (120, tagline_y + 80),
        f"Underground depth: top {depth_pct}%",
        fill=LIME,
        font=depth_font,
    )

    _draw_branding(draw)
    return _finalize(img)


# ---------------------------------------------------------------------------
# Card 2: Scene — Home City
# ---------------------------------------------------------------------------


def generate_scene_city_card(data: dict[str, Any]) -> bytes:
    """User's top city in huge text with city breakdown percentages."""
    img = _new_canvas()

    # Subtle orange diagonal gradient
    _diagonal_gradient(img, ORANGE, peak_alpha=25, direction="tl_br")

    draw = ImageDraw.Draw(img)

    scene = data.get("scene_city", {})
    cities = scene.get("cities", [])
    top_city = cities[0]["city"] if cities else "UNKNOWN"

    # Huge city name
    city_font = _font("bebas", 280)
    bbox = draw.textbbox((0, 0), top_city.upper(), font=city_font)
    tw = bbox[2] - bbox[0]
    x = (W - tw) // 2
    draw.text((x, CONTENT_TOP + 200), top_city.upper(), fill=WHITE, font=city_font)

    # "Your sonic home" label
    label_font = _font("dm", 52)
    label_text = "Your sonic home"
    lbbox = draw.textbbox((0, 0), label_text, font=label_font)
    lw = lbbox[2] - lbbox[0]
    draw.text(((W - lw) // 2, CONTENT_TOP + 80), label_text, fill=MUTED, font=label_font)

    # City breakdown
    breakdown_y = CONTENT_TOP + 650
    for i, city_item in enumerate(cities[:5]):
        y = breakdown_y + i * 140
        city_name = city_item["city"]
        pct = city_item["percentage"]

        # Percentage in Space Mono Bold
        pct_font = _font("space_bold", 72)
        draw.text((120, y), f"{pct}%", fill=LIME if i == 0 else WHITE, font=pct_font)

        # City name in DM Sans
        name_font = _font("dm", 56)
        draw.text((420, y + 10), city_name, fill=WHITE if i == 0 else MUTED, font=name_font)

        # Thin bar
        bar_w = int((W - 600) * pct / 100)
        bar_color = LIME if i == 0 else (60, 60, 60)
        _draw_rounded_bar(draw, 120, y + 100, max(bar_w, 20), 12, bar_color, radius=6)

    # Hashtag
    hashtag_font = _font("space", 48)
    hashtag = "#FrequenzCity"
    hbbox = draw.textbbox((0, 0), hashtag, font=hashtag_font)
    hw = hbbox[2] - hbbox[0]
    draw.text(((W - hw) // 2, CONTENT_BOT - 120), hashtag, fill=ORANGE, font=hashtag_font)

    _draw_branding(draw)
    return _finalize(img)


# ---------------------------------------------------------------------------
# Card 3: Taste Tribe
# ---------------------------------------------------------------------------


def generate_taste_tribe_card(data: dict[str, Any]) -> bytes:
    """Tribe name in huge stacked text with tagline and rarity."""
    img = _new_canvas()

    tribe_data = data.get("taste_tribe", {})
    tribe = tribe_data.get("tribe", {}) or {}
    tribe_name = tribe.get("name", "Unknown Tribe")
    tagline = tribe.get("tagline", "")
    confidence = tribe.get("confidence", 50)
    description = tribe.get("description", "")

    # Split tribe name into words for stacked text
    words = tribe_name.upper().split()

    # Radial glow behind tribe name
    _radial_glow(img, W // 2, H // 2 - 400, 800, LIME, peak_alpha=45)

    draw = ImageDraw.Draw(img)

    # "Your Taste Tribe" header
    header_font = _font("dm", 52)
    draw.text((120, CONTENT_TOP), "Your Taste Tribe", fill=MUTED, font=header_font)

    # Stacked tribe name — line 1 white, line 2 lime
    name_font = _font("bebas", 220)
    y = CONTENT_TOP + 160
    for i, word in enumerate(words):
        color = WHITE if i == 0 else LIME
        draw.text((120, y), word, fill=color, font=name_font)
        y += 220

    # Tagline in italic
    tagline_font = _font("dm_italic", 56)
    draw.text((120, y + 40), f'"{tagline}"', fill=MUTED, font=tagline_font)

    # Rarity / confidence
    rarity_y = y + 180
    rarity_font = _font("space_bold", 52)
    # Simulated rarity — based on confidence inverted
    rarity_pct = max(3, min(30, 100 - confidence))
    draw.text(
        (120, rarity_y),
        f"{rarity_pct}% of Frequenz users share this tribe",
        fill=LIME,
        font=rarity_font,
    )

    # Description as trait bullets
    bullet_y = rarity_y + 140
    bullet_font = _font("dm", 44)
    # Split description into ~3 bullet-sized chunks
    desc_words = description.split()
    chunk_size = max(1, len(desc_words) // 3)
    chunks = []
    for j in range(0, len(desc_words), chunk_size):
        chunks.append(" ".join(desc_words[j : j + chunk_size]))
    for j, chunk in enumerate(chunks[:3]):
        draw.text(
            (170, bullet_y + j * 80),
            f"\u2022  {chunk}",
            fill=MUTED,
            font=bullet_font,
        )

    _draw_branding(draw)
    return _finalize(img)


# ---------------------------------------------------------------------------
# Card 4: Cross-Genre Bridge
# ---------------------------------------------------------------------------


def generate_cross_genre_card(data: dict[str, Any]) -> bytes:
    """Two overlapping circles with genre names, bridge rarity, artist names."""
    img = _new_canvas()

    bridges_data = data.get("cross_genre_bridges", {})
    bridges = bridges_data.get("bridges", [])

    # Use the rarest bridge as the hero
    bridge = bridges[0] if bridges else {
        "genre_a": "house",
        "genre_b": "rock/metal",
        "rarity_pct": 3,
    }

    genre_a = bridge["genre_a"]
    genre_b = bridge["genre_b"]
    rarity = bridge.get("rarity_pct", 5)

    # Circle parameters
    circle_r = 420
    cx1 = W // 2 - 250
    cx2 = W // 2 + 250
    cy = H // 2 - 200

    # Dual radial glows
    _radial_glow(img, cx1, cy, circle_r + 100, LIME, peak_alpha=30)
    _radial_glow(img, cx2, cy, circle_r + 100, ORANGE, peak_alpha=30)

    draw = ImageDraw.Draw(img)

    # Header
    header_font = _font("dm", 52)
    draw.text((120, CONTENT_TOP), "Cross-Genre Bridge", fill=MUTED, font=header_font)

    # Draw circle strokes
    stroke_w = 6
    # Lime circle (left)
    for offset in range(stroke_w):
        draw.ellipse(
            [cx1 - circle_r + offset, cy - circle_r + offset,
             cx1 + circle_r - offset, cy + circle_r - offset],
            outline=LIME + (180,),
        )
    # Orange circle (right)
    for offset in range(stroke_w):
        draw.ellipse(
            [cx2 - circle_r + offset, cy - circle_r + offset,
             cx2 + circle_r - offset, cy + circle_r - offset],
            outline=ORANGE + (180,),
        )

    # Overlap fill at 12% opacity
    overlap = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    overlap_draw = ImageDraw.Draw(overlap)
    # Create masks for intersection
    mask1 = Image.new("L", (W, H), 0)
    mask2 = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask1).ellipse(
        [cx1 - circle_r, cy - circle_r, cx1 + circle_r, cy + circle_r], fill=255
    )
    ImageDraw.Draw(mask2).ellipse(
        [cx2 - circle_r, cy - circle_r, cx2 + circle_r, cy + circle_r], fill=255
    )
    # Intersection
    intersection = np.minimum(np.array(mask1), np.array(mask2))
    blend_color = _lerp_color(LIME, ORANGE, 0.5)
    overlap_arr = np.zeros((H, W, 4), dtype=np.uint8)
    overlap_arr[:, :, 0] = blend_color[0]
    overlap_arr[:, :, 1] = blend_color[1]
    overlap_arr[:, :, 2] = blend_color[2]
    overlap_arr[:, :, 3] = (intersection * 0.12).astype(np.uint8)
    overlap_img = Image.fromarray(overlap_arr, "RGBA")
    img.alpha_composite(overlap_img)

    # Redraw text on top
    draw = ImageDraw.Draw(img)

    # Genre names inside circles
    genre_font = _font("bebas", 80)
    # Left genre
    a_bbox = draw.textbbox((0, 0), genre_a.upper(), font=genre_font)
    a_w = a_bbox[2] - a_bbox[0]
    draw.text((cx1 - circle_r // 2 - a_w // 2, cy - 40), genre_a.upper(), fill=LIME, font=genre_font)
    # Right genre
    b_bbox = draw.textbbox((0, 0), genre_b.upper(), font=genre_font)
    b_w = b_bbox[2] - b_bbox[0]
    draw.text((cx2 + circle_r // 2 - b_w // 2, cy - 40), genre_b.upper(), fill=ORANGE, font=genre_font)

    # Bridge rarity % in center overlap
    rarity_font = _font("space_bold", 100)
    rarity_text = f"{rarity}%"
    r_bbox = draw.textbbox((0, 0), rarity_text, font=rarity_font)
    r_w = r_bbox[2] - r_bbox[0]
    draw.text(((W - r_w) // 2, cy - 60), rarity_text, fill=WHITE, font=rarity_font)

    bridge_label_font = _font("dm", 40)
    bridge_label = "of listeners bridge these"
    bl_bbox = draw.textbbox((0, 0), bridge_label, font=bridge_label_font)
    bl_w = bl_bbox[2] - bl_bbox[0]
    draw.text(((W - bl_w) // 2, cy + 60), bridge_label, fill=MUTED, font=bridge_label_font)

    # Additional bridges below
    other_y = cy + circle_r + 200
    if len(bridges) > 1:
        other_font = _font("dm", 44)
        draw.text((120, other_y - 60), "Other bridges:", fill=MUTED, font=_font("dm", 40))
        for i, b in enumerate(bridges[1:3]):
            draw.text(
                (120, other_y + i * 90),
                f"{b['genre_a']}  \u00d7  {b['genre_b']}  \u2014  {b['rarity_pct']}%",
                fill=WHITE,
                font=other_font,
            )

    _draw_branding(draw)
    return _finalize(img)


# ---------------------------------------------------------------------------
# Card 5: Dancefloor vs Headphones
# ---------------------------------------------------------------------------


def generate_dancefloor_card(data: dict[str, Any]) -> bytes:
    """Split bar with DANCEFLOOR in orange vs HEADPHONES in lime."""
    img = _new_canvas()

    # Diagonal color shift background
    _diagonal_gradient(img, ORANGE, peak_alpha=20, direction="bl_tr")
    _diagonal_gradient(img, LIME, peak_alpha=15, direction="tr_bl")

    draw = ImageDraw.Draw(img)

    df_data = data.get("dancefloor_ratio", {})
    df_pct = df_data.get("dancefloor_pct", 50)
    hp_pct = df_data.get("headphones_pct", 50)
    label = df_data.get("label", "Balanced")

    # "DANCEFLOOR" big text in orange
    df_font = _font("bebas", 180)
    draw.text((120, CONTENT_TOP + 100), "DANCEFLOOR", fill=ORANGE, font=df_font)

    # "vs" in muted, centered
    vs_font = _font("dm_italic", 72)
    vs_bbox = draw.textbbox((0, 0), "vs", font=vs_font)
    vs_w = vs_bbox[2] - vs_bbox[0]
    draw.text(((W - vs_w) // 2, CONTENT_TOP + 340), "vs", fill=MUTED, font=vs_font)

    # "HEADPHONES" big text in lime
    hp_font = _font("bebas", 180)
    hp_bbox = draw.textbbox((0, 0), "HEADPHONES", font=hp_font)
    hp_w = hp_bbox[2] - hp_bbox[0]
    draw.text((W - hp_w - 120, CONTENT_TOP + 460), "HEADPHONES", fill=LIME, font=hp_font)

    # Split bar
    bar_y = CONTENT_TOP + 820
    bar_h = 100
    bar_total_w = W - 240
    bar_x = 120

    # Orange portion
    df_bar_w = max(int(bar_total_w * df_pct / 100), 40)
    hp_bar_w = bar_total_w - df_bar_w

    # Draw rounded rect for the whole bar (background)
    _draw_rounded_bar(draw, bar_x, bar_y, bar_total_w, bar_h, (30, 30, 30), radius=50)

    # Orange portion (left)
    if df_pct > 0:
        _draw_rounded_bar(draw, bar_x, bar_y, df_bar_w, bar_h, ORANGE, radius=50)

    # Lime portion (right) — only if headphones > ~10%
    if hp_pct > 10:
        # We overlap slightly so the right side looks rounded too
        _draw_rounded_bar(
            draw,
            bar_x + df_bar_w,
            bar_y,
            hp_bar_w,
            bar_h,
            LIME,
            radius=50,
        )

    # Percentage labels above bar
    pct_font = _font("space_bold", 80)
    draw.text((bar_x + 20, bar_y - 120), f"{df_pct}%", fill=ORANGE, font=pct_font)

    hp_text = f"{hp_pct}%"
    hp_pct_bbox = draw.textbbox((0, 0), hp_text, font=pct_font)
    hp_pct_w = hp_pct_bbox[2] - hp_pct_bbox[0]
    draw.text(
        (bar_x + bar_total_w - hp_pct_w - 20, bar_y - 120),
        hp_text,
        fill=LIME,
        font=pct_font,
    )

    # Verdict label
    verdict_y = bar_y + bar_h + 140
    verdict_font = _font("dm", 56)
    vbbox = draw.textbbox((0, 0), label, font=verdict_font)
    vw = vbbox[2] - vbbox[0]
    draw.text(((W - vw) // 2, verdict_y), label, fill=WHITE, font=verdict_font)

    # Decorative detail line
    line_y = verdict_y + 100
    draw.line([(W // 2 - 200, line_y), (W // 2 + 200, line_y)], fill=MUTED + (80,), width=2)

    _draw_branding(draw)
    return _finalize(img)


# ---------------------------------------------------------------------------
# Generate all cards
# ---------------------------------------------------------------------------


def generate_all_cards(taste_dna_data: dict[str, Any]) -> dict[str, bytes]:
    """Generate all 5 cards from taste DNA data.

    Args:
        taste_dna_data: dict with keys:
            - top_genres: list of {genre, percentage}
            - total_artists: int
            - scene_city: {cities: [{city, percentage, flag}]}
            - taste_tribe: {tribe: {name, tagline, description, confidence}}
            - cross_genre_bridges: {bridges: [{genre_a, genre_b, rarity_pct}]}
            - dancefloor_ratio: {dancefloor_pct, headphones_pct, label}

    Returns:
        Dict mapping card name to PNG bytes.
    """
    return {
        "taste-dna": generate_taste_dna_card(taste_dna_data),
        "scene-city": generate_scene_city_card(taste_dna_data),
        "taste-tribe": generate_taste_tribe_card(taste_dna_data),
        "cross-genre": generate_cross_genre_card(taste_dna_data),
        "dancefloor": generate_dancefloor_card(taste_dna_data),
    }
