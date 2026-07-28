import os
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import math
import re
import traceback
from typing import Any, Dict, List, Optional, Set, Tuple

# Import base classes
from recommenders.base import BaseCache, BaseRecommender

# Import shared utilities
from utils import (
    COLLECTION_BONUS_BASE,
    COLLECTION_BONUS_CAP,
    COLLECTION_BONUS_LOG_FACTOR,
    GREEN,
    RED,
    RESET,
    TOP_CAST_COUNT,
    YELLOW,
    calculate_recency_multiplier,
    calculate_rewatch_multiplier,
    calculate_similarity_score,
    compute_profile_hash,
    create_empty_counters,
    extract_genres,
    extract_ids_from_guids,
    extract_rating,
    fetch_plex_watch_history_shows,
    fetch_show_completion_data,
    fetch_tautulli_show_watched_data,
    format_media_output,
    get_plex_account_ids,
    get_project_root,
    get_watched_show_count,
    identify_dropped_shows,
    log_error,
    log_info,
    log_warning,
    merge_show_watched_data,
    process_counters_from_cache,
    record_run_status,
    run_recommender_main,
    setup_log_file,
    show_progress,
    teardown_log_file,
)

# Module-level logger - configured by setup_logging() in main()
logger = logging.getLogger("curatarr")


class ShowCache(BaseCache):
    """Cache for TV show metadata including TMDB data, genres, and keywords."""

    media_type = "tv"
    media_key = "shows"
    cache_filename = "all_shows_cache.json"

    def _process_item(self, show, tmdb_api_key: Optional[str]) -> Optional[Dict]:
        """Process a single TV show and return its info dict.

        Args:
            show: Plex TV show item
            tmdb_api_key: Optional TMDB API key

        Returns:
            Dict with show metadata or None on error
        """
        # Get TMDB data using base class method
        tmdb_data = (
            self._get_tmdb_data(show, tmdb_api_key)
            if tmdb_api_key
            else {"tmdb_id": None, "imdb_id": None, "keywords": [], "rating": None, "vote_count": None}
        )

        return {
            "title": show.title,
            "year": getattr(show, "year", None),
            "genres": [g.tag.lower() for g in show.genres] if hasattr(show, "genres") else [],
            "studio": getattr(show, "studio", "N/A"),
            "cast": [r.tag for r in show.roles[:TOP_CAST_COUNT]] if hasattr(show, "roles") else [],
            "summary": getattr(show, "summary", ""),
            "language": self._get_language(show),
            "tmdb_keywords": tmdb_data["keywords"],
            "tmdb_id": tmdb_data["tmdb_id"],
            "imdb_id": tmdb_data["imdb_id"],
            # rating/vote_count feed tv: quality_filters (min_rating/
            # min_vote_count) in BaseRecommender.get_recommendations() -
            # previously omitted here, so those thresholds were a no-op
            # for TV (see CHANGELOG). Mirrors MovieCache._process_item.
            "rating": tmdb_data.get("rating"),
            "vote_count": tmdb_data.get("vote_count"),
            "production_company_ids": tmdb_data.get("production_company_ids", []),
        }


