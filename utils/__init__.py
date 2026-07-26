"""
Curatarr Utilities Package.

This package contains modular utility functions organized by responsibility.
All public functions are re-exported here for backwards compatibility.
"""

# Config utilities
# Cache utilities
from .cache import (
    load_json_cache,
    load_media_cache,
    save_json_cache,
    save_media_cache,
    save_watched_cache,
)

# CLI utilities
from .cli import (
    get_users_from_config,
    print_runtime,
    print_update_notice,
    resolve_admin_username,
    run_recommender_main,
    setup_log_file,
    teardown_log_file,
    update_config_for_user,
)
from .config import (
    CACHE_VERSION,
    COLLECTION_BONUS_BASE,
    COLLECTION_BONUS_CAP,
    COLLECTION_BONUS_LOG_FACTOR,
    DEFAULT_LIMIT_PLEX_RESULTS,
    DEFAULT_NEGATIVE_MULTIPLIERS,
    DEFAULT_NEGATIVE_THRESHOLD,
    DEFAULT_RATING,
    DEFAULT_RATING_MULTIPLIERS,
    MEDIA_KEY_MOVIES,
    MEDIA_KEY_SHOWS,
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_TV,
    PLEX_REQUEST_TIMEOUT,
    RADARR_REQUEST_TIMEOUT,
    RATING_MULTIPLIER_2_STAR,
    RATING_MULTIPLIER_3_STAR,
    RATING_MULTIPLIER_4_STAR,
    RATING_MULTIPLIER_5_STAR,
    RATING_MULTIPLIER_UNRATED,
    RATING_MULTIPLIERS,
    RATING_TIER_3_STAR,
    RATING_TIER_4_STAR,
    RATING_TIER_5_STAR,
    SONARR_REQUEST_TIMEOUT,
    TIER_DIVERSE_PERCENT,
    TIER_SAFE_PERCENT,
    TIER_WILDCARD_PERCENT,
    TMDB_ANIMATION_GENRE_ID,
    TMDB_RATE_LIMIT_DELAY,
    TMDB_REQUEST_TIMEOUT,
    TMDB_TV_MOVIE_GENRE_ID,
    TOP_CAST_COUNT,
    TOP_POOL_PERCENTAGE,
    UPDATE_MODES,
    WEIGHT_SUM_TOLERANCE,
    __version__,
    adapt_config_for_media_type,
    check_cache_version,
    get_config_section,
    get_effective_arr_config,
    get_libraries,
    get_libraries_for_media_type,
    get_negative_multiplier,
    get_negative_signals_config,
    get_rating_multipliers,
    get_tmdb_config,
    get_update_mode,
    load_config,
)

# Counter utilities
from .counters import (
    create_empty_counters,
    process_counters_from_cache,
)

# Display utilities
from .display import (
    ANSI_PATTERN,
    CYAN,
    GREEN,
    RED,
    RESET,
    YELLOW,
    ColoredFormatter,
    JsonFormatter,
    TeeLogger,
    clickable_link,
    format_media_output,
    log_error,
    log_warning,
    print_similarity_breakdown,
    print_status,
    print_user_footer,
    print_user_header,
    setup_logging,
    show_progress,
    smart_open_html,
    user_select_recommendations,
)

# Helper utilities
from .helpers import (
    TITLE_SUFFIXES_TO_STRIP,
    cleanup_old_logs,
    compute_profile_hash,
    get_project_root,
    map_path,
    migrate_legacy_cache_dir,
    normalize_title,
)

# Label utilities
from .labels import (
    add_labels_to_items,
    build_label_name,
    categorize_labeled_items,
    remove_labels_from_items,
)

# MDBList utilities
from .mdblist import (
    MDBListAPIError,
    MDBListClient,
    create_mdblist_client,
)

# Metrics utilities (local-first Prometheus text format - see module
# docstring for why this isn't the prometheus_client package)
from .metrics import (
    DURATION_BUCKETS,
    record_api_call,
    record_cache_lookup,
    record_recommender_run,
    record_self_update_attempt,
    record_unhandled_error,
    render_prometheus_text,
    track_api_call,
)

