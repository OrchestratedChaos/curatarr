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

# Calibrated recommendation re-ranking (Steck, RecSys 2018)
from .calibration import (
    CalibrationDimension,
    build_certificate_distribution,
    build_target_distribution,
    calibrate_multi,
    calibrate_recommendations,
    calibration_report,
    item_genre_distribution,
    kl_divergence,
    list_distribution,
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
    CALIBRATION_CERTIFICATE_WEIGHT,
    CALIBRATION_DIVERGENCE_SCALE,
    CALIBRATION_GENRE_WEIGHT,
    CALIBRATION_SMOOTHING_ALPHA,
    CANDIDATE_BUFFER_MULTIPLIER,
    COLLECTION_BONUS_BASE,
    COLLECTION_BONUS_CAP,
    COLLECTION_BONUS_LOG_FACTOR,
    DEFAULT_CALIBRATION_STRENGTH,
    DEFAULT_LIMIT_RESULTS,
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_NEGATIVE_MULTIPLIERS,
    DEFAULT_NEGATIVE_THRESHOLD,
    DEFAULT_RATING,
    DEFAULT_RATING_MULTIPLIERS,
    IDF_MIN_CORPUS_SIZE,
    IDF_MIN_WEIGHT,
    IGNORED_REC_MAX_PROFILE_FRACTION,
    IGNORED_REC_MIN_DAYS_SHOWN,
    IGNORED_REC_PENALTY,
    MEDIA_KEY_MOVIES,
    MEDIA_KEY_SHOWS,
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_TV,
    PLEX_REQUEST_TIMEOUT,
    POOL_DEPLETION_RATIO,
    RADARR_REQUEST_TIMEOUT,
    RATING_MULTIPLIER_2_STAR,
    RATING_MULTIPLIER_3_STAR,
    RATING_MULTIPLIER_4_STAR,
    RATING_MULTIPLIER_5_STAR,
    RATING_MULTIPLIER_UNRATED,
    RATING_TIER_3_STAR,
    RATING_TIER_4_STAR,
    RATING_TIER_5_STAR,
    RECOMMEND_FOR_NO_HISTORY_DEFAULT,
    SONARR_REQUEST_TIMEOUT,
    SUPPLY_GAP_MIN_PROFILE_SHARE,
    SUPPLY_GAP_MIN_SHORTFALL,
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
    load_resolved_config,
    resolve_media_type_overrides,
)

# Corpus-level IDF - the missing half of scoring's "TF-IDF"
from .corpus_idf import (
    build_corpus_idf,
    build_document_frequency,
    describe_least_informative,
    idf_weight,
)

# Counter utilities
from .counters import (
    build_profile_from_counters,
    create_empty_counters,
    process_counters_from_cache,
)

# Display utilities
from .display import (
    ANSI_PATTERN,
    CURATARR_LOG_LEVEL_ENV_VAR,
    CYAN,
    GREEN,
    LOG_VERBOSITY_DEFAULT,
    LOG_VERBOSITY_LEVELS,
    RED,
    RESET,
    YELLOW,
    ColoredFormatter,
    JsonFormatter,
    TeeLogger,
    clickable_link,
    format_media_output,
    log_error,
    log_info,
    log_warning,
    print_similarity_breakdown,
    print_status,
    print_user_footer,
    print_user_header,
    resolve_log_level,
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
    get_code_root,
    get_project_root,
    map_path,
    migrate_legacy_cache_dir,
    normalize_title,
)

# Negative feedback from declined recommendations
from .ignored_recs import (
    apply_ignored_penalties,
    find_ignored_recommendations,
)

# Integration-health signal (explicit, structured last-attempt status -
# see module docstring for why this exists instead of log-string matching)
from .integration_status import (
    get_integration_status,
    record_integration_status,
)

# Label utilities
from .labels import (
    DEFAULT_MOVIE_NAME_TEMPLATE,
    DEFAULT_TV_NAME_TEMPLATE,
    add_labels_to_items,
    build_label_name,
    categorize_labeled_items,
    remove_labels_from_items,
    render_collection_name,
)

