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
# City skyline SVG path data (viewBox: 0 0 1080 420, ground at y=385)
# ---------------------------------------------------------------------------

SKYLINE_PATHS: dict[str, str] = {
    "amsterdam": (
        "M0,385 L0,295 L8,295 L8,275 L15,275 L15,258 L25,245 L33,232 L41,245 "
        "L48,258 L48,275 L55,275 L55,295 L65,295 L65,320 L72,320 L72,285 "
        "L78,270 Q103,218 128,270 L135,285 L135,320 L142,320 L142,265 L150,265 "
        "L173,205 L196,265 L205,265 L205,320 L212,320 L212,260 L228,260 "
        "L228,248 L235,232 L243,218 L251,232 L258,248 L258,260 L275,260 "
        "L275,320 L282,320 L282,300 L290,300 L290,280 L297,280 L297,262 "
        "L307,248 L315,235 L323,248 L330,262 L330,280 L337,280 L337,300 "
        "L345,300 L345,320 L352,320 L352,290 L358,272 Q383,220 408,272 "
        "L415,290 L415,320 L422,320 L422,270 L430,270 L453,212 L476,270 "
        "L485,270 L485,320 L492,320 L492,262 L508,262 L508,250 L515,235 "
        "L523,220 L531,235 L538,250 L538,262 L555,262 L555,320 L562,320 "
        "L562,295 L570,295 L570,275 L577,275 L577,258 L587,245 L595,232 "
        "L603,245 L610,258 L610,275 L617,275 L617,295 L625,295 L625,320 "
        "L632,320 L632,285 L638,268 Q663,215 688,268 L695,285 L695,320 "
        "L702,320 L702,268 L710,268 L733,210 L756,268 L765,268 L765,320 "
        "L772,320 L772,265 L788,265 L788,252 L795,238 L803,225 L811,238 "
        "L818,252 L818,265 L835,265 L835,320 L842,320 L842,300 L850,300 "
        "L850,282 L857,282 L857,265 L867,252 L875,240 L883,252 L890,265 "
        "L890,282 L897,282 L897,300 L905,300 L905,320 L912,320 L912,295 "
        "L918,278 Q943,228 968,278 L975,295 L975,320 L982,320 L982,285 "
        "L990,285 L1013,240 L1036,285 L1045,285 L1045,320 L1080,335 L1080,385 Z"
    ),
    "berlin": (
        "M0,385 L0,325 L55,325 L55,302 L95,302 L95,280 L125,280 L125,298 "
        "L165,298 L165,315 L200,315 L200,285 L235,285 L235,265 L270,265 "
        "L270,248 L290,248 L290,265 L310,265 L310,215 L335,215 L340,208 "
        "L348,198 L355,190 L362,182 L369,190 L376,198 L383,208 L388,215 "
        "L410,215 L410,248 L430,248 L430,265 L460,265 L460,280 L490,280 "
        "L490,260 L510,260 L510,240 L530,240 L530,260 L555,260 L555,242 "
        "L570,242 L585,252 L590,240 L600,240 L608,230 L614,210 L618,185 "
        "L620,160 L621,135 L622,105 L622,82 "
        "C619,76 617,68 616,58 C615,48 616,40 619,34 "
        "L621,28 L623,20 L624,12 L625,6 L627,6 L628,12 L629,20 L631,28 "
        "C634,34 635,40 636,48 C637,58 636,68 633,76 "
        "L630,82 L630,105 L629,135 L628,160 L626,185 L622,210 L616,230 "
        "L610,240 L620,240 L628,252 L645,242 L660,242 L660,260 L680,260 "
        "L680,280 L710,280 L710,265 L740,265 L740,280 L770,280 L770,298 "
        "L800,298 L800,280 L830,280 L830,302 L870,302 L870,282 L900,282 "
        "L900,302 L940,302 L940,318 L980,318 L980,302 L1010,302 L1010,322 "
        "L1050,322 L1050,312 L1080,312 L1080,385 Z"
    ),
    "detroit": (
        "M0,385 L0,318 L50,318 L50,300 L85,300 L85,312 L125,312 L125,280 "
        "L140,280 L140,262 L148,262 L148,240 L152,200 L158,200 L158,240 "
        "L166,240 L166,262 L185,262 L185,245 L192,245 L192,225 L196,190 "
        "L202,190 L202,225 L208,225 L208,245 L230,245 L230,262 L238,262 "
        "L238,242 L242,210 L248,210 L248,242 L256,242 L256,262 L280,262 "
        "L280,282 L310,282 L310,300 L345,300 L345,272 L375,272 L375,252 "
        "L405,252 L405,232 L420,232 L420,260 L450,260 L450,280 L480,280 "
        "L480,262 L498,262 L498,242 L515,242 L515,205 L530,205 L530,182 "
        "L540,182 L540,168 L550,168 L550,152 L562,152 L562,140 L575,140 "
        "L575,128 L590,128 L590,118 L610,118 L610,128 L625,128 L625,140 "
        "L638,140 L638,152 L650,152 L650,168 L660,168 L660,182 L670,182 "
        "L670,205 L685,205 L685,242 L702,242 L702,262 L720,262 L720,280 "
        "L750,280 L750,262 L778,262 L778,280 L810,280 L810,302 L845,302 "
        "L845,282 L875,282 L875,298 L910,298 L910,312 L950,312 L950,302 "
        "L985,302 L985,318 L1020,318 L1020,328 L1055,328 L1080,332 L1080,385 Z"
    ),
    "london": (
        "M0,385 L0,322 L48,322 L48,302 L82,302 L82,282 L105,282 L105,302 "
        "L135,302 L135,272 L155,272 L155,292 L185,292 L185,262 L205,262 "
        "L205,242 L225,242 L225,262 L255,262 L255,282 L275,282 L275,262 "
        "L280,242 L284,218 L288,192 L291,165 L294,138 L296,115 L298,92 "
        "L300,78 L302,78 L304,92 L306,115 L308,138 L311,165 L314,192 "
        "L318,218 L322,242 L327,262 L340,262 L340,282 L365,282 L365,262 "
        "L385,262 L385,242 L405,242 L405,225 L425,225 L425,252 L455,252 "
        "L455,272 L475,272 L475,252 L495,252 L495,235 L515,235 L515,262 "
        "L545,262 L545,282 L570,282 L570,302 L605,302 L605,275 L625,275 "
        "L625,292 L645,292 L645,265 L660,265 L660,235 L668,235 L668,210 "
        "L672,210 L672,190 L676,190 L676,172 L679,172 L680,162 L681,155 "
        "L682,155 L683,162 L684,172 L687,172 L687,190 L691,190 L691,210 "
        "L695,210 L695,235 L703,235 L703,265 L718,265 L718,292 L740,292 "
        "L740,312 L775,312 L775,282 L798,282 L798,302 L828,302 L828,282 "
        "L858,282 L858,302 L888,302 L888,288 L908,288 L908,302 L938,302 "
        "L938,318 L968,318 L968,308 L998,308 L998,322 L1035,322 L1035,312 "
        "L1065,312 L1080,322 L1080,385 Z"
    ),
    "chicago": (
        "M0,385 L0,312 L38,312 L38,285 L62,285 L62,265 L85,265 L85,245 "
        "L105,245 L105,265 L128,265 L128,235 L148,235 L148,215 L168,215 "
        "L168,245 L188,245 L188,225 L208,225 L208,252 L228,252 L228,225 "
        "L248,225 L248,205 L268,205 L268,185 L288,185 L288,205 L308,205 "
        "L308,225 L328,225 L328,195 L348,195 L348,175 L368,175 L368,205 "
        "L385,205 L385,175 L398,175 L398,148 L410,148 L410,118 L422,118 "
        "L422,92 L445,92 L445,118 L458,118 L458,148 L470,148 L470,175 "
        "L485,175 L485,205 L502,205 L502,178 L518,178 L518,158 L535,158 "
        "L535,140 L552,140 L552,158 L568,158 L568,182 L588,182 L588,205 "
        "L608,205 L608,232 L628,232 L628,215 L648,215 L648,242 L668,242 "
        "L668,225 L688,225 L688,205 L708,205 L708,232 L728,232 L728,262 "
        "L748,262 L748,242 L768,242 L768,265 L788,265 L788,282 L808,282 "
        "L808,265 L828,265 L828,285 L858,285 L858,302 L888,302 L888,285 "
        "L918,285 L918,302 L948,302 L948,318 L978,318 L978,305 L1008,305 "
        "L1008,322 L1042,322 L1062,328 L1080,332 L1080,385 Z"
    ),
    "madrid": (
        "M0,385 L0,322 L48,322 L48,305 L82,305 L82,285 L108,285 L108,305 "
        "L140,305 L140,275 L158,275 L158,265 L162,248 L166,235 L169,222 "
        "L172,235 L176,248 L180,265 L195,265 L195,285 L210,285 L210,305 "
        "L235,305 L235,278 L252,278 L252,265 L260,265 L260,250 L265,250 "
        "L268,238 L270,228 L272,238 L275,250 L280,250 L280,245 L298,245 "
        "L298,240 L318,240 L318,237 L322,225 L326,218 L328,212 L330,218 "
        "L334,225 L338,237 L338,240 L358,240 L358,237 L362,225 L366,215 "
        "L370,225 L374,237 L374,240 L392,240 L392,245 L410,245 L410,250 "
        "L415,250 L418,238 L420,228 L422,238 L425,250 L430,250 L430,265 "
        "L438,265 L438,278 L455,278 L455,295 L480,295 L480,305 L515,305 "
        "L515,280 L535,280 L535,265 L550,265 L552,248 L555,235 L557,225 "
        "L559,235 L562,248 L570,265 L585,265 L585,285 L610,285 L610,305 "
        "L640,305 L640,285 L660,285 L660,270 L668,255 L672,242 L675,232 "
        "L678,242 L682,255 L690,270 L690,285 L712,285 L712,305 L745,305 "
        "L745,290 L768,290 L768,305 L798,305 L798,315 L828,315 L828,300 "
        "L855,300 L855,315 L882,315 L882,305 L908,305 L908,318 L942,318 "
        "L942,310 L972,310 L972,325 L1008,325 L1008,315 L1045,315 L1045,328 "
        "L1080,328 L1080,385 Z"
    ),
    "ibiza": (
        "M0,385 L0,352 L35,352 L35,345 L65,345 L65,340 L95,340 L95,332 "
        "L115,332 L115,328 L135,328 L135,322 L155,322 L160,316 L165,310 "
        "L170,316 L178,318 L195,318 L200,310 L205,304 L210,310 L218,312 "
        "L235,312 L240,304 L245,296 L250,304 L258,306 L268,306 L273,298 "
        "L278,290 L283,296 L292,298 L305,298 L310,290 L315,282 L320,290 "
        "L330,292 L345,292 L350,282 L355,274 L360,282 L370,284 L385,284 "
        "L390,274 L395,266 L400,274 L410,276 L425,276 L430,266 L435,258 "
        "L440,266 L450,268 L465,268 L470,258 L475,250 L480,258 L490,260 "
        "L505,260 L510,250 L515,242 L520,250 L530,252 L545,252 L550,242 "
        "L555,232 L560,240 L570,242 L585,242 L590,232 L595,222 L600,230 "
        "L610,232 L620,232 L625,220 L630,210 L635,202 L640,192 L645,184 "
        "L650,175 L654,168 L658,162 L662,156 L666,150 L670,145 L674,140 "
        "L678,136 L682,132 L686,130 L690,128 L695,130 L700,134 L705,140 "
        "L710,148 L715,158 L720,168 L728,180 L736,192 L745,205 L755,218 "
        "L768,232 L782,248 L800,262 L820,276 L842,288 L868,300 L898,312 "
        "L932,322 L968,330 L1008,336 L1050,340 L1080,345 L1080,385 Z"
    ),
    "default": (
        "M0,385 L0,310 L40,310 L40,280 L60,280 L60,230 L80,230 L80,200 "
        "L100,200 L100,170 L120,170 L120,200 L140,200 L140,250 L180,250 "
        "L180,220 L200,220 L200,170 L220,170 L220,120 L240,120 L240,80 "
        "L260,80 L260,120 L280,120 L280,200 L320,200 L320,160 L340,160 "
        "L340,60 L360,60 L360,40 L380,40 L380,60 L400,60 L400,160 L420,160 "
        "L420,250 L460,250 L460,210 L480,210 L480,140 L500,140 L500,100 "
        "L520,100 L520,140 L540,140 L540,210 L560,210 L560,270 L600,270 "
        "L600,230 L620,230 L620,170 L640,170 L640,120 L660,120 L660,170 "
        "L680,170 L680,230 L720,230 L720,190 L740,190 L740,160 L760,160 "
        "L760,100 L780,100 L780,160 L800,160 L800,210 L840,210 L840,250 "
        "L860,250 L860,190 L880,190 L880,230 L920,230 L920,290 L960,290 "
        "L960,230 L980,230 L980,190 L1000,190 L1000,230 L1020,230 L1020,300 "
        "L1060,300 L1060,310 L1080,310 L1080,385 Z"
    ),
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
    city_name_raw = cities_raw[0]["city"] if cities_raw else "Unknown"
    city_key = city_name_raw.lower()
    skyline_d = SKYLINE_PATHS.get(city_key, SKYLINE_PATHS["default"])
    hashtag = f"#Frequenz{city_name_raw.title().replace(' ', '')}"

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
        "city_key": city_key,
        "skyline_d": skyline_d,
        "hashtag": hashtag,
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
