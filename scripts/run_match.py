"""CLI runner for Vibe Radar -- find your artists playing in Madrid.

Usage:
    python -m scripts.run_match
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime

from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.collectors.events.bandsintown import BandsintownCollector
from src.collectors.events.resident_advisor import ResidentAdvisorCollector
from src.collectors.events.songkick import SongkickCollector
from src.collectors.soundcloud import SoundCloudCollector
from src.collectors.spotify import SpotifyCollector
from src.config import settings
from src.matching.exact import ExactMatcher
from src.matching.vibe import VibeMatcher, build_taste_profile
from src.models import Artist, Event, Match, MatchType, TasteProfile

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

console = Console()

# Configure loguru: disable default sink, add a stderr sink at INFO level
# so that log messages don't collide with the rich output.
logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FEATURE_BAR_LABELS = [
    ("Danceability", "danceability"),
    ("Energy", "energy"),
    ("Valence", "valence"),
    ("Acousticness", "acousticness"),
    ("Instrumentalness", "instrumentalness"),
    ("Liveness", "liveness"),
    ("Speechiness", "speechiness"),
]


def _bar(value: float, width: int = 20) -> str:
    """Render a 0-1 value as a text bar chart row."""
    filled = round(value * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _print_taste_profile(profile: TasteProfile) -> None:
    """Print a summary of the user's taste profile using rich panels."""

    # -- Top genres ----------------------------------------------------------
    if profile.top_genres:
        genre_lines: list[str] = []
        for genre, count in profile.top_genres[:15]:
            genre_lines.append(f"  {genre:<30} ({count} artists)")
        genre_text = "\n".join(genre_lines)
    else:
        genre_text = "  No genre data available."

    console.print(
        Panel(
            genre_text,
            title="[bold cyan]Top Genres[/bold cyan]",
            border_style="cyan",
            expand=False,
        )
    )

    # -- Audio features radar (text) -----------------------------------------
    if profile.avg_features is not None:
        feat = profile.avg_features
        lines: list[str] = []
        for label, field in _FEATURE_BAR_LABELS:
            val = getattr(feat, field)
            lines.append(f"  {label:<18} {_bar(val)} {val:.2f}")
        lines.append(f"  {'Tempo':<18} {feat.tempo:.0f} BPM")
        radar_text = "\n".join(lines)
    else:
        radar_text = "  No audio features available (Spotify data needed)."

    console.print(
        Panel(
            radar_text,
            title="[bold magenta]Audio Profile[/bold magenta]",
            border_style="magenta",
            expand=False,
        )
    )

    # -- Source breakdown -----------------------------------------------------
    source_parts = [f"{src}: {cnt}" for src, cnt in profile.sources.items()]
    console.print(
        f"  [dim]Sources:[/dim] {' | '.join(source_parts)}  "
        f"[dim]Total artists:[/dim] {profile.total_artists}\n"
    )


def _merge_artists(artist_lists: list[list[Artist]]) -> list[Artist]:
    """Merge multiple artist lists, deduplicating by normalized name.

    When two artists collide, genres are merged and the higher popularity wins.
    """
    by_name: dict[str, Artist] = {}

    for artists in artist_lists:
        for artist in artists:
            key = artist.normalized_name
            if key in by_name:
                existing = by_name[key]
                # Merge genres
                merged = list(existing.genres)
                for g in artist.genres:
                    if g not in merged:
                        merged.append(g)
                existing.genres = merged
                # Keep higher popularity
                if artist.popularity is not None:
                    if existing.popularity is None or artist.popularity > existing.popularity:
                        existing.popularity = artist.popularity
                # Fill missing image/url
                if existing.image_url is None and artist.image_url is not None:
                    existing.image_url = artist.image_url
                if existing.source_url is None and artist.source_url is not None:
                    existing.source_url = artist.source_url
            else:
                by_name[key] = artist.model_copy()

    return list(by_name.values())


def _merge_events(event_lists: list[list[Event]]) -> list[Event]:
    """Merge multiple event lists, deduplicating by (date, venue, similar name)."""
    seen: dict[str, Event] = {}

    for events in event_lists:
        for event in events:
            venue_name = event.venue.name.lower().strip() if event.venue else "unknown"
            date_key = event.date.strftime("%Y-%m-%d")
            # Build a dedup key from date + venue + first 40 chars of name
            name_key = event.name.lower().strip()[:40]
            key = f"{date_key}|{venue_name}|{name_key}"

            if key not in seen:
                seen[key] = event
            else:
                # Merge artist lists from duplicate events
                existing = seen[key]
                for artist_name in event.artists:
                    if artist_name not in existing.artists:
                        existing.artists.append(artist_name)
                        existing.normalized_artists.append(artist_name.lower().strip())

    return list(seen.values())


