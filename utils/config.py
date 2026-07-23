"""
Configuration utilities for Curatarr.
Handles config loading, section access, and rating multipliers.
"""

import os
import json
import re
import yaml
from typing import Dict, List

# Project version - single source of truth
__version__ = "2.8.23"

# Cache version - bump this when cache format changes to auto-invalidate old caches
CACHE_VERSION = 4  # v4: Added production_company_ids for TV franchise bonus

# Common constants used across recommenders
TOP_CAST_COUNT = 3                  # Number of top actors to consider
TMDB_RATE_LIMIT_DELAY = 0.5         # Seconds between TMDB API calls
DEFAULT_RATING = 5.0                # Default rating when none available
WEIGHT_SUM_TOLERANCE = 1e-6         # Tolerance for weight sum validation
DEFAULT_LIMIT_PLEX_RESULTS = 100    # Default candidate pool (2x collection target for better selection)
TOP_POOL_PERCENTAGE = 0.1           # Top 10% for randomization pool

# Media type constants - use these instead of hardcoded strings
MEDIA_TYPE_MOVIE = 'movie'
MEDIA_TYPE_TV = 'tv'
MEDIA_KEY_MOVIES = 'movies'
MEDIA_KEY_SHOWS = 'shows'

# Recommendation tier percentages (for diversified recommendations)
# Safe: High-confidence picks similar to user's taste
# Diverse: Mid-tier picks that introduce variety
# Wildcard: Lower-scored discoveries for exploration
TIER_SAFE_PERCENT = 0.6             # 60% safe picks from top scores
TIER_DIVERSE_PERCENT = 0.3          # 30% diverse picks from mid-tier
TIER_WILDCARD_PERCENT = 0.1         # 10% wildcard picks for discovery

# TF-IDF scoring penalties for rare/unseen content attributes
TFIDF_GENRE_PENALTY = 0.3           # Max 30% penalty per rare genre
TFIDF_KEYWORD_PENALTY = 0.15        # Max 15% penalty per rare keyword
UNSEEN_GENRE_PENALTY = 0.1          # Penalty for genres user has never watched
UNSEEN_KEYWORD_PENALTY = 0.02       # Penalty for keywords user has never seen

# Popularity dampening for very popular content (prevents blockbusters dominating)
POPULARITY_DAMPENING_FACTOR = 0.03  # ~3% penalty per order of magnitude above threshold
POPULARITY_DAMPENING_CAP = 0.90     # Cap at 10% max penalty (minimum multiplier)

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

# Default negative multipliers for low-rated content (ratings 0-3 become penalties)
# These are applied instead of positive multipliers when rating <= threshold
DEFAULT_NEGATIVE_MULTIPLIERS = {
    0: -1.0,   # Strong dislike -> strong penalty
    1: -0.8,   # Very poor -> significant penalty
    2: -0.5,   # Poor -> moderate penalty
    3: -0.3,   # Below average -> mild penalty
}

# Default threshold for negative signals (Plex 0-10 scale)
DEFAULT_NEGATIVE_THRESHOLD = 3  # Ratings 0-3 become negative signals

# Rating tier thresholds (Plex uses 0-10 scale, Plex UI shows 0-5 stars)
RATING_TIER_5_STAR = 9.0    # 5 stars: ratings 9-10
RATING_TIER_4_STAR = 7.0    # 4 stars: ratings 7-8
RATING_TIER_3_STAR = 5.0    # 3 stars: ratings 5-6

# Rating tier multipliers for preference weighting
RATING_MULTIPLIER_5_STAR = 1.0     # Strong preference
RATING_MULTIPLIER_4_STAR = 0.75    # Moderate preference
RATING_MULTIPLIER_3_STAR = 0.5     # Weak preference
RATING_MULTIPLIER_2_STAR = 0.25    # Very weak preference
RATING_MULTIPLIER_UNRATED = 0.6    # Default for unrated content

# HTTP request timeouts (seconds)
PLEX_REQUEST_TIMEOUT = 30
TMDB_REQUEST_TIMEOUT = 10
SONARR_REQUEST_TIMEOUT = 30
RADARR_REQUEST_TIMEOUT = 30

# Collection bonus parameters (for movies in user's started collections)
COLLECTION_BONUS_BASE = 0.05          # Base bonus multiplier
COLLECTION_BONUS_LOG_FACTOR = 0.5     # Log scaling factor for collection size
COLLECTION_BONUS_CAP = 0.15           # Maximum 15% bonus

