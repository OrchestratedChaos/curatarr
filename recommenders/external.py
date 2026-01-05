#!/usr/bin/env python3
"""
Generate external recommendations - content NOT in your Plex library
Creates per-user markdown watchlists that update daily and auto-remove acquired items
"""

import os
import sys
import webbrowser

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
import json
import math
import requests
import urllib3
from datetime import datetime
from collections import Counter
from plexapi.server import PlexServer
from plexapi.myplex import MyPlexAccount

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Import shared utilities - same as internal recommenders
from utils import (
    RED, GREEN, YELLOW, CYAN, RESET,
    RATING_MULTIPLIERS, GENRE_NORMALIZATION,
    get_plex_account_ids, get_tmdb_config, get_tmdb_keywords,
    fetch_watch_history_with_tmdb,
    print_user_header, print_user_footer, print_status,
    log_warning, log_error, load_config, clickable_link,
    calculate_rewatch_multiplier, calculate_recency_multiplier,
    calculate_similarity_score, normalize_genre, fuzzy_keyword_match,
    create_trakt_client, get_authenticated_trakt_client, TraktAPIError, TraktAuthError,
    load_imdb_tmdb_cache, save_imdb_tmdb_cache, get_tmdb_id_from_imdb,
    load_trakt_enhance_cache, save_trakt_enhance_cache,
)

# Import output generation
from recommenders.external_output import generate_markdown, generate_combined_html, SERVICE_DISPLAY_NAMES

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

# Output thresholds - match score is king, rating is just tiebreaker
OUTPUT_MIN_SCORE = 0.65         # 65%+ match required - this is what matters
OUTPUT_MIN_VOTES = 200          # Enough votes to be reliable

# Legacy aliases for cache filtering (votes only, no rating gate)
MIN_RATING = 0.0                # Don't filter by rating in cache
MIN_VOTE_COUNT = OUTPUT_MIN_VOTES
SCORE_CHANGE_THRESHOLD = 0.01  # Minimum score change to log during updates

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


def discover_candidates_by_profile(tmdb_api_key, user_profile, library_data, media_type='movie', max_candidates=500):
    """
    Discover candidates using TMDB Discover API based on user profile.
    Searches by top genres and keywords for higher quality matches.
    """
    print(f"  Discovering candidates via TMDB Discover API...")

    candidates = {}  # tmdb_id -> basic info
    media = 'movie' if media_type == 'movie' else 'tv'

    # Get top genres from profile
    top_genres = list(user_profile['genres'].most_common(10))  # More genres = wider net
    genre_id_map = TMDB_MOVIE_GENRE_IDS if media_type == 'movie' else TMDB_TV_GENRE_IDS

    # Get top keywords from profile
    top_keywords = list(user_profile['keywords'].most_common(10))

    # Search by top genres
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
                'page': 1
            }
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                results = response.json().get('results', [])
                for item in results[:40]:  # Top 40 per genre - wider net
                    tmdb_id = item['id']
                    title = item.get('title') or item.get('name')
                    year = (item.get('release_date') or item.get('first_air_date', ''))[:4]

                    # Skip if in library or already discovered
                    if tmdb_id in candidates:
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

        except (requests.RequestException, KeyError):
            pass

    print(f"    Found {len(candidates)} candidates from genre search")

    # Also search by top keywords using search API
    for keyword, _ in top_keywords[:10]:  # Top 10 keywords - wider net
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
                        'page': 1
                    }
                    response = requests.get(url, params=params, timeout=10)

                    if response.status_code == 200:
                        results = response.json().get('results', [])
                        for item in results[:15]:  # Top 15 per keyword
                            tmdb_id = item['id']
                            title = item.get('title') or item.get('name')
                            year = (item.get('release_date') or item.get('first_air_date', ''))[:4]

                            if tmdb_id in candidates:
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

        except (requests.RequestException, KeyError):
            pass

    print(f"    Total candidates after keyword search: {len(candidates)}")
    return candidates


def load_user_profile_from_cache(config, username, media_type='movie'):
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
        print(f"  Loaded {media_type} profile from cache: {watched_count} watched, {len(profile['keywords'])} keywords")

        return profile

    except Exception as e:
        print(f"  Error loading cache for {username}: {e}")
        return None


def build_user_profile(plex, config, username, media_type='movie'):
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
        # Show progress every 50 items or at the end
        if i % 50 == 0 or i == total_items:
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
                except (ValueError, IndexError):
                    pass

        if tmdb_id:
            keywords = get_tmdb_keywords(tmdb_api_key, tmdb_id, media_type)
            for keyword in keywords:
                counters['keywords'][keyword] += multiplier

        # Language
        if hasattr(item, 'originallyAvailableAt'):
            # Try to get language from TMDB
            pass  # We'll get it from TMDB metadata if needed

    print()  # Newline after progress indicator
    print(f"  Found {watched_count} watched {media_type}s")
    print(f"  Top genres: {dict(counters['genres'].most_common(5))}")

    return counters


