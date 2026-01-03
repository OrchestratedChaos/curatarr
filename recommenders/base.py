"""
Base classes for Plex Recommender caches and recommenders.
Provides shared functionality for movies and TV shows.
"""

import os
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import time
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Set

from utils import (
    CACHE_VERSION,
    GREEN, YELLOW, CYAN, RESET,
    load_config,
    init_plex,
    get_configured_users,
    get_tmdb_config,
    check_cache_version,
    load_media_cache,
    save_media_cache,
    extract_ids_from_guids,
    get_tmdb_id_for_item,
    get_tmdb_keywords,
    fetch_tmdb_with_retry,
    get_full_language_name,
    log_warning,
    log_error,
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
                        time.sleep(0.5)

                    # Process the item (media-specific logic)
                    item_info = self._process_item(item, tmdb_api_key)

                    if item_info:
                        self.cache[self.media_key][item_id] = item_info

                except Exception as e:
                    log_warning(f"Error processing {self.media_type} {item.title}: {e}")
                    continue

        self.cache['library_count'] = current_count
        self.cache['last_updated'] = datetime.now().isoformat()
        self._save_cache()
        print(f"\n{GREEN}{self.media_key.title()} cache updated{RESET}")
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
            'vote_count': None
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

            # Get rating/vote_count (movies only)
            if self.media_type == 'movie':
                detail_data = fetch_tmdb_with_retry(
                    f"https://api.themoviedb.org/3/movie/{result['tmdb_id']}",
                    {'api_key': tmdb_api_key}
                )
                if detail_data:
                    result['rating'] = detail_data.get('vote_average')
                    result['vote_count'] = detail_data.get('vote_count')

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
        self.cache_dir = os.path.join(os.path.dirname(__file__), 'cache')
        os.makedirs(self.cache_dir, exist_ok=True)

        # Load display options
        self.confirm_operations = general_config.get('confirm_operations', False)
        self.limit_plex_results = general_config.get('limit_plex_results', 10)
        self.combine_watch_history = general_config.get('combine_watch_history', True)
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
        if not abs(total_weight - 1.0) < 1e-6:
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

    @abstractmethod
    def get_recommendations(self, username: str = None) -> List[Dict]:
        """
        Get recommendations for a user.

        Must be implemented by subclasses.

        Args:
            username: Optional username for single-user recommendations

        Returns:
            List of recommendation dicts sorted by similarity score
        """
        pass
