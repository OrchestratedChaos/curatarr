# curatarr
# Copyright (C) 2026 OrchestratedChaos
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Base classes for Curatarr caches and recommenders.
Provides shared functionality for movies and TV shows.
"""

import os
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import logging
import re
import time
import traceback
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import plexapi.exceptions
import requests
from plexapi.myplex import MyPlexAccount

from utils import (
    CACHE_VERSION,
    CALIBRATION_CERTIFICATE_WEIGHT,
    CALIBRATION_GENRE_WEIGHT,
    CALIBRATION_MIN_PROFILE_SAMPLE,
    CANDIDATE_BUFFER_MULTIPLIER,
    CYAN,
    DEFAULT_MOVIE_NAME_TEMPLATE,
    DEFAULT_NEGATIVE_THRESHOLD,
    DEFAULT_TV_NAME_TEMPLATE,
    GREEN,
    IGNORED_REC_MIN_DAYS_SHOWN,
    IGNORED_REC_PENALTY,
    RATING_MULTIPLIER_2_STAR,
    RATING_MULTIPLIER_3_STAR,
    RATING_MULTIPLIER_4_STAR,
    RATING_MULTIPLIER_5_STAR,
    RATING_MULTIPLIER_UNRATED,
    RATING_TIER_3_STAR,
    RATING_TIER_4_STAR,
    RATING_TIER_5_STAR,
    RECOMMEND_FOR_NO_HISTORY_DEFAULT,
    RED,
    RESET,
    TIER_DIVERSE_PERCENT,
    TIER_SAFE_PERCENT,
    TIER_WILDCARD_PERCENT,
    TMDB_RATE_LIMIT_DELAY,
    WEIGHT_SUM_TOLERANCE,
    YELLOW,
    CalibrationDimension,
    add_labels_to_items,
    apply_ignored_penalties,
    apply_user_label_restrictions,
    assess_pool_health,
    build_all_private_labels,
    build_certificate_distribution,
    build_corpus_idf,
    build_label_name,
    build_target_distribution,
    calculate_recency_multiplier,
    calculate_rewatch_multiplier,
    calibrate_multi,
    calibration_report,
    categorize_labeled_items,
    check_cache_version,
    cleanup_legacy_unnamed_collection,
    cleanup_old_collections,
    create_empty_counters,
    describe_least_informative,
    enhance_profile_with_trakt,
    extract_ids_from_guids,
    fetch_tmdb_with_retry,
    fetch_user_played_ids,
    find_ignored_recommendations,
    find_supply_gaps,
    format_health_report,
    get_configured_users,
    get_excluded_genres_for_user,
    get_full_language_name,
    get_libraries_for_media_type,
    get_library_imdb_ids_from_items,
    get_max_rating_for_user,
    get_negative_multiplier,
    get_negative_signals_config,
    get_project_root,
    get_tmdb_config,
    get_tmdb_id_for_item,
    get_tmdb_keywords,
    init_plex,
    is_rating_allowed,
    is_sufficiently_sampled,
    load_config,
    load_media_cache,
    log_error,
    log_warning,
    migrate_legacy_cache_dir,
    print_similarity_breakdown,
    process_counters_from_cache,
    remove_labels_from_items,
    remove_owned_collection,
    render_collection_name,
    resolve_media_type_overrides,
    save_media_cache,
    save_watched_cache,
    select_tiered_recommendations,
    show_progress,
    update_plex_collection,
    user_select_recommendations,
)

logger = logging.getLogger("curatarr")


class BaseCache(ABC):
    """
    Abstract base class for media caches (movies and TV shows).

    Provides common functionality for loading, saving, and updating caches.
    Subclasses must implement media-specific processing.
    """

    # Subclasses must define these as literal class attributes (e.g.
    # media_type = "movie") - bare annotations only here (no runtime
    # default) so mypy treats every subclass's self.media_type/media_key/
    # cache_filename as a plain str (matching how they're actually used
    # downstream) rather than inferring Optional[str] from a `= None`
    # placeholder that no concrete subclass ever leaves unset.
    media_type: str  # 'movie' or 'tv'
    media_key: str  # 'movies' or 'shows'
    cache_filename: str  # e.g., 'all_movies_cache.json'

    def __init__(self, cache_dir: str, recommender=None):
        """
        Initialize the cache.

        Args:
            cache_dir: Directory path where cache files are stored
            recommender: Reference to parent recommender instance
        """
        # Set recommender before building the cache path so the
        # per-library prefix (#157 Phase 3) can be resolved. Single-library
        # installs get prefix "" -- exact legacy filename, unchanged.
        self.recommender = recommender
        prefix = recommender._cache_library_prefix() if recommender else ""
        self.cache_path = os.path.join(cache_dir, f"{prefix}{self.cache_filename}")
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict:
        """Load cache from file."""
        return load_media_cache(self.cache_path, self.media_key)

    def _save_cache(self):
        """Save cache to file."""
        self.cache["cache_version"] = CACHE_VERSION
        save_media_cache(self.cache_path, self.cache, self.media_key)

    def update_cache(
        self, plex, library_title: str, tmdb_api_key: Optional[str] = None, all_items: Optional[List] = None
    ) -> bool:
        """
        Update cache with current library contents and TMDB metadata.

        Args:
            plex: PlexServer instance
            library_title: Name of the library section
            tmdb_api_key: Optional TMDB API key for fetching additional metadata
            all_items: Optional pre-fetched section.all() result. When
                provided, this skips the library fetch entirely instead of
                re-querying Plex - callers that already hold a full-library
                snapshot (see BaseRecommender._get_all_library_items(), #233
                audit remediation batch D / PR1(a)) should pass it here so a
                single run only fetches the library once, not once per
                consumer. Falls back to fetching it here (unchanged
                behavior) when omitted.

        Returns:
            bool: True if cache was updated, False if already up to date
        """
        if all_items is None:
            section = plex.library.section(library_title)
            all_items = section.all()
        current_count = len(all_items)

        if current_count == self.cache["library_count"]:
            print(f"{GREEN}{self.media_key.title()} cache is up to date{RESET}")
            # Still check for missing collection data (backfill for existing caches)
            if self.media_type == "movie" and tmdb_api_key:
                if self._backfill_collection_data(tmdb_api_key):
                    self._save_cache()
            return False

        print(f"\n{YELLOW}Analyzing library {self.media_key}...{RESET}")

        # Remove items no longer in library
        current_ids = set(str(item.ratingKey) for item in all_items)
        removed = set(self.cache[self.media_key].keys()) - current_ids

        if removed:
            print(f"{YELLOW}Removing {len(removed)} {self.media_key} from cache that are no longer in library{RESET}")
            for item_id in removed:
                del self.cache[self.media_key][item_id]

        # Find new items to process
        existing_ids = set(self.cache[self.media_key].keys())
        new_items = [item for item in all_items if str(item.ratingKey) not in existing_ids]

        if new_items:
            print(f"Found {len(new_items)} new {self.media_key} to analyze")

            for i, item in enumerate(new_items, 1):
                pct_done = int((i / len(new_items)) * 100)
                msg = f"\r{CYAN}Processing {self.media_type} {i}/{len(new_items)} ({pct_done}%){RESET}"
                sys.stdout.write(msg)
                sys.stdout.flush()

                item_id = str(item.ratingKey)
                try:
                    item.reload()

                    # Rate limiting for TMDB
                    if i > 1 and tmdb_api_key:
                        time.sleep(TMDB_RATE_LIMIT_DELAY)

                    # Process the item (media-specific logic)
                    item_info = self._process_item(item, tmdb_api_key)

                    if item_info:
                        self.cache[self.media_key][item_id] = item_info

                except (plexapi.exceptions.PlexApiException, requests.RequestException, AttributeError, KeyError) as e:
                    log_warning(f"Error processing {self.media_type} {item.title}: {e}")
                    continue

        self.cache["library_count"] = current_count
        self.cache["last_updated"] = datetime.now().isoformat()

        # Backfill collection data for movies missing it
        if self.media_type == "movie" and tmdb_api_key:
            self._backfill_collection_data(tmdb_api_key)

        self._save_cache()
        print(f"\n{GREEN}{self.media_key.title()} cache updated{RESET}")
        return True

    def _backfill_collection_data(self, tmdb_api_key: str) -> bool:
        """
        Backfill collection data for cached movies that don't have it.

        This handles existing cached movies that were stored before
        collection tracking was added.

        Returns:
            True if any movies were updated, False otherwise
        """
        movies_needing_collection = [
            (item_id, info)
            for item_id, info in self.cache[self.media_key].items()
            if info.get("tmdb_id") and "collection_id" not in info
        ]

        if not movies_needing_collection:
            return False

        total = len(movies_needing_collection)
        print(f"\n{CYAN}Backfilling collection data for {total} movies (one-time migration)...{RESET}")

        updated = 0
        for i, (_item_id, info) in enumerate(movies_needing_collection, 1):
            pct = int((i / total) * 100)
            sys.stdout.write(f"\r{CYAN}Processing {i}/{total} ({pct}%) - Found {updated} collections{RESET}")
            sys.stdout.flush()

            try:
                time.sleep(TMDB_RATE_LIMIT_DELAY)
                detail_data = fetch_tmdb_with_retry(
                    f"https://api.themoviedb.org/3/movie/{info['tmdb_id']}", {"api_key": tmdb_api_key}
                )
                if detail_data:
                    collection = detail_data.get("belongs_to_collection")
                    if collection:
                        info["collection_id"] = collection.get("id")
                        info["collection_name"] = collection.get("name")
                        updated += 1
                    else:
                        info["collection_id"] = None
                        info["collection_name"] = None
                else:
                    # API failed (404, etc) - mark as processed to avoid infinite retries
                    info["collection_id"] = None
                    info["collection_name"] = None
            except (requests.RequestException, KeyError) as e:
                # Mark as processed even on exception
                logger.debug(f"Error fetching collection for TMDB {info.get('tmdb_id')}: {e}")
                info["collection_id"] = None
                info["collection_name"] = None

        print(f"\n{GREEN}Added collection data for {updated} movies{RESET}")
        return True

    @abstractmethod
    def _process_item(self, item, tmdb_api_key: Optional[str]) -> Optional[Dict]:
        """
        Process a single media item and return its info dict.

        Must be implemented by subclasses for media-specific processing.

        Args:
            item: Plex media item
            tmdb_api_key: Optional TMDB API key

        Returns:
            Dict with item metadata or None on error
        """
        pass

    def _get_language(self, item) -> str:
        """
        Get media item's primary audio language.

        Args:
            item: Plex media item

        Returns:
            Language name string or 'N/A'
        """
        try:
            # For TV shows, get first episode
            if self.media_type == "tv":
                episodes = item.episodes()
                if not episodes:
                    return "N/A"
                item = episodes[0]
                item.reload()

            if not item.media:
                return "N/A"

            for media in item.media:
                for part in media.parts:
                    audio_streams = part.audioStreams()
                    if audio_streams:
                        audio = audio_streams[0]
                        lang_code = getattr(audio, "languageTag", None) or getattr(audio, "language", None)
                        if lang_code:
                            return get_full_language_name(lang_code)
        except (plexapi.exceptions.PlexApiException, AttributeError, TypeError) as e:
            logger.debug(f"Error getting language for {getattr(item, 'title', 'unknown')}: {e}")
        return "N/A"

    def _get_tmdb_data(self, item, tmdb_api_key: str) -> Dict:
        """
        Get TMDB ID and keywords for an item.

        Args:
            item: Plex media item
            tmdb_api_key: TMDB API key

        Returns:
            Dict with 'tmdb_id', 'imdb_id', 'keywords', 'rating', 'vote_count'
        """
        result: Dict[str, Any] = {
            "tmdb_id": None,
            "imdb_id": None,
            "keywords": [],
            "rating": None,
            "vote_count": None,
            "collection_id": None,
            "collection_name": None,
            "production_company_ids": [],  # For TV franchise detection
        }

        # Extract IDs from GUIDs
        ids = extract_ids_from_guids(item)
        result["imdb_id"] = ids["imdb_id"]
        result["tmdb_id"] = ids["tmdb_id"]

        # Get TMDB ID if not found in GUIDs
        if not result["tmdb_id"] and tmdb_api_key:
            result["tmdb_id"] = get_tmdb_id_for_item(item, tmdb_api_key, self.media_type)

        # Fetch TMDB metadata
        if result["tmdb_id"] and tmdb_api_key:
            # Get keywords
            result["keywords"] = get_tmdb_keywords(tmdb_api_key, result["tmdb_id"], self.media_type)

            # Get rating/vote_count for both media types (used by
            # quality_filters); collection info is movie-only (sequel
            # bonus), production companies are TV-only (franchise bonus).
            if self.media_type == "movie":
                detail_data = fetch_tmdb_with_retry(
                    f"https://api.themoviedb.org/3/movie/{result['tmdb_id']}", {"api_key": tmdb_api_key}
                )
                if detail_data:
                    result["rating"] = detail_data.get("vote_average")
                    result["vote_count"] = detail_data.get("vote_count")
                    # Extract collection info (for sequel bonus)
                    collection = detail_data.get("belongs_to_collection")
                    if collection:
                        result["collection_id"] = collection.get("id")
                        result["collection_name"] = collection.get("name")

            elif self.media_type == "tv":
                detail_data = fetch_tmdb_with_retry(
                    f"https://api.themoviedb.org/3/tv/{result['tmdb_id']}", {"api_key": tmdb_api_key}
                )
                if detail_data:
                    result["rating"] = detail_data.get("vote_average")
                    result["vote_count"] = detail_data.get("vote_count")
                    production_companies = detail_data.get("production_companies", [])
                    result["production_company_ids"] = [pc["id"] for pc in production_companies]

        # Update recommender caches if available
        if self.recommender and result["tmdb_id"]:
            self.recommender.plex_tmdb_cache[str(item.ratingKey)] = result["tmdb_id"]
            if result["keywords"]:
                self.recommender.tmdb_keywords_cache[str(result["tmdb_id"])] = result["keywords"]

        return result


class BaseRecommender(ABC):
    """
    Abstract base class for media recommenders.

    Provides common functionality for loading config, connecting to Plex,
    managing caches, and generating recommendations.
    """

    # Subclasses must define these as literal class attributes - bare
    # annotations only (see BaseCache's identical comment above).
    media_type: str  # 'movie' or 'tv'
    media_key: str  # 'movies' or 'shows'
    library_config_key: str  # e.g., 'movie_library_title'
    default_library_name: str  # e.g., 'Movies'

    # Instance attributes set by subclasses' own __init__ (movie.py/tv.py) -
    # bare annotations only (no runtime default) so BaseRecommender's own
    # methods that reference self.watched_cache_path/self.profile_hash
    # type-check without changing subclass assignment behavior.
    watched_cache_path: str
    profile_hash: str

    def __init__(
        self,
        config_path: str,
        single_user: Optional[str] = None,
        library: Optional[Dict] = None,
        library_items_cache: Optional[Dict[str, List]] = None,
        label_restrictions_state: Optional[Dict[str, bool]] = None,
    ):
        """
        Initialize the recommender.

        Args:
            config_path: Path to the config.yml configuration file
            single_user: Optional username for single-user mode
            library: Optional normalized library dict (see utils.config.get_libraries)
                for the #157 Phase 3 per-library recommendation loop. When
                provided, the recommender operates against this library's
                Plex section. When None, falls back to the legacy
                library_config_key/default_library_name resolution.
            library_items_cache: Optional dict shared across every
                recommender instance processed against the same library in
                a single run (utils.cli.run_recommender_main creates one per
                run and threads it through movie.py/tv.py's
                process_recommendations -> here). Keyed by library_title;
                see _get_all_library_items(). When None (direct/test
                instantiation), a fresh per-instance dict is used instead -
                this still dedupes the up-to-6x-per-user Plex fetches within
                one instantiation (#233 audit remediation batch D / PR1(a)),
                it just doesn't share across users/instances.
            label_restrictions_state: Optional dict shared across EVERY
                recommender instance for the WHOLE run - every (user,
                library) pair, not just one library's (#360 - contrast
                with library_items_cache above). manage_plex_labels()
                uses this to apply the cross-user Plex exclude-filter
                restrictions exactly once per run instead of once per
                (library x user) pair - see its own comment there for why
                that's safe. When None (direct/test instantiation, or a
                caller that never threads this through), a fresh
                per-instance dict is used instead, so restrictions are
                still applied on every call exactly as before #360 - no
                cross-instance dedup, but never a behavior change for a
                caller that doesn't opt in.
        """
        self.single_user = single_user
        self._label_restrictions_state: Dict[str, bool] = (
            label_restrictions_state if label_restrictions_state is not None else {}
        )
        # load_config() (modular merge + auto-migration + env-var
        # overrides) followed by resolve_media_type_overrides() (the
        # movies:/tv: per-media-type resolution - see its docstring in
        # utils/config.py for exactly which keys this adds/overwrites and
        # why) - the one resolution path every recommender now shares
        # with utils/cli.py.
        self.config = resolve_media_type_overrides(load_config(config_path), self.media_type)
        self.library = library
        self._library_items_cache: Dict[str, List] = library_items_cache if library_items_cache is not None else {}

        if self.library:
            self.library_id = self.library["id"]
            self.library_title = self.library["section"]
        else:
            self.library_id = None
            self.library_title = self.config["plex"].get(self.library_config_key, self.default_library_name)

        # Sibling libraries of this media type - drives cache filename /
        # collection naming back-compat: a single library (synthesized or
        # explicitly configured) keeps today's exact names; only genuine
        # multi-library installs get library-qualified names (#157 Phase 3).
        self._sibling_libraries = get_libraries_for_media_type(self.config, self.media_type)
        self._is_multi_library = bool(self.library) and len(self._sibling_libraries) > 1

        # Initialize counters and caches
        self.cached_watched_count = 0
        self.watched_data_counters: Dict[str, Any] = {}
        self.plex_tmdb_cache: Dict[str, Any] = {}
        self.tmdb_keywords_cache: Dict[str, Any] = {}
        self.label_dates: Dict[str, Any] = {}
        # Rating keys THIS user has played, read through their own Plex
        # connection (utils/plex.fetch_user_played_ids). Populated lazily
        # by _load_user_played_ids() because it needs the library section
        # name, and deliberately not persisted - it is server state, not
        # profile state. Empty is the safe default: an item wrongly
        # thought unwatched is a redundant recommendation, one wrongly
        # thought watched vanishes from consideration entirely.
        self.user_played_ids: Set[int] = set()
        # #356: cache_dir computed here with the exact same formula
        # self.cache_dir uses further below (not yet set at this point in
        # __init__) - passed through so a not-yet-confirmed rename
        # recorded this same run by utils.user_migration.
        # migrate_renamed_plex_users can be tolerated instead of raising.
        # A missing/not-yet-created directory is fine - get_configured_users
        # only ever reads from it.
        self.users = get_configured_users(
            self.config, cache_dir=os.path.join(get_project_root(), self.config.get("cache_dir", "cache"))
        )

        # Set for tracking watched item IDs
        self.watched_ids: Set[int] = set()

        print("Initializing recommendation system...")
        print("Connecting to Plex server...")
        self.plex = init_plex(self.config)
        print("Connected to Plex successfully!\n")

        # Load general config
        general_config = self.config.get("general", {})
        self.debug = general_config.get("debug", False)

        # Media-specific section (movies:/tv: in tuning.yml) - the
        # documented, web-UI-writable location for display options,
        # randomize_recommendations, normalize_counters, quality_filters,
        # and weights (see config/tuning.example.yml). Kept as a direct
        # reference to the raw section (not the resolved keys above) for
        # get_recommendations()'s own quality_filters read further down,
        # which resolves that key fresh at call time rather than once
        # here - see its comment there for why.
        media_section = "movies" if self.media_type == "movie" else "tv"
        self.media_config = self.config.get(media_section, self.config.get(media_section.upper(), {})) or {}

        print(f"{YELLOW}Checking Cache...{RESET}")
        tmdb_config = get_tmdb_config(self.config)
        self.use_tmdb_keywords = tmdb_config["use_keywords"]
        self.tmdb_api_key = tmdb_config["api_key"]

        # Setup cache directory - routed through get_project_root() (same
        # resolver as utils/cli.py and recommenders/external.py) rather than
        # a __file__-relative path, so it honors CURATARR_CONFIG_DIR/the
        # frozen-binary per-user data dir instead of always landing next to
        # the installed code (which Docker doesn't mount and a PyInstaller
        # onefile binary deletes on exit). config-level 'cache_dir' override
        # (relative subdir name or absolute path) is applied the same way
        # external.py's cache_dir setup does - os.path.join() already
        # discards project_root when the override is absolute.
        legacy_cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
        self.cache_dir = os.path.join(get_project_root(), self.config.get("cache_dir", "cache"))
        os.makedirs(self.cache_dir, exist_ok=True)
        # #291 (data-loss fix): migrate_legacy_cache_dir() now COPIES,
        # never moves - see its own docstring for the real incident
        # that changed this. Runs unconditionally on every construction
        # of every recommender (a constructor mutating the filesystem
        # as a side effect is itself a surprising design, flagged but
        # not restructured out of __init__ in that same fix - a
        # one-time explicit startup step would be a cleaner home for
        # it, but that's a bigger change than a copy-not-move fix
        # should also carry) - safe to call every time regardless,
        # since its own per-file "already exists at destination" check
        # makes every call after the first a no-op.
        migrate_legacy_cache_dir(legacy_cache_dir, self.cache_dir)

        # Load display options
        self.confirm_operations = general_config.get("confirm_operations", False)

        # Final recommendation/collection count (config/tuning.yml movies:/tv:
        # limit_results - documented but never read prior to this fix, see
        # CHANGELOG). This is now the single source of truth for how many
        # items end up in the recommendation collection - manage_plex_labels()
        # below reads self.limit_results directly as its target_count.
        # Resolved once, up front, by resolve_media_type_overrides() (see
        # its docstring in utils/config.py) - this class no longer hand-
        # resolves movies:/tv: overrides itself.
        self.limit_results = self.config["limit_results"]

        # Internal candidate-scoring buffer: generate CANDIDATE_BUFFER_MULTIPLIER x
        # limit_results scoring candidates per run, so the best-scoring items can
        # compete against whatever a prior run already labeled instead of being
        # capped at exactly the final collection size (see manage_plex_labels()).
        # general.limit_plex_results remains an advanced override of this buffer
        # only - it no longer drives the final collection size (limit_results does
        # that now) - and an explicit override here is honored exactly as
        # configured, never clamped up to limit_results: this preserves the
        # existing documented/tested behavior for installs that already set it.
        default_limit = self.limit_results * CANDIDATE_BUFFER_MULTIPLIER
        self.limit_plex_results = general_config.get("limit_plex_results", default_limit)
        # Quality gate + calibration (config/tuning.yml movies:/tv:), both
        # resolved by resolve_media_type_overrides() and both defaulting to
        # 0.0 = off, so an install that sets neither is bit-for-bit
        # unchanged. See utils/calibration.py for what calibration does.
        self.min_similarity = self.config["min_similarity"]
        self.calibration_strength = self.config["calibration_strength"]

        self.randomize_recommendations = self.config["randomize_recommendations"]
        self.normalize_counters = self.config["normalize_counters"]
        self.show_summary = self.config["show_summary"]
        self.show_genres = self.config["show_genres"]
        self.show_cast = self.config["show_cast"]
        self.show_language = self.config["show_language"]
        self.show_rating = self.config["show_rating"]
        self.show_imdb_link = self.config["show_imdb_link"]

        # Load excluded genres
        exclude_genre_str = general_config.get("exclude_genre", "")
        self.exclude_genres = (
            [g.strip().lower() for g in exclude_genre_str.split(",") if g.strip()] if exclude_genre_str else []
        )

        # Load user preferences
        self.user_preferences = self.config.get("users", {}).get("preferences", {}) or {}

        # Load weights - movies:/tv: weights: (documented location, see
        # config/tuning.example.yml) take priority over the legacy
        # root-level `weights` key some back-compat installs/tests still
        # use; that fallback chain is resolved once by
        # resolve_media_type_overrides() (self.config["weights"]) -
        # _load_weights() below only applies the media-type-specific
        # per-field defaults (director vs studio, etc.), unchanged.
        self.weights = self._load_weights(self.config["weights"])

        # Validate weights sum
        total_weight = sum(self.weights.values())
        if not abs(total_weight - 1.0) < WEIGHT_SUM_TOLERANCE:
            log_warning(f"Warning: Weights sum to {total_weight}, expected 1.0.")

    @abstractmethod
    def _load_weights(self, weights_config: Dict) -> Dict:
        """
        Load scoring weights from config.

        Must be implemented by subclasses for media-specific weights.

        Args:
            weights_config: Weights configuration dict

        Returns:
            Dict of weight names to values
        """
        pass

    def _calculate_rating_multiplier(self, user_rating):
        """Calculate rating multiplier based on user's star rating (0-10 scale in Plex)

        With negative signals enabled, low ratings (0-3) return negative multipliers
        to penalize similar content instead of weakly preferring it.

        Rating scale (negative signals enabled):
        - 9-10 (5 stars): 1.0x weight - love it, strong preference
        - 7-8 (4 stars): 0.75x weight - like it, moderate preference
        - 5-6 (3 stars): 0.5x weight - neutral, weak preference
        - 4 (2 stars): 0.25x weight - dislike, very weak preference
        - 0-3 (1-1.5 stars): NEGATIVE weight - hate it, penalize similar content
        - None/0 (unrated): 0.6x weight - default, slightly lower than neutral
        """
        if not user_rating or user_rating == 0:
            return RATING_MULTIPLIER_UNRATED

        rating_int = int(round(user_rating))

        # Check if negative signals are enabled
        ns_config = self.config.get("negative_signals", {})
        bad_ratings_config = ns_config.get("bad_ratings", {})
        ns_enabled = ns_config.get("enabled", True) and bad_ratings_config.get("enabled", True)
        threshold = bad_ratings_config.get("threshold", DEFAULT_NEGATIVE_THRESHOLD)

        # Return negative multiplier for low ratings if enabled
        if ns_enabled and rating_int <= threshold:
            return get_negative_multiplier(rating_int)

        # Positive multipliers for higher ratings
        if user_rating >= RATING_TIER_5_STAR:
            return RATING_MULTIPLIER_5_STAR
        elif user_rating >= RATING_TIER_4_STAR:
            return RATING_MULTIPLIER_4_STAR
        elif user_rating >= RATING_TIER_3_STAR:
            return RATING_MULTIPLIER_3_STAR
        else:
            return RATING_MULTIPLIER_2_STAR

    def _cache_library_prefix(self) -> str:
        """
        Prefix for per-library cache filenames (#157 Phase 3).

        Empty string for a single-library install (back-compat: existing
        watched-cache files keep their exact legacy names). Only genuine
        multi-library installs (>1 library of this media type) get a
        library-id-qualified prefix, so caches don't collide across libraries.

        Returns:
            "{library_id}_" when multi-library, else ""
        """
        if not self._is_multi_library:
            return ""
        return f"{self.library_id}_"

    def _library_suffix_for_label(self) -> str:
        """Plex label suffix - empty unless >1 library shares this media type."""
        if not self._is_multi_library:
            return ""
        return f"_{self.library_id}"

    def _library_suffix_for_collection_name(self) -> str:
        """Collection display-name suffix - empty unless >1 library shares this media type."""
        if not self._is_multi_library:
            return ""
        name = (self.library or {}).get("name") or self.library_title
        return f" ({name})"

    def _get_user_context(self) -> str:
        """
        Get a safe string representing the current user context for cache filenames.

        Returns:
            Sanitized user context string, prefixed with the library id when
            this install has more than one library of this media type.
        """
        if self.single_user:
            user_ctx = f"plex_{self.single_user}"
        elif self.users["plex_users"]:
            user_ctx = "plex_" + "_".join(self.users["plex_users"])
        else:
            user_ctx = "plex_" + "_".join(self.users["managed_users"])

        sanitized = re.sub(r"\W+", "", user_ctx)
        return f"{self._cache_library_prefix()}{sanitized}"

    def _refresh_watched_data(self):
        """Force refresh of watched data from Plex."""
        # {} (not None) - every consumer below only ever checks this
        # attribute's truthiness (hasattr(...) and self.watched_data_counters),
        # never `is None`, so an empty dict forces the same "no cached
        # data yet" bypass without widening this attribute's type to
        # Optional everywhere it's used.
        self.watched_data_counters = {}
        self.watched_ids = set()
        self.watched_data_counters = self._get_watched_data()
        self._save_watched_cache()

    def _get_managed_users_watched_data(self) -> Dict:
        """Get watched data from managed Plex users."""
        if not self.single_user and hasattr(self, "watched_data_counters") and self.watched_data_counters:
            logger.debug("Using cached watched data (not single user mode)")
            return self.watched_data_counters

        if hasattr(self, "watched_data_counters") and self.watched_data_counters:
            logger.debug("Using existing watched data counters")
            return self.watched_data_counters

        counters = create_empty_counters(self.media_type)

        account = MyPlexAccount(token=self.config["plex"]["token"])
        admin_user = self.users["admin_user"]

        if self.single_user:
            if self.single_user.lower() in ["admin", "administrator"]:
                users_to_process = [admin_user]
            else:
                users_to_process = [self.single_user]
        else:
            users_to_process = self.users["managed_users"] or [admin_user]

        negative_signal_count = 0
        ns_config = self.config.get("negative_signals", {})
        cap_penalty = ns_config.get("bad_ratings", {}).get("cap_penalty", 0.5)
        recency_config = self.config.get("recency_decay", {})

        for username in users_to_process:
            try:
                if username.lower() == admin_user.lower():
                    user_plex = self.plex
                else:
                    user = account.user(username)
                    user_plex = self.plex.switchUser(user)

                watched_items = user_plex.library.section(self.library_title).search(unwatched=False)

                print(f"\nScanning watched {self.media_key} for {username}")
                for i, item in enumerate(watched_items, 1):
                    show_progress(f"Processing {username}'s watched", i, len(watched_items))
                    self.watched_ids.add(int(item.ratingKey))

                    item_info = self._get_media_cache().cache[self.media_key].get(str(item.ratingKey))
                    if item_info:
                        # #273 PR2: this path previously called
                        # process_counters_from_cache() with no weight,
                        # rating, view_count, or viewed_at at all - every
                        # watched item counted exactly 1.0 regardless of
                        # how many times it was rewatched, how it was
                        # rated, or how recently it was watched (verified:
                        # every real managed-users profile's counter
                        # values were plain integers, no fractional
                        # weighting whatsoever). Now uses the exact same
                        # recency/rating/rewatch formula movie.py's and
                        # tv.py's own per-user builders already apply,
                        # sourced from this same switchUser()-scoped
                        # library item (already fixed to be per-user, not
                        # shared-admin, by #273 PR1's
                        # _get_all_library_items_for_user - this method
                        # already called search() through that same
                        # per-user connection before this PR, only the
                        # weighting itself was missing).
                        last_viewed_at = getattr(item, "lastViewedAt", None)
                        viewed_at = int(last_viewed_at.timestamp()) if last_viewed_at else None
                        recency_multiplier = (
                            calculate_recency_multiplier(viewed_at, recency_config) if viewed_at else 1.0
                        )
                        rating_multiplier = self._calculate_rating_multiplier(getattr(item, "userRating", None))
                        rewatch_multiplier = calculate_rewatch_multiplier(getattr(item, "viewCount", None) or 1)
                        weight = recency_multiplier * rating_multiplier * rewatch_multiplier

                        if weight < 0:
                            negative_signal_count += 1
                            logger.debug(
                                f"Negative signal: {item_info.get('title')} "
                                f"(rating: {getattr(item, 'userRating', None)}, weight: {weight:.2f})"
                            )

                        process_counters_from_cache(
                            item_info, counters, media_type=self.media_type, weight=weight, cap_penalty=cap_penalty
                        )

                        if tmdb_id := item_info.get("tmdb_id"):
                            counters["tmdb_ids"].add(tmdb_id)

            except (plexapi.exceptions.PlexApiException, KeyError, AttributeError) as e:
                log_error(f"Error processing user {username}: {e}")
                continue

        logger.debug(f"Collected {len(counters['tmdb_ids'])} unique TMDB IDs from managed users")
        if negative_signal_count > 0:
            logger.info(f"Processed {negative_signal_count} {self.media_key} as negative signals (low ratings)")

        return counters

    def _load_watched_cache(self) -> Dict:
        """Load watched cache from file. Returns the loaded cache dict."""
        watched_cache = {}
        # Salvage label_dates BEFORE the version check, which deletes the
        # file outright when CACHE_VERSION has moved.
        #
        # CACHE_VERSION exists to invalidate DERIVED data - cached scores,
        # metadata shape - and is bumped for scoring changes that have
        # nothing to do with user behavior. label_dates is not derived: it
        # records when each recommendation was first shown, and it is the
        # only clock the ignored-recommendation signal has (see
        # utils/ignored_recs.py, which needs weeks of it). Letting a
        # scoring change reset that clock meant the signal could never
        # accumulate on an actively-maintained install - observed
        # directly: two CACHE_VERSION bumps in one week left every label
        # across six users no older than 6 days.
        salvaged_label_dates = self._salvage_label_dates(self.watched_cache_path)
        cache_valid = check_cache_version(self.watched_cache_path, f"{self.media_type.upper()} watched cache")
        if cache_valid and os.path.exists(self.watched_cache_path):
            try:
                with open(self.watched_cache_path, "r", encoding="utf-8") as f:
                    watched_cache = json.load(f)
                    self.cached_watched_count = watched_cache.get("watched_count", 0)
                    self.watched_data_counters = watched_cache.get("watched_data_counters", {})
                    self.plex_tmdb_cache = {str(k): v for k, v in watched_cache.get("plex_tmdb_cache", {}).items()}
                    self.tmdb_keywords_cache = {
                        str(k): v for k, v in watched_cache.get("tmdb_keywords_cache", {}).items()
                    }
                    self.label_dates = watched_cache.get("label_dates", {})

                    # Load watched IDs (key differs by media type)
                    watched_ids_key = (
                        f"watched_{self.media_type}_ids" if self.media_type == "movie" else "watched_show_ids"
                    )
                    watched_ids_list = watched_cache.get(watched_ids_key, [])
                    if isinstance(watched_ids_list, list):
                        self.watched_ids = {int(id_) for id_ in watched_ids_list if str(id_).isdigit()}
                    else:
                        log_warning(f"Warning: Invalid {watched_ids_key} format in cache")
                        self.watched_ids = set()

                    if not self.watched_ids and self.cached_watched_count > 0:
                        log_error(
                            f"Warning: Cached watched count is {self.cached_watched_count} but no valid IDs loaded"
                        )
                        self._refresh_watched_data()

            except (json.JSONDecodeError, KeyError, IOError) as e:
                log_warning(f"Error loading watched cache: {e}")
                self._refresh_watched_data()

        # Carry the pre-rebuild clock forward. Entries already loaded from
        # a still-valid cache win - they are the same values - so this only
        # has an effect when the version check just deleted the file.
        if salvaged_label_dates:
            merged = dict(salvaged_label_dates)
            merged.update(getattr(self, "label_dates", {}) or {})
            if len(merged) > len(getattr(self, "label_dates", {}) or {}):
                logger.debug(
                    f"Preserved {len(merged) - len(getattr(self, 'label_dates', {}) or {})} label date(s) "
                    f"across a watched-cache rebuild"
                )
            self.label_dates = merged
        return watched_cache

    def _do_save_watched_cache(self):
        """Save watched cache to file using the utility."""
        save_watched_cache(
            cache_path=self.watched_cache_path,
            watched_data_counters=self.watched_data_counters,
            plex_tmdb_cache=self.plex_tmdb_cache,
            tmdb_keywords_cache=self.tmdb_keywords_cache,
            watched_ids=self.watched_ids,
            label_dates=getattr(self, "label_dates", {}),
            watched_count=len(self.watched_ids) if self.media_type == "movie" else self.cached_watched_count,
            media_type=self.media_type,
        )

    def get_recommendations(self) -> Dict[str, List[Dict]]:
        """Get recommendations based on watched content."""
        if self.cached_watched_count > 0 and not self.watched_ids:
            self.watched_data_counters = self._get_watched_data()
            self._save_watched_cache()

        # #291: a user with ZERO watch history gets no collection built
        # for them at all when movies.recommend_for_no_history/
        # tv.recommend_for_no_history is explicitly set to False -
        # self.watched_ids is populated by whichever watched-data
        # builder actually ran for this user (movie.py's/tv.py's own
        # per-user builder, or base.py's managed-users path - see #273),
        # so this check sits above all of them and applies regardless
        # of which one populated it.
        #
        # Default is True (create - see RECOMMEND_FOR_NO_HISTORY_DEFAULT's
        # own comment in utils/config.py for the cold-start reasoning):
        # a zero-history user gets EXACTLY today's behavior, no change,
        # unless this is explicitly turned off.
        #
        # When explicitly off, returning {"plex_recommendations": []}
        # here reaches the exact same "nothing to recommend" path
        # movie.py/tv.py already handle for a user with zero matching
        # candidates (movie.py never calls manage_plex_labels() at all
        # in that case; manage_plex_labels([]) itself also returns
        # immediately before touching Plex) - so on the default path an
        # existing collection is always left untouched. Only on this
        # explicit opt-out do we go further and actively remove a
        # collection already sitting in Plex for this user (see
        # _remove_collection_for_no_history) - never on the default path,
        # and never for a user who has ANY watch history at all.
        recommend_for_no_history = self.media_config.get("recommend_for_no_history", RECOMMEND_FOR_NO_HISTORY_DEFAULT)
        watched_count = len(self.watched_ids)
        if watched_count == 0 and not recommend_for_no_history:
            who = self.single_user or "the configured user(s)"
            media_section = "movies" if self.media_type == "movie" else "tv"
            log_warning(
                f"Skipping {self.media_key} recommendations for {who}: no watch history and "
                f"{media_section}.recommend_for_no_history is disabled in tuning.yml. Removing "
                "any existing collection this run may have previously created for them."
            )
            self._remove_collection_for_no_history(who)
            return {"plex_recommendations": []}

        # Get all items from cache
        media_cache = self._get_media_cache()
        all_items = media_cache.cache[self.media_key]

        print(f"\n{YELLOW}Processing recommendations...{RESET}")

        # Filter out watched items and excluded genres
        unwatched_items = []
        excluded_count = 0
        quality_filtered_count = 0

        # Get user-specific excluded genres
        excluded_genres = get_excluded_genres_for_user(self.exclude_genres, self.user_preferences, self.single_user)

        # Get quality filters from config - movies:/tv: quality_filters: is
        # the documented location (config/tuning.example.yml); fall back to
        # the legacy root-level key for back-compat installs/tests.
        quality_filters = self.media_config.get("quality_filters", self.config.get("quality_filters", {}))
        min_rating = quality_filters.get("min_rating", 0.0)
        min_vote_count = quality_filters.get("min_vote_count", 0)

        for item_id, item_info in all_items.items():
            if int(str(item_id)) in self.watched_ids:
                continue

            if any(g.lower() in excluded_genres for g in item_info.get("genres", [])):
                excluded_count += 1
                continue

            rating = item_info.get("rating") or 0.0
            vote_count = item_info.get("vote_count") or 0

            if rating < min_rating or vote_count < min_vote_count:
                quality_filtered_count += 1
                continue

            # Store ratingKey in item for later matching
            item_info["plex_rating_key"] = int(str(item_id))
            unwatched_items.append(item_info)

        if excluded_count > 0:
            print(f"Excluded {excluded_count} {self.media_key} based on genre filters")
        if quality_filtered_count > 0:
            log_warning(
                f"Filtered {quality_filtered_count} {self.media_key} below quality thresholds "
                f"(rating: {min_rating}+, votes: {min_vote_count}+)"
            )

        # Corpus IDF over the whole library, built once per run (see
        # utils/corpus_idf.py). Scoring falls back to its previous
        # behavior wherever these come back empty.
        self.genre_idf = build_corpus_idf(all_items.values(), "genres")
        self.keyword_idf = build_corpus_idf(all_items.values(), "tmdb_keywords")
        if self.keyword_idf and logger.isEnabledFor(logging.DEBUG):
            discounted = describe_least_informative(self.keyword_idf)
            summary = ", ".join(f"{t} ({w:.2f})" for t, w in discounted)
            logger.debug(f"Least informative keywords (discounted by corpus IDF): {summary}")

        self._load_user_played_ids()
        self._report_library_health(unwatched_items)

        if not unwatched_items:
            log_warning(f"No unwatched {self.media_key} found matching your criteria.")
            plex_recs = []
        else:
            print(f"Calculating similarity scores for {len(unwatched_items)} {self.media_key}...")

            scored_items = []
            cache_hits = 0
            scores_updated = False
            for i, item_info in enumerate(unwatched_items, 1):
                show_progress("Processing", i, len(unwatched_items))
                try:
                    cached_hash = item_info.get("profile_hash")
                    cached_score = item_info.get("cached_score")

                    if cached_hash == self.profile_hash and cached_score is not None:
                        similarity_score = cached_score
                        breakdown = item_info.get("score_breakdown", {})
                        cache_hits += 1
                    else:
                        similarity_score, breakdown = self._calculate_similarity_from_cache(item_info)
                        item_info["cached_score"] = similarity_score
                        item_info["profile_hash"] = self.profile_hash
                        item_info["score_breakdown"] = breakdown
                        scores_updated = True

                    item_info["similarity_score"] = similarity_score
                    scored_items.append(item_info)
                except (KeyError, TypeError, ValueError) as e:
                    log_warning(f"Error processing {item_info['title']}: {e}")
                    continue

            if scores_updated:
                media_cache._save_cache()
                logger.debug(f"Saved {len(unwatched_items) - cache_hits} new scores to cache")
            if cache_hits > 0:
                logger.debug(f"Used {cache_hits} cached scores")

            # #291 tiebreaker: with no watch history (or any tie in
            # general), every candidate can score identically (observed:
            # every component of calculate_similarity_score returns 0.0
            # against an empty profile) - Python's sort is stable, so an
            # unbroken tie previously fell through to media-cache
            # insertion order, which is alphabetical by title. Breaking
            # ties by (rating, vote_count) instead means a cold-start (or
            # any tied) collection surfaces well-regarded, well-known
            # unwatched titles first rather than an arbitrary alphabetical
            # slice - the standard cold-start fallback (recommend
            # popular/well-rated items), not produce noise. Applies to
            # every tie, not just all-zero-score cold start.
            scored_items.sort(
                key=lambda x: (x["similarity_score"], x.get("rating") or 0.0, x.get("vote_count") or 0),
                reverse=True,
            )

            # Quality gate. Applied here as well as in _update_labels_by_rank
            # so the printed recommendation list never advertises items the
            # collection would refuse - previously this list ran all the way
            # down the candidate buffer regardless of score.
            if self.min_similarity > 0:
                above_floor = [x for x in scored_items if x["similarity_score"] >= self.min_similarity]
                dropped = len(scored_items) - len(above_floor)
                if dropped:
                    logger.debug(
                        f"Score floor dropped {dropped} of {len(scored_items)} scored candidates "
                        f"below min_similarity {self.min_similarity:.2f}"
                    )
                scored_items = above_floor

            if self.randomize_recommendations:
                plex_recs = select_tiered_recommendations(
                    scored_items,
                    self.limit_plex_results,
                    TIER_SAFE_PERCENT,
                    TIER_DIVERSE_PERCENT,
                    TIER_WILDCARD_PERCENT,
                )
            else:
                plex_recs = scored_items[: self.limit_plex_results]

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("=== Similarity Score Breakdowns for Recommendations ===")
                for item in plex_recs:
                    self._print_similarity_breakdown(item, item["similarity_score"], item["score_breakdown"])

        print("\nRecommendation process completed!")
        return {"plex_recommendations": plex_recs}

    def _find_plex_items_for_recs(self, section, selected_items: List[Dict]) -> Tuple[List, List[str]]:
        """Find Plex items matching recommendations."""
        items_found = []
        skipped = []
        total = len(selected_items)
        print(f"Finding {total} recommendations in Plex library...")

        for i, rec in enumerate(selected_items, 1):
            if i % 20 == 0 or i == total:
                print(f"\r  Locating items: {i}/{total}...", end="", flush=True)

            plex_item = None

            # Try direct fetch by ratingKey first (reliable)
            rating_key = rec.get("plex_rating_key")
            if rating_key:
                try:
                    plex_item = self.plex.fetchItem(int(rating_key))
                except Exception:
                    pass  # Fall back to search

            # Fallback to fuzzy search
            if not plex_item:
                plex_item = self._find_plex_item(section, rec)

            if plex_item:
                plex_item.reload()
                items_found.append(plex_item)
            else:
                skipped.append(f"{rec['title']} ({rec.get('year', 'N/A')})")

        print()  # newline after progress
        return items_found, skipped

    def _remove_outdated_labels(self, section, label_name: str, stale_days: int) -> List:
        """Remove labels from watched/stale/excluded items, return fresh items."""
        currently_labeled = section.search(label=label_name)
        print(f"Found {len(currently_labeled)} currently labeled {self.media_key}")

        excluded_genres = get_excluded_genres_for_user(self.exclude_genres, self.user_preferences, self.single_user)
        # Union so categorize_labeled_items() no longer has to read
        # isPlayed off admin-connection items - same defect as in
        # _build_scored_candidates, and it was silently evicting other
        # users' still-unwatched recommendations as "watched".
        watched_for_categorize = self.watched_ids | self.user_played_ids
        categories = categorize_labeled_items(
            currently_labeled, watched_for_categorize, excluded_genres, label_name, self.label_dates, stale_days
        )

        print(f"{GREEN}Keeping {len(categories['fresh'])} unwatched recommendations{RESET}")
        print(f"{YELLOW}Removing {len(categories['watched'])} watched {self.media_key} from recommendations{RESET}")
        print(f"{YELLOW}Removing {len(categories['excluded'])} {self.media_key} with excluded genres{RESET}")

        remove_labels_from_items(categories["watched"], label_name, self.label_dates, "watched")
        remove_labels_from_items(categories["excluded"], label_name, self.label_dates, "excluded genre")

        return categories["fresh"]

    def _build_scored_candidates(self, unwatched_labeled: List, selected_items: List[Dict], items_found: List) -> Dict:
        """Build dict of item_id -> (plex_item, score) for all candidates."""
        all_candidates = {}
        media_cache = self._get_media_cache()

        total_labeled = len(unwatched_labeled)
        if total_labeled > 0:
            print(f"  Scoring {total_labeled} existing labeled items...", end="", flush=True)

        for _i, item in enumerate(unwatched_labeled, 1):
            item_id = int(item.ratingKey)
            item_info = media_cache.cache[self.media_key].get(str(item_id))
            if item_info:
                try:
                    score, _ = self._calculate_similarity_from_cache(item_info)
                    all_candidates[item_id] = (item, score)
                except Exception as e:
                    logger.debug(f"Scoring failed for item {item_id}: {e}")
                    all_candidates[item_id] = (item, 0.0)
            else:
                # Item labeled but not in cache - still include as candidate with 0 score
                logger.debug(f"Item {item_id} ({item.title}) not in cache, adding with score 0")
                all_candidates[item_id] = (item, 0.0)

        if total_labeled > 0:
            print(" done")

        # Build lookup by ratingKey for fast matching
        items_found_by_key = {int(m.ratingKey): m for m in items_found}

        for rec in selected_items:
            # Match by ratingKey (reliable) instead of title+year (can mismatch with fuzzy search)
            rec_key = rec.get("plex_rating_key")
            plex_item = items_found_by_key.get(rec_key) if rec_key else None

            # Fallback to title+year match for backwards compatibility
            if not plex_item:
                plex_item = next(
                    (m for m in items_found if m.title == rec["title"] and m.year == rec.get("year")), None
                )

            if plex_item:
                item_id = int(plex_item.ratingKey)
                # NOT getattr(plex_item, "isPlayed"): plex_item came from
                # the ADMIN connection, where isPlayed is the admin's
                # watched state for every user in the loop. Measured on a
                # real server, that dropped 45% of a Home user's candidate
                # pool - everything the admin had seen - leaving a
                # disproportionately children's remainder. user_played_ids
                # is that user's own state (utils/plex.fetch_user_played_ids).
                is_watched = item_id in self.watched_ids or item_id in self.user_played_ids
                if not is_watched:
                    score = rec.get("similarity_score", 0.0)
                    if item_id not in all_candidates or score > all_candidates[item_id][1]:
                        all_candidates[item_id] = (plex_item, score)

        return all_candidates

    def _filter_candidates_by_rating(self, all_candidates: Dict, max_rating: Optional[str]) -> Dict:
        """Filter candidates by content rating.

        Args:
            all_candidates: Dict of item_id -> (plex_item, score)
            max_rating: Maximum allowed content rating (e.g., 'PG-13', 'TV-14') or None for no filtering

        Returns:
            Filtered dict of item_id -> (plex_item, score)
        """
        if not max_rating:
            return all_candidates

        filtered = {}
        filtered_count = 0

        for item_id, (plex_item, score) in all_candidates.items():
            content_rating = getattr(plex_item, "contentRating", None)
            if is_rating_allowed(content_rating, max_rating, self.media_type):
                filtered[item_id] = (plex_item, score)
            else:
                filtered_count += 1
                logger.debug(f"Filtered {plex_item.title} ({content_rating}) - exceeds max rating {max_rating}")

        if filtered_count > 0:
            print(f"{YELLOW}Filtered {filtered_count} {self.media_key} exceeding max rating {max_rating}{RESET}")

        return filtered

    def _load_user_played_ids(self) -> None:
        """
        Read this user's own watched state from Plex (once per run).

        Skipped when no single_user is set - a combined/multi-user run has
        no single identity to switch to, and the union semantics there
        already come from the merged watch history.
        """
        if not self.single_user or self.user_played_ids:
            return
        try:
            self.user_played_ids = fetch_user_played_ids(self.plex, self.config, self.single_user, self.library_title)
            if self.user_played_ids:
                logger.debug(f"{self.single_user}: {len(self.user_played_ids)} items played per their own Plex view")
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            logger.debug(f"Could not load per-user played ids: {e}")

    @staticmethod
    def _salvage_label_dates(cache_path: str) -> Dict[str, Any]:
        """
        Read label_dates out of a watched cache without honoring its
        version.

        Deliberately version-blind: this is the one key in that file that
        must outlive a CACHE_VERSION bump, because it is an observation
        log rather than derived data. Returns {} for any unreadable or
        malformed file - a missing clock is recoverable, a crashed run is
        not.
        """
        try:
            with open(cache_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        dates = data.get("label_dates") if isinstance(data, dict) else None
        if not isinstance(dates, dict):
            return {}
        return {k: v for k, v in dates.items() if isinstance(k, str) and isinstance(v, str)}

    def _collection_label_name(self) -> str:
        """
        This user's collection label - the same value manage_plex_labels()
        builds, factored out so the ignored-recommendation feedback can
        key into label_dates before any collection work runs.
        """
        base_label = self.config.get("collections", {}).get("label_name", "Recommended")
        base_label = f"{base_label}{self._library_suffix_for_label()}"
        # Same derivation manage_plex_labels() uses - self.users is the
        # dict from get_configured_users(); build_label_name() wants the
        # flat username LIST off it, not the dict. Getting this wrong
        # would silently produce a label that matches nothing in
        # label_dates, and the feedback would quietly never fire.
        users = self.users["plex_users"] or self.users["managed_users"]
        append_usernames = self.config.get("collections", {}).get("append_usernames", True)
        return build_label_name(base_label, users, self.single_user, append_usernames)

    def _apply_ignored_recommendation_feedback(self) -> None:
        """
        Fold declined recommendations into the profile as negative signal
        (utils/ignored_recs.py).

        Must run BEFORE compute_profile_hash(): the penalties change the
        profile, and the hash is what invalidates cached item scores.
        Applying them after would leave every score cached against the
        pre-penalty profile and the feedback would have no effect until
        something else happened to change the profile.
        """
        signals = get_negative_signals_config(self.config)
        if not signals.get("enabled", True):
            return
        ignored_config = signals.get("ignored_recommendations", {})
        if not ignored_config.get("enabled", True):
            return

        label_dates = getattr(self, "label_dates", None)
        if not label_dates:
            return

        try:
            ignored = find_ignored_recommendations(
                label_dates,
                self._collection_label_name(),
                self.watched_ids,
                min_days_shown=ignored_config.get("min_days_shown", IGNORED_REC_MIN_DAYS_SHOWN),
            )
            if not ignored:
                return

            media_items = self._get_media_cache().cache.get(self.media_key, {})
            ignored_items = [media_items[str(rk)] for rk, _days in ignored if str(rk) in media_items]
            if not ignored_items:
                return

            applied = apply_ignored_penalties(
                self.watched_data_counters,
                ignored_items,
                penalty=ignored_config.get("penalty", IGNORED_REC_PENALTY),
            )
            print(
                f"{YELLOW}Negative feedback: {len(ignored_items)} recommendation(s) shown "
                f"but never watched are now counted against this profile{RESET}"
            )
            logger.debug(f"Ignored-recommendation penalties applied: {applied}")
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            logger.debug(f"Ignored-recommendation feedback skipped: {e}")

    def _report_library_health(self, unwatched_items: List[Dict]) -> None:
        """
        Measure and report candidate supply (utils/library_health.py).

        Also stashes the resulting supply gaps on the instance so the
        external/Radarr discovery path can aim acquisition at the genres
        this profile wants and the library cannot serve, instead of at
        the genres it already holds most of.
        """
        self.supply_gaps = []
        try:
            genre_counter = (self.watched_data_counters or {}).get("genres") or {}
            target_distribution = build_target_distribution(genre_counter)

            # Depletion is a statement about a user having consumed their
            # library, which is meaningless without watch history: a
            # zero-history user facing a small library has not "watched
            # most of it", and telling them so would be simply false.
            # Cold start is handled by the recommend_for_no_history path,
            # not by this report.
            if not target_distribution:
                logger.debug("Skipping library health report: no watch history to assess supply against")
                return

            health = assess_pool_health(len(unwatched_items), self.limit_results)
            self.supply_gaps = find_supply_gaps(
                target_distribution,
                [[g.lower() for g in (i.get("genres") or [])] for i in unwatched_items],
            )

            for line in format_health_report(health, self.supply_gaps, self.media_key):
                if health.depleted:
                    log_warning(line)
                else:
                    print(line)
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            # Reporting must never take down a run that would otherwise
            # produce a collection.
            logger.debug(f"Library health report skipped: {e}")

    def _select_calibrated(self, sorted_candidates: List, media_items: Dict, target_count: int) -> List:
        """
        Pick the final `target_count` items from score-sorted candidates.

        With calibration disabled (the default) this is the historical
        plain truncation. With it enabled, the selection instead matches
        the collection's genre mix to the user's actual watch-history mix
        - see utils/calibration.py.

        Returns the selected (item_id, (plex_item, score)) entries.
        """
        if self.calibration_strength <= 0:
            return sorted_candidates[:target_count]

        genre_counter = (self.watched_data_counters or {}).get("genres") or {}
        target_distribution = build_target_distribution(genre_counter)
        if not target_distribution:
            # Cold start: no profile to calibrate against, so score order
            # (which _rank_key already falls back to rating/votes for) is
            # the only meaningful signal.
            return sorted_candidates[:target_count]

        def _genres(entry):
            item_id, _ = entry
            item_info = media_items.get(str(item_id)) or {}
            return [g.lower() for g in (item_info.get("genres") or [])]

        def _certificate(entry):
            item_id, _ = entry
            item_info = media_items.get(str(item_id)) or {}
            cert = item_info.get("content_rating")
            # An item with no certificate contributes nothing to this
            # dimension rather than being bucketed as "unknown" - see
            # build_certificate_distribution.
            return [str(cert).strip()] if cert else []

        # Sample sizes gate each dimension: calibration reproduces its
        # target faithfully, so a target derived from a handful of titles
        # is a confidently WRONG target, not a weak one. Measured: a user
        # with two watched shows (both TV-G) would have had their whole
        # collection driven to ~100% TV-G.
        genre_sample = len(self.watched_ids)
        dimensions = [
            CalibrationDimension("genre", target_distribution, _genres, CALIBRATION_GENRE_WEIGHT, genre_sample),
        ]

        # Certificate dimension: what the profile is rated, not just what
        # it is about. Built from the watched items' own certificates
        # rather than a profile counter, since nothing tracks them there.
        watched_certs = [(media_items.get(str(rk)) or {}).get("content_rating") for rk in self.watched_ids]
        cert_target = build_certificate_distribution(watched_certs)
        cert_sample = sum(1 for c in watched_certs if c)
        if cert_target:
            dimensions.append(
                CalibrationDimension(
                    "certificate", cert_target, _certificate, CALIBRATION_CERTIFICATE_WEIGHT, cert_sample
                )
            )
        else:
            logger.debug("No certificates on watched items - calibrating on genre alone")

        skipped = [d for d in dimensions if not is_sufficiently_sampled(d)]
        for d in skipped:
            log_warning(
                f"Not calibrating on {d.name}: its target comes from only {d.sample_size} watched "
                f"{self.media_key} (minimum {CALIBRATION_MIN_PROFILE_SAMPLE}). Calibrating to a target "
                f"that thin would pull the collection toward a handful of titles rather than a taste."
            )
        if len(skipped) == len(dimensions):
            log_warning(
                "Skipping calibration entirely for this profile - ranking by similarity instead. "
                "It needs more watch history before its distribution means anything."
            )
            return sorted_candidates[:target_count]

        # Calibration works by CHOOSING. Handed no more candidates than
        # slots it returns them unchanged, which looks identical to
        # "calibration is enabled and working" from the outside - the
        # failure that wasted a full debugging cycle: min_similarity 0.10
        # cut a 125-candidate pool to 48 for a 50-item collection, so
        # every run silently produced an uncalibrated collection while
        # reporting that it had calibrated one.
        if len(sorted_candidates) <= target_count:
            log_warning(
                f"Calibration cannot act: only {len(sorted_candidates)} candidates for "
                f"{target_count} slots, so every candidate is selected regardless of mix. "
                f"Lower movies/tv min_similarity (currently {self.min_similarity:.2f}) or "
                f"raise limit_results' candidate supply - calibration needs more candidates "
                f"than slots to have anything to choose between."
            )

        selected = calibrate_multi(
            sorted_candidates,
            target_count,
            get_score=lambda entry: entry[1][1],
            dimensions=dimensions,
            calibration_strength=self.calibration_strength,
        )

        # Report ONLY the dimensions that were actually applied. Printing a
        # dimension that calibrate_multi() dropped made a genre-only run
        # look like a genre+certificate one - the same "looks like
        # success" failure this guard exists to prevent, reintroduced by
        # the guard itself.
        applied = [d for d in dimensions if is_sufficiently_sampled(d)]
        applied_names = {d.name for d in applied}

        if selected and applied:
            dims = "+".join(d.name for d in applied)
            strength = self.calibration_strength
            print(f"{GREEN}Calibrated collection to profile {dims} mix (strength {strength:.2f}){RESET}")
            if "genre" in applied_names:
                for genre, target, actual in calibration_report(target_distribution, [_genres(e) for e in selected]):
                    print(f"  {genre:<20} profile {target * 100:5.1f}%  ->  collection {actual * 100:5.1f}%")
            if "certificate" in applied_names:
                for cert, target, actual in calibration_report(cert_target, [_certificate(e) for e in selected]):
                    print(f"  [{cert:<18}] profile {target * 100:5.1f}%  ->  collection {actual * 100:5.1f}%")

        return selected

    def _update_labels_by_rank(
        self, all_candidates: Dict, unwatched_labeled: List, label_name: str, target_count: int
    ) -> List:
        """Update labels to keep only top-scoring items, return final collection."""
        # #291 tiebreaker - same rationale as get_recommendations()'s own
        # scored_items.sort(): all_candidates values are (plex_item, score)
        # tuples, and plex_item itself carries no TMDB rating/vote_count,
        # so the tiebreak fields are looked up from the media cache by
        # item_id (same cache/key shape _build_scored_candidates above
        # already reads them from).
        media_cache = self._get_media_cache()
        media_items = media_cache.cache.get(self.media_key, {})

        def _rank_key(entry):
            item_id, (_plex_item, score) = entry
            item_info = media_items.get(str(item_id)) or {}
            return (score, item_info.get("rating") or 0.0, item_info.get("vote_count") or 0)

        sorted_candidates = sorted(all_candidates.items(), key=_rank_key, reverse=True)

        # Quality gate before size. A collection short of target_count is a
        # truthful report that the library is exhausted for this user;
        # padding it to target_count with sub-threshold items is not.
        if self.min_similarity > 0:
            eligible = [c for c in sorted_candidates if c[1][1] >= self.min_similarity]
            dropped = len(sorted_candidates) - len(eligible)
            if dropped:
                log_warning(
                    f"{dropped} of {len(sorted_candidates)} candidates scored below "
                    f"min_similarity {self.min_similarity:.2f} and were excluded"
                )
            sorted_candidates = eligible

        top_candidates = self._select_calibrated(sorted_candidates, media_items, target_count)

        if len(top_candidates) < target_count:
            log_warning(
                f"Collection is {target_count - len(top_candidates)} short of the configured "
                f"{target_count}: only {len(top_candidates)} candidates qualified. The library "
                f"is running out of unwatched {self.media_key} matching this profile - consider "
                f"adding new content rather than lowering the bar."
            )

        top_ids = {item_id for item_id, _ in top_candidates}

        current_ids = {int(m.ratingKey) for m in unwatched_labeled}
        ids_to_add = top_ids - current_ids
        ids_to_remove = current_ids - top_ids

        if ids_to_remove:
            items_to_remove = [m for m in unwatched_labeled if int(m.ratingKey) in ids_to_remove]
            print(f"{YELLOW}Removing {len(items_to_remove)} lower-scoring items to make room for better ones{RESET}")
            remove_labels_from_items(items_to_remove, label_name, self.label_dates, "replaced by higher score")

        items_to_add = [all_candidates[item_id][0] for item_id in ids_to_add if item_id in all_candidates]
        if items_to_add:
            print(f"{GREEN}Adding {len(items_to_add)} new high-scoring recommendations{RESET}")
            add_labels_to_items(items_to_add, label_name, self.label_dates)

        print(f"{GREEN}Collection now has top {len(top_candidates)} recommendations by score{RESET}")
        return [plex_item for item_id, (plex_item, score) in top_candidates]

    def _sync_plex_collection(
        self,
        section,
        label_name: str,
        final_items: List,
        username: Optional[str] = None,
        private_label: Optional[str] = None,
        legacy_private_labels: Optional[List[str]] = None,
    ) -> bool:
        """Create/update Plex collection with final recommendations.

        username/private_label are the real per-user identity and the
        already-built PrivateCollection_* label - both computed once by
        the caller (manage_plex_labels) and passed through explicitly,
        rather than re-derived here by stripping a "Recommended_" prefix
        off label_name (#261: that stripping was a silent no-op whenever
        collections.append_usernames was false, since label_name was then
        just the bare base label with nothing to strip - every user's
        collection/label ended up named the literal string "Recommended").

        legacy_private_labels: #357 - older label form(s) this user's
        collection may still carry (see
        _compute_legacy_private_label_names), passed through to
        update_plex_collection so a rename_on_template_change search
        cannot miss a collection labeled before this run's private_label
        was ever refreshed.

        Returns:
            True if collection was created/updated, False otherwise.
        """
        if not final_items:
            print(f"{YELLOW}No items to add to collection{RESET}")
            return False

        if not username:
            # Defensive only - every real caller (manage_plex_labels) always
            # supplies self.single_user, which utils/cli.py's per-user loop
            # always sets to a real username. Falls back to the old (#261)
            # derivation so a hypothetical future caller degrades instead of
            # crashing; still correct whenever label_name IS prefixed.
            log_warning(
                "_sync_plex_collection called with no username - using legacy "
                "prefix-strip derivation, which only works if label_name is "
                "prefixed with 'Recommended_' (see #261)"
            )
            username = label_name.replace("Recommended_", "")

        if (
            self.user_preferences
            and username in self.user_preferences
            and "display_name" in self.user_preferences[username]
        ):
            display_name = self.user_preferences[username]["display_name"]
        else:
            display_name = username.capitalize()

        # emoji is still needed below, independent of the #267 naming
        # template - cleanup_old_collections/cleanup_legacy_unnamed_
        # collection match OLD, hardcoded "{emoji} ..." patterns to find
        # orphaned collections left behind by a prior run/config, and
        # that legacy-pattern matching is unaffected by whatever NEW
        # template collections.movie_name_template/tv_name_template may
        # now be set to.
        emoji = "🎬" if self.media_type == "movie" else "📺"

        # #267: collections.movie_name_template/tv_name_template (see
        # config/tuning.example.yml) - defaults are byte-for-byte the
        # pre-#267 hardcoded "{emoji} {display_name} - Recommendation"
        # format, so an install that never sets these sees zero change.
        # The multi-library disambiguation suffix is appended
        # unconditionally AFTER the template renders (see
        # render_collection_name's own docstring for why) - a custom
        # template can't accidentally break that correctness guarantee.
        name_template = self.config.get("collections", {}).get(
            f"{self.media_type}_name_template",
            DEFAULT_MOVIE_NAME_TEMPLATE if self.media_type == "movie" else DEFAULT_TV_NAME_TEMPLATE,
        )
        rendered_name = render_collection_name(name_template, display_name, self.media_type)
        collection_name = f"{rendered_name}{self._library_suffix_for_collection_name()}"
        # Default True: an old-named collection left behind by a template
        # change is renamed (via the PrivateCollection_* label, never
        # title-guessing) rather than orphaned - see
        # config/tuning.example.yml's own comment and
        # utils.plex.update_plex_collection's docstring for the exact
        # ownership/collision rules.
        rename_on_template_change = self.config.get("collections", {}).get("rename_on_template_change", True)
        success = update_plex_collection(
            section,
            collection_name,
            final_items,
            logger,
            label_name=label_name,
            private_label=private_label,
            rename_on_template_change=rename_on_template_change,
            legacy_private_labels=legacy_private_labels,
        )
        if success:
            cleanup_old_collections(section, collection_name, username, emoji, logger)
            cleanup_legacy_unnamed_collection(section, collection_name, emoji, logger)
        return success

    def manage_plex_labels(self, recommended_items: List[Dict]) -> bool:
        """Manage Plex labels and collections for recommendations.

        Returns:
            True if collection was created/updated, False otherwise.
        """
        if not self.config.get("collections", {}).get("add_label", True):
            print(f"{YELLOW}Skipping collection creation (add_label is disabled in config){RESET}")
            return False

        recommended_items = recommended_items or []

        if not recommended_items:
            print(f"{YELLOW}No recommendations generated - collection not created{RESET}")
            return False

        if self.confirm_operations and recommended_items:
            selected_items = self._user_select_recommendations(recommended_items, "label in Plex")
            if not selected_items:
                selected_items = []
        else:
            selected_items = recommended_items

        try:
            section = self.plex.library.section(self.library_title)
            base_label = self.config.get("collections", {}).get("label_name", "Recommended")
            base_label = f"{base_label}{self._library_suffix_for_label()}"
            # Default True (#261): matches config/tuning.example.yml's
            # documented default. Nothing in any install path (Dockerfile,
            # setup.sh, the web UI) ever writes a real tuning.yml, so every
            # fresh install ran on this code default - False meant every
            # user's item label AND collection-level filter label collapsed
            # to the identical bare base_label ("Recommended"), which made
            # private_collections (below) push a filter that hid every
            # user's recommendations from every other user instead of
            # isolating them.
            append_usernames = self.config.get("collections", {}).get("append_usernames", True)
            users = self.users["plex_users"] or self.users["managed_users"]
            label_name = build_label_name(base_label, users, self.single_user, append_usernames)

            # Separate, hardcoded-root label applied to the COLLECTION
            # object itself (never to individual items) so the exclude
            # filter below only ever hides a collection, never an item
            # shared in everyone's normal library view. Built via the same
            # build_label_name() call (same real username/append_usernames
            # inputs) as label_name above, instead of derived later by
            # rewriting label_name's prefix (#261 - see
            # utils/plex_policy.apply_user_label_restrictions's docstring).
            private_base_label = f"PrivateCollection{self._library_suffix_for_label()}"
            # #352: normalize_case - see build_label_name/_sanitize_user_
            # token's docstrings. This is the label actually attached to
            # the live collection; keeping it case/whitespace-stable is
            # what stops a title flap from making update_plex_collection
            # fail to recognize this run's own collection as the same
            # one it created last run.
            private_label_name = build_label_name(
                private_base_label, users, self.single_user, append_usernames, normalize_case=True
            )

            # Find items in Plex
            items_found, skipped = self._find_plex_items_for_recs(section, selected_items)
            if skipped:
                log_warning(f"Skipped {len(skipped)} {self.media_key} not found in Plex:")
                for item in skipped[:5]:
                    print(f"  - {item}")
                if len(skipped) > 5:
                    print(f"  ... and {len(skipped) - 5} more")

            if not items_found and skipped:
                print(f"{RED}No recommendations found in your Plex library - collection not created{RESET}")
                return False

            print(f"{GREEN}Starting incremental collection update with staleness check...{RESET}")

            if not hasattr(self, "label_dates") or not self.label_dates:
                self.label_dates = {}

            stale_days = self.config.get("collections", {}).get("stale_removal_days", 7)

            # Remove outdated labels and get fresh items
            unwatched_labeled = self._remove_outdated_labels(section, label_name, stale_days)

            # Build candidates with scores - target_count is limit_results
            # (config/tuning.yml movies:/tv:), resolved once in __init__ (see
            # its comment there for why this no longer reads
            # general.limit_plex_results, which is the candidate-buffer size,
            # not the final collection size).
            target_count = self.limit_results
            print(f"{GREEN}Building optimal collection of top {target_count} recommendations...{RESET}")

            all_candidates = self._build_scored_candidates(unwatched_labeled, selected_items, items_found)

            # Filter by content rating if user has max_rating preference
            username = self.single_user or (users[0] if users else None)
            max_rating = get_max_rating_for_user(self.user_preferences, username)
            if max_rating:
                all_candidates = self._filter_candidates_by_rating(all_candidates, max_rating)

            # Update labels to keep top items
            final_items = self._update_labels_by_rank(all_candidates, unwatched_labeled, label_name, target_count)

            self._save_watched_cache()

            print(f"{GREEN}Final collection size: {len(final_items)} {self.media_key} (sorted by similarity){RESET}")
            print(f"{GREEN}Successfully updated labels incrementally{RESET}")

            # Sync to Plex collection
            # #357: legacy_private_labels - the compound case from a
            # rename_on_template_change search that would otherwise miss
            # a collection labeled before #352 (or before this user's
            # very first manage_plex_labels run since upgrading) once a
            # new movie_name_template/tv_name_template renders a
            # different collection_name than that old collection's own
            # title.
            legacy_private_labels = self._compute_legacy_private_label_names(self.single_user)
            success = self._sync_plex_collection(
                section, label_name, final_items, self.single_user, private_label_name, legacy_private_labels
            )

            # Apply user label restrictions if private_collections is enabled (default: true)
            # Note: Only works for shared friends, not Plex Home managed users
            if success and self.config.get("collections", {}).get("private_collections", True):
                if self._label_restrictions_state.get("applied"):
                    # #360: this whole block (both the warning and the
                    # actual apply_user_label_restrictions call below) is
                    # a pure function of self.config and the full
                    # configured user list - identical on every one of
                    # the (library x user) calls this run reaches this
                    # point from, never scoped to self.single_user or
                    # self.library (build_all_private_labels always reads
                    # every library of every media type from the full,
                    # unscoped config - see its own docstring). Once
                    # applied by the first such call this run, every
                    # later call would just recompute and re-send the
                    # IDENTICAL result - skip it rather than doing that
                    # redundant work (and redundant Plex API calls) N x L
                    # times per run.
                    logger.debug(
                        "Label restrictions already applied earlier this run (#360) - "
                        "skipping the redundant per-(user, library) recomputation"
                    )
                elif not append_usernames and len(users) > 1:
                    # #261: with more than one user configured, a false
                    # append_usernames means every user's label above is
                    # identical - private_collections has no way to tell
                    # them apart, so applying it would push a filter that
                    # hides the (one, shared) collection and its items from
                    # every non-admin user instead of isolating each user's
                    # own. Fail loud instead of sending that filter.
                    log_warning(
                        "collections.append_usernames is false with more than one "
                        "user configured (see config/tuning.yml) - private_collections "
                        "cannot correctly separate per-user labels this way, since "
                        "every user would get the identical label. Skipping label "
                        "restrictions this run rather than hiding recommendations "
                        "from everyone (#261). Set collections.append_usernames: true "
                        "in config/tuning.yml (the documented default) to enable "
                        "per-user private collections, or private_collections: false "
                        "if a single shared collection is what you actually want."
                    )
                    # #360: this diagnosis is also a pure function of
                    # self.config (append_usernames, user count) - true or
                    # false for the whole run, not just this one call.
                    # Marking it applied here too stops the identical
                    # warning from repeating N x L times.
                    self._label_restrictions_state["applied"] = True
                else:
                    # Build dict of all users' PrivateCollection_* labels for
                    # exclude-based restrictions - each user's own label stays
                    # visible to them, every OTHER user's label is excluded.
                    # Every library's labels, not just this media type's
                    # (#332). apply_user_label_restrictions() writes both
                    # filterMovies and filterTelevision on every call, so
                    # supplying only the running media type's labels meant
                    # the later run (TV) overwrote the earlier one's
                    # (movies) in both fields.
                    all_user_private_labels = build_all_private_labels(self.config, users, append_usernames)

                    # #351: cache_dir so departed owners' labels are
                    # retained/warned-about/prunable - see
                    # utils.plex_policy.apply_user_label_restrictions's
                    # own docstring for what passing this actually does.
                    apply_user_label_restrictions(self.config, all_user_private_labels, cache_dir=self.cache_dir)
                    # #360: mark done for the rest of this run - see the
                    # already-applied branch above for why every later
                    # (library x user) call would just repeat this exact
                    # computation.
                    self._label_restrictions_state["applied"] = True

            return success

        except (plexapi.exceptions.PlexApiException, AttributeError, KeyError) as e:
            log_error(f"Error managing Plex labels: {e}")
            print(traceback.format_exc())
            return False

    def _compute_private_label_name(self, username: Optional[str] = None) -> str:
        """This run's PrivateCollection_<user> label name - identical
        formula to the private_label_name manage_plex_labels() computes
        when it creates/updates a collection (same base label, same
        build_label_name() call, same append_usernames source), so a
        caller can identify/act on a user's EXISTING collection without
        duplicating (and risking silently drifting from - see #261's
        history of exactly that class of bug) that computation.

        Args:
            username: Defaults to self.single_user - the real per-user
                identity every real caller (get_recommendations, via
                _remove_collection_for_no_history) always has set.
        """
        users = self.users["plex_users"] or self.users["managed_users"]
        append_usernames = self.config.get("collections", {}).get("append_usernames", True)
        private_base_label = f"PrivateCollection{self._library_suffix_for_label()}"
        # #352: normalize_case - must match manage_plex_labels' own
        # private_label_name computation above exactly, or this stops
        # correctly identifying a user's own existing collection.
        return build_label_name(
            private_base_label, users, username or self.single_user, append_usernames, normalize_case=True
        )

    def _compute_legacy_private_label_names(self, username: Optional[str] = None) -> List[str]:
        """#357: legacy (pre-#352, not case/whitespace-normalized) form of
        this run's PrivateCollection_<user> label - identical inputs to
        _compute_private_label_name, just normalize_case=False.

        A collection can still carry only this older form if it was
        created before #352 and this user's label was never refreshed
        since - e.g. movies.recommend_for_no_history: false, which skips
        manage_plex_labels() (the only place the label ever gets
        refreshed) entirely. Callers that need to FIND such a collection
        (never to apply a fresh label - only
        _compute_private_label_name's current form is ever newly
        attached, see update_plex_collection's private_label docstring)
        pass this alongside the current form.

        Returns a list (possibly empty, never containing the current
        form itself) so callers can splice it directly alongside
        [private_label, ...] without a separate equality check.
        """
        current = self._compute_private_label_name(username)
        users = self.users["plex_users"] or self.users["managed_users"]
        append_usernames = self.config.get("collections", {}).get("append_usernames", True)
        private_base_label = f"PrivateCollection{self._library_suffix_for_label()}"
        legacy = build_label_name(
            private_base_label, users, username or self.single_user, append_usernames, normalize_case=False
        )
        return [legacy] if legacy != current else []

    def _remove_collection_for_no_history(self, who: str) -> None:
        """#291 recommend_for_no_history: false path - remove any
        collection curatarr already created for a user who now has zero
        watch history. Called ONLY from get_recommendations()'s explicit
        opt-out branch; never on the default (create) path, and never
        for a user with any watch history at all.

        Delegates the actual find/confirm/remove work to
        utils.plex.remove_owned_collection, which trusts ONLY the
        PrivateCollection_<user> label already on the collection - see
        that function's own docstring for the full ownership-safety
        rules (never title/emoji/name-pattern inference, ambiguous
        matches are left alone and logged, every removal is logged).
        """
        if not self.config.get("collections", {}).get("add_label", True):
            # add_label disabled means curatarr never applies
            # PrivateCollection_* to anything - ownership can never be
            # confirmed, so there is nothing safe to remove. Not an
            # error; this run just never created labeled collections.
            return

        try:
            section = self.plex.library.section(self.library_title)
        except plexapi.exceptions.PlexApiException as e:
            log_warning(
                f"Could not access the Plex library section to check for an existing "
                f"collection to remove for {who}: {e}"
            )
            return

        resolved_who = who if who != "the configured user(s)" else None
        private_label = self._compute_private_label_name(resolved_who)
        # #357: also match the pre-#352 legacy label form - this path is
        # exactly the one that never goes through manage_plex_labels (the
        # only place a label gets refreshed to the current form), so a
        # collection created before that upgrade can still carry only
        # the old one.
        legacy_labels = self._compute_legacy_private_label_names(resolved_who)
        media_section = "movies" if self.media_type == "movie" else "tv"
        reason = f"no watch history and {media_section}.recommend_for_no_history is disabled"
        remove_owned_collection(section, [private_label, *legacy_labels], who, reason, logger)

    def _user_select_recommendations(self, recommended_items: List[Dict], operation_label: str) -> List[Dict]:
        """Prompt user to select recommendations - delegates to utility."""
        return user_select_recommendations(recommended_items, operation_label)

    @abstractmethod
    def _get_media_cache(self):
        """Return the media cache instance (movie_cache or show_cache)."""
        pass

    # ------------------------------------------------------------------------
    # TMDB HELPER METHODS (shared by movie and TV recommenders)
    # ------------------------------------------------------------------------
    def _get_plex_item_tmdb_id(self, plex_item) -> Optional[int]:
        """Get TMDB ID for a Plex item with caching.

        Args:
            plex_item: Plex media item (movie or show)

        Returns:
            TMDB ID or None if not found
        """
        cache_key = str(plex_item.ratingKey)
        if cache_key in self.plex_tmdb_cache:
            return self.plex_tmdb_cache[cache_key]

        tmdb_id = get_tmdb_id_for_item(plex_item, self.tmdb_api_key, self.media_type, self.plex_tmdb_cache)

        if tmdb_id:
            self.plex_tmdb_cache[cache_key] = tmdb_id
            self._save_watched_cache()
        return tmdb_id

    def _get_plex_item_imdb_id(self, plex_item) -> Optional[str]:
        """Get IMDb ID for a Plex item with fallback to TMDB.

        Args:
            plex_item: Plex media item (movie or show)

        Returns:
            IMDb ID string or None if not found
        """
        # Try extracting from GUIDs first
        ids = extract_ids_from_guids(plex_item)
        if ids["imdb_id"]:
            return ids["imdb_id"]

        # Fallback: Check legacy guid attribute
        if hasattr(plex_item, "guid") and plex_item.guid and plex_item.guid.startswith("imdb://"):
            return plex_item.guid.split("imdb://")[1]

        # Fallback to TMDB to get IMDb ID
        tmdb_id = self._get_plex_item_tmdb_id(plex_item)
        if tmdb_id:
            if self.media_type == "movie":
                data = fetch_tmdb_with_retry(
                    f"https://api.themoviedb.org/3/movie/{tmdb_id}", {"api_key": self.tmdb_api_key}
                )
                return data.get("imdb_id") if data else None
            else:
                # TV shows need the external_ids endpoint
                data = fetch_tmdb_with_retry(
                    f"https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids", {"api_key": self.tmdb_api_key}
                )
                return data.get("imdb_id") if data else None
        return None

    def _get_tmdb_id_via_imdb(self, plex_item) -> Optional[int]:
        """Get TMDB ID using IMDb ID as a fallback method.

        Args:
            plex_item: Plex media item (movie or show)

        Returns:
            TMDB ID or None if not found
        """
        imdb_id = self._get_plex_item_imdb_id(plex_item)
        if not imdb_id or not self.tmdb_api_key:
            return None

        data = fetch_tmdb_with_retry(
            f"https://api.themoviedb.org/3/find/{imdb_id}", {"api_key": self.tmdb_api_key, "external_source": "imdb_id"}
        )
        if data:
            results_key = "movie_results" if self.media_type == "movie" else "tv_results"
            results = data.get(results_key, [])
            if results:
                return results[0].get("id")
        return None

    def _get_tmdb_keywords_for_id(self, tmdb_id: int) -> Set[str]:
        """Get keywords for a media item from TMDB.

        Args:
            tmdb_id: TMDB ID of the item

        Returns:
            Set of keyword strings
        """
        if not tmdb_id or not self.use_tmdb_keywords or not self.tmdb_api_key:
            return set()

        keywords = get_tmdb_keywords(self.tmdb_api_key, tmdb_id, self.media_type, self.tmdb_keywords_cache)
        if keywords:
            self._save_watched_cache()
        return set(keywords)

    def _get_all_library_items(self) -> List:
        """Fetch this run's full library item list from Plex exactly once,
        then reuse it for every consumer that needs a full library scan -
        cache update, ID/title-set derivation, and view-count lookups all
        used to call section.all() independently, up to 6 Plex round trips
        per user per run (#233 audit remediation batch D / PR1(a)).

        Cached per library_title in self._library_items_cache. When that
        dict is the one utils.cli.run_recommender_main shares across every
        user processed against this library in a single run, this also
        collapses the fetch to once per library per run, not once per user
        - see __init__'s library_items_cache docstring. Only a *successful*
        fetch is cached; a Plex error here propagates to the caller (which
        already has its own try/except and log message) without poisoning
        the cache, so the next consumer simply retries instead of being
        stuck with a cached failure.
        """
        if self.library_title not in self._library_items_cache:
            section = self.plex.library.section(self.library_title)
            self._library_items_cache[self.library_title] = section.all()
        return self._library_items_cache[self.library_title]

    def _get_all_library_items_for_user(self, username: str) -> List:
        """Fetch `username`'s OWN library item snapshot from Plex (#273) -
        unlike _get_all_library_items() above (one snapshot fetched
        through the shared ADMIN token), this switches to `username`'s
        own Plex account first, so viewCount/userRating on each returned
        item reflect THAT user's own account state. Both are per-account
        Plex state the admin's token can never see for another user -
        verified against a real library: every real watched_cache_plex_
        <user>.json on disk had zero negative rating signals despite the
        library containing plenty of low-rated watched movies, because
        every builder read this state through one shared admin snapshot.

        Only used behind the profile_accuracy.enabled config flag (see
        movie.py's/tv.py's watched-data builders) - default ON since
        v2.10.82; the legacy path (_get_all_library_items()'s shared
        admin snapshot) is still available by explicitly setting
        profile_accuracy.enabled: false, so this method has zero effect
        on any install that has opted back out.

        Cached in the same shared _library_items_cache dict as
        _get_all_library_items(), under a per-user key so it's never
        confused with the plain admin-snapshot entry (or another user's),
        and is still reused (not re-fetched) if called more than once for
        the same user within one run.

        Falls back to _get_all_library_items() (the admin snapshot) if
        `username` IS the admin account (switchUser is a no-op for the
        account that's already connected) or if switching to their
        account fails for any reason (matches
        utils.plex.get_user_specific_connection's own fallback shape) -
        never raises up to the caller.
        """
        admin_user = self.users["admin_user"]
        if username.lower() == admin_user.lower():
            return self._get_all_library_items()

        cache_key = f"{self.library_title}::user::{username}"
        if cache_key in self._library_items_cache:
            return self._library_items_cache[cache_key]

        try:
            account = MyPlexAccount(token=self.config["plex"]["token"])
            user = account.user(username)
            user_plex = self.plex.switchUser(user)
            items = user_plex.library.section(self.library_title).all()
        except (plexapi.exceptions.PlexApiException, KeyError, AttributeError) as e:
            log_warning(f"Could not fetch {username}'s own library snapshot, falling back to shared admin view: {e}")
            return self._get_all_library_items()

        self._library_items_cache[cache_key] = items
        return items

    def _get_library_imdb_ids(self) -> Set[str]:
        """Get set of all IMDb IDs in the library."""
        return get_library_imdb_ids_from_items(self._get_all_library_items())

    @abstractmethod
    def _find_plex_item(self, section, rec: Dict):
        """Find a Plex item matching the recommendation."""
        pass

    @abstractmethod
    def _calculate_similarity_from_cache(self, item_info: Dict) -> Tuple[float, Dict]:
        """Calculate similarity score for an item."""
        pass

    def _print_similarity_breakdown(self, item_info: Dict, score: float, breakdown: Dict):
        """Print detailed breakdown of similarity score calculation.

        Concrete for both media types (movie.py/tv.py previously
        duplicated this, differing only in the media_type literal
        passed through to the shared print_similarity_breakdown
        formatter - self.media_type already carries that).
        """
        print_similarity_breakdown(item_info, score, breakdown, self.media_type)

    @abstractmethod
    def _get_watched_data(self) -> Dict:
        """
        Get watched media data from Plex.

        Must be implemented by subclasses.

        Returns:
            Dict with counters for genres, actors, etc.
        """
        pass

    @abstractmethod
    def _get_watched_count(self) -> int:
        """
        Get count of watched items from Plex (for cache invalidation).

        Must be implemented by subclasses.

        Returns:
            Count of watched items
        """
        pass

    @abstractmethod
    def _save_watched_cache(self):
        """
        Save watched data cache to file.

        Must be implemented by subclasses.
        """
        pass

    def _save_cache(self):
        """Save the recommender's caches. Concrete for both media types
        (movie.py/tv.py previously duplicated this identical one-line
        body) - just delegates to the abstract _save_watched_cache
        subclasses must already implement.
        """
        self._save_watched_cache()

    def _enhance_profile_with_trakt(self):
        """
        Enhance watched_data_counters with Trakt watch history.

        This adds items watched on streaming services (not in Plex) to the profile,
        giving the recommender a more complete picture of user preferences.

        Only runs if Trakt is enabled and import.merge_watch_history is True.
        Delegates to shared enhance_profile_with_trakt() utility.
        """
        enhance_profile_with_trakt(
            profile=self.watched_data_counters,
            config=self.config,
            tmdb_api_key=self.tmdb_api_key,
            cache_dir=self.cache_dir,
            media_type=self.media_type,
            single_user=self.single_user,
        )
