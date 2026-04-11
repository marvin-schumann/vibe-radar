"""Pre-generate the 30 Frequenz taste cards for the April 13 DM round.

For each DJ in data/madrid_dj_seed_list.yaml:
  1. Scrape their public SoundCloud profile (likes, reposts, follows)
  2. Compute their taste DNA
  3. Match to top events (Madrid)
  4. Derive their character (one of the 10 launch personas)
  5. Compose the final 1080x1920 PNG card
  6. Save to data/dj_dm_cards/<dj_slug>.png

Output: 30 ready-to-attach card images, named by SoundCloud handle.
Marvin attaches these directly to his DMs on April 13.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.cards.composer import compose_and_save
from src.collectors.soundcloud import SoundCloudCollector
from src.collectors.events.resident_advisor import ResidentAdvisorCollector
from src.analytics.taste_dna import compute_taste_dna
from src.matching.exact import ExactMatcher
from src.matching.vibe import VibeMatcher, build_taste_profile
from src.api.scan import _derive_character, _serialise_match

YAML_FILE = ROOT / "data" / "madrid_dj_seed_list.yaml"
OUTPUT_DIR = ROOT / "data" / "dj_dm_cards"


def parse_yaml_djs(path: Path) -> list[dict]:
    """Tiny YAML parser for our specific format — no PyYAML dependency."""
    djs: list[dict] = []
    current: dict | None = None
    text = path.read_text()
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("djs:"):
            continue
        if line.startswith("  - name:"):
            if current:
                djs.append(current)
            current = {}
            current["name"] = _strip_quotes(line.split(":", 1)[1].strip())
        elif line.startswith("    ") and current is not None and ":" in line:
            key, _, val = line.lstrip().partition(":")
            current[key.strip()] = _strip_quotes(val.strip())
    if current:
        djs.append(current)
    return djs


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


async def scan_dj(dj: dict) -> dict | None:
    """Run the scan pipeline for a single DJ. Returns dict with character + taste + top_event."""
    handle = dj["sc_handle"]
    name = dj["name"]
    print(f"\n→ {name} (@{handle})", flush=True)

    try:
        # 1. Scrape SoundCloud (collect_artists is async)
        collector = SoundCloudCollector(username=handle)
        artists = await collector.collect_artists()
        if not artists:
            print(f"  ✗ no artists scraped", flush=True)
            return None
        print(f"  ✓ scraped {len(artists)} artists", flush=True)

        # 2. Compute taste DNA
        artist_objects = [
            {
                "name": a.name,
                "genres": list(a.genres or []),
                "popularity": getattr(a, "popularity", 0) or 0,
                "source": "soundcloud",
                "play_count": getattr(a, "play_count", 0) or 0,
            }
            for a in artists
        ]
        taste = compute_taste_dna(artist_objects)
        print(f"  ✓ computed taste DNA", flush=True)

        # 3. Derive character
        character = _derive_character(taste)
        if not character:
            print(f"  ✗ no character derived", flush=True)
            return None
        print(f"  ✓ character: {character['name']}", flush=True)

        # 4. Match top event in Madrid
        ra = ResidentAdvisorCollector()
        events = await ra.collect_events(days_ahead=90)
        if not events:
            print(f"  ⚠ no Madrid events to match", flush=True)
            top_event = None
        else:
            exact = ExactMatcher(threshold=85)
            exact_matches = exact.match(artists, events)
            if exact_matches:
                top = max(exact_matches, key=lambda m: m.confidence)
                top_event = _serialise_match(top)
                print(f"  ✓ top match: {top_event.get('artist','?')} ({top_event.get('match_score','?')}%)", flush=True)
            else:
                # Fall back to vibe match
                vibe = VibeMatcher(threshold=0.45)
                vibe_matches = vibe.match(artists, events)
                if vibe_matches:
                    top = max(vibe_matches, key=lambda m: m.confidence)
                    top_event = _serialise_match(top)
                    print(f"  ✓ vibe match: {top_event.get('artist','?')} ({top_event.get('match_score','?')}%)", flush=True)
                else:
                    top_event = None
                    print(f"  ⚠ no event matches", flush=True)

        return {
            "character": character,
            "taste_dna": taste,
            "top_event": top_event,
        }
    except Exception as exc:
        import traceback
        print(f"  ✗ error: {exc}", flush=True)
        traceback.print_exc()
        return None


async def main(limit: int | None = None) -> None:
    djs = parse_yaml_djs(YAML_FILE)
    print(f"Loaded {len(djs)} DJs from {YAML_FILE.name}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if limit:
        djs = djs[:limit]
        print(f"Limited to first {limit}")

    succeeded = 0
    failed = 0
    for i, dj in enumerate(djs, 1):
        print(f"\n[{i}/{len(djs)}]", end="")
        result = await scan_dj(dj)
        if not result:
            failed += 1
            continue
        try:
            slug = re.sub(r"[^a-z0-9]+", "_", dj["sc_handle"].lower()).strip("_")
            out = OUTPUT_DIR / f"{slug}.png"
            compose_and_save(
                result["character"],
                out,
                taste_dna=result["taste_dna"],
                top_event=result["top_event"],
            )
            print(f"  ↓ saved {out.relative_to(ROOT)}", flush=True)
            succeeded += 1
        except Exception as exc:
            print(f"  ✗ compose failed: {exc}", flush=True)
            failed += 1

    print(f"\n=== Done. {succeeded} succeeded, {failed} failed. ===")
    print(f"Cards in: {OUTPUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(main(limit=limit))
