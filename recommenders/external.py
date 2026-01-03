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
    get_plex_account_ids, get_tmdb_config,
    fetch_watch_history_with_tmdb,
    print_user_header, print_user_footer, print_status,
    log_warning, log_error, load_config,
    calculate_rewatch_multiplier, calculate_recency_multiplier,
    calculate_similarity_score, normalize_genre, fuzzy_keyword_match
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

# Reverse mapping for config service names to display names
SERVICE_DISPLAY_NAMES = {
    'netflix': 'Netflix',
    'hulu': 'Hulu',
    'disney_plus': 'Disney+',
    'amazon_prime': 'Amazon Prime Video',
    'paramount_plus': 'Paramount+',
    'apple_tv_plus': 'Apple TV+',
    'max': 'Max',
    'peacock': 'Peacock',
    'crunchyroll': 'Crunchyroll',
    'crackle': 'Crackle',
    'tubi': 'Tubi',
    'mubi': 'MUBI',
    'shudder': 'Shudder'
}

# Reverse TMDB genre mappings (name to ID) for Discover API
TMDB_MOVIE_GENRE_IDS = {v.lower(): k for k, v in TMDB_MOVIE_GENRES.items()}
TMDB_TV_GENRE_IDS = {v.lower(): k for k, v in TMDB_TV_GENRES.items()}

# Quality thresholds for candidate filtering
MIN_RATING = 6.0
MIN_VOTE_COUNT = 100
MAX_CANDIDATES = 500
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
    top_genres = list(user_profile['genres'].most_common(5))
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
                'vote_average.gte': MIN_RATING,
                'vote_count.gte': MIN_VOTE_COUNT,
                'sort_by': 'vote_average.desc',
                'page': 1
            }
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                results = response.json().get('results', [])
                for item in results[:20]:  # Top 20 per genre
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
                        'rating': item.get('vote_average', 0)
                    }

        except (requests.RequestException, KeyError):
            pass

    print(f"    Found {len(candidates)} candidates from genre search")

    # Also search by top keywords using search API
    for keyword, _ in top_keywords[:5]:  # Top 5 keywords
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
                        'vote_average.gte': MIN_RATING,
                        'vote_count.gte': MIN_VOTE_COUNT,
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
                                'rating': item.get('vote_average', 0)
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


def get_tmdb_keywords(tmdb_api_key, tmdb_id, media_type='movie'):
    """Fetch keywords from TMDB for a given item."""
    try:
        media = 'movie' if media_type == 'movie' else 'tv'
        url = f"https://api.themoviedb.org/3/{media}/{tmdb_id}/keywords"
        response = requests.get(url, params={'api_key': tmdb_api_key}, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # Movies use 'keywords', TV uses 'results'
            keywords_list = data.get('keywords', data.get('results', []))
            return [kw['name'] for kw in keywords_list[:10]]  # Top 10 keywords
    except (requests.RequestException, KeyError):
        pass
    return []


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
                'overview': data.get('overview', '')
            }
    except Exception as e:
        pass
    return None


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

