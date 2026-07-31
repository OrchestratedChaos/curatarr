"""
Configuration utilities for Curatarr.
Handles config loading, section access, and rating multipliers.
"""

import difflib
import json
import os
import re
from typing import Dict, List, Optional

import yaml

from .display import log_error, log_info, log_warning

# Project version - single source of truth
__version__ = "2.12.0"

# Cache version - bump this when cache format changes to auto-invalidate old caches
CACHE_VERSION = 9  # v9: new cache FIELD - `content_rating` per item
# (recommenders/movie.py/tv.py _process_item), used by calibration to hold
# a collection to the profile's certificate mix and not just its genre mix.
# Unlike v6-v8 this is a genuine format change: without a rebuild the field
# is simply absent and certificate calibration silently no-ops.
#
# v8: SCORING change - corpus IDF (utils/corpus_idf.py)
# now discounts genre/keyword matches by how ubiquitous the term is across
# the library. Same reasoning as v7/v6 below: per-item scores are cached
# against profile_hash, which captures the user's profile but NOT the
# scoring code, so without this bump every existing install would keep
# serving scores computed by the pre-IDF algorithm and the change would be
# invisible until each user's profile happened to shift.
#
# v7: another SCORING change, not a format one - the
# MAX_REDISTRIBUTION_MULTIPLIER cap below (2.10.90). Same reasoning as v6
# immediately after this: a scoring change that doesn't move this constant
# is invisible on every existing install.
#
# v6: NOT a format change - a SCORING change (2.10.85's
# per-item weight-redistribution fix, see CHANGELOG). Cached per-item scores
# are keyed on profile_hash alone (recommenders/base.py's cache-hit branch in
# get_recommendations()), which captures the user's watch profile but NOT the
# scoring code that produced the number. So a scoring fix is a silent no-op on
# every existing install - every item is a cache hit against a score computed
# by the OLD algorithm - until that user's profile happens to change. Confirmed
# empirically while shipping 2.10.85: the fix changed nothing on a real install
# until this constant moved. Any future change to how scores are CALCULATED
# needs this bump too, not just changes to what the cache stores.
#
# v5: Added rating/vote_count to TV show cache entries so
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

# Library supply health (utils/library_health.py). Below this
# candidates-per-slot ratio the selection stage has effectively no
# discretion left - at 1:1 "the top N" is just "all of them", and
# quality floors or calibration downstream have nothing to choose
# between. Flagged so an exhausted library is reported as such instead
# of silently producing weak collections. 3:1 is the point at which
# eviction stops being able to meaningfully improve a collection.
POOL_DEPLETION_RATIO = 3.0
# Minimum share of a profile a genre must hold before a shortfall in it
# is worth reporting - a genre the user barely watches is not a supply
# problem.
SUPPLY_GAP_MIN_PROFILE_SHARE = 0.03
# Minimum profile-vs-available shortfall before it counts as a gap
# rather than noise.
SUPPLY_GAP_MIN_SHORTFALL = 0.02
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

# Minimum similarity score an item must reach to enter a Plex collection
# (config/tuning.yml movies:/tv: `min_similarity`). The library
# recommendation path previously had no quality gate at all, so a
# collection was always padded to limit_results no matter how weak the
# remaining candidates were - a user who has watched most of their
# library would get items scoring ~12% presented as recommendations.
# (The external-recommendation path has always had its own equivalent:
# external_recommendations.min_relevance_score.) Defaults to 0.0 =
# disabled, so existing installs keep exactly today's behavior until
# they opt in.
DEFAULT_MIN_SIMILARITY = 0.0

