"""HTML-to-PNG card renderer using Playwright.

Renders Jinja2 HTML templates to 1080x1920 PNG screenshots via headless Chromium.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent / "templates"
CARD_W, CARD_H = 1080, 1920

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=False,
)

# ---------------------------------------------------------------------------
# Tribe icon mapping
# ---------------------------------------------------------------------------

TRIBE_ICONS: dict[str, str] = {
    "strobe nomad": "\U0001f52e",       # crystal ball
    "bass templar": "\u26a1",            # lightning
    "vinyl monk": "\U0001f4bf",          # disc
    "cloud drifter": "\u2601\ufe0f",     # cloud
    "circuit shaman": "\U0001f9ea",      # test tube
    "groove pilgrim": "\U0001f6b6",      # walking person
    "echo mystic": "\U0001f30a",         # wave
    "beat architect": "\U0001f3db\ufe0f", # classical building
    "rhythm oracle": "\U0001f52e",       # crystal ball
    "sonic nomad": "\U0001f30d",         # globe
}

# ---------------------------------------------------------------------------
# Rarity logic
# ---------------------------------------------------------------------------


def _compute_rarity(underground_depth: int, cross_genre_min_pct: float) -> tuple[str, str]:
    """Compute rarity label and CSS class from user stats.

    Rules:
    - LEGENDARY: underground_depth >= 75 AND rarest bridge <= 3%
    - RARE: underground_depth >= 60 OR rarest bridge <= 5%
    - UNCOMMON: underground_depth >= 40 OR rarest bridge <= 10%
    - COMMON: everything else
    """
    if underground_depth >= 75 and cross_genre_min_pct <= 3:
        return "LEGENDARY", "rarity-legendary"
    if underground_depth >= 60 or cross_genre_min_pct <= 5:
        return "RARE", "rarity-rare"
    if underground_depth >= 40 or cross_genre_min_pct <= 10:
        return "UNCOMMON", "rarity-uncommon"
    return "COMMON", "rarity-common"


# ---------------------------------------------------------------------------
# Color helpers for genre bars
# ---------------------------------------------------------------------------

_LIME = (196, 241, 53)
_ORANGE = (255, 92, 53)


def _lerp_hex(t: float) -> tuple[str, str]:
    """Return (start_color, end_color) as hex for a gradient stop at position t."""
    def _lerp(a: tuple[int, ...], b: tuple[int, ...], f: float) -> str:
        r = int(a[0] + (b[0] - a[0]) * f)
        g = int(a[1] + (b[1] - a[1]) * f)
        bl = int(a[2] + (b[2] - a[2]) * f)
        return f"#{r:02x}{g:02x}{bl:02x}"

    start = _lerp(_LIME, _ORANGE, max(0, t - 0.1))
    end = _lerp(_LIME, _ORANGE, min(1, t + 0.1))
    return start, end


# ---------------------------------------------------------------------------
# Template data builders
# ---------------------------------------------------------------------------


def _build_taste_dna_data(data: dict[str, Any]) -> dict[str, Any]:
    top_genres = data.get("top_genres", [])[:5]
    total_artists = data.get("total_artists", 0)
    underground = data.get("underground_depth", 50)
    bridges = data.get("cross_genre_bridges", {}).get("bridges", [])
    min_bridge = min((b.get("rarity_pct", 100) for b in bridges), default=100)

    rarity_label, rarity_class = _compute_rarity(underground, min_bridge)

    max_pct = max((g.get("percentage", 1) for g in top_genres), default=1)
    genres = []
    for i, g in enumerate(top_genres):
        pct = g.get("percentage", 0)
        t = i / max(len(top_genres) - 1, 1)
        cs, ce = _lerp_hex(t)
        genres.append({
            "name": g.get("genre", g.get("name", "")).upper(),
            "pct": pct,
            "bar_width": max(int(pct / max(max_pct, 1) * 100), 8),
            "color_start": cs,
            "color_end": ce,
        })

    return {
        "total_artists": total_artists,
        "genres": genres,
        "underground_depth": f"top {100 - underground}%" if underground > 0 else "—",
        "rarity_label": rarity_label,
        "rarity_class": rarity_class,
    }


def _build_scene_city_data(data: dict[str, Any]) -> dict[str, Any]:
    scene = data.get("scene_city", {})
    cities_raw = scene.get("cities", [])
    underground = data.get("underground_depth", 50)
    bridges = data.get("cross_genre_bridges", {}).get("bridges", [])
    min_bridge = min((b.get("rarity_pct", 100) for b in bridges), default=100)
    rarity_label, rarity_class = _compute_rarity(underground, min_bridge)

    top_city = cities_raw[0]["city"].upper() if cities_raw else "UNKNOWN"
    max_pct = max((c["percentage"] for c in cities_raw[:5]), default=1)

    cities = []
    for c in cities_raw[:5]:
        cities.append({
            "name": c["city"],
            "pct": c["percentage"],
            "bar_width": max(int(c["percentage"] / max(max_pct, 1) * 100), 4),
        })

    return {
        "top_city": top_city,
        "cities": cities,
        "rarity_label": rarity_label,
        "rarity_class": rarity_class,
    }


def _build_taste_tribe_data(data: dict[str, Any]) -> dict[str, Any]:
    tribe_data = data.get("taste_tribe", {})
    tribe = tribe_data.get("tribe", {}) or {}
    tribe_name = tribe.get("name", "Unknown Tribe")
    tagline = tribe.get("tagline", "")
    confidence = tribe.get("confidence", 50)
    secondary = tribe_data.get("secondary", {})
    secondary_name = secondary.get("name", "—") if isinstance(secondary, dict) else str(secondary) if secondary else "—"

    underground = data.get("underground_depth", 50)
    bridges = data.get("cross_genre_bridges", {}).get("bridges", [])
    min_bridge = min((b.get("rarity_pct", 100) for b in bridges), default=100)
    rarity_label, rarity_class = _compute_rarity(underground, min_bridge)

    tribe_icon = TRIBE_ICONS.get(tribe_name.lower(), "\U0001f3b5")  # default: musical note
    rarity_pct = max(3, min(30, 100 - confidence))

    return {
        "tribe_words": tribe_name.upper().split(),
        "tribe_icon": tribe_icon,
        "tagline": tagline,
        "confidence": confidence,
        "secondary_tribe": secondary_name.upper(),
        "rarity_pct": rarity_pct,
        "rarity_label": rarity_label,
        "rarity_class": rarity_class,
    }


def _build_cross_genre_data(data: dict[str, Any]) -> dict[str, Any]:
    bridges_data = data.get("cross_genre_bridges", {})
    bridges = bridges_data.get("bridges", [])
    underground = data.get("underground_depth", 50)
    min_bridge = min((b.get("rarity_pct", 100) for b in bridges), default=100)
    rarity_label, rarity_class = _compute_rarity(underground, min_bridge)

    hero = bridges[0] if bridges else {"genre_a": "house", "genre_b": "rock/metal", "rarity_pct": 3}

    other_bridges = []
    for b in bridges[1:3]:
        other_bridges.append({
            "genre_a": b["genre_a"].title(),
            "genre_b": b["genre_b"].title(),
            "rarity": b.get("rarity_pct", 5),
        })

    return {
        "genre_a": hero["genre_a"].upper(),
        "genre_b": hero["genre_b"].upper(),
        "genre_a_lower": hero["genre_a"].lower(),
        "genre_b_lower": hero["genre_b"].lower(),
        "hero_rarity": hero.get("rarity_pct", 5),
        "other_bridges": other_bridges,
        "rarity_label": rarity_label,
        "rarity_class": rarity_class,
    }


def _build_dancefloor_data(data: dict[str, Any]) -> dict[str, Any]:
    df = data.get("dancefloor_ratio", {})
    dance_pct = df.get("dancefloor_pct", 50)
    head_pct = df.get("headphones_pct", 50)
    label = df.get("label", "Balanced")
    underground = data.get("underground_depth", 50)
    bridges = data.get("cross_genre_bridges", {}).get("bridges", [])
    min_bridge = min((b.get("rarity_pct", 100) for b in bridges), default=100)
    rarity_label, rarity_class = _compute_rarity(underground, min_bridge)

    # Verdict styling
    if dance_pct >= 75:
        verdict = "BORN FOR THE FLOOR"
        verdict_color = "verdict-orange"
        subtitle = "The speakers are your soulmate"
    elif dance_pct >= 55:
        verdict = "DANCEFLOOR LEANING"
        verdict_color = "verdict-orange"
        subtitle = "Your body knows the rhythm"
    elif head_pct >= 75:
        verdict = "DEEP LISTENER"
        verdict_color = "verdict-lime"
        subtitle = "The headphones are sacred"
    elif head_pct >= 55:
        verdict = "HEADPHONE LEANING"
        verdict_color = "verdict-lime"
        subtitle = "Introspection over bass drops"
    else:
        verdict = "PERFECTLY SPLIT"
        verdict_color = "verdict-white"
        subtitle = "Two souls, one playlist"

    return {
        "dance_pct": dance_pct,
        "head_pct": head_pct,
        "verdict": verdict,
        "verdict_color_class": verdict_color,
        "verdict_subtitle": subtitle,
        "rarity_label": rarity_label,
        "rarity_class": rarity_class,
    }


# ---------------------------------------------------------------------------
# Rendering engine
# ---------------------------------------------------------------------------

# Map card names to (template file, data builder)
CARD_REGISTRY: dict[str, tuple[str, Any]] = {
    "taste-dna": ("taste_dna.html", _build_taste_dna_data),
    "scene-city": ("scene_city.html", _build_scene_city_data),
    "taste-tribe": ("taste_tribe.html", _build_taste_tribe_data),
    "cross-genre": ("cross_genre.html", _build_cross_genre_data),
    "dancefloor": ("dancefloor.html", _build_dancefloor_data),
}


def _render_html(template_name: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template to an HTML string."""
    tmpl = _jinja_env.get_template(template_name)
    return tmpl.render(**context)