# Plex utilities
from .plex import (
    MOVIE_RATING_HIERARCHY,
    TV_RATING_HIERARCHY,
    apply_user_label_restrictions,
    cleanup_old_collections,
    extract_genres,
    extract_ids_from_guids,
    extract_rating,
    fetch_plex_watch_history_movies,
    fetch_plex_watch_history_shows,
    fetch_show_completion_data,
    fetch_watch_history_with_tmdb,
    find_plex_movie,
    get_configured_users,
    get_current_users,
    get_excluded_genres_for_user,
    get_library_imdb_ids,
    get_max_rating_for_user,
    get_plex_account_ids,
    get_plex_user_ids,
    get_user_specific_connection,
    get_watched_movie_count,
    get_watched_show_count,
    identify_dropped_shows,
    init_plex,
    is_rating_allowed,
    update_plex_collection,
)

# Radarr utilities
from .radarr import (
    RadarrAPIError,
    RadarrClient,
    create_radarr_client,
    create_radarr_client_from,
)

# Scoring utilities
from .scoring import (
    GENRE_NORMALIZATION,
    calculate_recency_multiplier,
    calculate_rewatch_multiplier,
    calculate_similarity_score,
    fuzzy_keyword_match,
    normalize_genre,
    normalize_user_profile,
    select_tiered_recommendations,
)

# In-binary self-update utilities (frozen/PyInstaller binaries only -
# see module docstring; source installs keep using run.sh/run.ps1)
from .self_update import (
    PINNED_SIGNING_KEY_FINGERPRINT,
    DownloadError,
    HashMismatchError,
    NotFrozenError,
    NoUpdateAvailableError,
    SelfUpdateError,
    SignatureVerificationError,
    SwapError,
    UnsupportedPlatformError,
    VerifiedUpdate,
    cleanup_stale_old_binary,
    current_binary_path,
    determine_update_target,
    download_and_verify_update,
    parse_sha256sums,
    perform_self_update,
    sanitize_frozen_relaunch_env,
    select_asset_name,
    sha256_file,
    swap_binary,
    verify_downloaded_asset,
    verify_pinned_signature,
)

# Simkl utilities
from .simkl import (
    SimklAPIError,
    SimklAuthError,
    SimklClient,
    create_simkl_client,
    get_authenticated_simkl_client,
)

# Sonarr utilities
from .sonarr import (
    SonarrAPIError,
    SonarrClient,
    create_sonarr_client,
    create_sonarr_client_from,
)

# Tautulli utilities
from .tautulli import (
    TautulliAPIError,
    TautulliClient,
    TautulliHistoryItem,
    build_user_map,
    create_tautulli_client,
    fetch_tautulli_movie_history,
    fetch_tautulli_show_watched_data,
    map_users,
    merge_movie_history,
    merge_show_watched_data,
)

# TMDB utilities
from .tmdb import (
    IMDB_TMDB_CACHE_VERSION,
    LANGUAGE_CODES,
    fetch_tmdb_with_retry,
    get_full_language_name,
    get_tmdb_id_for_item,
    get_tmdb_id_from_imdb,
    get_tmdb_keywords,
    load_imdb_tmdb_cache,
    save_imdb_tmdb_cache,
)

# Trakt utilities
from .trakt import (
    TRAKT_ENHANCE_CACHE_VERSION,
    TRAKT_RATE_LIMIT_DELAY,
    TraktAPIError,
    TraktAuthError,
    TraktClient,
    create_trakt_client,
    enhance_profile_with_trakt,
    fetch_tmdb_details_for_profile,
    get_authenticated_trakt_client,
    load_trakt_enhance_cache,
    save_trakt_enhance_cache,
)

# Trakt discovery utilities
from .trakt_discovery import (
    DISCOVERY_CACHE_TTL,
    discover_from_trakt,
    get_anticipated_items,
    get_popular_items,
    get_recommended_items,
    get_trakt_discovery_candidates,
    get_trending_items,
)