def _print_results(matches: list[Match]) -> None:
    """Print match results as a rich table."""

    if not matches:
        console.print(
            Panel(
                "[yellow]No matches found.[/yellow]\n\n"
                "This could mean:\n"
                "  - None of your artists are playing in Madrid soon\n"
                "  - Event data sources returned limited results\n"
                "  - Try lowering the match threshold in .env",
                title="[bold]Results[/bold]",
                border_style="yellow",
            )
        )
        return

    table = Table(
        title=f"Matched Events in {settings.city}",
        title_style="bold white",
        show_lines=True,
        pad_edge=True,
    )
    table.add_column("Date", style="bold", width=12)
    table.add_column("Event", max_width=35)
    table.add_column("Venue", max_width=25)
    table.add_column("Matched Artist", max_width=22)
    table.add_column("Match", justify="center", width=7)
    table.add_column("Conf.", justify="center", width=6)
    table.add_column("Source", justify="center", width=10)

    # Sort: date ascending, then exact before vibe
    sorted_matches = sorted(
        matches,
        key=lambda m: (m.event.date, 0 if m.match_type == MatchType.EXACT else 1),
    )

    for m in sorted_matches:
        date_str = m.event.date.strftime("%a %b %d")
        venue_str = m.event.venue.name if m.event.venue else "TBA"

        if m.match_type == MatchType.EXACT:
            match_label = Text("EXACT", style="bold green")
            event_style = "green"
        else:
            match_label = Text("VIBE", style="bold yellow")
            event_style = "yellow"

        confidence_str = f"{m.confidence:.0%}"
        source_str = m.event.source.value.replace("_", " ").title()

        table.add_row(
            date_str,
            Text(m.event.name[:35], style=event_style),
            venue_str[:25],
            m.matched_artist.name[:22],
            match_label,
            confidence_str,
            source_str,
        )

    console.print()
    console.print(table)
    console.print(
        f"\n  [bold green]{sum(1 for m in matches if m.match_type == MatchType.EXACT)}[/bold green] exact matches  "
        f"[bold yellow]{sum(1 for m in matches if m.match_type == MatchType.VIBE)}[/bold yellow] vibe matches  "
        f"[dim]across {len({m.event.url for m in matches})} unique events[/dim]\n"
    )