# Library supply health - is ranking still the constraint?
from .library_health import (
    PoolHealth,
    SupplyGap,
    assess_pool_health,
    find_supply_gaps,
    format_health_report,
    gaps_to_dict,
    prioritize_discovery_genres,
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
    cleanup_legacy_unnamed_collection,
    cleanup_old_collections,
    extract_genres,
    extract_ids_from_guids,
    extract_rating,
    fetch_plex_libraries,
    fetch_plex_users,
    fetch_plex_watch_history_movies,
    fetch_plex_watch_history_shows,
    fetch_show_completion_data,
    fetch_watch_history_with_tmdb,
    find_plex_movie,
    get_configured_users,
    get_current_users,
    get_excluded_genres_for_user,
    get_library_imdb_ids,
    get_library_imdb_ids_from_items,
    get_plex_account_ids,
    get_plex_user_ids,
    get_streaming_services_for_user,
    get_user_specific_connection,
    get_watched_movie_count,
    get_watched_show_count,
    identify_dropped_shows,
    init_plex,
    remove_owned_collection,
    update_plex_collection,
)

# Plex rating/label POLICY (split from .plex - see utils/plex_policy.py's
# own module docstring for why these four specifically live here instead)
from .plex_policy import (
    MOVIE_RATING_HIERARCHY,
    TV_RATING_HIERARCHY,
    apply_user_label_restrictions,
    get_max_rating_for_user,
    is_rating_allowed,
)

# Radarr utilities
from .radarr import (
    RadarrAPIError,
    RadarrClient,
    create_radarr_client,
    create_radarr_client_from,
)

# Explicit, structured per-(engine, user) recommender run status (#292 -
# see module docstring for why this replaces log-tail marker matching)
from .run_status import (
    get_latest_run_status_for_user,
    get_run_status,
    record_run_status,
)

