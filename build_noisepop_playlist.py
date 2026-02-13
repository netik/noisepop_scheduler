#!/usr/bin/env python3
"""
Build a playlist of the most popular song for each Noise Pop headliner.

Reads schedule.json, skips 'happy hour' entries, looks up each artist on Last.fm
(artist.getTopTracks), and writes a playlist to JSON + Markdown. No Spotify API.

Setup:
  1. Get a free API key at https://www.last.fm/api/account/create
  2. Put it in credentials.json (see below) or set env var LASTFM_API_KEY
  3. pip install -r requirements.txt
  4. python build_noisepop_playlist.py

  credentials.json (next to this script) should contain:
    {"api_key": "your_lastfm_api_key"}
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import requests

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"
SCRIPT_DIR = Path(__file__).resolve().parent
CREDENTIALS_PATH = SCRIPT_DIR / "credentials.json"
SCHEDULE_PATH = SCRIPT_DIR / "schedule.json"
OUT_JSON = SCRIPT_DIR / "noisepop_2026_playlist.json"
OUT_MD = SCRIPT_DIR / "noisepop_2026_playlist.md"


def load_lastfm_api_key() -> str | None:
    """Load Last.fm API key from credentials.json or LASTFM_API_KEY env."""
    if CREDENTIALS_PATH.exists():
        try:
            with open(CREDENTIALS_PATH, encoding="utf-8") as f:
                creds = json.load(f)
            key = creds.get("api_key") or creds.get("API key")
            if key:
                return key.strip()
        except (json.JSONDecodeError, OSError):
            pass
    return os.environ.get("LASTFM_API_KEY")


def load_schedule(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_happy_hour(headliner: str) -> bool:
    return "happy hour" in headliner.lower()


def headliners_from_schedule(schedule: list[dict]) -> list[str]:
    headliners = []
    for item in schedule:
        name = (item.get("headliner") or "").strip()
        if not name or is_happy_hour(name):
            continue
        headliners.append(name)
    return headliners


def clean_artist_query(headliner: str) -> str:
    """Strip suffixes that hurt Last.fm artist matching."""
    query = re.sub(
        r"\s*[-–—]\s*(Night\s+\d+|SOLD OUT|2 Shows|Early Show|Late Show).*$",
        "",
        headliner,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"\s*\([^)]*\)\s*$", "", query)
    query = re.sub(r"\s+at\s+[\w\s]+$", "", query, flags=re.IGNORECASE)
    return (query.strip() or headliner).strip()


def lastfm_get(api_key: str, method: str, **params: str) -> dict | None:
    payload = {"method": method, "api_key": api_key, "format": "json", **params}
    try:
        r = requests.get(LASTFM_BASE, params=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            return None
        return data
    except Exception:  # pylint: disable=broad-except
        return None


def get_top_track_for_artist(api_key: str, headliner: str) -> dict | None:
    """
    Return {artist, track, url} for the headliner's most popular track on Last.fm,
    or None if not found.
    """
    query = clean_artist_query(headliner)
    data = lastfm_get(
        api_key,
        "artist.getTopTracks",
        artist=query,
        limit="1",
        autocorrect="1",
    )
    if not data:
        return None
    toptracks = data.get("toptracks", {}) or {}
    track_list = toptracks.get("track")
    if not track_list:
        search = lastfm_get(api_key, "artist.search", artist=query, limit="1")
        if not search:
            return None
        artists = (search.get("results") or {}).get("artistmatches", {}).get("artist")
        if not artists:
            return None
        artist_name = artists[0].get("name") or query
        time.sleep(0.25)
        data = lastfm_get(
            api_key,
            "artist.getTopTracks",
            artist=artist_name,
            limit="1",
        )
        if not data:
            return None
        toptracks = data.get("toptracks", {}) or {}
        track_list = toptracks.get("track")

    if not track_list:
        return None
    track = track_list[0] if isinstance(track_list, list) else track_list
    artist_name = toptracks.get("@attr", {}).get("artist") or query
    if not artist_name and isinstance(track.get("artist"), dict):
        artist_name = track["artist"].get("name", query)
    elif not artist_name:
        artist_name = query
    return {
        "artist": artist_name,
        "track": track.get("name", ""),
        "url": track.get("url", ""),
    }


def main() -> None:
    api_key = load_lastfm_api_key()
    if not api_key:
        print(
            "Set api_key in credentials.json or set LASTFM_API_KEY "
            "(get a free key at https://www.last.fm/api/account/create)."
        )
        raise SystemExit(1)

    schedule = load_schedule(SCHEDULE_PATH)
    headliners = headliners_from_schedule(schedule)
    print(f"Found {len(headliners)} headliners (happy hours excluded).")

    playlist: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    failed: list[str] = []

    for i, headliner in enumerate(headliners, 1):
        time.sleep(0.2)
        result = get_top_track_for_artist(api_key, headliner)
        if result:
            key = (result["artist"].lower(), result["track"].lower())
            if key not in seen_keys:
                seen_keys.add(key)
                playlist.append(
                    {
                        "headliner": headliner,
                        "artist": result["artist"],
                        "track": result["track"],
                        "url": result["url"],
                    }
                )
                print(f"  [{i}/{len(headliners)}] {headliner} -> {result['track']}")
            else:
                print(f"  [{i}/{len(headliners)}] {headliner} -> skipped (duplicate)")
        else:
            failed.append(headliner)
            print(f"  [{i}/{len(headliners)}] {headliner} -> not found")

    if failed:
        print(f"\nCould not find top track for {len(failed)} headliner(s):")
        for name in failed:
            print(f"  - {name}")

    if not playlist:
        print("No tracks found. Exiting.")
        raise SystemExit(0)

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(playlist, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {OUT_JSON}")

    lines = [
        "# Noise Pop 2026 – Headliner Top Tracks",
        "",
        "One most-popular track per headliner (from Last.fm).",
        "",
        "| # | Headliner | Track | Listen |",
        "|---|-----------|-------|--------|",
    ]
    yt_base = "https://www.youtube.com/results?search_query="
    for idx, row in enumerate(playlist, 1):
        q = urllib.parse.quote(f"{row['artist']} {row['track']}")
        lines.append(
            f"| {idx} | {row['headliner']} | {row['artist']} – {row['track']} | "
            f"[Last.fm]({row['url']}) · [YouTube]({yt_base}{q}) |"
        )
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {OUT_MD}")
    print(f"\nDone. {len(playlist)} tracks. Open the Markdown for links to listen.")


if __name__ == "__main__":
    main()
