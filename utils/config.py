"""
Configuration utilities for Curatarr.
Handles config loading, section access, and rating multipliers.
"""

import json
import os
import re
from typing import Dict, List, Optional

import yaml

from .display import log_error, log_info, log_warning

# Project version - single source of truth
__version__ = "2.10.69"

# Cache version - bump this when cache format changes to auto-invalidate old caches
CACHE_VERSION = 5  # v5: Added rating/vote_count to TV show cache entries so
# tv: quality_filters (min_rating/min_vote_count) actually apply - they were
# previously silently a no-op for TV (see CHANGELOG). Bumping this forces a
# one-time full rebuild of BOTH the movie and show caches on next run (this
# constant isn't tracked per-media-type) - existing cache files are deleted
# and every item is re-fetched from TMDB from scratch, so no show is ever
# left with a half-populated/missing rating that would be misread as 0 and
# wrongly filtered out.

# Common constants used across recommenders
TOP_CAST_COUNT = 3  # Number of top actors to consider
TMDB_RATE_LIMIT_DELAY = 0.5  # Seconds between TMDB API calls
DEFAULT_RATING = 5.0  # Default rating when none available
WEIGHT_SUM_TOLERANCE = 1e-6  # Tolerance for weight sum validation
# Final recommendation/collection count per media type - the
# config/tuning.yml movies:/tv: `limit_results` value (documented since
# it shipped, but never actually read anywhere until PR1 of the 2026-07
# audit remediation batch - see CHANGELOG). recommenders/base.py reads
# this dict as the fallback when limit_results is unset, so existing
# installs keep exactly today's effective 50/20 behavior.
DEFAULT_LIMIT_RESULTS = {"movie": 50, "tv": 20}
# How many scoring candidates recommenders/base.py generates per
# limit_results item (self.limit_plex_results), so the best-scoring
# items can compete against whatever a prior run already labeled
# instead of being capped at exactly the final collection size. Was
# previously two independent hardcoded 100/40-vs-50/20 literals at two
# call sites in recommenders/base.py; now derived from limit_results so
# the buffer scales with it automatically.
CANDIDATE_BUFFER_MULTIPLIER = 2
TOP_POOL_PERCENTAGE = 0.1  # Top 10% for randomization pool

# Media type constants - use these instead of hardcoded strings
MEDIA_TYPE_MOVIE = "movie"
MEDIA_TYPE_TV = "tv"
MEDIA_KEY_MOVIES = "movies"
MEDIA_KEY_SHOWS = "shows"

# Recommendation tier percentages (for diversified recommendations)
# Safe: High-confidence picks similar to user's taste
# Diverse: Mid-tier picks that introduce variety
# Wildcard: Lower-scored discoveries for exploration
TIER_SAFE_PERCENT = 0.6  # 60% safe picks from top scores
TIER_DIVERSE_PERCENT = 0.3  # 30% diverse picks from mid-tier
TIER_WILDCARD_PERCENT = 0.1  # 10% wildcard picks for discovery

# TF-IDF scoring penalties for rare/unseen content attributes
TFIDF_GENRE_PENALTY = 0.3  # Max 30% penalty per rare genre
TFIDF_KEYWORD_PENALTY = 0.15  # Max 15% penalty per rare keyword
UNSEEN_GENRE_PENALTY = 0.1  # Penalty for genres user has never watched
UNSEEN_KEYWORD_PENALTY = 0.02  # Penalty for keywords user has never seen

# Popularity dampening for very popular content (prevents blockbusters dominating)
POPULARITY_DAMPENING_FACTOR = 0.03  # ~3% penalty per order of magnitude above threshold
POPULARITY_DAMPENING_CAP = 0.90  # Cap at 10% max penalty (minimum multiplier)

# Default rating multipliers for similarity scoring (Plex uses 0-10 scale)
# Higher ratings = stronger signal. 5-star (10) boosted to emphasize favorites.
DEFAULT_RATING_MULTIPLIERS = {
    0: 0.1,  # Strong dislike
    1: 0.2,  # Very poor
    2: 0.4,  # Poor
    3: 0.6,  # Below average
    4: 0.8,  # Slightly below average
    5: 1.0,  # Neutral/baseline
    6: 1.2,  # Slightly above average
    7: 1.4,  # Good
    8: 1.7,  # Very good
    9: 2.0,  # Excellent
    10: 2.5,  # Outstanding (5 stars) - strong signal
}

