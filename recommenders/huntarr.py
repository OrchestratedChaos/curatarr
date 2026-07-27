"""
Sequel Huntarr - find missing movies from TMDB collections the user has
started (owns at least one part of).

Extracted out of recommenders/external.py (PR2 architecture decomposition
- see CHANGELOG). This module also owns get_watch_providers() and
get_collection_details(): both are TMDB detail-fetching helpers Sequel
Huntarr introduced, and both are reused elsewhere -
get_collection_details() by Horizon Huntarr (recommenders/horizon.py,
which shares the exact same "movie -> collection -> full collection
details" data model as a deliberate sibling feature), and
get_watch_providers() by recommenders/external.py's
categorize_by_streaming_service(). Keeping them here (rather than a
separate shared "TMDB lookups" module) was a deliberate scope decision to
keep this decomposition's seams mechanical/low-risk - splitting them out
further into their own module is a reasonable follow-up, not required by
the decomposition this module is part of.

This is a pure relocation: every function/constant below is byte-for-byte
identical to its former recommenders/external.py definition (see that
module's git history for the pre-move version) - only import paths
changed. recommenders/external.py re-exports everything below it still
needs (find_missing_sequels, load_huntarr_cache, save_huntarr_cache,
get_watch_providers, get_collection_details, and the constants) so
existing callers/tests (many of which `@patch("recommenders.external.X")`)
keep working unchanged.
"""

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from utils import (
    CYAN,
    GREEN,
    RESET,
    TMDB_ANIMATION_GENRE_ID,
    TMDB_REQUEST_TIMEOUT,
    TMDB_TV_MOVIE_GENRE_ID,
    get_project_root,
    load_json_cache,
    log_warning,
    save_json_cache,
)

logger = logging.getLogger("curatarr")

# TMDB Watch Provider ID mappings (US region) - subscription streaming
TMDB_STREAMING_PROVIDERS = {
    8: "netflix",
    15: "hulu",
    337: "disney_plus",
    9: "amazon_prime",
    531: "paramount_plus",
    350: "apple_tv_plus",
    384: "max",
    387: "peacock",
    1899: "max",  # HBO Max (legacy ID)
    203: "crunchyroll",
    283: "crackle",
    613: "tubi",
    207: "mubi",
    619: "shudder",
}

# TMDB Watch Provider ID mappings - rental/purchase services
TMDB_RENTAL_PROVIDERS = {
    2: "Apple TV",
    3: "Google Play",
    7: "Vudu",
    10: "Amazon",
    68: "Microsoft",
    192: "YouTube",
    358: "DIRECTV",
    486: "Spectrum",
}

# Backwards compatibility alias
TMDB_PROVIDERS = TMDB_STREAMING_PROVIDERS

# Watch provider cache (streaming availability changes infrequently)
WATCH_PROVIDER_CACHE_TTL = 7 * 24 * 3600  # 7 days in seconds
_watch_provider_cache: Dict[Tuple[int, str], Tuple[float, Dict]] = {}  # (tmdb_id, media_type) -> (timestamp, providers)


