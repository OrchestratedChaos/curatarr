"""
Configuration utilities for Plex Recommender.
Handles config loading, section access, and rating multipliers.
"""

import os
import json
import yaml
from typing import Dict

# Cache version - bump this when cache format changes to auto-invalidate old caches
CACHE_VERSION = 2  # v2: Added TMDB keywords to cache

# Default rating multipliers for similarity scoring (Plex uses 0-10 scale)
# Higher ratings = stronger signal. 5-star (10) boosted to emphasize favorites.
DEFAULT_RATING_MULTIPLIERS = {
    0: 0.1,   # Strong dislike
    1: 0.2,   # Very poor
    2: 0.4,   # Poor
    3: 0.6,   # Below average
    4: 0.8,   # Slightly below average
    5: 1.0,   # Neutral/baseline
    6: 1.2,   # Slightly above average
    7: 1.4,   # Good
    8: 1.7,   # Very good
    9: 2.0,   # Excellent
    10: 2.5   # Outstanding (5 stars) - strong signal
}

# Backwards compatibility alias
RATING_MULTIPLIERS = DEFAULT_RATING_MULTIPLIERS


def check_cache_version(cache_path: str, cache_type: str = "cache") -> bool:
    """
    Check if cache file is compatible with current version.

    Args:
        cache_path: Path to the cache file
        cache_type: Description for logging (e.g., "movie cache", "watched cache")

    Returns:
        True if cache is valid and compatible, False if it should be rebuilt
    """
    if not os.path.exists(cache_path):
        return False

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        cached_version = data.get('cache_version', 1)  # Default to v1 if not present

        if cached_version < CACHE_VERSION:
            print(f"\033[93m{cache_type} is outdated (v{cached_version} < v{CACHE_VERSION}), rebuilding...\033[0m")
            os.remove(cache_path)
            return False

        return True
    except Exception as e:
        print(f"\033[93mError reading {cache_type}, rebuilding: {e}\033[0m")
        return False


def get_config_section(config: Dict, key: str, default: Dict = None) -> Dict:
    """
    Get a config section case-insensitively.

    Args:
        config: The configuration dictionary
        key: The key to look for (will check lowercase and uppercase)
        default: Default value if key not found

    Returns:
        The config section or default value
    """
    if default is None:
        default = {}
    # Try lowercase first (preferred), then uppercase for backwards compatibility
    return config.get(key.lower(), config.get(key.upper(), default))


def get_tmdb_config(config: Dict) -> Dict:
    """
    Get TMDB configuration section, handling case variations.

    Args:
        config: The root configuration dictionary

    Returns:
        Dict with 'api_key' and 'use_keywords' keys
    """
    tmdb_config = get_config_section(config, 'tmdb')
    return {
        'api_key': tmdb_config.get('api_key'),
        'use_keywords': tmdb_config.get('use_tmdb_keywords', tmdb_config.get('use_TMDB_keywords', True))
    }


def load_config(config_path: str) -> dict:
    """
    Load YAML configuration file.

    Args:
        config_path: Path to config.yml file

    Returns:
        Parsed config dictionary
    """
    try:
        with open(config_path, 'r') as file:
            config = yaml.safe_load(file)
            print(f"Successfully loaded configuration from {config_path}")
            return config
    except Exception as e:
        print(f"\033[91mError loading config from {config_path}: {e}\033[0m")
        raise