def render_card(card_name: str, data: dict[str, Any]) -> bytes:
    """Render a single card to PNG bytes.

    Args:
        card_name: One of taste-dna, scene-city, taste-tribe, cross-genre, dancefloor
        data: The full taste DNA data dict

    Returns:
        PNG image bytes (1080x1920)
    """
    if card_name not in CARD_REGISTRY:
        raise ValueError(f"Unknown card: {card_name}")

    template_file, builder = CARD_REGISTRY[card_name]
    context = builder(data)
    html = _render_html(template_file, context)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": CARD_W, "height": CARD_H})

        # Write HTML to a temp file so file:// protocol works for fonts
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            f.write(html)
            tmp_path = f.name

        page.goto(f"file://{tmp_path}", wait_until="networkidle")
        png_bytes = page.screenshot(type="png")
        browser.close()

        Path(tmp_path).unlink(missing_ok=True)

    return png_bytes


def render_all_cards(data: dict[str, Any]) -> dict[str, bytes]:
    """Render all 5 cards to PNG bytes.

    Uses a single browser instance for efficiency.

    Returns:
        Dict mapping card name to PNG bytes.
    """
    from playwright.sync_api import sync_playwright

    results: dict[str, bytes] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": CARD_W, "height": CARD_H})

        for card_name, (template_file, builder) in CARD_REGISTRY.items():
            context = builder(data)
            html = _render_html(template_file, context)

            with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
                f.write(html)
                tmp_path = f.name

            page.goto(f"file://{tmp_path}", wait_until="networkidle")
            results[card_name] = page.screenshot(type="png")
            Path(tmp_path).unlink(missing_ok=True)

        browser.close()

    return results