# Default negative multipliers for low-rated content (ratings 0-3 become penalties)
# These are applied instead of positive multipliers when rating <= threshold
DEFAULT_NEGATIVE_MULTIPLIERS = {
    0: -1.0,  # Strong dislike -> strong penalty
    1: -0.8,  # Very poor -> significant penalty
    2: -0.5,  # Poor -> moderate penalty
    3: -0.3,  # Below average -> mild penalty
}

# Default threshold for negative signals (Plex 0-10 scale)
DEFAULT_NEGATIVE_THRESHOLD = 3  # Ratings 0-3 become negative signals

# Rating tier thresholds (Plex uses 0-10 scale, Plex UI shows 0-5 stars)
RATING_TIER_5_STAR = 9.0  # 5 stars: ratings 9-10
RATING_TIER_4_STAR = 7.0  # 4 stars: ratings 7-8
RATING_TIER_3_STAR = 5.0  # 3 stars: ratings 5-6

# Rating tier multipliers for preference weighting
RATING_MULTIPLIER_5_STAR = 1.0  # Strong preference
RATING_MULTIPLIER_4_STAR = 0.75  # Moderate preference
RATING_MULTIPLIER_3_STAR = 0.5  # Weak preference
RATING_MULTIPLIER_2_STAR = 0.25  # Very weak preference
RATING_MULTIPLIER_UNRATED = 0.6  # Default for unrated content

# HTTP request timeouts (seconds)
PLEX_REQUEST_TIMEOUT = 30
TMDB_REQUEST_TIMEOUT = 10
SONARR_REQUEST_TIMEOUT = 30
RADARR_REQUEST_TIMEOUT = 30

# A handful of Plex calls (e.g. a watch-history page fetch of up to
# 10000 items) legitimately take longer than the default request timeout
# above - this is a deliberate, separate ceiling for just those call
# sites, not a general Plex timeout.
PLEX_LONG_REQUEST_TIMEOUT = 60

# Cap on any single log file under logs/ before cleanup_old_logs() force-
# truncates it, regardless of its mtime. Needed because an append-only log
# (e.g. a cron job's `>> logs/daily-run.log` redirect) has its mtime
# refreshed on every write, so the normal age-based retention_days cleanup
# below can never delete it - left unchecked it grows forever. 20MB is
# comfortably larger than any single run's own log output.
MAX_LOG_FILE_BYTES = 20 * 1024 * 1024

# Collection bonus parameters (for movies in user's started collections)
COLLECTION_BONUS_BASE = 0.05  # Base bonus multiplier
COLLECTION_BONUS_LOG_FACTOR = 0.5  # Log scaling factor for collection size
COLLECTION_BONUS_CAP = 0.15  # Maximum 15% bonus

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
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        cached_version = data.get("cache_version", 1)  # Default to v1 if not present

        if cached_version < CACHE_VERSION:
            print(f"\033[93m{cache_type} is outdated (v{cached_version} < v{CACHE_VERSION}), rebuilding...\033[0m")
            os.remove(cache_path)
            return False

        return True
    except Exception as e:
        print(f"\033[93mError reading {cache_type}, rebuilding: {e}\033[0m")
        return False


def get_config_section(config: Dict, key: str, default: Optional[Dict] = None) -> Dict:
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
    tmdb_config = get_config_section(config, "tmdb")
    return {
        "api_key": tmdb_config.get("api_key"),
        "use_keywords": tmdb_config.get("use_tmdb_keywords", tmdb_config.get("use_TMDB_keywords", True)),
    }


def _deep_merge_dicts(base: dict, override: dict) -> dict:
    """
    Recursively merge `override` on top of `base`, returning a new dict.

    Precedence: `override` wins for any key it defines. Root/base keys that
    `override` does not mention are preserved untouched.

    - If both `base[key]` and `override[key]` are dicts, they are merged
      recursively (so `override` only needs to specify the sub-keys it
      wants to change; sibling sub-keys from `base` survive).
    - Any other value type - including lists - is replaced outright by
      `override`'s value. Lists are NOT concatenated/deduped; redefining a
      list means replacing it wholesale, which matches how config authors
      expect to override a list (e.g. `users.list`, exclude-genre lists).
    """
    merged = dict(base)
    for key, value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(base_value, value)
        else:
            merged[key] = value
    return merged