# TMDB genre ID for TV movies (used to identify specials)
TMDB_TV_MOVIE_GENRE_ID = 10770

# TMDB genre ID for Animation
TMDB_ANIMATION_GENRE_ID = 16


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


def _load_module_configs(config: dict, config_dir: str) -> dict:
    """
    Load and merge modular config files into the main config.

    Loads tuning.yml, trakt.yml, radarr.yml, sonarr.yml if they exist.
    Module files take precedence over main config.yml.
    """
    # Tuning modules merge their sections into root
    tuning_path = os.path.join(config_dir, 'tuning.yml')
    if os.path.exists(tuning_path):
        try:
            with open(tuning_path, 'r', encoding='utf-8') as f:
                tuning = yaml.safe_load(f)
                if tuning:
                    for key, value in tuning.items():
                        config[key] = value
                    print(f"  Loaded tuning.yml")
        except Exception as e:
            print(f"\033[93mWarning: Could not load tuning.yml: {e}\033[0m")

    # Feature modules go under their key
    for module in ['trakt', 'radarr', 'sonarr']:
        module_path = os.path.join(config_dir, f'{module}.yml')
        if os.path.exists(module_path):
            try:
                with open(module_path, 'r', encoding='utf-8') as f:
                    module_config = yaml.safe_load(f)
                    if module_config:
                        config[module] = module_config
                        print(f"  Loaded {module}.yml")
            except Exception as e:
                print(f"\033[93mWarning: Could not load {module}.yml: {e}\033[0m")

    return config


def _auto_migrate_if_needed(config: dict, config_path: str) -> dict:
    """
    Auto-migrate monolithic config to modular format if needed.

    Returns the migrated config (reloaded after migration).
    """
    # Import here to avoid circular imports
    from utils.migrate_config import needs_migration, migrate_config

    if needs_migration(config):
        print("\033[93mDetected legacy config format, migrating to modular files...\033[0m")
        result = migrate_config(config_path)
        if result['migrated']:
            print("\033[92mConfig migration complete!\033[0m")
            # Reload the now-split config
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

    return config


