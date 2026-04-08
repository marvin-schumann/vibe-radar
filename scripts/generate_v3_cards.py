#!/usr/bin/env python3
"""Generate v3 sample cards with Marvin's real data."""

from pathlib import Path
from src.cards.renderer import render_all_cards

DATA = {
    "top_genres": [
        {"genre": "Trance", "percentage": 34},
        {"genre": "House", "percentage": 22},
        {"genre": "Techno", "percentage": 18},
        {"genre": "Bass", "percentage": 14},
        {"genre": "Ambient", "percentage": 12},
    ],
    "total_artists": 847,
    "underground_depth": 72,
    "scene_city": {
        "cities": [
            {"city": "Amsterdam", "percentage": 58},
            {"city": "Berlin", "percentage": 20},
            {"city": "Detroit", "percentage": 10},
            {"city": "London", "percentage": 9},
            {"city": "Ibiza", "percentage": 3},
        ]
    },
    "taste_tribe": {
        "tribe": {
            "name": "Strobe Nomad",
            "tagline": "Chasing lasers across continents",
            "description": "You follow the sound wherever it takes you",
            "confidence": 78,
        },
        "secondary": {"name": "Bass Templar"},
    },
    "cross_genre_bridges": {
        "bridges": [
            {"genre_a": "house", "genre_b": "rock/metal", "rarity_pct": 3},
            {"genre_a": "techno", "genre_b": "world", "rarity_pct": 5},
        ]
    },
    "dancefloor_ratio": {
        "dancefloor_pct": 89,
        "headphones_pct": 11,
        "label": "Born for the Floor",
    },
}


def main():
    print("Generating v3 sample cards...")
    cards = render_all_cards(DATA)
    for name, png_bytes in cards.items():
        path = Path(f"/tmp/frequenz-card-v3-{name}.png")
        path.write_bytes(png_bytes)
        print(f"  {path} ({len(png_bytes):,} bytes)")
    print("Done!")


if __name__ == "__main__":
    main()
