import os
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging
import re
from plexapi.myplex import MyPlexAccount
import yaml
from typing import Dict, List, Set, Optional, Tuple
import copy
from datetime import datetime

# Import shared utilities
from utils import (
    RED, GREEN, YELLOW, CYAN, RESET,
    TOP_CAST_COUNT,
    cleanup_old_logs, setup_logging,
    get_plex_account_ids, get_watched_show_count,
    fetch_plex_watch_history_shows,
    fetch_show_completion_data, identify_dropped_shows,
    log_warning, log_error,
    calculate_rewatch_multiplier,
    calculate_similarity_score,
    show_progress, TeeLogger,
    extract_genres, extract_ids_from_guids,
    adapt_config_for_media_type,
    format_media_output,
    print_similarity_breakdown,
    create_empty_counters, process_counters_from_cache,
    compute_profile_hash
)

# Module-level logger - configured by setup_logging() in main()
logger = logging.getLogger('curatarr')

__version__ = "1.6.19"

# Import base classes
from recommenders.base import BaseCache, BaseRecommender


class ShowCache(BaseCache):
    """Cache for TV show metadata including TMDB data, genres, and keywords."""

    media_type = 'tv'
    media_key = 'shows'
    cache_filename = 'all_shows_cache.json'

    def _process_item(self, show, tmdb_api_key: Optional[str]) -> Optional[Dict]:
        """Process a single TV show and return its info dict.

        Args:
            show: Plex TV show item
            tmdb_api_key: Optional TMDB API key

        Returns:
            Dict with show metadata or None on error
        """
        # Get TMDB data using base class method
        tmdb_data = self._get_tmdb_data(show, tmdb_api_key) if tmdb_api_key else {
            'tmdb_id': None, 'imdb_id': None, 'keywords': []
        }

        return {
            'title': show.title,
            'year': getattr(show, 'year', None),
            'genres': [g.tag.lower() for g in show.genres] if hasattr(show, 'genres') else [],
            'studio': getattr(show, 'studio', 'N/A'),
            'cast': [r.tag for r in show.roles[:TOP_CAST_COUNT]] if hasattr(show, 'roles') else [],
            'summary': getattr(show, 'summary', ''),
            'language': self._get_language(show),
            'tmdb_keywords': tmdb_data['keywords'],
            'tmdb_id': tmdb_data['tmdb_id'],
            'imdb_id': tmdb_data['imdb_id']
        }