class PlexTVRecommender(BaseRecommender):
    """Generates personalized TV show recommendations based on Plex watch history.

    Analyzes watched shows to build preference profiles based on genres, studios,
    actors, languages, and TMDB keywords. Uses similarity scoring to rank unwatched
    shows in your Plex library.
    """

    # Required class attributes for BaseRecommender
    media_type = "tv"
    media_key = "shows"
    library_config_key = "tv_library"
    default_library_name = "TV Shows"

    def _load_weights(self, weights_config: Dict) -> Dict:
        """Load TV-specific scoring weights from config."""
        return {
            "genre": weights_config.get("genre", weights_config.get("genre_weight", 0.20)),
            "actor": weights_config.get("actor", weights_config.get("actor_weight", 0.15)),
            "studio": weights_config.get("studio", weights_config.get("studio_weight", 0.15)),
            "keyword": weights_config.get("keyword", weights_config.get("keyword_weight", 0.45)),
            "language": weights_config.get("language", weights_config.get("language_weight", 0.05)),
        }

    def __init__(
        self,
        config_path: str,
        single_user: Optional[str] = None,
        library: Optional[Dict] = None,
        library_items_cache: Optional[Dict] = None,
    ):
        """Initialize the TV show recommender.

        Args:
            config_path: Path to the config.yml configuration file
            single_user: Optional username to generate recommendations for a single user
            library: Optional normalized library dict (#157 Phase 3 per-library loop)
            library_items_cache: Optional dict shared across every user's
                recommender instance for this library in one run (see
                BaseRecommender.__init__ / _get_all_library_items - #233
                audit remediation batch D / PR1(a))
        """
        # Initialize base class (config, plex, display options, weights, etc.)
        super().__init__(config_path, single_user, library=library, library_items_cache=library_items_cache)

        # TV-specific initialization
        self.cached_unwatched_count = 0
        self.cached_library_show_count = 0
        self.synced_show_ids: Set[int] = set()
        self.cached_unwatched_shows: List[Any] = []
        self.plex_watched_rating_keys: Set[int] = set()

        # Create show cache
        self.show_cache = ShowCache(self.cache_dir, recommender=self)
        # Pass the run's (or run+library-shared) library snapshot in
        # rather than letting update_cache() re-fetch it - see
        # _get_all_library_items() (#233 audit remediation batch D / PR1(a)).
        self.show_cache.update_cache(
            self.plex, self.library_title, self.tmdb_api_key, all_items=self._get_all_library_items()
        )

        # Verify Plex user configuration
        if self.users["plex_users"]:
            users_to_process = [self.single_user] if self.single_user else self.users["plex_users"]
            print(f"{GREEN}Processing recommendations for Plex users: {users_to_process}{RESET}")

        # Verify library exists
        if not self.plex.library.section(self.library_title):
            raise ValueError(f"TV Show library '{self.library_title}' not found in Plex")

        # Update cache paths to be user-specific (uses base class method)
        self.watched_cache_path = os.path.join(self.cache_dir, f"tv_watched_cache_{self._get_user_context()}.json")

        # Load watched cache using base class method
        watched_cache = self._load_watched_cache()

        # Get library rating keys for filtering (must be ints to match watched_ids).
        # Reuses this run's shared library snapshot instead of a fresh
        # section.all() (#233 audit remediation batch D / PR1(a)).
        current_library_rating_keys = {int(show.ratingKey) for show in self._get_all_library_items()}

        # Clean up both watched show tracking mechanisms
        self.plex_watched_rating_keys = {
            rk for rk in self.plex_watched_rating_keys if int(rk) in current_library_rating_keys
        }
        self.watched_ids = {show_id for show_id in self.watched_ids if show_id in current_library_rating_keys}

        if self.plex_tmdb_cache is None:
            self.plex_tmdb_cache = {}
        if self.tmdb_keywords_cache is None:
            self.tmdb_keywords_cache = {}

        current_watched_count = self._get_watched_count()
        cache_exists = os.path.exists(self.watched_cache_path)

        if (not cache_exists) or (current_watched_count != self.cached_watched_count):
            print("Watched count changed or no cache found; gathering watched data now. This may take a while...\n")
            # Clear existing data to force actual fetch (prevents early returns in fetch
            # functions) - {} not None, see BaseRecommender._refresh_watched_data's comment.
            self.watched_data_counters = {}
            self.watched_ids = set()
            if self.users["plex_users"]:
                self.watched_data = self._get_plex_watched_shows_data()
            else:
                self.watched_data = self._get_managed_users_watched_data()
            self.watched_data_counters = self.watched_data
            self.cached_watched_count = current_watched_count
            self._save_watched_cache()
        else:
            print(f"Watched count unchanged. Using cached data for {self.cached_watched_count} shows")
            self.watched_data = self.watched_data_counters
            # Ensure watched_ids are preserved (cache file uses 'watched_show_ids' key)
            if not self.watched_ids and "watched_show_ids" in watched_cache:
                self.watched_ids = {int(id_) for id_ in watched_cache["watched_show_ids"] if str(id_).isdigit()}
            logger.debug(f"Using cached data: {self.cached_watched_count} watched shows, {len(self.watched_ids)} IDs")

        # Enhance profile with Trakt watch history (if enabled)
        self._enhance_profile_with_trakt()

        # Compute profile hash for score caching
        self.profile_hash = compute_profile_hash(self.watched_data_counters)

        print("Fetching library metadata (for existing Shows checks)...")
        self.library_shows = self._get_library_shows_set()
        self.library_imdb_ids = self._get_library_imdb_ids()

    def _get_watched_count(self) -> int:
        """Get count of watched TV shows from Plex (for cache invalidation)"""
        # Determine which users to process
        if self.single_user:
            users_to_check = [self.single_user]
        elif self.users.get("plex_users"):
            users_to_check = self.users["plex_users"]
        else:
            users_to_check = self.users.get("managed_users", [])

        # Use shared utility function
        return get_watched_show_count(self.config, users_to_check)

    # _calculate_rating_multiplier() inherited from BaseRecommender

    def _get_plex_account_ids(self):
        """Get Plex account IDs for configured users with flexible name matching"""
        # Determine which users to process
        users_to_match = [self.single_user] if self.single_user else self.users["plex_users"]

        # Use shared utility function
        return get_plex_account_ids(self.config, users_to_match)

    def _get_plex_watched_shows_data(self) -> Dict:
        """Get watched show data from Plex's native history (using Plex API)"""
        if not self.single_user and hasattr(self, "watched_data_counters") and self.watched_data_counters:
            return self.watched_data_counters

        shows_section = self.plex.library.section(self.library_title)
        counters = create_empty_counters("tv")
        watched_ids: Set[int] = set()
        not_found_count = 0

        log_warning("Querying Plex watch history directly...")
        account_ids = self._get_plex_account_ids()
        if not account_ids:
            log_error("No valid users found!")
            return counters

        # Use shared utility to fetch watch history with timestamps for recency decay.
        # fetch_plex_watch_history_shows() returns a plain set when
        # return_timestamps is False and a (set, dict) tuple when True (see
        # its own docstring) - the isinstance check below is a real type
        # narrowing (return_timestamps=True is always passed here, so this
        # never actually hits the `else`), not a defensive runtime guard.
        history_result = fetch_plex_watch_history_shows(self.config, account_ids, shows_section, return_timestamps=True)
        if isinstance(history_result, tuple):
            watched_ids, show_timestamps = history_result
        else:
            watched_ids, show_timestamps = history_result, {}

        # Optionally merge in Tautulli watch history, weighted the same way as
        # Plex history. Covers users whose Plex-native history is thin (e.g.
        # shared/external users). Falls back to Plex-only if disabled,
        # unreachable, or no users could be mapped.
        if self.config.get("tautulli", {}).get("enabled", False):
            tautulli_ids, tautulli_timestamps = fetch_tautulli_show_watched_data(self.config, account_ids)
            if tautulli_ids:
                plex_count = len(watched_ids)
                watched_ids, show_timestamps = merge_show_watched_data(
                    watched_ids, show_timestamps, tautulli_ids, tautulli_timestamps
                )
                logger.info(
                    f"Tautulli: merged {len(tautulli_ids)} watched shows "
                    f"({len(watched_ids)} unique watched shows, was {plex_count} from Plex alone)"
                )

        # Store watched show IDs
        self.watched_ids.update(watched_ids)

        # Detect dropped shows (started but abandoned)
        dropped_show_ids = set()
        show_completion_data = {}  # Initialize before conditional block
        ns_config = self.config.get("negative_signals", {})
        dropped_config = ns_config.get("dropped_shows", {})
        if ns_config.get("enabled", True) and dropped_config.get("enabled", True):
            print(f"{YELLOW}Analyzing show completion for dropped show detection...{RESET}")
            show_completion_data = fetch_show_completion_data(self.config, account_ids, shows_section)
            dropped_show_ids = identify_dropped_shows(show_completion_data, self.config)
            if dropped_show_ids:
                logger.info(f"Identified {len(dropped_show_ids)} dropped shows as negative signals")
                for show_id in dropped_show_ids:
                    if show_id in show_completion_data:
                        data = show_completion_data[show_id]
                        logger.debug(
                            f"Dropped: {data.get('title')} "
                            f"({data['watched_episodes']}/{data['total_episodes']} eps, "
                            f"{data['completion_percent']:.0f}%)"
                        )

        # Build rewatch data and user ratings for shows
        # Each show gets base weight of 1.0 regardless of episode count
        # Only apply rewatch bonus if user actually rewatched episodes
        show_rewatch_counts: Dict[int, int] = {}
        user_ratings: Dict[int, float] = {}  # Store user ratings for each show

        def _record_show_rewatch_and_rating(show) -> None:
            show_id = int(show.ratingKey)
            if show_id not in watched_ids:
                return
            if hasattr(show, "viewCount") and show.viewCount:
                view_count = int(show.viewCount)
                # Get watched episode count from completion data
                watched_eps = 1
                if show_id in show_completion_data:
                    watched_eps = max(1, show_completion_data[show_id].get("watched_episodes", 1))
                # Calculate actual show rewatches (viewCount / watched_episodes)
                # If > 1, user rewatched some episodes
                rewatches = max(1, view_count // watched_eps)
                show_rewatch_counts[show_id] = max(show_rewatch_counts.get(show_id, 1), rewatches)

            # Get user rating if available
            if hasattr(show, "userRating") and show.userRating:
                user_rating = float(show.userRating)
                if show_id not in user_ratings or user_rating > user_ratings[show_id]:
                    user_ratings[show_id] = user_rating

        # profile_accuracy.enabled (config flag, default OFF - see
        # config/tuning.example.yml, #273): fetches EACH user's own
        # Plex-token library snapshot (_get_all_library_items_for_user)
        # instead of the one shared admin-token snapshot every builder
        # used before - viewCount/userRating are per-account Plex state,
        # so the admin's token can only ever see the admin's OWN values
        # for them, never another configured user's (verified against a
        # real library - see CHANGELOG). Per-user max-merge (rewatch
        # count and rating both) for when more than one user is
        # configured. Disabled (default): unchanged legacy behavior -
        # one shared admin-token snapshot for every user, reused from
        # this run instead of a fresh section.all() (#233 audit
        # remediation batch D / PR1(a)).
        try:
            if self.config.get("profile_accuracy", {}).get("enabled", False):
                users_to_match = [self.single_user] if self.single_user else self.users["plex_users"]
                for username in users_to_match:
                    for show in self._get_all_library_items_for_user(username):
                        _record_show_rewatch_and_rating(show)
            else:
                for show in self._get_all_library_items():
                    _record_show_rewatch_and_rating(show)
        except Exception as e:
            logger.debug(f"Error getting rewatch counts/ratings for shows: {e}")

        # Process show metadata from cache - exclude dropped shows from positive signals
        # Each show weighted equally (1.0 base) regardless of episode count
        normal_watched = watched_ids - dropped_show_ids
        print("")
        print(
            f"Processing {len(normal_watched)} watched shows with recency decay "
            f"(excluding {len(dropped_show_ids)} dropped):"
        )

        # Track production companies for franchise/spinoff bonus
        production_companies: Dict[int, float] = {}  # production_company_id -> weighted count

        for i, show_id in enumerate(normal_watched, 1):
            show_progress("Processing", i, len(normal_watched))

            show_info = self.show_cache.cache["shows"].get(str(show_id))
            if show_info:
                # Calculate recency multiplier based on last episode watched
                viewed_at = show_timestamps.get(show_id)
                recency_multiplier = (
                    calculate_recency_multiplier(viewed_at, self.config.get("recency_decay", {})) if viewed_at else 1.0
                )

                # Base weight 1.0 per show, with rewatch bonus only if actually rewatched
                rewatch_multiplier = calculate_rewatch_multiplier(show_rewatch_counts.get(show_id, 1))

                # Calculate rating multiplier based on user's star rating
                rating_multiplier = self._calculate_rating_multiplier(user_ratings.get(show_id))

                # Combined weight: recency * rewatch * rating
                weight = recency_multiplier * rewatch_multiplier * rating_multiplier
                process_counters_from_cache(show_info, counters, media_type="tv", weight=weight)

                if tmdb_id := show_info.get("tmdb_id"):
                    counters["tmdb_ids"].add(tmdb_id)

                # Track production companies with weight for franchise bonus
                for pc_id in show_info.get("production_company_ids", []):
                    production_companies[pc_id] = production_companies.get(pc_id, 0) + weight
            else:
                not_found_count += 1

        # Process dropped shows as negative signals
        if dropped_show_ids:
            penalty_mult = dropped_config.get("penalty_multiplier", -0.4)
            print("")
            print(f"{YELLOW}Processing {len(dropped_show_ids)} dropped shows as negative signals...{RESET}")

            for show_id in dropped_show_ids:
                show_info = self.show_cache.cache["shows"].get(str(show_id))
                if show_info:
                    # Process with negative weight
                    cap_penalty = dropped_config.get("cap_penalty", 0.5)
                    process_counters_from_cache(
                        show_info, counters, media_type="tv", weight=penalty_mult, cap_penalty=cap_penalty
                    )

                    # Still track TMDB ID so we don't recommend the same show
                    if tmdb_id := show_info.get("tmdb_id"):
                        counters["tmdb_ids"].add(tmdb_id)

        logger.debug(f"Watched shows not in cache: {not_found_count}, TMDB IDs collected: {len(counters['tmdb_ids'])}")

        # Store production companies for franchise/spinoff bonus during scoring
        counters["production_companies"] = production_companies

        return counters

    # _get_managed_users_watched_data() is inherited from BaseRecommender

    # ------------------------------------------------------------------------
    # CACHING LOGIC
    # ------------------------------------------------------------------------
    def _save_watched_cache(self):
        """Save watched show cache using base class utility."""
        self._do_save_watched_cache()

    # _save_cache() inherited from BaseRecommender

    def _get_media_cache(self):
        """Return the show cache instance."""
        return self.show_cache

    def _find_plex_item(self, section, rec: Dict):
        """Find a Plex show matching the recommendation."""
        return next((s for s in section.search(title=rec["title"]) if s.year == rec.get("year")), None)

    # ------------------------------------------------------------------------
    # LIBRARY UTILITIES
    # ------------------------------------------------------------------------
    def _get_library_shows_set(self) -> Set[Tuple[str, Optional[int]]]:
        try:
            library_shows = set()
            for show in self._get_all_library_items():
                # Handle both normal titles and titles with embedded years
                title = show.title.lower()
                year = show.year

                # Add normal version
                library_shows.add((title, year))

                # Check for and strip embedded year pattern
                year_match = re.search(r"\s*\((\d{4})\)$", title)
                if year_match:
                    clean_title = title.replace(year_match.group(0), "").strip()
                    embedded_year = int(year_match.group(1))
                    library_shows.add((clean_title, embedded_year))

            return library_shows
        except Exception as e:
            log_error(f"Error getting library shows: {e}")
            return set()

    # _get_library_imdb_ids() inherited from BaseRecommender

    def get_show_details(self, show) -> Dict:
        """Extract comprehensive details from a TV show object."""
        try:
            show.reload()

            # Extract IDs using utility
            ids = extract_ids_from_guids(show)
            imdb_id = ids["imdb_id"]
            audience_rating: float = 0
            tmdb_keywords = []

            # Extract rating using shared utility
            if self.show_rating:
                audience_rating = extract_rating(show)

            if self.use_tmdb_keywords and self.tmdb_api_key:
                tmdb_id = self._get_plex_item_tmdb_id(show)
                if tmdb_id:
                    tmdb_keywords = list(self._get_tmdb_keywords_for_id(tmdb_id))

            show_info = {
                "title": show.title,
                "year": getattr(show, "year", None),
                "genres": extract_genres(show),
                "summary": getattr(show, "summary", ""),
                "studio": getattr(show, "studio", "N/A"),
                "language": self.show_cache._get_language(show),
                "imdb_id": imdb_id,
                "ratings": {"audience_rating": audience_rating} if audience_rating > 0 else {},
                "cast": [],
                "tmdb_keywords": tmdb_keywords,
            }

            if self.show_cast and hasattr(show, "roles"):
                show_info["cast"] = [r.tag for r in show.roles[:TOP_CAST_COUNT]]

            return show_info

        except Exception as e:
            log_warning(f"Error getting show details for {show.title}: {e}")
            return {}

    def _get_watched_data(self) -> Dict:
        """Get watched TV show data from Plex (implements abstract method from base)."""
        if self.users["plex_users"]:
            return self._get_plex_watched_shows_data()
        return self._get_managed_users_watched_data()

    # TMDB methods inherited from BaseRecommender:
    # - _get_plex_item_tmdb_id()
    # - _get_plex_item_imdb_id()
    # - _get_tmdb_id_via_imdb()
    # - _get_tmdb_keywords_for_id()

    # ------------------------------------------------------------------------
    # CALCULATE SCORES
    # ------------------------------------------------------------------------
    def _calculate_similarity_from_cache(self, show_info: Dict) -> Tuple[float, Dict]:
        """Calculate similarity score using cached show data and return score with breakdown"""
        # Build user profile from watched data
        user_profile = {
            "genres": self.watched_data.get("genres", {}),
            "studios": self.watched_data.get("studios", {}),
            "actors": self.watched_data.get("actors", {}),
            "languages": self.watched_data.get("languages", {}),
            "keywords": self.watched_data.get("tmdb_keywords", {}),
        }

        # Build content info dict
        content_info = {
            "genres": show_info.get("genres", []),
            "studio": show_info.get("studio", "N/A"),
            "cast": show_info.get("cast", []),
            "language": show_info.get("language", "N/A"),
            "keywords": show_info.get("tmdb_keywords", []),
            "vote_count": show_info.get("vote_count", 0),
        }

        # Use shared scoring function
        score, breakdown = calculate_similarity_score(
            content_info=content_info,
            user_profile=user_profile,
            media_type="tv",
            weights=self.weights,
            normalize_counters=self.normalize_counters,
            use_fuzzy_keywords=self.use_tmdb_keywords,
        )

        # Apply franchise/spinoff bonus based on shared production companies
        show_pc_ids = show_info.get("production_company_ids", [])
        user_production_companies = self.watched_data.get("production_companies", {})
        if show_pc_ids and user_production_companies:
            # Find max weight from any shared production company
            max_pc_weight = 0
            matching_pc_count = 0
            for pc_id in show_pc_ids:
                if pc_id in user_production_companies:
                    max_pc_weight = max(max_pc_weight, user_production_companies[pc_id])
                    matching_pc_count += 1

            if max_pc_weight > 0:
                # Apply bonus similar to movie collection bonus
                # Logarithmic bonus based on how many shows from this production company
                bonus = COLLECTION_BONUS_BASE * (1 + math.log2(max(1, max_pc_weight)) * COLLECTION_BONUS_LOG_FACTOR)
                bonus = min(bonus, COLLECTION_BONUS_CAP)
                score = min(1.0, score * (1 + bonus))
                breakdown["franchise_bonus"] = round(bonus, 3)
                breakdown["details"]["franchise"] = (
                    f"Shared production company (weight: {max_pc_weight:.1f}, bonus: {round(bonus * 100, 1)}%)"
                )

        return score, breakdown

    # _print_similarity_breakdown(), get_recommendations() and
    # manage_plex_labels() are inherited from BaseRecommender


# ------------------------------------------------------------------------
# OUTPUT FORMATTING
# ------------------------------------------------------------------------
def format_show_output(
    show: Dict,
    show_summary: bool = False,
    index: Optional[int] = None,
    show_cast: bool = False,
    show_language: bool = False,
    show_rating: bool = False,
    show_imdb_link: bool = False,
) -> str:
    """Format TV show for display - delegates to shared utility"""
    return format_media_output(
        media=show,
        media_type="tv",
        show_summary=show_summary,
        index=index,
        show_cast=show_cast,
        show_language=show_language,
        show_rating=show_rating,
        show_imdb_link=show_imdb_link,
    )


def main():
    """Entry point for TV show recommendations."""
    run_recommender_main(
        media_type="TV Show",
        description="TV Show Recommendations for Plex",
        process_func=process_recommendations,
        media_type_key="tv",
    )


def process_recommendations(
    config, config_path, log_retention_days, single_user=None, library=None, library_items_cache=None
):
    """Process and display TV show recommendations for configured users."""
    original_stdout = sys.stdout
    log_dir = os.path.join(get_project_root(), "logs")
    setup_log_file(log_dir, log_retention_days, single_user, "recommendations")

    # #292: explicit, structured outcome for THIS user's TV run - see
    # utils/run_status.py's own docstring and recommenders/movie.py's
    # matching hook for the full rationale.
    run_success = True
    run_detail = ""

    # #284: quiet-tier lifecycle visibility - see utils.display.
    # LOG_VERBOSITY_LEVELS's own docstring for why this is at INFO
    # (visible at the quiet default) rather than DEBUG.
    log_info(f"Starting tv recommendations for {single_user or 'configured users'}")

    try:
        # Create recommender with single user context
        recommender = PlexTVRecommender(
            config_path, single_user, library=library, library_items_cache=library_items_cache
        )
        recommendations = recommender.get_recommendations()

        print(f"\n{GREEN}=== Recommended Unwatched Shows in Your Library ==={RESET}")
        plex_recs = recommendations.get("plex_recommendations", [])
        if plex_recs:
            for i, show in enumerate(plex_recs, start=1):
                print(
                    format_show_output(
                        show,
                        show_summary=recommender.show_summary,
                        index=i,
                        show_cast=recommender.show_cast,
                        show_language=recommender.show_language,
                        show_rating=recommender.show_rating,
                        show_imdb_link=recommender.show_imdb_link,
                    )
                )
                print()
        else:
            log_warning("No recommendations found in your Plex library matching your criteria.")

        # Always manage labels (to remove old ones even if no new recommendations)
        recommender.manage_plex_labels(plex_recs)

        log_info(
            f"TV recommendations complete for {single_user or 'configured users'}: {len(plex_recs)} recommendation(s)"
        )

    except Exception as e:
        log_error(f"TV recommendations failed for {single_user or 'configured users'}: {e}")
        print(f"\n{RED}An error occurred: {e}{RESET}")
        print(traceback.format_exc())
        run_success = False
        run_detail = str(e)

    finally:
        if single_user:
            record_run_status(log_dir, "tv", single_user, run_success, run_detail)
        teardown_log_file(original_stdout, log_retention_days)


if __name__ == "__main__":
    main()
