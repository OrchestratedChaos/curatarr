"""
Base classes for Plex Recommender caches and recommenders.
Provides shared functionality for movies and TV shows.
"""

import os
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import re
import time
import logging
import requests
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from plexapi.myplex import MyPlexAccount

from utils import (
    CACHE_VERSION,
    TMDB_RATE_LIMIT_DELAY,
    DEFAULT_LIMIT_PLEX_RESULTS,
    WEIGHT_SUM_TOLERANCE,
    TIER_SAFE_PERCENT, TIER_DIVERSE_PERCENT, TIER_WILDCARD_PERCENT,
    GREEN, YELLOW, CYAN, RESET,
    load_config,
    init_plex,
    get_configured_users,
    get_tmdb_config,
    check_cache_version,
    load_media_cache,
    save_media_cache,
    save_watched_cache,
    extract_ids_from_guids,
    get_tmdb_id_for_item,
    get_tmdb_keywords,
    fetch_tmdb_with_retry,
    get_full_language_name,
    log_warning,
    log_error,
    select_tiered_recommendations,
    get_excluded_genres_for_user,
    show_progress,
    create_empty_counters,
    process_counters_from_cache,
    user_select_recommendations,
    build_label_name,
    categorize_labeled_items,
    remove_labels_from_items,
    add_labels_to_items,
    update_plex_collection,
    cleanup_old_collections,
    get_library_imdb_ids,
)

logger = logging.getLogger('plex_recommender')


class BaseCache(ABC):
    """
    Abstract base class for media caches (movies and TV shows).

    Provides common functionality for loading, saving, and updating caches.
    Subclasses must implement media-specific processing.
    """

    # Subclasses must define these
    media_type: str = None  # 'movie' or 'tv'
    media_key: str = None   # 'movies' or 'shows'
    cache_filename: str = None  # e.g., 'all_movies_cache.json'

    def __init__(self, cache_dir: str, recommender=None):
        """
        Initialize the cache.

        Args:
            cache_dir: Directory path where cache files are stored
            recommender: Reference to parent recommender instance
        """
        self.cache_path = os.path.join(cache_dir, self.cache_filename)
        self.cache = self._load_cache()
        self.recommender = recommender

    def _load_cache(self) -> Dict:
        """Load cache from file."""
        return load_media_cache(self.cache_path, self.media_key)

    def _save_cache(self):
        """Save cache to file."""
        self.cache['cache_version'] = CACHE_VERSION
        save_media_cache(self.cache_path, self.cache, self.media_key)

    def update_cache(self, plex, library_title: str, tmdb_api_key: Optional[str] = None) -> bool:
        """
        Update cache with current library contents and TMDB metadata.

        Args:
            plex: PlexServer instance
            library_title: Name of the library section
            tmdb_api_key: Optional TMDB API key for fetching additional metadata

        Returns:
            bool: True if cache was updated, False if already up to date
        """
        section = plex.library.section(library_title)
        all_items = section.all()
        current_count = len(all_items)

        if current_count == self.cache['library_count']:
            print(f"{GREEN}{self.media_key.title()} cache is up to date{RESET}")
            # Still check for missing collection data (backfill for existing caches)
            if self.media_type == 'movie' and tmdb_api_key:
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
                msg = f"\r{CYAN}Processing {self.media_type} {i}/{len(new_items)} ({int((i/len(new_items))*100)}%){RESET}"
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

                except Exception as e:
                    log_warning(f"Error processing {self.media_type} {item.title}: {e}")
                    continue

        self.cache['library_count'] = current_count
        self.cache['last_updated'] = datetime.now().isoformat()

        # Backfill collection data for movies missing it
        if self.media_type == 'movie' and tmdb_api_key:
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
            (item_id, info) for item_id, info in self.cache[self.media_key].items()
            if info.get('tmdb_id') and 'collection_id' not in info
        ]

        if not movies_needing_collection:
            return False

        total = len(movies_needing_collection)
        print(f"\n{CYAN}Backfilling collection data for {total} movies (one-time migration)...{RESET}")

        updated = 0
        for i, (item_id, info) in enumerate(movies_needing_collection, 1):
            pct = int((i / total) * 100)
            sys.stdout.write(f"\r{CYAN}Processing {i}/{total} ({pct}%) - Found {updated} collections{RESET}")
            sys.stdout.flush()

            try:
                time.sleep(TMDB_RATE_LIMIT_DELAY)
                detail_data = fetch_tmdb_with_retry(
                    f"https://api.themoviedb.org/3/movie/{info['tmdb_id']}",
                    {'api_key': tmdb_api_key}
                )
                if detail_data:
                    collection = detail_data.get('belongs_to_collection')
                    if collection:
                        info['collection_id'] = collection.get('id')
                        info['collection_name'] = collection.get('name')
                        updated += 1
                    else:
                        info['collection_id'] = None
                        info['collection_name'] = None
                else:
                    # API failed (404, etc) - mark as processed to avoid infinite retries
                    info['collection_id'] = None
                    info['collection_name'] = None
            except Exception:
                # Mark as processed even on exception
                info['collection_id'] = None
                info['collection_name'] = None

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
            if self.media_type == 'tv':
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
                        lang_code = (
                            getattr(audio, 'languageTag', None) or
                            getattr(audio, 'language', None)
                        )
                        if lang_code:
                            return get_full_language_name(lang_code)
        except Exception:
            pass
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
        result = {
            'tmdb_id': None,
            'imdb_id': None,
            'keywords': [],
            'rating': None,
            'vote_count': None,
            'collection_id': None,
            'collection_name': None
        }

        # Extract IDs from GUIDs
        ids = extract_ids_from_guids(item)
        result['imdb_id'] = ids['imdb_id']
        result['tmdb_id'] = ids['tmdb_id']

        # Get TMDB ID if not found in GUIDs
        if not result['tmdb_id'] and tmdb_api_key:
            result['tmdb_id'] = get_tmdb_id_for_item(item, tmdb_api_key, self.media_type)

        # Fetch TMDB metadata
        if result['tmdb_id'] and tmdb_api_key:
            # Get keywords
            result['keywords'] = get_tmdb_keywords(tmdb_api_key, result['tmdb_id'], self.media_type)

            # Get rating/vote_count/collection (movies only)
            if self.media_type == 'movie':
                detail_data = fetch_tmdb_with_retry(
                    f"https://api.themoviedb.org/3/movie/{result['tmdb_id']}",
                    {'api_key': tmdb_api_key}
                )
                if detail_data:
                    result['rating'] = detail_data.get('vote_average')
                    result['vote_count'] = detail_data.get('vote_count')
                    # Extract collection info (for sequel bonus)
                    collection = detail_data.get('belongs_to_collection')
                    if collection:
                        result['collection_id'] = collection.get('id')
                        result['collection_name'] = collection.get('name')

        # Update recommender caches if available
        if self.recommender and result['tmdb_id']:
            self.recommender.plex_tmdb_cache[str(item.ratingKey)] = result['tmdb_id']
            if result['keywords']:
                self.recommender.tmdb_keywords_cache[str(result['tmdb_id'])] = result['keywords']

        return result


