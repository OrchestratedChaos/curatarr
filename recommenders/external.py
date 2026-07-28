#!/usr/bin/env python3
"""
Generate external recommendations - content NOT in your Plex library
Creates per-user markdown watchlists that update daily and auto-remove acquired items
"""

import logging
import os
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
import traceback
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
import urllib3
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer

# Import shared utilities - same as internal recommenders
# Import export functions
# Import output generation
from recommenders.external_render import SERVICE_DISPLAY_NAMES, generate_combined_html, generate_markdown
from recommenders.external_sync import (
    export_to_mdblist,
    export_to_radarr,
    export_to_simkl,
    export_to_sonarr,
    export_to_trakt,
    get_imdb_id,
    sync_watch_history_to_trakt,
)
from recommenders.horizon import (
    HORIZON_HUNTARR_CACHE_VERSION,
    find_horizon_movies,
    get_movie_status,
    load_horizon_cache,
    save_horizon_cache,
)
from recommenders.huntarr import (
    HUNTARR_CACHE_VERSION,
    TMDB_PROVIDERS,
    TV_MOVIE_GENRE_ID,
    _watch_provider_cache,
    find_missing_sequels,
    get_collection_details,
    get_watch_providers,
    load_huntarr_cache,
    save_huntarr_cache,
)
from recommenders.streaming import categorize_by_streaming_service
from utils import (
    CYAN,
    GREEN,
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_TV,
    RESET,
    TMDB_ANIMATION_GENRE_ID,
    TMDB_RATE_LIMIT_DELAY,
    TMDB_REQUEST_TIMEOUT,
    YELLOW,
    calculate_similarity_score,
    clickable_link,
    enhance_profile_with_trakt,
    fetch_tmdb_details_for_profile,
    fetch_watch_history_with_tmdb,
    get_authenticated_trakt_client,
    get_libraries_for_media_type,
    get_plex_account_ids,
    get_project_root,
    get_streaming_services_for_user,
    get_tmdb_config,
    get_tmdb_id_from_imdb,
    get_trakt_discovery_candidates,
    load_config,
    load_json_cache,
    log_error,
    log_warning,
    normalize_genre,
    normalize_user_profile,
    record_recommender_run,
    record_run_status,
    record_unhandled_error,
    save_json_cache,
    smart_open_html,
)

# Module-level logger
logger = logging.getLogger("curatarr")

# Names imported above but never referenced in this module - re-exported
# for other modules to import FROM here (not from their own original
# definition module), so ruff would otherwise flag them F401
# ("imported but unused"). Confirmed live by grepping every caller,
# including tests, before listing here - see CHANGELOG.md's [2.10.14]
# entry on a prior ruff --fix pass that deleted six of these outright
# and broke the suite:
#   - sync_watch_history_to_trakt: trakt_sync.py's entry point imports
#     it from here (recommenders.external), not recommenders.external_sync.
#   - SERVICE_DISPLAY_NAMES, get_tmdb_id_from_imdb: imported from here
#     by tests/test_external.py.
#   - TMDB_ANIMATION_GENRE_ID, TMDB_PROVIDERS, TV_MOVIE_GENRE_ID,
#     HUNTARR_CACHE_VERSION, save_huntarr_cache, _watch_provider_cache,
#     get_collection_details, load_huntarr_cache: find_missing_sequels
#     moved to recommenders/huntarr.py (PR2 external.py decomposition -
#     see CHANGELOG). get_collection_details/load_huntarr_cache were
#     still called from find_horizon_movies (this module) until PR2 step
#     2 moved that too - now nothing in this module calls them, but
#     tests/test_external.py still imports them from here.
#   - HORIZON_HUNTARR_CACHE_VERSION, load_horizon_cache, save_horizon_cache,
#     get_movie_status: same story, find_horizon_movies moved to
#     recommenders/horizon.py (PR2 step 2), tests still import these from here.
#   - get_watch_providers: moved to recommenders/huntarr.py along with
#     find_missing_sequels (PR2 step 1); still called from this module's
#     categorize_by_streaming_service until PR2 step 3 moved that to
#     recommenders/streaming.py too - now nothing in this module calls it,
#     but tests/test_external.py still imports it from here.
__all__ = [
    "sync_watch_history_to_trakt",
    "SERVICE_DISPLAY_NAMES",
    "get_tmdb_id_from_imdb",
    "TMDB_ANIMATION_GENRE_ID",
    "TMDB_PROVIDERS",
    "TV_MOVIE_GENRE_ID",
    "HUNTARR_CACHE_VERSION",
    "save_huntarr_cache",
    "_watch_provider_cache",
    "get_collection_details",
    "load_huntarr_cache",
    "get_watch_providers",
    "HORIZON_HUNTARR_CACHE_VERSION",
    "load_horizon_cache",
    "save_horizon_cache",
    "get_movie_status",
]

# TMDB Genre ID mappings
TMDB_MOVIE_GENRES = {
    28: "Action",
    12: "Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    14: "Fantasy",
    36: "History",
    27: "Horror",
    10402: "Music",
    9648: "Mystery",
    10749: "Romance",
    878: "Science Fiction",
    10770: "TV Movie",
    53: "Thriller",
    10752: "War",
    37: "Western",
}

TMDB_TV_GENRES = {
    10759: "Action & Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    10762: "Kids",
    9648: "Mystery",
    10763: "News",
    10764: "Reality",
    10765: "Sci-Fi & Fantasy",
    10766: "Soap",
    10767: "Talk",
    10768: "War & Politics",
    37: "Western",
}

# Reverse TMDB genre mappings (name to ID) for Discover API
TMDB_MOVIE_GENRE_IDS = {v.lower(): k for k, v in TMDB_MOVIE_GENRES.items()}
TMDB_TV_GENRE_IDS = {v.lower(): k for k, v in TMDB_TV_GENRES.items()}

# Discovery thresholds - cast a wide net to find candidates
DISCOVER_MIN_RATING = 5.5  # Cast wider net, quality filtering happens during scoring
DISCOVER_MIN_VOTES = 50  # Lower barrier for candidates, OUTPUT_MIN_VOTES filters final list
MAX_CANDIDATES = 1500  # Larger pool for users with big libraries
DISCOVER_RESULTS_PER_GENRE = 60  # Top N results per genre search (more to offset library filtering)
DISCOVER_TOP_KEYWORDS = 10  # Number of top keywords to search
DISCOVER_RESULTS_PER_KEYWORD = 25  # Top N results per keyword search

# Output thresholds - match score is king, rating is just tiebreaker
OUTPUT_MIN_SCORE = 0.65  # 65%+ match required - this is what matters
OUTPUT_MIN_VOTES = 50  # Filters garbage TMDB entries, profile score is quality signal

# Iterative discovery settings
MAX_DISCOVERY_ITERATIONS = 8  # How many discovery passes before giving up
THRESHOLD_FLOOR = 0.35  # Minimum threshold for last-ditch iteration
THIN_PROFILE_THRESHOLD = 40  # Less than 40 items = use reduced iterations

# Legacy aliases for cache filtering (votes only, no rating gate)
MIN_RATING = 0.0  # Don't filter by rating in cache
MIN_VOTE_COUNT = OUTPUT_MIN_VOTES
SCORE_CHANGE_THRESHOLD = 0.01  # Minimum score change to log during updates
PROGRESS_UPDATE_FREQUENCY = 50  # Show progress every N items

# Default weights (specificity-first approach - same as internal recommenders)
# Director/language reduced - most people don't care about director, language data unreliable
DEFAULT_WEIGHTS = {
    "genre": 0.25,
    "director": 0.05,  # movies - low weight
    "studio": 0.10,  # TV shows
    "actor": 0.20,
    "keyword": 0.50,  # Primary driver - most specific signal
    "language": 0.0,  # Disabled - data unreliable
}

# Keyword ID cache (keyword names rarely change IDs)
_keyword_id_cache: Dict[str, Optional[int]] = {}  # keyword_name -> keyword_id (None if not found)


def discover_candidates_by_profile(
    tmdb_api_key: str,
    user_profile: Dict,
    library_data: Dict,
    media_type: str = "movie",
    max_candidates: int = 500,
    iteration: int = 0,
    exclude_ids: Optional[Set[int]] = None,
    top_scored_items: Optional[List[Dict]] = None,
    language_filter: Optional[str] = None,
) -> Dict[int, Dict]:
    """
    Discover candidates using TMDB Discover API based on user profile.
    Searches by top genres and keywords for higher quality matches.

    Iteration expansion strategy:
    - Iteration 0: Top 5 genres, top 10 keywords, page 1
    - Iteration 1: Page 2, genres 6-10, keywords 11-20
    - Iteration 2+: Similar-to queries for top scored items
    - Iteration 3: Page 3, genre combinations
    - Iteration 4: Keywords 21-40
    """
    if exclude_ids is None:
        exclude_ids = set()
    if top_scored_items is None:
        top_scored_items = []

    # Calculate page and ranges based on iteration
    page = iteration + 1
    genre_start = iteration * 5
    genre_end = genre_start + 5
    keyword_start = iteration * 10
    keyword_end = keyword_start + 10

    if iteration == 0:
        print("  Discovering candidates via TMDB Discover API...")
    else:
        print(f"  Discovery iteration {iteration + 1}: expanding search...")

    candidates: Dict[int, Dict] = {}  # tmdb_id -> basic info
    media = "movie" if media_type == "movie" else "tv"

    # Get genres for this iteration's range. Coerced to Counter (#273
    # PR3) rather than assumed - user_profile can come from
    # load_user_profile_from_cache()/_build_profile_via_recommender()
    # (already Counter-valued) or, in principle, a caller handing over
    # plain-dict-shaped data directly (e.g. straight off a JSON cache
    # read), which .most_common() can't be called on.
    genres_counter = user_profile.get("genres")
    if not isinstance(genres_counter, Counter):
        genres_counter = Counter(genres_counter or {})
    all_genres = list(genres_counter.most_common(20))
    top_genres = all_genres[genre_start:genre_end]
    genre_id_map = TMDB_MOVIE_GENRE_IDS if media_type == "movie" else TMDB_TV_GENRE_IDS

    # Get keywords for this iteration's range. Coerced to Counter (#273
    # PR3) rather than assumed - every real caller (load_user_profile_
    # from_cache()/_build_profile_via_recommender() below) always uses
    # 'keywords' as the key (never the raw watched_data_counters
    # 'tmdb_keywords' storage name - both translate that before
    # returning), so no dual-key fallback is needed here (#273 PR4
    # removed one that was never actually reachable - see CHANGELOG).
    keywords_counter = user_profile.get("keywords")
    if not isinstance(keywords_counter, Counter):
        keywords_counter = Counter(keywords_counter or {})
    all_keywords = list(keywords_counter.most_common(40))
    top_keywords = all_keywords[keyword_start:keyword_end]

    # Search by genres for this iteration
    for genre_name, _ in top_genres:
        if len(candidates) >= max_candidates:
            break

        # Normalize and find genre ID
        normalized = normalize_genre(genre_name).lower()
        genre_id = genre_id_map.get(normalized)

        if not genre_id:
            continue

        try:
            # Use Discover API with quality filters
            url = f"https://api.themoviedb.org/3/discover/{media}"
            params: Dict[str, Any] = {
                "api_key": tmdb_api_key,
                "with_genres": genre_id,
                "vote_average.gte": DISCOVER_MIN_RATING,
                "vote_count.gte": DISCOVER_MIN_VOTES,
                "sort_by": "vote_average.desc",
                "page": page,
            }
            if language_filter:
                params["with_original_language"] = language_filter
            response = requests.get(url, params=params, timeout=TMDB_REQUEST_TIMEOUT)

            if response.status_code == 200:
                results = response.json().get("results", [])
                for item in results[:DISCOVER_RESULTS_PER_GENRE]:
                    tmdb_id = item["id"]
                    title = item.get("title") or item.get("name")
                    year = (item.get("release_date") or item.get("first_air_date", ""))[:4]

                    # Skip if already seen, in library, or excluded
                    if tmdb_id in candidates or tmdb_id in exclude_ids:
                        continue
                    if is_in_library(tmdb_id, title, year, library_data):
                        continue

                    candidates[tmdb_id] = {
                        "tmdb_id": tmdb_id,
                        "title": title,
                        "year": year,
                        "rating": item.get("vote_average", 0),
                        "vote_count": item.get("vote_count", 0),
                    }

        except (requests.RequestException, KeyError) as e:
            log_warning(f"Error discovering by genre {genre_name}: {e}")

    genre_count = len(candidates)

    # Search by keywords for this iteration's range
    for keyword, _ in top_keywords:
        if len(candidates) >= max_candidates:
            break

        try:
            # Check keyword ID cache first
            keyword_lower = keyword.lower()
            if keyword_lower in _keyword_id_cache:
                kw_id = _keyword_id_cache[keyword_lower]
            else:
                # Search for keyword ID
                url = "https://api.themoviedb.org/3/search/keyword"
                response = requests.get(
                    url, params={"api_key": tmdb_api_key, "query": keyword}, timeout=TMDB_REQUEST_TIMEOUT
                )

                kw_id = None
                if response.status_code == 200:
                    kw_results = response.json().get("results", [])
                    if kw_results:
                        kw_id = kw_results[0]["id"]
                # Cache result (including None for not found)
                _keyword_id_cache[keyword_lower] = kw_id

            if kw_id:
                # Discover by keyword
                url = f"https://api.themoviedb.org/3/discover/{media}"
                params = {
                    "api_key": tmdb_api_key,
                    "with_keywords": kw_id,
                    "vote_average.gte": DISCOVER_MIN_RATING,
                    "vote_count.gte": DISCOVER_MIN_VOTES,
                    "sort_by": "vote_average.desc",
                    "page": page,
                }
                if language_filter:
                    params["with_original_language"] = language_filter
                response = requests.get(url, params=params, timeout=TMDB_REQUEST_TIMEOUT)

                if response.status_code == 200:
                    results = response.json().get("results", [])
                    for item in results[:DISCOVER_RESULTS_PER_KEYWORD]:
                        tmdb_id = item["id"]
                        title = item.get("title") or item.get("name")
                        year = (item.get("release_date") or item.get("first_air_date", ""))[:4]

                        if tmdb_id in candidates or tmdb_id in exclude_ids:
                            continue
                        if is_in_library(tmdb_id, title, year, library_data):
                            continue

                        candidates[tmdb_id] = {
                            "tmdb_id": tmdb_id,
                            "title": title,
                            "year": year,
                            "rating": item.get("vote_average", 0),
                            "vote_count": item.get("vote_count", 0),
                        }

        except (requests.RequestException, KeyError) as e:
            log_warning(f"Error discovering by keyword {keyword}: {e}")

    keyword_count = len(candidates) - genre_count

    # On iteration 2+, add similar-to queries for top scored items
    similar_count = 0
    if iteration >= 2 and top_scored_items:
        for item in top_scored_items[:5]:  # Top 5 high-scorers
            similar = fetch_similar_from_tmdb(
                tmdb_api_key, item["tmdb_id"], media_type, library_data, exclude_ids.union(set(candidates.keys()))
            )
            for sim_id, sim_item in similar.items():
                if sim_id not in candidates and sim_id not in exclude_ids:
                    candidates[sim_id] = sim_item
                    similar_count += 1

    print(
        f"    Iteration {iteration + 1}: {genre_count} from genres, "
        f"{keyword_count} from keywords, {similar_count} from similar"
    )
    return candidates