class PlexTVRecommender(BaseRecommender):
    """Generates personalized TV show recommendations based on Plex watch history.

    Analyzes watched shows to build preference profiles based on genres, studios,
    actors, languages, and TMDB keywords. Uses similarity scoring to rank unwatched
    shows in your Plex library.
    """

    # Required class attributes for BaseRecommender
    media_type = 'tv'
    media_key = 'shows'
    library_config_key = 'tv_library'
    default_library_name = 'TV Shows'

    def _load_weights(self, weights_config: Dict) -> Dict:
        """Load TV-specific scoring weights from config."""
        return {
            'genre': weights_config.get('genre', weights_config.get('genre_weight', 0.20)),
            'actor': weights_config.get('actor', weights_config.get('actor_weight', 0.15)),
            'studio': weights_config.get('studio', weights_config.get('studio_weight', 0.15)),
            'keyword': weights_config.get('keyword', weights_config.get('keyword_weight', 0.45)),
            'language': weights_config.get('language', weights_config.get('language_weight', 0.05)),
        }

    def __init__(self, config_path: str, single_user: str = None):
        """Initialize the TV show recommender.

        Args:
            config_path: Path to the config.yml configuration file
            single_user: Optional username to generate recommendations for a single user
        """
        # Initialize base class (config, plex, display options, weights, etc.)
        super().__init__(config_path, single_user)

        # TV-specific initialization
        self.cached_unwatched_count = 0
        self.cached_library_show_count = 0
        self.synced_show_ids = set()
        self.cached_unwatched_shows = []
        self.plex_watched_rating_keys = set()

        # Create show cache
        self.show_cache = ShowCache(self.cache_dir, recommender=self)
        self.show_cache.update_cache(self.plex, self.library_title, self.tmdb_api_key)

        # Verify Plex user configuration
        if self.users['plex_users']:
            users_to_process = [self.single_user] if self.single_user else self.users['plex_users']
            print(f"{GREEN}Processing recommendations for Plex users: {users_to_process}{RESET}")

        # Verify library exists
        if not self.plex.library.section(self.library_title):
            raise ValueError(f"TV Show library '{self.library_title}' not found in Plex")

        # Update cache paths to be user-specific (uses base class method)
        self.watched_cache_path = os.path.join(self.cache_dir, f"tv_watched_cache_{self._get_user_context()}.json")

        # Load watched cache using base class method
        watched_cache = self._load_watched_cache()

        # Get library rating keys for filtering (must be ints to match watched_ids)
        shows_section = self.plex.library.section(self.library_title)
        current_library_rating_keys = {int(show.ratingKey) for show in shows_section.all()}

        # Clean up both watched show tracking mechanisms
        self.plex_watched_rating_keys = {
            rk for rk in self.plex_watched_rating_keys
            if int(rk) in current_library_rating_keys
        }
        self.watched_ids = {
            show_id for show_id in self.watched_ids
            if show_id in current_library_rating_keys
        }

        if self.plex_tmdb_cache is None:
            self.plex_tmdb_cache = {}
        if self.tmdb_keywords_cache is None:
            self.tmdb_keywords_cache = {}

        current_watched_count = self._get_watched_count()
        cache_exists = os.path.exists(self.watched_cache_path)
        
        if (not cache_exists) or (current_watched_count != self.cached_watched_count):
            print("Watched count changed or no cache found; gathering watched data now. This may take a while...\n")
            # Clear existing data to force actual fetch (prevents early returns in fetch functions)
            self.watched_data_counters = None
            self.watched_ids = set()
            if self.users['plex_users']:
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
            if not self.watched_ids and 'watched_show_ids' in watched_cache:
                self.watched_ids = {int(id_) for id_ in watched_cache['watched_show_ids'] if str(id_).isdigit()}
            logger.debug(f"Using cached data: {self.cached_watched_count} watched shows, {len(self.watched_ids)} IDs")

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
        elif self.users.get('plex_users'):
            users_to_check = self.users['plex_users']
        else:
            users_to_check = self.users.get('managed_users', [])

        # Use shared utility function
        return get_watched_show_count(self.config, users_to_check)

    def _get_plex_account_ids(self):
        """Get Plex account IDs for configured users with flexible name matching"""
        # Determine which users to process
        users_to_match = [self.single_user] if self.single_user else self.users['plex_users']

        # Use shared utility function
        return get_plex_account_ids(self.config, users_to_match)

    def _get_plex_watched_shows_data(self) -> Dict:
        """Get watched show data from Plex's native history (using Plex API)"""
        if not self.single_user and hasattr(self, 'watched_data_counters') and self.watched_data_counters:
            return self.watched_data_counters

        shows_section = self.plex.library.section(self.library_title)
        counters = create_empty_counters('tv')
        watched_ids = set()
        not_found_count = 0

        log_warning(f"Querying Plex watch history directly...")
        account_ids = self._get_plex_account_ids()
        if not account_ids:
            log_error(f"No valid users found!")
            return counters

        # Use shared utility to fetch watch history
        watched_ids = fetch_plex_watch_history_shows(self.config, account_ids, shows_section)

        # Store watched show IDs
        self.watched_ids.update(watched_ids)

        # Detect dropped shows (started but abandoned)
        dropped_show_ids = set()
        ns_config = self.config.get('negative_signals', {})
        dropped_config = ns_config.get('dropped_shows', {})
        if ns_config.get('enabled', True) and dropped_config.get('enabled', True):
            print(f"{YELLOW}Analyzing show completion for dropped show detection...{RESET}")
            show_completion_data = fetch_show_completion_data(self.config, account_ids, shows_section)
            dropped_show_ids = identify_dropped_shows(show_completion_data, self.config)
            if dropped_show_ids:
                logger.info(f"Identified {len(dropped_show_ids)} dropped shows as negative signals")
                for show_id in dropped_show_ids:
                    if show_id in show_completion_data:
                        data = show_completion_data[show_id]
                        logger.debug(f"Dropped: {data.get('title')} ({data['watched_episodes']}/{data['total_episodes']} eps, {data['completion_percent']:.0f}%)")

        # Build rewatch data for shows (normalize by episode count)
        # Each show gets base weight of 1.0 regardless of episode count
        # Only apply rewatch bonus if user actually rewatched episodes
        show_rewatch_counts = {}
        try:
            for show in shows_section.all():
                show_id = int(show.ratingKey)
                if show_id in watched_ids and hasattr(show, 'viewCount') and show.viewCount:
                    view_count = int(show.viewCount)
                    # Get watched episode count from completion data
                    watched_eps = 1
                    if show_id in show_completion_data:
                        watched_eps = max(1, show_completion_data[show_id].get('watched_episodes', 1))
                    # Calculate actual show rewatches (viewCount / watched_episodes)
                    # If > 1, user rewatched some episodes
                    show_rewatch_counts[show_id] = max(1, view_count // watched_eps)
        except Exception:
            pass  # Fall back to no rewatch weighting if this fails

        # Process show metadata from cache - exclude dropped shows from positive signals
        # Each show weighted equally (1.0 base) regardless of episode count
        normal_watched = watched_ids - dropped_show_ids
        print(f"")
        print(f"Processing {len(normal_watched)} watched shows (excluding {len(dropped_show_ids)} dropped):")

        for i, show_id in enumerate(normal_watched, 1):
            show_progress("Processing", i, len(normal_watched))

            show_info = self.show_cache.cache['shows'].get(str(show_id))
            if show_info:
                # Base weight 1.0 per show, with rewatch bonus only if actually rewatched
                rewatch_multiplier = calculate_rewatch_multiplier(show_rewatch_counts.get(show_id, 1))
                process_counters_from_cache(show_info, counters, media_type='tv', weight=rewatch_multiplier)

                if tmdb_id := show_info.get('tmdb_id'):
                    counters['tmdb_ids'].add(tmdb_id)
            else:
                not_found_count += 1

        # Process dropped shows as negative signals
        if dropped_show_ids:
            penalty_mult = dropped_config.get('penalty_multiplier', -0.4)
            print(f"")
            print(f"{YELLOW}Processing {len(dropped_show_ids)} dropped shows as negative signals...{RESET}")

            for show_id in dropped_show_ids:
                show_info = self.show_cache.cache['shows'].get(str(show_id))
                if show_info:
                    # Process with negative weight
                    cap_penalty = dropped_config.get('cap_penalty', 0.5)
                    process_counters_from_cache(show_info, counters, media_type='tv', weight=penalty_mult, cap_penalty=cap_penalty)

                    # Still track TMDB ID so we don't recommend the same show
                    if tmdb_id := show_info.get('tmdb_id'):
                        counters['tmdb_ids'].add(tmdb_id)

        logger.debug(f"Watched shows not in cache: {not_found_count}, TMDB IDs collected: {len(counters['tmdb_ids'])}")

        return counters

    # _get_managed_users_watched_data() is inherited from BaseRecommender

    # ------------------------------------------------------------------------
    # CACHING LOGIC
    # ------------------------------------------------------------------------
    def _save_watched_cache(self):
        """Save watched show cache using base class utility."""
        self._do_save_watched_cache()

    def _save_cache(self):
        self._save_watched_cache()

    def _get_media_cache(self):
        """Return the show cache instance."""
        return self.show_cache

    def _find_plex_item(self, section, rec: Dict):
        """Find a Plex show matching the recommendation."""
        return next(
            (s for s in section.search(title=rec['title'])
             if s.year == rec.get('year')),
            None
        )

    # ------------------------------------------------------------------------
    # LIBRARY UTILITIES
    # ------------------------------------------------------------------------
    def _get_library_shows_set(self) -> Set[tuple]:
        try:
            shows = self.plex.library.section(self.library_title)
            library_shows = set()
            for show in shows.all():
                # Handle both normal titles and titles with embedded years
                title = show.title.lower()
                year = show.year
                
                # Add normal version
                library_shows.add((title, year))
                
                # Check for and strip embedded year pattern
                year_match = re.search(r'\s*\((\d{4})\)$', title)
                if year_match:
                    clean_title = title.replace(year_match.group(0), '').strip()
                    embedded_year = int(year_match.group(1))
                    library_shows.add((clean_title, embedded_year))
                
            return library_shows
        except Exception as e:
            log_error(f"Error getting library shows: {e}")
            return set()

    # _get_library_imdb_ids() inherited from BaseRecommender

    def get_show_details(self, show) -> Dict:
        try:
            show.reload()

            # Extract IDs using utility
            ids = extract_ids_from_guids(show)
            imdb_id = ids['imdb_id']
            audience_rating = 0
            tmdb_keywords = []
            
            if self.show_rating and hasattr(show, 'ratings'):
                for rating in show.ratings:
                    if (getattr(rating, 'image', '') == 'imdb://image.rating' and 
                        getattr(rating, 'type', '') == 'audience'):
                        try:
                            audience_rating = float(rating.value)
                            break
                        except (ValueError, AttributeError):
                            pass
                            
            if self.use_tmdb_keywords and self.tmdb_api_key:
                tmdb_id = self._get_plex_item_tmdb_id(show)
                if tmdb_id:
                    tmdb_keywords = list(self._get_tmdb_keywords_for_id(tmdb_id))
            
            show_info = {
                'title': show.title,
                'year': getattr(show, 'year', None),
                'genres': extract_genres(show),
                'summary': getattr(show, 'summary', ''),
                'studio': getattr(show, 'studio', 'N/A'),
                'language': self.show_cache._get_language(show),
                'imdb_id': imdb_id,
                'ratings': {
                    'audience_rating': audience_rating
                } if audience_rating > 0 else {},
                'cast': [],
                'tmdb_keywords': tmdb_keywords
            }
            
            if self.show_cast and hasattr(show, 'roles'):
                show_info['cast'] = [r.tag for r in show.roles[:TOP_CAST_COUNT]]
                
            return show_info
                
        except Exception as e:
            log_warning(f"Error getting show details for {show.title}: {e}")
            return {}

    def _get_watched_data(self) -> Dict:
        """Get watched TV show data from Plex (implements abstract method from base)."""
        if self.users['plex_users']:
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
            'genres': self.watched_data.get('genres', {}),
            'studios': self.watched_data.get('studio', {}),
            'actors': self.watched_data.get('actors', {}),
            'languages': self.watched_data.get('languages', {}),
            'keywords': self.watched_data.get('tmdb_keywords', {})
        }

        # Build content info dict
        content_info = {
            'genres': show_info.get('genres', []),
            'studio': show_info.get('studio', 'N/A'),
            'cast': show_info.get('cast', []),
            'language': show_info.get('language', 'N/A'),
            'keywords': show_info.get('tmdb_keywords', []),
            'vote_count': show_info.get('vote_count', 0)
        }

        # Use shared scoring function
        return calculate_similarity_score(
            content_info=content_info,
            user_profile=user_profile,
            media_type='tv',
            weights=self.weights,
            normalize_counters=self.normalize_counters,
            use_fuzzy_keywords=self.use_tmdb_keywords
        )

    def _print_similarity_breakdown(self, show_info: Dict, score: float, breakdown: Dict):
        """Print detailed breakdown of similarity score calculation"""
        print_similarity_breakdown(show_info, score, breakdown, 'tv')

    # get_recommendations() and manage_plex_labels() are inherited from BaseRecommender


