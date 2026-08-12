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
Horizon Huntarr - find upcoming/unreleased movies from TMDB collections the
user already owns at least one part of.

Extracted out of recommenders/external.py (PR2 architecture decomposition,
step 2 - see CHANGELOG). Horizon Huntarr is a deliberate sibling of Sequel
Huntarr (recommenders/huntarr.py): it shares the exact same "movie ->
collection -> full collection details" data model, and reuses Sequel
Huntarr's collection-detail cache (via load_huntarr_cache) and its
get_collection_details() TMDB helper directly rather than duplicating
either - see find_horizon_movies()'s own docstring/comments for how the
two caches interact.

This is a pure relocation: every function/constant below is byte-for-byte
identical to its former recommenders/external.py definition (see that
module's git history for the pre-move version) - only import paths
changed. recommenders/external.py re-exports everything below it still
needs (find_horizon_movies, load_horizon_cache, save_horizon_cache,
get_movie_status, HORIZON_HUNTARR_CACHE_VERSION) so existing callers/tests
(many of which `@patch("recommenders.external.X")`) keep working
unchanged.
"""

import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from recommenders.huntarr import get_collection_details, load_huntarr_cache
from utils import (
    CYAN,
    GREEN,
    RESET,
    TMDB_REQUEST_TIMEOUT,
    get_project_root,
    load_json_cache,
    log_warning,
    save_json_cache,
)

logger = logging.getLogger("curatarr")

HORIZON_HUNTARR_CACHE_VERSION = 1  # v1: Initial horizon huntarr


def get_movie_status(tmdb_api_key: str, tmdb_id: int) -> Tuple[str, str]:
    """
    Fetch status and release date for a movie from TMDB.

    Args:
        tmdb_api_key: TMDB API key
        tmdb_id: TMDB movie ID

    Returns:
        Tuple of (status, release_date). Status values: Rumored, Planned,
        In Production, Post Production, Released, Canceled
    """
    try:
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
        params = {"api_key": tmdb_api_key}
        response = requests.get(url, params=params, timeout=TMDB_REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            return data.get("status", "Unknown"), data.get("release_date", "")
    except (requests.RequestException, KeyError) as e:
        logger.debug(f"Failed to fetch status for TMDB ID {tmdb_id}: {e}")
    return "Unknown", ""


def load_horizon_cache(cache_path: str, stale_days: int = 7) -> Dict:
    """Load Horizon Huntarr cache from disk."""
    cache = load_json_cache(cache_path)
    if cache and cache.get("version") == HORIZON_HUNTARR_CACHE_VERSION:
        cached_at = cache.get("cached_at", 0)
        age_days = (time.time() - cached_at) / 86400
        if age_days < stale_days:
            return cache
    return {}


def save_horizon_cache(cache_path: str, cache: Dict) -> None:
    """Save Horizon Huntarr cache to disk."""
    cache["version"] = HORIZON_HUNTARR_CACHE_VERSION
    cache["cached_at"] = time.time()
    if not save_json_cache(cache_path, cache):
        log_warning("Could not save Horizon Huntarr cache")


def find_horizon_movies(tmdb_api_key: str, plex: Any, library_name: str, stale_days: int = 7) -> List[Dict]:
    """
    Find upcoming/unreleased movies from collections user owns.

    Scans library for movies with collection IDs, fetches full collections
    from TMDB, and identifies movies that are not yet released.

    Args:
        tmdb_api_key: TMDB API key
        plex: PlexServer instance
        library_name: Name of movie library
        stale_days: Days before cache is considered stale

    Returns:
        List of upcoming movie dicts with status info
    """
    from utils.display import show_progress

    project_root = get_project_root()
    cache_path = os.path.join(project_root, "cache", "horizon_huntarr_cache.json")
    sequel_cache_path = os.path.join(project_root, "cache", "huntarr_cache.json")

    try:
        library = plex.library.section(library_name)
        items = library.all()
    except Exception as e:
        log_warning(f"Could not access library {library_name}: {e}")
        return []

    # Build current library state
    library_tmdb_ids = set()
    for item in items:
        for guid in item.guids:
            if "tmdb://" in guid.id:
                try:
                    tmdb_id: Optional[int] = int(guid.id.split("tmdb://")[1])
                    library_tmdb_ids.add(tmdb_id)
                    break
                except (ValueError, IndexError):
                    continue

    # Load horizon cache
    cache = load_horizon_cache(cache_path, stale_days)
    cached_library_ids = set(cache.get("library_tmdb_ids", []))

    if cached_library_ids == library_tmdb_ids and cache.get("horizon_movies"):
        print(f"{GREEN}  Using cached Horizon Huntarr data ({len(cache['horizon_movies'])} upcoming movies){RESET}")
        return cache["horizon_movies"]

    # Try to reuse sequel huntarr's collection data
    sequel_cache = load_huntarr_cache(sequel_cache_path, stale_days)
    movie_collections = sequel_cache.get("movie_collections", {})
    collection_details_cache = sequel_cache.get("collection_details", {})

    # Diff the current library against the cached movie->collection map,
    # exactly like find_missing_sequels does: trust the cache for ids it
    # already knows about, but fetch collection data for any movie that
    # isn't in it yet (e.g. added to Plex after Sequel Huntarr's last run).
    # This intentionally always scans `items` rather than trusting
    # movie_collections wholesale, so newly-owned movies aren't silently
    # skipped.
    total_items = len(items)
    print(f"{CYAN}  Scanning {total_items} movies for collections...{RESET}")

    collection_owned: Dict[int, Set[int]] = {}

    for i, item in enumerate(items):
        if i % 50 == 0:
            show_progress("  Scanning library", i + 1, total_items)

        tmdb_id = None
        for guid in item.guids:
            if "tmdb://" in guid.id:
                try:
                    tmdb_id = int(guid.id.split("tmdb://")[1])
                    break
                except (ValueError, IndexError):
                    continue

        if not tmdb_id:
            continue

        tmdb_id_str = str(tmdb_id)
        if tmdb_id_str in movie_collections:
            coll_id = movie_collections[tmdb_id_str]
            if coll_id:
                if coll_id not in collection_owned:
                    collection_owned[coll_id] = set()
                collection_owned[coll_id].add(tmdb_id)
        else:
            # Fetch collection ID
            try:
                url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
                params = {"api_key": tmdb_api_key}
                response = requests.get(url, params=params, timeout=TMDB_REQUEST_TIMEOUT)

                if response.status_code == 200:
                    data = response.json()
                    collection = data.get("belongs_to_collection")
                    if collection:
                        coll_id = collection["id"]
                        movie_collections[tmdb_id_str] = coll_id
                        if coll_id not in collection_owned:
                            collection_owned[coll_id] = set()
                        collection_owned[coll_id].add(tmdb_id)
                    else:
                        movie_collections[tmdb_id_str] = None
            except (requests.RequestException, KeyError):
                continue

    show_progress("  Scanning library", total_items, total_items)

    print(f"{GREEN}  Found {len(collection_owned)} collections to check for upcoming movies{RESET}")

    # Find upcoming movies from collections
    horizon_movies = []
    today = datetime.now().strftime("%Y-%m-%d")
    total_collections = len(collection_owned)

    print(f"{CYAN}  Checking collections for upcoming releases...{RESET}")

    for i, (coll_id, _owned_ids) in enumerate(collection_owned.items()):
        if i % 5 == 0:
            show_progress("  Checking collections", i + 1, total_collections)

        coll_id_str = str(coll_id)
        if coll_id_str in collection_details_cache:
            coll_details = collection_details_cache[coll_id_str]
        else:
            coll_details = get_collection_details(tmdb_api_key, coll_id)
            if coll_details:
                collection_details_cache[coll_id_str] = coll_details

        if not coll_details:
            continue

        coll_name = coll_details["collection_name"]
        all_movies = coll_details["movies"]

        # Find movies that are unreleased (no year OR future release date)
        for movie in all_movies:
            release_date = movie.get("release_date", "")
            year = movie.get("year", "")

            # Skip if already in library
            if movie["tmdb_id"] in library_tmdb_ids:
                continue

            # Check if unreleased: no release date OR future release date
            is_unreleased = not year or (release_date and release_date > today)

            if is_unreleased:
                # Get current status from TMDB
                status, current_release_date = get_movie_status(tmdb_api_key, movie["tmdb_id"])

                # Skip canceled movies
                if status == "Canceled":
                    continue

                # Skip if status shows it's already released
                if status == "Released":
                    continue

                horizon_movies.append(
                    {
                        "tmdb_id": movie["tmdb_id"],
                        "title": movie["title"],
                        "collection_id": coll_id,
                        "collection_name": coll_name,
                        "release_date": current_release_date or "TBA",
                        "status": status,
                        "genre_ids": movie.get("genre_ids", []),
                    }
                )

    show_progress("  Checking collections", total_collections, total_collections)

    # Sort by collection name, then status priority, then release date
    status_order = {"Post Production": 0, "In Production": 1, "Planned": 2, "Rumored": 3, "Unknown": 4}
    horizon_movies.sort(
        key=lambda x: (x["collection_name"], status_order.get(x["status"], 5), x.get("release_date", "ZZZ"))
    )

    # Save cache
    save_horizon_cache(cache_path, {"library_tmdb_ids": list(library_tmdb_ids), "horizon_movies": horizon_movies})

    print(f"{GREEN}  Found {len(horizon_movies)} upcoming movies from owned collections{RESET}")
    return horizon_movies