# Scheduler utilities (#264)
from .scheduler import (
    WEEKDAY_NAMES,
    compute_next_run,
    describe_next_run,
    parse_schedule_config,
    resolve_scheduler_timezone,
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
    derive_trakt_list_slug,
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
    "CALIBRATION_CERTIFICATE_WEIGHT",
    "CALIBRATION_DIVERGENCE_SCALE",
    "CALIBRATION_GENRE_WEIGHT",
    "IDF_MIN_CORPUS_SIZE",
    "IDF_MIN_WEIGHT",
    "IGNORED_REC_MAX_PROFILE_FRACTION",
    "IGNORED_REC_MIN_DAYS_SHOWN",
    "IGNORED_REC_PENALTY",
    "POOL_DEPLETION_RATIO",
    "SUPPLY_GAP_MIN_PROFILE_SHARE",
    "SUPPLY_GAP_MIN_SHORTFALL",
    "CALIBRATION_SMOOTHING_ALPHA",
    "DEFAULT_CALIBRATION_STRENGTH",
    "DEFAULT_LIMIT_RESULTS",
    "DEFAULT_MIN_SIMILARITY",
    "CANDIDATE_BUFFER_MULTIPLIER",
    "TOP_POOL_PERCENTAGE",
    "MEDIA_TYPE_MOVIE",
    "MEDIA_TYPE_TV",
    "MEDIA_KEY_MOVIES",
    "MEDIA_KEY_SHOWS",
    "TIER_SAFE_PERCENT",
    "TIER_DIVERSE_PERCENT",
    "TIER_WILDCARD_PERCENT",
    "DEFAULT_RATING_MULTIPLIERS",
    "UPDATE_MODES",
    "check_cache_version",
    "get_config_section",
    "get_tmdb_config",
    "load_config",
    "load_resolved_config",
    "resolve_media_type_overrides",
    "get_rating_multipliers",
    "get_libraries",
    "get_libraries_for_media_type",
    "get_effective_arr_config",
    "get_update_mode",
    "get_negative_multiplier",
    "get_negative_signals_config",
    "PLEX_REQUEST_TIMEOUT",
    "SONARR_REQUEST_TIMEOUT",
    "RADARR_REQUEST_TIMEOUT",
    "TMDB_REQUEST_TIMEOUT",
    "TMDB_ANIMATION_GENRE_ID",
    "TMDB_TV_MOVIE_GENRE_ID",
    "DEFAULT_NEGATIVE_MULTIPLIERS",
    "DEFAULT_NEGATIVE_THRESHOLD",
    "RECOMMEND_FOR_NO_HISTORY_DEFAULT",
    "COLLECTION_BONUS_BASE",
    "COLLECTION_BONUS_CAP",
    "COLLECTION_BONUS_LOG_FACTOR",
    "RATING_MULTIPLIER_2_STAR",
    "RATING_MULTIPLIER_3_STAR",
    "RATING_MULTIPLIER_4_STAR",
    "RATING_MULTIPLIER_5_STAR",
    "RATING_MULTIPLIER_UNRATED",
    "RATING_TIER_3_STAR",
    "RATING_TIER_4_STAR",
    "RATING_TIER_5_STAR",
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
    "LOG_VERBOSITY_DEFAULT",
    "LOG_VERBOSITY_LEVELS",
    "CURATARR_LOG_LEVEL_ENV_VAR",
    "resolve_log_level",
    "setup_logging",
    "print_user_header",
    "print_user_footer",
    "print_status",
    "log_warning",
    "log_error",
    "log_info",
    "clickable_link",
    "show_progress",
    "format_media_output",
    "print_similarity_breakdown",
    "user_select_recommendations",
    "smart_open_html",
    # TMDB
    "LANGUAGE_CODES",
    "IMDB_TMDB_CACHE_VERSION",
    "get_full_language_name",
    "fetch_tmdb_with_retry",
    "get_tmdb_id_for_item",
    "get_tmdb_id_from_imdb",
    "get_tmdb_keywords",
    "load_imdb_tmdb_cache",
    "save_imdb_tmdb_cache",
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
    # Corpus IDF
    "build_corpus_idf",
    "build_document_frequency",
    "describe_least_informative",
    "idf_weight",
    # Ignored-recommendation negative feedback
    "apply_ignored_penalties",
    "find_ignored_recommendations",
    # Library supply health
    "PoolHealth",
    "SupplyGap",
    "assess_pool_health",
    "find_supply_gaps",
    "format_health_report",
    "gaps_to_dict",
    "prioritize_discovery_genres",
    # Calibration
    "CalibrationDimension",
    "build_certificate_distribution",
    "build_target_distribution",
    "calibrate_multi",
    "calibrate_recommendations",
    "calibration_report",
    "item_genre_distribution",
    "kl_divergence",
    "list_distribution",
    # Counters
    "build_profile_from_counters",
    "create_empty_counters",
    "process_counters_from_cache",
    # Helpers
    "TITLE_SUFFIXES_TO_STRIP",
    "get_code_root",
    "get_project_root",
    "migrate_legacy_cache_dir",
    "normalize_title",
    "map_path",
    "cleanup_old_logs",
    "compute_profile_hash",
    # Integration-health signal
    "record_integration_status",
    "get_integration_status",
    # Per-(engine, user) recommender run status
    "record_run_status",
    "get_run_status",
    "get_latest_run_status_for_user",
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
    "remove_owned_collection",
    "cleanup_old_collections",
    "cleanup_legacy_unnamed_collection",
    "render_collection_name",
    "DEFAULT_MOVIE_NAME_TEMPLATE",
    "DEFAULT_TV_NAME_TEMPLATE",
    "fetch_plex_users",
    "fetch_plex_libraries",
    "resolve_scheduler_timezone",
    "parse_schedule_config",
    "compute_next_run",
    "describe_next_run",
    "WEEKDAY_NAMES",
    "get_configured_users",
    "get_current_users",
    "get_excluded_genres_for_user",
    "get_streaming_services_for_user",
    "get_max_rating_for_user",
    "is_rating_allowed",
    "MOVIE_RATING_HIERARCHY",
    "TV_RATING_HIERARCHY",
    "apply_user_label_restrictions",
    "get_user_specific_connection",
    "find_plex_movie",
    "extract_genres",
    "extract_ids_from_guids",
    "extract_rating",
    "get_library_imdb_ids",
    "get_library_imdb_ids_from_items",
    "get_plex_user_ids",
    "fetch_show_completion_data",
    "identify_dropped_shows",
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
    "TRAKT_ENHANCE_CACHE_VERSION",
    "TraktAuthError",
    "TraktAPIError",
    "TraktClient",
    "create_trakt_client",
    "derive_trakt_list_slug",
    "get_authenticated_trakt_client",
    "fetch_tmdb_details_for_profile",
    "enhance_profile_with_trakt",
    "load_trakt_enhance_cache",
    "save_trakt_enhance_cache",
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