# Update-check utilities (advisory-only - see module docstring)
from .update_check import (
    GITHUB_RELEASES_API,
    GITHUB_RELEASES_PAGE,
    UPDATE_CHECK_INTERVAL_HOURS,
    get_latest_version,
    parse_version,
    update_available,
)

# Update-dismissal (7-day snooze) state - shared by the web banner and
# the CLI notice (see module docstring)
from .update_dismissal import (
    DISMISS_SNOOZE_DAYS,
    is_dismissed,
    record_dismissal,
)

# User identity migration utilities (stable Plex account id -> username)
from .user_migration import (
    USER_ID_MAP_FILENAME,
    cleanup_orphaned_user_collections,
    detect_renamed_users,
    get_live_plex_user_map,
    load_user_id_map,
    migrate_cache_files,
    migrate_renamed_plex_users,
    rename_user_in_users_list,
    rename_user_preferences_key,
    save_user_id_map,
)

# Define __all__ for explicit public API
__all__ = [
    # Config
    "__version__",
    "CACHE_VERSION",
    "TOP_CAST_COUNT",
    "TMDB_RATE_LIMIT_DELAY",
    "DEFAULT_RATING",
    "WEIGHT_SUM_TOLERANCE",
    "DEFAULT_LIMIT_PLEX_RESULTS",
    "TOP_POOL_PERCENTAGE",
    "MEDIA_TYPE_MOVIE",
    "MEDIA_TYPE_TV",
    "MEDIA_KEY_MOVIES",
    "MEDIA_KEY_SHOWS",
    "TIER_SAFE_PERCENT",
    "TIER_DIVERSE_PERCENT",
    "TIER_WILDCARD_PERCENT",
    "DEFAULT_RATING_MULTIPLIERS",
    "RATING_MULTIPLIERS",
    "UPDATE_MODES",
    "check_cache_version",
    "get_config_section",
    "get_tmdb_config",
    "load_config",
    "get_rating_multipliers",
    "adapt_config_for_media_type",
    "get_libraries",
    "get_libraries_for_media_type",
    "get_effective_arr_config",
    "get_update_mode",
    # Self-update (frozen binaries only)
    "SelfUpdateError",
    "NotFrozenError",
    "UnsupportedPlatformError",
    "NoUpdateAvailableError",
    "DownloadError",
    "SignatureVerificationError",
    "HashMismatchError",
    "SwapError",
    "PINNED_SIGNING_KEY_FINGERPRINT",
    "select_asset_name",
    "determine_update_target",
    "verify_pinned_signature",
    "parse_sha256sums",
    "sha256_file",
    "verify_downloaded_asset",
    "swap_binary",
    "cleanup_stale_old_binary",
    "current_binary_path",
    "sanitize_frozen_relaunch_env",
    "VerifiedUpdate",
    "download_and_verify_update",
    "perform_self_update",
    # Update check
    "GITHUB_RELEASES_API",
    "GITHUB_RELEASES_PAGE",
    "UPDATE_CHECK_INTERVAL_HOURS",
    "parse_version",
    "get_latest_version",
    "update_available",
    # Update dismissal (7-day snooze)
    "DISMISS_SNOOZE_DAYS",
    "record_dismissal",
    "is_dismissed",
    # Display
    "RED",
    "GREEN",
    "YELLOW",
    "CYAN",
    "RESET",
    "ANSI_PATTERN",
    "ColoredFormatter",
    "JsonFormatter",
    "TeeLogger",
    "setup_logging",
    "print_user_header",
    "print_user_footer",
    "print_status",
    "log_warning",
    "log_error",
    "clickable_link",
    "show_progress",
    "format_media_output",
    "print_similarity_breakdown",
    "user_select_recommendations",
    "smart_open_html",
    # TMDB
    "LANGUAGE_CODES",
    "get_full_language_name",
    "fetch_tmdb_with_retry",
    "get_tmdb_id_for_item",
    "get_tmdb_keywords",
    # Cache
    "save_json_cache",
    "load_json_cache",
    "load_media_cache",
    "save_media_cache",
    "save_watched_cache",
    # Labels
    "build_label_name",
    "categorize_labeled_items",
    "remove_labels_from_items",
    "add_labels_to_items",
    # Scoring
    "GENRE_NORMALIZATION",
    "normalize_genre",
    "normalize_user_profile",
    "fuzzy_keyword_match",
    "calculate_recency_multiplier",
    "calculate_rewatch_multiplier",
    "calculate_similarity_score",
    "select_tiered_recommendations",
    # Counters
    "create_empty_counters",
    "process_counters_from_cache",
    # Helpers
    "TITLE_SUFFIXES_TO_STRIP",
    "get_project_root",
    "migrate_legacy_cache_dir",
    "normalize_title",
    "map_path",
    "cleanup_old_logs",
    "compute_profile_hash",
    # CLI
    "get_users_from_config",
    "resolve_admin_username",
    "update_config_for_user",
    "setup_log_file",
    "teardown_log_file",
    "print_runtime",
    "print_update_notice",
    "run_recommender_main",
    # Plex
    "init_plex",
    "get_plex_account_ids",
    "get_watched_movie_count",
    "get_watched_show_count",
    "fetch_plex_watch_history_movies",
    "fetch_plex_watch_history_shows",
    "fetch_watch_history_with_tmdb",
    "update_plex_collection",
    "cleanup_old_collections",
    "get_configured_users",
    "get_current_users",
    "get_excluded_genres_for_user",
    "get_max_rating_for_user",
    "is_rating_allowed",
    "MOVIE_RATING_HIERARCHY",
    "TV_RATING_HIERARCHY",
    "get_user_specific_connection",
    "find_plex_movie",
    "extract_genres",
    "extract_ids_from_guids",
    "extract_rating",
    "get_library_imdb_ids",
    "get_plex_user_ids",
    # User migration (stable Plex id -> username)
    "USER_ID_MAP_FILENAME",
    "load_user_id_map",
    "save_user_id_map",
    "get_live_plex_user_map",
    "detect_renamed_users",
    "rename_user_preferences_key",
    "rename_user_in_users_list",
    "migrate_cache_files",
    "cleanup_orphaned_user_collections",
    "migrate_renamed_plex_users",
    # Trakt
    "TRAKT_RATE_LIMIT_DELAY",
    "TraktAuthError",
    "TraktAPIError",
    "TraktClient",
    "create_trakt_client",
    "get_authenticated_trakt_client",
    "fetch_tmdb_details_for_profile",
    "enhance_profile_with_trakt",
    # Trakt Discovery
    "DISCOVERY_CACHE_TTL",
    "get_trending_items",
    "get_popular_items",
    "get_anticipated_items",
    "get_recommended_items",
    "discover_from_trakt",
    "get_trakt_discovery_candidates",
    # Sonarr
    "SonarrAPIError",
    "SonarrClient",
    "create_sonarr_client",
    "create_sonarr_client_from",
    # Radarr
    "RadarrAPIError",
    "RadarrClient",
    "create_radarr_client",
    "create_radarr_client_from",
    # MDBList
    "MDBListAPIError",
    "MDBListClient",
    "create_mdblist_client",
    # Simkl
    "SimklAuthError",
    "SimklAPIError",
    "SimklClient",
    "create_simkl_client",
    "get_authenticated_simkl_client",
    # Tautulli
    "TautulliAPIError",
    "TautulliClient",
    "TautulliHistoryItem",
    "create_tautulli_client",
    "build_user_map",
    "map_users",
    "fetch_tautulli_movie_history",
    "fetch_tautulli_show_watched_data",
    "merge_movie_history",
    "merge_show_watched_data",
    # Metrics
    "DURATION_BUCKETS",
    "record_recommender_run",
    "record_api_call",
    "track_api_call",
    "record_cache_lookup",
    "record_self_update_attempt",
    "record_unhandled_error",
    "render_prometheus_text",
]
