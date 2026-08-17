#!/usr/bin/env python3
# curatarr
# Copyright (C) 2026 OrchestratedChaos
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
READ-ONLY audit of what is actually in each user's Plex recommendation
collection.

Measures `section.search(label=...)` - the real artifact - rather than
parsing a run log. CLAUDE.md is explicit about why: the log prints
`plex_recs` (up to `general.limit_plex_results`, 2x the target), while
the collection holds the smaller `final_items` set. One observed run
printed 50 titles with 19 kid films while the collection held 44 with 7.

Never writes. No label is added or removed, no collection is created or
touched, and the recommender itself is never constructed - importing it
would connect as the admin and (via MovieCache.update_cache) rewrite the
media cache. Only `section.search`, per-user played-id reads, and local
JSON reads happen here.

Exit code is 1 if any check fails, so this is usable as a post-run gate.

Usage:
    python3 verify_collections.py [--user USERNAME] [--media movie|tv]
"""

import argparse
import json
import os
import sys

# .claude/skills/verify-recommendations/<this file> -> four levels up.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
sys.path.insert(0, REPO)

import yaml  # noqa: E402
from plexapi.server import PlexServer  # noqa: E402

from utils.franchise import build_franchise_index, find_next_unwatched  # noqa: E402
from utils.labels import build_label_name  # noqa: E402
from utils.plex import fetch_user_played_ids, get_excluded_genres_for_user  # noqa: E402
from utils.plex_policy import get_franchise_order_for_user, get_max_rating_for_user, is_rating_allowed  # noqa: E402

GREEN, YELLOW, RED, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def load_config():
    with open(os.path.join(REPO, "config", "config.yml"), "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_tuning():
    path = os.path.join(REPO, "config", "tuning.yml")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def user_list(config):
    users = (config.get("plex_users") or {}).get("list") or (config.get("users") or {}).get("list") or []
    if isinstance(users, str):
        users = [u.strip() for u in users.split(",") if u.strip()]
    return users


def watched_ids_for(plex, config, user, library, media_key):
    """
    Returns (as_of_last_run, live).

    Kept apart on purpose. The union of the two is what the recommender
    reasons with (BaseRecommender._franchise_watched_ids - the shared
    watched cache alone misses plays only that user's own Plex view knows
    about). But for auditing, the DIFFERENCE is the interesting part: a
    still-labeled item the last run already knew was watched is a real
    failure, while one watched since that run is just lag the next run
    clears via _remove_outdated_labels(). Collapsing them into one set
    makes this tool cry wolf every time somebody watches a film.
    """
    prefix = "watched_cache" if media_key == "movies" else "tv_watched_cache"
    path = os.path.join(REPO, "cache", f"{prefix}_plex_{user}.json")
    as_of_last_run = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        key = "watched_movie_ids" if media_key == "movies" else "watched_show_ids"
        as_of_last_run = {int(i) for i in data.get(key, []) or [] if str(i).isdigit()}
    live = set(as_of_last_run)
    try:
        live |= fetch_user_played_ids(plex, config, user, library)
    except Exception as e:  # noqa: BLE001 - reporting tool; a partial answer beats none
        print(f"    {YELLOW}note: per-user played ids unavailable ({e}); using the shared cache only{RESET}")
    return as_of_last_run, live


def check_user(plex, config, tuning, user, media):
    media_key = "movies" if media == "movie" else "shows"
    library = config["plex"]["movie_library" if media == "movie" else "tv_library"]
    section = plex.library.section(library)

    coll_cfg = config.get("collections") or {}
    label = build_label_name(
        coll_cfg.get("label_name", "Recommended"),
        user_list(config),
        user,
        coll_cfg.get("append_usernames", True),
    )
    items = section.search(label=label)

    cache_file = "all_movies_cache.json" if media == "movie" else "all_shows_cache.json"
    with open(os.path.join(REPO, "cache", cache_file), "r", encoding="utf-8") as fh:
        cached = json.load(fh)[media_key]

    prefs = (config.get("users") or {}).get("preferences") or {}
    general = config.get("general") or {}
    global_excludes = [g.strip().lower() for g in (general.get("exclude_genre") or "").split(",") if g.strip()]
    excluded = {g.lower() for g in get_excluded_genres_for_user(global_excludes, prefs, user)}
    max_rating = get_max_rating_for_user(prefs, user)
    media_tuning = tuning.get("movies" if media == "movie" else "tv") or {}
    target = media_tuning.get("limit_results", 50 if media == "movie" else 20)

    watched_then, watched = watched_ids_for(plex, config, user, library, media_key)
    index = build_franchise_index(cached)
    franchise_on = get_franchise_order_for_user(prefs, user, bool(media_tuning.get("franchise_order", True)))

    problems = []
    notes = []
    series_items = started_items = 0

    if not items:
        # Not a silent pass. Either the collection was never built, it was
        # removed, or the label drifted - all worth shouting about. The one
        # legitimate case is an explicit movies.recommend_for_no_history:
        # false user, which the message names so it can be dismissed.
        problems.append(
            f"no items carry label {label!r} - collection missing, or this user is recommend_for_no_history: false"
        )

    for item in items:
        key = int(item.ratingKey)
        info = cached.get(str(key)) or {}
        title = f"{item.title} ({getattr(item, 'year', '?')})"

        if key in watched_then:
            problems.append(f"watched before the last run but still labeled: {title}")
        elif key in watched:
            # Watched since the run that built this collection; the next
            # run strips the label in _remove_outdated_labels().
            notes.append(f"watched since the last run, clears tonight: {title}")

        genres = [g.lower() for g in (info.get("genres") or [])]
        hit = excluded.intersection(genres)
        if hit:
            problems.append(f"excluded genre {sorted(hit)}: {title}")

        if max_rating and not is_rating_allowed(getattr(item, "contentRating", None), max_rating, media):
            problems.append(f"exceeds max_rating {max_rating} ({getattr(item, 'contentRating', None)}): {title}")

        entries = index.get(info.get("collection_id")) if info.get("collection_id") else None
        if entries and len(entries) > 1:
            series_items += 1
            if any(e.rating_key in watched for e in entries):
                started_items += 1
            if franchise_on:
                canonical = find_next_unwatched(
                    entries, watched, excluded_genres=excluded, max_rating=max_rating, media_type=media
                )
                if canonical is None or canonical.rating_key != key:
                    want = canonical.label() if canonical else "nothing (series exhausted)"
                    problems.append(
                        f"mid-series entry: {title} - franchise order wants {want} [{info.get('collection_name')}]"
                    )

    status = f"{GREEN}OK{RESET}" if not problems else f"{RED}{len(problems)} problem(s){RESET}"
    short = f" {YELLOW}({target - len(items)} short of {target}){RESET}" if len(items) < target else ""
    print(
        f"  {user:<18} {label:<28} {len(items):>3} items{short} | "
        f"series {series_items:>2} ({started_items} started) | {status}"
    )
    for p in problems[:8]:
        print(f"       {RED}!{RESET} {p}")
    if len(problems) > 8:
        print(f"       ... and {len(problems) - 8} more")
    for n in notes[:4]:
        print(f"       {YELLOW}i{RESET} {n}")
    if len(notes) > 4:
        print(f"       ... and {len(notes) - 4} more watched since the run")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", help="Only this user (default: every configured user)")
    ap.add_argument("--media", choices=["movie", "tv"], default="movie")
    args = ap.parse_args()

    config = load_config()
    tuning = load_tuning()
    plex = PlexServer(config["plex"]["url"], config["plex"]["token"], timeout=30)
    print(f"{GREEN}Connected to {plex.friendlyName} (Plex {plex.version}){RESET} - READ ONLY\n")

    configured = user_list(config)
    if not configured:
        print(f"{RED}No users configured{RESET}")
        return 1
    if args.user and args.user not in configured:
        # Otherwise a typo searches for a label nothing carries, finds
        # nothing, and reports success.
        print(f"{RED}Unknown user {args.user!r}. Configured: {', '.join(configured)}{RESET}")
        return 1
    users = [args.user] if args.user else configured

    total = 0
    for user in users:
        total += len(check_user(plex, config, tuning, user, args.media))

    print()
    if total:
        print(f"{RED}{total} problem(s) found{RESET}")
        return 1
    print(f"{GREEN}All checks passed{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
