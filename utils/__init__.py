"""
Curatarr Utilities Package.

This package contains modular utility functions organized by responsibility.
All public functions are re-exported here for backwards compatibility.
"""

# Config utilities
from .config import (
    __version__,
    CACHE_VERSION,
    TOP_CAST_COUNT,
    TMDB_RATE_LIMIT_DELAY,
    DEFAULT_RATING,
    WEIGHT_SUM_TOLERANCE,
    DEFAULT_LIMIT_PLEX_RESULTS,
    TOP_POOL_PERCENTAGE,
    TIER_SAFE_PERCENT,
    TIER_DIVERSE_PERCENT,
    TIER_WILDCARD_PERCENT,
    DEFAULT_RATING_MULTIPLIERS,
    RATING_MULTIPLIERS,
    DEFAULT_NEGATIVE_MULTIPLIERS,
    DEFAULT_NEGATIVE_THRESHOLD,
    RATING_TIER_5_STAR,
    RATING_TIER_4_STAR,
    RATING_TIER_3_STAR,
    RATING_MULTIPLIER_5_STAR,
    RATING_MULTIPLIER_4_STAR,
    RATING_MULTIPLIER_3_STAR,
    RATING_MULTIPLIER_2_STAR,
    RATING_MULTIPLIER_UNRATED,
    check_cache_version,
    get_config_section,
    get_tmdb_config,
    load_config,
    get_rating_multipliers,
    get_negative_signals_config,
    get_negative_multiplier,
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
    clickable_link,
    show_progress,
    format_media_output,
    print_similarity_breakdown,
    user_select_recommendations,
)

# TMDB utilities
from .tmdb import (
    LANGUAGE_CODES,
    IMDB_TMDB_CACHE_VERSION,
    get_full_language_name,
    fetch_tmdb_with_retry,
    get_tmdb_id_for_item,
    get_tmdb_keywords,
    load_imdb_tmdb_cache,
    save_imdb_tmdb_cache,
    get_tmdb_id_from_imdb,
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
    select_tiered_recommendations,
)

# Counter utilities
from .counters import (
    create_empty_counters,
    process_counters_from_cache,
)

# Helper utilities
from .helpers import (
    TITLE_SUFFIXES_TO_STRIP,
    get_project_root,
    normalize_title,
    map_path,
    cleanup_old_logs,
    compute_profile_hash,
)

# CLI utilities
from .cli import (
    get_users_from_config,
    resolve_admin_username,
    update_config_for_user,
    setup_log_file,
    teardown_log_file,
    print_runtime,
    run_recommender_main,
)

# Plex utilities
from .plex import (
    init_plex,
    get_plex_account_ids,
    get_watched_movie_count,
    get_watched_show_count,
    fetch_plex_watch_history_movies,
    fetch_plex_watch_history_shows,
    fetch_show_completion_data,
    identify_dropped_shows,
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

# Trakt utilities
from .trakt import (
    TRAKT_RATE_LIMIT_DELAY,
    TRAKT_ENHANCE_CACHE_VERSION,
    TraktAuthError,
    TraktAPIError,
    TraktClient,
    create_trakt_client,
    get_authenticated_trakt_client,
    load_trakt_enhance_cache,
    save_trakt_enhance_cache,
    fetch_tmdb_details_for_profile,
    enhance_profile_with_trakt,
)

# Trakt discovery utilities
from .trakt_discovery import (
    DISCOVERY_CACHE_TTL,
    get_trending_items,
    get_popular_items,
    get_anticipated_items,
    get_recommended_items,
    discover_from_trakt,
    get_trakt_discovery_candidates,
)

# Sonarr utilities
from .sonarr import (
    SonarrAPIError,
    SonarrClient,
    create_sonarr_client,
)

# Radarr utilities
from .radarr import (
    RadarrAPIError,
    RadarrClient,
    create_radarr_client,
)

# MDBList utilities
from .mdblist import (
    MDBListAPIError,
    MDBListClient,
    create_mdblist_client,
)

# Simkl utilities
from .simkl import (
    SimklAuthError,
    SimklAPIError,
    SimklClient,
    create_simkl_client,
    get_authenticated_simkl_client,
)

# Define __all__ for explicit public API
__all__ = [
    # Config
    '__version__',
    'CACHE_VERSION',
    'TOP_CAST_COUNT',
    'TMDB_RATE_LIMIT_DELAY',
    'DEFAULT_RATING',
    'WEIGHT_SUM_TOLERANCE',
    'DEFAULT_LIMIT_PLEX_RESULTS',
    'TOP_POOL_PERCENTAGE',
    'TIER_SAFE_PERCENT',
    'TIER_DIVERSE_PERCENT',
    'TIER_WILDCARD_PERCENT',
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
    'clickable_link',
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
    'select_tiered_recommendations',
    # Counters
    'create_empty_counters',
    'process_counters_from_cache',
    # Helpers
    'TITLE_SUFFIXES_TO_STRIP',
    'get_project_root',
    'normalize_title',
    'map_path',
    'cleanup_old_logs',
    'compute_profile_hash',
    # CLI
    'get_users_from_config',
    'resolve_admin_username',
    'update_config_for_user',
    'setup_log_file',
    'teardown_log_file',
    'print_runtime',
    'run_recommender_main',
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
    # Trakt
    'TRAKT_RATE_LIMIT_DELAY',
    'TraktAuthError',
    'TraktAPIError',
    'TraktClient',
    'create_trakt_client',
    'get_authenticated_trakt_client',
    'fetch_tmdb_details_for_profile',
    'enhance_profile_with_trakt',
    # Trakt Discovery
    'DISCOVERY_CACHE_TTL',
    'get_trending_items',
    'get_popular_items',
    'get_anticipated_items',
    'get_recommended_items',
    'discover_from_trakt',
    'get_trakt_discovery_candidates',
    # Sonarr
    'SonarrAPIError',
    'SonarrClient',
    'create_sonarr_client',
    # Radarr
    'RadarrAPIError',
    'RadarrClient',
    'create_radarr_client',
    # MDBList
    'MDBListAPIError',
    'MDBListClient',
    'create_mdblist_client',
    # Simkl
    'SimklAuthError',
    'SimklAPIError',
    'SimklClient',
    'create_simkl_client',
    'get_authenticated_simkl_client',
]