# ------------------------------------------------------------------------
# OUTPUT FORMATTING
# ------------------------------------------------------------------------
def format_show_output(show: Dict,
                      show_summary: bool = False,
                      index: Optional[int] = None,
                      show_cast: bool = False,
                      show_language: bool = False,
                      show_rating: bool = False,
                      show_imdb_link: bool = False) -> str:
    """Format TV show for display - delegates to shared utility"""
    return format_media_output(
        media=show,
        media_type='tv',
        show_summary=show_summary,
        index=index,
        show_cast=show_cast,
        show_language=show_language,
        show_rating=show_rating,
        show_imdb_link=show_imdb_link
    )

# ------------------------------------------------------------------------
# CONFIG ADAPTER
# ------------------------------------------------------------------------
def adapt_root_config_to_legacy(root_config):
    """Convert root config.yml format to legacy TRFP format"""
    return adapt_config_for_media_type(root_config, 'tv')

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='TV Show Recommendations for Plex')
    parser.add_argument('username', nargs='?', help='Process recommendations for only this user')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    start_time = datetime.now()
    print(f"{CYAN}TV Show Recommendations for Plex v{__version__}{RESET}")
    print("-" * 50)

    # Load config from project root (one level up from recommenders/)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, 'config.yml')

    try:
        with open(config_path, 'r') as f:
            root_config = yaml.safe_load(f)
        # Adapt root config to legacy format
        base_config = adapt_root_config_to_legacy(root_config)
    except Exception as e:
        log_error(f"Could not load config.yml from project root: {e}")
        log_warning(f"Looking for config at: {config_path}")
        sys.exit(1)

    # Setup logging (--debug flag overrides config)
    logger = setup_logging(debug=args.debug, config=root_config)
    logger.debug("Debug logging enabled")

    general = base_config.get('general', {})
    log_retention_days = general.get('log_retention_days', 7)

    # Process single user mode
    single_user = args.username
    if single_user:
        log_warning(f"Single user mode: {single_user}")

    # Get all users that need to be processed
    all_users = []

    # Check users.list first (new config format)
    users_config = base_config.get('users', {})
    user_list = users_config.get('list', '')
    if user_list:
        if isinstance(user_list, str):
            all_users = [u.strip() for u in user_list.split(',') if u.strip()]
        elif isinstance(user_list, list):
            all_users = user_list

    # Fall back to plex_users.users (legacy format)
    if not all_users:
        plex_config = base_config.get('plex_users', {})
        plex_users = plex_config.get('users')
        if plex_users and str(plex_users).lower() != 'none':
            if isinstance(plex_users, str):
                all_users = [u.strip() for u in plex_users.split(',') if u.strip()]
            elif isinstance(plex_users, list):
                all_users = plex_users

    # Fall back to plex.managed_users (oldest format)
    if not all_users:
        managed_users = base_config.get('plex', {}).get('managed_users', '')
        if managed_users:
            all_users = [u.strip() for u in managed_users.split(',') if u.strip()]

    # If single user specified via command line, override the user list
    if single_user:
        all_users = [single_user]

    if not all_users:
        # No users configured - shouldn't happen but handle gracefully
        log_error("No users configured. Please configure plex_users or managed_users in config.yml")
        sys.exit(1)

    # Process each user individually
    for user in all_users:
        print(f"\n{GREEN}Processing recommendations for user: {user}{RESET}")
        print("-" * 50)

        # Create modified config for this user
        user_config = copy.deepcopy(base_config)

        # Resolve Admin to actual username if needed
        resolved_user = user
        try:
            account = MyPlexAccount(token=base_config['plex']['token'])
            admin_username = account.username
            if user.lower() in ['admin', 'administrator']:
                resolved_user = admin_username
                log_warning(f"Resolved Admin to: {admin_username}")
        except Exception as e:
            log_warning(f"Could not resolve admin username: {e}")

        if 'managed_users' in user_config['plex']:
            user_config['plex']['managed_users'] = resolved_user
        elif 'users' in user_config.get('plex_users', {}):
            user_config['plex_users']['users'] = [resolved_user]

        # Process recommendations for this user
        process_recommendations(user_config, config_path, log_retention_days, single_user=resolved_user)
        print(f"\n{GREEN}Completed processing for user: {resolved_user}{RESET}")
        print("-" * 50)

    runtime = datetime.now() - start_time
    hours = runtime.seconds // 3600
    minutes = (runtime.seconds % 3600) // 60
    seconds = runtime.seconds % 60
    print(f"\n{GREEN}All processing completed!{RESET}")
    print(f"Total runtime: {hours:02d}:{minutes:02d}:{seconds:02d}")

