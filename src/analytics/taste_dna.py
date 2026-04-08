"""Taste DNA analytics — Scene City, Taste Tribe, Cross-Genre Bridges, Dancefloor ratio.

All computations use SoundCloud artist data only (genres, popularity, play counts).
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any


# ---------------------------------------------------------------------------
# City-scene taxonomy: genre keywords → city affinity
# ---------------------------------------------------------------------------

CITY_SCENES: dict[str, dict[str, Any]] = {
    "Berlin": {
        "keywords": [
            "techno", "minimal techno", "dark techno", "dub techno",
            "industrial techno", "hard techno", "minimal", "berlin",
        ],
        "flag": "DE",
    },
    "London": {
        "keywords": [
            "uk bass", "garage", "uk garage", "grime", "jungle",
            "dubstep", "drum and bass", "breakbeat", "bassline",
            "post-dubstep", "uk funky", "london",
        ],
        "flag": "GB",
    },
    "Detroit": {
        "keywords": [
            "detroit techno", "detroit", "electro", "detroit house",
        ],
        "flag": "US",
    },
    "Amsterdam": {
        "keywords": [
            "dutch house", "trance", "dutch", "gabber", "hardcore",
            "amsterdam", "psytrance",
        ],
        "flag": "NL",
    },
    "Chicago": {
        "keywords": [
            "chicago house", "chicago", "acid house", "acid",
            "ghetto house", "juke", "footwork",
        ],
        "flag": "US",
    },
    "Madrid": {
        "keywords": [
            "latin house", "reggaeton", "latin", "flamenco",
            "spanish", "madrid",
        ],
        "flag": "ES",
    },
    "Ibiza": {
        "keywords": [
            "balearic", "progressive house", "ibiza", "chill",
            "deep house", "melodic house",
        ],
        "flag": "ES",
    },
}


# ---------------------------------------------------------------------------
# Taste Tribe definitions
# ---------------------------------------------------------------------------

TRIBES: list[dict[str, Any]] = [
    {
        "name": "Warehouse Monk",
        "tagline": "Devotion to the 4/4 sacrament",
        "description": "Deep, repetitive, hypnotic. Your listening is a practice, not a pastime. The club is a temple; the music is meditation through intensity.",
        "icon": "//",
        "genre_keywords": [
            "techno", "dark techno", "industrial techno", "hard techno",
            "dub techno", "minimal techno",
        ],
        "popularity_sweet_spot": (0, 35),
        "entropy_range": (0, 2.0),
    },
    {
        "name": "Sonic Archaeologist",
        "tagline": "Digging is the destination",
        "description": "Your identity is built on depth of knowledge — obscure labels, forgotten releases, lineage tracing. The crate digger supreme.",
        "icon": "<<",
        "genre_keywords": [
            "deep house", "acid house", "detroit techno", "electro",
            "chicago house", "italo disco", "rave", "breakbeat",
        ],
        "popularity_sweet_spot": (0, 25),
        "entropy_range": (3.0, 99),
    },
    {
        "name": "Fog Machine Philosopher",
        "tagline": "Dancing is thinking with your body",
        "description": "Genre boundaries mean nothing to you. You value atmosphere, narrative arc, and critical discourse as much as the music itself.",
        "icon": "~~",
        "genre_keywords": [
            "ambient techno", "experimental", "electroacoustic",
            "deconstructed club", "leftfield", "post-dubstep", "glitch",
            "experimental electronic",
        ],
        "popularity_sweet_spot": (0, 30),
        "entropy_range": (3.5, 99),
    },
    {
        "name": "Strobe Nomad",
        "tagline": "The party is the pilgrimage",
        "description": "Your taste is informed by live experience. Social, gregarious, and deeply event-oriented — you discover music on dancefloors, not algorithms.",
        "icon": "->",
        "genre_keywords": [
            "house", "tech house", "melodic techno", "disco",
            "afro house", "breaks", "uk garage",
        ],
        "popularity_sweet_spot": (25, 60),
        "entropy_range": (2.0, 3.5),
    },
    {
        "name": "Dawn Chaser",
        "tagline": "The comedown is the peak",
        "description": "You prize texture over rhythm, atmosphere over drops. Your listening occupies the liminal spaces — sunrise sets, late-night headphone sessions.",
        "icon": "**",
        "genre_keywords": [
            "ambient", "dark ambient", "drone", "downtempo",
            "new age", "ambient house", "balearic", "lo-fi",
        ],
        "popularity_sweet_spot": (0, 35),
        "entropy_range": (1.0, 2.5),
    },
    {
        "name": "Bass Templar",
        "tagline": "Sub-frequencies are scripture",
        "description": "Defined by allegiance to low-end frequency and breakbeat-derived rhythms. You carry the lineage of UK rave and sound system culture.",
        "icon": "##",
        "genre_keywords": [
            "drum and bass", "jungle", "dubstep", "grime",
            "uk garage", "bassline", "footwork", "halftime",
            "uk bass",
        ],
        "popularity_sweet_spot": (10, 40),
        "entropy_range": (1.5, 3.0),
    },
    {
        "name": "Circuit Bender",
        "tagline": "The patch cable is the instrument",
        "description": "You don't just listen, you reverse-engineer. Your taste leans experimental but is anchored by craft appreciation and production knowledge.",
        "icon": "&&",
        "genre_keywords": [
            "idm", "electro", "modular", "generative", "glitch",
            "acid", "experimental electronic", "noise", "synthesis",
        ],
        "popularity_sweet_spot": (0, 25),
        "entropy_range": (2.5, 3.5),
    },
]


# ---------------------------------------------------------------------------
# Electronic subgenre taxonomy: raw SC tags → clean families
# Only electronic music families are considered for bridge detection.
# Non-electronic tags (rock, pop, world, etc.) are ignored.
# ---------------------------------------------------------------------------

ELECTRONIC_SUBGENRES: dict[str, list[str]] = {
    "techno": [
        "techno", "hard techno", "industrial techno", "minimal techno",
        "dub techno", "acid techno", "dark techno", "detroit techno",
        "melodic techno",
    ],
    "house": [
        "house", "deep house", "tech house", "progressive house",
        "afro house", "acid house", "chicago house", "melodic house",
        "latin house", "ghetto house",
    ],
    "trance": [
        "trance", "psytrance", "uplifting trance", "progressive trance",
        "hard trance", "goa",
    ],
    "bass": [
        "dubstep", "drum and bass", "jungle", "uk bass", "garage",
        "uk garage", "grime", "bassline", "dnb", "footwork", "juke",
        "halftime",
    ],
    "ambient": [
        "ambient", "downtempo", "chillout", "drone", "dark ambient",
        "ambient house", "new age",
    ],
    "disco": [
        "disco", "nu-disco", "italo disco", "funk", "boogie",
    ],
    "experimental": [
        "idm", "experimental", "glitch", "noise", "modular",
        "generative", "experimental electronic", "electroacoustic",
        "deconstructed club", "leftfield",
    ],
    "breaks": [
        "breakbeat", "breaks", "big beat", "electro",
    ],
}

# Keep GENRE_FAMILIES as alias for _genre_to_family (used elsewhere)
GENRE_FAMILIES = ELECTRONIC_SUBGENRES

# ---------------------------------------------------------------------------
# Rarity matrix for electronic subgenre bridges
# Value = estimated % of electronic music listeners who bridge these families.
# Only within-electronic pairs — no rock/pop/world noise.
# ---------------------------------------------------------------------------

RARE_BRIDGES: dict[tuple[str, str], float] = {
    # Rare (< 5% of electronic listeners)
    ("techno", "trance"): 4,
    ("techno", "disco"): 5,
    ("techno", "bass"): 6,
    ("trance", "bass"): 3,
    ("trance", "experimental"): 3,
    ("trance", "disco"): 2,
    ("trance", "breaks"): 4,
    ("bass", "disco"): 3,
    ("bass", "ambient"): 5,
    ("ambient", "disco"): 3,
    ("ambient", "breaks"): 5,
    ("experimental", "disco"): 4,
    ("experimental", "house"): 8,
    ("experimental", "trance"): 3,
    # Uncommon (5-10%)
    ("techno", "ambient"): 9,
    ("techno", "experimental"): 10,
    ("techno", "breaks"): 8,
    ("house", "bass"): 10,
    ("house", "ambient"): 7,
    ("house", "breaks"): 9,
    ("house", "trance"): 6,
    ("bass", "experimental"): 7,
    ("bass", "breaks"): 12,
    ("breaks", "experimental"): 8,
    ("breaks", "disco"): 6,
    ("ambient", "experimental"): 14,
    # Common (> 10%) — still shown but labelled differently
    ("techno", "house"): 25,
    ("house", "disco"): 22,
}

# Same pair reversed also counts
_reversed = {(b, a): v for (a, b), v in RARE_BRIDGES.items()}
RARE_BRIDGES.update(_reversed)


# ---------------------------------------------------------------------------
# Dancefloor vs Headphones genre classification
# ---------------------------------------------------------------------------

ALLOCENTRIC_GENRES = {
    "house", "deep house", "tech house", "techno", "disco", "nu-disco",
    "afro house", "latin house", "chicago house", "acid house", "melodic house",
    "hard techno", "industrial techno", "funk", "boogie", "garage",
    "uk garage", "uk funky", "drum and bass", "jungle", "dubstep",
    "grime", "bassline", "breaks", "breakbeat", "trance", "psytrance",
    "footwork", "juke", "electro", "gabber", "hardcore", "minimal techno",
    "dub techno", "reggaeton", "cumbia", "dancehall",
}

AUTOCENTRIC_GENRES = {
    "ambient", "dark ambient", "drone", "downtempo", "new age",
    "ambient house", "lo-fi", "experimental", "idm", "glitch",
    "noise", "modular", "generative", "experimental electronic",
    "electroacoustic", "neo-classical", "modern classical", "classical",
    "post-rock", "shoegaze", "field recordings", "meditation",
    "jazz", "nu jazz", "jazz fusion",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_taste_dna(artist_objects: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute all taste DNA features from artist objects.

    Args:
        artist_objects: list of artist dicts with keys: name, genres, popularity, source, play_count

    Returns:
        Dict with keys: scene_city, taste_tribe, cross_genre_bridges, dancefloor_ratio
    """
    sc_artists = [a for a in artist_objects if a.get("source") == "soundcloud"]
    if not sc_artists:
        sc_artists = artist_objects  # fallback to all if no SC-specific

    # Collect all genres (flattened, lowercased)
    all_genres: list[str] = []
    for a in sc_artists:
        for g in a.get("genres") or []:
            if g:
                all_genres.append(g.lower().strip())

    genre_counts = Counter(all_genres)

    # Popularity data
    pops = [a["popularity"] for a in sc_artists if a.get("popularity") is not None]
    avg_pop = sum(pops) / len(pops) if pops else 50

    return {
        "scene_city": _compute_scene_city(genre_counts),
        "taste_tribe": _compute_taste_tribe(genre_counts, avg_pop),
        "cross_genre_bridges": _compute_bridges(genre_counts),
        "dancefloor_ratio": _compute_dancefloor_ratio(genre_counts),
    }