def get_tmdb_details(tmdb_api_key, tmdb_id, media_type='movie'):
    """Fetch full details from TMDB for scoring."""
    try:
        media = 'movie' if media_type == 'movie' else 'tv'
        url = f"https://api.themoviedb.org/3/{media}/{tmdb_id}"
        params = {'api_key': tmdb_api_key, 'append_to_response': 'keywords,credits'}
        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()

            # Extract genres
            genres = [g['name'] for g in data.get('genres', [])]

            # Extract keywords
            kw_data = data.get('keywords', {})
            keywords_list = kw_data.get('keywords', kw_data.get('results', []))
            keywords = [kw['name'] for kw in keywords_list[:10]]

            # Extract cast (top 5)
            credits = data.get('credits', {})
            cast = [c['name'] for c in credits.get('cast', [])[:5]]

            # Extract directors (movies) or created_by (TV)
            directors = []
            if media_type == 'movie':
                crew = credits.get('crew', [])
                directors = [c['name'] for c in crew if c.get('job') == 'Director']

            # Studio/Network
            studios = []
            if media_type == 'movie':
                studios = [c['name'] for c in data.get('production_companies', [])[:2]]
            else:
                studios = [n['name'] for n in data.get('networks', [])[:2]]

            # Language
            language = data.get('original_language', '')

            return {
                'genres': genres,
                'keywords': keywords,
                'cast': cast,
                'directors': directors,
                'studios': studios,
                'language': language,
                'title': data.get('title') or data.get('name'),
                'year': (data.get('release_date') or data.get('first_air_date', ''))[:4],
                'rating': data.get('vote_average', 0),
                'vote_count': data.get('vote_count', 0),
                'overview': data.get('overview', '')
            }
    except Exception as e:
        pass
    return None


def enhance_profile_with_trakt(profile, config, tmdb_api_key, media_type='movie'):
    """
    Enhance user profile with Trakt watch history.

    Fetches Trakt watch history for items not already in the profile (from streaming services)
    and adds their genres, keywords, cast, etc. to build a more complete taste profile.

    Args:
        profile: Existing profile dict with Counter objects
        config: Full config dict with Trakt settings
        tmdb_api_key: TMDB API key for fetching details
        media_type: 'movie' or 'tv'

    Returns:
        Enhanced profile (same dict, modified in place)
    """
    trakt_config = config.get('trakt', {})
    import_config = trakt_config.get('import', {})

    # Check if Trakt import is enabled
    if not trakt_config.get('enabled', False):
        return profile
    if not import_config.get('enabled', True):
        return profile
    # Check if watch history merging is enabled
    if not import_config.get('merge_watch_history', True):
        return profile

    # Get authenticated Trakt client
    trakt_client = get_authenticated_trakt_client(config)
    if not trakt_client:
        return profile

    print(f"  Enhancing {media_type} profile with Trakt watch history...")

    # Get Trakt watch history
    sys.stdout.write(f"    Fetching Trakt {media_type} history...")
    sys.stdout.flush()
    if media_type == 'movie':
        watched = trakt_client.get_watched_movies()
    else:
        watched = trakt_client.get_watched_shows()

    if not watched:
        print(f"\r    No Trakt {media_type} history found      ")
        return profile

    # Extract all IMDB IDs from Trakt response
    media_key = 'movie' if media_type == 'movie' else 'show'
    current_imdb_ids = set()
    for item in watched:
        imdb_id = item.get(media_key, {}).get('ids', {}).get('imdb')
        if imdb_id:
            current_imdb_ids.add(imdb_id)

    # Load cached IDs to check for changes
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(project_root, config.get('cache_dir', 'cache'))
    enhance_cache = load_trakt_enhance_cache(cache_dir)
    cache_key = 'movie_ids' if media_type == 'movie' else 'show_ids'
    cached_ids = enhance_cache.get(cache_key, set())

    # Check if anything changed
    new_ids = current_imdb_ids - cached_ids
    if not new_ids:
        print(f"\r    Trakt {media_type}s unchanged ({len(current_imdb_ids)} items) - skipping")
        return profile

    print(f"\r    Found {len(new_ids)} new Trakt {media_type}s to process")

    # Get existing TMDB IDs from profile to avoid duplicates
    existing_tmdb_ids = profile.get('tmdb_ids', set())

    # Load IMDB→TMDB cache for fast lookups
    imdb_cache = load_imdb_tmdb_cache(cache_dir)
    initial_cache_size = len(imdb_cache)

    # Process only new Trakt watched items
    added_count = 0
    total = len(new_ids)
    for i, imdb_id in enumerate(new_ids, 1):
        # Show progress
        pct = int((i / total) * 100)
        sys.stdout.write(f"\r    Processing new Trakt items {i}/{total} ({pct}%) - {added_count} added")
        sys.stdout.flush()

        # Convert IMDB to TMDB (uses cache)
        tmdb_id = get_tmdb_id_from_imdb(tmdb_api_key, imdb_id, media_type, imdb_cache)
        if not tmdb_id or tmdb_id in existing_tmdb_ids:
            continue  # Skip if already in profile

        # Fetch TMDB details
        details = get_tmdb_details(tmdb_api_key, tmdb_id, media_type)
        if not details:
            continue

        # Add to profile with base weight (no rating data from Trakt history API)
        # Could enhance with Trakt ratings API if needed
        weight = 1.0

        for genre in details.get('genres', []):
            profile['genres'][genre] += weight
        for actor in details.get('cast', [])[:3]:  # Top 3 actors
            profile['actors'][actor] += weight
        for keyword in details.get('keywords', []):
            profile['keywords'][keyword] += weight

        if media_type == 'movie':
            for director in details.get('directors', []):
                profile['directors'][director] += weight
        else:
            for studio in details.get('studios', []):
                profile['studios'][studio.lower()] += weight

        profile['tmdb_ids'].add(tmdb_id)
        added_count += 1

    # Save caches
    if len(imdb_cache) > initial_cache_size:
        save_imdb_tmdb_cache(cache_dir, imdb_cache)

    # Update enhance cache with all current IDs
    if media_type == 'movie':
        save_trakt_enhance_cache(cache_dir, current_imdb_ids, enhance_cache.get('show_ids', set()))
    else:
        save_trakt_enhance_cache(cache_dir, enhance_cache.get('movie_ids', set()), current_imdb_ids)

    # Final summary
    print(f"\r    Processing new Trakt items {total}/{total} (100%) - {added_count} added")

    return profile