# Calibrated recommendations - see utils/calibration.py for the method
# (Steck, RecSys 2018). `lambda` trades relevance against how closely the
# collection's genre mix matches the user's actual watch history.
# Defaults to 0.0 = disabled (plain top-N by score, today's behavior).
DEFAULT_CALIBRATION_STRENGTH = 0.0
# Suggested starting value when a user turns calibration on. 0.5 weights
# relevance and calibration equally; Steck reports the relevance cost of
# calibrating stays small well past this point.
SUGGESTED_CALIBRATION_STRENGTH = 0.5
# Steck's smoothing constant for KL(target || list) - keeps the
# divergence finite when the list omits a genre the user watches.
CALIBRATION_SMOOTHING_ALPHA = 0.01
# Scale factor putting the KL term on the same footing as the similarity
# term in calibrate_recommendations()'s objective.
#
# Adding one item to an already-large list barely moves that list's genre
# distribution, so a marginal KL change is ~1e-3 while a marginal
# similarity change is ~1e-1. Unscaled, similarity dominates until
# lambda is within ~0.01 of 1.0 - Steck's own experiments use 0.99 for
# exactly this reason. That makes a terrible configuration knob: 0.5
# would be indistinguishable from off. Scaling the divergence by this
# factor maps the useful range back onto a plain 0.0-1.0 dial, where
# 0.25/0.5/0.75 are all meaningfully different. Derived from the
# unscaled behavior: strength 0.5 here reproduces roughly lambda=0.99.
CALIBRATION_DIVERGENCE_SCALE = 100.0
# Relative weight of each calibration dimension (utils/calibration.py's
# calibrate_multi). Genre says what a title is ABOUT; the certificate says
# who it is FOR, and on real libraries only the certificate is reliable
# about the latter - measured on the reference library, `family` is
# attached to Frequency and Skyscraper while the live-action R.I.P.D.
# carries `animation`, and genuine children's films often carry no kid
# genre at all. By certificate the same split is clean: G 90% / PG 51%
# kid-tagged against 1% for both PG-13 and R.
#
# Weighted equally: genre still drives what kind of story surfaces, the
# certificate stops a collection drifting to content aimed at a different
# audience than the profile watches.
CALIBRATION_GENRE_WEIGHT = 1.0
CALIBRATION_CERTIFICATE_WEIGHT = 1.0

# Corpus-level IDF (utils/corpus_idf.py) - the missing half of the
# "TF-IDF" in utils/scoring.py, which only ever measured rarity within a
# user's own profile and never across the library. Without it,
# ubiquitous structural metadata ("sequel" - 28% of the reference
# library, "aftercreditsstinger" - 14%) scores like genuine taste signal.
#
# Smallest corpus worth deriving term distribution from. Below this,
# document frequency is too noisy to distinguish "ubiquitous" from
# "happens to appear twice", so no weighting is applied at all.
IDF_MIN_CORPUS_SIZE = 30
# Floor for an IDF multiplier. A term in literally every item carries no
# information, but zeroing it would silently erase a scoring dimension
# for items whose metadata is entirely common terms - degrade, never
# silently drop (CLAUDE.md).
IDF_MIN_WEIGHT = 0.05

# TF-IDF scoring penalties for rare/unseen content attributes
TFIDF_GENRE_PENALTY = 0.3  # Max 30% penalty per rare genre
TFIDF_KEYWORD_PENALTY = 0.15  # Max 15% penalty per rare keyword
UNSEEN_GENRE_PENALTY = 0.1  # Penalty for genres user has never watched
UNSEEN_KEYWORD_PENALTY = 0.02  # Penalty for keywords user has never seen

# Ceiling on per-item weight redistribution (utils/scoring.py's
# _apply_active_weight_redistribution). When an item carries no data at
# all for a dimension, that dimension's weight moves onto the ones that
# did score - which is right for a small dimension (a missing language
# field, weight 0.05) and badly wrong for a large one.
#
# Measured on a real library: a title with no tmdb_keywords, against a
# profile weighting keyword at 0.5 - half the whole budget - had its
# score multiplied by 2.67x and jumped from a true rank of #54 to #1,
# ahead of every title that actually matched on several dimensions.
# Everything else was being scaled by 1.00-1.07x.
#
# 1.25 keeps small gaps forgiven while refusing to let an absent
# dimension take over the score: past this point the remaining weight
# stays lost, because an item we know less about genuinely has less
# evidence of matching. Raising this re-opens that failure; lowering it
# toward 1.0 approaches "absent scores zero".
MAX_REDISTRIBUTION_MULTIPLIER = 1.25

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

