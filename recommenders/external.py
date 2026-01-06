#!/usr/bin/env python3
"""
Generate external recommendations - content NOT in your Plex library
Creates per-user markdown watchlists that update daily and auto-remove acquired items
"""

import os
import sys
import webbrowser
import logging

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import math
import requests
import traceback
import urllib3
from datetime import datetime
from collections import Counter
from typing import Dict, List, Set, Optional, Tuple, Any
from plexapi.server import PlexServer
from plexapi.myplex import MyPlexAccount

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Module-level logger
logger = logging.getLogger('curatarr')

# Import shared utilities - same as internal recommenders
from utils import (
    RED, GREEN, YELLOW, CYAN, RESET,
    RATING_MULTIPLIERS, GENRE_NORMALIZATION,
    get_plex_account_ids, get_tmdb_config, get_tmdb_keywords,
    fetch_watch_history_with_tmdb,
    log_warning, log_error, load_config, clickable_link,
    calculate_rewatch_multiplier, calculate_recency_multiplier,
    calculate_similarity_score, normalize_genre, fuzzy_keyword_match,
    load_imdb_tmdb_cache, save_imdb_tmdb_cache, get_tmdb_id_from_imdb,
    load_trakt_enhance_cache, save_trakt_enhance_cache,
    get_trakt_discovery_candidates,
    enhance_profile_with_trakt,
    fetch_tmdb_details_for_profile,
    get_project_root,
    get_authenticated_trakt_client,
)

# Import output generation
from recommenders.external_output import generate_markdown, generate_combined_html, SERVICE_DISPLAY_NAMES

# Import export functions
from recommenders.external_exports import (
    export_to_trakt,
    export_to_sonarr,
    export_to_radarr,
    export_to_mdblist,
    export_to_simkl,
    sync_watch_history_to_trakt,
    get_imdb_id,
)

# TMDB Genre ID mappings
TMDB_MOVIE_GENRES = {
    28: 'Action', 12: 'Adventure', 16: 'Animation', 35: 'Comedy', 80: 'Crime',
    99: 'Documentary', 18: 'Drama', 10751: 'Family', 14: 'Fantasy', 36: 'History',
    27: 'Horror', 10402: 'Music', 9648: 'Mystery', 10749: 'Romance', 878: 'Science Fiction',
    10770: 'TV Movie', 53: 'Thriller', 10752: 'War', 37: 'Western'
}

TMDB_TV_GENRES = {
    10759: 'Action & Adventure', 16: 'Animation', 35: 'Comedy', 80: 'Crime', 99: 'Documentary',
    18: 'Drama', 10751: 'Family', 10762: 'Kids', 9648: 'Mystery', 10763: 'News',
    10764: 'Reality', 10765: 'Sci-Fi & Fantasy', 10766: 'Soap', 10767: 'Talk',
    10768: 'War & Politics', 37: 'Western'
}

# TMDB Watch Provider ID mappings (US region)
TMDB_PROVIDERS = {
    8: 'netflix',
    15: 'hulu',
    337: 'disney_plus',
    9: 'amazon_prime',
    531: 'paramount_plus',
    350: 'apple_tv_plus',
    384: 'max',
    387: 'peacock',
    1899: 'max',  # HBO Max (legacy ID)
    203: 'crunchyroll',
    283: 'crackle',
    613: 'tubi',
    207: 'mubi',
    619: 'shudder'
}

# Reverse TMDB genre mappings (name to ID) for Discover API
TMDB_MOVIE_GENRE_IDS = {v.lower(): k for k, v in TMDB_MOVIE_GENRES.items()}
TMDB_TV_GENRE_IDS = {v.lower(): k for k, v in TMDB_TV_GENRES.items()}

# Discovery thresholds - cast a wide net to find candidates
DISCOVER_MIN_RATING = 5.0       # Low bar - just filter out garbage
DISCOVER_MIN_VOTES = 50         # Enough to know it's a real film
MAX_CANDIDATES = 1500           # Bigger pool = more chances for great matches
DISCOVER_RESULTS_PER_GENRE = 40     # Top N results per genre search
DISCOVER_TOP_KEYWORDS = 10          # Number of top keywords to search
DISCOVER_RESULTS_PER_KEYWORD = 15   # Top N results per keyword search

# Output thresholds - match score is king, rating is just tiebreaker
OUTPUT_MIN_SCORE = 0.65         # 65%+ match required - this is what matters
OUTPUT_MIN_VOTES = 50           # Filters garbage TMDB entries, profile score is quality signal

# Iterative discovery settings
MAX_DISCOVERY_ITERATIONS = 5    # How many discovery passes before giving up
THRESHOLD_FLOOR = 0.25          # Minimum threshold for last-ditch iteration

# Legacy aliases for cache filtering (votes only, no rating gate)
MIN_RATING = 0.0                # Don't filter by rating in cache
MIN_VOTE_COUNT = OUTPUT_MIN_VOTES
SCORE_CHANGE_THRESHOLD = 0.01   # Minimum score change to log during updates
PROGRESS_UPDATE_FREQUENCY = 50  # Show progress every N items

# Default weights (specificity-first approach - same as internal recommenders)
# Director/language reduced - most people don't care about director, language data unreliable
DEFAULT_WEIGHTS = {
    'genre': 0.25,
    'director': 0.05,  # movies - low weight
    'studio': 0.10,    # TV shows
    'actor': 0.20,
    'keyword': 0.50,   # Primary driver - most specific signal
    'language': 0.0    # Disabled - data unreliable
}