def _load_module_configs(config: dict, config_dir: str) -> dict:
    """
    Load and merge modular config files into the main config.

    Loads tuning.yml, trakt.yml, radarr.yml, sonarr.yml if they exist.

    Precedence: for any top-level key a module file defines, that module
    file wins. Dict-valued keys are deep-merged (see `_deep_merge_dicts`),
    so e.g. tuning.yml's `users.preferences` does not silently wipe
    config.yml's `users.list` just because both files happen to define a
    top-level `users:` key - only the sub-keys tuning.yml actually
    specifies are overridden. Non-dict values (including lists) are
    replaced outright, never merged.
    """
    # Tuning modules merge their sections into root
    tuning_path = os.path.join(config_dir, "tuning.yml")
    if os.path.exists(tuning_path):
        try:
            with open(tuning_path, "r", encoding="utf-8") as f:
                tuning = yaml.safe_load(f)
                if tuning:
                    config = _deep_merge_dicts(config, tuning)
                    log_info("Loaded tuning.yml")
        except Exception as e:
            log_warning(f"Could not load tuning.yml: {e}")

    # Feature modules go under their key, but still deep-merge in case
    # config.yml already carries a same-named section (e.g. pre-migration
    # leftovers) - same precedence rule as above applies within that key.
    for module in ["trakt", "radarr", "sonarr"]:
        module_path = os.path.join(config_dir, f"{module}.yml")
        if os.path.exists(module_path):
            try:
                with open(module_path, "r", encoding="utf-8") as f:
                    module_config = yaml.safe_load(f)
                    if module_config:
                        existing = config.get(module)
                        if isinstance(existing, dict):
                            config[module] = _deep_merge_dicts(existing, module_config)
                        else:
                            config[module] = module_config
                        log_info(f"Loaded {module}.yml")
            except Exception as e:
                log_warning(f"Could not load {module}.yml: {e}")

    return config


def _auto_migrate_if_needed(config: dict, config_path: str) -> dict:
    """
    Auto-migrate monolithic config to modular format if needed.

    Returns the migrated config (reloaded after migration).
    """
    # Import here to avoid circular imports
    from utils.migrate_config import migrate_config, needs_migration

    if needs_migration(config):
        print("\033[93mDetected legacy config format, migrating to modular files...\033[0m")
        result = migrate_config(config_path)
        if result["migrated"]:
            print("\033[92mConfig migration complete!\033[0m")
            # Reload the now-split config
            with open(config_path, "r", encoding="utf-8") as f:
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
        with open(config_path, "r", encoding="utf-8") as file:
            config = yaml.safe_load(file)
            # #262: this (and every print() in _load_module_configs
            # above) fired on EVERY load_config() call with no level
            # control - web/app.py calls load_config() 1-2x per page
            # render (dashboard/config context-processor/route handler),
            # so a container's logs filled with "Loaded tuning.yml"-style
            # lines on every request, which read as if the container
            # were repeatedly restarting. Converted to the project
            # logger: the CLI (utils/cli.py) always calls setup_logging()
            # first, which attaches a handler at INFO by default, so
            # normal CLI runs see exactly the same lines as before at
            # the same default visibility. web/app.py and
            # web/docker_server.py never call setup_logging() at all,
            # so with no handler configured these fall through to
            # Python's WARNING-only last-resort handler and are silent
            # by default there - see web/app.py's own config-load cache
            # (added in this same PR) for the other half of this fix.
            log_info(f"Successfully loaded configuration from {config_path}")

        config_dir = os.path.dirname(config_path) or "."

        # Auto-migrate legacy monolithic config if needed
        config = _auto_migrate_if_needed(config, config_path)

        # Load and merge modular config files
        config = _load_module_configs(config, config_dir)

        # Override with environment variables (security best practice)
        env_overrides = [
            ("PLEX_URL", "plex", "url"),
            ("PLEX_TOKEN", "plex", "token"),
            ("TMDB_API_KEY", "tmdb", "api_key"),
        ]

        for env_var, section, key in env_overrides:
            value = os.environ.get(env_var)
            if value:
                if section not in config:
                    config[section] = {}
                config[section][key] = value
                log_info(f"Using {env_var} from environment")

        return config
    except Exception as e:
        log_error(f"Error loading config from {config_path}: {e}")
        raise


# Valid values for general.update_mode (see get_update_mode() below).
UPDATE_MODES = ("notify", "force", "off")