# Ignored-recommendation negative signal (utils/ignored_recs.py). An item
# that sat in a user's collection this long without being watched counts
# as declined - the impression-level feedback every large recommender
# leans on, which curatarr recorded (label_dates) but never read.
# Three weeks is long enough to outlast "saving it for the weekend".
IGNORED_REC_MIN_DAYS_SHOWN = 21
# Total negative weight one ignored title contributes, split across the
# terms it carries. Small on purpose: one ignored title is weak evidence,
# twenty sharing a genre is not.
IGNORED_REC_PENALTY = 0.5
# Hard floor on how negative a term may go, as a fraction of the
# profile's largest positive count. Without it a long run of ignored
# recommendations could bury a genre permanently, leaving the profile
# unable to recover if the user's taste swung back.
IGNORED_REC_MAX_PROFILE_FRACTION = 0.25

# #291: whether a user with zero watch history still gets a
# Recommended collection built for them. Default True (create - see
# RECOMMEND_FOR_NO_HISTORY_DEFAULT below): a brand-new/zero-history user
# gets exactly the same collection they always have, because cold-start
# is a solved problem in recommenders - the standard answer is falling
# back to popular/well-rated unwatched items (see the sort-tiebreaker
# fix alongside this flag), not producing nothing. A user who opens
# Plex and sees no collection at all concludes the app is broken.
#
# Set to False (movies.recommend_for_no_history/tv.recommend_for_no_history
# in tuning.yml) to opt OUT of that and skip zero-history users instead -
# BaseRecommender.get_recommendations() then also removes any collection
# already sitting in Plex for that user (see
# BaseRecommender._remove_collection_for_no_history), so a user who
# later goes quiet again doesn't keep a stale collection around. That
# removal only ever targets a collection provably owned by this user via
# its PrivateCollection_<user> label (utils.plex.remove_owned_collection)
# - never inferred from title/emoji/name pattern - and only ever fires
# on this explicit opt-out, never on the default path.
#
# Documented in config/tuning.example.yml's
# movies.recommend_for_no_history/tv.recommend_for_no_history -
# tests/test_config.py's guardrail class enforces the two stay identical
# (#261 precedent: a documented example silently drifting from the real
# code default).
RECOMMEND_FOR_NO_HISTORY_DEFAULT = True

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

    Loads tuning.yml, trakt.yml, radarr.yml, sonarr.yml, mdblist.yml,
    simkl.yml if they exist.

    Precedence: for any top-level key a module file defines, that module
    file wins. Dict-valued keys are deep-merged (see `_deep_merge_dicts`),
    so e.g. tuning.yml's `users.preferences` does not silently wipe
    config.yml's `users.list` just because both files happen to define a
    top-level `users:` key - only the sub-keys tuning.yml actually
    specifies are overridden. Non-dict values (including lists) are
    replaced outright, never merged.
    """
    loaded_modules = []

    # Tuning modules merge their sections into root
    tuning_path = os.path.join(config_dir, "tuning.yml")
    if os.path.exists(tuning_path):
        try:
            with open(tuning_path, "r", encoding="utf-8") as f:
                tuning = yaml.safe_load(f)
                if tuning:
                    config = _deep_merge_dicts(config, tuning)
                    log_info("Loaded tuning.yml")
                    loaded_modules.append("tuning.yml")
        except Exception as e:
            log_warning(f"Could not load tuning.yml: {e}")

    # Feature modules go under their key, but still deep-merge in case
    # config.yml already carries a same-named section (e.g. pre-migration
    # leftovers) - same precedence rule as above applies within that key.
    # mdblist/simkl added here alongside trakt/radarr/sonarr (previously
    # missing, which meant a user's mdblist.yml/simkl.yml was silently
    # never read at all).
    for module in ["trakt", "radarr", "sonarr", "mdblist", "simkl"]:
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
                        loaded_modules.append(f"{module}.yml")
            except Exception as e:
                log_warning(f"Could not load {module}.yml: {e}")

    # Make it visible at load time which optional module files were
    # actually found and merged, rather than only ever seeing per-file
    # lines scroll by (or, for a file that doesn't exist, no signal at
    # all) - one summary line an operator can grep for.
    if loaded_modules:
        log_info(f"Module configs merged: {', '.join(loaded_modules)}")
    else:
        log_info("No optional module config files found (config.yml only)")

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


# #289: single source of truth for every secret's environment-variable
# override - (ENV_VAR, config-section, config-key). load_config() applies
# these (env always wins over whatever's on disk); get_env_override()
# below lets a caller (web/config_io.py's secret-status display, so the
# web UI shows "configured" - not a stale "not set" - for a value that's
# only ever set via the environment) ask the identical question without
# duplicating (and risking drifting from - #261's whole class of bug)
# this list. This is a convenience for operators using Docker
# secrets/an orchestrator's own secrets management, not a replacement
# for one - see config/tuning.example.yml and docs/DOCKER.md for the
# full list with setup instructions.
ENV_VAR_OVERRIDES = [
    ("PLEX_URL", "plex", "url"),
    ("PLEX_TOKEN", "plex", "token"),
    ("TMDB_API_KEY", "tmdb", "api_key"),
    ("TAUTULLI_API_KEY", "tautulli", "api_key"),
    ("SONARR_API_KEY", "sonarr", "api_key"),
    ("RADARR_API_KEY", "radarr", "api_key"),
    ("TRAKT_CLIENT_SECRET", "trakt", "client_secret"),
    ("TRAKT_ACCESS_TOKEN", "trakt", "access_token"),
    ("TRAKT_REFRESH_TOKEN", "trakt", "refresh_token"),
    ("SIMKL_CLIENT_ID", "simkl", "client_id"),
    ("SIMKL_ACCESS_TOKEN", "simkl", "access_token"),
    ("MDBLIST_API_KEY", "mdblist", "api_key"),
]


def get_env_override(section: str, key: str) -> Optional[str]:
    """The environment-variable value that would override config[section]
    [key] at load_config() time, or None if either no such override is
    registered in ENV_VAR_OVERRIDES or the env var isn't actually set.

    Exists so a caller other than load_config() itself (currently: web/
    config_io.py's secret-status display) can ask "is this value coming
    from the environment" without hand-rolling a second lookup that
    could silently drift out of sync with the real override list.
    """
    for env_var, env_section, env_key in ENV_VAR_OVERRIDES:
        if env_section == section and env_key == key:
            return os.environ.get(env_var) or None
    return None


# =============================================================================
# #316: loud detection of config keys nothing reads.
#
# The bug class this exists to kill: a key that is spelled plausibly, sits
# at a plausible depth, parses as valid YAML, and is then silently never
# read - so the setting is simply inert and the operator has no signal at
# all. That is exactly how `movies:`/`tv:` `quality_filters` and
# `randomize_recommendations` stayed non-functional through several
# releases (see CHANGELOG 2.10.23 and this module's
# resolve_media_type_overrides docstring). The resolution paths themselves
# were consolidated and fixed then; this is the other half of #316 - the
# standing warning so the next such key can't go quiet.
#
# Deliberately warn-only, never fatal: an unknown key is far more likely
# to be a typo or a leftover from an older release than something worth
# refusing to start over, and failing closed here would turn a cosmetic
# config wart into an outage on an unattended scheduled run.
# =============================================================================

# Every top-level section any shipped code path actually reads. Sourced
# from config/*.example.yml plus a sweep of real root-level reads in the
# codebase - see TestKnownRootConfigKeysCoverThePublishedExamples, which
# fails if an example file grows a section that isn't listed here.
#
# `weights` and `quality_filters` are the legacy pre-2.10.23 root-level
# spellings, still honored as a fallback tier by resolve_media_type_over
# rides() and BaseRecommender.get_recommendations() respectively - they
# are read, so they must not warn.
KNOWN_ROOT_CONFIG_KEYS = frozenset(
    {
        # config.yml
        "plex",
        "tmdb",
        "users",
        "plex_users",
        "general",
        "logging",
        "schedule",
        "huntarr",
        "tautulli",
        "libraries",
        "cache_dir",
        # Read by recommenders/external.py as the global service list
        # that users.preferences.<user>.streaming_services unions onto -
        # see get_streaming_services_for_user().
        "streaming_services",
        # Nothing reads `platform` today, but migrate_config.CORE_SECTIONS
        # deliberately preserves it across a migration, so it is a
        # sanctioned section rather than a stray key. Listed here on
        # purpose: warning about something the project itself carries
        # forward would be noise, and noise is what gets a warning like
        # this one tuned out.
        "platform",
        # tuning.yml (merged into root by _load_module_configs)
        "movies",
        "tv",
        "collections",
        "external_recommendations",
        "recency_decay",
        "rating_multipliers",
        "negative_signals",
        "profile_accuracy",
        # module files, each landing under its own key
        "trakt",
        "radarr",
        "sonarr",
        "mdblist",
        "simkl",
        # legacy root-level fallbacks (still read - see note above)
        "weights",
        "quality_filters",
    }
)

# Keys meaningful inside a `movies:`/`tv:` section of tuning.yml. Split
# because `show_director` is movies-only: TV has no director-equivalent
# display option, so resolve_media_type_overrides() never reads it for
# TV and setting it there is silently inert - precisely the shape of bug
# this whole section exists to surface.
KNOWN_MEDIA_SECTION_KEYS = frozenset(
    {
        "limit_results",
        "min_similarity",
        "calibration_strength",
        "randomize_recommendations",
        "normalize_counters",
        "show_summary",
        "show_genres",
        "show_cast",
        "show_language",
        "show_rating",
        "show_imdb_link",
        "weights",
        "quality_filters",
        "recommend_for_no_history",
    }
)
MOVIES_ONLY_MEDIA_SECTION_KEYS = frozenset({"show_director"})


def _suggest_similar_key(unknown: str, known) -> str:
    """Return a ' (did you mean ...)' fragment for a likely typo, or ''."""
    matches = difflib.get_close_matches(unknown.lower(), sorted(known), n=1, cutoff=0.72)
    return f" (did you mean '{matches[0]}'?)" if matches else ""


def warn_unknown_config_keys(config: dict) -> List[str]:
    """
    Log a warning for every config key nothing in the codebase reads.

    Checks two levels - the merged root, and the `movies:`/`tv:` sections
    of tuning.yml, which is where the #316 class of silent-ignore bug
    actually bit. Key lookups are case-insensitive because
    get_config_section() itself accepts an uppercase spelling (`TMDB:`,
    `MOVIES:`) for backwards compatibility, so warning on those would be
    a false positive on a config that genuinely works.

    Args:
        config: The merged root config dict, as built by load_config()

    Returns:
        The warning messages emitted, in order - returned (not just
        logged) so tests can assert on them without capturing log output.
    """
    warnings: List[str] = []

    def _warn(message: str) -> None:
        warnings.append(message)
        log_warning(message)

    for key in config:
        if not isinstance(key, str) or key.lower() in KNOWN_ROOT_CONFIG_KEYS:
            continue
        _warn(
            f"Unrecognized config key '{key}' at the top level of your "
            f"config - nothing reads it, so it has no effect"
            f"{_suggest_similar_key(key, KNOWN_ROOT_CONFIG_KEYS)}"
        )

    for section_name in ("movies", "tv"):
        section = config.get(section_name, config.get(section_name.upper()))
        if not isinstance(section, dict):
            continue
        allowed = KNOWN_MEDIA_SECTION_KEYS
        if section_name == "movies":
            allowed = allowed | MOVIES_ONLY_MEDIA_SECTION_KEYS

        for key in section:
            if not isinstance(key, str) or key.lower() in allowed:
                continue
            # Distinguish "this key doesn't exist" from "this key exists
            # but not here" - the second is much more confusing to hit,
            # because the operator has seen it work under the other
            # section and reasonably assumes it is symmetric.
            if key.lower() in MOVIES_ONLY_MEDIA_SECTION_KEYS:
                _warn(
                    f"Config key '{key}' under '{section_name}:' is "
                    f"movies-only and is ignored for TV - remove it, or "
                    f"move it under 'movies:'"
                )
            else:
                _warn(
                    f"Unrecognized config key '{key}' under "
                    f"'{section_name}:' - nothing reads it, so it has no "
                    f"effect{_suggest_similar_key(key, allowed)}"
                )

    return warnings


def load_config(config_path: str) -> dict:
    """
    Load YAML configuration with modular config file support.

    Loads config.yml and merges optional module files:
    - tuning.yml: Display/scoring options (merged into root)
    - trakt.yml: Trakt integration settings
    - radarr.yml: Radarr integration settings
    - sonarr.yml: Sonarr integration settings
    - mdblist.yml: MDBList integration settings
    - simkl.yml: Simkl integration settings

    Environment variables take precedence over all config values - see
    ENV_VAR_OVERRIDES above for the full, current list (this is a
    convenience for operators using Docker secrets/an orchestrator, not
    a secrets-manager replacement - see docs/DOCKER.md).

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

        # Override with environment variables (security best practice) -
        # never log the value itself, only which env var was used.
        for env_var, section, key in ENV_VAR_OVERRIDES:
            value = os.environ.get(env_var)
            if value:
                if section not in config:
                    config[section] = {}
                config[section][key] = value
                log_info(f"Using {env_var} from environment")

        # #316: last step, after every merge/override above, so this sees
        # exactly the dict the rest of the app will read - not an
        # intermediate state that would warn about a key tuning.yml was
        # about to supply, or miss one a module file just introduced.
        warn_unknown_config_keys(config)

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
            "ignored_recommendations": {
                "enabled": True,
                "min_days_shown": IGNORED_REC_MIN_DAYS_SHOWN,
                "penalty": IGNORED_REC_PENALTY,
            },
        }

    ns = config.get("negative_signals", {})

    # If master switch is off, return disabled config
    if not ns.get("enabled", True):
        return {
            "enabled": False,
            "bad_ratings": {"enabled": False},
            "dropped_shows": {"enabled": False},
            "ignored_recommendations": {"enabled": False},
        }

    bad_ratings = ns.get("bad_ratings", {})
    dropped_shows = ns.get("dropped_shows", {})
    ignored_recs = ns.get("ignored_recommendations", {})

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
        "ignored_recommendations": {
            "enabled": ignored_recs.get("enabled", True),
            "min_days_shown": ignored_recs.get("min_days_shown", IGNORED_REC_MIN_DAYS_SHOWN),
            "penalty": ignored_recs.get("penalty", IGNORED_REC_PENALTY),
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
    config["min_similarity"] = media_config.get("min_similarity", DEFAULT_MIN_SIMILARITY)
    config["calibration_strength"] = media_config.get("calibration_strength", DEFAULT_CALIBRATION_STRENGTH)
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