def is_thin_profile(user_profile: Dict) -> bool:
    """Check if profile has too few items for reliable matching."""
    # Coerced to Counter (#273 PR3) - see discover_popular_by_genre's own
    # identical note above; .values() happens to work on a plain dict
    # too, but coercing here keeps this function's input contract
    # consistent with every other user_profile consumer in this file.
    genres = user_profile.get("genres")
    if not isinstance(genres, Counter):
        genres = Counter(genres or {})
    total_items = sum(genres.values())
    return total_items < THIN_PROFILE_THRESHOLD


def discover_popular_by_genre(
    tmdb_api_key: str,
    top_genres: List[str],
    library_data: Dict,
    media_type: str = "movie",
    limit: int = 50,
    language_filter: Optional[str] = None,
) -> List[Dict]:
    """
    Fallback discovery for thin profiles - fetch popular content by genre.
    Much faster than profile-based scoring since we skip detailed matching.

    Args:
        tmdb_api_key: TMDB API key
        top_genres: List of genre names from user's sparse profile
        library_data: Existing library to filter out
        media_type: 'movie' or 'show'
        limit: Max items to return
        language_filter: ISO 639-1 language code (e.g., 'en') to filter by

    Returns:
        List of recommendation dicts
    """
    media = "movie" if media_type == "movie" else "tv"
    genre_id_map = TMDB_MOVIE_GENRE_IDS if media_type == "movie" else TMDB_TV_GENRE_IDS
    library_ids = set(library_data.keys()) if library_data else set()

    recommendations: List[Dict] = []
    seen_ids = set()

    # Fetch popular content for each genre
    for genre_name in top_genres:
        if len(recommendations) >= limit:
            break

        genre_id = genre_id_map.get(genre_name.lower())
        if not genre_id:
            continue

        try:
            # Use TMDB Discover sorted by vote_average (quality) with minimum votes
            url = f"https://api.themoviedb.org/3/discover/{media}"
            params: Dict[str, Any] = {
                "api_key": tmdb_api_key,
                "with_genres": genre_id,
                "sort_by": "vote_average.desc",
                "vote_count.gte": 500,  # Ensure popular, well-rated
                "vote_average.gte": 7.0,
                "page": 1,
            }
            if language_filter:
                params["with_original_language"] = language_filter
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            for item in data.get("results", []):
                tmdb_id = item.get("id")
                if not tmdb_id or tmdb_id in seen_ids or tmdb_id in library_ids:
                    continue

                seen_ids.add(tmdb_id)
                title = item.get("title") if media_type == "movie" else item.get("name")
                year_str = item.get("release_date" if media_type == "movie" else "first_air_date", "")
                year = int(year_str[:4]) if year_str and len(year_str) >= 4 else 0

                recommendations.append(
                    {
                        "tmdb_id": tmdb_id,
                        "title": title,
                        "year": year,
                        "rating": item.get("vote_average", 0),
                        "vote_count": item.get("vote_count", 0),
                        "score": 0.5,  # Neutral score for popularity-based recs
                        "overview": item.get("overview", ""),
                        "genres": [genre_name],
                        "genre_ids": item.get("genre_ids", []),
                    }
                )

                if len(recommendations) >= limit:
                    break

            time.sleep(TMDB_RATE_LIMIT_DELAY)
        except Exception as e:
            log_warning(f"Genre discover failed for {genre_name}: {e}")
            continue

    print(f"  {GREEN}Found {len(recommendations)} popular items in user's genres{RESET}")
    return recommendations[:limit]


def load_user_profile_from_cache(config: Dict, username: str, media_type: str = "movie") -> Optional[Dict]:
    """
    Load user profile from the watched cache (pre-computed by internal recommenders).
    This is MUCH faster than rebuilding from API calls.

    Returns:
        dict: Weighted counters for genres, actors, directors/studios, keywords, languages
        None: If cache not found or invalid
    """
    cache_dir = config.get("cache_dir", "cache")

    # Cache file naming matches internal recommenders
    if media_type == "movie":
        cache_file = os.path.join(cache_dir, f"watched_cache_plex_{username}.json")
    else:
        cache_file = os.path.join(cache_dir, f"tv_watched_cache_plex_{username}.json")

    if not os.path.exists(cache_file):
        print(f"  No watched cache found for {username} ({media_type}), will build from scratch")
        return None

    try:
        with open(cache_file, "r") as f:
            cache_data = json.load(f)

        wdc = cache_data.get("watched_data_counters", {})
        if not wdc:
            print(f"  Empty watched_data_counters in cache for {username}")
            return None

        # Convert to Counter format expected by scoring
        # Note: cache uses 'tmdb_keywords' for keywords
        profile = {
            "genres": Counter(wdc.get("genres", {})),
            "directors": Counter(wdc.get("directors", {})),
            "studios": Counter(wdc.get("studios", {})),
            "actors": Counter(wdc.get("actors", {})),
            "keywords": Counter(wdc.get("tmdb_keywords", {})),
            "languages": Counter(wdc.get("languages", {})),
            "tmdb_ids": set(wdc.get("tmdb_ids", [])),
        }

        watched_count = cache_data.get("watched_count", len(profile["genres"]))
        print(
            f"  {GREEN}Loaded {media_type} profile from cache: {watched_count} watched, "
            f"{len(profile['keywords'])} keywords{RESET}"
        )

        return profile

    except Exception as e:
        log_warning(f"Error loading cache for {username}: {e}")
        return None


def _build_profile_via_recommender(username: str, media_type: str) -> Dict:
    """Replaces the deleted build_user_profile() (#273 PR3).

    build_user_profile() had a fatal, unfixable-in-place bug (#3): its
    `username` parameter had zero effect on the output - it always
    scanned whatever `plex` connection the caller already had (the one
    shared admin-token connection every caller in this file uses),
    never username's own. Rather than maintaining a second,
    independent, username-aware Plex-scanning implementation here (a
    third copy of logic recommenders/movie.py's and recommenders/tv.py's
    own watched-data builders already get right, including #273 PR1's
    per-user token fix), this constructs the real
    PlexMovieRecommender/PlexTVRecommender for `username` directly - the
    same "shared path" those internal recommenders already use. That
    also means this benefits from every one of #273 PR1's/PR2's fixes
    automatically, and - unlike build_user_profile() - persists a real,
    correctly-weighted watched-cache file to disk as a side effect of
    construction, so load_user_profile_from_cache() finds it on the very
    next call for this user instead of paying this same slow full-Plex-
    scan cost forever.

    Constructs its own config_path via get_project_root() (matching
    _main_impl()'s own resolution) rather than threading one through
    every caller's signature (process_user()/process_user_movie_library()/
    process_user_tv_library()/_pu_build_profiles() never otherwise need
    one - callers here already hold an in-memory `config`/`plex`, which
    this intentionally does NOT reuse; a second Plex connection is the
    cost of reusing the real, correct recommender construction instead
    of a bespoke scan, and this is only ever reached when no cache
    exists yet for this user/media type - not the common case).

    Returns the profile in the exact same shape
    load_user_profile_from_cache() returns (Counter-valued, 'keywords'
    not 'tmdb_keywords') - the ONE shape every real consumer of a
    profile dict in this file (is_thin_profile(),
    discover_popular_by_genre(), calculate_similarity_score()) expects;
    #273 PR4 removed the dual-key ('keywords' or 'tmdb_keywords')
    tolerance that used to sit at each of those call sites, since no
    real caller ever needed it - see CHANGELOG.
    """
    from recommenders.movie import PlexMovieRecommender
    from recommenders.tv import PlexTVRecommender

    config_path = os.path.join(get_project_root(), "config/config.yml")
    recommender_cls = PlexMovieRecommender if media_type == "movie" else PlexTVRecommender

    wdc: Dict[str, Any] = {}
    try:
        recommender = recommender_cls(config_path, single_user=username)
        wdc = recommender.watched_data_counters or {}
    except Exception as e:
        log_warning(f"Could not build {media_type} profile for {username} via recommender: {e}")

    return {
        "genres": Counter(wdc.get("genres", {})),
        "directors": Counter(wdc.get("directors", {})),
        "studios": Counter(wdc.get("studios", {})),
        "actors": Counter(wdc.get("actors", {})),
        "keywords": Counter(wdc.get("tmdb_keywords", {})),
        "languages": Counter(wdc.get("languages", {})),
        "tmdb_ids": set(wdc.get("tmdb_ids", [])),
    }


def get_library_items(plex: Any, library_name: str, media_type: str = "movie") -> Dict[str, Set]:
    """Get all items currently in Plex library - returns dict with tmdb_ids, tvdb_ids, and titles"""
    try:
        library = plex.library.section(library_name)
        items = library.all()

        # Extract multiple identifiers for comparison
        tmdb_ids = set()
        tvdb_ids = set()
        titles = set()  # (title_lower, year) tuples for fallback matching

        for item in items:
            # Add title for fallback matching
            title_lower = item.title.lower().strip()
            year = getattr(item, "year", None)
            titles.add((title_lower, year))

            for guid in item.guids:
                if "tmdb://" in guid.id:
                    try:
                        tmdb_id = guid.id.split("tmdb://")[1]
                        tmdb_ids.add(int(tmdb_id))
                    except (ValueError, IndexError) as e:
                        logger.debug(f"Error parsing TMDB ID from guid {guid.id}: {e}")
                elif "tvdb://" in guid.id:
                    try:
                        tvdb_id = guid.id.split("tvdb://")[1]
                        tvdb_ids.add(int(tvdb_id))
                    except (ValueError, IndexError) as e:
                        logger.debug(f"Error parsing TVDB ID from guid {guid.id}: {e}")

        return {"tmdb_ids": tmdb_ids, "tvdb_ids": tvdb_ids, "titles": titles}
    except Exception as e:
        log_warning(f"Warning: Could not fetch {library_name} library: {e}")
        return {"tmdb_ids": set(), "tvdb_ids": set(), "titles": set()}