def flatten_categorized_items(categorized):
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


def collect_imdb_ids(categorized, tmdb_api_key, media_type='movie'):
    """
    Collect IMDB IDs from categorized items.

    Args:
        categorized: Dict with categorized items
        tmdb_api_key: TMDB API key for ID lookups
        media_type: 'movie' or 'tv'

    Returns:
        List of IMDB IDs
    """
    items = flatten_categorized_items(categorized)
    imdb_ids = []
    for item in items:
        tmdb_id = item.get('tmdb_id')
        if tmdb_id:
            imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, media_type)
            if imdb_id:
                imdb_ids.append(imdb_id)
    return imdb_ids


def get_library_items(plex, library_name, media_type='movie'):
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
                    except (ValueError, IndexError):
                        pass
                elif 'tvdb://' in guid.id:
                    try:
                        tvdb_id = guid.id.split('tvdb://')[1]
                        tvdb_ids.add(int(tvdb_id))
                    except (ValueError, IndexError):
                        pass

        return {'tmdb_ids': tmdb_ids, 'tvdb_ids': tvdb_ids, 'titles': titles}
    except Exception as e:
        log_warning(f"Warning: Could not fetch {library_name} library: {e}")
        return {'tmdb_ids': set(), 'tvdb_ids': set(), 'titles': set()}