def discover_candidates_by_profile(
    tmdb_api_key: str,
    user_profile: Dict,
    library_data: Dict,
    media_type: str = 'movie',
    max_candidates: int = 500,
    iteration: int = 0,
    exclude_ids: Optional[Set[int]] = None,
    top_scored_items: Optional[List[Dict]] = None
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
        print(f"  Discovering candidates via TMDB Discover API...")
    else:
        print(f"  Discovery iteration {iteration + 1}: expanding search...")

    candidates = {}  # tmdb_id -> basic info
    media = 'movie' if media_type == 'movie' else 'tv'

    # Get genres for this iteration's range
    all_genres = list(user_profile['genres'].most_common(20))
    top_genres = all_genres[genre_start:genre_end]
    genre_id_map = TMDB_MOVIE_GENRE_IDS if media_type == 'movie' else TMDB_TV_GENRE_IDS

    # Get keywords for this iteration's range
    all_keywords = list(user_profile['keywords'].most_common(40))
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
            params = {
                'api_key': tmdb_api_key,
                'with_genres': genre_id,
                'vote_average.gte': DISCOVER_MIN_RATING,
                'vote_count.gte': DISCOVER_MIN_VOTES,
                'sort_by': 'vote_average.desc',
                'page': page
            }
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                results = response.json().get('results', [])
                for item in results[:DISCOVER_RESULTS_PER_GENRE]:
                    tmdb_id = item['id']
                    title = item.get('title') or item.get('name')
                    year = (item.get('release_date') or item.get('first_air_date', ''))[:4]

                    # Skip if already seen, in library, or excluded
                    if tmdb_id in candidates or tmdb_id in exclude_ids:
                        continue
                    if is_in_library(tmdb_id, title, year, library_data):
                        continue

                    candidates[tmdb_id] = {
                        'tmdb_id': tmdb_id,
                        'title': title,
                        'year': year,
                        'rating': item.get('vote_average', 0),
                        'vote_count': item.get('vote_count', 0)
                    }

        except (requests.RequestException, KeyError) as e:
            log_warning(f"Error discovering by genre {genre_name}: {e}")

    genre_count = len(candidates)

    # Search by keywords for this iteration's range
    for keyword, _ in top_keywords:
        if len(candidates) >= max_candidates:
            break

        try:
            # Search for keyword ID first
            url = "https://api.themoviedb.org/3/search/keyword"
            response = requests.get(url, params={'api_key': tmdb_api_key, 'query': keyword}, timeout=10)

            if response.status_code == 200:
                kw_results = response.json().get('results', [])
                if kw_results:
                    kw_id = kw_results[0]['id']

                    # Discover by keyword
                    url = f"https://api.themoviedb.org/3/discover/{media}"
                    params = {
                        'api_key': tmdb_api_key,
                        'with_keywords': kw_id,
                        'vote_average.gte': DISCOVER_MIN_RATING,
                        'vote_count.gte': DISCOVER_MIN_VOTES,
                        'sort_by': 'vote_average.desc',
                        'page': page
                    }
                    response = requests.get(url, params=params, timeout=10)

                    if response.status_code == 200:
                        results = response.json().get('results', [])
                        for item in results[:DISCOVER_RESULTS_PER_KEYWORD]:
                            tmdb_id = item['id']
                            title = item.get('title') or item.get('name')
                            year = (item.get('release_date') or item.get('first_air_date', ''))[:4]

                            if tmdb_id in candidates or tmdb_id in exclude_ids:
                                continue
                            if is_in_library(tmdb_id, title, year, library_data):
                                continue

                            candidates[tmdb_id] = {
                                'tmdb_id': tmdb_id,
                                'title': title,
                                'year': year,
                                'rating': item.get('vote_average', 0),
                                'vote_count': item.get('vote_count', 0)
                            }

        except (requests.RequestException, KeyError) as e:
            log_warning(f"Error discovering by keyword {keyword}: {e}")

    keyword_count = len(candidates) - genre_count

    # On iteration 2+, add similar-to queries for top scored items
    similar_count = 0
    if iteration >= 2 and top_scored_items:
        for item in top_scored_items[:5]:  # Top 5 high-scorers
            similar = fetch_similar_from_tmdb(
                tmdb_api_key,
                item['tmdb_id'],
                media_type,
                library_data,
                exclude_ids.union(set(candidates.keys()))
            )
            for sim_id, sim_item in similar.items():
                if sim_id not in candidates and sim_id not in exclude_ids:
                    candidates[sim_id] = sim_item
                    similar_count += 1

    print(f"    Iteration {iteration + 1}: {genre_count} from genres, {keyword_count} from keywords, {similar_count} from similar")
    return candidates


def load_user_profile_from_cache(config: Dict, username: str, media_type: str = 'movie') -> Optional[Dict]:
    """
    Load user profile from the watched cache (pre-computed by internal recommenders).
    This is MUCH faster than rebuilding from API calls.

    Returns:
        dict: Weighted counters for genres, actors, directors/studios, keywords, languages
        None: If cache not found or invalid
    """
    cache_dir = config.get('cache_dir', 'cache')

    # Cache file naming matches internal recommenders
    if media_type == 'movie':
        cache_file = os.path.join(cache_dir, f"watched_cache_plex_{username}.json")
    else:
        cache_file = os.path.join(cache_dir, f"tv_watched_cache_plex_{username}.json")

    if not os.path.exists(cache_file):
        print(f"  No watched cache found for {username} ({media_type}), will build from scratch")
        return None

    try:
        with open(cache_file, 'r') as f:
            cache_data = json.load(f)

        wdc = cache_data.get('watched_data_counters', {})
        if not wdc:
            print(f"  Empty watched_data_counters in cache for {username}")
            return None

        # Convert to Counter format expected by scoring
        # Note: cache uses 'tmdb_keywords' and 'studio' (singular for TV)
        profile = {
            'genres': Counter(wdc.get('genres', {})),
            'directors': Counter(wdc.get('directors', {})),
            'studios': Counter(wdc.get('studios', wdc.get('studio', {}))),  # Handle both singular and plural
            'actors': Counter(wdc.get('actors', {})),
            'keywords': Counter(wdc.get('tmdb_keywords', {})),
            'languages': Counter(wdc.get('languages', {})),
            'tmdb_ids': set(wdc.get('tmdb_ids', []))
        }

        watched_count = cache_data.get('watched_count', len(profile['genres']))
        print(f"  {GREEN}Loaded {media_type} profile from cache: {watched_count} watched, {len(profile['keywords'])} keywords{RESET}")

        return profile

    except Exception as e:
        log_warning(f"Error loading cache for {username}: {e}")
        return None


def build_user_profile(plex: Any, config: Dict, username: str, media_type: str = 'movie') -> Dict:
    """
    Build weighted user profile from ALL watch history.
    Uses same weighting as internal recommenders: ratings + rewatches + recency.

    NOTE: This is slow! Use load_user_profile_from_cache() first when possible.

    Returns:
        dict: Weighted counters for genres, actors, directors/studios, keywords, languages
    """
    library_name = config['plex'].get('movie_library' if media_type == 'movie' else 'tv_library')
    library = plex.library.section(library_name)
    all_items = library.all()
    total_items = len(all_items)
    print(f"Building {media_type} profile for {username} ({total_items} items to scan)...")

    # Get recency config
    recency_config = config.get('recency_decay', {})
    recency_enabled = recency_config.get('enabled', True)

    counters = {
        'genres': Counter(),
        'directors': Counter(),  # movies
        'studios': Counter(),    # TV shows
        'actors': Counter(),
        'keywords': Counter(),
        'languages': Counter(),
        'tmdb_ids': set()
    }

    # Get account for user checking
    account = MyPlexAccount(token=config['plex']['token'])
    tmdb_api_key = get_tmdb_config(config)['api_key']

    watched_count = 0

    for i, item in enumerate(all_items, 1):
        # Show progress periodically or at the end
        if i % PROGRESS_UPDATE_FREQUENCY == 0 or i == total_items:
            print(f"\r  Scanning library: {i}/{total_items} ({int(i/total_items*100)}%)", end="", flush=True)

        if not item.isWatched:
            continue

        watched_count += 1

        # Get view count for rewatch multiplier
        view_count = getattr(item, 'viewCount', 1) or 1
        rewatch_mult = calculate_rewatch_multiplier(view_count)

        # Get last viewed date for recency multiplier
        recency_mult = 1.0
        if recency_enabled and hasattr(item, 'lastViewedAt') and item.lastViewedAt:
            # Plex returns datetime object, convert to timestamp for calculate_recency_multiplier
            last_viewed = item.lastViewedAt
            if hasattr(last_viewed, 'timestamp'):
                last_viewed = int(last_viewed.timestamp())
            recency_mult = calculate_recency_multiplier(last_viewed, recency_config)

        # Get user rating (convert 1-10 to multiplier)
        user_rating = getattr(item, 'userRating', None)
        if user_rating:
            rating_int = max(0, min(10, int(round(user_rating))))
            rating_mult = RATING_MULTIPLIERS.get(rating_int, 1.0)
        else:
            # Use audience rating as fallback, but with less weight
            audience_rating = getattr(item, 'audienceRating', 5.0) or 5.0
            rating_int = max(0, min(10, int(round(audience_rating))))
            rating_mult = RATING_MULTIPLIERS.get(rating_int, 1.0) * 0.5  # Half weight for audience rating

        # Combined multiplier
        multiplier = rewatch_mult * recency_mult * rating_mult

        # Extract attributes from Plex item
        for genre in item.genres:
            counters['genres'][genre.tag] += multiplier

        if media_type == 'movie':
            for director in getattr(item, 'directors', []):
                counters['directors'][director.tag] += multiplier
        else:
            if hasattr(item, 'studio') and item.studio:
                counters['studios'][item.studio.lower()] += multiplier

        # Top 3 actors
        for actor in list(getattr(item, 'roles', []))[:3]:
            counters['actors'][actor.tag] += multiplier

        # Get TMDB keywords (need to fetch from TMDB)
        tmdb_id = None
        for guid in item.guids:
            if 'tmdb://' in guid.id:
                try:
                    tmdb_id = int(guid.id.split('tmdb://')[1])
                    counters['tmdb_ids'].add(tmdb_id)
                    break
                except (ValueError, IndexError) as e:
                    logger.debug(f"Error parsing TMDB ID from guid {guid.id}: {e}")

        if tmdb_id:
            keywords = get_tmdb_keywords(tmdb_api_key, tmdb_id, media_type)
            for keyword in keywords:
                counters['keywords'][keyword] += multiplier

    print()  # Newline after progress indicator
    print(f"  Found {watched_count} watched {media_type}s")
    print(f"  Top genres: {dict(counters['genres'].most_common(5))}")

    return counters


def flatten_categorized_items(categorized: Dict) -> List[Dict]:
    """
    Flatten categorized items into a single list.

    Args:
        categorized: Dict with 'user_services', 'other_services', 'acquire' keys

    Returns:
        List of all items from all categories
    """
    items = []
    for service_items in categorized.get('user_services', {}).values():
        items.extend(service_items)
    for service_items in categorized.get('other_services', {}).values():
        items.extend(service_items)
    items.extend(categorized.get('acquire', []))
    return items


def get_library_items(plex: Any, library_name: str, media_type: str = 'movie') -> Dict[str, Set]:
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
            year = getattr(item, 'year', None)
            titles.add((title_lower, year))

            for guid in item.guids:
                if 'tmdb://' in guid.id:
                    try:
                        tmdb_id = guid.id.split('tmdb://')[1]
                        tmdb_ids.add(int(tmdb_id))
                    except (ValueError, IndexError) as e:
                        logger.debug(f"Error parsing TMDB ID from guid {guid.id}: {e}")
                elif 'tvdb://' in guid.id:
                    try:
                        tvdb_id = guid.id.split('tvdb://')[1]
                        tvdb_ids.add(int(tvdb_id))
                    except (ValueError, IndexError) as e:
                        logger.debug(f"Error parsing TVDB ID from guid {guid.id}: {e}")

        return {'tmdb_ids': tmdb_ids, 'tvdb_ids': tvdb_ids, 'titles': titles}
    except Exception as e:
        log_warning(f"Warning: Could not fetch {library_name} library: {e}")
        return {'tmdb_ids': set(), 'tvdb_ids': set(), 'titles': set()}


def get_watch_providers(tmdb_api_key: str, tmdb_id: int, media_type: str = 'movie') -> List[str]:
    """
    Get streaming providers for a TMDB item (US region)
    Returns list of service names (e.g., ['netflix', 'hulu'])
    """
    try:
        url = f"https://api.themoviedb.org/3/{'movie' if media_type == 'movie' else 'tv'}/{tmdb_id}/watch/providers"
        params = {'api_key': tmdb_api_key}
        response = requests.get(url, params=params, timeout=10)

        if response.status_code != 200:
            return []

        data = response.json()

        # Get US providers (flatrate = subscription streaming)
        us_providers = data.get('results', {}).get('US', {})
        flatrate_providers = us_providers.get('flatrate', [])

        # Map provider IDs to service names
        services = []
        for provider in flatrate_providers:
            provider_id = provider.get('provider_id')
            if provider_id in TMDB_PROVIDERS:
                service_name = TMDB_PROVIDERS[provider_id]
                if service_name not in services:  # Avoid duplicates
                    services.append(service_name)

        return services
    except Exception as e:
        logger.debug(f"Error fetching watch providers for TMDB {tmdb_id}: {e}")
        return []


def fetch_similar_from_tmdb(
    tmdb_api_key: str,
    tmdb_id: int,
    media_type: str,
    library_data: Dict,
    exclude_ids: Optional[Set[int]] = None
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
    media = 'movie' if media_type == 'movie' else 'tv'

    try:
        url = f"https://api.themoviedb.org/3/{media}/{tmdb_id}/similar"
        params = {
            'api_key': tmdb_api_key,
            'page': 1
        }
        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 200:
            results = response.json().get('results', [])
            for item in results[:20]:  # Top 20 similar items
                item_id = item['id']
                title = item.get('title') or item.get('name')
                year = (item.get('release_date') or item.get('first_air_date', ''))[:4]
                vote_count = item.get('vote_count', 0)

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
                    'tmdb_id': item_id,
                    'title': title,
                    'year': year,
                    'rating': item.get('vote_average', 0),
                    'vote_count': vote_count
                }

    except (requests.RequestException, KeyError) as e:
        logger.debug(f"Error fetching similar for TMDB {tmdb_id}: {e}")

    return candidates


def categorize_by_streaming_service(
    recommendations: List[Dict],
    tmdb_api_key: str,
    user_services: List[str],
    media_type: str = 'movie'
) -> Dict:
    """
    Categorize recommendations by streaming availability
    Returns dict: {
        'user_services': {service_name: [items]},
        'other_services': {service_name: [items]},
        'acquire': [items]
    }
    """
    result = {
        'user_services': {},
        'other_services': {},
        'acquire': []
    }

    for item in recommendations:
        tmdb_id = item['tmdb_id']
        providers = get_watch_providers(tmdb_api_key, tmdb_id, media_type)

        if not providers:
            # Not available on any streaming service
            result['acquire'].append(item)
        else:
            # Check which services have it - add to FIRST matching service only
            # Priority: user's services first, then other services
            placed = False

            # First try user's services
            for service in providers:
                if service in user_services:
                    if service not in result['user_services']:
                        result['user_services'][service] = []
                    result['user_services'][service].append(item)
                    placed = True
                    break  # Only add to ONE service

            # If not on user's services, add to first other service
            if not placed:
                for service in providers:
                    if service not in user_services:
                        if service not in result['other_services']:
                            result['other_services'][service] = []
                        result['other_services'][service].append(item)
                        break  # Only add to ONE service

    return result

def get_genre_distribution(plex: Any, config: Dict, username: str, media_type: str = 'movie') -> Tuple[Dict, int]:
    """Calculate genre distribution from user's watch history"""
    try:
        library_name = config['plex'].get('movie_library' if media_type == 'movie' else 'tv_library')
        library = plex.library.section(library_name)

        genre_counts = {}
        total_items = 0

        # For admin user, check watched items directly
        account = MyPlexAccount(token=config['plex']['token'])
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

def get_user_watch_history(plex: Any, config: Dict, username: str, media_type: str = 'movie') -> List[Dict]:
    """Get user's watch history from Plex using shared utility"""
    print(f"Fetching {media_type} watch history for {username}...")

    try:
        # Get library
        library_name = config['plex'].get('movie_library' if media_type == 'movie' else 'tv_library')
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

def balance_genres_proportionally(
    recommendations: List[Dict],
    genre_distribution: Dict,
    limit: int,
    media_type: str = 'movie'
) -> List[Dict]:
    """
    Balance recommendations to match user's genre distribution from watch history
    Prevents any single genre from dominating the list
    """
    if not genre_distribution or not recommendations:
        return recommendations[:limit]

    genre_map = TMDB_MOVIE_GENRES if media_type == 'movie' else TMDB_TV_GENRES

    # Calculate target counts for each genre
    genre_targets = {}
    for genre_name, percentage in genre_distribution.items():
        target_count = int(limit * percentage)
        # Ensure at least 1 slot for genres that exist in history
        if target_count == 0 and percentage > 0:
            target_count = 1
        genre_targets[genre_name] = target_count

    # Track how many of each genre we've added
    genre_counts = {genre: 0 for genre in genre_targets}

    balanced_recs = []
    remaining_recs = []

    # First pass: add items up to their genre targets
    for rec in recommendations:
        if len(balanced_recs) >= limit:
            break

        # Get primary genre (first genre_id)
        primary_genre_id = rec['genre_ids'][0] if rec['genre_ids'] else None
        primary_genre = genre_map.get(primary_genre_id, 'Unknown')

        # Check if this genre is under its target
        if primary_genre in genre_targets and genre_counts[primary_genre] < genre_targets[primary_genre]:
            balanced_recs.append(rec)
            genre_counts[primary_genre] += 1
        else:
            remaining_recs.append(rec)

    # Second pass: fill remaining slots with best-scored items regardless of genre
    remaining_needed = limit - len(balanced_recs)
    if remaining_needed > 0:
        balanced_recs.extend(remaining_recs[:remaining_needed])

    print(f"Genre balancing: {len(balanced_recs)} items selected")
    for genre, count in sorted(genre_counts.items(), key=lambda x: x[1], reverse=True):
        if count > 0:
            target = genre_targets.get(genre, 0)
            actual_pct = (count / len(balanced_recs) * 100) if balanced_recs else 0
            target_pct = genre_distribution.get(genre, 0) * 100
            print(f"  {genre}: {count} items ({actual_pct:.1f}% actual vs {target_pct:.1f}% target)")

    return balanced_recs

def is_in_library(tmdb_id: Optional[int], title: Optional[str], year: Optional[str], library_data: Dict) -> bool:
    """Check if item is in library by TMDB ID or title+year"""
    # Check TMDB ID first
    if tmdb_id and tmdb_id in library_data.get('tmdb_ids', set()):
        return True

    # Fallback: check by title+year
    if title:
        title_lower = title.lower().strip()
        year_int = int(year) if year and str(year).isdigit() else None
        # Check exact match
        if (title_lower, year_int) in library_data.get('titles', set()):
            return True
        # Check without year (some shows don't have year in Plex)
        for lib_title, lib_year in library_data.get('titles', set()):
            if lib_title == title_lower:
                return True

    return False

def find_similar_content_with_profile(
    tmdb_api_key: str,
    user_profile: Dict,
    library_data: Dict,
    media_type: str = 'movie',
    limit: int = 50,
    exclude_genres: Optional[List[str]] = None,
    min_relevance_score: float = 0.65,
    config: Optional[Dict] = None,
    exclude_imdb_ids: Optional[Set[str]] = None,
    max_iterations: Optional[int] = None,
    exclude_cached_ids: Optional[Set[int]] = None
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

    if not user_profile or not user_profile.get('genres'):
        print(f"{YELLOW}No user profile data found{RESET}")
        return []

    # Get iteration settings from config (can be overridden by parameter)
    external_config = config.get('external_recommendations', {}) if config else {}
    if max_iterations is None:
        max_iterations = external_config.get('max_iterations', MAX_DISCOVERY_ITERATIONS)
    min_votes = external_config.get('min_votes', OUTPUT_MIN_VOTES)

    # Get weights from config or use defaults
    weights = DEFAULT_WEIGHTS
    if config:
        config_weights = config.get('movies' if media_type == 'movie' else 'tv', {}).get('weights', {})
        if config_weights:
            weights = {
                'genre': config_weights.get('genre', 0.20),
                'director': config_weights.get('director', 0.15),
                'studio': config_weights.get('studio', 0.15),
                'actor': config_weights.get('actor', 0.15),
                'keyword': config_weights.get('keyword', 0.45),
                'language': config_weights.get('language', 0.05)
            }

    # Track state across iterations
    quality_recs = []  # Items meeting quality bar
    seen_ids = set(exclude_cached_ids or set())  # Include cached IDs to skip
    scored_cache = {}  # tmdb_id -> scored item (avoid re-scoring)

    # Get Trakt candidates once (not per-iteration)
    trakt_candidates = {}
    if config:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cache_dir = os.path.join(project_root, config.get('cache_dir', 'cache'))
        library_tmdb_ids = library_data.get('tmdb_ids', set())

        trakt_candidates = get_trakt_discovery_candidates(
            config,
            media_type,
            cache_dir,
            library_tmdb_ids,
            exclude_imdb_ids
        )

    # Iterative discovery loop
    for iteration in range(max_iterations):
        # Check if we've hit the target
        if len(quality_recs) >= limit:
            print(f"  {GREEN}Target of {limit} reached after {iteration} iteration(s){RESET}")
            break

        # Progressive threshold relaxation: drop 10% each iteration after iter 2, floor at iter 5
        if iteration < 2:
            iteration_threshold = min_relevance_score
        elif iteration == max_iterations - 1:  # Last iteration - drop to floor
            iteration_threshold = THRESHOLD_FLOOR
        else:
            # Iterations 2, 3, etc: drop 10% each
            drops = iteration - 1
            iteration_threshold = max(min_relevance_score - (drops * 0.10), THRESHOLD_FLOOR)

        # Discover candidates for this iteration
        candidates = discover_candidates_by_profile(
            tmdb_api_key,
            user_profile,
            library_data,
            media_type,
            max_candidates=MAX_CANDIDATES,
            iteration=iteration,
            exclude_ids=seen_ids,
            top_scored_items=quality_recs[:10]  # Pass top items for similar-to queries
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
                    content_genres = [g.lower() for g in details.get('genres', [])]
                    if any(eg.lower() in content_genres for eg in exclude_genres):
                        continue

                # Check if on Trakt watchlist (exclude if IMDB ID matches)
                if exclude_imdb_ids:
                    imdb_id = get_imdb_id(tmdb_api_key, candidate_id, media_type)
                    if imdb_id and imdb_id in exclude_imdb_ids:
                        continue

                # Calculate similarity score
                content_info = {
                    'genres': details.get('genres', []),
                    'directors': details.get('directors', []),
                    'studios': details.get('studios', []),
                    'cast': details.get('cast', []),
                    'language': details.get('language', ''),
                    'keywords': details.get('keywords', [])
                }
                score, _ = calculate_similarity_score(content_info, user_profile, media_type, weights)

                scored_item = {
                    'tmdb_id': candidate_id,
                    'title': details['title'],
                    'year': details['year'],
                    'rating': details['rating'],
                    'vote_count': details.get('vote_count', 0),
                    'score': score,
                    'overview': details.get('overview', ''),
                    'genres': details.get('genres', []),
                    'genre_ids': []
                }
                scored_cache[candidate_id] = scored_item

                # Check if meets quality bar (threshold relaxes in later iterations)
                if score >= iteration_threshold and scored_item['vote_count'] >= min_votes:
                    quality_recs.append(scored_item)
                    new_quality_this_iteration += 1

            print()  # newline after progress

        # Re-sort quality_recs after adding new items
        quality_recs.sort(key=lambda x: (x['score'], x['rating']), reverse=True)

        print(f"  {CYAN}Iteration {iteration + 1} ({iteration_threshold:.0%} threshold): {new_quality_this_iteration} new quality items, {len(quality_recs)} total{RESET}")

    print(f"  {GREEN}{len(quality_recs)} items meet quality bar (>={min_votes} votes){RESET}")

    # Take quality items only - no backfill with low-quality
    final_recs = quality_recs[:limit]

    if final_recs:
        print(f"  Top recommendation: {final_recs[0]['title']} ({final_recs[0]['score']:.1%})")

    return final_recs


def load_cache(display_name: str, media_type: str) -> Dict:
    """Load existing recommendations cache, filtering out items below quality thresholds"""
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    safe_name = display_name.lower().replace(' ', '_')
    cache_file = os.path.join(cache_dir, f'external_recs_{safe_name}_{media_type}.json')

    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            cache = json.load(f)
            # Add tmdb_id to items that don't have it (backwards compatibility)
            for tmdb_id_str, item in cache.items():
                if 'tmdb_id' not in item:
                    item['tmdb_id'] = int(tmdb_id_str)

            # Filter out items without enough votes (match score filtering happens at output)
            filtered = {}
            removed_count = 0
            for tmdb_id_str, item in cache.items():
                vote_count = item.get('vote_count', 0)  # Missing vote_count = needs re-fetch
                if vote_count >= MIN_VOTE_COUNT:
                    filtered[tmdb_id_str] = item
                else:
                    removed_count += 1

            if removed_count > 0:
                print(f"  Filtered {removed_count} cached items with < {MIN_VOTE_COUNT} votes")

            return filtered
    return {}

def save_cache(display_name: str, media_type: str, cache_data: Dict) -> None:
    """Save recommendations cache"""
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    safe_name = display_name.lower().replace(' ', '_')
    cache_file = os.path.join(cache_dir, f'external_recs_{safe_name}_{media_type}.json')

    with open(cache_file, 'w') as f:
        json.dump(cache_data, f, indent=2)

def load_ignore_list(display_name: str) -> Set[str]:
    """Load user's manual ignore list"""
    safe_name = display_name.lower().replace(' ', '_')
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ignore_file = os.path.join(project_root, 'recommendations', 'external', f'{safe_name}_ignore.txt')
    if os.path.exists(ignore_file):
        with open(ignore_file, 'r') as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def process_user(config, plex, username):
    """Process external recommendations for a single user"""
    user_prefs = config.get('users', {}).get('preferences', {}).get(username, {})
    display_name = user_prefs.get('display_name', username)

    print(f"\n{GREEN}Processing external recommendations for: {display_name}{RESET}")

    # Get current library contents
    movie_library = config['plex'].get('movie_library', 'Movies')
    tv_library = config['plex'].get('tv_library', 'TV Shows')

    library_movies = get_library_items(plex, movie_library, 'movie')
    library_shows = get_library_items(plex, tv_library, 'show')

    print(f"{CYAN}Library has {len(library_movies['titles'])} movies, {len(library_shows['titles'])} TV shows{RESET}")

    # Load existing cache and ignore list
    movie_cache = load_cache(display_name, 'movies')
    show_cache = load_cache(display_name, 'shows')
    ignore_list = load_ignore_list(display_name)

    # Remove acquired items from cache (now in library) - check TMDB IDs AND titles
    removed_movies = []
    for tmdb_id, item in list(movie_cache.items()):
        if is_in_library(int(tmdb_id), item.get('title'), item.get('year'), library_movies):
            removed_movies.append(tmdb_id)
            del movie_cache[tmdb_id]
            print(f"  Removed movie from cache: {item.get('title')} (in library)")

    removed_shows = []
    for tmdb_id, item in list(show_cache.items()):
        if is_in_library(int(tmdb_id), item.get('title'), item.get('year'), library_shows):
            removed_shows.append(tmdb_id)
            del show_cache[tmdb_id]
            print(f"  Removed show from cache: {item.get('title')} (in library)")

    if removed_movies or removed_shows:
        print(f"{GREEN}Removed {len(removed_movies)} movies and {len(removed_shows)} shows (now in library){RESET}")

    # Remove ignored items
    removed_ignored = 0
    for tmdb_id, item in list(movie_cache.items()):
        if item['title'] in ignore_list:
            del movie_cache[tmdb_id]
            removed_ignored += 1
    for tmdb_id, item in list(show_cache.items()):
        if item['title'] in ignore_list:
            del show_cache[tmdb_id]
            removed_ignored += 1

    if removed_ignored:
        print(f"{YELLOW}Removed {removed_ignored} ignored items{RESET}")

    # Remove stale items (on list too long without being acquired)
    stale_days = config.get('collections', {}).get('stale_removal_days', 7)
    now = datetime.now()
    stale_removed = 0

    for tmdb_id, item in list(movie_cache.items()):
        if 'added_date' in item:
            added = datetime.fromisoformat(item['added_date'])
            if (now - added).days > stale_days:
                del movie_cache[tmdb_id]
                stale_removed += 1

    for tmdb_id, item in list(show_cache.items()):
        if 'added_date' in item:
            added = datetime.fromisoformat(item['added_date'])
            if (now - added).days > stale_days:
                del show_cache[tmdb_id]
                stale_removed += 1

    if stale_removed:
        print(f"{YELLOW}Removed {stale_removed} stale items (>{stale_days} days on list){RESET}")

    # Load user profiles from cache (FAST) or build from scratch (SLOW)
    # Cache is pre-computed by internal recommenders with proper weighting
    movie_profile = load_user_profile_from_cache(config, username, 'movie')
    if not movie_profile:
        movie_profile = build_user_profile(plex, config, username, 'movie')

    show_profile = load_user_profile_from_cache(config, username, 'tv')
    if not show_profile:
        show_profile = build_user_profile(plex, config, username, 'show')

    # Enhance profiles with Trakt watch history (streaming services not in Plex)
    # Only for users in the Trakt mapping
    tmdb_api_key = get_tmdb_config(config)['api_key']
    trakt_config = config.get('trakt', {})
    export_config = trakt_config.get('export', {})
    user_mode = export_config.get('user_mode', 'mapping')
    plex_users = export_config.get('plex_users', [])

    should_enhance = True
    if user_mode == 'mapping' and plex_users:
        plex_users_lower = [u.lower() for u in plex_users]
        if username.lower() not in plex_users_lower:
            should_enhance = False

    if should_enhance:
        cache_dir = os.path.join(get_project_root(), config.get('cache_dir', 'cache'))
        if movie_profile:
            movie_profile = enhance_profile_with_trakt(movie_profile, config, tmdb_api_key, cache_dir, 'movie')
        if show_profile:
            show_profile = enhance_profile_with_trakt(show_profile, config, tmdb_api_key, cache_dir, 'tv')

    # Find new recommendations using profile-based scoring
    external_config = config.get('external_recommendations', {})
    movie_limit = external_config.get('movie_limit', 50)
    show_limit = external_config.get('show_limit', 20)
    min_relevance = external_config.get('min_relevance_score', 0.65)

    # Get excluded genres for this user
    exclude_genres = user_prefs.get('exclude_genres', [])
    if exclude_genres:
        print(f"Excluding genres: {', '.join(exclude_genres)}")

    # Check cache health and calculate deficit
    quality_movies = [m for m in movie_cache.values() if m.get('score', 0) >= min_relevance]
    quality_shows = [s for s in show_cache.values() if s.get('score', 0) >= min_relevance]

    movie_deficit = max(0, movie_limit - len(quality_movies))
    show_deficit = max(0, show_limit - len(quality_shows))

    # Collect cached TMDB IDs for exclusion (avoids re-scoring existing items)
    cached_movie_ids = {int(tid) for tid in movie_cache.keys()}
    cached_show_ids = {int(tid) for tid in show_cache.keys()}

    # Get Trakt watchlist exclusions if enabled (only if we need discovery)
    trakt_config = config.get('trakt', {})
    import_config = trakt_config.get('import', {})
    exclude_movie_imdb_ids = set()
    exclude_show_imdb_ids = set()

    if (movie_deficit > 0 or show_deficit > 0) and import_config.get('exclude_watchlist', True):
        trakt_client = get_authenticated_trakt_client(config)
        if trakt_client:
            print("Loading Trakt watchlist for exclusion...")
            exclude_movie_imdb_ids = trakt_client.get_watchlist_imdb_ids('movies')
            exclude_show_imdb_ids = trakt_client.get_watchlist_imdb_ids('shows')
            if exclude_movie_imdb_ids or exclude_show_imdb_ids:
                print(f"Excluding {len(exclude_movie_imdb_ids)} movies, {len(exclude_show_imdb_ids)} shows from Trakt watchlist")

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
            'movie',
            limit=movie_deficit,  # Only find what we need
            exclude_genres=exclude_genres,
            min_relevance_score=min_relevance,
            config=config,
            exclude_imdb_ids=exclude_movie_imdb_ids,
            exclude_cached_ids=cached_movie_ids  # Skip items already in cache
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
            'tv',
            limit=show_deficit,  # Only find what we need
            exclude_genres=exclude_genres,
            min_relevance_score=min_relevance,
            config=config,
            exclude_imdb_ids=exclude_show_imdb_ids,
            exclude_cached_ids=cached_show_ids  # Skip items already in cache
        )

    # Merge with existing cache - UPDATE scores for existing items, ADD new ones
    for movie in new_movies:
        tmdb_id = str(movie['tmdb_id'])
        if tmdb_id in movie_cache:
            # Update score for existing item (profile may have changed)
            old_score = movie_cache[tmdb_id].get('score', 0)
            movie_cache[tmdb_id]['score'] = movie['score']
            movie_cache[tmdb_id]['rating'] = movie['rating']
            movie_cache[tmdb_id]['vote_count'] = movie.get('vote_count', 0)
            if abs(movie['score'] - old_score) > SCORE_CHANGE_THRESHOLD:
                print(f"    Updated score: {movie['title']} {old_score:.1%} -> {movie['score']:.1%}")
        else:
            # Add new item
            movie_cache[tmdb_id] = {
                'tmdb_id': movie['tmdb_id'],
                'title': movie['title'],
                'year': movie['year'],
                'rating': movie['rating'],
                'vote_count': movie.get('vote_count', 0),
                'score': movie['score'],
                'added_date': datetime.now().isoformat()
            }

    for show in new_shows:
        tmdb_id = str(show['tmdb_id'])
        if tmdb_id in show_cache:
            # Update score for existing item (profile may have changed)
            old_score = show_cache[tmdb_id].get('score', 0)
            show_cache[tmdb_id]['score'] = show['score']
            show_cache[tmdb_id]['rating'] = show['rating']
            show_cache[tmdb_id]['vote_count'] = show.get('vote_count', 0)
            if abs(show['score'] - old_score) > SCORE_CHANGE_THRESHOLD:
                print(f"    Updated score: {show['title']} {old_score:.1%} -> {show['score']:.1%}")
        else:
            # Add new item
            show_cache[tmdb_id] = {
                'tmdb_id': show['tmdb_id'],
                'title': show['title'],
                'year': show['year'],
                'rating': show['rating'],
                'vote_count': show.get('vote_count', 0),
                'score': show['score'],
                'added_date': datetime.now().isoformat()
            }

    # Save updated caches
    save_cache(display_name, 'movies', movie_cache)
    save_cache(display_name, 'shows', show_cache)

    # Prepare lists for categorization - apply threshold and limits
    all_movies = sorted(movie_cache.values(), key=lambda x: x['score'], reverse=True)
    all_shows = sorted(show_cache.values(), key=lambda x: x['score'], reverse=True)

    # Filter by relevance threshold - prioritize high-score items
    high_movies = [m for m in all_movies if m['score'] >= min_relevance]
    low_movies = [m for m in all_movies if m['score'] < min_relevance]
    high_shows = [s for s in all_shows if s['score'] >= min_relevance]
    low_shows = [s for s in all_shows if s['score'] < min_relevance]

    # Take high-score items first, backfill with low-score only if needed
    movies_list = high_movies[:movie_limit]
    if len(movies_list) < movie_limit:
        movies_list.extend(low_movies[:movie_limit - len(movies_list)])

    shows_list = high_shows[:show_limit]
    if len(shows_list) < show_limit:
        shows_list.extend(low_shows[:show_limit - len(shows_list)])

    print(f"{GREEN}Output: {len(movies_list)} movies ({len(high_movies)} above {int(min_relevance*100)}% threshold){RESET}")
    print(f"{GREEN}Output: {len(shows_list)} shows ({len(high_shows)} above {int(min_relevance*100)}% threshold){RESET}")

    # Get household streaming services from top-level config
    user_services = config.get('streaming_services', [])

    # Categorize by streaming service availability
    print(f"{CYAN}Categorizing by streaming service availability...{RESET}")
    movies_categorized = categorize_by_streaming_service(
        movies_list,
        tmdb_api_key,
        user_services,
        'movie'
    )
    shows_categorized = categorize_by_streaming_service(
        shows_list,
        tmdb_api_key,
        user_services,
        'tv'
    )

    # Generate markdown per user
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = os.path.join(project_root, 'recommendations', 'external')
    generate_markdown(username, display_name, movies_categorized, shows_categorized, output_dir)

    # Count totals
    total_movies = sum(len(items) for items in movies_categorized['user_services'].values()) + \
                   sum(len(items) for items in movies_categorized['other_services'].values()) + \
                   len(movies_categorized['acquire'])
    total_shows = sum(len(items) for items in shows_categorized['user_services'].values()) + \
                  sum(len(items) for items in shows_categorized['other_services'].values()) + \
                  len(shows_categorized['acquire'])

    print(f"{GREEN}Processed: {total_movies} movies, {total_shows} shows{RESET}")
    print(f"\nExternal recommendation process completed for {display_name}!")

    # Return data for combined HTML generation and Trakt sync
    return {
        'username': username,
        'display_name': display_name,
        'movies_categorized': movies_categorized,
        'shows_categorized': shows_categorized,
        'movie_profile': movie_profile,
        'show_profile': show_profile
    }

def main():
    print(f"\n{GREEN}=== External Recommendations Generator ==={RESET}")

    # Load config from project root (one level up from recommenders/)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, 'config/config.yml')
    config = load_config(config_path)

    # Get TMDB API key
    tmdb_api_key = get_tmdb_config(config)['api_key']

    # Get users
    users = [u.strip() for u in config['users']['list'].split(',')]

    # Note: Trakt sync happens in run.sh BEFORE recommenders run
    # This ensures both internal and external recommenders benefit

    # Connect to Plex
    try:
        plex = PlexServer(config['plex']['url'], config['plex']['token'])
        print(f"{GREEN}Connected to Plex{RESET}")
    except Exception as e:
        log_error(f"Error connecting to Plex: {e}")
        sys.exit(1)

    # Process each user and collect data for combined HTML
    all_users_data = []
    for username in users:
        try:
            user_data = process_user(config, plex, username)
            if user_data:
                all_users_data.append(user_data)
        except Exception as e:
            log_error(f"Error processing {username}: {e}")
            traceback.print_exc()

    # Build shared counts: how many users want each item
    movie_counts = {}  # tmdb_id -> count
    show_counts = {}
    total_users = len(all_users_data)

    for user_data in all_users_data:
        # Count movies across all categories
        for category in ['user_services', 'other_services']:
            for service_items in user_data.get('movies_categorized', {}).get(category, {}).values():
                for item in service_items:
                    tmdb_id = str(item.get('tmdb_id'))
                    movie_counts[tmdb_id] = movie_counts.get(tmdb_id, 0) + 1
        for item in user_data.get('movies_categorized', {}).get('acquire', []):
            tmdb_id = str(item.get('tmdb_id'))
            movie_counts[tmdb_id] = movie_counts.get(tmdb_id, 0) + 1
        # Count shows across all categories
        for category in ['user_services', 'other_services']:
            for service_items in user_data.get('shows_categorized', {}).get(category, {}).values():
                for item in service_items:
                    tmdb_id = str(item.get('tmdb_id'))
                    show_counts[tmdb_id] = show_counts.get(tmdb_id, 0) + 1
        for item in user_data.get('shows_categorized', {}).get('acquire', []):
            tmdb_id = str(item.get('tmdb_id'))
            show_counts[tmdb_id] = show_counts.get(tmdb_id, 0) + 1

    # Generate combined HTML with all users
    output_dir = os.path.join(project_root, 'recommendations', 'external')

    if all_users_data:
        html_file = generate_combined_html(
            all_users_data, output_dir, tmdb_api_key, get_imdb_id,
            movie_counts=movie_counts, show_counts=show_counts, total_users=total_users
        )
        print(f"{GREEN}Combined watchlist generated!{RESET}")
    else:
        html_file = None
        print(f"{YELLOW}No user data to generate watchlist{RESET}")

    print(f"Watchlists saved to: {output_dir}")
    if html_file:
        file_url = f"file://{html_file}"
        print(f"\nView watchlist: {clickable_link(file_url)}")

    # Auto-open HTML if enabled
    external_config = config.get('external_recommendations', {})
    if external_config.get('auto_open_html', False) and html_file:
        print("Opening watchlist in browser...")
        webbrowser.open(f'file://{html_file}')

    # Export to external services (if configured and auto_sync enabled)
    if all_users_data:
        print(f"\n{GREEN}=== Checking External Service Exports ==={RESET}")
        export_to_trakt(config, all_users_data, tmdb_api_key)
        export_to_sonarr(config, all_users_data, tmdb_api_key)
        export_to_radarr(config, all_users_data, tmdb_api_key)
        export_to_mdblist(config, all_users_data, tmdb_api_key)
        export_to_simkl(config, all_users_data, tmdb_api_key)


if __name__ == "__main__":
    main()