def find_similar_content_with_profile(tmdb_api_key, user_profile, library_data, media_type='movie', limit=50, exclude_genres=None, min_relevance_score=0.25, config=None):
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

    Returns:
        List of scored recommendations
    """
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

    print(f"  Found {len(candidates)} quality candidates (rating >= {MIN_RATING}, votes >= {MIN_VOTE_COUNT})")

    # Now score each candidate using profile-based similarity
    scored_recommendations = []
    candidate_list = list(candidates.keys())

    print(f"  Scoring candidates against user profile...")
    for i, candidate_id in enumerate(candidate_list):
        if i % 100 == 0 and i > 0:
            print(f"    Scored {i}/{len(candidate_list)} candidates...")

        # Fetch full details from TMDB
        details = get_tmdb_details(tmdb_api_key, candidate_id, media_type)
        if not details:
            continue

        # Check excluded genres
        if exclude_genres:
            content_genres = [g.lower() for g in details.get('genres', [])]
            if any(eg.lower() in content_genres for eg in exclude_genres):
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
            'score': score,
            'overview': details.get('overview', ''),
            'genres': details.get('genres', []),
            'genre_ids': []  # For compatibility with genre balancing
        })

    # Sort by score (highest first), then by rating as tiebreaker
    scored_recommendations.sort(key=lambda x: (x['score'], x['rating']), reverse=True)

    # Apply threshold filtering
    high_score = [r for r in scored_recommendations if r['score'] >= min_relevance_score]
    low_score = [r for r in scored_recommendations if r['score'] < min_relevance_score]

    print(f"  {len(high_score)} items above {int(min_relevance_score*100)}% threshold, {len(low_score)} below")

    # Take high-score items first, backfill if needed
    final_recs = high_score[:limit]
    if len(final_recs) < limit:
        final_recs.extend(low_score[:limit - len(final_recs)])

    if final_recs:
        print(f"  Top recommendation: {final_recs[0]['title']} ({final_recs[0]['score']:.1%})")

    return final_recs


# Keep old function name for compatibility but redirect to new one
def find_similar_content(tmdb_api_key, watched_items, library_data, media_type='movie', limit=50, genre_distribution=None, exclude_genres=None, min_relevance_score=0.25):
    """Legacy wrapper - redirects to profile-based scoring in process_user()"""
    # This function is kept for compatibility but the actual work
    # is now done in process_user() using find_similar_content_with_profile()
    print_status("Warning: Using legacy find_similar_content", "warning")
    return []

def load_cache(display_name, media_type):
    """Load existing recommendations cache"""
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
            return cache
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

def generate_markdown(username, display_name, movies_categorized, shows_categorized, output_dir):
    """
    Generate markdown watchlist file with streaming service grouping

    Args:
        movies_categorized: dict with 'user_services', 'other_services', 'acquire' keys
        shows_categorized: dict with 'user_services', 'other_services', 'acquire' keys
    """
    os.makedirs(output_dir, exist_ok=True)
    # Use display_name for filename, sanitized for filesystem
    safe_name = display_name.lower().replace(' ', '_')
    output_file = os.path.join(output_dir, f'{safe_name}_watchlist.md')

    now = datetime.now()

    def write_service_section(f, items, media_icon):
        """Helper to write a table of items"""
        f.write(f"| Title | Year | Rating | Score | Days on List |\n")
        f.write(f"|-------|------|--------|-------|-------------|\n")
        for item in items:
            days_listed = (now - datetime.fromisoformat(item['added_date'])).days
            f.write(f"| {item['title']} | {item['year']} | ⭐ {item['rating']:.1f} | {item['score']:.1%} | {days_listed} |\n")
        f.write("\n")

    with open(output_file, 'w') as f:
        f.write(f"# 🎬 Watchlist for {display_name}\n\n")
        f.write(f"*Last updated: {now.strftime('%Y-%m-%d %H:%M')}*\n\n")
        f.write("---\n\n")

        # Movies section
        if any([movies_categorized['user_services'], movies_categorized['other_services'], movies_categorized['acquire']]):
            f.write("## 🎥 Movies to Watch\n\n")

            # User's services
            if movies_categorized['user_services']:
                f.write("### Available on Your Services\n\n")
                for service, items in sorted(movies_categorized['user_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} movies)\n\n")
                    write_service_section(f, items, "🎥")
                f.write("---\n\n")

            # Other services
            if movies_categorized['other_services']:
                f.write("### Available on Other Services\n\n")
                f.write("*Consider subscribing if many recommendations are on a single service*\n\n")
                for service, items in sorted(movies_categorized['other_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} movies)\n\n")
                    write_service_section(f, items, "🎥")
                f.write("---\n\n")

            # Acquire
            if movies_categorized['acquire']:
                f.write(f"### Acquire ({len(movies_categorized['acquire'])} movies)\n\n")
                f.write("*Not available on any streaming service - need physical/digital copy*\n\n")
                write_service_section(f, movies_categorized['acquire'], "🎥")

        # TV Shows section
        if any([shows_categorized['user_services'], shows_categorized['other_services'], shows_categorized['acquire']]):
            f.write("## 📺 TV Shows to Watch\n\n")

            # User's services
            if shows_categorized['user_services']:
                f.write("### Available on Your Services\n\n")
                for service, items in sorted(shows_categorized['user_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} shows)\n\n")
                    write_service_section(f, items, "📺")
                f.write("---\n\n")

            # Other services
            if shows_categorized['other_services']:
                f.write("### Available on Other Services\n\n")
                f.write("*Consider subscribing if many recommendations are on a single service*\n\n")
                for service, items in sorted(shows_categorized['other_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} shows)\n\n")
                    write_service_section(f, items, "📺")
                f.write("---\n\n")

            # Acquire
            if shows_categorized['acquire']:
                f.write(f"### Acquire ({len(shows_categorized['acquire'])} shows)\n\n")
                f.write("*Not available on any streaming service - need physical/digital copy*\n\n")
                write_service_section(f, shows_categorized['acquire'], "📺")

        # Instructions
        f.write("---\n\n")
        f.write("## 📝 How to Use This List\n\n")
        f.write("- Items are automatically removed when added to your Plex library\n")
        f.write(f"- To manually ignore an item, add its title to `{safe_name}_ignore.txt`\n")
        f.write("- List updates daily with new recommendations\n")
        f.write("- Grouped by streaming availability to help you decide what to watch or acquire\n\n")

    return output_file


def generate_combined_html(all_users_data, output_dir, tmdb_api_key):
    """
    Generate single HTML watchlist with tabs for all users.
    Users can switch between tabs, select items, and export.
    """
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, 'watchlist.html')

    now = datetime.now()

    # Collect all IMDB IDs across all users (to avoid duplicate API calls)
    print("  Fetching IMDB IDs for export...")
    all_imdb_ids = {}  # tmdb_id -> imdb_id

    for user_data in all_users_data:
        movies_cat = user_data['movies_categorized']
        shows_cat = user_data['shows_categorized']

        # Gather all movie tmdb_ids
        for items in movies_cat['user_services'].values():
            for item in items:
                tmdb_id = item.get('tmdb_id')
                if tmdb_id and tmdb_id not in all_imdb_ids:
                    imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, 'movie')
                    if imdb_id:
                        all_imdb_ids[tmdb_id] = imdb_id
        for items in movies_cat['other_services'].values():
            for item in items:
                tmdb_id = item.get('tmdb_id')
                if tmdb_id and tmdb_id not in all_imdb_ids:
                    imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, 'movie')
                    if imdb_id:
                        all_imdb_ids[tmdb_id] = imdb_id
        for item in movies_cat['acquire']:
            tmdb_id = item.get('tmdb_id')
            if tmdb_id and tmdb_id not in all_imdb_ids:
                imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, 'movie')
                if imdb_id:
                    all_imdb_ids[tmdb_id] = imdb_id

        # Gather all show tmdb_ids
        for items in shows_cat['user_services'].values():
            for item in items:
                tmdb_id = item.get('tmdb_id')
                if tmdb_id and tmdb_id not in all_imdb_ids:
                    imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, 'tv')
                    if imdb_id:
                        all_imdb_ids[tmdb_id] = imdb_id
        for items in shows_cat['other_services'].values():
            for item in items:
                tmdb_id = item.get('tmdb_id')
                if tmdb_id and tmdb_id not in all_imdb_ids:
                    imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, 'tv')
                    if imdb_id:
                        all_imdb_ids[tmdb_id] = imdb_id
        for item in shows_cat['acquire']:
            tmdb_id = item.get('tmdb_id')
            if tmdb_id and tmdb_id not in all_imdb_ids:
                imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, 'tv')
                if imdb_id:
                    all_imdb_ids[tmdb_id] = imdb_id

    def render_table(items, media_type, user_id):
        """Render HTML table for items with checkboxes (unchecked by default)"""
        rows = []
        for item in items:
            tmdb_id = item.get('tmdb_id', '')
            imdb_id = all_imdb_ids.get(tmdb_id, '')
            days_listed = (now - datetime.fromisoformat(item['added_date'])).days
            rows.append(f'''
                <tr data-tmdb="{tmdb_id}" data-imdb="{imdb_id}" data-type="{media_type}" data-user="{user_id}">
                    <td><input type="checkbox" class="select-item"></td>
                    <td>{item['title']}</td>
                    <td>{item['year']}</td>
                    <td>{item['rating']:.1f}</td>
                    <td>{item['score']:.0%}</td>
                    <td>{days_listed}</td>
                </tr>''')
        return '\n'.join(rows)

    def render_service_section(service, items, media_type, user_id):
        """Render a service section with table"""
        service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
        return f'''
            <h4>{service_display} ({len(items)} {media_type}s)</h4>
            <table>
                <thead>
                    <tr><th><input type="checkbox" class="select-all-table"></th><th>Title</th><th>Year</th><th>Rating</th><th>Score</th><th>Days</th></tr>
                </thead>
                <tbody>
                    {render_table(items, media_type, user_id)}
                </tbody>
            </table>'''

    # Build tabs HTML
    tabs_html = ""
    panels_html = ""

    for i, user_data in enumerate(all_users_data):
        display_name = user_data['display_name']
        user_id = user_data['username'].lower().replace(' ', '_')
        movies_cat = user_data['movies_categorized']
        shows_cat = user_data['shows_categorized']
        is_active = "active" if i == 0 else ""

        # Tab button
        tabs_html += f'<button class="tab-btn {is_active}" data-user="{user_id}">{display_name}</button>\n'

        # Panel content
        panel_content = ""

        # Movies section
        if any([movies_cat['user_services'], movies_cat['other_services'], movies_cat['acquire']]):
            panel_content += "<h2>Movies to Watch</h2>"

            if movies_cat['user_services']:
                panel_content += "<h3>Available on Your Services</h3>"
                for service, items in sorted(movies_cat['user_services'].items(), key=lambda x: -len(x[1])):
                    panel_content += render_service_section(service, items, 'movie', user_id)

            if movies_cat['other_services']:
                panel_content += "<h3>Available on Other Services</h3>"
                for service, items in sorted(movies_cat['other_services'].items(), key=lambda x: -len(x[1])):
                    panel_content += render_service_section(service, items, 'movie', user_id)

            if movies_cat['acquire']:
                panel_content += f"<h3>Need to Acquire ({len(movies_cat['acquire'])} movies)</h3>"
                panel_content += f'''
                    <table>
                        <thead>
                            <tr><th><input type="checkbox" class="select-all-table"></th><th>Title</th><th>Year</th><th>Rating</th><th>Score</th><th>Days</th></tr>
                        </thead>
                        <tbody>
                            {render_table(movies_cat['acquire'], 'movie', user_id)}
                        </tbody>
                    </table>'''

        # Shows section
        if any([shows_cat['user_services'], shows_cat['other_services'], shows_cat['acquire']]):
            panel_content += "<h2>TV Shows to Watch</h2>"

            if shows_cat['user_services']:
                panel_content += "<h3>Available on Your Services</h3>"
                for service, items in sorted(shows_cat['user_services'].items(), key=lambda x: -len(x[1])):
                    panel_content += render_service_section(service, items, 'show', user_id)

            if shows_cat['other_services']:
                panel_content += "<h3>Available on Other Services</h3>"
                for service, items in sorted(shows_cat['other_services'].items(), key=lambda x: -len(x[1])):
                    panel_content += render_service_section(service, items, 'show', user_id)

            if shows_cat['acquire']:
                panel_content += f"<h3>Need to Acquire ({len(shows_cat['acquire'])} shows)</h3>"
                panel_content += f'''
                    <table>
                        <thead>
                            <tr><th><input type="checkbox" class="select-all-table"></th><th>Title</th><th>Year</th><th>Rating</th><th>Score</th><th>Days</th></tr>
                        </thead>
                        <tbody>
                            {render_table(shows_cat['acquire'], 'show', user_id)}
                        </tbody>
                    </table>'''

        if not panel_content:
            panel_content = "<p>No recommendations available for this user.</p>"

        panels_html += f'<div class="tab-panel {is_active}" data-user="{user_id}">{panel_content}</div>\n'

    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Plex Watchlist</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: linear-gradient(135deg, #0d0d0d 0%, #1a1a1a 50%, #0d0d0d 100%);
            color: #e8e8e8;
            min-height: 100vh;
        }}
        h1 {{
            color: #d4af37;
            margin-bottom: 5px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            font-size: 2em;
        }}
        h2 {{
            background: linear-gradient(90deg, #8b0000 0%, #660000 100%);
            color: #d4af37;
            padding: 12px 15px;
            border-radius: 3px;
            margin-top: 30px;
            border-left: 4px solid #d4af37;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-size: 1.1em;
        }}
        h3 {{
            background: #1c1c1c;
            color: #c0c0c0;
            padding: 10px 12px;
            border-radius: 3px;
            border-left: 3px solid #8b0000;
            font-size: 0.95em;
        }}
        h4 {{
            color: #d4af37;
            margin: 15px 0 8px 0;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
            margin-bottom: 25px;
            padding-bottom: 20px;
            border-bottom: 1px solid #333;
        }}
        .export-buttons {{
            display: flex;
            gap: 10px;
        }}
        .export-btn {{
            background: linear-gradient(180deg, #8b0000 0%, #5c0000 100%);
            color: #d4af37;
            border: 1px solid #d4af37;
            padding: 12px 24px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 13px;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.2s ease;
        }}
        .export-btn:hover {{
            background: linear-gradient(180deg, #a00000 0%, #700000 100%);
            box-shadow: 0 0 10px rgba(212, 175, 55, 0.3);
        }}
        .export-btn.sonarr {{
            background: linear-gradient(180deg, #2a2a2a 0%, #1a1a1a 100%);
        }}
        .export-btn.sonarr:hover {{
            background: linear-gradient(180deg, #3a3a3a 0%, #2a2a2a 100%);
        }}
        .tabs {{
            display: flex;
            gap: 3px;
            margin-bottom: 25px;
            flex-wrap: wrap;
            background: #111;
            padding: 5px;
            border-radius: 5px;
        }}
        .tab-btn {{
            background: #1a1a1a;
            color: #888;
            border: none;
            padding: 12px 24px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 13px;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.2s ease;
        }}
        .tab-btn:hover {{
            background: #2a2a2a;
            color: #c0c0c0;
        }}
        .tab-btn.active {{
            background: linear-gradient(180deg, #8b0000 0%, #5c0000 100%);
            color: #d4af37;
        }}
        .tab-panel {{ display: none; }}
        .tab-panel.active {{ display: block; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 10px 0 25px 0;
            background: #141414;
            border-radius: 3px;
            overflow: hidden;
            box-shadow: 0 2px 10px rgba(0,0,0,0.3);
        }}
        th, td {{
            padding: 12px 10px;
            text-align: left;
            border-bottom: 1px solid #2a2a2a;
        }}
        th {{
            background: #1c1c1c;
            color: #d4af37;
            font-size: 0.8em;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        tr:hover {{ background: #1f1f1f; }}
        td:first-child, th:first-child {{ width: 40px; text-align: center; }}
        input[type="checkbox"] {{
            width: 16px;
            height: 16px;
            cursor: pointer;
            accent-color: #8b0000;
        }}
        .timestamp {{ color: #666; font-size: 12px; }}
        .instructions {{
            background: #141414;
            padding: 20px;
            border-radius: 3px;
            margin-top: 40px;
            border: 1px solid #2a2a2a;
        }}
        .instructions h3 {{
            background: none;
            border: none;
            padding: 0;
            color: #d4af37;
            margin-bottom: 15px;
        }}
        .instructions ul {{
            margin: 0;
            padding-left: 20px;
            color: #999;
        }}
        .instructions li {{ margin-bottom: 8px; }}
        .instructions strong {{ color: #c0c0c0; }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>Plex Watchlist</h1>
            <p class="timestamp">Last updated: {now.strftime('%Y-%m-%d %H:%M')}</p>
        </div>
        <div class="export-buttons">
            <button class="export-btn" onclick="exportRadarr()">Export to Radarr (<span id="movie-count">0</span>)</button>
            <button class="export-btn sonarr" onclick="exportSonarr()">Export to Sonarr (<span id="show-count">0</span>)</button>
        </div>
    </div>

    <div class="tabs">
        {tabs_html}
    </div>

    {panels_html}

    <div class="instructions">
        <h3>How to Use</h3>
        <ul>
            <li>Click a user tab to view their recommendations</li>
            <li>Check the items you want to export</li>
            <li><strong>Radarr:</strong> Click "Export to Radarr" to download IMDB IDs for selected movies</li>
            <li><strong>Sonarr:</strong> Click "Export to Sonarr" to download IMDB IDs for selected shows</li>
            <li>Exports include selections from ALL users, not just the active tab</li>
        </ul>
    </div>

    <script>
        // Tab switching
        document.querySelectorAll('.tab-btn').forEach(btn => {{
            btn.addEventListener('click', function() {{
                const userId = this.getAttribute('data-user');

                // Update active tab
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                this.classList.add('active');

                // Show corresponding panel
                document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
                document.querySelector(`.tab-panel[data-user="${{userId}}"]`).classList.add('active');
            }});
        }});

        // Select-all checkbox functionality (per table)
        document.querySelectorAll('.select-all-table').forEach(selectAll => {{
            selectAll.addEventListener('change', function() {{
                const table = this.closest('table');
                table.querySelectorAll('.select-item').forEach(cb => {{
                    cb.checked = this.checked;
                }});
                updateCounts();
            }});
        }});

        // Update counts when individual items are checked
        document.querySelectorAll('.select-item').forEach(cb => {{
            cb.addEventListener('change', updateCounts);
        }});

        function updateCounts() {{
            // Count ALL selected items across ALL users
            const movieCount = document.querySelectorAll('tr[data-type="movie"] .select-item:checked').length;
            const showCount = document.querySelectorAll('tr[data-type="show"] .select-item:checked').length;
            document.getElementById('movie-count').textContent = movieCount;
            document.getElementById('show-count').textContent = showCount;
        }}

        function exportRadarr() {{
            // Export from ALL users
            const rows = document.querySelectorAll('tr[data-type="movie"]');
            const imdbIds = [];
            rows.forEach(row => {{
                const checkbox = row.querySelector('.select-item');
                if (checkbox && checkbox.checked) {{
                    const imdb = row.getAttribute('data-imdb');
                    if (imdb && imdb.startsWith('tt')) {{
                        imdbIds.push(imdb);
                    }}
                }}
            }});
            if (imdbIds.length === 0) {{
                alert('No selected movies with IMDB IDs to export. Select items first.');
                return;
            }}
            downloadFile('radarr_import.txt', [...new Set(imdbIds)].join('\\n'));
            alert('Exported ' + imdbIds.length + ' movies for Radarr import.');
        }}

        function exportSonarr() {{
            // Export from ALL users
            const rows = document.querySelectorAll('tr[data-type="show"]');
            const imdbIds = [];
            rows.forEach(row => {{
                const checkbox = row.querySelector('.select-item');
                if (checkbox && checkbox.checked) {{
                    const imdb = row.getAttribute('data-imdb');
                    if (imdb && imdb.startsWith('tt')) {{
                        imdbIds.push(imdb);
                    }}
                }}
            }});
            if (imdbIds.length === 0) {{
                alert('No selected TV shows with IMDB IDs to export. Select items first.');
                return;
            }}
            downloadFile('sonarr_import.txt', [...new Set(imdbIds)].join('\\n'));
            alert('Exported ' + imdbIds.length + ' shows for Sonarr import.');
        }}

        function downloadFile(filename, content) {{
            const blob = new Blob([content], {{ type: 'text/plain' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }}

        // Initialize counts on load
        updateCounts();
    </script>
</body>
</html>'''

    with open(output_file, 'w') as f:
        f.write(html_content)

    return output_file


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

    # Find new recommendations using profile-based scoring
    external_config = config.get('external_recommendations', {})
    movie_limit = external_config.get('movie_limit', 30)
    show_limit = external_config.get('show_limit', 20)
    min_relevance = external_config.get('min_relevance_score', 0.25)

    # Get excluded genres for this user
    exclude_genres = user_prefs.get('exclude_genres', [])
    if exclude_genres:
        print(f"Excluding genres: {', '.join(exclude_genres)}")

    tmdb_api_key = get_tmdb_config(config)['api_key']

    new_movies = find_similar_content_with_profile(
        tmdb_api_key,
        movie_profile,
        library_movies,
        'movie',
        limit=movie_limit,
        exclude_genres=exclude_genres,
        min_relevance_score=min_relevance,
        config=config
    )

    new_shows = find_similar_content_with_profile(
        tmdb_api_key,
        show_profile,
        library_shows,
        'tv',
        limit=show_limit,
        exclude_genres=exclude_genres,
        min_relevance_score=min_relevance,
        config=config
    )

    # Merge with existing cache - UPDATE scores for existing items, ADD new ones
    for movie in new_movies:
        tmdb_id = str(movie['tmdb_id'])
        if tmdb_id in movie_cache:
            # Update score for existing item (profile may have changed)
            old_score = movie_cache[tmdb_id].get('score', 0)
            movie_cache[tmdb_id]['score'] = movie['score']
            movie_cache[tmdb_id]['rating'] = movie['rating']
            if abs(movie['score'] - old_score) > SCORE_CHANGE_THRESHOLD:
                print(f"    Updated score: {movie['title']} {old_score:.1%} -> {movie['score']:.1%}")
        else:
            # Add new item
            movie_cache[tmdb_id] = {
                'tmdb_id': movie['tmdb_id'],
                'title': movie['title'],
                'year': movie['year'],
                'rating': movie['rating'],
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
            if abs(show['score'] - old_score) > SCORE_CHANGE_THRESHOLD:
                print(f"    Updated score: {show['title']} {old_score:.1%} -> {show['score']:.1%}")
        else:
            # Add new item
            show_cache[tmdb_id] = {
                'tmdb_id': show['tmdb_id'],
                'title': show['title'],
                'year': show['year'],
                'rating': show['rating'],
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

    # Return data for combined HTML generation
    return {
        'username': username,
        'display_name': display_name,
        'movies_categorized': movies_categorized,
        'shows_categorized': shows_categorized
    }

def main():
    print(f"\n{CYAN}External Recommendations Generator{RESET}")
    print("-" * 50)

    # Load config from project root (one level up from recommenders/)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, 'config.yml')
    config = load_config(config_path)

    # Connect to Plex
    try:
        plex = PlexServer(config['plex']['url'], config['plex']['token'])
        print_status("Connected to Plex", "success")
    except Exception as e:
        print_status(f"Error connecting to Plex: {e}", "error")
        sys.exit(1)

    # Get users
    users = [u.strip() for u in config['users']['list'].split(',')]

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
    tmdb_api_key = get_tmdb_config(config)['api_key']

    if all_users_data:
        html_file = generate_combined_html(all_users_data, output_dir, tmdb_api_key)
        print_status("Combined watchlist generated!", "success")
    else:
        html_file = None
        print_status("No user data to generate watchlist", "warning")

    print(f"Watchlists saved to: {output_dir}")

    # Auto-open HTML if enabled
    external_config = config.get('external_recommendations', {})
    if external_config.get('auto_open_html', False) and html_file:
        print_status("Opening watchlist in browser...", "info")
        webbrowser.open(f'file://{html_file}')

if __name__ == "__main__":
    main()