# ---------------------------------------------------------------------------
# Feature 1: Scene — Home City
# ---------------------------------------------------------------------------


def _compute_scene_city(genre_counts: Counter) -> dict[str, Any]:
    """Map genre distribution to city-scene profiles."""
    if not genre_counts:
        return {"cities": [], "total_matches": 0}

    city_scores: dict[str, float] = {}

    for city, info in CITY_SCENES.items():
        score = 0.0
        for keyword in info["keywords"]:
            # Check for exact match or substring match in genre tags
            for genre, count in genre_counts.items():
                if keyword == genre or keyword in genre:
                    score += count
        city_scores[city] = score

    total = sum(city_scores.values())
    if total == 0:
        return {"cities": [], "total_matches": 0}

    cities = []
    for city, score in sorted(city_scores.items(), key=lambda x: -x[1]):
        if score > 0:
            pct = round(score / total * 100)
            if pct >= 1:  # Only show cities with >= 1%
                cities.append({
                    "city": city,
                    "percentage": pct,
                    "flag": CITY_SCENES[city]["flag"],
                })

    return {"cities": cities, "total_matches": int(total)}


# ---------------------------------------------------------------------------
# Feature 2: Taste Tribe / Listener Archetype
# ---------------------------------------------------------------------------


def _shannon_entropy(counts: Counter) -> float:
    """Compute Shannon entropy of a distribution."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    probs = [c / total for c in counts.values() if c > 0]
    return -sum(p * math.log2(p) for p in probs)


def _compute_taste_tribe(
    genre_counts: Counter,
    avg_popularity: float,
) -> dict[str, Any]:
    """Assign one of 7 taste tribes based on genre distribution, popularity, and entropy."""
    if not genre_counts:
        return {"tribe": None}

    entropy = _shannon_entropy(genre_counts)
    total_genre_tags = sum(genre_counts.values())

    tribe_scores: list[tuple[str, float, dict]] = []

    for tribe in TRIBES:
        score = 0.0

        # 1. Genre keyword match (strongest signal, weight 50%)
        genre_match = 0
        for kw in tribe["genre_keywords"]:
            for genre, count in genre_counts.items():
                if kw == genre or kw in genre:
                    genre_match += count
        genre_ratio = genre_match / total_genre_tags if total_genre_tags > 0 else 0
        score += genre_ratio * 50

        # 2. Popularity fit (weight 25%)
        pop_low, pop_high = tribe["popularity_sweet_spot"]
        if pop_low <= avg_popularity <= pop_high:
            # Perfect fit
            score += 25
        else:
            # Partial fit — distance penalty
            dist = min(abs(avg_popularity - pop_low), abs(avg_popularity - pop_high))
            score += max(0, 25 - dist)

        # 3. Entropy fit (weight 25%)
        ent_low, ent_high = tribe["entropy_range"]
        if ent_low <= entropy <= ent_high:
            score += 25
        else:
            dist = min(abs(entropy - ent_low), abs(entropy - ent_high))
            score += max(0, 25 - dist * 8)

        tribe_scores.append((tribe["name"], score, tribe))

    tribe_scores.sort(key=lambda x: -x[1])
    best_name, best_score, best_tribe = tribe_scores[0]
    max_possible = 100
    confidence = min(round(best_score / max_possible * 100), 99)

    # Secondary tribe
    secondary = None
    if len(tribe_scores) > 1 and tribe_scores[1][1] > 20:
        secondary = {
            "name": tribe_scores[1][2]["name"],
            "tagline": tribe_scores[1][2]["tagline"],
            "confidence": min(round(tribe_scores[1][1] / max_possible * 100), 99),
        }

    return {
        "tribe": {
            "name": best_tribe["name"],
            "tagline": best_tribe["tagline"],
            "description": best_tribe["description"],
            "icon": best_tribe["icon"],
            "confidence": confidence,
        },
        "secondary": secondary,
        "entropy": round(entropy, 2),
        "avg_popularity": round(avg_popularity, 1),
    }


# ---------------------------------------------------------------------------
# Feature 3: Cross-Genre Bridges
# ---------------------------------------------------------------------------


def _genre_to_family(genre: str) -> str | None:
    """Map a genre string to its electronic subgenre family.

    Returns None for non-electronic genres (rock, pop, world, etc.)
    so they are filtered out of bridge computation.
    """
    genre = genre.lower().strip()
    for family, keywords in ELECTRONIC_SUBGENRES.items():
        if genre in keywords or any(kw in genre for kw in keywords):
            return family
    return None


def _compute_bridges(genre_counts: Counter) -> dict[str, Any]:
    """Find the user's rarest electronic subgenre bridges.

    Only considers genres that map to ELECTRONIC_SUBGENRES families.
    Non-electronic tags are ignored to avoid meaningless bridges
    like "house + rock".
    """
    if not genre_counts:
        return {"bridges": []}

    # Map genres to electronic families only
    family_counts: Counter = Counter()
    for genre, count in genre_counts.items():
        family = _genre_to_family(genre)
        if family:
            family_counts[family] += count

    # Need at least 2 electronic families for bridges
    active_families = [f for f, c in family_counts.items() if c >= 1]
    if len(active_families) < 2:
        return {"bridges": []}

    # Find rare bridges among user's active electronic families
    bridges: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for f1 in active_families:
        for f2 in active_families:
            if f1 >= f2:
                continue
            pair = (f1, f2)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            rarity_pct = RARE_BRIDGES.get(pair)
            if rarity_pct is None:
                continue

            # Rarity label based on electronic listener context
            if rarity_pct <= 5:
                rarity_label = "rare"
            elif rarity_pct <= 10:
                rarity_label = "uncommon"
            else:
                rarity_label = "notable"

            bridges.append({
                "genre_a": f1,
                "genre_b": f2,
                "rarity_pct": rarity_pct,
                "rarity_label": rarity_label,
                "description": (
                    f"You bridge {f1} + {f2} — only ~{rarity_pct}% "
                    f"of electronic listeners do ({rarity_label})"
                ),
            })

    bridges.sort(key=lambda x: x["rarity_pct"])
    return {"bridges": bridges[:3]}


# ---------------------------------------------------------------------------
# Feature 4: Dancefloor vs Headphones Ratio
# ---------------------------------------------------------------------------


def _compute_dancefloor_ratio(genre_counts: Counter) -> dict[str, Any]:
    """Classify genres as dancefloor (allocentric) vs headphones (autocentric)."""
    if not genre_counts:
        return {"dancefloor_pct": 50, "headphones_pct": 50, "total_classified": 0}

    dancefloor = 0
    headphones = 0

    for genre, count in genre_counts.items():
        g = genre.lower().strip()
        if g in ALLOCENTRIC_GENRES or any(ag in g for ag in ALLOCENTRIC_GENRES):
            dancefloor += count
        elif g in AUTOCENTRIC_GENRES or any(ag in g for ag in AUTOCENTRIC_GENRES):
            headphones += count
        else:
            # Partial: split 70/30 toward dancefloor as default for electronic
            dancefloor += count * 0.7
            headphones += count * 0.3

    total = dancefloor + headphones
    if total == 0:
        return {"dancefloor_pct": 50, "headphones_pct": 50, "total_classified": 0}

    df_pct = round(dancefloor / total * 100)
    hp_pct = 100 - df_pct

    if df_pct >= 80:
        label = "Pure dancefloor energy"
    elif df_pct >= 60:
        label = "Mostly moving, sometimes reflecting"
    elif df_pct >= 40:
        label = "Balanced: body and mind"
    elif df_pct >= 20:
        label = "Mostly introspective, sometimes moving"
    else:
        label = "Deep in headphone territory"

    return {
        "dancefloor_pct": df_pct,
        "headphones_pct": hp_pct,
        "label": label,
        "total_classified": int(total),
    }