def get_movie_genre_ids(tmdb_api_key: str, tmdb_id: int) -> List[int]:
    """
    Fetch genre IDs for a movie from TMDB.

    Args:
        tmdb_api_key: TMDB API key
        tmdb_id: TMDB movie ID

    Returns:
        List of genre IDs, empty list on error
    """
    try:
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
        params = {"api_key": tmdb_api_key}
        response = requests.get(url, params=params, timeout=TMDB_REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            return [g["id"] for g in data.get("genres", [])]
    except (requests.RequestException, KeyError) as e:
        logger.debug(f"Failed to fetch genres for TMDB ID {tmdb_id}: {e}")
    return []


# External recs cache version (Sequel/Horizon Huntarr's own cache-version
# constants moved to recommenders/huntarr.py and recommenders/horizon.py
# respectively, along with the functions that use them - see those
# modules' imports above)
EXTERNAL_RECS_CACHE_VERSION = 1


def fetch_similar_from_tmdb(
    tmdb_api_key: str, tmdb_id: int, media_type: str, library_data: Dict, exclude_ids: Optional[Set[int]] = None
) -> Dict[int, Dict]:
    """
    Fetch similar content from TMDB's recommendations endpoint.
    Used in later iterations to find content similar to high-scoring items.

    Args:
        tmdb_api_key: TMDB API key
        tmdb_id: TMDB ID of the source item
        media_type: 'movie' or 'tv'
        library_data: Dict with tmdb_ids, titles for library filtering
        exclude_ids: Set of TMDB IDs to skip

    Returns:
        Dict mapping tmdb_id -> basic item info
    """
    if exclude_ids is None:
        exclude_ids = set()

    candidates = {}
    media = "movie" if media_type == "movie" else "tv"

    try:
        url = f"https://api.themoviedb.org/3/{media}/{tmdb_id}/similar"
        params: Dict[str, Any] = {"api_key": tmdb_api_key, "page": 1}
        response = requests.get(url, params=params, timeout=TMDB_REQUEST_TIMEOUT)

        if response.status_code == 200:
            results = response.json().get("results", [])
            for item in results[:20]:  # Top 20 similar items
                item_id = item["id"]
                title = item.get("title") or item.get("name")
                year = (item.get("release_date") or item.get("first_air_date", ""))[:4]
                vote_count = item.get("vote_count", 0)

                # Skip if already seen, in library, or low votes
                if item_id in exclude_ids:
                    continue
                if item_id in candidates:
                    continue
                if vote_count < DISCOVER_MIN_VOTES:
                    continue
                if is_in_library(item_id, title, year, library_data):
                    continue

                candidates[item_id] = {
                    "tmdb_id": item_id,
                    "title": title,
                    "year": year,
                    "rating": item.get("vote_average", 0),
                    "vote_count": vote_count,
                }

    except (requests.RequestException, KeyError) as e:
        logger.debug(f"Error fetching similar for TMDB {tmdb_id}: {e}")

    return candidates


def get_genre_distribution(plex: Any, config: Dict, username: str, media_type: str = "movie") -> Tuple[Dict, int]:
    """Calculate genre distribution from user's watch history"""
    try:
        library_name = config["plex"].get("movie_library" if media_type == "movie" else "tv_library")
        library = plex.library.section(library_name)

        genre_counts: Dict[str, int] = {}
        total_items = 0

        # For admin user, check watched items directly
        account = MyPlexAccount(token=config["plex"]["token"])
        if username.lower() == account.username.lower():
            for item in library.all():
                if item.isWatched:
                    total_items += 1
                    for genre in item.genres:
                        genre_counts[genre.tag] = genre_counts.get(genre.tag, 0) + 1

        # Calculate percentages
        genre_distribution = {}
        if total_items > 0:
            for genre, count in genre_counts.items():
                genre_distribution[genre] = count / total_items

        return genre_distribution, total_items
    except Exception as e:
        log_warning(f"  Warning: Could not calculate genre distribution: {e}")
        return {}, 0


def get_user_watch_history(plex: Any, config: Dict, username: str, media_type: str = "movie") -> List[Dict]:
    """Get user's watch history from Plex using shared utility"""
    print(f"Fetching {media_type} watch history for {username}...")

    try:
        # Get library
        library_name = config["plex"].get("movie_library" if media_type == "movie" else "tv_library")
        library = plex.library.section(library_name)

        # Get user's account using flexible matching from shared utils
        account_ids = get_plex_account_ids(config, [username])

        if not account_ids:
            log_warning(f"  Warning: User {username} not found")
            return []

        # Use shared utility to fetch watch history with TMDB IDs
        return fetch_watch_history_with_tmdb(plex, config, account_ids, library, media_type)

    except Exception as e:
        log_warning(f"  Warning: Could not fetch watch history: {e}")
        return []


def is_in_library(tmdb_id: Optional[int], title: Optional[str], year: Optional[str], library_data: Dict) -> bool:
    """Check if item is in library by TMDB ID or title+year"""
    # Check TMDB ID first (fast O(1) lookup)
    if tmdb_id and tmdb_id in library_data.get("tmdb_ids", set()):
        return True

    # Fallback: check by title+year
    if title:
        title_lower = title.lower().strip()
        year_int = int(year) if year and str(year).isdigit() else None
        # Check exact match
        if (title_lower, year_int) in library_data.get("titles", set()):
            return True
        # Check without year - use pre-built title set if available (O(1) vs O(N))
        if "_title_set" not in library_data:
            # Build title-only set once and cache it
            library_data["_title_set"] = {t for t, y in library_data.get("titles", set())}
        if title_lower in library_data["_title_set"]:
            return True

    return False


def find_similar_content_with_profile(
    tmdb_api_key: str,
    user_profile: Dict,
    library_data: Dict,
    media_type: str = "movie",
    limit: int = 50,
    exclude_genres: Optional[List[str]] = None,
    min_relevance_score: float = 0.65,
    config: Optional[Dict] = None,
    exclude_imdb_ids: Optional[Set[str]] = None,
    max_iterations: Optional[int] = None,
    exclude_cached_ids: Optional[Set[int]] = None,
) -> List[Dict]:
    """
    Find similar content NOT in library using profile-based scoring.
    Uses iterative TMDB Discover API + Trakt discovery for candidates + profile-based scoring.

    Iterates discovery until we hit the target count or run out of new candidates.

    Args:
        tmdb_api_key: TMDB API key
        user_profile: Weighted user profile from build_user_profile()
        library_data: Dict with tmdb_ids, titles for library filtering
        media_type: 'movie' or 'tv'
        limit: Max recommendations to return
        exclude_genres: List of genres to exclude
        min_relevance_score: Minimum score threshold (0-1)
        config: Config dict for weights
        exclude_imdb_ids: Set of IMDB IDs to exclude (e.g., Trakt watchlist)
        max_iterations: Override max discovery iterations (None = use config/default)
        exclude_cached_ids: Set of TMDB IDs already in cache (skip scoring)

    Returns:
        List of scored recommendations
    """
    if exclude_imdb_ids is None:
        exclude_imdb_ids = set()
    print(f"{CYAN}Finding external {media_type}s using profile-based scoring...{RESET}")

    if not user_profile or not user_profile.get("genres"):
        print(f"{YELLOW}No user profile data found{RESET}")
        return []

    # Pre-normalize profile once for efficient scoring (avoids rebuilding lowercase dicts per item)
    normalize_user_profile(user_profile)

    # Get iteration settings from config (can be overridden by parameter)
    external_config = config.get("external_recommendations", {}) if config else {}
    if max_iterations is None:
        max_iterations = external_config.get("max_iterations", MAX_DISCOVERY_ITERATIONS)
    min_votes = external_config.get("min_votes", OUTPUT_MIN_VOTES)
    language_filter = external_config.get("language")  # ISO 639-1 code like 'en'

    # Get weights from config or use defaults
    weights = DEFAULT_WEIGHTS
    if config:
        config_weights = config.get("movies" if media_type == "movie" else "tv", {}).get("weights", {})
        if config_weights:
            weights = {
                "genre": config_weights.get("genre", 0.20),
                "director": config_weights.get("director", 0.15),
                "studio": config_weights.get("studio", 0.15),
                "actor": config_weights.get("actor", 0.15),
                "keyword": config_weights.get("keyword", 0.45),
                "language": config_weights.get("language", 0.05),
            }

    # Check for thin profile - reduce iterations instead of skipping personalization entirely
    if is_thin_profile(user_profile):
        profile_size = sum(user_profile.get("genres", Counter()).values())
        print(f"  {CYAN}Thin profile detected ({profile_size} items) - using reduced iterations{RESET}")
        max_iterations = min(max_iterations, 2)  # Quick discovery pass, not zero

    # Show language filter status
    if language_filter:
        print(f"  {CYAN}Language filter: {language_filter.upper()} only{RESET}")

    # Track state across iterations
    quality_recs: List[Dict] = []  # Items meeting quality bar
    seen_ids = set(exclude_cached_ids or set())  # Include cached IDs to skip
    scored_cache = {}  # tmdb_id -> scored item (avoid re-scoring)
    consecutive_zero_iterations = 0  # Track for early termination

    # Get Trakt candidates once (not per-iteration)
    trakt_candidates = {}
    if config:
        project_root = get_project_root()
        cache_dir = os.path.join(project_root, config.get("cache_dir", "cache"))
        library_tmdb_ids = library_data.get("tmdb_ids", set())

        trakt_candidates = get_trakt_discovery_candidates(
            config, media_type, cache_dir, library_tmdb_ids, exclude_imdb_ids
        )

    # Iterative discovery loop
    for iteration in range(max_iterations):
        # Check if we've hit the target
        if len(quality_recs) >= limit:
            print(f"  {GREEN}Target of {limit} reached after {iteration} iteration(s){RESET}")
            break

        # Progressive threshold relaxation: drop 5% each iteration after iter 2 (slower than before)
        if iteration < 2:
            iteration_threshold = min_relevance_score
        elif iteration == max_iterations - 1:  # Last iteration - drop to floor
            iteration_threshold = THRESHOLD_FLOOR
        else:
            # Iterations 2, 3, etc: drop 5% each (was 10%, too aggressive)
            drops = iteration - 1
            iteration_threshold = max(min_relevance_score - (drops * 0.05), THRESHOLD_FLOOR)

        # Discover candidates for this iteration
        candidates = discover_candidates_by_profile(
            tmdb_api_key,
            user_profile,
            library_data,
            media_type,
            max_candidates=MAX_CANDIDATES,
            iteration=iteration,
            exclude_ids=seen_ids,
            top_scored_items=quality_recs[:10],  # Pass top items for similar-to queries
            language_filter=language_filter,
        )

        # On first iteration, also add Trakt candidates
        if iteration == 0 and trakt_candidates:
            trakt_added = 0
            for tmdb_id, item in trakt_candidates.items():
                if tmdb_id not in candidates and tmdb_id not in seen_ids:
                    candidates[tmdb_id] = item
                    trakt_added += 1
            if trakt_added > 0:
                print(f"  Added {trakt_added} candidates from Trakt discovery")

        if not candidates:
            print(f"  No new candidates found in iteration {iteration + 1}")
            break

        # Score new candidates
        new_quality_this_iteration = 0
        candidate_list = [cid for cid in candidates.keys() if cid not in seen_ids]

        if candidate_list:
            total_to_score = len(candidate_list)
            print(f"  Scoring {total_to_score} new candidates...")

            for i, candidate_id in enumerate(candidate_list, 1):
                if i % PROGRESS_UPDATE_FREQUENCY == 0 or i == total_to_score:
                    print(f"\r    Scored {i}/{total_to_score}...", end="", flush=True)

                seen_ids.add(candidate_id)

                # Fetch full details from TMDB
                details = fetch_tmdb_details_for_profile(tmdb_api_key, candidate_id, media_type)
                if not details:
                    continue

                # Check excluded genres
                if exclude_genres:
                    content_genres = [g.lower() for g in details.get("genres", [])]
                    if any(eg.lower() in content_genres for eg in exclude_genres):
                        continue

                # Check if on Trakt watchlist (exclude if IMDB ID matches)
                if exclude_imdb_ids:
                    imdb_id = get_imdb_id(tmdb_api_key, candidate_id, media_type)
                    if imdb_id and imdb_id in exclude_imdb_ids:
                        continue

                # Calculate similarity score
                content_info = {
                    "genres": details.get("genres", []),
                    "directors": details.get("directors", []),
                    "studios": details.get("studios", []),
                    "cast": details.get("cast", []),
                    "language": details.get("language", ""),
                    "keywords": details.get("keywords", []),
                }
                score, _ = calculate_similarity_score(content_info, user_profile, media_type, weights)

                scored_item = {
                    "tmdb_id": candidate_id,
                    "title": details["title"],
                    "year": details["year"],
                    "rating": details["rating"],
                    "vote_count": details.get("vote_count", 0),
                    "score": score,
                    "overview": details.get("overview", ""),
                    "genres": details.get("genres", []),
                    "genre_ids": [],
                    "original_language": details.get("original_language", ""),
                }
                scored_cache[candidate_id] = scored_item

                # Skip if language filter is set and doesn't match
                if language_filter and details.get("original_language", "") != language_filter:
                    continue

                # Check if meets quality bar (threshold relaxes in later iterations)
                if score >= iteration_threshold and scored_item["vote_count"] >= min_votes:
                    quality_recs.append(scored_item)
                    new_quality_this_iteration += 1

            print()  # newline after progress

        # Re-check previously scored items that may now pass the relaxed threshold
        if iteration >= 2:
            quality_rec_ids = {r["tmdb_id"] for r in quality_recs}
            rechecked = 0
            for tmdb_id, scored_item in scored_cache.items():
                if tmdb_id not in quality_rec_ids:
                    if scored_item["score"] >= iteration_threshold and scored_item["vote_count"] >= min_votes:
                        quality_recs.append(scored_item)
                        new_quality_this_iteration += 1
                        rechecked += 1
            if rechecked > 0:
                logger.debug(f"Re-check found {rechecked} items now meeting threshold")

        # Re-sort quality_recs after adding new items
        quality_recs.sort(key=lambda x: (x["score"], x["rating"]), reverse=True)

        print(
            f"  {CYAN}Iteration {iteration + 1} ({iteration_threshold:.0%} threshold): "
            f"{new_quality_this_iteration} new quality items, {len(quality_recs)} total{RESET}"
        )

        # Early termination check - only if we're close to target
        if new_quality_this_iteration == 0:
            consecutive_zero_iterations += 1
            # Only early exit if we're at least 80% to target
            progress_pct = len(quality_recs) / limit if limit > 0 else 1.0
            if consecutive_zero_iterations >= 2 and progress_pct >= 0.8:
                print(
                    f"  {CYAN}Early exit: 2 consecutive iterations with no new matches "
                    f"({len(quality_recs)}/{limit}){RESET}"
                )
                break
        else:
            consecutive_zero_iterations = 0  # Reset on success

    print(f"  {GREEN}{len(quality_recs)} items meet quality bar (>={min_votes} votes){RESET}")

    # Take quality items only - no backfill with low-quality
    final_recs = quality_recs[:limit]

    if final_recs:
        print(f"  Top recommendation: {final_recs[0]['title']} ({final_recs[0]['score']:.1%})")

    return final_recs


def load_cache(display_name: str, media_type: str, lib_id: Optional[str] = None) -> Dict:
    """Load existing recommendations cache, filtering out items below quality thresholds

    Args:
        display_name: User's display name
        media_type: 'movies' or 'shows'
        lib_id: Optional library id (#157 Phase 3). When provided, the cache
            filename is qualified with it so multiple libraries of the same
            media type don't collide. None (default) keeps the legacy
            filename - required for single-library back-compat.
    """
    cache_dir = os.path.join(get_project_root(), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    safe_name = display_name.lower().replace(" ", "_")
    lib_prefix = f"{lib_id}_" if lib_id else ""
    cache_file = os.path.join(cache_dir, f"external_recs_{lib_prefix}{safe_name}_{media_type}.json")

    cache = load_json_cache(cache_file)
    if cache is not None:
        # Check cache version - invalidate old format
        cache_version = cache.get("version", 0)
        if cache_version < EXTERNAL_RECS_CACHE_VERSION:
            print(f"  {YELLOW}External recs cache outdated (v{cache_version}), rebuilding...{RESET}")
            return {}

        items = cache.get("items", {})

        # Add tmdb_id to items that don't have it (backwards compatibility)
        for tmdb_id_str, item in items.items():
            if "tmdb_id" not in item:
                item["tmdb_id"] = int(tmdb_id_str)

        # Filter out items without enough votes (match score filtering happens at output)
        filtered = {}
        removed_count = 0
        for tmdb_id_str, item in items.items():
            vote_count = item.get("vote_count", 0)  # Missing vote_count = needs re-fetch
            if vote_count >= MIN_VOTE_COUNT:
                filtered[tmdb_id_str] = item
            else:
                removed_count += 1

        if removed_count > 0:
            print(f"  Filtered {removed_count} cached items with < {MIN_VOTE_COUNT} votes")

        return filtered
    return {}


def save_cache(display_name: str, media_type: str, cache_data: Dict, lib_id: Optional[str] = None) -> None:
    """Save recommendations cache with version.

    Args:
        display_name: User's display name
        media_type: 'movies' or 'shows'
        cache_data: Cache payload to persist
        lib_id: Optional library id (#157 Phase 3) - see load_cache()
    """
    cache_dir = os.path.join(get_project_root(), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    safe_name = display_name.lower().replace(" ", "_")
    lib_prefix = f"{lib_id}_" if lib_id else ""
    cache_file = os.path.join(cache_dir, f"external_recs_{lib_prefix}{safe_name}_{media_type}.json")

    versioned_cache = {"version": EXTERNAL_RECS_CACHE_VERSION, "items": cache_data}
    if not save_json_cache(cache_file, versioned_cache):
        log_warning(f"Could not save external recs cache for {display_name} ({media_type})")


def _stamp_library_id(categorized: Dict, library_id: Optional[str]) -> None:
    """Stamp library_id provenance onto every item in a categorized dict (#157 Phase 3).

    Args:
        categorized: Dict with 'user_services' (dict of service -> items),
            'other_services' (dict of service -> items), and 'acquire' (list)
        library_id: The source library's id, or None
    """
    for service_items in categorized.get("user_services", {}).values():
        for item in service_items:
            item["library_id"] = library_id
    for service_items in categorized.get("other_services", {}).values():
        for item in service_items:
            item["library_id"] = library_id
    for item in categorized.get("acquire", []):
        item["library_id"] = library_id


def load_ignore_list(display_name: str) -> Set[str]:
    """Load user's manual ignore list"""
    safe_name = display_name.lower().replace(" ", "_")
    project_root = get_project_root()
    ignore_file = os.path.join(project_root, "recommendations", "external", f"{safe_name}_ignore.txt")
    if os.path.exists(ignore_file):
        with open(ignore_file, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def _pu_resolve_context(config, username, movie_library, tv_library):
    """process_user stage: resolve display name/library section names/cache
    lib-ids for this user (see process_user)."""
    user_prefs = config.get("users", {}).get("preferences", {}).get(username, {})
    display_name = user_prefs.get("display_name", username)

    print(f"\n{GREEN}Processing external recommendations for: {display_name}{RESET}")

    # Get current library contents
    movie_library_name = movie_library["section"] if movie_library else config["plex"].get("movie_library", "Movies")
    tv_library_name = tv_library["section"] if tv_library else config["plex"].get("tv_library", "TV Shows")

    # Cache filenames stay legacy (unqualified) unless this install has more
    # than one library of that media type - back-compat for single-library
    # installs (#157 Phase 3), matching the internal recommenders' rule.
    movie_is_multi = bool(movie_library) and len(get_libraries_for_media_type(config, MEDIA_TYPE_MOVIE)) > 1
    tv_is_multi = bool(tv_library) and len(get_libraries_for_media_type(config, MEDIA_TYPE_TV)) > 1
    movie_cache_lib_id = movie_library["id"] if movie_is_multi else None
    tv_cache_lib_id = tv_library["id"] if tv_is_multi else None

    return (user_prefs, display_name, movie_library_name, tv_library_name, movie_cache_lib_id, tv_cache_lib_id)


def _pu_load_libraries_and_caches(
    plex, display_name, movie_library_name, tv_library_name, movie_cache_lib_id, tv_cache_lib_id
):
    """process_user stage: fetch current library contents and load this
    user's existing recommendation caches/ignore list (see process_user)."""
    library_movies = get_library_items(plex, movie_library_name, "movie")
    library_shows = get_library_items(plex, tv_library_name, "show")

    print(f"{CYAN}Library has {len(library_movies['titles'])} movies, {len(library_shows['titles'])} TV shows{RESET}")

    # Load existing cache and ignore list
    movie_cache = load_cache(display_name, "movies", lib_id=movie_cache_lib_id)
    show_cache = load_cache(display_name, "shows", lib_id=tv_cache_lib_id)
    ignore_list = load_ignore_list(display_name)

    return (library_movies, library_shows, movie_cache, show_cache, ignore_list)


def _pu_clean_caches(config, movie_cache, show_cache, library_movies, library_shows, ignore_list):
    """process_user stage: mutate movie_cache/show_cache in place - drop
    items that fail the configured language filter, are now in the
    library (acquired), or are on the user's ignore list (see
    process_user)."""
    # Get language filter from config
    external_config = config.get("external_recommendations", {})
    language_filter = external_config.get("language")

    # Filter cached items by language if filter is set
    # Items without language info are also filtered (old cache entries)
    if language_filter:
        filtered_movies = 0
        for tmdb_id, item in list(movie_cache.items()):
            item_lang = item.get("original_language", "")
            if item_lang != language_filter:  # No language or wrong language = filtered
                del movie_cache[tmdb_id]
                filtered_movies += 1
        filtered_shows = 0
        for tmdb_id, item in list(show_cache.items()):
            item_lang = item.get("original_language", "")
            if item_lang != language_filter:  # No language or wrong language = filtered
                del show_cache[tmdb_id]
                filtered_shows += 1
        if filtered_movies or filtered_shows:
            print(
                f"{CYAN}Filtered {filtered_movies} movies and {filtered_shows} shows "
                f"(not {language_filter.upper()}){RESET}"
            )

    # Remove acquired items from cache (now in library) - check TMDB IDs AND titles
    removed_movies = []
    for tmdb_id, item in list(movie_cache.items()):
        if is_in_library(int(tmdb_id), item.get("title"), item.get("year"), library_movies):
            removed_movies.append(tmdb_id)
            del movie_cache[tmdb_id]
            print(f"  Removed movie from cache: {item.get('title')} (in library)")

    removed_shows = []
    for tmdb_id, item in list(show_cache.items()):
        if is_in_library(int(tmdb_id), item.get("title"), item.get("year"), library_shows):
            removed_shows.append(tmdb_id)
            del show_cache[tmdb_id]
            print(f"  Removed show from cache: {item.get('title')} (in library)")

    if removed_movies or removed_shows:
        print(f"{GREEN}Removed {len(removed_movies)} movies and {len(removed_shows)} shows (now in library){RESET}")

    # Remove ignored items
    removed_ignored = 0
    for tmdb_id, item in list(movie_cache.items()):
        if item["title"] in ignore_list:
            del movie_cache[tmdb_id]
            removed_ignored += 1
    for tmdb_id, item in list(show_cache.items()):
        if item["title"] in ignore_list:
            del show_cache[tmdb_id]
            removed_ignored += 1

    if removed_ignored:
        print(f"{YELLOW}Removed {removed_ignored} ignored items{RESET}")


def _pu_build_profiles(plex, config, username):
    """process_user stage: load (or build) this user's movie/show
    preference profiles, then enhance them with Trakt watch history where
    applicable (see process_user)."""
    # Load user profiles from cache (FAST) or build from scratch (SLOW)
    # Cache is pre-computed by internal recommenders with proper weighting
    movie_profile = load_user_profile_from_cache(config, username, "movie")
    if not movie_profile:
        movie_profile = _build_profile_via_recommender(username, "movie")

    show_profile = load_user_profile_from_cache(config, username, "tv")
    if not show_profile:
        show_profile = _build_profile_via_recommender(username, "tv")

    # Enhance profiles with Trakt watch history (streaming services not in Plex)
    # Only for users in the Trakt mapping
    tmdb_api_key = get_tmdb_config(config)["api_key"]
    trakt_config = config.get("trakt", {})
    export_config = trakt_config.get("export", {})
    user_mode = export_config.get("user_mode", "mapping")
    plex_users = export_config.get("plex_users", [])

    should_enhance = True
    if user_mode == "mapping" and plex_users:
        plex_users_lower = [u.lower() for u in plex_users]
        if username.lower() not in plex_users_lower:
            should_enhance = False

    if should_enhance:
        cache_dir = os.path.join(get_project_root(), config.get("cache_dir", "cache"))
        if movie_profile:
            movie_profile = enhance_profile_with_trakt(movie_profile, config, tmdb_api_key, cache_dir, "movie")
        if show_profile:
            show_profile = enhance_profile_with_trakt(show_profile, config, tmdb_api_key, cache_dir, "tv")

    return (movie_profile, show_profile, tmdb_api_key)


def _pu_plan_discovery(config, user_prefs, movie_cache, show_cache):
    """process_user stage: compute each cache's quality-item deficit
    against its configured limit, the cached-id exclusion sets, and any
    Trakt watchlist IMDB ids to exclude from discovery (see
    process_user)."""
    # Find new recommendations using profile-based scoring
    external_config = config.get("external_recommendations", {})
    movie_limit = external_config.get("movie_limit", 50)
    show_limit = external_config.get("show_limit", 20)
    min_relevance = external_config.get("min_relevance_score", 0.65)

    # Get excluded genres for this user
    exclude_genres = user_prefs.get("exclude_genres", [])
    if exclude_genres:
        print(f"Excluding genres: {', '.join(exclude_genres)}")

    # Check cache health and calculate deficit
    quality_movies = [m for m in movie_cache.values() if m.get("score", 0) >= min_relevance]
    quality_shows = [s for s in show_cache.values() if s.get("score", 0) >= min_relevance]

    movie_deficit = max(0, movie_limit - len(quality_movies))
    show_deficit = max(0, show_limit - len(quality_shows))

    # Collect cached TMDB IDs for exclusion (avoids re-scoring existing items)
    cached_movie_ids = {int(tid) for tid in movie_cache.keys()}
    cached_show_ids = {int(tid) for tid in show_cache.keys()}

    # Get Trakt watchlist exclusions if enabled (only if we need discovery)
    trakt_config = config.get("trakt", {})
    import_config = trakt_config.get("import", {})
    exclude_movie_imdb_ids = set()
    exclude_show_imdb_ids = set()

    if (movie_deficit > 0 or show_deficit > 0) and import_config.get("exclude_watchlist", True):
        trakt_client = get_authenticated_trakt_client(config)
        if trakt_client:
            print("Loading Trakt watchlist for exclusion...")
            exclude_movie_imdb_ids = trakt_client.get_watchlist_imdb_ids("movies")
            exclude_show_imdb_ids = trakt_client.get_watchlist_imdb_ids("shows")
            if exclude_movie_imdb_ids or exclude_show_imdb_ids:
                print(
                    f"Excluding {len(exclude_movie_imdb_ids)} movies, "
                    f"{len(exclude_show_imdb_ids)} shows from Trakt watchlist"
                )

    return (
        movie_limit,
        show_limit,
        min_relevance,
        exclude_genres,
        quality_movies,
        quality_shows,
        movie_deficit,
        show_deficit,
        cached_movie_ids,
        cached_show_ids,
        exclude_movie_imdb_ids,
        exclude_show_imdb_ids,
    )


def _pu_discover_new_content(
    tmdb_api_key,
    movie_profile,
    show_profile,
    library_movies,
    library_shows,
    movie_deficit,
    show_deficit,
    quality_movies,
    quality_shows,
    exclude_genres,
    min_relevance,
    config,
    exclude_movie_imdb_ids,
    exclude_show_imdb_ids,
    cached_movie_ids,
    cached_show_ids,
):
    """process_user stage: discover new movie/show recommendations to fill
    each cache's deficit (skipped entirely when a cache is already at or
    above its limit) (see process_user)."""
    # Movie discovery - skip if cache is full, otherwise find deficit items
    if movie_deficit == 0:
        print(f"{GREEN}Movie cache healthy ({len(quality_movies)} quality items), skipping discovery{RESET}")
        new_movies = []
    else:
        print(f"{CYAN}Movie cache needs {movie_deficit} items, discovering...{RESET}")
        new_movies = find_similar_content_with_profile(
            tmdb_api_key,
            movie_profile,
            library_movies,
            "movie",
            limit=movie_deficit,  # Only find what we need
            exclude_genres=exclude_genres,
            min_relevance_score=min_relevance,
            config=config,
            exclude_imdb_ids=exclude_movie_imdb_ids,
            exclude_cached_ids=cached_movie_ids,  # Skip items already in cache
        )

    # Show discovery - skip if cache is full, otherwise find deficit items
    if show_deficit == 0:
        print(f"{GREEN}Show cache healthy ({len(quality_shows)} quality items), skipping discovery{RESET}")
        new_shows = []
    else:
        print(f"{CYAN}Show cache needs {show_deficit} items, discovering...{RESET}")
        new_shows = find_similar_content_with_profile(
            tmdb_api_key,
            show_profile,
            library_shows,
            "tv",
            limit=show_deficit,  # Only find what we need
            exclude_genres=exclude_genres,
            min_relevance_score=min_relevance,
            config=config,
            exclude_imdb_ids=exclude_show_imdb_ids,
            exclude_cached_ids=cached_show_ids,  # Skip items already in cache
        )

    return (new_movies, new_shows)


def _pu_reconcile_caches(
    movie_cache,
    show_cache,
    new_movies,
    new_shows,
    movie_limit,
    show_limit,
    display_name,
    movie_cache_lib_id,
    tv_cache_lib_id,
    min_relevance,
):
    """process_user stage: merge newly-discovered items into the caches
    (updating scores for items already present), trim each cache back down
    to its configured limit (keeping the highest-scored items), persist
    both caches, then build the final threshold-filtered, limit-backfilled
    movie/show lists for this run's output (see process_user)."""
    # Merge with existing cache - UPDATE scores for existing items, ADD new ones
    for movie in new_movies:
        tmdb_id = str(movie["tmdb_id"])
        if tmdb_id in movie_cache:
            # Update score for existing item (profile may have changed)
            old_score = movie_cache[tmdb_id].get("score", 0)
            movie_cache[tmdb_id]["score"] = movie["score"]
            movie_cache[tmdb_id]["rating"] = movie["rating"]
            movie_cache[tmdb_id]["vote_count"] = movie.get("vote_count", 0)
            if abs(movie["score"] - old_score) > SCORE_CHANGE_THRESHOLD:
                print(f"    Updated score: {movie['title']} {old_score:.1%} -> {movie['score']:.1%}")
        else:
            # Add new item
            movie_cache[tmdb_id] = {
                "tmdb_id": movie["tmdb_id"],
                "title": movie["title"],
                "year": movie["year"],
                "rating": movie["rating"],
                "vote_count": movie.get("vote_count", 0),
                "score": movie["score"],
                "original_language": movie.get("original_language", ""),
                "added_date": datetime.now().isoformat(),
            }

    for show in new_shows:
        tmdb_id = str(show["tmdb_id"])
        if tmdb_id in show_cache:
            # Update score for existing item (profile may have changed)
            old_score = show_cache[tmdb_id].get("score", 0)
            show_cache[tmdb_id]["score"] = show["score"]
            show_cache[tmdb_id]["rating"] = show["rating"]
            show_cache[tmdb_id]["vote_count"] = show.get("vote_count", 0)
            if abs(show["score"] - old_score) > SCORE_CHANGE_THRESHOLD:
                print(f"    Updated score: {show['title']} {old_score:.1%} -> {show['score']:.1%}")
        else:
            # Add new item
            show_cache[tmdb_id] = {
                "tmdb_id": show["tmdb_id"],
                "title": show["title"],
                "year": show["year"],
                "rating": show["rating"],
                "vote_count": show.get("vote_count", 0),
                "score": show["score"],
                "original_language": show.get("original_language", ""),
                "added_date": datetime.now().isoformat(),
            }

    # Trim caches to limit - keep highest scored items, remove lowest
    # This replaces time-based staleness: better recs push out worse ones
    trimmed_movies = 0
    trimmed_shows = 0

    if len(movie_cache) > movie_limit:
        sorted_movies = sorted(movie_cache.items(), key=lambda x: x[1].get("score", 0), reverse=True)
        keep_ids = {tmdb_id for tmdb_id, _ in sorted_movies[:movie_limit]}
        for tmdb_id in list(movie_cache.keys()):
            if tmdb_id not in keep_ids:
                del movie_cache[tmdb_id]
                trimmed_movies += 1

    if len(show_cache) > show_limit:
        sorted_shows = sorted(show_cache.items(), key=lambda x: x[1].get("score", 0), reverse=True)
        keep_ids = {tmdb_id for tmdb_id, _ in sorted_shows[:show_limit]}
        for tmdb_id in list(show_cache.keys()):
            if tmdb_id not in keep_ids:
                del show_cache[tmdb_id]
                trimmed_shows += 1

    if trimmed_movies or trimmed_shows:
        print(f"{YELLOW}Trimmed cache: {trimmed_movies} movies, {trimmed_shows} shows (replaced by better recs){RESET}")

    # Save updated caches
    save_cache(display_name, "movies", movie_cache, lib_id=movie_cache_lib_id)
    save_cache(display_name, "shows", show_cache, lib_id=tv_cache_lib_id)

    # Prepare lists for categorization - apply threshold and limits
    all_movies = sorted(movie_cache.values(), key=lambda x: x["score"], reverse=True)
    all_shows = sorted(show_cache.values(), key=lambda x: x["score"], reverse=True)

    # Filter by relevance threshold - prioritize high-score items
    high_movies = [m for m in all_movies if m["score"] >= min_relevance]
    low_movies = [m for m in all_movies if m["score"] < min_relevance]
    high_shows = [s for s in all_shows if s["score"] >= min_relevance]
    low_shows = [s for s in all_shows if s["score"] < min_relevance]

    # Take high-score items first, backfill with low-score only if needed
    movies_list = high_movies[:movie_limit]
    if len(movies_list) < movie_limit:
        movies_list.extend(low_movies[: movie_limit - len(movies_list)])

    shows_list = high_shows[:show_limit]
    if len(shows_list) < show_limit:
        shows_list.extend(low_shows[: show_limit - len(shows_list)])

    print(
        f"{GREEN}Output: {len(movies_list)} movies "
        f"({len(high_movies)} above {int(min_relevance * 100)}% threshold){RESET}"
    )
    print(
        f"{GREEN}Output: {len(shows_list)} shows ({len(high_shows)} above {int(min_relevance * 100)}% threshold){RESET}"
    )

    return (movies_list, shows_list)


def _pu_categorize_and_stamp(config, movies_list, shows_list, tmdb_api_key, movie_library, tv_library, username):
    """process_user stage: categorize the final movie/show lists by
    streaming-service availability and stamp each item with its source
    library id (#157 Phase 3 provenance) (see process_user)."""
    # Household streaming services from top-level config, merged with
    # this user's personal override if they have one
    # (users.preferences.<user>.streaming_services) - mirrors
    # get_excluded_genres_for_user()'s global-plus-per-user merge
    # semantics (see get_streaming_services_for_user).
    user_prefs_all = config.get("users", {}).get("preferences", {})
    user_services = get_streaming_services_for_user(config.get("streaming_services", []), user_prefs_all, username)

    # Categorize by streaming service availability
    print(f"{CYAN}Categorizing by streaming service availability...{RESET}")
    movies_categorized = categorize_by_streaming_service(movies_list, tmdb_api_key, user_services, "movie")
    shows_categorized = categorize_by_streaming_service(shows_list, tmdb_api_key, user_services, "tv")

    # Item provenance (#157 Phase 3): stamp each recommendation with the
    # library it was sourced from/targets. None when running the legacy
    # single-library path (movie_library/tv_library not passed).
    movie_item_library_id = movie_library["id"] if movie_library else None
    tv_item_library_id = tv_library["id"] if tv_library else None
    _stamp_library_id(movies_categorized, movie_item_library_id)
    _stamp_library_id(shows_categorized, tv_item_library_id)

    return (movies_categorized, shows_categorized, user_services)


def _pu_finalize_output(username, display_name, movies_categorized, shows_categorized):
    """process_user stage: write this user's markdown recommendations file
    and print the final per-user summary (see process_user)."""
    # Generate markdown per user
    project_root = get_project_root()
    output_dir = os.path.join(project_root, "recommendations", "external")
    generate_markdown(username, display_name, movies_categorized, shows_categorized, output_dir)

    # Count totals
    total_movies = (
        sum(len(items) for items in movies_categorized["user_services"].values())
        + sum(len(items) for items in movies_categorized["other_services"].values())
        + len(movies_categorized["acquire"])
    )
    total_shows = (
        sum(len(items) for items in shows_categorized["user_services"].values())
        + sum(len(items) for items in shows_categorized["other_services"].values())
        + len(shows_categorized["acquire"])
    )

    print(f"{GREEN}Processed: {total_movies} movies, {total_shows} shows{RESET}")
    print(f"\nExternal recommendation process completed for {display_name}!")


def process_user(config, plex, username, movie_library=None, tv_library=None):
    """Process external recommendations for a single user.

    Args:
        config: Root configuration dictionary
        plex: Connected PlexServer instance
        username: Plex username to process
        movie_library: Optional normalized movie library dict (#157 Phase 3,
            see utils.config.get_libraries). None keeps the legacy
            single-library behavior (config['plex'].movie_library).
        tv_library: Optional normalized tv library dict (#157 Phase 3).
            None keeps the legacy single-library behavior
            (config['plex'].tv_library).
    """
    user_prefs, display_name, movie_library_name, tv_library_name, movie_cache_lib_id, tv_cache_lib_id = (
        _pu_resolve_context(config, username, movie_library, tv_library)
    )

    library_movies, library_shows, movie_cache, show_cache, ignore_list = _pu_load_libraries_and_caches(
        plex, display_name, movie_library_name, tv_library_name, movie_cache_lib_id, tv_cache_lib_id
    )

    _pu_clean_caches(config, movie_cache, show_cache, library_movies, library_shows, ignore_list)

    movie_profile, show_profile, tmdb_api_key = _pu_build_profiles(plex, config, username)

    (
        movie_limit,
        show_limit,
        min_relevance,
        exclude_genres,
        quality_movies,
        quality_shows,
        movie_deficit,
        show_deficit,
        cached_movie_ids,
        cached_show_ids,
        exclude_movie_imdb_ids,
        exclude_show_imdb_ids,
    ) = _pu_plan_discovery(config, user_prefs, movie_cache, show_cache)

    new_movies, new_shows = _pu_discover_new_content(
        tmdb_api_key,
        movie_profile,
        show_profile,
        library_movies,
        library_shows,
        movie_deficit,
        show_deficit,
        quality_movies,
        quality_shows,
        exclude_genres,
        min_relevance,
        config,
        exclude_movie_imdb_ids,
        exclude_show_imdb_ids,
        cached_movie_ids,
        cached_show_ids,
    )

    movies_list, shows_list = _pu_reconcile_caches(
        movie_cache,
        show_cache,
        new_movies,
        new_shows,
        movie_limit,
        show_limit,
        display_name,
        movie_cache_lib_id,
        tv_cache_lib_id,
        min_relevance,
    )

    movies_categorized, shows_categorized, user_services = _pu_categorize_and_stamp(
        config, movies_list, shows_list, tmdb_api_key, movie_library, tv_library, username
    )

    _pu_finalize_output(username, display_name, movies_categorized, shows_categorized)

    # Return data for combined HTML generation and Trakt sync.
    #
    # library_id (#157 Phase 3 provenance): a single call processes BOTH
    # movies and shows, which can belong to different libraries (different
    # ids) once there's more than one library of either media type. Export
    # routing's _resolve_library_groups() (Phase 2, unchanged) reads this
    # single field for BOTH Radarr and Sonarr grouping without checking
    # media_type, so it cannot safely hold two different ids at once -
    # stamping either one here would misroute the other media type's export
    # in the common case (one movie library + one TV library, different
    # ids). We deliberately leave it None so Phase 2's existing per-media-type
    # fallback (get_libraries_for_media_type(...)[0]) keeps routing
    # correctly; per-item library_id (see _stamp_library_id above) already
    # carries the real provenance for anything that inspects individual
    # recommendations.
    #
    # This function is only ever called by main() for the single-per-media-
    # type case (see the fan_out branch below and its docstring on
    # process_user_movie_library/process_user_tv_library) - it is
    # deliberately left untouched by #157 Phase 3.5 so that path stays
    # byte-identical to Phase 3.
    return {
        "username": username,
        "display_name": display_name,
        "movies_categorized": movies_categorized,
        "shows_categorized": shows_categorized,
        "movie_profile": movie_profile,
        "show_profile": show_profile,
        "user_services": user_services,
        "library_id": None,
    }


def _empty_categorized() -> Dict:
    """A freshly-allocated, empty categorize_by_streaming_service()-shaped
    dict (#157 Phase 3.5) - used to fill in the "other" media type's slot
    when a fan-out run only covers one media type."""
    return {"user_services": {}, "other_services": {}, "acquire": [], "all_items": []}


def process_user_movie_library(config, plex, username, library):
    """Process external MOVIE recommendations for one user against ONE
    movie library (#157 Phase 3.5 fan-out).

    process_user() handles movies and shows together against a single
    "primary" library per media type and can't safely fan out to more (see
    its docstring) - main() uses this function instead once a config has
    more than one movie library, calling it once per (movie library, user)
    so each library gets its own discovery pass, its own qualified cache
    files, its own qualified markdown watchlist, and items stamped with
    this library's real id for #157 Phase 2 export routing.

    Args:
        config: Root configuration dictionary
        plex: Connected PlexServer instance
        username: Plex username to process
        library: Normalized movie library dict (see utils.config.get_libraries)

    Returns:
        Dict shaped like process_user()'s return value: shows_categorized is
        an empty categorized dict, show_profile is None, and library_id is
        this library's real id (never None - unlike process_user()).
    """
    user_prefs = config.get("users", {}).get("preferences", {}).get(username, {})
    display_name = user_prefs.get("display_name", username)

    print(f"\n{GREEN}Processing external movie recommendations for: {display_name} ({library['name']}){RESET}")

    movie_library_name = library["section"]

    # Cache/markdown filenames stay legacy (unqualified) unless this install
    # has more than one movie library - same back-compat rule as
    # process_user()/the internal recommenders (#157 Phase 3).
    is_multi = len(get_libraries_for_media_type(config, MEDIA_TYPE_MOVIE)) > 1
    cache_lib_id = library["id"] if is_multi else None

    library_movies = get_library_items(plex, movie_library_name, "movie")
    print(f"{CYAN}Library has {len(library_movies['titles'])} movies{RESET}")

    movie_cache = load_cache(display_name, "movies", lib_id=cache_lib_id)
    ignore_list = load_ignore_list(display_name)

    external_config = config.get("external_recommendations", {})
    language_filter = external_config.get("language")

    if language_filter:
        filtered_movies = 0
        for tmdb_id, item in list(movie_cache.items()):
            item_lang = item.get("original_language", "")
            if item_lang != language_filter:
                del movie_cache[tmdb_id]
                filtered_movies += 1
        if filtered_movies:
            print(f"{CYAN}Filtered {filtered_movies} movies (not {language_filter.upper()}){RESET}")

    removed_movies = []
    for tmdb_id, item in list(movie_cache.items()):
        if is_in_library(int(tmdb_id), item.get("title"), item.get("year"), library_movies):
            removed_movies.append(tmdb_id)
            del movie_cache[tmdb_id]
            print(f"  Removed movie from cache: {item.get('title')} (in library)")
    if removed_movies:
        print(f"{GREEN}Removed {len(removed_movies)} movies (now in library){RESET}")

    removed_ignored = 0
    for tmdb_id, item in list(movie_cache.items()):
        if item["title"] in ignore_list:
            del movie_cache[tmdb_id]
            removed_ignored += 1
    if removed_ignored:
        print(f"{YELLOW}Removed {removed_ignored} ignored items{RESET}")

    movie_profile = load_user_profile_from_cache(config, username, "movie")
    if not movie_profile:
        movie_profile = _build_profile_via_recommender(username, "movie")

    tmdb_api_key = get_tmdb_config(config)["api_key"]
    trakt_config = config.get("trakt", {})
    export_config = trakt_config.get("export", {})
    user_mode = export_config.get("user_mode", "mapping")
    plex_users = export_config.get("plex_users", [])

    should_enhance = True
    if user_mode == "mapping" and plex_users:
        plex_users_lower = [u.lower() for u in plex_users]
        if username.lower() not in plex_users_lower:
            should_enhance = False

    if should_enhance:
        cache_dir = os.path.join(get_project_root(), config.get("cache_dir", "cache"))
        if movie_profile:
            movie_profile = enhance_profile_with_trakt(movie_profile, config, tmdb_api_key, cache_dir, "movie")

    movie_limit = external_config.get("movie_limit", 50)
    min_relevance = external_config.get("min_relevance_score", 0.65)

    exclude_genres = user_prefs.get("exclude_genres", [])
    if exclude_genres:
        print(f"Excluding genres: {', '.join(exclude_genres)}")

    quality_movies = [m for m in movie_cache.values() if m.get("score", 0) >= min_relevance]
    movie_deficit = max(0, movie_limit - len(quality_movies))

    cached_movie_ids = {int(tid) for tid in movie_cache.keys()}

    import_config = trakt_config.get("import", {})
    exclude_movie_imdb_ids = set()

    if movie_deficit > 0 and import_config.get("exclude_watchlist", True):
        trakt_client = get_authenticated_trakt_client(config)
        if trakt_client:
            print("Loading Trakt watchlist for exclusion...")
            exclude_movie_imdb_ids = trakt_client.get_watchlist_imdb_ids("movies")
            if exclude_movie_imdb_ids:
                print(f"Excluding {len(exclude_movie_imdb_ids)} movies from Trakt watchlist")

    if movie_deficit == 0:
        print(f"{GREEN}Movie cache healthy ({len(quality_movies)} quality items), skipping discovery{RESET}")
        new_movies = []
    else:
        print(f"{CYAN}Movie cache needs {movie_deficit} items, discovering...{RESET}")
        new_movies = find_similar_content_with_profile(
            tmdb_api_key,
            movie_profile,
            library_movies,
            "movie",
            limit=movie_deficit,
            exclude_genres=exclude_genres,
            min_relevance_score=min_relevance,
            config=config,
            exclude_imdb_ids=exclude_movie_imdb_ids,
            exclude_cached_ids=cached_movie_ids,
        )

    for movie in new_movies:
        tmdb_id = str(movie["tmdb_id"])
        if tmdb_id in movie_cache:
            old_score = movie_cache[tmdb_id].get("score", 0)
            movie_cache[tmdb_id]["score"] = movie["score"]
            movie_cache[tmdb_id]["rating"] = movie["rating"]
            movie_cache[tmdb_id]["vote_count"] = movie.get("vote_count", 0)
            if abs(movie["score"] - old_score) > SCORE_CHANGE_THRESHOLD:
                print(f"    Updated score: {movie['title']} {old_score:.1%} -> {movie['score']:.1%}")
        else:
            movie_cache[tmdb_id] = {
                "tmdb_id": movie["tmdb_id"],
                "title": movie["title"],
                "year": movie["year"],
                "rating": movie["rating"],
                "vote_count": movie.get("vote_count", 0),
                "score": movie["score"],
                "original_language": movie.get("original_language", ""),
                "added_date": datetime.now().isoformat(),
            }

    trimmed_movies = 0
    if len(movie_cache) > movie_limit:
        sorted_movies = sorted(movie_cache.items(), key=lambda x: x[1].get("score", 0), reverse=True)
        keep_ids = {tmdb_id for tmdb_id, _ in sorted_movies[:movie_limit]}
        for tmdb_id in list(movie_cache.keys()):
            if tmdb_id not in keep_ids:
                del movie_cache[tmdb_id]
                trimmed_movies += 1
    if trimmed_movies:
        print(f"{YELLOW}Trimmed cache: {trimmed_movies} movies (replaced by better recs){RESET}")

    save_cache(display_name, "movies", movie_cache, lib_id=cache_lib_id)

    all_movies = sorted(movie_cache.values(), key=lambda x: x["score"], reverse=True)
    high_movies = [m for m in all_movies if m["score"] >= min_relevance]
    low_movies = [m for m in all_movies if m["score"] < min_relevance]
    movies_list = high_movies[:movie_limit]
    if len(movies_list) < movie_limit:
        movies_list.extend(low_movies[: movie_limit - len(movies_list)])

    print(
        f"{GREEN}Output: {len(movies_list)} movies "
        f"({len(high_movies)} above {int(min_relevance * 100)}% threshold){RESET}"
    )

    # Household streaming services, merged with this user's personal
    # override if they have one - see get_streaming_services_for_user.
    user_services = get_streaming_services_for_user(
        config.get("streaming_services", []), config.get("users", {}).get("preferences", {}), username
    )

    print(f"{CYAN}Categorizing by streaming service availability...{RESET}")
    movies_categorized = categorize_by_streaming_service(movies_list, tmdb_api_key, user_services, "movie")

    # Item provenance (#157 Phase 3 stamping, Phase 3.5 fan-out): every item
    # from a scoped single-library run always carries this library's real id.
    _stamp_library_id(movies_categorized, library["id"])

    shows_categorized = _empty_categorized()

    project_root = get_project_root()
    output_dir = os.path.join(project_root, "recommendations", "external")
    library_suffix = f"_{library['id']}" if is_multi else ""
    generate_markdown(
        username, display_name, movies_categorized, shows_categorized, output_dir, library_suffix=library_suffix
    )

    total_movies = (
        sum(len(items) for items in movies_categorized["user_services"].values())
        + sum(len(items) for items in movies_categorized["other_services"].values())
        + len(movies_categorized["acquire"])
    )

    print(f"{GREEN}Processed: {total_movies} movies (library: {library['name']}){RESET}")
    print(f"\nExternal movie recommendation process completed for {display_name} ({library['name']})!")

    return {
        "username": username,
        "display_name": display_name,
        "movies_categorized": movies_categorized,
        "shows_categorized": shows_categorized,
        "movie_profile": movie_profile,
        "show_profile": None,
        "user_services": user_services,
        "library_id": library["id"],
    }


def process_user_tv_library(config, plex, username, library):
    """Process external TV recommendations for one user against ONE tv
    library (#157 Phase 3.5 fan-out). TV sibling of process_user_movie_library
    - see its docstring for the rationale.

    Args:
        config: Root configuration dictionary
        plex: Connected PlexServer instance
        username: Plex username to process
        library: Normalized tv library dict (see utils.config.get_libraries)

    Returns:
        Dict shaped like process_user()'s return value: movies_categorized is
        an empty categorized dict, movie_profile is None, and library_id is
        this library's real id (never None - unlike process_user()).
    """
    user_prefs = config.get("users", {}).get("preferences", {}).get(username, {})
    display_name = user_prefs.get("display_name", username)

    print(f"\n{GREEN}Processing external TV recommendations for: {display_name} ({library['name']}){RESET}")

    tv_library_name = library["section"]

    is_multi = len(get_libraries_for_media_type(config, MEDIA_TYPE_TV)) > 1
    cache_lib_id = library["id"] if is_multi else None

    library_shows = get_library_items(plex, tv_library_name, "show")
    print(f"{CYAN}Library has {len(library_shows['titles'])} TV shows{RESET}")

    show_cache = load_cache(display_name, "shows", lib_id=cache_lib_id)
    ignore_list = load_ignore_list(display_name)

    external_config = config.get("external_recommendations", {})
    language_filter = external_config.get("language")

    if language_filter:
        filtered_shows = 0
        for tmdb_id, item in list(show_cache.items()):
            item_lang = item.get("original_language", "")
            if item_lang != language_filter:
                del show_cache[tmdb_id]
                filtered_shows += 1
        if filtered_shows:
            print(f"{CYAN}Filtered {filtered_shows} shows (not {language_filter.upper()}){RESET}")

    removed_shows = []
    for tmdb_id, item in list(show_cache.items()):
        if is_in_library(int(tmdb_id), item.get("title"), item.get("year"), library_shows):
            removed_shows.append(tmdb_id)
            del show_cache[tmdb_id]
            print(f"  Removed show from cache: {item.get('title')} (in library)")
    if removed_shows:
        print(f"{GREEN}Removed {len(removed_shows)} shows (now in library){RESET}")

    removed_ignored = 0
    for tmdb_id, item in list(show_cache.items()):
        if item["title"] in ignore_list:
            del show_cache[tmdb_id]
            removed_ignored += 1
    if removed_ignored:
        print(f"{YELLOW}Removed {removed_ignored} ignored items{RESET}")

    show_profile = load_user_profile_from_cache(config, username, "tv")
    if not show_profile:
        show_profile = _build_profile_via_recommender(username, "tv")

    tmdb_api_key = get_tmdb_config(config)["api_key"]
    trakt_config = config.get("trakt", {})
    export_config = trakt_config.get("export", {})
    user_mode = export_config.get("user_mode", "mapping")
    plex_users = export_config.get("plex_users", [])

    should_enhance = True
    if user_mode == "mapping" and plex_users:
        plex_users_lower = [u.lower() for u in plex_users]
        if username.lower() not in plex_users_lower:
            should_enhance = False

    if should_enhance:
        cache_dir = os.path.join(get_project_root(), config.get("cache_dir", "cache"))
        if show_profile:
            show_profile = enhance_profile_with_trakt(show_profile, config, tmdb_api_key, cache_dir, "tv")

    show_limit = external_config.get("show_limit", 20)
    min_relevance = external_config.get("min_relevance_score", 0.65)

    exclude_genres = user_prefs.get("exclude_genres", [])
    if exclude_genres:
        print(f"Excluding genres: {', '.join(exclude_genres)}")

    quality_shows = [s for s in show_cache.values() if s.get("score", 0) >= min_relevance]
    show_deficit = max(0, show_limit - len(quality_shows))

    cached_show_ids = {int(tid) for tid in show_cache.keys()}

    import_config = trakt_config.get("import", {})
    exclude_show_imdb_ids = set()

    if show_deficit > 0 and import_config.get("exclude_watchlist", True):
        trakt_client = get_authenticated_trakt_client(config)
        if trakt_client:
            print("Loading Trakt watchlist for exclusion...")
            exclude_show_imdb_ids = trakt_client.get_watchlist_imdb_ids("shows")
            if exclude_show_imdb_ids:
                print(f"Excluding {len(exclude_show_imdb_ids)} shows from Trakt watchlist")

    if show_deficit == 0:
        print(f"{GREEN}Show cache healthy ({len(quality_shows)} quality items), skipping discovery{RESET}")
        new_shows = []
    else:
        print(f"{CYAN}Show cache needs {show_deficit} items, discovering...{RESET}")
        new_shows = find_similar_content_with_profile(
            tmdb_api_key,
            show_profile,
            library_shows,
            "tv",
            limit=show_deficit,
            exclude_genres=exclude_genres,
            min_relevance_score=min_relevance,
            config=config,
            exclude_imdb_ids=exclude_show_imdb_ids,
            exclude_cached_ids=cached_show_ids,
        )

    for show in new_shows:
        tmdb_id = str(show["tmdb_id"])
        if tmdb_id in show_cache:
            old_score = show_cache[tmdb_id].get("score", 0)
            show_cache[tmdb_id]["score"] = show["score"]
            show_cache[tmdb_id]["rating"] = show["rating"]
            show_cache[tmdb_id]["vote_count"] = show.get("vote_count", 0)
            if abs(show["score"] - old_score) > SCORE_CHANGE_THRESHOLD:
                print(f"    Updated score: {show['title']} {old_score:.1%} -> {show['score']:.1%}")
        else:
            show_cache[tmdb_id] = {
                "tmdb_id": show["tmdb_id"],
                "title": show["title"],
                "year": show["year"],
                "rating": show["rating"],
                "vote_count": show.get("vote_count", 0),
                "score": show["score"],
                "original_language": show.get("original_language", ""),
                "added_date": datetime.now().isoformat(),
            }

    trimmed_shows = 0
    if len(show_cache) > show_limit:
        sorted_shows = sorted(show_cache.items(), key=lambda x: x[1].get("score", 0), reverse=True)
        keep_ids = {tmdb_id for tmdb_id, _ in sorted_shows[:show_limit]}
        for tmdb_id in list(show_cache.keys()):
            if tmdb_id not in keep_ids:
                del show_cache[tmdb_id]
                trimmed_shows += 1
    if trimmed_shows:
        print(f"{YELLOW}Trimmed cache: {trimmed_shows} shows (replaced by better recs){RESET}")

    save_cache(display_name, "shows", show_cache, lib_id=cache_lib_id)

    all_shows = sorted(show_cache.values(), key=lambda x: x["score"], reverse=True)
    high_shows = [s for s in all_shows if s["score"] >= min_relevance]
    low_shows = [s for s in all_shows if s["score"] < min_relevance]
    shows_list = high_shows[:show_limit]
    if len(shows_list) < show_limit:
        shows_list.extend(low_shows[: show_limit - len(shows_list)])

    print(
        f"{GREEN}Output: {len(shows_list)} shows ({len(high_shows)} above {int(min_relevance * 100)}% threshold){RESET}"
    )

    # Household streaming services, merged with this user's personal
    # override if they have one - see get_streaming_services_for_user.
    user_services = get_streaming_services_for_user(
        config.get("streaming_services", []), config.get("users", {}).get("preferences", {}), username
    )

    print(f"{CYAN}Categorizing by streaming service availability...{RESET}")
    shows_categorized = categorize_by_streaming_service(shows_list, tmdb_api_key, user_services, "tv")

    # Item provenance (#157 Phase 3 stamping, Phase 3.5 fan-out): every item
    # from a scoped single-library run always carries this library's real id.
    _stamp_library_id(shows_categorized, library["id"])

    movies_categorized = _empty_categorized()

    project_root = get_project_root()
    output_dir = os.path.join(project_root, "recommendations", "external")
    library_suffix = f"_{library['id']}" if is_multi else ""
    generate_markdown(
        username, display_name, movies_categorized, shows_categorized, output_dir, library_suffix=library_suffix
    )

    total_shows = (
        sum(len(items) for items in shows_categorized["user_services"].values())
        + sum(len(items) for items in shows_categorized["other_services"].values())
        + len(shows_categorized["acquire"])
    )

    print(f"{GREEN}Processed: {total_shows} shows (library: {library['name']}){RESET}")
    print(f"\nExternal TV recommendation process completed for {display_name} ({library['name']})!")

    return {
        "username": username,
        "display_name": display_name,
        "movies_categorized": movies_categorized,
        "shows_categorized": shows_categorized,
        "movie_profile": None,
        "show_profile": show_profile,
        "user_services": user_services,
        "library_id": library["id"],
    }


def _merge_categorized(categorized_list: List[Dict]) -> Dict:
    """Merge multiple categorize_by_streaming_service()-shaped dicts into one
    (#157 Phase 3.5 fan-out).

    Used to rebuild a single combined view of a user's recommendations
    across all of their libraries of a media type, for the consumers that
    have no per-library routing concept: the combined watchlist.html tabs
    and the Trakt/MDBList/Simkl exports (only Sonarr/Radarr route by
    library_id - see main()'s arr_export_data).

    Args:
        categorized_list: List of categorize_by_streaming_service() results

    Returns:
        A single merged categorized dict, 'all_items' re-sorted by score
    """
    merged = _empty_categorized()
    for categorized in categorized_list:
        for service, items in categorized.get("user_services", {}).items():
            merged["user_services"].setdefault(service, []).extend(items)
        for service, items in categorized.get("other_services", {}).items():
            merged["other_services"].setdefault(service, []).extend(items)
        merged["acquire"].extend(categorized.get("acquire", []))
        merged["all_items"].extend(categorized.get("all_items", []))
    merged["all_items"].sort(key=lambda x: x.get("score", 0), reverse=True)
    return merged


def _merge_user_runs(username: str, movie_runs: List[Dict], tv_runs: List[Dict]) -> Dict:
    """Merge one user's per-library fan-out results (#157 Phase 3.5) into a
    single combined entry for watchlist.html / Trakt / MDBList / Simkl - see
    _merge_categorized().

    Args:
        username: Plex username
        movie_runs: This user's process_user_movie_library() results (0+)
        tv_runs: This user's process_user_tv_library() results (0+)

    Returns:
        Dict shaped like process_user()'s return value, library_id=None
        (combines multiple libraries, same reasoning as process_user()'s
        return docstring)
    """
    source = movie_runs[0] if movie_runs else tv_runs[0]

    movies_categorized = (
        _merge_categorized([r["movies_categorized"] for r in movie_runs]) if movie_runs else _empty_categorized()
    )
    shows_categorized = (
        _merge_categorized([r["shows_categorized"] for r in tv_runs]) if tv_runs else _empty_categorized()
    )

    return {
        "username": username,
        "display_name": source["display_name"],
        "movies_categorized": movies_categorized,
        "shows_categorized": shows_categorized,
        "movie_profile": movie_runs[0]["movie_profile"] if movie_runs else None,
        "show_profile": tv_runs[0]["show_profile"] if tv_runs else None,
        "user_services": source["user_services"],
        "library_id": None,
    }


def main():
    """Thin wrapper around _main_impl() that records
    curatarr_recommender_runs_total/curatarr_recommender_run_duration_seconds
    (engine='external' - see utils/metrics.py) for the whole run,
    regardless of how it ends (normal completion, sys.exit(), or an
    unhandled exception) - kept separate from _main_impl() itself so
    that function's existing body/control flow (including its own
    per-user try/except blocks) needed no re-indentation to add this."""
    run_start = time.monotonic()
    outcome = "failure"
    try:
        _main_impl()
        outcome = "success"
    except SystemExit:
        raise
    except Exception:
        record_unhandled_error(component="external")
        raise
    finally:
        record_recommender_run("external", outcome, time.monotonic() - run_start)


def _main_impl():
    import argparse

    parser = argparse.ArgumentParser(description="External Recommendations Generator")
    parser.add_argument("--huntarr-only", action="store_true", help="Run only Huntarr features (skip recommendations)")
    args = parser.parse_args()

    # Load config from project root (repo root for a source install, the
    # per-user data dir when frozen - see utils.helpers.get_project_root)
    project_root = get_project_root()
    config_path = os.path.join(project_root, "config/config.yml")
    config = load_config(config_path)
    # #292: same directory get_last_run_status()/record_run_status()
    # already use for movie.py/tv.py - external.py has no setup_log_file()
    # call of its own (it writes recommendations/external/* output, not
    # a logs/*.log file), so this is purely for the explicit run-status
    # signal below, not an actual log file.
    log_dir = os.path.join(project_root, "logs")

    # Suppress urllib3's InsecureRequestWarning ONLY when this config
    # actually opts out of certificate verification (verify_ssl: false,
    # an explicit user choice, e.g. for a local Plex server with a
    # self-signed cert) - never unconditionally at import time (this
    # module's previous behavior), which would also silence the warning
    # for every other HTTPS request this process ever makes. Matches
    # utils/plex.py's _resolve_verify_ssl - see that function's
    # docstring for the same reasoning.
    if not config.get("plex", {}).get("verify_ssl", True):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Huntarr: config controls which features are enabled
    # huntarr.sequel_huntarr: true/false (default: true) - missing collection movies
    # huntarr.horizon_huntarr: true/false (default: true) - upcoming unreleased movies
    huntarr_config = config.get("huntarr", {})
    run_sequel_huntarr = huntarr_config.get("sequel_huntarr", True)
    run_horizon_huntarr = huntarr_config.get("horizon_huntarr", True)
    # external_recommendations.enabled: default True, matching the
    # documented example and every install's effective behavior before
    # this was wired up - this key was previously read nowhere at all
    # (config/tuning.example.yml documented it, but no code path ever
    # checked it), so anyone who set it false was silently still getting
    # external recommendations anyway. Gates ONLY the recommendations/
    # watchlist-building pass below, never Huntarr (a separate feature
    # under its own huntarr.* keys, already independently gated above).
    external_recommendations_enabled = config.get("external_recommendations", {}).get("enabled", True)
    run_recommendations = not args.huntarr_only and external_recommendations_enabled

    if args.huntarr_only:
        print(f"\n{GREEN}=== Huntarr: Collection Movie Finder ==={RESET}")
    else:
        print(f"\n{GREEN}=== External Recommendations Generator ==={RESET}")
        if not external_recommendations_enabled:
            print(f"{YELLOW}Skipping recommendations (external_recommendations.enabled is false in config){RESET}")
        if run_sequel_huntarr:
            print(f"{CYAN}Sequel Huntarr enabled{RESET}")
        if run_horizon_huntarr:
            print(f"{CYAN}Horizon Huntarr enabled{RESET}")

    # Get TMDB API key
    tmdb_api_key = get_tmdb_config(config)["api_key"]
    if not tmdb_api_key:
        # Unlike movie.py/tv.py (where tmdb_api_key is genuinely optional -
        # both guard every use with `if tmdb_api_key`, degrading to
        # Plex-native-only scoring without it), this module has no degraded
        # mode: every candidate it discovers comes from TMDB (discovery/
        # keyword/collection/watch-provider lookups all type-hint
        # tmdb_api_key as `str`, never `Optional[str]`), so a missing key
        # wouldn't shrink the watchlist, it would silently break it
        # end-to-end. fetch_tmdb_with_retry() swallows every TMDB failure
        # into a bare `None` by design (see that function's docstring), so
        # without this check that would otherwise surface only as a
        # confusing empty/broken watchlist with no indication why - fail
        # early and clearly here instead.
        log_error("TMDB API key is not configured (config/config.yml: tmdb.api_key).")
        log_error("Get a free key from: https://www.themoviedb.org/settings/api")
        sys.exit(1)

    # Get users
    users = [u.strip() for u in config["users"]["list"].split(",")]

    # Note: Trakt sync happens in run.sh BEFORE recommenders run
    # This ensures both internal and external recommenders benefit

    # Connect to Plex
    try:
        plex = PlexServer(config["plex"]["url"], config["plex"]["token"])
        print(f"{GREEN}Connected to Plex{RESET}")
    except Exception as e:
        log_error(f"Error connecting to Plex: {e}")
        sys.exit(1)

    # Process each user and collect data for combined HTML.
    #
    # all_users_data: exactly one entry per user (merged across that user's
    # libraries when fanned out - see _merge_user_runs) - feeds
    # generate_combined_html, the movie/show "shared" counts below, and the
    # Trakt/MDBList/Simkl exports, none of which have a per-library routing
    # concept.
    #
    # arr_export_data: one entry per (user, library) - feeds ONLY the
    # Sonarr/Radarr exports (#157 Phase 2's _resolve_library_groups routes
    # by each entry's library_id). In the non-fan-out path these are the
    # exact same list (library_id is None on every entry either way, so
    # routing is unaffected).
    all_users_data = []
    arr_export_data = []
    movie_counts = {}  # tmdb_id -> count
    show_counts = {}
    total_users = 0

    if run_recommendations:
        # #157 Phase 3: resolve the movie/tv libraries for this run.
        movie_libraries = get_libraries_for_media_type(config, MEDIA_TYPE_MOVIE)
        tv_libraries = get_libraries_for_media_type(config, MEDIA_TYPE_TV)

        # #157 Phase 3.5: only configs with 2+ libraries of the SAME media
        # type fan out into per-library runs (below). A single-library
        # install, or a one-movie-library + one-tv-library install, takes
        # the untouched Phase 3 path: process_user() handles movies and
        # shows together for a user in a single pass (shared Trakt
        # watchlist fetch, one combined markdown file per user) - exactly
        # one process_user() call per user, same output filenames, same
        # external API call volume as before Phase 3.5.
        fan_out = len(movie_libraries) > 1 or len(tv_libraries) > 1

        if not fan_out:
            primary_movie_library = movie_libraries[0] if movie_libraries else None
            primary_tv_library = tv_libraries[0] if tv_libraries else None

            for username in users:
                try:
                    user_data = process_user(
                        config, plex, username, movie_library=primary_movie_library, tv_library=primary_tv_library
                    )
                    if user_data:
                        all_users_data.append(user_data)
                    # #292: explicit, structured outcome for this user's
                    # external run - see utils/run_status.py's own
                    # docstring and recommenders/movie.py's matching hook.
                    record_run_status(log_dir, "external", username, True)
                except Exception as e:
                    log_error(f"Error processing {username}: {e}")
                    traceback.print_exc()
                    record_run_status(log_dir, "external", username, False, str(e))

            arr_export_data = all_users_data
        else:
            # Fan out: run movie recs once per movie library and tv recs
            # once per tv library, independently (not a movie x tv cross
            # product) - each library gets its own discovery pass and its
            # own real library_id. Entries must stay media-type-pure (never
            # mix a movie library's id with a tv library's id on one entry)
            # so #157 Phase 2's library_id-keyed Sonarr/Radarr grouping can't
            # misroute - so even a media type with exactly one library (the
            # *other* type is what triggered fan-out) gets its own scoped
            # run here rather than reusing the combined process_user().
            for username in users:
                try:
                    movie_runs = []
                    for library in movie_libraries:
                        data = process_user_movie_library(config, plex, username, library)
                        if data:
                            arr_export_data.append(data)
                            movie_runs.append(data)

                    tv_runs = []
                    for library in tv_libraries:
                        data = process_user_tv_library(config, plex, username, library)
                        if data:
                            arr_export_data.append(data)
                            tv_runs.append(data)

                    if movie_runs or tv_runs:
                        all_users_data.append(_merge_user_runs(username, movie_runs, tv_runs))
                    record_run_status(log_dir, "external", username, True)
                except Exception as e:
                    log_error(f"Error processing {username}: {e}")
                    traceback.print_exc()
                    record_run_status(log_dir, "external", username, False, str(e))

        # Build shared counts: how many users want each item
        total_users = len(all_users_data)

        for user_data in all_users_data:
            # Count movies across all categories
            for category in ["user_services", "other_services"]:
                for service_items in user_data.get("movies_categorized", {}).get(category, {}).values():
                    for item in service_items:
                        tmdb_id = str(item.get("tmdb_id"))
                        movie_counts[tmdb_id] = movie_counts.get(tmdb_id, 0) + 1
            for item in user_data.get("movies_categorized", {}).get("acquire", []):
                tmdb_id = str(item.get("tmdb_id"))
                movie_counts[tmdb_id] = movie_counts.get(tmdb_id, 0) + 1
            # Count shows across all categories
            for category in ["user_services", "other_services"]:
                for service_items in user_data.get("shows_categorized", {}).get(category, {}).values():
                    for item in service_items:
                        tmdb_id = str(item.get("tmdb_id"))
                        show_counts[tmdb_id] = show_counts.get(tmdb_id, 0) + 1
            for item in user_data.get("shows_categorized", {}).get("acquire", []):
                tmdb_id = str(item.get("tmdb_id"))
                show_counts[tmdb_id] = show_counts.get(tmdb_id, 0) + 1

    # Huntarr: Find missing sequels and upcoming movies
    missing_sequels = []
    horizon_movies = []
    movie_library = config["plex"].get("movie_library", "Movies")
    tv_library = config["plex"].get("tv_library", "TV Shows")
    user_services = config.get("streaming_services", [])
    stale_days = config.get("collections", {}).get("stale_removal_days", 7)

    if run_sequel_huntarr:
        print(f"\n{CYAN}=== Sequel Huntarr: Finding Missing Collection Movies ==={RESET}")
        missing_sequels = find_missing_sequels(tmdb_api_key, plex, movie_library, tv_library, user_services, stale_days)

    if run_horizon_huntarr:
        print(f"\n{CYAN}=== Horizon Huntarr: Finding Upcoming Collection Movies ==={RESET}")
        horizon_movies = find_horizon_movies(tmdb_api_key, plex, movie_library, stale_days)

    # Generate combined HTML with all users
    output_dir = os.path.join(project_root, "recommendations", "external")

    if all_users_data or missing_sequels or horizon_movies:
        html_file = generate_combined_html(
            all_users_data,
            output_dir,
            tmdb_api_key,
            get_imdb_id,
            movie_counts=movie_counts,
            show_counts=show_counts,
            total_users=total_users,
            missing_sequels=missing_sequels,
            horizon_movies=horizon_movies,
        )
        print(f"{GREEN}Watchlist generated!{RESET}")
    else:
        html_file = None
        print(f"{YELLOW}No data to generate watchlist{RESET}")

    print(f"Watchlists saved to: {output_dir}")
    if html_file:
        file_url = f"file://{html_file}"
        print(f"\nView watchlist: {clickable_link(file_url)}")

    # Auto-open HTML if enabled
    external_config = config.get("external_recommendations", {})
    if external_config.get("auto_open_html", False) and html_file:
        smart_open_html(html_file)

    # Export to external services (if configured and auto_sync enabled).
    # Sonarr/Radarr route by library_id (#157 Phase 2/3.5) so they get the
    # per-library arr_export_data; Trakt/MDBList/Simkl have no per-library
    # routing concept so they get the merged-per-user all_users_data (see
    # the arr_export_data/all_users_data docstring above).
    if all_users_data and run_recommendations:
        print(f"\n{GREEN}=== Checking External Service Exports ==={RESET}")
        export_to_trakt(config, all_users_data, tmdb_api_key)
        export_to_sonarr(config, arr_export_data, tmdb_api_key)
        export_to_radarr(config, arr_export_data, tmdb_api_key)
        export_to_mdblist(config, all_users_data, tmdb_api_key)
        export_to_simkl(config, all_users_data, tmdb_api_key)


if __name__ == "__main__":
    main()