def process_recommendations(config, config_path, log_retention_days, single_user=None):
    original_stdout = sys.stdout
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')

    if log_retention_days > 0:
        try:
            os.makedirs(log_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            user_suffix = f"_{single_user}" if single_user else ""
            log_file_path = os.path.join(log_dir, f"recommendations{user_suffix}_{timestamp}.log")
            lf = open(log_file_path, "w", encoding="utf-8")
            sys.stdout = TeeLogger(lf)
            cleanup_old_logs(log_dir, log_retention_days)
        except Exception as e:
            log_error(f"Could not set up logging: {e}")

    try:
        # Create recommender with single user context
        recommender = PlexTVRecommender(config_path, single_user)
        recommendations = recommender.get_recommendations()

        print(f"\n{GREEN}=== Recommended Unwatched Shows in Your Library ==={RESET}")
        plex_recs = recommendations.get('plex_recommendations', [])
        if plex_recs:
            for i, show in enumerate(plex_recs, start=1):
                print(format_show_output(
                    show,
                    show_summary=recommender.show_summary,
                    index=i,
                    show_cast=recommender.show_cast,
                    show_language=recommender.show_language,
                    show_rating=recommender.show_rating,
                    show_imdb_link=recommender.show_imdb_link
                ))
                print()
        else:
            log_warning(f"No recommendations found in your Plex library matching your criteria.")

        # Always manage labels (to remove old ones even if no new recommendations)
        recommender.manage_plex_labels(plex_recs)

    except Exception as e:
        print(f"\n{RED}An error occurred: {e}{RESET}")
        import traceback
        print(traceback.format_exc())

    finally:
        if log_retention_days > 0 and sys.stdout is not original_stdout:
            try:
                sys.stdout.logfile.close()
                sys.stdout = original_stdout
            except Exception as e:
                log_warning(f"Error closing log file: {e}")


if __name__ == "__main__":
    main()