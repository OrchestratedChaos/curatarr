"""
Cache I/O utilities for Plex Recommender.
Handles loading and saving of various cache files.
"""

import os
import json
import copy
import logging
from datetime import datetime
from typing import Dict, Optional

from .config import CACHE_VERSION, check_cache_version
from .display import log_warning


def save_json_cache(cache_path: str, data: Dict, cache_version: int = None) -> bool:
    """
    Save data to a JSON cache file.

    Args:
        cache_path: Path to the cache file
        data: Dictionary to save
        cache_version: Optional version number to include

    Returns:
        True on success, False on failure
    """
    try:
        if cache_version is not None:
            data['cache_version'] = cache_version
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        logging.error(f"Error saving cache to {cache_path}: {e}")
        return False


def load_json_cache(cache_path: str) -> Optional[Dict]:
    """
    Load data from a JSON cache file.

    Args:
        cache_path: Path to the cache file

    Returns:
        Dictionary from cache or None on failure
    """
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"Error loading cache from {cache_path}: {e}")
        return None


def load_media_cache(cache_path: str, media_key: str = 'movies') -> Dict:
    """
    Load media cache from file with version checking.

    Args:
        cache_path: Path to the cache file
        media_key: Key for media items ('movies' or 'shows')

    Returns:
        Cache dictionary with media items, or empty structure if invalid/missing
    """
    empty_cache = {media_key: {}, 'last_updated': None, 'library_count': 0, 'cache_version': CACHE_VERSION}

    if not check_cache_version(cache_path, f"{media_key.title()} cache"):
        return empty_cache

    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_warning(f"Error loading {media_key} cache: {e}")
            return empty_cache
    return empty_cache


def save_media_cache(cache_path: str, cache_data: Dict, media_key: str = 'movies') -> bool:
    """
    Save media cache to file.

    Args:
        cache_path: Path to the cache file
        cache_data: Cache dictionary to save
        media_key: Key for media items (for logging)

    Returns:
        True on success, False on failure
    """
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        log_warning(f"Error saving {media_key} cache: {e}")
        return False


def save_watched_cache(
    cache_path: str,
    watched_data_counters: Dict,
    plex_tmdb_cache: Dict,
    tmdb_keywords_cache: Dict,
    watched_ids: set,
    label_dates: Dict,
    watched_count: int,
    media_type: str = 'movie'
) -> bool:
    """
    Save watched data cache to file.

    Args:
        cache_path: Path to save cache
        watched_data_counters: Counter data for preferences
        plex_tmdb_cache: Plex to TMDB ID mappings
        tmdb_keywords_cache: TMDB keywords cache
        watched_ids: Set of watched item IDs
        label_dates: Label date tracking dict
        watched_count: Count of watched items
        media_type: 'movie' or 'tv'

    Returns:
        True on success, False on failure
    """
    try:
        # Create a copy for serialization
        watched_data_for_cache = copy.deepcopy(watched_data_counters)

        # Convert any set objects to lists for JSON serialization
        if 'tmdb_ids' in watched_data_for_cache and isinstance(watched_data_for_cache['tmdb_ids'], set):
            watched_data_for_cache['tmdb_ids'] = list(watched_data_for_cache['tmdb_ids'])

        # Build cache data structure
        id_key = 'watched_movie_ids' if media_type == 'movie' else 'watched_show_ids'
        cache_data = {
            'cache_version': CACHE_VERSION,
            'watched_count': watched_count,
            'watched_data_counters': watched_data_for_cache,
            'plex_tmdb_cache': {str(k): v for k, v in plex_tmdb_cache.items()},
            'tmdb_keywords_cache': {str(k): v for k, v in tmdb_keywords_cache.items()},
            id_key: list(watched_ids),
            'label_dates': label_dates,
            'last_updated': datetime.now().isoformat()
        }

        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=4, ensure_ascii=False)

        logging.debug(f"Saved watched cache: {watched_count} {media_type}s, {len(watched_ids)} IDs")
        return True

    except Exception as e:
        logging.error(f"Error saving watched cache: {e}")
        return False
