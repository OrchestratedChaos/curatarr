"""
TMDB API utilities for Plex Recommender.
Handles TMDB API calls, keyword fetching, and ID lookups.
"""

import time
import logging
import requests
from typing import Dict, List, Optional

# Language code mappings
LANGUAGE_CODES = {
    'en': 'English', 'es': 'Spanish', 'fr': 'French', 'de': 'German', 'it': 'Italian',
    'pt': 'Portuguese', 'ru': 'Russian', 'ja': 'Japanese', 'ko': 'Korean', 'zh': 'Chinese',
    'ar': 'Arabic', 'hi': 'Hindi', 'nl': 'Dutch', 'sv': 'Swedish', 'no': 'Norwegian',
    'da': 'Danish', 'fi': 'Finnish', 'pl': 'Polish', 'tr': 'Turkish', 'el': 'Greek',
    'he': 'Hebrew', 'th': 'Thai', 'vi': 'Vietnamese', 'id': 'Indonesian', 'ms': 'Malay',
    'cs': 'Czech', 'hu': 'Hungarian', 'ro': 'Romanian', 'uk': 'Ukrainian', 'fa': 'Persian',
    'bn': 'Bengali', 'ta': 'Tamil', 'te': 'Telugu', 'mr': 'Marathi', 'ur': 'Urdu'
}


def get_full_language_name(lang_code: str) -> str:
    """
    Convert language code to full language name.

    Args:
        lang_code: ISO language code (e.g., 'en', 'es')

    Returns:
        Full language name (e.g., 'English', 'Spanish')
    """
    return LANGUAGE_CODES.get(lang_code.lower(), lang_code.capitalize())


def fetch_tmdb_with_retry(url: str, params: Dict, max_retries: int = 3, timeout: int = 15) -> Optional[Dict]:
    """
    Fetch from TMDB API with retry logic and rate limit handling.

    Args:
        url: TMDB API endpoint URL
        params: Query parameters (must include api_key)
        max_retries: Maximum retry attempts (default 3)
        timeout: Request timeout in seconds (default 15)

    Returns:
        JSON response dict or None on failure
    """
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)

            if resp.status_code == 429:
                sleep_time = 2 * (attempt + 1)
                logging.warning(f"TMDB rate limit hit, waiting {sleep_time}s...")
                time.sleep(sleep_time)
                continue

            if resp.status_code == 200:
                return resp.json()

            logging.debug(f"TMDB request failed with status {resp.status_code}")
            return None

        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            logging.warning(f"TMDB connection error, retrying... ({attempt+1}/{max_retries})")
            time.sleep(1)
            if attempt == max_retries - 1:
                logging.warning(f"TMDB request failed after {max_retries} tries: {e}")
        except Exception as e:
            logging.warning(f"TMDB request error: {e}")
            return None

    return None


def get_tmdb_id_for_item(item, tmdb_api_key: str, media_type: str = 'movie', cache: Dict = None) -> Optional[int]:
    """
    Get TMDB ID for a Plex item using multiple fallback methods.

    Args:
        item: Plex media item
        tmdb_api_key: TMDB API key
        media_type: 'movie' or 'tv'
        cache: Optional dict to check/store cached lookups

    Returns:
        TMDB ID as integer or None
    """
    from .plex import extract_ids_from_guids

    # Check cache first
    cache_key = str(getattr(item, 'ratingKey', None))
    if cache and cache_key in cache:
        return cache[cache_key]

    # Method 1: Extract from Plex GUIDs
    ids = extract_ids_from_guids(item)
    if ids['tmdb_id']:
        if cache is not None:
            cache[cache_key] = ids['tmdb_id']
        return ids['tmdb_id']

    # Method 2: Search TMDB API
    if tmdb_api_key:
        title = getattr(item, 'title', '')
        year = getattr(item, 'year', None)

        search_url = f"https://api.themoviedb.org/3/search/{media_type}"
        params = {
            'api_key': tmdb_api_key,
            'query': title,
            'include_adult': False
        }

        # Add year parameter (different field name for TV)
        if year:
            if media_type == 'movie':
                params['year'] = year
            else:
                params['first_air_date_year'] = year

        data = fetch_tmdb_with_retry(search_url, params)
        if data and data.get('results'):
            tmdb_id = data['results'][0]['id']
            if cache is not None:
                cache[cache_key] = tmdb_id
            return tmdb_id

    # Method 3: Try via IMDb ID if available
    if ids['imdb_id'] and tmdb_api_key:
        find_url = f"https://api.themoviedb.org/3/find/{ids['imdb_id']}"
        params = {'api_key': tmdb_api_key, 'external_source': 'imdb_id'}
        data = fetch_tmdb_with_retry(find_url, params)
        if data:
            results_key = 'movie_results' if media_type == 'movie' else 'tv_results'
            if data.get(results_key):
                tmdb_id = data[results_key][0]['id']
                if cache is not None:
                    cache[cache_key] = tmdb_id
                return tmdb_id

    return None


def get_tmdb_keywords(tmdb_api_key: str, tmdb_id: int, media_type: str = 'movie', cache: Dict = None) -> List[str]:
    """
    Get keywords for a TMDB item.

    Args:
        tmdb_api_key: TMDB API key
        tmdb_id: TMDB ID
        media_type: 'movie' or 'tv'
        cache: Optional dict to check/store cached keywords

    Returns:
        List of lowercase keyword strings
    """
    if not tmdb_id or not tmdb_api_key:
        return []

    # Check cache
    cache_key = str(tmdb_id)
    if cache and cache_key in cache:
        return list(cache[cache_key])

    media = 'movie' if media_type == 'movie' else 'tv'
    url = f"https://api.themoviedb.org/3/{media}/{tmdb_id}/keywords"
    params = {'api_key': tmdb_api_key}

    data = fetch_tmdb_with_retry(url, params)
    if data:
        # Movies use 'keywords', TV uses 'results'
        keywords_list = data.get('keywords', data.get('results', []))
        keywords = [k['name'].lower() for k in keywords_list if 'name' in k]

        if cache is not None and keywords:
            cache[cache_key] = keywords

        return keywords

    return []