def get_rating_multipliers(config: dict = None) -> dict:
    """
    Get rating multipliers from config or use defaults.

    Config uses 5-star scale, Plex uses 10-point scale.
    Maps: star_5 -> 9-10, star_4 -> 7-8, star_3 -> 5-6, star_2 -> 3-4, star_1 -> 1-2

    Args:
        config: Configuration dict with optional rating_multipliers section

    Returns:
        Dict mapping Plex ratings (0-10) to multiplier values
    """
    if not config or 'rating_multipliers' not in config:
        return DEFAULT_RATING_MULTIPLIERS.copy()

    rm = config['rating_multipliers']

    # Get values from config with defaults
    star_5 = rm.get('star_5', 2.5)
    star_4 = rm.get('star_4', 1.7)
    star_3 = rm.get('star_3', 1.0)
    star_2 = rm.get('star_2', 0.4)
    star_1 = rm.get('star_1', 0.2)

    # Map 5-star config to 10-point Plex scale
    return {
        0: 0.1,                              # Unrated/dislike
        1: star_1,                           # 1 star
        2: star_1 + (star_2 - star_1) * 0.5, # Between 1-2 stars
        3: star_2,                           # 2 stars
        4: star_2 + (star_3 - star_2) * 0.5, # Between 2-3 stars
        5: star_3,                           # 3 stars (baseline)
        6: star_3 + (star_4 - star_3) * 0.5, # Between 3-4 stars
        7: star_4,                           # 4 stars
        8: star_4 + (star_5 - star_4) * 0.5, # Between 4-5 stars
        9: star_5 - (star_5 - star_4) * 0.2, # High 4 stars
        10: star_5                           # 5 stars
    }


def adapt_config_for_media_type(root_config: Dict, media_type: str = 'movies') -> Dict:
    """
    Adapt root configuration for a specific media type (movies or TV).

    Creates a unified config dict by merging media-specific settings
    with global settings, handling key variations for backwards compatibility.

    Args:
        root_config: Root configuration dictionary from config.yml
        media_type: 'movies' or 'tv'

    Returns:
        Unified config dict with all needed settings for the media type
    """
    # Get media-specific section
    media_section = media_type.lower()
    media_config = root_config.get(media_section, root_config.get(media_section.upper(), {}))

    # Build unified config
    config = {
        'plex': root_config.get('plex', {}),
        'tmdb': root_config.get('tmdb', root_config.get('TMDB', {})),
        'users': root_config.get('users', {}),
        'collections': root_config.get('collections', {}),
        'recency_decay': root_config.get('recency_decay', {}),
        'rating_multipliers': root_config.get('rating_multipliers', {}),
        'general': root_config.get('general', {}),
        'cache_dir': root_config.get('cache_dir', 'cache'),
    }

    # Add media-specific settings
    config['limit_results'] = media_config.get('limit_results', 50 if media_type == 'movies' else 20)
    config['randomize_recommendations'] = media_config.get('randomize_recommendations', False)
    config['normalize_counters'] = media_config.get('normalize_counters', True)
    config['show_summary'] = media_config.get('show_summary', True)
    config['show_cast'] = media_config.get('show_cast', True)
    config['show_language'] = media_config.get('show_language', True)
    config['show_rating'] = media_config.get('show_rating', True)
    config['show_imdb_link'] = media_config.get('show_imdb_link', False)

    # Quality filters
    quality = media_config.get('quality_filters', {})
    config['min_rating'] = quality.get('min_rating', 5.0 if media_type == 'movies' else 0.0)
    config['min_vote_count'] = quality.get('min_vote_count', 50 if media_type == 'movies' else 0)

    # Weights - handle both old and new key names
    weights = media_config.get('weights', {})
    config['weights'] = {
        'genre': weights.get('genre', weights.get('genre_weight', 0.25)),
        'actor': weights.get('actor', weights.get('actor_weight', 0.20)),
        'keyword': weights.get('keyword', weights.get('keyword_weight', 0.50)),
        'language': weights.get('language', weights.get('language_weight', 0.0)),
    }

    # Media-specific weights
    if media_type == 'movies':
        config['weights']['director'] = weights.get('director', weights.get('director_weight', 0.05))
    else:
        config['weights']['studio'] = weights.get('studio', weights.get('studio_weight', 0.10))

    # Radarr/Sonarr integration
    arr_key = 'radarr' if media_type == 'movies' else 'sonarr'
    config[arr_key] = media_config.get(arr_key, {})

    # Collection settings
    collections = root_config.get('collections', {})
    config['add_label'] = collections.get('add_label', True)
    config['stale_removal_days'] = collections.get('stale_removal_days', 7)

    return config