class BaseRecommender(ABC):
    """
    Abstract base class for media recommenders.

    Provides common functionality for loading config, connecting to Plex,
    managing caches, and generating recommendations.
    """

    # Subclasses must define these
    media_type: str = None  # 'movie' or 'tv'
    library_config_key: str = None  # e.g., 'movie_library_title'
    default_library_name: str = None  # e.g., 'Movies'

    def __init__(self, config_path: str, single_user: str = None):
        """
        Initialize the recommender.

        Args:
            config_path: Path to the config.yml configuration file
            single_user: Optional username for single-user mode
        """
        self.single_user = single_user
        self.config = load_config(config_path)
        self.library_title = self.config['plex'].get(self.library_config_key, self.default_library_name)

        # Initialize counters and caches
        self.cached_watched_count = 0
        self.watched_data_counters = {}
        self.plex_tmdb_cache = {}
        self.tmdb_keywords_cache = {}
        self.label_dates = {}
        self.users = get_configured_users(self.config)

        # Set for tracking watched item IDs
        self.watched_ids: Set[int] = set()

        print("Initializing recommendation system...")
        print("Connecting to Plex server...")
        self.plex = init_plex(self.config)
        print(f"Connected to Plex successfully!\n")

        # Load general config
        general_config = self.config.get('general', {})
        self.debug = general_config.get('debug', False)

        print(f"{YELLOW}Checking Cache...{RESET}")
        tmdb_config = get_tmdb_config(self.config)
        self.use_tmdb_keywords = tmdb_config['use_keywords']
        self.tmdb_api_key = tmdb_config['api_key']

        # Setup cache directory
        self.cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache')
        os.makedirs(self.cache_dir, exist_ok=True)

        # Load display options
        self.confirm_operations = general_config.get('confirm_operations', False)
        self.limit_plex_results = general_config.get('limit_plex_results', DEFAULT_LIMIT_PLEX_RESULTS)
        self.randomize_recommendations = general_config.get('randomize_recommendations', True)
        self.normalize_counters = general_config.get('normalize_counters', True)
        self.show_summary = general_config.get('show_summary', False)
        self.show_genres = general_config.get('show_genres', True)
        self.show_cast = general_config.get('show_cast', False)
        self.show_language = general_config.get('show_language', False)
        self.show_rating = general_config.get('show_rating', False)
        self.show_imdb_link = general_config.get('show_imdb_link', False)

        # Load excluded genres
        exclude_genre_str = general_config.get('exclude_genre', '')
        self.exclude_genres = [
            g.strip().lower() for g in exclude_genre_str.split(',') if g.strip()
        ] if exclude_genre_str else []

        # Load user preferences
        self.user_preferences = self.config.get('users', {}).get('preferences', {})

        # Load weights
        weights_config = self.config.get('weights', {})
        self.weights = self._load_weights(weights_config)

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

    def _get_user_context(self) -> str:
        """
        Get a safe string representing the current user context for cache filenames.

        Returns:
            Sanitized user context string
        """
        if self.single_user:
            user_ctx = f"plex_{self.single_user}"
        elif self.users['plex_users']:
            user_ctx = 'plex_' + '_'.join(self.users['plex_users'])
        else:
            user_ctx = 'plex_' + '_'.join(self.users['managed_users'])

        return re.sub(r'\W+', '', user_ctx)

    def _refresh_watched_data(self):
        """Force refresh of watched data from Plex."""
        self.watched_data_counters = None
        self.watched_ids = set()
        self.watched_data = self._get_watched_data()
        self.watched_data_counters = self.watched_data
        self._save_watched_cache()

    def _get_plex_user_ids(self) -> List[str]:
        """Resolve configured Plex usernames to their user IDs."""
        user_ids = []
        try:
            users_response = requests.get(
                f"{self.config['plex_users']['url']}/api/v2",
                params={
                    'apikey': self.config['plex_users']['api_key'],
                    'cmd': 'get_users'
                },
                timeout=30
            )
            users_response.raise_for_status()
            plex_users = users_response.json()['response']['data']

            users_to_match = [self.single_user] if self.single_user else self.users['plex_users']

            for username in users_to_match:
                user = next(
                    (u for u in plex_users
                     if u['username'].lower() == username.lower()),
                    None
                )
                if user:
                    user_ids.append(str(user['user_id']))
                else:
                    log_error(f"User '{username}' not found in Plex accounts!")

        except Exception as e:
            log_error(f"Error resolving Plex users: {e}")

        return user_ids

    def _get_managed_users_watched_data(self) -> Dict:
        """Get watched data from managed Plex users."""
        if not self.single_user and hasattr(self, 'watched_data_counters') and self.watched_data_counters:
            logger.debug("Using cached watched data (not single user mode)")
            return self.watched_data_counters

        if hasattr(self, 'watched_data_counters') and self.watched_data_counters:
            logger.debug("Using existing watched data counters")
            return self.watched_data_counters

        counters = create_empty_counters(self.media_type)

        account = MyPlexAccount(token=self.config['plex']['token'])
        admin_user = self.users['admin_user']

        if self.single_user:
            if self.single_user.lower() in ['admin', 'administrator']:
                users_to_process = [admin_user]
            else:
                users_to_process = [self.single_user]
        else:
            users_to_process = self.users['managed_users'] or [admin_user]

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
                        process_counters_from_cache(item_info, counters, media_type=self.media_type)

                        if tmdb_id := item_info.get('tmdb_id'):
                            counters['tmdb_ids'].add(tmdb_id)

            except Exception as e:
                log_error(f"Error processing user {username}: {e}")
                continue

        logger.debug(f"Collected {len(counters['tmdb_ids'])} unique TMDB IDs from managed users")

        return counters

    def _load_watched_cache(self) -> Dict:
        """Load watched cache from file. Returns the loaded cache dict."""
        watched_cache = {}
        cache_valid = check_cache_version(self.watched_cache_path, f"{self.media_type.upper()} watched cache")
        if cache_valid and os.path.exists(self.watched_cache_path):
            try:
                with open(self.watched_cache_path, 'r', encoding='utf-8') as f:
                    watched_cache = json.load(f)
                    self.cached_watched_count = watched_cache.get('watched_count', 0)
                    self.watched_data_counters = watched_cache.get('watched_data_counters', {})
                    self.plex_tmdb_cache = {str(k): v for k, v in watched_cache.get('plex_tmdb_cache', {}).items()}
                    self.tmdb_keywords_cache = {str(k): v for k, v in watched_cache.get('tmdb_keywords_cache', {}).items()}
                    self.label_dates = watched_cache.get('label_dates', {})

                    # Load watched IDs (key differs by media type)
                    watched_ids_key = f'watched_{self.media_type}_ids' if self.media_type == 'movie' else 'watched_show_ids'
                    watched_ids_list = watched_cache.get(watched_ids_key, [])
                    if isinstance(watched_ids_list, list):
                        self.watched_ids = {int(id_) for id_ in watched_ids_list if str(id_).isdigit()}
                    else:
                        log_warning(f"Warning: Invalid {watched_ids_key} format in cache")
                        self.watched_ids = set()

                    if not self.watched_ids and self.cached_watched_count > 0:
                        log_error(f"Warning: Cached watched count is {self.cached_watched_count} but no valid IDs loaded")
                        self._refresh_watched_data()

            except Exception as e:
                log_warning(f"Error loading watched cache: {e}")
                self._refresh_watched_data()
        return watched_cache

    def _do_save_watched_cache(self):
        """Save watched cache to file using the utility."""
        save_watched_cache(
            cache_path=self.watched_cache_path,
            watched_data_counters=self.watched_data_counters,
            plex_tmdb_cache=self.plex_tmdb_cache,
            tmdb_keywords_cache=self.tmdb_keywords_cache,
            watched_ids=self.watched_ids,
            label_dates=getattr(self, 'label_dates', {}),
            watched_count=len(self.watched_ids) if self.media_type == 'movie' else self.cached_watched_count,
            media_type=self.media_type
        )

    def get_recommendations(self) -> Dict[str, List[Dict]]:
        """Get recommendations based on watched content."""
        if self.cached_watched_count > 0 and not self.watched_ids:
            self.watched_data = self._get_watched_data()
            self.watched_data_counters = self.watched_data
            self._save_watched_cache()

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

        # Get quality filters from config
        quality_filters = self.config.get('quality_filters', {})
        min_rating = quality_filters.get('min_rating', 0.0)
        min_vote_count = quality_filters.get('min_vote_count', 0)

        for item_id, item_info in all_items.items():
            if int(str(item_id)) in self.watched_ids:
                continue

            if any(g.lower() in excluded_genres for g in item_info.get('genres', [])):
                excluded_count += 1
                continue

            rating = item_info.get('rating') or 0.0
            vote_count = item_info.get('vote_count') or 0

            if rating < min_rating or vote_count < min_vote_count:
                quality_filtered_count += 1
                continue

            unwatched_items.append(item_info)

        if excluded_count > 0:
            print(f"Excluded {excluded_count} {self.media_key} based on genre filters")
        if quality_filtered_count > 0:
            log_warning(f"Filtered {quality_filtered_count} {self.media_key} below quality thresholds (rating: {min_rating}+, votes: {min_vote_count}+)")

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
                    cached_hash = item_info.get('profile_hash')
                    cached_score = item_info.get('cached_score')

                    if cached_hash == self.profile_hash and cached_score is not None:
                        similarity_score = cached_score
                        breakdown = item_info.get('score_breakdown', {})
                        cache_hits += 1
                    else:
                        similarity_score, breakdown = self._calculate_similarity_from_cache(item_info)
                        item_info['cached_score'] = similarity_score
                        item_info['profile_hash'] = self.profile_hash
                        item_info['score_breakdown'] = breakdown
                        scores_updated = True

                    item_info['similarity_score'] = similarity_score
                    scored_items.append(item_info)
                except Exception as e:
                    log_warning(f"Error processing {item_info['title']}: {e}")
                    continue

            if scores_updated:
                media_cache._save_cache()
                logger.debug(f"Saved {len(unwatched_items) - cache_hits} new scores to cache")
            if cache_hits > 0:
                logger.debug(f"Used {cache_hits} cached scores")

            scored_items.sort(key=lambda x: x['similarity_score'], reverse=True)

            if self.randomize_recommendations:
                plex_recs = select_tiered_recommendations(
                    scored_items,
                    self.limit_plex_results,
                    TIER_SAFE_PERCENT,
                    TIER_DIVERSE_PERCENT,
                    TIER_WILDCARD_PERCENT
                )
            else:
                plex_recs = scored_items[:self.limit_plex_results]

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("=== Similarity Score Breakdowns for Recommendations ===")
                for item in plex_recs:
                    self._print_similarity_breakdown(item, item['similarity_score'], item['score_breakdown'])

        print(f"\nRecommendation process completed!")
        return {
            'plex_recommendations': plex_recs
        }

    def manage_plex_labels(self, recommended_items: List[Dict]) -> None:
        """Manage Plex labels and collections for recommendations."""
        if not self.config.get('collections', {}).get('add_label'):
            return

        recommended_items = recommended_items or []

        if self.confirm_operations and recommended_items:
            selected_items = self._user_select_recommendations(recommended_items, "label in Plex")
            if not selected_items:
                selected_items = []
        else:
            selected_items = recommended_items

        try:
            section = self.plex.library.section(self.library_title)
            base_label = self.config.get('collections', {}).get('label_name', 'Recommended')
            append_usernames = self.config.get('collections', {}).get('append_usernames', False)
            users = self.users['plex_users'] or self.users['managed_users']
            label_name = build_label_name(base_label, users, self.single_user, append_usernames)

            # Find items in Plex
            items_to_update = []
            skipped_items = []
            for rec in selected_items:
                plex_item = self._find_plex_item(section, rec)
                if plex_item:
                    plex_item.reload()
                    items_to_update.append(plex_item)
                else:
                    skipped_items.append(f"{rec['title']} ({rec.get('year', 'N/A')})")

            if skipped_items:
                log_warning(f"Skipped {len(skipped_items)} {self.media_key} not found in Plex:")
                for item in skipped_items[:5]:
                    print(f"  - {item}")
                if len(skipped_items) > 5:
                    print(f"  ... and {len(skipped_items) - 5} more")

            print(f"{GREEN}Starting incremental collection update with staleness check...{RESET}")

            if not hasattr(self, 'label_dates') or not self.label_dates:
                self.label_dates = {}

            stale_days = self.config.get('collections', {}).get('stale_removal_days', 7)

            currently_labeled = section.search(label=label_name)
            print(f"Found {len(currently_labeled)} currently labeled {self.media_key}")

            excluded_genres = get_excluded_genres_for_user(self.exclude_genres, self.user_preferences, self.single_user)

            categories = categorize_labeled_items(
                currently_labeled, self.watched_ids, excluded_genres,
                label_name, self.label_dates, stale_days
            )
            unwatched_labeled = categories['fresh']
            watched_labeled = categories['watched']
            stale_labeled = categories['stale']
            excluded_labeled = categories['excluded']

            print(f"{GREEN}Keeping {len(unwatched_labeled)} fresh unwatched recommendations{RESET}")
            print(f"{YELLOW}Removing {len(watched_labeled)} watched {self.media_key} from recommendations{RESET}")
            print(f"{YELLOW}Removing {len(stale_labeled)} stale recommendations (unwatched > {stale_days} days){RESET}")
            print(f"{YELLOW}Removing {len(excluded_labeled)} {self.media_key} with excluded genres{RESET}")

            remove_labels_from_items(watched_labeled, label_name, self.label_dates, "watched")
            remove_labels_from_items(stale_labeled, label_name, self.label_dates, "stale")
            remove_labels_from_items(excluded_labeled, label_name, self.label_dates, "excluded genre")

            target_count = self.config['general'].get('limit_plex_results', 50 if self.media_type == 'movie' else 20)

            print(f"{GREEN}Building optimal collection of top {target_count} recommendations...{RESET}")

            all_candidates = {}
            media_cache = self._get_media_cache()

            for item in unwatched_labeled:
                item_id = int(item.ratingKey)
                item_info = media_cache.cache[self.media_key].get(str(item_id))
                if item_info:
                    try:
                        score, _ = self._calculate_similarity_from_cache(item_info)
                        all_candidates[item_id] = (item, score)
                    except Exception:
                        all_candidates[item_id] = (item, 0.0)

            for rec in selected_items:
                plex_item = next(
                    (m for m in items_to_update if m.title == rec['title'] and m.year == rec.get('year')),
                    None
                )
                if plex_item:
                    item_id = int(plex_item.ratingKey)
                    is_watched = item_id in self.watched_ids or getattr(plex_item, 'isPlayed', False)
                    if not is_watched:
                        score = rec.get('similarity_score', 0.0)
                        if item_id not in all_candidates or score > all_candidates[item_id][1]:
                            all_candidates[item_id] = (plex_item, score)

            sorted_candidates = sorted(all_candidates.items(), key=lambda x: x[1][1], reverse=True)
            top_candidates = sorted_candidates[:target_count]
            top_ids = {item_id for item_id, _ in top_candidates}

            current_ids = {int(m.ratingKey) for m in unwatched_labeled}
            ids_to_add = top_ids - current_ids
            ids_to_remove = current_ids - top_ids

            if ids_to_remove:
                items_to_remove = [m for m in unwatched_labeled if int(m.ratingKey) in ids_to_remove]
                print(f"{YELLOW}Removing {len(items_to_remove)} lower-scoring items to make room for better ones{RESET}")
                remove_labels_from_items(items_to_remove, label_name, self.label_dates, "replaced by higher score")

            items_to_add = [all_candidates[mid][0] for mid in ids_to_add if mid in all_candidates]
            if items_to_add:
                print(f"{GREEN}Adding {len(items_to_add)} new high-scoring recommendations{RESET}")
                add_labels_to_items(items_to_add, label_name, self.label_dates)

            self._save_watched_cache()

            print(f"{GREEN}Collection now has top {len(top_candidates)} recommendations by score{RESET}")

            final_collection_items = [plex_item for item_id, (plex_item, score) in top_candidates]

            print(f"{GREEN}Final collection size: {len(final_collection_items)} {self.media_key} (sorted by similarity){RESET}")
            print(f"{GREEN}Successfully updated labels incrementally{RESET}")

            if final_collection_items:
                username = label_name.replace('Recommended_', '')
                if username in self.user_preferences and 'display_name' in self.user_preferences[username]:
                    display_name = self.user_preferences[username]['display_name']
                else:
                    display_name = username.capitalize()

                emoji = "🎬" if self.media_type == 'movie' else "📺"
                collection_name = f"{emoji} {display_name} - Recommendation"
                update_plex_collection(section, collection_name, final_collection_items, logger)
                cleanup_old_collections(section, collection_name, username, emoji, logger)

        except Exception as e:
            log_error(f"Error managing Plex labels: {e}")
            import traceback
            print(traceback.format_exc())

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
        if ids['imdb_id']:
            return ids['imdb_id']

        # Fallback: Check legacy guid attribute
        if hasattr(plex_item, 'guid') and plex_item.guid and plex_item.guid.startswith('imdb://'):
            return plex_item.guid.split('imdb://')[1]

        # Fallback to TMDB to get IMDb ID
        tmdb_id = self._get_plex_item_tmdb_id(plex_item)
        if tmdb_id:
            if self.media_type == 'movie':
                data = fetch_tmdb_with_retry(
                    f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                    {'api_key': self.tmdb_api_key}
                )
                return data.get('imdb_id') if data else None
            else:
                # TV shows need the external_ids endpoint
                data = fetch_tmdb_with_retry(
                    f"https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids",
                    {'api_key': self.tmdb_api_key}
                )
                return data.get('imdb_id') if data else None
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
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            {'api_key': self.tmdb_api_key, 'external_source': 'imdb_id'}
        )
        if data:
            results_key = 'movie_results' if self.media_type == 'movie' else 'tv_results'
            results = data.get(results_key, [])
            if results:
                return results[0].get('id')
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

    def _get_library_imdb_ids(self) -> Set[str]:
        """Get set of all IMDb IDs in the library."""
        return get_library_imdb_ids(self.plex.library.section(self.library_title))

    @abstractmethod
    def _find_plex_item(self, section, rec: Dict):
        """Find a Plex item matching the recommendation."""
        pass

    @abstractmethod
    def _calculate_similarity_from_cache(self, item_info: Dict) -> Tuple[float, Dict]:
        """Calculate similarity score for an item."""
        pass

    @abstractmethod
    def _print_similarity_breakdown(self, item_info: Dict, score: float, breakdown: Dict):
        """Print detailed breakdown of similarity score."""
        pass

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