def get_update_mode(config: dict) -> str:
    """
    Resolve the effective general.update_mode, with legacy fallback to
    general.auto_update for installs that predate update_mode.

    Back-compat contract: an existing install's behavior must not
    change on upgrade, so auto_update is still read here (never
    removed by anything in this codebase) even though update_mode is
    now the preferred key:
      - general.update_mode present and valid -> used verbatim
      - general.update_mode present but not one of UPDATE_MODES ->
        'notify' (never silently force/disable updates from a typo'd
        or otherwise-unrecognized value)
      - general.update_mode absent, general.auto_update present ->
        True => 'force' (mirrors the old "auto_update: true" behavior:
        auto-apply signed updates on launch, no prompt), False =>
        'off' (mirrors the old silent-no-op behavior)
      - neither present -> 'notify' (new default: notify, don't force)

    Note: an unquoted `update_mode: off` in YAML parses as the Python
    boolean False, not the string 'off' - YAML 1.1's boolean literals
    include on/off/yes/no (both PyYAML's safe_load and ruamel.yaml's
    default resolver do this). That's handled explicitly below rather
    than requiring users/the web UI to always quote 'off'.

    Args:
        config: Root configuration dictionary (or a media-adapted one -
            both carry a 'general' section through unchanged)

    Returns:
        One of 'notify', 'force', 'off'
    """
    general = (config or {}).get("general") or {}
    mode = general.get("update_mode")
    if mode is False:
        return "off"
    if mode:
        return mode if mode in UPDATE_MODES else "notify"
    if "auto_update" in general:
        return "force" if general.get("auto_update") else "off"
    return "notify"


def get_rating_multipliers(config: Optional[dict] = None) -> dict:
    """
    Get rating multipliers from config or use defaults.

    Config uses 5-star scale, Plex uses 10-point scale.
    Maps: star_5 -> 9-10, star_4 -> 7-8, star_3 -> 5-6, star_2 -> 3-4, star_1 -> 1-2

    Args:
        config: Configuration dict with optional rating_multipliers section

    Returns:
        Dict mapping Plex ratings (0-10) to multiplier values
    """
    if not config or "rating_multipliers" not in config:
        return DEFAULT_RATING_MULTIPLIERS.copy()

    rm = config["rating_multipliers"]

    # Get values from config with defaults
    star_5 = rm.get("star_5", 2.5)
    star_4 = rm.get("star_4", 1.7)
    star_3 = rm.get("star_3", 1.0)
    star_2 = rm.get("star_2", 0.4)
    star_1 = rm.get("star_1", 0.2)

    # Map 5-star config to 10-point Plex scale
    return {
        0: 0.1,  # Unrated/dislike
        1: star_1,  # 1 star
        2: star_1 + (star_2 - star_1) * 0.5,  # Between 1-2 stars
        3: star_2,  # 2 stars
        4: star_2 + (star_3 - star_2) * 0.5,  # Between 2-3 stars
        5: star_3,  # 3 stars (baseline)
        6: star_3 + (star_4 - star_3) * 0.5,  # Between 3-4 stars
        7: star_4,  # 4 stars
        8: star_4 + (star_5 - star_4) * 0.5,  # Between 4-5 stars
        9: star_5 - (star_5 - star_4) * 0.2,  # High 4 stars
        10: star_5,  # 5 stars
    }


def get_negative_signals_config(config: Optional[dict] = None) -> dict:
    """
    Get negative signals configuration with defaults.

    Args:
        config: Configuration dict with optional negative_signals section

    Returns:
        Dict with negative signal settings
    """
    if not config:
        return {
            "enabled": True,
            "bad_ratings": {
                "enabled": True,
                "threshold": DEFAULT_NEGATIVE_THRESHOLD,
                "cap_penalty": 0.5,
            },
            "dropped_shows": {
                "enabled": True,
                "min_episodes_watched": 2,
                "max_completion_percent": 25,
                "penalty_multiplier": -0.4,
            },
        }

    ns = config.get("negative_signals", {})

    # If master switch is off, return disabled config
    if not ns.get("enabled", True):
        return {"enabled": False, "bad_ratings": {"enabled": False}, "dropped_shows": {"enabled": False}}

    bad_ratings = ns.get("bad_ratings", {})
    dropped_shows = ns.get("dropped_shows", {})

    return {
        "enabled": True,
        "bad_ratings": {
            "enabled": bad_ratings.get("enabled", True),
            "threshold": bad_ratings.get("threshold", DEFAULT_NEGATIVE_THRESHOLD),
            "cap_penalty": bad_ratings.get("cap_penalty", 0.5),
        },
        "dropped_shows": {
            "enabled": dropped_shows.get("enabled", True),
            "min_episodes_watched": dropped_shows.get("min_episodes_watched", 2),
            "max_completion_percent": dropped_shows.get("max_completion_percent", 25),
            "penalty_multiplier": dropped_shows.get("penalty_multiplier", -0.4),
        },
    }