def load_config(config_path: str) -> dict:
    """
    Load YAML configuration with modular config file support.

    Loads config.yml and merges optional module files:
    - tuning.yml: Display/scoring options (merged into root)
    - trakt.yml: Trakt integration settings
    - radarr.yml: Radarr integration settings
    - sonarr.yml: Sonarr integration settings

    Environment variables take precedence over all config values:
        PLEX_URL      -> plex.url
        PLEX_TOKEN    -> plex.token
        TMDB_API_KEY  -> tmdb.api_key

    Args:
        config_path: Path to config.yml file

    Returns:
        Parsed and merged config dictionary
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            config = yaml.safe_load(file)
            print(f"Successfully loaded configuration from {config_path}")

        config_dir = os.path.dirname(config_path) or '.'

        # Auto-migrate legacy monolithic config if needed
        config = _auto_migrate_if_needed(config, config_path)

        # Load and merge modular config files
        config = _load_module_configs(config, config_dir)

        # Override with environment variables (security best practice)
        env_overrides = [
            ('PLEX_URL', 'plex', 'url'),
            ('PLEX_TOKEN', 'plex', 'token'),
            ('TMDB_API_KEY', 'tmdb', 'api_key'),
        ]

        for env_var, section, key in env_overrides:
            value = os.environ.get(env_var)
            if value:
                if section not in config:
                    config[section] = {}
                config[section][key] = value
                print(f"  Using {env_var} from environment")

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


def get_negative_signals_config(config: dict = None) -> dict:
    """
    Get negative signals configuration with defaults.

    Args:
        config: Configuration dict with optional negative_signals section

    Returns:
        Dict with negative signal settings
    """
    if not config:
        return {
            'enabled': True,
            'bad_ratings': {
                'enabled': True,
                'threshold': DEFAULT_NEGATIVE_THRESHOLD,
                'cap_penalty': 0.5,
            },
            'dropped_shows': {
                'enabled': True,
                'min_episodes_watched': 2,
                'max_completion_percent': 25,
                'penalty_multiplier': -0.4,
            }
        }

    ns = config.get('negative_signals', {})

    # If master switch is off, return disabled config
    if not ns.get('enabled', True):
        return {'enabled': False, 'bad_ratings': {'enabled': False}, 'dropped_shows': {'enabled': False}}

    bad_ratings = ns.get('bad_ratings', {})
    dropped_shows = ns.get('dropped_shows', {})

    return {
        'enabled': True,
        'bad_ratings': {
            'enabled': bad_ratings.get('enabled', True),
            'threshold': bad_ratings.get('threshold', DEFAULT_NEGATIVE_THRESHOLD),
            'cap_penalty': bad_ratings.get('cap_penalty', 0.5),
        },
        'dropped_shows': {
            'enabled': dropped_shows.get('enabled', True),
            'min_episodes_watched': dropped_shows.get('min_episodes_watched', 2),
            'max_completion_percent': dropped_shows.get('max_completion_percent', 25),
            'penalty_multiplier': dropped_shows.get('penalty_multiplier', -0.4),
        }
    }


def get_negative_multiplier(rating: int, config: dict = None) -> float:
    """
    Get the negative multiplier for a low rating.

    Args:
        rating: Plex rating (0-10 scale)
        config: Optional config with custom multipliers

    Returns:
        Negative multiplier value (negative float)
    """
    return DEFAULT_NEGATIVE_MULTIPLIERS.get(rating, -0.3)


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

    # Radarr/Sonarr integration - check root level first (new modular format),
    # then fall back to nested under movies/tv (legacy format)
    arr_key = 'radarr' if media_type == 'movies' else 'sonarr'
    config[arr_key] = root_config.get(arr_key, media_config.get(arr_key, {}))

    # Collection settings
    collections = root_config.get('collections', {})
    config['add_label'] = collections.get('add_label', True)

    # Negative signals configuration
    config['negative_signals'] = get_negative_signals_config(root_config)

    return config


# =============================================================================
# Multi-library support (#157 Phase 1)
#
# `libraries` is a repeatable, first-class entity living inside config.yml:
#
#   libraries:
#     - id: movies
#       name: Movies
#       section: Movies
#       media_type: movie
#       arr:
#         root_folder: /data/movies
#         quality_profile: HD-1080p
#         instance:
#           url: http://localhost:7878
#           api_key: KEY
#
# Global sonarr.yml/radarr.yml remain the default *arr instance (enabled/
# url/api_key), the which-users-sync policy (auto_sync/user_mode/plex_users),
# and the field-level fallback for any arr.* field a library omits.
#
# Nothing in the recommender pipeline consumes these yet (see Phases 2-4) -
# this is purely additive.
# =============================================================================

# Legacy global radarr.yml/sonarr.yml field name -> unified library arr.*
# field name, for the handful of fields whose name differs by media type.
_ARR_FIELD_ALIASES = {
    MEDIA_TYPE_MOVIE: {'search': 'search_for_movie'},
    MEDIA_TYPE_TV: {'search': 'search_for_series'},
}

# Per-library routing fields eligible for field-level fallback to the global
# radarr/sonarr block, by media type. minimum_availability is movie-only,
# series_type is tv-only.
_ARR_ROUTING_FIELDS = {
    MEDIA_TYPE_MOVIE: ['root_folder', 'quality_profile', 'tag', 'monitor', 'search', 'minimum_availability'],
    MEDIA_TYPE_TV: ['root_folder', 'quality_profile', 'tag', 'monitor', 'search', 'series_type'],
}

# *arr instance/connection fields - overridable per-library via arr.instance
_ARR_INSTANCE_FIELDS = ['enabled', 'url', 'api_key']

# Sensible boolean defaults for fields that should never resolve to None
_ARR_FIELD_DEFAULTS = {'enabled': False, 'monitor': False, 'search': False}


def _slugify_library_id(name: str) -> str:
    """
    Derive a stable slug id from a library name (e.g. "TV Shows" -> "tv-shows").

    Args:
        name: Library display name

    Returns:
        Lowercase, hyphenated slug. Falls back to 'library' if name is blank
        or has no alphanumeric characters.
    """
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').strip().lower()).strip('-')
    return slug or 'library'


def _normalize_library(library: Dict) -> Dict:
    """
    Fill in default id/media_type/section for a single library entry.

    Args:
        library: Raw library dict from config['libraries']

    Returns:
        A copy of library with id, name, media_type, section, and arr
        guaranteed to be present.
    """
    normalized = dict(library or {})
    name = normalized.get('name') or normalized.get('id') or 'Library'
    normalized['name'] = name
    normalized['id'] = normalized.get('id') or _slugify_library_id(name)
    normalized['media_type'] = normalized.get('media_type') or MEDIA_TYPE_MOVIE
    normalized['section'] = normalized.get('section') or name
    normalized.setdefault('arr', {})
    return normalized


def _synthesize_legacy_libraries(config: Dict) -> List[Dict]:
    """
    Back-compat fallback: synthesize a movie + tv library entry from the
    legacy single-library plex.movie_library/plex.tv_library settings.

    Each synthesized entry's 'arr' override is left empty, so
    get_effective_arr_config() naturally falls back to the global
    radarr/sonarr block for that entry's routing - i.e. arr routing is
    still effectively "pulled from" the global radarr/sonarr config.

    Args:
        config: Root configuration dictionary

    Returns:
        Two-entry list: [movie library, tv library]
    """
    plex_config = get_config_section(config, 'plex')
    movie_library = plex_config.get('movie_library', 'Movies')
    tv_library = plex_config.get('tv_library', 'TV Shows')

    return [
        {
            'id': _slugify_library_id(movie_library),
            'name': movie_library,
            'section': movie_library,
            'media_type': MEDIA_TYPE_MOVIE,
            'arr': {},
        },
        {
            'id': _slugify_library_id(tv_library),
            'name': tv_library,
            'section': tv_library,
            'media_type': MEDIA_TYPE_TV,
            'arr': {},
        },
    ]


def get_libraries(config: Dict) -> List[Dict]:
    """
    Get the normalized list of libraries from config.

    Reads config['libraries'] (repeatable multi-library entries) and fills
    in defaults for any omitted fields: id (slug of name), media_type
    (defaults to 'movie'), section (defaults to name).

    Back-compat fallback: if config has no 'libraries' section (or it's
    empty), synthesizes a movie entry from plex.movie_library (default
    'Movies') and a tv entry from plex.tv_library (default 'TV Shows'),
    so existing single-library installs keep working without a
    'libraries:' block in config.yml. This is the single back-compat
    fallback path.

    Args:
        config: Root configuration dictionary

    Returns:
        List of normalized library dicts, each with at least:
        id, name, section, media_type, arr
    """
    raw_libraries = config.get('libraries')

    if raw_libraries:
        return [_normalize_library(lib) for lib in raw_libraries]

    return _synthesize_legacy_libraries(config)


def get_libraries_for_media_type(config: Dict, media_type: str) -> List[Dict]:
    """
    Get normalized libraries filtered to a specific media type.

    Args:
        config: Root configuration dictionary
        media_type: 'movie' or 'tv' (see MEDIA_TYPE_MOVIE / MEDIA_TYPE_TV)

    Returns:
        List of normalized library dicts matching media_type
    """
    return [lib for lib in get_libraries(config) if lib.get('media_type') == media_type]


def get_effective_arr_config(config: Dict, library: Dict) -> Dict:
    """
    Resolve the effective *arr (Radarr/Sonarr) routing config for a library.

    Deep-merges, in increasing precedence:
      1. The global sonarr/radarr block (selected by library['media_type'])
      2. library['arr'] (per-library routing overrides)
      3. library['arr']['instance'] (per-library *arr instance connection)

    Args:
        config: Root configuration dictionary
        library: A library dict (see get_libraries)

    Returns:
        Dict with effective keys: enabled, url, api_key, root_folder,
        quality_profile, tag, monitor, search, plus minimum_availability
        (movie libraries) or series_type (tv libraries).
    """
    media_type = library.get('media_type') or MEDIA_TYPE_MOVIE
    arr_key = 'radarr' if media_type == MEDIA_TYPE_MOVIE else 'sonarr'
    global_arr = get_config_section(config, arr_key)
    library_arr = library.get('arr') or {}
    instance = library_arr.get('instance') or {}
    aliases = _ARR_FIELD_ALIASES.get(media_type, {})

    effective = {}

    # Instance/connection fields: global -> library.arr -> library.arr.instance
    for field in _ARR_INSTANCE_FIELDS:
        value = global_arr.get(field, _ARR_FIELD_DEFAULTS.get(field))
        if field in library_arr:
            value = library_arr[field]
        if field in instance:
            value = instance[field]
        effective[field] = value

    # Routing fields: global (legacy field name) -> library.arr (unified name)
    for field in _ARR_ROUTING_FIELDS.get(media_type, []):
        global_field = aliases.get(field, field)
        value = global_arr.get(global_field, _ARR_FIELD_DEFAULTS.get(field))
        if field in library_arr:
            value = library_arr[field]
        effective[field] = value

    return effective
