"""
Plex Recommender Utilities Package.

This package contains modular utility functions organized by responsibility.
All public functions are re-exported here for backwards compatibility.
"""

# Config utilities
from .config import (
    CACHE_VERSION,
    DEFAULT_RATING_MULTIPLIERS,
    RATING_MULTIPLIERS,
    check_cache_version,
    get_config_section,
    get_tmdb_config,
    load_config,
    get_rating_multipliers,
    adapt_config_for_media_type,
)

# Display utilities
from .display import (
    RED,
    GREEN,
    YELLOW,
    CYAN,
    RESET,
    ANSI_PATTERN,
    ColoredFormatter,
    TeeLogger,
    setup_logging,
    print_user_header,
    print_user_footer,
    print_status,
    log_warning,
    log_error,
    show_progress,
    format_media_output,
    print_similarity_breakdown,
    user_select_recommendations,
)

# TMDB utilities
from .tmdb import (
    LANGUAGE_CODES,
    get_full_language_name,
    fetch_tmdb_with_retry,
    get_tmdb_id_for_item,
    get_tmdb_keywords,
)

# Cache utilities
from .cache import (
    save_json_cache,
    load_json_cache,
    load_media_cache,
    save_media_cache,
    save_watched_cache,
)

# Label utilities
from .labels import (
    build_label_name,
    categorize_labeled_items,
    remove_labels_from_items,
    add_labels_to_items,
)

# Scoring utilities
from .scoring import (
    GENRE_NORMALIZATION,
    normalize_genre,
    fuzzy_keyword_match,
    calculate_recency_multiplier,
    calculate_rewatch_multiplier,
    calculate_similarity_score,
)

# Counter utilities
from .counters import (
    create_empty_counters,
    process_counters_from_cache,
)

# Helper utilities
from .helpers import (
    TITLE_SUFFIXES_TO_STRIP,
    normalize_title,
    map_path,
    cleanup_old_logs,
)

# Plex utilities
from .plex import (
    init_plex,
    get_plex_account_ids,
    get_watched_movie_count,
    get_watched_show_count,
    fetch_plex_watch_history_movies,
    fetch_plex_watch_history_shows,
    fetch_watch_history_with_tmdb,
    update_plex_collection,
    cleanup_old_collections,
    get_configured_users,
    get_current_users,
    get_excluded_genres_for_user,
    get_user_specific_connection,
    find_plex_movie,
    extract_genres,
    extract_ids_from_guids,
    extract_rating,
    get_library_imdb_ids,
    get_plex_user_ids,
)

# Define __all__ for explicit public API
__all__ = [
    # Config
    'CACHE_VERSION',
    'DEFAULT_RATING_MULTIPLIERS',
    'RATING_MULTIPLIERS',
    'check_cache_version',
    'get_config_section',
    'get_tmdb_config',
    'load_config',
    'get_rating_multipliers',
    'adapt_config_for_media_type',
    # Display
    'RED',
    'GREEN',
    'YELLOW',
    'CYAN',
    'RESET',
    'ANSI_PATTERN',
    'ColoredFormatter',
    'TeeLogger',
    'setup_logging',
    'print_user_header',
    'print_user_footer',
    'print_status',
    'log_warning',
    'log_error',
    'show_progress',
    'format_media_output',
    'print_similarity_breakdown',
    'user_select_recommendations',
    # TMDB
    'LANGUAGE_CODES',
    'get_full_language_name',
    'fetch_tmdb_with_retry',
    'get_tmdb_id_for_item',
    'get_tmdb_keywords',
    # Cache
    'save_json_cache',
    'load_json_cache',
    'load_media_cache',
    'save_media_cache',
    'save_watched_cache',
    # Labels
    'build_label_name',
    'categorize_labeled_items',
    'remove_labels_from_items',
    'add_labels_to_items',
    # Scoring
    'GENRE_NORMALIZATION',
    'normalize_genre',
    'fuzzy_keyword_match',
    'calculate_recency_multiplier',
    'calculate_rewatch_multiplier',
    'calculate_similarity_score',
    # Counters
    'create_empty_counters',
    'process_counters_from_cache',
    # Helpers
    'TITLE_SUFFIXES_TO_STRIP',
    'normalize_title',
    'map_path',
    'cleanup_old_logs',
    # Plex
    'init_plex',
    'get_plex_account_ids',
    'get_watched_movie_count',
    'get_watched_show_count',
    'fetch_plex_watch_history_movies',
    'fetch_plex_watch_history_shows',
    'fetch_watch_history_with_tmdb',
    'update_plex_collection',
    'cleanup_old_collections',
    'get_configured_users',
    'get_current_users',
    'get_excluded_genres_for_user',
    'get_user_specific_connection',
    'find_plex_movie',
    'extract_genres',
    'extract_ids_from_guids',
    'extract_rating',
    'get_library_imdb_ids',
    'get_plex_user_ids',
]