def get_negative_multiplier(rating: int, config: Optional[dict] = None) -> float:
    """
    Get the negative multiplier for a low rating.

    Args:
        rating: Plex rating (0-10 scale)
        config: Optional config with custom multipliers

    Returns:
        Negative multiplier value (negative float)
    """
    return DEFAULT_NEGATIVE_MULTIPLIERS.get(rating, -0.3)


def resolve_media_type_overrides(config: Dict, media_type: str) -> Dict:
    """
    Overlay resolved `movies:`/`tv:` (config/tuning.yml) per-media-type
    overrides onto an already-loaded root config (see load_config()).

    This is THE single resolution path for these keys - it replaces two
    formerly-independent implementations that had quietly drifted apart
    (see CHANGELOG 2.10.23/2.10.37/2.10.39 and this module's git history):
    `recommenders/base.py`'s own inline `self.media_config` resolution
    (LIVE - this is what every install's actual recommendations used),
    and this module's now-deleted `adapt_config_for_media_type()` (DEAD -
    computed a plausible-looking, differently-defaulted result that
    nothing in the recommendation-generation path ever read). Consolidated
    here so a future new `movies:`/`tv:` key only needs wiring up once,
    with a standing test (see tests/test_config.py's
    TestResolveMediaTypeOverridesKeyEnumeration) asserting every key
    documented in config/tuning.example.yml actually resolves.

    Mutates and returns `config` in place (matching load_config()'s own
    `_load_module_configs()` merge convention) with these additional/
    overwritten top-level keys: `limit_results`, `randomize_recommendations`,
    `normalize_counters`, `show_summary`, `show_genres`, `show_cast`,
    `show_language`, `show_rating`, `show_imdb_link`, `weights`, and
    (movies only) `show_director`.

    Every other root-level section (`plex`, `tmdb`, `users`, `plex_users`,
    `collections`, `recency_decay`, `rating_multipliers`, `general`,
    `cache_dir`, `libraries`, `negative_signals`, `radarr`, `sonarr`,
    `quality_filters`, and anything else) passes through completely
    untouched, because `config` itself (not a cherry-picked
    reconstruction of it) is what gets returned - there is no way for
    this function to silently drop a root-level key the way the old
    `adapt_config_for_media_type()` dropped `plex_users` (see CHANGELOG).

    Deliberately NOT resolved here (left exactly where they already
    correctly, non-divergently live):
      - `quality_filters` (`min_rating`/`min_vote_count`): still resolved
        by `BaseRecommender.get_recommendations()` at call time, straight
        from `self.media_config`/`self.config` - that was always the one
        correct, live implementation (the old dead path's 5.0/50 movies
        default never matched it - see CHANGELOG for which one won).
      - Per-field weight *defaults* (`director` vs `studio`, and their
        values): still resolved by `PlexMovieRecommender`/
        `PlexTVRecommender._load_weights()` - already the single,
        non-divergent source once the dead path is gone. Only the
        `movies:`/`tv:` -> legacy-root-level `weights:` fallback *chain*
        is centralized here (that part WAS duplicated, and divergently -
        the old dead path never checked the legacy root-level tier).

    Args:
        config: Root config dict, as returned by load_config()
        media_type: MEDIA_TYPE_MOVIE ('movie') or MEDIA_TYPE_TV ('tv')

    Returns:
        The same `config` dict, with the keys above added/overwritten
    """
    general_config = config.get("general", {}) or {}
    media_section = MEDIA_KEY_MOVIES if media_type == MEDIA_TYPE_MOVIE else "tv"
    media_config = config.get(media_section, config.get(media_section.upper(), {})) or {}

    config["limit_results"] = media_config.get("limit_results", DEFAULT_LIMIT_RESULTS[media_type])
    config["randomize_recommendations"] = media_config.get(
        "randomize_recommendations", general_config.get("randomize_recommendations", True)
    )
    config["normalize_counters"] = media_config.get(
        "normalize_counters", general_config.get("normalize_counters", True)
    )
    config["show_summary"] = media_config.get("show_summary", general_config.get("show_summary", False))
    config["show_genres"] = media_config.get("show_genres", general_config.get("show_genres", True))
    config["show_cast"] = media_config.get("show_cast", general_config.get("show_cast", False))
    config["show_language"] = media_config.get("show_language", general_config.get("show_language", False))
    config["show_rating"] = media_config.get("show_rating", general_config.get("show_rating", False))
    config["show_imdb_link"] = media_config.get("show_imdb_link", general_config.get("show_imdb_link", False))

    if media_type == MEDIA_TYPE_MOVIE:
        # movies-only: recommenders/movie.py's self.show_director (TV has
        # no director-equivalent display option).
        config["show_director"] = media_config.get("show_director", general_config.get("show_director", False))

    # Weights - only the movies:/tv: -> legacy-root-level `weights:`
    # fallback CHAIN is resolved here (see docstring above for why the
    # per-field defaults deliberately stay in _load_weights()).
    config["weights"] = media_config.get("weights", config.get("weights", {})) or {}

    return config


