"""Card Composer — produces the final 1080x1920 Frequenz share card.

This is the viral artifact. Given a character slug + the user's taste DNA
output from compute_taste_dna(), it composites:

  Layer 1: dark gradient background with subtle grain
  Layer 2: the character image (full or upper-body PNG)
  Layer 3: typography overlay
    - "FREQUENZ" branding (vertical, left edge)
    - Character name (huge, top)
    - Character voice line (italic, below name)
    - Top stat: scene city + percentage
    - Top tribe / underground depth
    - First matched event teaser (artist + venue + date + match score)
    - "frequenz.live" small footer

Output: 1080x1920 PNG, ready to attach to a DM or Instagram story.

Used by:
- scripts/generate_dj_seed_cards.py (pre-generation for the April 13 DM round)
- src/api/scan.py reveal stage (when the user finishes a scan)
- Future: GET /api/cards/{user_id} endpoint that lets users re-download their card

Design approach: characters are the hero, typography wraps around them, the
data is secondary. Reads first as a character + name, second as the data.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

BG = (12, 12, 12)
LIME = (196, 241, 53)
ORANGE = (255, 92, 53)
WHITE = (245, 245, 245)
MUTED = (160, 160, 160)
DIM = (90, 90, 90)
ACCENT_RED = (255, 0, 60)

# Render at 2x for anti-aliasing, then downscale
W, H = 2160, 3840
FINAL_W, FINAL_H = 1080, 1920

# Margins
MARGIN_X = 96
MARGIN_TOP = 120
MARGIN_BOT = 120

# Where the character image lives in the canvas
CHAR_BOX = (200, 700, W - 200, 2900)  # left, top, right, bottom

ROOT = Path(__file__).parent.parent
FONTS_DIR = ROOT / "web" / "static" / "fonts"
CHARS_DIR = ROOT / "web" / "static" / "characters"

# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
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
# Layer 1: Background
# ---------------------------------------------------------------------------


def _draw_background(canvas: Image.Image) -> None:
    """Dark gradient base + subtle vignette + grain."""
    draw = ImageDraw.Draw(canvas)

    # Vertical gradient: deep black at top → very dark grey at center → black at bottom
    for y in range(H):
        # Three-stop gradient
        t = y / H
        if t < 0.5:
            base = int(8 + (24 - 8) * (t / 0.5))
        else:
            base = int(24 + (8 - 24) * ((t - 0.5) / 0.5))
        draw.line([(0, y), (W, y)], fill=(base, base, base + 2, 255))

    # Soft vignette darken at edges
    vignette = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vignette)
    cx, cy = W // 2, H // 2
    max_r = math.sqrt(cx * cx + cy * cy)
    for r in range(0, int(max_r), 40):
        alpha = int(120 * (r / max_r) ** 2)
        vd.ellipse(
            (cx - r, cy - r, cx + r, cy + r),
            outline=alpha,
        )
    vignette = vignette.filter(ImageFilter.GaussianBlur(80))
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    overlay.putalpha(vignette)
    canvas.alpha_composite(overlay)


def _draw_card_border(canvas: Image.Image) -> None:
    """Subtle 1px lime/orange gradient border around the safe zone."""
    draw = ImageDraw.Draw(canvas)
    pad = 60
    inset = 12
    # Outer border
    draw.rectangle(
        (pad, pad, W - pad, H - pad),
        outline=(*LIME, 80),
        width=4,
    )
    # Inner accent line
    draw.rectangle(
        (pad + inset, pad + inset, W - pad - inset, H - pad - inset),
        outline=(*ORANGE, 40),
        width=2,
    )


# ---------------------------------------------------------------------------
# Layer 2: Character image
# ---------------------------------------------------------------------------


def _load_character_image(slug: str) -> Image.Image | None:
    """Load the character PNG from src/web/static/characters/<slug>.png."""
    path = CHARS_DIR / f"{slug}.png"
    if not path.exists():
        return None
    img = Image.open(path).convert("RGBA")
    return img


def _composite_character(canvas: Image.Image, char_img: Image.Image) -> None:
    """Place the character image into the CHAR_BOX, preserving aspect ratio."""
    box_w = CHAR_BOX[2] - CHAR_BOX[0]
    box_h = CHAR_BOX[3] - CHAR_BOX[1]

    # Fit the image inside the box, preserving aspect
    src_w, src_h = char_img.size
    src_ratio = src_w / src_h
    box_ratio = box_w / box_h

    if src_ratio > box_ratio:
        # Wider than the box → fit by width
        new_w = box_w
        new_h = int(box_w / src_ratio)
    else:
        # Taller than the box → fit by height
        new_h = box_h
        new_w = int(box_h * src_ratio)

    resized = char_img.resize((new_w, new_h), Image.LANCZOS)

    # Center the image inside the box
    px = CHAR_BOX[0] + (box_w - new_w) // 2
    py = CHAR_BOX[1] + (box_h - new_h) // 2

    canvas.alpha_composite(resized, (px, py))


# ---------------------------------------------------------------------------
# Layer 3: Typography overlay
# ---------------------------------------------------------------------------


def _draw_vertical_brand(canvas: Image.Image) -> None:
    """FREQUENZ in vertical text on the left edge."""
    draw = ImageDraw.Draw(canvas)
    text = "FREQUENZ"
    font = _font("space_bold", 48)

    # Draw each character vertically with letter-spacing
    x = 90
    y_start = MARGIN_TOP + 200
    char_spacing = 70
    for i, ch in enumerate(text):
        draw.text(
            (x, y_start + i * char_spacing),
            ch,
            font=font,
            fill=(*LIME, 220),
        )


def _draw_top_meta(canvas: Image.Image) -> None:
    """Small label above the character name."""
    draw = ImageDraw.Draw(canvas)
    label = "// YOUR CHARACTER"
    font = _font("space_bold", 36)
    draw.text(
        (MARGIN_X + 100, MARGIN_TOP),
        label,
        font=font,
        fill=(*MUTED, 200),
    )


def _draw_character_name(canvas: Image.Image, name: str, alt_name: str | None) -> None:
    """The big character name at the top of the canvas."""
    draw = ImageDraw.Draw(canvas)
    name_font = _font("bebas", 220)
    alt_font = _font("dm_italic", 56)

    # Name (uppercase, centered)
    name = name.upper()
    bbox = draw.textbbox((0, 0), name, font=name_font)
    name_w = bbox[2] - bbox[0]
    name_x = (W - name_w) // 2
    name_y = MARGIN_TOP + 80

    draw.text(
        (name_x, name_y),
        name,
        font=name_font,
        fill=(*WHITE, 255),
    )

    if alt_name:
        alt_text = f"a.k.a. {alt_name}"
        bbox = draw.textbbox((0, 0), alt_text, font=alt_font)
        alt_w = bbox[2] - bbox[0]
        alt_x = (W - alt_w) // 2
        alt_y = name_y + 250
        draw.text(
            (alt_x, alt_y),
            alt_text,
            font=alt_font,
            fill=(*MUTED, 220),
        )


def _draw_voice_line(canvas: Image.Image, voice_line: str) -> None:
    """Italic voice line below the character name."""
    if not voice_line:
        return
    draw = ImageDraw.Draw(canvas)
    font = _font("dm_italic", 56)
    bbox = draw.textbbox((0, 0), voice_line, font=font)
    text_w = bbox[2] - bbox[0]
    if text_w > W - 2 * MARGIN_X:
        # Wrap manually if too long
        words = voice_line.split()
        line1, line2 = "", ""
        for w in words:
            test = f"{line1} {w}".strip()
            tbbox = draw.textbbox((0, 0), test, font=font)
            if tbbox[2] - tbbox[0] < W - 2 * MARGIN_X - 200:
                line1 = test
            else:
                line2 = f"{line2} {w}".strip()
        for i, line in enumerate([line1, line2]):
            if not line:
                continue
            tbbox = draw.textbbox((0, 0), line, font=font)
            tw = tbbox[2] - tbbox[0]
            tx = (W - tw) // 2
            ty = 600 + i * 70
            draw.text((tx, ty), line, font=font, fill=(*WHITE, 200))
    else:
        tx = (W - text_w) // 2
        ty = 600
        draw.text((tx, ty), voice_line, font=font, fill=(*WHITE, 200))


def _draw_stat_block(
    canvas: Image.Image,
    label: str,
    value: str,
    x: int,
    y: int,
    color: tuple[int, int, int] = WHITE,
) -> None:
    """Generic stat block: label on top, big value below."""
    draw = ImageDraw.Draw(canvas)
    label_font = _font("space_bold", 32)
    value_font = _font("bebas", 130)

    draw.text((x, y), label.upper(), font=label_font, fill=(*MUTED, 220))
    draw.text((x, y + 60), value, font=value_font, fill=(*color, 255))


def _draw_bottom_stats(canvas: Image.Image, taste_dna: dict[str, Any]) -> None:
    """Bottom row: scene city + dancefloor ratio + underground depth."""
    if not taste_dna:
        return

    draw = ImageDraw.Draw(canvas)

    # Scene city
    scene_block = taste_dna.get("scene_city") or {}
    cities = scene_block.get("cities") or []
    if cities:
        top_city = cities[0]
        city_name = (top_city.get("city") or "").upper()
        city_pct = f"{top_city.get('percentage', 0)}%"
        _draw_stat_block(
            canvas,
            "SCENE",
            city_name,
            MARGIN_X + 60,
            2950,
            color=LIME,
        )
        _draw_stat_block(
            canvas,
            f"{city_pct} OF YOUR LIKES",
            "",
            MARGIN_X + 60,
            3140,
            color=MUTED,
        )

    # Underground depth
    depth_block = taste_dna.get("underground_depth") or {}
    if depth_block:
        score = depth_block.get("score", 0)
        label = depth_block.get("label", "")
        _draw_stat_block(
            canvas,
            "DEPTH",
            str(score),
            W - 700,
            2950,
            color=ORANGE,
        )
        # Tier label
        tier_font = _font("space_bold", 36)
        draw.text(
            (W - 700, 3140),
            label.upper(),
            font=tier_font,
            fill=(*MUTED, 220),
        )


def _draw_event_teaser(canvas: Image.Image, event: dict[str, Any] | None) -> None:
    """Bottom event card teaser: artist + venue + date + match score."""
    if not event:
        return

    draw = ImageDraw.Draw(canvas)

    # Background card panel
    panel_top = 3260
    panel_height = 320
    draw.rectangle(
        (MARGIN_X, panel_top, W - MARGIN_X, panel_top + panel_height),
        fill=(20, 20, 20, 200),
        outline=(*LIME, 80),
        width=3,
    )

    # Event title (artist or event name)
    title = (
        event.get("artist")
        or event.get("name")
        or event.get("event_name")
        or "Tonight in Madrid"
    )
    title_font = _font("space_bold", 56)
    draw.text(
        (MARGIN_X + 60, panel_top + 60),
        title.upper()[:40],
        font=title_font,
        fill=(*WHITE, 255),
    )

    # Venue + date
    venue = event.get("venue") or event.get("venue_name") or "—"
    date = event.get("date") or event.get("when") or ""
    sub_font = _font("space", 40)
    draw.text(
        (MARGIN_X + 60, panel_top + 140),
        f"{venue} · {date}".strip(" ·"),
        font=sub_font,
        fill=(*MUTED, 220),
    )

    # Match score on the right
    score = event.get("match_score") or event.get("confidence") or 0
    if isinstance(score, float):
        score = int(score * 100) if score <= 1 else int(score)
    score_str = f"{score}%"
    score_font = _font("bebas", 140)
    sbbox = draw.textbbox((0, 0), score_str, font=score_font)
    sw = sbbox[2] - sbbox[0]
    draw.text(
        (W - MARGIN_X - sw - 60, panel_top + 80),
        score_str,
        font=score_font,
        fill=(*LIME, 255),
    )


def _draw_footer(canvas: Image.Image) -> None:
    """frequenz.live tag at the very bottom."""
    draw = ImageDraw.Draw(canvas)
    font = _font("space_bold", 40)
    text = "frequenz.live"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(
        ((W - tw) // 2, H - MARGIN_BOT - 20),
        text,
        font=font,
        fill=(*MUTED, 220),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compose_card(
    character: dict[str, Any],
    taste_dna: dict[str, Any] | None = None,
    top_event: dict[str, Any] | None = None,
) -> Image.Image:
    """Compose a complete 1080x1920 share card.

    Args:
        character: Dict with at minimum {slug, name, voice_line, image_path?, alt_name?}
                   as returned by src/api/scan.py:_derive_character()
        taste_dna: Output of compute_taste_dna() — used for the bottom stats row
        top_event: A serialised match dict from the matching pipeline — used for
                   the bottom event teaser

    Returns:
        PIL Image at FINAL_W x FINAL_H (1080x1920), ready to .save() as PNG.
    """
    canvas = Image.new("RGBA", (W, H), BG + (255,))

    # Layer 1: background
    _draw_background(canvas)
    _draw_card_border(canvas)

    # Layer 2: character
    slug = character.get("slug")
    if slug:
        char_img = _load_character_image(slug)
        if char_img:
            _composite_character(canvas, char_img)

    # Layer 3: typography
    _draw_vertical_brand(canvas)
    _draw_top_meta(canvas)
    _draw_character_name(
        canvas,
        character.get("name", ""),
        character.get("alt_name"),
    )
    _draw_voice_line(canvas, character.get("voice_line", ""))

    if taste_dna:
        _draw_bottom_stats(canvas, taste_dna)
    if top_event:
        _draw_event_teaser(canvas, top_event)

    _draw_footer(canvas)

    # Downscale to final size with LANCZOS for anti-aliasing
    final = canvas.resize((FINAL_W, FINAL_H), Image.LANCZOS)
    return final


def compose_and_save(
    character: dict[str, Any],
    output_path: Path,
    taste_dna: dict[str, Any] | None = None,
    top_event: dict[str, Any] | None = None,
) -> Path:
    """Compose a card and save it to disk. Returns the output path."""
    img = compose_card(character, taste_dna=taste_dna, top_event=top_event)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    return output_path