def get_imdb_id(tmdb_api_key, tmdb_id, media_type='movie'):
    """Fetch IMDB ID from TMDB external IDs endpoint."""
    try:
        media = 'movie' if media_type == 'movie' else 'tv'
        url = f"https://api.themoviedb.org/3/{media}/{tmdb_id}/external_ids"
        response = requests.get(url, params={'api_key': tmdb_api_key}, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get('imdb_id')
    except (requests.RequestException, KeyError):
        pass
    return None


def get_watch_providers(tmdb_api_key, tmdb_id, media_type='movie'):
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
        # Silently fail for individual items - don't spam logs
        return []

def categorize_by_streaming_service(recommendations, tmdb_api_key, user_services, media_type='movie'):
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
            # Check which services have it
            user_has_it = False
            for service in providers:
                if service in user_services:
                    # Available on user's service
                    if service not in result['user_services']:
                        result['user_services'][service] = []
                    result['user_services'][service].append(item)
                    user_has_it = True
                else:
                    # Available on other service
                    if service not in result['other_services']:
                        result['other_services'][service] = []
                    result['other_services'][service].append(item)

            # If not on any of user's services, it's in other_services only
            # (already handled above)

    return result

def get_genre_distribution(plex, config, username, media_type='movie'):
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

def get_user_watch_history(plex, config, username, media_type='movie'):
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

def balance_genres_proportionally(recommendations, genre_distribution, limit, media_type='movie'):
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

def is_in_library(tmdb_id, title, year, library_data):
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

def find_similar_content_with_profile(tmdb_api_key, user_profile, library_data, media_type='movie', limit=50, exclude_genres=None, min_relevance_score=0.25, config=None, exclude_imdb_ids=None):
    """
    Find similar content NOT in library using profile-based scoring.
    Uses TMDB Discover API for quality candidates + profile-based scoring.

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

    Returns:
        List of scored recommendations
    """
    if exclude_imdb_ids is None:
        exclude_imdb_ids = set()
    print(f"Finding external {media_type}s using profile-based scoring...")

    if not user_profile or not user_profile.get('genres'):
        print_status("No user profile data found", "warning")
        return []

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

    # Use TMDB Discover API to find quality candidates based on profile
    # This replaces the old approach of crawling recommendations from watched items
    candidates = discover_candidates_by_profile(
        tmdb_api_key,
        user_profile,
        library_data,
        media_type,
        max_candidates=MAX_CANDIDATES
    )

    if not candidates:
        print_status("No candidates found", "warning")
        return []

    print(f"  Found {len(candidates)} candidates (discovery: rating >= {DISCOVER_MIN_RATING}, votes >= {DISCOVER_MIN_VOTES})")

    # Now score each candidate using profile-based similarity
    scored_recommendations = []
    candidate_list = list(candidates.keys())

    total_candidates = len(candidate_list)
    print(f"  Scoring {total_candidates} candidates against user profile...")
    for i, candidate_id in enumerate(candidate_list, 1):
        if i % 50 == 0 or i == total_candidates:
            print(f"\r    Scored {i}/{total_candidates} candidates...", end="", flush=True)

        # Fetch full details from TMDB
        details = get_tmdb_details(tmdb_api_key, candidate_id, media_type)
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

        # Calculate similarity score using shared function
        # Build content_info in the format expected by calculate_similarity_score
        content_info = {
            'genres': details.get('genres', []),
            'directors': details.get('directors', []),
            'studios': details.get('studios', []),
            'cast': details.get('cast', []),
            'language': details.get('language', ''),
            'keywords': details.get('keywords', [])
        }
        score, _ = calculate_similarity_score(content_info, user_profile, media_type, weights)

        scored_recommendations.append({
            'tmdb_id': candidate_id,
            'title': details['title'],
            'year': details['year'],
            'rating': details['rating'],
            'vote_count': details.get('vote_count', 0),
            'score': score,
            'overview': details.get('overview', ''),
            'genres': details.get('genres', []),
            'genre_ids': []  # For compatibility with genre balancing
        })

    print()  # newline after progress

    # Sort by score (highest first), then by rating as tiebreaker
    scored_recommendations.sort(key=lambda x: (x['score'], x['rating']), reverse=True)

    # Apply output filtering - match score is king, votes just validates it's real
    quality_recs = [
        r for r in scored_recommendations
        if r['score'] >= OUTPUT_MIN_SCORE
        and r.get('vote_count', 0) >= OUTPUT_MIN_VOTES
    ]

    print(f"  {len(quality_recs)} items meet quality bar (>={int(OUTPUT_MIN_SCORE*100)}% match, >={OUTPUT_MIN_VOTES} votes)")

    # Take quality items only - no backfill with low-quality
    final_recs = quality_recs[:limit]

    if final_recs:
        print(f"  Top recommendation: {final_recs[0]['title']} ({final_recs[0]['score']:.1%})")

    return final_recs


def load_cache(display_name, media_type):
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

def save_cache(display_name, media_type, cache_data):
    """Save recommendations cache"""
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    safe_name = display_name.lower().replace(' ', '_')
    cache_file = os.path.join(cache_dir, f'external_recs_{safe_name}_{media_type}.json')

    with open(cache_file, 'w') as f:
        json.dump(cache_data, f, indent=2)

def load_ignore_list(display_name):
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
    user_prefs = config['users']['preferences'].get(username, {})
    display_name = user_prefs.get('display_name', username)

    print_user_header(f"{display_name} (external recommendations)")

    # Get current library contents
    movie_library = config['plex'].get('movie_library', 'Movies')
    tv_library = config['plex'].get('tv_library', 'TV Shows')

    library_movies = get_library_items(plex, movie_library, 'movie')
    library_shows = get_library_items(plex, tv_library, 'show')

    print(f"Library has {len(library_movies['titles'])} movies, {len(library_shows['titles'])} TV shows")

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
        print_status(f"Removed {len(removed_movies)} movies and {len(removed_shows)} shows (now in library)", "success")

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
        print_status(f"Removed {removed_ignored} ignored items", "warning")

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
        if movie_profile:
            movie_profile = enhance_profile_with_trakt(movie_profile, config, tmdb_api_key, 'movie')
        if show_profile:
            show_profile = enhance_profile_with_trakt(show_profile, config, tmdb_api_key, 'tv')

    # Find new recommendations using profile-based scoring
    external_config = config.get('external_recommendations', {})
    movie_limit = external_config.get('movie_limit', 30)
    show_limit = external_config.get('show_limit', 20)
    min_relevance = external_config.get('min_relevance_score', 0.25)

    # Get excluded genres for this user
    exclude_genres = user_prefs.get('exclude_genres', [])
    if exclude_genres:
        print(f"Excluding genres: {', '.join(exclude_genres)}")

    # Get Trakt watchlist exclusions if enabled
    trakt_config = config.get('trakt', {})
    import_config = trakt_config.get('import', {})
    exclude_movie_imdb_ids = set()
    exclude_show_imdb_ids = set()

    if import_config.get('exclude_watchlist', True):
        trakt_client = get_authenticated_trakt_client(config)
        if trakt_client:
            print("Loading Trakt watchlist for exclusion...")
            exclude_movie_imdb_ids = trakt_client.get_watchlist_imdb_ids('movies')
            exclude_show_imdb_ids = trakt_client.get_watchlist_imdb_ids('shows')
            if exclude_movie_imdb_ids or exclude_show_imdb_ids:
                print_status(f"Excluding {len(exclude_movie_imdb_ids)} movies, {len(exclude_show_imdb_ids)} shows from Trakt watchlist", "info")

    new_movies = find_similar_content_with_profile(
        tmdb_api_key,
        movie_profile,
        library_movies,
        'movie',
        limit=movie_limit,
        exclude_genres=exclude_genres,
        min_relevance_score=min_relevance,
        config=config,
        exclude_imdb_ids=exclude_movie_imdb_ids
    )

    new_shows = find_similar_content_with_profile(
        tmdb_api_key,
        show_profile,
        library_shows,
        'tv',
        limit=show_limit,
        exclude_genres=exclude_genres,
        min_relevance_score=min_relevance,
        config=config,
        exclude_imdb_ids=exclude_show_imdb_ids
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

    print(f"Output: {len(movies_list)} movies ({len(high_movies)} above {int(min_relevance*100)}% threshold)")
    print(f"Output: {len(shows_list)} shows ({len(high_shows)} above {int(min_relevance*100)}% threshold)")

    # Get household streaming services from top-level config
    user_services = config.get('streaming_services', [])

    # Categorize by streaming service availability
    print("Categorizing by streaming service availability...")
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

    print_status(f"Processed: {total_movies} movies, {total_shows} shows", "success")
    print_user_footer(f"{display_name} (external recommendations)")

    # Return data for combined HTML generation and Trakt sync
    return {
        'username': username,
        'display_name': display_name,
        'movies_categorized': movies_categorized,
        'shows_categorized': shows_categorized,
        'movie_profile': movie_profile,
        'show_profile': show_profile
    }

def export_to_trakt(config, all_users_data, tmdb_api_key):
    """
    Export recommendations to Trakt lists.

    Creates/updates lists named: "{prefix} - {username} - Movies/TV"

    Config options:
        trakt.enabled: Master switch for Trakt integration
        trakt.export.enabled: Enable export feature (default: true)
        trakt.export.auto_sync: Auto-sync on each run (default: true)
        trakt.export.user_mode: How to handle multiple Plex users:
            - mapping: Only export users in plex_users list (recommended)
            - per_user: Separate list for each Plex user
            - combined: All users combined into one list
        trakt.export.plex_users: List of Plex usernames to export (for mapping mode)
    """
    trakt_config = config.get('trakt', {})
    export_config = trakt_config.get('export', {})

    # Check if export is enabled
    if not trakt_config.get('enabled', False):
        return
    if not export_config.get('enabled', True):
        return
    # Check if auto-sync is enabled (can still manually export via HTML)
    if not export_config.get('auto_sync', True):
        return

    # Get authenticated Trakt client
    trakt_client = get_authenticated_trakt_client(config)
    if not trakt_client:
        log_warning("Trakt not authenticated - run setup wizard to authenticate")
        return

    list_prefix = export_config.get('list_prefix', 'Curatarr')
    trakt_username = trakt_client.get_username()
    user_mode = export_config.get('user_mode', 'mapping')
    plex_users = export_config.get('plex_users', [])

    # Safety check: mapping mode requires explicit plex_users configuration
    if user_mode == 'mapping':
        # Reject empty list, placeholder, or unconfigured
        invalid_configs = [[], ['YourPlexUsername'], None]
        if plex_users in invalid_configs or not plex_users:
            log_warning(
                "Trakt export: No plex_users configured.\n"
                "  Edit config.yml -> trakt.export.plex_users and add YOUR Plex username.\n"
                "  Example: plex_users: [\"jason\"]\n"
                "  This prevents accidentally syncing other users' data to YOUR Trakt account."
            )
            return

    print(f"\n{CYAN}Exporting to Trakt...{RESET}")

    # Filter users based on mode
    if user_mode == 'mapping':
        # Only export users in the plex_users list (case-insensitive)
        plex_users_lower = [u.lower() for u in plex_users]
        users_to_export = [
            u for u in all_users_data
            if u['username'].lower() in plex_users_lower
        ]
        if not users_to_export:
            log_warning(
                f"Trakt export: No matching users found. Configured plex_users: {plex_users}\n"
                "  Check that your Plex username matches exactly."
            )
            return
    else:
        users_to_export = all_users_data

    # Handle combined mode - merge all users into one list
    if user_mode == 'combined':
        all_movie_imdb_ids = []
        all_show_imdb_ids = []
        for user_data in users_to_export:
            all_movie_imdb_ids.extend(
                collect_imdb_ids(user_data['movies_categorized'], tmdb_api_key, 'movie')
            )
            all_show_imdb_ids.extend(
                collect_imdb_ids(user_data['shows_categorized'], tmdb_api_key, 'tv')
            )
        # Deduplicate
        all_movie_imdb_ids = list(dict.fromkeys(all_movie_imdb_ids))
        all_show_imdb_ids = list(dict.fromkeys(all_show_imdb_ids))

        try:
            if all_movie_imdb_ids:
                movie_list_name = f"{list_prefix} - Movies"
                trakt_client.sync_list(
                    movie_list_name,
                    movies=all_movie_imdb_ids,
                    description="Combined movie recommendations from Curatarr"
                )
                movie_slug = movie_list_name.lower().replace(" ", "-").replace("_", "-")
                movie_url = f"https://trakt.tv/users/{trakt_username}/lists/{movie_slug}"
                print_status(f"  Combined: {len(all_movie_imdb_ids)} movies -> Trakt", "success")
                print(f"    {clickable_link(movie_url)}")

            if all_show_imdb_ids:
                show_list_name = f"{list_prefix} - TV"
                trakt_client.sync_list(
                    show_list_name,
                    shows=all_show_imdb_ids,
                    description="Combined TV recommendations from Curatarr"
                )
                show_slug = show_list_name.lower().replace(" ", "-").replace("_", "-")
                show_url = f"https://trakt.tv/users/{trakt_username}/lists/{show_slug}"
                print_status(f"  Combined: {len(all_show_imdb_ids)} shows -> Trakt", "success")
                print(f"    {clickable_link(show_url)}")

        except (TraktAPIError, TraktAuthError) as e:
            log_error(f"Failed to export combined list to Trakt: {e}")
        return

    # Per-user or mapping mode - separate list per user
    for user_data in users_to_export:
        display_name = user_data['display_name']
        movies_categorized = user_data['movies_categorized']
        shows_categorized = user_data['shows_categorized']

        # Collect IMDB IDs using helper
        movie_imdb_ids = collect_imdb_ids(movies_categorized, tmdb_api_key, 'movie')
        show_imdb_ids = collect_imdb_ids(shows_categorized, tmdb_api_key, 'tv')

        # Sync to Trakt lists
        try:
            if movie_imdb_ids:
                movie_list_name = f"{list_prefix} - {display_name} - Movies"
                trakt_client.sync_list(
                    movie_list_name,
                    movies=movie_imdb_ids,
                    description=f"Movie recommendations for {display_name} from Curatarr"
                )
                movie_slug = movie_list_name.lower().replace(" ", "-").replace("_", "-")
                movie_url = f"https://trakt.tv/users/{trakt_username}/lists/{movie_slug}"
                print_status(f"  {display_name}: {len(movie_imdb_ids)} movies -> Trakt", "success")
                print(f"    {clickable_link(movie_url)}")

            if show_imdb_ids:
                show_list_name = f"{list_prefix} - {display_name} - TV"
                trakt_client.sync_list(
                    show_list_name,
                    shows=show_imdb_ids,
                    description=f"TV recommendations for {display_name} from Curatarr"
                )
                show_slug = show_list_name.lower().replace(" ", "-").replace("_", "-")
                show_url = f"https://trakt.tv/users/{trakt_username}/lists/{show_slug}"
                print_status(f"  {display_name}: {len(show_imdb_ids)} shows -> Trakt", "success")
                print(f"    {clickable_link(show_url)}")

        except (TraktAPIError, TraktAuthError) as e:
            log_error(f"Failed to export {display_name} to Trakt: {e}")


def sync_watch_history_to_trakt(config, tmdb_api_key, users=None):
    """
    Sync Plex watch history to Trakt.

    Loads watched TMDB IDs from cache files, converts to IMDB IDs,
    and marks them as watched on Trakt.

    This should run BEFORE processing users so Trakt data is available
    for profile enhancement.

    Args:
        config: Full config dict
        tmdb_api_key: TMDB API key for ID conversion
        users: Optional list of usernames (defaults to config users list)
    """
    trakt_config = config.get('trakt', {})
    export_config = trakt_config.get('export', {})

    # Check if auto_sync is enabled
    if not trakt_config.get('enabled', False):
        return
    if not export_config.get('auto_sync', False):
        return

    # Get authenticated Trakt client
    trakt_client = get_authenticated_trakt_client(config)
    if not trakt_client:
        log_warning("Trakt not authenticated - run setup wizard to authenticate")
        return

    user_mode = export_config.get('user_mode', 'mapping')
    plex_users = export_config.get('plex_users', [])

    # Safety check for mapping mode
    if user_mode == 'mapping':
        if not plex_users or plex_users in [[], ['YourPlexUsername']]:
            log_warning(
                "Trakt sync: No plex_users configured.\n"
                "  Edit config.yml -> trakt.export.plex_users and add YOUR Plex username."
            )
            return

    print(f"\n{CYAN}Syncing Plex watch history to Trakt...{RESET}")

    # Get existing Trakt watch history to avoid duplicates
    existing_movie_imdb = trakt_client.get_watch_history_imdb_ids('movies')
    existing_show_imdb = trakt_client.get_watch_history_imdb_ids('shows')
    print(f"  Already on Trakt: {len(existing_movie_imdb)} movies, {len(existing_show_imdb)} shows")

    # Get users to sync
    if users is None:
        users = [u.strip() for u in config['users']['list'].split(',')]

    # Filter users based on mode
    if user_mode == 'mapping':
        plex_users_lower = [u.lower() for u in plex_users]
        users_to_sync = [u for u in users if u.lower() in plex_users_lower]
    else:
        users_to_sync = users

    if not users_to_sync:
        log_warning("No matching users to sync")
        return

    # Load TMDB IDs from cache files (fast - no API calls)
    all_movie_tmdb_ids = set()
    all_show_tmdb_ids = set()

    for username in users_to_sync:
        movie_profile = load_user_profile_from_cache(config, username, 'movie')
        if movie_profile:
            all_movie_tmdb_ids.update(movie_profile.get('tmdb_ids', set()))

        tv_profile = load_user_profile_from_cache(config, username, 'tv')
        if tv_profile:
            all_show_tmdb_ids.update(tv_profile.get('tmdb_ids', set()))

    if not all_movie_tmdb_ids and not all_show_tmdb_ids:
        print("  No Plex watch history in cache - run internal recommenders first")
        return

    # Load cache of already-synced TMDB IDs (avoid re-converting every run)
    TRAKT_SYNC_CACHE_VERSION = 1
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(project_root, config.get('cache_dir', 'cache'))
    sync_cache_file = os.path.join(cache_dir, 'trakt_synced_ids.json')
    synced_movie_tmdb = set()
    synced_show_tmdb = set()

    if os.path.exists(sync_cache_file):
        try:
            with open(sync_cache_file, 'r') as f:
                sync_cache = json.load(f)
                # Check cache version
                if sync_cache.get('version', 0) < TRAKT_SYNC_CACHE_VERSION:
                    print("  Trakt sync cache outdated, rebuilding...")
                else:
                    synced_movie_tmdb = set(sync_cache.get('movies', []))
                    synced_show_tmdb = set(sync_cache.get('shows', []))
        except Exception:
            pass

    # Only process items we haven't synced before
    new_movie_tmdb = all_movie_tmdb_ids - synced_movie_tmdb
    new_show_tmdb = all_show_tmdb_ids - synced_show_tmdb

    print(f"  Plex watched: {len(all_movie_tmdb_ids)} movies, {len(all_show_tmdb_ids)} shows")
    print(f"  Already synced: {len(synced_movie_tmdb)} movies, {len(synced_show_tmdb)} shows")

    if not new_movie_tmdb and not new_show_tmdb:
        print_status("  Watch history already synced to Trakt", "success")
        return

    print(f"  New to sync: {len(new_movie_tmdb)} movies, {len(new_show_tmdb)} shows")

    # Convert only NEW TMDB IDs to IMDB IDs
    new_movie_imdb = []
    new_show_imdb = []
    converted_movies = set()  # Track ALL converted (for cache)
    converted_shows = set()

    # Movies with progress
    movie_list = list(new_movie_tmdb)
    total_movies = len(movie_list)
    if total_movies > 0:
        if len(synced_movie_tmdb) == 0:
            print("  (First-time sync - this is a one-time operation)")
        for i, tmdb_id in enumerate(movie_list, 1):
            if i % 10 == 0 or i == total_movies:
                pct = int(i / total_movies * 100)
                sys.stdout.write(f"\r  Converting movie IDs: {i}/{total_movies} ({pct}%)")
                sys.stdout.flush()
            imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, 'movie')
            if imdb_id:
                converted_movies.add(tmdb_id)  # Cache ALL converted
                if imdb_id not in existing_movie_imdb:
                    new_movie_imdb.append(imdb_id)
        print()  # newline after progress

    # Shows with progress
    show_list = list(new_show_tmdb)
    total_shows = len(show_list)
    if total_shows > 0:
        for i, tmdb_id in enumerate(show_list, 1):
            if i % 10 == 0 or i == total_shows:
                pct = int(i / total_shows * 100)
                sys.stdout.write(f"\r  Converting show IDs: {i}/{total_shows} ({pct}%)")
                sys.stdout.flush()
            imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, 'tv')
            if imdb_id:
                converted_shows.add(tmdb_id)  # Cache ALL converted
                if imdb_id not in existing_show_imdb:
                    new_show_imdb.append(imdb_id)
        print()  # newline after progress

    # Update cache with all converted IDs (including ones already on Trakt)
    synced_movie_tmdb.update(converted_movies)
    synced_show_tmdb.update(converted_shows)
    try:
        with open(sync_cache_file, 'w') as f:
            json.dump({
                'version': TRAKT_SYNC_CACHE_VERSION,
                'movies': list(synced_movie_tmdb),
                'shows': list(synced_show_tmdb)
            }, f)
    except Exception:
        pass

    if not new_movie_imdb and not new_show_imdb:
        print_status("  Watch history already synced to Trakt", "success")
        return

    print(f"  New items to sync: {len(new_movie_imdb)} movies, {len(new_show_imdb)} shows")

    # Sync to Trakt in batches (avoid timeout with large lists)
    BATCH_SIZE = 100
    total_movies_added = 0
    total_shows_added = 0

    try:
        # Batch movies
        for i in range(0, len(new_movie_imdb), BATCH_SIZE):
            batch = new_movie_imdb[i:i + BATCH_SIZE]
            batch_num = (i // BATCH_SIZE) + 1
            total_batches = (len(new_movie_imdb) + BATCH_SIZE - 1) // BATCH_SIZE
            sys.stdout.write(f"\r  Syncing movies: batch {batch_num}/{total_batches}")
            sys.stdout.flush()
            result = trakt_client.add_to_history(movies=batch)
            total_movies_added += result.get('added', {}).get('movies', 0)
        if new_movie_imdb:
            print()  # newline after progress

        # Batch shows
        for i in range(0, len(new_show_imdb), BATCH_SIZE):
            batch = new_show_imdb[i:i + BATCH_SIZE]
            batch_num = (i // BATCH_SIZE) + 1
            total_batches = (len(new_show_imdb) + BATCH_SIZE - 1) // BATCH_SIZE
            sys.stdout.write(f"\r  Syncing shows: batch {batch_num}/{total_batches}")
            sys.stdout.flush()
            result = trakt_client.add_to_history(shows=batch)
            total_shows_added += result.get('added', {}).get('episodes', 0)
        if new_show_imdb:
            print()  # newline after progress

        print_status(
            f"  Synced to Trakt: {total_movies_added} movies, {total_shows_added} shows",
            "success"
        )
    except (TraktAPIError, TraktAuthError) as e:
        log_error(f"Failed to sync watch history to Trakt: {e}")


def main():
    print(f"\n{CYAN}External Recommendations Generator{RESET}")
    print("-" * 50)

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
        print_status("Connected to Plex", "success")
    except Exception as e:
        print_status(f"Error connecting to Plex: {e}", "error")
        sys.exit(1)

    # Process each user and collect data for combined HTML
    all_users_data = []
    for username in users:
        try:
            user_data = process_user(config, plex, username)
            if user_data:
                all_users_data.append(user_data)
        except Exception as e:
            print_status(f"Error processing {username}: {e}", "error")
            import traceback
            traceback.print_exc()

    # Generate combined HTML with all users
    output_dir = os.path.join(project_root, 'recommendations', 'external')

    if all_users_data:
        html_file = generate_combined_html(all_users_data, output_dir, tmdb_api_key, get_imdb_id)
        print_status("Combined watchlist generated!", "success")
    else:
        html_file = None
        print_status("No user data to generate watchlist", "warning")

    print(f"Watchlists saved to: {output_dir}")
    if html_file:
        file_url = f"file://{html_file}"
        print(f"\nView watchlist: {clickable_link(file_url)}")

    # Auto-open HTML if enabled
    external_config = config.get('external_recommendations', {})
    if external_config.get('auto_open_html', False) and html_file:
        print_status("Opening watchlist in browser...", "info")
        webbrowser.open(f'file://{html_file}')


if __name__ == "__main__":
    main()