def load_resolved_config(config_path: str, media_type: str) -> Dict:
    """
    The one function a caller needs for a fully media-type-resolved
    config: load_config() (modular merge + auto-migration + env-var
    overrides) followed by resolve_media_type_overrides() (movies:/tv:
    per-media-type overrides) - see that function's docstring for exactly
    which keys this adds/overwrites and why the rest is untouched.

    Args:
        config_path: Path to config.yml file
        media_type: MEDIA_TYPE_MOVIE ('movie') or MEDIA_TYPE_TV ('tv')

    Returns:
        Parsed, merged, and media-type-resolved config dictionary
    """
    return resolve_media_type_overrides(load_config(config_path), media_type)


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
    MEDIA_TYPE_MOVIE: {"search": "search_for_movie"},
    MEDIA_TYPE_TV: {"search": "search_for_series"},
}

# Per-library routing fields eligible for field-level fallback to the global
# radarr/sonarr block, by media type. minimum_availability is movie-only,
# series_type is tv-only.
_ARR_ROUTING_FIELDS = {
    MEDIA_TYPE_MOVIE: ["root_folder", "quality_profile", "tag", "monitor", "search", "minimum_availability"],
    MEDIA_TYPE_TV: ["root_folder", "quality_profile", "tag", "monitor", "search", "series_type"],
}

# *arr instance/connection fields - overridable per-library via arr.instance
_ARR_INSTANCE_FIELDS = ["enabled", "url", "api_key"]

# Sensible boolean defaults for fields that should never resolve to None
_ARR_FIELD_DEFAULTS = {"enabled": False, "monitor": False, "search": False}


def _slugify_library_id(name: str) -> str:
    """
    Derive a stable slug id from a library name (e.g. "TV Shows" -> "tv-shows").

    Args:
        name: Library display name

    Returns:
        Lowercase, hyphenated slug. Falls back to 'library' if name is blank
        or has no alphanumeric characters.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "library"


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
    name = normalized.get("name") or normalized.get("id") or "Library"
    normalized["name"] = name
    normalized["id"] = normalized.get("id") or _slugify_library_id(name)
    normalized["media_type"] = normalized.get("media_type") or MEDIA_TYPE_MOVIE
    normalized["section"] = normalized.get("section") or name
    normalized.setdefault("arr", {})
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
    plex_config = get_config_section(config, "plex")
    movie_library = plex_config.get("movie_library", "Movies")
    tv_library = plex_config.get("tv_library", "TV Shows")

    return [
        {
            "id": _slugify_library_id(movie_library),
            "name": movie_library,
            "section": movie_library,
            "media_type": MEDIA_TYPE_MOVIE,
            "arr": {},
        },
        {
            "id": _slugify_library_id(tv_library),
            "name": tv_library,
            "section": tv_library,
            "media_type": MEDIA_TYPE_TV,
            "arr": {},
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
    raw_libraries = config.get("libraries")

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
    return [lib for lib in get_libraries(config) if lib.get("media_type") == media_type]


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
    media_type = library.get("media_type") or MEDIA_TYPE_MOVIE
    arr_key = "radarr" if media_type == MEDIA_TYPE_MOVIE else "sonarr"
    global_arr = get_config_section(config, arr_key)
    library_arr = library.get("arr") or {}
    instance = library_arr.get("instance") or {}
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