def get_watch_providers(tmdb_api_key: str, tmdb_id: int, media_type: str = "movie") -> Dict[str, List[str]]:
    """
    Get watch providers for a TMDB item (US region).
    Results are cached for 7 days since streaming availability changes infrequently.

    Returns dict with:
        - streaming: subscription services (Netflix, Hulu, etc.)
        - rent: rental providers (iTunes, Amazon, etc.)
        - buy: purchase providers
    """
    empty_result: Dict[str, List] = {"streaming": [], "rent": [], "buy": []}

    # Check cache first
    cache_key = (tmdb_id, media_type)
    if cache_key in _watch_provider_cache:
        cached_time, cached_result = _watch_provider_cache[cache_key]
        if time.time() - cached_time < WATCH_PROVIDER_CACHE_TTL:
            return cached_result

    try:
        url = f"https://api.themoviedb.org/3/{'movie' if media_type == 'movie' else 'tv'}/{tmdb_id}/watch/providers"
        params = {"api_key": tmdb_api_key}
        response = requests.get(url, params=params, timeout=TMDB_REQUEST_TIMEOUT)

        if response.status_code != 200:
            return empty_result

        data = response.json()
        us_providers = data.get("results", {}).get("US", {})

        def extract_providers(provider_list, provider_map):
            """Extract provider names from TMDB provider list."""
            services = []
            for provider in provider_list:
                provider_id = provider.get("provider_id")
                if provider_id in provider_map:
                    service_name = provider_map[provider_id]
                    if service_name not in services:
                        services.append(service_name)
            return services

        result = {
            "streaming": extract_providers(us_providers.get("flatrate", []), TMDB_STREAMING_PROVIDERS),
            "rent": extract_providers(us_providers.get("rent", []), TMDB_RENTAL_PROVIDERS),
            "buy": extract_providers(us_providers.get("buy", []), TMDB_RENTAL_PROVIDERS),
        }
        # Cache successful result
        _watch_provider_cache[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        logger.debug(f"Error fetching watch providers for TMDB {tmdb_id}: {e}")
        return empty_result


def get_collection_details(tmdb_api_key: str, collection_id: int) -> Optional[Dict]:
    """
    Fetch all movies in a TMDB collection.

    Args:
        tmdb_api_key: TMDB API key
        collection_id: TMDB collection ID

    Returns:
        Dict with collection name and list of movies, or None if failed
    """
    try:
        url = f"https://api.themoviedb.org/3/collection/{collection_id}"
        params = {"api_key": tmdb_api_key}
        response = requests.get(url, params=params, timeout=TMDB_REQUEST_TIMEOUT)

        if response.status_code != 200:
            return None

        data = response.json()
        movies = []
        for part in data.get("parts", []):
            movies.append(
                {
                    "tmdb_id": part["id"],
                    "title": part.get("title", ""),
                    "year": (part.get("release_date") or "")[:4],
                    "release_date": part.get("release_date", ""),
                    "genre_ids": part.get("genre_ids", []),  # Include genres to avoid extra API call
                }
            )

        # Sort by release date
        movies.sort(key=lambda x: x.get("release_date", ""))

        return {
            "collection_id": collection_id,
            "collection_name": data.get("name", "Unknown Collection"),
            "movies": movies,
        }
    except (requests.RequestException, KeyError) as e:
        logger.debug(f"Error fetching collection {collection_id}: {e}")
        return None


# Alias for backwards compatibility with tests
TV_MOVIE_GENRE_ID = TMDB_TV_MOVIE_GENRE_ID

# Huntarr cache versions
SEQUEL_HUNTARR_CACHE_VERSION = 4  # v4: Added rent_services and buy_services

# Backwards compatibility alias
HUNTARR_CACHE_VERSION = SEQUEL_HUNTARR_CACHE_VERSION


def load_huntarr_cache(cache_path: str, stale_days: int = 7) -> Dict:
    """Load Huntarr cache from disk."""
    cache = load_json_cache(cache_path)
    if cache and cache.get("version") == HUNTARR_CACHE_VERSION:
        # Check staleness
        cached_at = cache.get("cached_at", 0)
        age_days = (time.time() - cached_at) / 86400
        if age_days < stale_days:
            return cache
    return {}


def save_huntarr_cache(cache_path: str, cache: Dict) -> None:
    """Save Huntarr cache to disk."""
    cache["version"] = HUNTARR_CACHE_VERSION
    cache["cached_at"] = time.time()
    if not save_json_cache(cache_path, cache):
        log_warning("Could not save Huntarr cache")


def find_missing_sequels(
    tmdb_api_key: str, plex: Any, library_name: str, tv_library_name: str, user_services: List[str], stale_days: int = 7
) -> List[Dict]:
    """
    Find missing movies from collections user has started.

    Scans library for movies with collection IDs, fetches full collections
    from TMDB, and identifies gaps (movies not in library). Also checks
    TV library for TV movie specials that might be stored as episodes.

    Args:
        tmdb_api_key: TMDB API key
        plex: PlexServer instance
        library_name: Name of movie library
        tv_library_name: Name of TV library (for checking TV specials)
        user_services: List of user's streaming services
        stale_days: Days before cache is considered stale

    Returns:
        List of missing movie dicts with streaming info
    """
    from utils.display import show_progress

    # Cache paths
    project_root = get_project_root()
    cache_path = os.path.join(project_root, "cache", "huntarr_cache.json")

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
                    tmdb_id = int(guid.id.split("tmdb://")[1])
                    library_tmdb_ids.add(tmdb_id)
                    break
                except (ValueError, IndexError):
                    continue

    # Load cache and check if library changed
    cache = load_huntarr_cache(cache_path, stale_days)
    cached_library_ids = set(cache.get("library_tmdb_ids", []))

    if cached_library_ids == library_tmdb_ids and cache.get("missing_sequels"):
        print(f"{GREEN}  Using cached Sequel Huntarr data ({len(cache['missing_sequels'])} missing movies){RESET}")
        # Update streaming services for current user
        missing = cache["missing_sequels"]
        for item in missing:
            item["on_user_services"] = [s for s in item.get("streaming_services", []) if s in user_services]
        return missing

    total_items = len(items)
    print(f"{CYAN}  Scanning {total_items} movies for collections...{RESET}")

    # Step 1: Find all movies with collection IDs and track which are owned
    collection_owned: Dict[int, Set[int]] = {}  # collection_id -> set of owned tmdb_ids

    # Use cached movie->collection mapping if available
    movie_collections = cache.get("movie_collections", {})
    movies_to_fetch = []

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

        # Check cache first
        tmdb_id_str = str(tmdb_id)
        if tmdb_id_str in movie_collections:
            coll_id = movie_collections[tmdb_id_str]
            if coll_id:
                if coll_id not in collection_owned:
                    collection_owned[coll_id] = set()
                collection_owned[coll_id].add(tmdb_id)
        else:
            movies_to_fetch.append(tmdb_id)

    show_progress("  Scanning library", total_items, total_items)

    # Fetch collection IDs for uncached movies
    if movies_to_fetch:
        print(f"{CYAN}  Fetching collection data for {len(movies_to_fetch)} new movies...{RESET}")
        for i, tmdb_id in enumerate(movies_to_fetch):
            if i % 10 == 0:
                show_progress("  Fetching collections", i + 1, len(movies_to_fetch))

            try:
                url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
                params = {"api_key": tmdb_api_key}
                response = requests.get(url, params=params, timeout=TMDB_REQUEST_TIMEOUT)

                if response.status_code == 200:
                    data = response.json()
                    collection = data.get("belongs_to_collection")
                    if collection:
                        coll_id = collection["id"]
                        movie_collections[str(tmdb_id)] = coll_id
                        if coll_id not in collection_owned:
                            collection_owned[coll_id] = set()
                        collection_owned[coll_id].add(tmdb_id)
                    else:
                        movie_collections[str(tmdb_id)] = None
            except (requests.RequestException, KeyError) as e:
                logger.debug(f"Failed to fetch collection for TMDB ID {tmdb_id}: {e}")
                continue

        show_progress("  Fetching collections", len(movies_to_fetch), len(movies_to_fetch))

    print(f"{GREEN}  Found {len(collection_owned)} collections{RESET}")

    # Step 2: Fetch full collection details and find gaps
    missing_sequels = []
    collections_with_gaps = 0
    total_collections = len(collection_owned)

    # Use cached collection details
    collection_details_cache = cache.get("collection_details", {})

    print(f"{CYAN}  Checking collections for missing movies...{RESET}")

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

        # Filter to only released movies (must have a year)
        released_movies = [m for m in all_movies if m.get("year")]
        total_count = len(released_movies)

        # Skip collections with no released movies
        if total_count == 0:
            continue

        # Skip if user owns all released movies in collection
        if all(m["tmdb_id"] in library_tmdb_ids for m in released_movies):
            continue

        collections_with_gaps += 1

        # Count owned released movies
        owned_released = sum(1 for m in released_movies if m["tmdb_id"] in library_tmdb_ids)

        # Find missing movies (only released ones)
        for movie in released_movies:
            if movie["tmdb_id"] not in library_tmdb_ids:
                # Get streaming/rent/buy availability
                providers = get_watch_providers(tmdb_api_key, movie["tmdb_id"], "movie")
                streaming = providers.get("streaming", [])

                # Check if this is a TV movie (special) - genre ID 10770
                # Use genre_ids from collection details to avoid extra API call
                genre_ids = movie.get("genre_ids", [])
                is_tv_movie = TV_MOVIE_GENRE_ID in genre_ids
                is_animated = TMDB_ANIMATION_GENRE_ID in genre_ids

                missing_sequels.append(
                    {
                        "tmdb_id": movie["tmdb_id"],
                        "title": movie["title"],
                        "year": movie["year"],
                        "collection_id": coll_id,
                        "collection_name": coll_name,
                        "owned_count": owned_released,
                        "total_count": total_count,
                        "streaming_services": streaming,
                        "rent_services": providers.get("rent", []),
                        "buy_services": providers.get("buy", []),
                        "on_user_services": [s for s in streaming if s in user_services],
                        "release_date": movie.get("release_date", ""),
                        "is_tv_movie": is_tv_movie,
                        "is_animated": is_animated,
                    }
                )

    show_progress("  Checking collections", total_collections, total_collections)

    # Sort by collection name, then release date within collection
    missing_sequels.sort(key=lambda x: (x["collection_name"], x.get("release_date", "")))

    # For TV movies (specials), also check TV library - they might be stored as episodes
    # Note: TMDB often has TV specials as both "movies" and "TV episodes" with different IDs
    # So we use title matching in addition to ID matching
    tv_movies = [m for m in missing_sequels if m.get("is_tv_movie")]
    if tv_movies and tv_library_name:
        print(f"{CYAN}  Checking {len(tv_movies)} TV specials against TV library...{RESET}")

        # Build lookup maps: normalized title -> TMDB movie ID
        def normalize_title(title: str) -> str:
            """Normalize title for comparison (lowercase, strip punctuation)"""
            return re.sub(r"[^\w\s]", "", title.lower()).strip()

        tv_movie_ids = {m["tmdb_id"] for m in tv_movies}
        found_tmdb_ids = set()

        try:
            tv_library = plex.library.section(tv_library_name)

            # Use Plex search for each TV special title - much faster than scanning all episodes
            for i, tv_movie in enumerate(tv_movies):
                if i % 5 == 0:
                    show_progress("  Searching TV library", i + 1, len(tv_movies))

                title = tv_movie["title"]
                title_norm = normalize_title(title)

                # Search for episodes matching the title
                try:
                    # Extract key words for search (first few significant words)
                    search_term = " ".join(title.split()[:3])
                    results = tv_library.search(search_term, libtype="episode")

                    for episode in results:
                        # Check by TMDB ID
                        for guid in episode.guids:
                            if "tmdb://" in guid.id:
                                try:
                                    tmdb_id = int(guid.id.split("tmdb://")[1])
                                    if tmdb_id in tv_movie_ids:
                                        found_tmdb_ids.add(tmdb_id)
                                except (ValueError, IndexError):
                                    pass

                        # Check by normalized title match
                        ep_title_norm = normalize_title(episode.title)
                        if ep_title_norm == title_norm:
                            found_tmdb_ids.add(tv_movie["tmdb_id"])
                        # Also check show name + episode title (e.g., "Phineas and Ferb" + "Mission Marvel")
                        elif hasattr(episode, "grandparentTitle"):
                            combined_norm = normalize_title(f"{episode.grandparentTitle} {episode.title}")
                            if combined_norm == title_norm:
                                found_tmdb_ids.add(tv_movie["tmdb_id"])
                            # Also check if episode title is a suffix of movie title
                            elif title_norm.endswith(ep_title_norm) and len(ep_title_norm) > 5:
                                found_tmdb_ids.add(tv_movie["tmdb_id"])
                except Exception:
                    pass  # Search failed for this title, continue

            show_progress("  Searching TV library", len(tv_movies), len(tv_movies))
        except Exception as e:
            log_warning(f"Could not scan TV library for specials: {e}")

        # Remove TV movies found in TV library
        if found_tmdb_ids:
            before_count = len(missing_sequels)
            missing_sequels = [
                m for m in missing_sequels if not (m.get("is_tv_movie") and m["tmdb_id"] in found_tmdb_ids)
            ]
            removed = before_count - len(missing_sequels)
            if removed > 0:
                print(f"{GREEN}  Removed {removed} TV specials found in TV library{RESET}")

    # Save cache
    save_huntarr_cache(
        cache_path,
        {
            "library_tmdb_ids": list(library_tmdb_ids),
            "movie_collections": movie_collections,
            "collection_details": collection_details_cache,
            "missing_sequels": missing_sequels,
        },
    )

    print(
        f"{GREEN}  Found {len(missing_sequels)} missing movies across "
        f"{collections_with_gaps} incomplete collections{RESET}"
    )
    return missing_sequels