def _check_config() -> bool:
    """Check that at least one music source is configured. Print instructions if not."""
    has_spotify = bool(settings.spotify_client_id and settings.spotify_client_secret)
    has_soundcloud = bool(settings.soundcloud_username)

    if not has_spotify and not has_soundcloud:
        console.print(
            Panel(
                "[bold red]No music sources configured.[/bold red]\n\n"
                "Set up at least one source in your [cyan].env[/cyan] file:\n\n"
                "[bold]Spotify[/bold] (recommended):\n"
                "  1. Create an app at https://developer.spotify.com/dashboard\n"
                "  2. Add these to .env:\n"
                "     SPOTIFY_CLIENT_ID=your_client_id\n"
                "     SPOTIFY_CLIENT_SECRET=your_client_secret\n\n"
                "[bold]SoundCloud[/bold]:\n"
                "  Add to .env:\n"
                "     SOUNDCLOUD_USERNAME=your_username\n\n"
                "[bold]Event sources[/bold] (optional, improves results):\n"
                "  BANDSINTOWN_APP_ID=your_app_id\n"
                "  SONGKICK_API_KEY=your_api_key\n\n"
                "[dim]Resident Advisor works without an API key.[/dim]",
                title="[bold]Setup Required[/bold]",
                border_style="red",
                expand=False,
            )
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


async def main() -> None:
    """Run the full Vibe Radar pipeline."""

    console.print(
        Panel(
            "[bold]Scanning your music taste and Madrid events...[/bold]",
            title="[bold cyan]VIBE RADAR[/bold cyan]",
            subtitle=f"[dim]{settings.city}, next {settings.days_ahead} days[/dim]",
            border_style="cyan",
        )
    )

    # -- Pre-flight check ----------------------------------------------------
    if not _check_config():
        return

    has_spotify = bool(settings.spotify_client_id and settings.spotify_client_secret)
    has_soundcloud = bool(settings.soundcloud_username)

    # -----------------------------------------------------------------------
    # Step 1: Collect artists from music sources (parallel)
    # -----------------------------------------------------------------------
    console.print("\n[bold]1/5[/bold]  Collecting your artists...", highlight=False)

    artist_tasks: list[asyncio.Task[list[Artist]]] = []

    if has_spotify:
        console.print("      [dim]Spotify: connecting...[/dim]")
        try:
            spotify = SpotifyCollector()
            artist_tasks.append(asyncio.create_task(spotify.collect_artists()))
        except Exception as exc:
            logger.warning("Failed to initialise Spotify collector: {}", exc)

    if has_soundcloud:
        console.print("      [dim]SoundCloud: connecting...[/dim]")
        try:
            soundcloud = SoundCloudCollector()
            artist_tasks.append(asyncio.create_task(soundcloud.collect_artists()))
        except Exception as exc:
            logger.warning("Failed to initialise SoundCloud collector: {}", exc)

    artist_results: list[list[Artist]] = []
    if artist_tasks:
        raw_results = await asyncio.gather(*artist_tasks, return_exceptions=True)
        for result in raw_results:
            if isinstance(result, BaseException):
                logger.error("Artist collection failed: {}", result)
            else:
                artist_results.append(result)

    # Merge and deduplicate
    all_artists = _merge_artists(artist_results)
    console.print(f"      [green]Found {len(all_artists)} unique artists[/green]")

    if not all_artists:
        console.print(
            "\n[yellow]No artists collected. Check your API credentials "
            "and try again.[/yellow]"
        )
        return

    # -----------------------------------------------------------------------
    # Step 2: Build taste profile
    # -----------------------------------------------------------------------
    console.print("\n[bold]2/5[/bold]  Building taste profile...", highlight=False)

    # Fetch Spotify audio profile if available
    if has_spotify:
        try:
            audio_profile = await spotify.get_audio_profile()
            # Attach the aggregated audio features to the profile later
            for artist in all_artists:
                if artist.audio_features is None and artist.source.value == "spotify":
                    artist.audio_features = audio_profile
        except Exception as exc:
            logger.warning("Could not fetch Spotify audio profile: {}", exc)

    taste_profile = build_taste_profile(all_artists)

    # -----------------------------------------------------------------------
    # Step 3: Print taste profile summary
    # -----------------------------------------------------------------------
    console.print()
    _print_taste_profile(taste_profile)

    # -----------------------------------------------------------------------
    # Step 4: Collect events from all sources (parallel)
    # -----------------------------------------------------------------------
    console.print("[bold]3/5[/bold]  Scanning Madrid events...", highlight=False)

    artist_names = [a.name for a in all_artists]

    event_tasks: list[asyncio.Task[list[Event]]] = []

    # Resident Advisor (no API key needed)
    console.print("      [dim]Resident Advisor...[/dim]")
    ra = ResidentAdvisorCollector()
    event_tasks.append(
        asyncio.create_task(ra.collect_events(days_ahead=settings.days_ahead))
    )

    # Bandsintown (needs app_id, but the collector handles missing keys)
    console.print("      [dim]Bandsintown...[/dim]")
    bit = BandsintownCollector()
    event_tasks.append(
        asyncio.create_task(
            bit.collect_events(artist_names=artist_names, days_ahead=settings.days_ahead)
        )
    )

    # Songkick (needs api_key, but the collector handles missing keys)
    console.print("      [dim]Songkick...[/dim]")
    sk = SongkickCollector()
    event_tasks.append(
        asyncio.create_task(sk.collect_events(days_ahead=settings.days_ahead))
    )

    event_results: list[list[Event]] = []
    raw_event_results = await asyncio.gather(*event_tasks, return_exceptions=True)
    for result in raw_event_results:
        if isinstance(result, BaseException):
            logger.error("Event collection failed: {}", result)
        else:
            event_results.append(result)

    # Merge and deduplicate events
    all_events = _merge_events(event_results)
    console.print(f"      [green]Found {len(all_events)} unique events[/green]")

    if not all_events:
        console.print(
            "\n[yellow]No events found in Madrid for the next "
            f"{settings.days_ahead} days. Try increasing DAYS_AHEAD.[/yellow]"
        )
        return

    # -----------------------------------------------------------------------
    # Step 5: Run matching
    # -----------------------------------------------------------------------
    console.print("\n[bold]4/5[/bold]  Matching artists to events...", highlight=False)

    # Exact matching
    exact_matcher = ExactMatcher()
    exact_matches = exact_matcher.match(all_artists, all_events)
    console.print(f"      [green]{len(exact_matches)} exact matches[/green]")

    # Vibe matching (exclude events already matched exactly)
    exact_event_urls: set[str] = set()
    for m in exact_matches:
        if m.event.url:
            exact_event_urls.add(m.event.url)

    vibe_matcher = VibeMatcher()
    vibe_matches = vibe_matcher.match(
        all_artists,
        all_events,
        taste_profile=taste_profile,
        exclude_event_ids=exact_event_urls,
    )
    console.print(f"      [yellow]{len(vibe_matches)} vibe matches[/yellow]")

    # -----------------------------------------------------------------------
    # Step 6: Print results
    # -----------------------------------------------------------------------
    console.print("\n[bold]5/5[/bold]  Results", highlight=False)

    all_matches = exact_matches + vibe_matches
    _print_results(all_matches)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
