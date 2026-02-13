#!/usr/bin/env python3
"""
Build an iCalendar (.ics) file of Noise Pop shows for bands you like.

Reads noisepop_liked.csv (artist names from column 3), schedule.json,
matches liked artists to schedule entries, and writes a .ics file with
date, band, venue, and venue address (from cache or Nominatim lookup).

Usage:
  python liked_to_ical.py
  python liked_to_ical.py --no-lookup   # skip address lookup, use cache only
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIKED_CSV = SCRIPT_DIR / "noisepop_liked.csv"
SCHEDULE_JSON = SCRIPT_DIR / "schedule.json"
VENUE_CACHE = SCRIPT_DIR / "venue_address_cache.json"
OUT_ICS = SCRIPT_DIR / "noisepop_liked_shows.ics"

KNOWN_VENUE_ADDRESSES: dict[str, str] = {
    "Great American Music Hall": "859 O'Farrell St, San Francisco, CA 94109",
    "Rickshaw Stop": "155 Fell St, San Francisco, CA 94102",
    "Swedish American Hall": "2174 Market St, San Francisco, CA 94114",
    "Bottom of the Hill": "1233 17th St, San Francisco, CA 94107",
    "Public Works": "161 Erie St, San Francisco, CA 94103",
    "SF Jazz": "201 Franklin St, San Francisco, CA 94102",
    "The Chapel": "777 Valencia St, San Francisco, CA 94110",
    "Cafe Du Nord": "2170 Market St, San Francisco, CA 94114",
    "Gray Area": "2665 Mission St, San Francisco, CA 94110",
    "Brick & Mortar Music Hall": "1710 Mission St, San Francisco, CA 94103",
    "The UC Theatre": "2036 University Ave, Berkeley, CA 94704",
    "The Independent": "628 Divisadero St, San Francisco, CA 94117",
    "Kilowatt": "3160 16th St, San Francisco, CA 94103",
    "Bender's": "806 S Van Ness Ave, San Francisco, CA 94110",
    "4 Star Theater": "2200 Clement St, San Francisco, CA 94121",
    "Hi-Hat": "5043 York Blvd, Los Angeles, CA 90042",
    "KQED HQ": "2601 Mariposa St, San Francisco, CA 94110",
}

DEFAULT_START_HOUR = 20
DEFAULT_END_HOUR = 23
DEFAULT_MINUTE = 0
ICAL_YEAR = 2026
MONTH_ABBREV_TO_NUM = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def load_liked_artists(csv_path: Path) -> set[str]:
    """Extract unique artist names from the liked CSV (column index 3)."""
    artists: set[str] = set()
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if len(row) > 3:
                raw = row[3].strip()
                for part in re.split(r"[;&,]|\s+and\s+", raw, flags=re.IGNORECASE):
                    name = part.strip()
                    if name:
                        artists.add(name)
    return artists


def load_schedule(json_path: Path) -> list[dict]:
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


def normalize_for_match(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[.\-_',]", "", s)
    return s


def headliner_matches_artist(headliner: str, artist: str) -> bool:
    h = re.sub(
        r"\s*[-–—]\s*(Night\s+\d+|SOLD OUT|2 Shows|Early Show|Late Show).*$",
        "",
        headliner,
        flags=re.IGNORECASE,
    )
    h = re.sub(r"\s*\([^)]*\)\s*$", "", h)
    h = re.sub(r"\s+at\s+[\w\s]+$", "", h, flags=re.IGNORECASE)
    h = re.sub(r"\s*&\s*Friends.*$", "", h, flags=re.IGNORECASE)
    h = re.sub(r"\s+play\s+.*$", "", h, flags=re.IGNORECASE)
    h = h.strip()

    nh = normalize_for_match(h)
    na = normalize_for_match(artist)
    if not na or not nh:
        return False
    if nh == na:
        return True
    if nh.startswith(na) or na.startswith(nh):
        return True
    if na in nh or nh in na:
        return True
    if nh.replace(" of ", " ") == na.replace(" of ", " "):
        return True
    return False


def match_schedule_to_liked(
    schedule: list[dict], liked_artists: set[str]
) -> list[dict]:
    matched: list[dict] = []
    for entry in schedule:
        headliner = (entry.get("headliner") or "").strip()
        if "happy hour" in headliner.lower():
            continue
        for artist in liked_artists:
            if headliner_matches_artist(headliner, artist):
                matched.append({**entry, "matched_artist": artist})
                break
    return matched


def parse_schedule_date(date_str: str) -> tuple[int, int, int]:
    parts = date_str.strip().split()
    if len(parts) < 2:
        return (ICAL_YEAR, 1, 1)
    month_str = parts[0][:3]
    month = MONTH_ABBREV_TO_NUM.get(month_str, 1)
    try:
        day = int(parts[1].rstrip(","))
    except ValueError:
        day = 1
    return (ICAL_YEAR, month, day)


def format_ical_datetime(year: int, month: int, day: int, hour: int, minute: int) -> str:
    return f"{year:04d}{month:02d}{day:02d}T{hour:02d}{minute:02d}00"


def load_venue_cache() -> dict[str, str]:
    if not VENUE_CACHE.exists():
        return {}
    try:
        with open(VENUE_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_venue_cache(cache: dict[str, str]) -> None:
    with open(VENUE_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def lookup_venue_address(venue_name: str, do_lookup: bool) -> str:
    cache = load_venue_cache()
    if venue_name in cache and cache[venue_name]:
        return cache[venue_name]
    if venue_name in KNOWN_VENUE_ADDRESSES:
        addr = KNOWN_VENUE_ADDRESSES[venue_name]
        cache[venue_name] = addr
        save_venue_cache(cache)
        return addr
    if do_lookup:
        time.sleep(1.1)
        q = f"{venue_name}, San Francisco, CA"
        try:
            url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                {"q": q, "format": "json", "limit": 1}
            )
            req = urllib.request.Request(url, headers={"User-Agent": "NoisePopScheduler/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if data and isinstance(data, list) and len(data) > 0:
                addr = data[0].get("display_name", "")
                if addr:
                    cache[venue_name] = addr
                    save_venue_cache(cache)
                    return addr
        except Exception:  # pylint: disable=broad-except
            pass
    cache[venue_name] = ""
    save_venue_cache(cache)
    return ""


def escape_ical_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def write_ics(matched: list[dict], venue_addresses: dict[str, str], out_path: Path) -> None:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Noise Pop Liked Shows//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Noise Pop 2026 – Liked Bands",
    ]
    for i, entry in enumerate(matched):
        headliner = entry.get("headliner", "")
        venue = entry.get("venue", "")
        date_str = entry.get("date", "")
        addr = venue_addresses.get(venue, "")
        location = addr if addr else venue

        y, m, d = parse_schedule_date(date_str)
        start_dt = format_ical_datetime(y, m, d, DEFAULT_START_HOUR, DEFAULT_MINUTE)
        end_dt = format_ical_datetime(y, m, d, DEFAULT_END_HOUR, DEFAULT_MINUTE)

        summary = f"{headliner} at {venue}"
        desc_parts = [f"Band: {headliner}", f"Venue: {venue}"]
        if addr:
            desc_parts.append(f"Address: {addr}")
        description = "\\n".join(escape_ical_text(p) for p in desc_parts)

        uid = f"noisepop2026-{i}-{start_dt}@liked"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART;TZID=America/Los_Angeles:{start_dt}",
            f"DTEND;TZID=America/Los_Angeles:{end_dt}",
            f"SUMMARY:{escape_ical_text(summary)}",
            f"DESCRIPTION:{description}",
            f"LOCATION:{escape_ical_text(location)}",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\r\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build iCal from liked bands + schedule")
    parser.add_argument(
        "--no-lookup",
        action="store_true",
        help="Do not call Nominatim for unknown venues (use cache + known only)",
    )
    parser.add_argument(
        "-o", "--output",
        default=str(OUT_ICS),
        help="Output .ics file path",
    )
    args = parser.parse_args()

    if not LIKED_CSV.exists():
        print(f"Missing {LIKED_CSV}")
        raise SystemExit(1)
    if not SCHEDULE_JSON.exists():
        print(f"Missing {SCHEDULE_JSON}")
        raise SystemExit(1)

    liked_artists = load_liked_artists(LIKED_CSV)
    print(f"Liked artists: {len(liked_artists)}")

    schedule = load_schedule(SCHEDULE_JSON)
    matched = match_schedule_to_liked(schedule, liked_artists)
    print(f"Matched shows: {len(matched)}")

    if not matched:
        print("No matching shows. Exiting.")
        raise SystemExit(0)

    venue_addresses: dict[str, str] = {}
    venues_needed = {e.get("venue", "") for e in matched if e.get("venue")}
    for venue in venues_needed:
        venue_addresses[venue] = lookup_venue_address(venue, do_lookup=not args.no_lookup)

    out_path = Path(args.output)
    write_ics(matched, venue_addresses, out_path)
    print(f"Wrote {out_path}")
    print("Import this .ics file into Apple Calendar (File → Import) or other iCal-compatible app.")


if __name__ == "__main__":
    main()
