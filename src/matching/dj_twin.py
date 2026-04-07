"""DJ Twin matching — find which DJs share your taste.

Compares the user's genre distribution against cached DJ taste vectors
using cosine similarity.  Returns a ranked list of DJ matches with
similarity percentages.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from loguru import logger

_DATA_DIR = Path(__file__).parent.parent / "data"
_VECTORS_PATH = _DATA_DIR / "dj_taste_vectors.json"
_PROFILES_PATH = _DATA_DIR / "dj_profiles.json"


# ---------------------------------------------------------------------------
# Vector math
# ---------------------------------------------------------------------------

def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two sparse genre vectors.

    Keys are genre names, values are counts/percentages (any positive number).
    Returns a value between 0.0 and 1.0.
    """
    # Get the union of all genres
    all_genres = set(a) | set(b)
    if not all_genres:
        return 0.0

    dot = sum(a.get(g, 0.0) * b.get(g, 0.0) for g in all_genres)
    mag_a = math.sqrt(sum(v ** 2 for v in a.values()))
    mag_b = math.sqrt(sum(v ** 2 for v in b.values()))

    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0

    return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def load_dj_vectors() -> dict[str, dict[str, Any]]:
    """Load cached DJ taste vectors from disk.

    Returns dict of DJ name → vector data (genre_distribution, metadata).
    """
    if not _VECTORS_PATH.exists():
        logger.warning("No DJ taste vectors found at {}", _VECTORS_PATH)
        return {}
    with open(_VECTORS_PATH) as f:
        return json.load(f)


def load_dj_profiles() -> list[dict[str, Any]]:
    """Load the curated DJ profiles list."""
    if not _PROFILES_PATH.exists():
        return []
    with open(_PROFILES_PATH) as f:
        return json.load(f)


def compute_dj_similarity(
    user_genres: dict[str, float],
    dj_vectors: dict[str, dict[str, Any]],
    *,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Compare user's genre distribution against all DJ vectors.

    Args:
        user_genres: User's genre distribution as {genre: count_or_pct}.
        dj_vectors: DJ taste vectors as loaded by ``load_dj_vectors()``.
        top_n: Number of top matches to return.

    Returns:
        List of dicts sorted by similarity descending::

            [
                {
                    "name": "Peggy Gou",
                    "similarity_pct": 87,
                    "classification": "twin",
                    "city": "Berlin",
                    "soundcloud_url": "https://soundcloud.com/peggygou",
                    "shared_genres": ["house", "techno"],
                    "dj_top_genres": [("house", 42.1), ("techno", 28.3)],
                },
                ...
            ]
    """
    if not user_genres or not dj_vectors:
        return []

    # Normalize user genres to lowercase
    user_vec = {g.lower().strip(): float(v) for g, v in user_genres.items()}

    results: list[tuple[str, float, dict]] = []

    for dj_name, dj_data in dj_vectors.items():
        dj_genre_dist = dj_data.get("genre_distribution", {})
        if not dj_genre_dist:
            continue

        # Cosine similarity on genre vectors
        sim = _cosine_similarity(user_vec, dj_genre_dist)
        results.append((dj_name, sim, dj_data))

    # Sort by similarity descending
    results.sort(key=lambda x: -x[1])

    # Build output with classification
    output: list[dict[str, Any]] = []
    user_genre_set = set(user_vec.keys())

    for rank, (dj_name, sim, dj_data) in enumerate(results[:top_n]):
        dj_genre_dist = dj_data.get("genre_distribution", {})
        dj_genre_set = set(dj_genre_dist.keys())

        # Classification: top match = "twin", 2nd-3rd = "adjacent", rest = "similar"
        if rank == 0:
            classification = "twin"
        elif rank <= 2:
            classification = "adjacent"
        else:
            classification = "similar"

        output.append({
            "name": dj_name,
            "similarity_pct": round(sim * 100),
            "similarity_raw": round(sim, 4),
            "classification": classification,
            "city": dj_data.get("city", ""),
            "soundcloud_url": dj_data.get("soundcloud_url", ""),
            "shared_genres": sorted(user_genre_set & dj_genre_set),
            "dj_top_genres": [
                (g, pct) for g, pct in
                sorted(dj_genre_dist.items(), key=lambda x: -x[1])[:5]
            ],
        })

    logger.info(
        "DJ Twin match: top={} ({}%), computed against {} DJs",
        output[0]["name"] if output else "none",
        output[0]["similarity_pct"] if output else 0,
        len(dj_vectors),
    )

    return output


def get_user_genre_distribution(
    artists: list[dict[str, Any]] | None = None,
    taste_profile: Any | None = None,
) -> dict[str, float]:
    """Extract user's genre distribution from artists or taste profile.

    Accepts either a list of artist dicts (with 'genres' key) or a
    TasteProfile object (with 'top_genres' attribute).
    """
    genre_counts: dict[str, float] = {}

    if taste_profile is not None:
        # TasteProfile.top_genres is list[tuple[str, int]]
        top_genres = getattr(taste_profile, "top_genres", [])
        for genre, count in top_genres:
            genre_counts[genre.lower().strip()] = float(count)
    elif artists is not None:
        for artist in artists:
            genres = artist.get("genres", [])
            if isinstance(genres, list):
                for g in genres:
                    key = g.lower().strip()
                    genre_counts[key] = genre_counts.get(key, 0) + 1.0
    else:
        return {}

    return genre_counts
