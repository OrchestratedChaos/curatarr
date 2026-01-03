import os
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging
from plexapi.myplex import MyPlexAccount
import yaml
import requests
from typing import Dict, List, Set, Optional, Tuple
import time
import random
import json
import re
from datetime import datetime
import copy

# Import shared utilities
from utils import (
    RED, GREEN, YELLOW, CYAN, RESET,
    RATING_MULTIPLIERS, CACHE_VERSION, check_cache_version,
    TOP_CAST_COUNT, TMDB_RATE_LIMIT_DELAY, DEFAULT_RATING,
    WEIGHT_SUM_TOLERANCE, DEFAULT_LIMIT_PLEX_RESULTS, TOP_POOL_PERCENTAGE,
    get_full_language_name, cleanup_old_logs, setup_logging, get_tmdb_config,
    get_plex_account_ids, get_watched_show_count,
    fetch_plex_watch_history_shows,
    log_warning, log_error, update_plex_collection, cleanup_old_collections,
    load_config, init_plex, get_configured_users,
    get_excluded_genres_for_user,
    calculate_recency_multiplier, calculate_rewatch_multiplier,
    calculate_similarity_score,
    show_progress, TeeLogger,
    # Consolidated utilities
    extract_genres, extract_ids_from_guids, fetch_tmdb_with_retry,
    get_tmdb_id_for_item, get_tmdb_keywords, adapt_config_for_media_type,
    # Additional consolidated utilities
    user_select_recommendations, format_media_output,
    build_label_name, categorize_labeled_items, remove_labels_from_items, add_labels_to_items,
    get_library_imdb_ids, print_similarity_breakdown,
    load_media_cache, save_media_cache, create_empty_counters,
    save_watched_cache
)

# Module-level logger - configured by setup_logging() in main()
logger = logging.getLogger('plex_recommender')

__version__ = "1.2.3"

# Import base class
from recommenders.base import BaseCache


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

class PlexTVRecommender:
    """Generates personalized TV show recommendations based on Plex watch history.

    Analyzes watched shows to build preference profiles based on genres, studios,
    actors, languages, and TMDB keywords. Uses similarity scoring to rank unwatched
    shows in your Plex library.
    """

    def __init__(self, config_path: str, single_user: str = None):
        """Initialize the TV show recommender.

        Args:
            config_path: Path to the config.yml configuration file
            single_user: Optional username to generate recommendations for a single user
        """
        self.single_user = single_user
        self.config = load_config(config_path)
        self.library_title = self.config['plex'].get('TV_library_title', 'TV Shows')
        
        # Initialize counters and caches
        self.cached_watched_count = 0
        self.cached_unwatched_count = 0
        self.cached_library_show_count = 0
        self.watched_data_counters = {}
        self.synced_show_ids = set()
        self.cached_unwatched_shows = []
        self.plex_tmdb_cache = {}
        self.tmdb_keywords_cache = {}
        self.plex_watched_rating_keys = set()
        self.watched_show_ids = set()
        self.users = get_configured_users(self.config)

        print("Initializing recommendation system...")
        print("Connecting to Plex server...")
        self.plex = init_plex(self.config)
        print(f"Connected to Plex successfully!\n")
        general_config = self.config.get('general', {})
        self.debug = general_config.get('debug', False)
        print(f"{YELLOW}Checking Cache...{RESET}")
        tmdb_config = get_tmdb_config(self.config)
        self.use_tmdb_keywords = tmdb_config['use_keywords']
        self.tmdb_api_key = tmdb_config['api_key']
		
        self.cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache')
        os.makedirs(self.cache_dir, exist_ok=True)
        self.show_cache = ShowCache(self.cache_dir, recommender=self)
        self.show_cache.update_cache(self.plex, self.library_title, self.tmdb_api_key)

        self.confirm_operations = general_config.get('confirm_operations', False)
        self.limit_plex_results = general_config.get('limit_plex_results', DEFAULT_LIMIT_PLEX_RESULTS)
        self.combine_watch_history = general_config.get('combine_watch_history', True)
        self.randomize_recommendations = general_config.get('randomize_recommendations', True)
        self.normalize_counters = general_config.get('normalize_counters', True)
        self.show_summary = general_config.get('show_summary', False)
        self.plex_only = general_config.get('plex_only', False)
        self.show_cast = general_config.get('show_cast', False)
        self.show_language = general_config.get('show_language', False)
        self.show_rating = general_config.get('show_rating', False)
        self.show_imdb_link = general_config.get('show_imdb_link', False)
        
        exclude_genre_str = general_config.get('exclude_genre', '')
        self.exclude_genres = [g.strip().lower() for g in exclude_genre_str.split(',') if g.strip()] if exclude_genre_str else []

        weights_config = self.config.get('weights', {})
        self.weights = {
            'genre': float(weights_config.get('genre', 0.20)),
            'studio': float(weights_config.get('studio', 0.15)),
            'actor': float(weights_config.get('actor', 0.15)),
            'language': float(weights_config.get('language', 0.05)),
            'keyword': float(weights_config.get('keyword', 0.45))
        }

        total_weight = sum(self.weights.values())
        if not abs(total_weight - 1.0) < WEIGHT_SUM_TOLERANCE:
            log_warning(f"Warning: Weights sum to {total_weight}, expected 1.0.")

        # Verify Plex user configuration
        if self.users['plex_users']:
            # Plex-only mode: No external validation needed
            users_to_process = [self.single_user] if self.single_user else self.users['plex_users']
            print(f"{GREEN}Processing recommendations for Plex users: {users_to_process}{RESET}")

        # Verify library exists
        if not self.plex.library.section(self.library_title):
            raise ValueError(f"TV Show library '{self.library_title}' not found in Plex")

        # Load user preferences for per-user customization
        self.user_preferences = self.config.get('users', {}).get('preferences', {})

        # Get user context for cache files
        if single_user:
            user_ctx = f"plex_{single_user}" if not self.users['plex_users'] else f"plex_{single_user}"
        else:
            if self.users['plex_users']:
                user_ctx = 'plex_' + '_'.join(self.users['plex_users'])
            else:
                user_ctx = 'plex_' + '_'.join(self.users['managed_users'])
        
        safe_ctx = re.sub(r'\W+', '', user_ctx)
        
        # Update cache paths to be user-specific
        self.watched_cache_path = os.path.join(self.cache_dir, f"tv_watched_cache_{safe_ctx}.json")

        # Initialize label_dates before cache loading
        self.label_dates = {}
        watched_cache = {}

        # Check cache version first
        cache_valid = check_cache_version(self.watched_cache_path, "TV watched cache")
        if cache_valid and os.path.exists(self.watched_cache_path):
            try:
                with open(self.watched_cache_path, 'r', encoding='utf-8') as f:
                    watched_cache = json.load(f)
                    self.cached_watched_count = watched_cache.get('watched_count', 0)
                    self.watched_data_counters = watched_cache.get('watched_data_counters', {})
                    self.plex_tmdb_cache = {str(k): v for k, v in watched_cache.get('plex_tmdb_cache', {}).items()}
                    self.tmdb_keywords_cache = {str(k): v for k, v in watched_cache.get('tmdb_keywords_cache', {}).items()}
                    self.label_dates = watched_cache.get('label_dates', {})

                    # Load watched show IDs
                    watched_ids = watched_cache.get('watched_show_ids', [])
                    if isinstance(watched_ids, list):
                        self.watched_show_ids = {int(id_) for id_ in watched_ids if str(id_).isdigit()}
                    else:
                        log_warning(f"Warning: Invalid watched_show_ids format in cache")
                        self.watched_show_ids = set()
                    
                    if not self.watched_show_ids and self.cached_watched_count > 0:
                        log_error(f"Warning: Cached watched count is {self.cached_watched_count} but no valid IDs loaded")
                        # Force a refresh of watched data
                        self._refresh_watched_data()
                    
            except Exception as e:
                log_warning(f"Error loading watched cache: {e}")
                self._refresh_watched_data()

        # Get library rating keys for filtering (must be ints to match watched_show_ids)
        shows_section = self.plex.library.section(self.library_title)
        current_library_rating_keys = {int(show.ratingKey) for show in shows_section.all()}

        # Clean up both watched show tracking mechanisms
        self.plex_watched_rating_keys = {
            rk for rk in self.plex_watched_rating_keys
            if int(rk) in current_library_rating_keys
        }
        self.watched_show_ids = {
            show_id for show_id in self.watched_show_ids
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
            self.watched_show_ids = set()
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
            # Ensure watched_show_ids are preserved
            if not self.watched_show_ids and 'watched_show_ids' in watched_cache:
                self.watched_show_ids = {int(id_) for id_ in watched_cache['watched_show_ids'] if str(id_).isdigit()}
            logger.debug(f"Using cached data: {self.cached_watched_count} watched shows, {len(self.watched_show_ids)} IDs")
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

    def _get_plex_user_ids(self):
        """Resolve configured Plex usernames to their user IDs"""
        user_ids = []
        try:
            # Get all Plex users
            users_response = requests.get(
                f"{self.config['plex_users']['url']}/api/v2",
                params={
                    'apikey': self.config['plex_users']['api_key'],
                    'cmd': 'get_users'
                }
            )
            users_response.raise_for_status()
            plex_users = users_response.json()['response']['data']
    
            # Determine which users to process based on single_user mode
            users_to_match = [self.single_user] if self.single_user else self.users['plex_users']
    
            # Match configured usernames to user IDs
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
		
    def _get_plex_watched_shows_data(self) -> Dict:
        """Get watched show data from Plex's native history (using Plex API)"""
        if not self.single_user and hasattr(self, 'watched_data_counters') and self.watched_data_counters:
            return self.watched_data_counters

        shows_section = self.plex.library.section(self.library_title)
        counters = create_empty_counters('tv')
        watched_show_ids = set()
        not_found_count = 0

        log_warning(f"Querying Plex watch history directly...")
        account_ids = self._get_plex_account_ids()
        if not account_ids:
            log_error(f"No valid users found!")
            return counters

        # Use shared utility to fetch watch history
        watched_show_ids = fetch_plex_watch_history_shows(self.config, account_ids)

        # Store watched show IDs
        self.watched_show_ids.update(watched_show_ids)

        # Build view count map for rewatch weighting
        show_view_counts = {}
        try:
            for show in shows_section.all():
                show_id = int(show.ratingKey)
                if show_id in watched_show_ids and hasattr(show, 'viewCount') and show.viewCount:
                    show_view_counts[show_id] = int(show.viewCount)
        except Exception:
            pass  # Fall back to no rewatch weighting if this fails

        # Process show metadata from cache
        print(f"")
        print(f"Processing {len(watched_show_ids)} unique watched shows from Plex history:")
        for i, show_id in enumerate(watched_show_ids, 1):
            show_progress("Processing", i, len(watched_show_ids))

            show_info = self.show_cache.cache['shows'].get(str(show_id))
            if show_info:
                # Calculate rewatch multiplier based on view count
                rewatch_multiplier = calculate_rewatch_multiplier(show_view_counts.get(show_id, 1))
                self._process_show_counters_from_cache(show_info, counters, rewatch_multiplier)

                if tmdb_id := show_info.get('tmdb_id'):
                    counters['tmdb_ids'].add(tmdb_id)
            else:
                not_found_count += 1

        logger.debug(f"Watched shows not in cache: {not_found_count}, TMDB IDs collected: {len(counters['tmdb_ids'])}")

        return counters

    def _get_managed_users_watched_data(self):
        # Return cached data if available and we're not in single user mode
        if not self.single_user and hasattr(self, 'watched_data_counters') and self.watched_data_counters:
            logger.debug("Using cached watched data (not single user mode)")
            return self.watched_data_counters

        # Only proceed with scanning if we need to
        if hasattr(self, 'watched_data_counters') and self.watched_data_counters:
            logger.debug("Using existing watched data counters")
            return self.watched_data_counters
    
        counters = create_empty_counters('tv')

        account = MyPlexAccount(token=self.config['plex']['token'])
        admin_user = self.users['admin_user']
        
        # Determine which users to process
        if self.single_user:
            # Check if the single user is the admin
            if self.single_user.lower() in ['admin', 'administrator']:
                users_to_process = [admin_user]
            else:
                users_to_process = [self.single_user]
        else:
            users_to_process = self.users['managed_users'] or [admin_user]
        
        for username in users_to_process:
            try:
                # Check if current user is admin (using case-insensitive comparison)
                if username.lower() == admin_user.lower():
                    user_plex = self.plex
                else:
                    user = account.user(username)
                    user_plex = self.plex.switchUser(user)
                
                watched_shows = user_plex.library.section(self.library_title).search(unwatched=False)
                
                print(f"\nScanning watched shows for {username}")
                for i, show in enumerate(watched_shows, 1):
                    show_progress(f"Processing {username}'s watched", i, len(watched_shows))
                    self.watched_show_ids.add(int(show.ratingKey))
                    
                    show_info = self.show_cache.cache['shows'].get(str(show.ratingKey))
                    if show_info:
                        self._process_show_counters_from_cache(show_info, counters)
                        
                        # Explicitly add TMDB ID to the set if available
                        if tmdb_id := show_info.get('tmdb_id'):
                            counters['tmdb_ids'].add(tmdb_id)
                    
            except Exception as e:
                log_error(f"Error processing user {username}: {e}")
                continue
        
        logger.debug(f"Collected {len(counters['tmdb_ids'])} unique TMDB IDs from managed users")

        return counters

    # ------------------------------------------------------------------------
    # CACHING LOGIC
    # ------------------------------------------------------------------------
    def _save_watched_cache(self):
        """Save watched show cache using utility"""
        save_watched_cache(
            cache_path=self.watched_cache_path,
            watched_data_counters=self.watched_data_counters,
            plex_tmdb_cache=self.plex_tmdb_cache,
            tmdb_keywords_cache=self.tmdb_keywords_cache,
            watched_ids=self.watched_show_ids,
            label_dates=getattr(self, 'label_dates', {}),
            watched_count=self.cached_watched_count,
            media_type='tv'
        )

    def _save_cache(self):
        self._save_watched_cache()

    def _process_show_counters_from_cache(self, show_info: Dict, counters: Dict, rewatch_multiplier: float = 1.0) -> None:
        try:
            rating = float(show_info.get('user_rating', 0))
            if not rating:
                rating = float(show_info.get('audience_rating', DEFAULT_RATING))
            rating = max(0, min(10, int(round(rating))))
            multiplier = RATING_MULTIPLIERS.get(rating, 1.0) * rewatch_multiplier
    
            # Process all counters using cached data
            for genre in show_info.get('genres', []):
                counters['genres'][genre] += multiplier
            
            if studio := show_info.get('studio'):
                counters['studio'][studio.lower()] += multiplier
                
            for actor in show_info.get('cast', [])[:TOP_CAST_COUNT]:
                counters['actors'][actor] += multiplier

            if language := show_info.get('language'):
                counters['languages'][language.lower()] += multiplier
                
            # Store TMDB data in caches if available
            if tmdb_id := show_info.get('tmdb_id'):
                # Using the show_id from the cache key instead of ratingKey
                show_id = next((k for k, v in self.show_cache.cache['shows'].items() 
                              if v.get('title') == show_info['title'] and 
                              v.get('year') == show_info.get('year')), None)
                if show_id:
                    self.plex_tmdb_cache[str(show_id)] = tmdb_id
                    if keywords := show_info.get('tmdb_keywords', []):
                        self.tmdb_keywords_cache[str(tmdb_id)] = keywords
                        counters['tmdb_keywords'].update({k: multiplier for k in keywords})
    
        except Exception as e:
            log_warning(f"Error processing counters for {show_info.get('title')}: {e}")

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

    def _is_show_in_library(self, title: str, year: Optional[int]) -> bool:
        if not title:
            return False
            
        title_lower = title.lower()
        
        # Check for year in title and strip it if found
        year_match = re.search(r'\s*\((\d{4})\)$', title_lower)
        if year_match:
            clean_title = title_lower.replace(year_match.group(0), '').strip()
            embedded_year = int(year_match.group(1))
            if (clean_title, embedded_year) in self.library_shows:
                return True
        
        # Check both with and without year
        if (title_lower, year) in self.library_shows:
            return True
            
        # Check title-only matches
        return any(lib_title == title_lower or 
                  lib_title == f"{title_lower} ({year})" or
                  lib_title.replace(f" ({year})", "") == title_lower 
                  for lib_title, _ in self.library_shows)

    def _process_show_counters(self, show, counters):
        show_details = self.get_show_details(show)
        
        try:
            rating = float(getattr(show, 'userRating', 0))
        except (TypeError, ValueError):
            try:
                rating = float(getattr(show, 'audienceRating', DEFAULT_RATING))
            except (TypeError, ValueError):
                rating = DEFAULT_RATING
    
        rating = max(0, min(10, int(round(rating))))
        multiplier = RATING_MULTIPLIERS.get(rating, 1.0)
    
        # Process all the existing counters...
        for genre in show_details.get('genres', []):
            counters['genres'][genre] += multiplier
        
        if hasattr(show, 'studio') and show.studio:
            counters['studio'][show.studio.lower()] += multiplier
            
        for actor in show_details.get('cast', [])[:TOP_CAST_COUNT]:
            counters['actors'][actor] += multiplier

        if language := show_details.get('language'):
            counters['languages'][language] += multiplier
            
        for keyword in show_details.get('tmdb_keywords', []):
            counters['tmdb_keywords'][keyword] += multiplier
    
        # Get TVDB IDs and watch dates for all watched episodes
        try:
            watched_episodes = [ep for ep in show.episodes() if ep.isWatched]
            if watched_episodes:
                
                for episode in watched_episodes:
                    episode.reload()
                    
                    # Get watched date and TVDB ID
                    if hasattr(episode, 'lastViewedAt') and hasattr(episode, 'guids'):
                        # Handle lastViewedAt whether it's a timestamp or datetime
                        if isinstance(episode.lastViewedAt, datetime):
                            watched_at = episode.lastViewedAt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                        else:
                            watched_at = datetime.fromtimestamp(int(episode.lastViewedAt)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                        
                        for guid in episode.guids:
                            if 'tvdb://' in guid.id:
                                try:
                                    episode_tvdb_id = int(guid.id.split('tvdb://')[1].split('?')[0])
                                    if 'tvdb_ids' not in counters:
                                        counters['tvdb_ids'] = set()
                                    if 'watch_dates' not in counters:
                                        counters['watch_dates'] = {}
                                    counters['tvdb_ids'].add(episode_tvdb_id)
                                    counters['watch_dates'][episode_tvdb_id] = watched_at
                                    break
                                except (ValueError, IndexError) as e:
                                    continue
        except Exception as e:
            log_warning(f"Error getting episode TVDB IDs for {show.title}: {e}")

    def _get_library_imdb_ids(self) -> Set[str]:
        """Get set of all IMDb IDs in the library"""
        return get_library_imdb_ids(self.plex.library.section(self.library_title))

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
                tmdb_id = self._get_plex_show_tmdb_id(show)
                if tmdb_id:
                    tmdb_keywords = list(self._get_tmdb_keywords_for_id(tmdb_id))
            
            show_info = {
                'title': show.title,
                'year': getattr(show, 'year', None),
                'genres': self._extract_genres(show),
                'summary': getattr(show, 'summary', ''),
                'studio': getattr(show, 'studio', 'N/A'),
                'language': self._get_show_language(show),
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
		
    def _validate_watched_shows(self):
        cleaned_ids = set()
        for show_id in self.watched_show_ids:
            try:
                cleaned_ids.add(int(str(show_id)))
            except (ValueError, TypeError):
                log_warning(f"Invalid watched show ID found: {show_id}")
        self.watched_show_ids = cleaned_ids

    def _refresh_watched_data(self):
        """Force refresh of watched data"""
        # Clear existing data to force actual refresh (prevents early returns in fetch functions)
        self.watched_data_counters = None
        self.watched_show_ids = set()

        if self.users['plex_users']:
            self.watched_data = self._get_plex_watched_shows_data()
        else:
            self.watched_data = self._get_managed_users_watched_data()
        self.watched_data_counters = self.watched_data
        self._save_watched_cache()
    # ------------------------------------------------------------------------
    # TMDB HELPER METHODS
    # ------------------------------------------------------------------------
    def _get_tmdb_id_via_imdb(self, plex_show) -> Optional[int]:
        imdb_id = self._get_plex_show_imdb_id(plex_show)
        if not imdb_id or not self.tmdb_api_key:
            return None

        data = fetch_tmdb_with_retry(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            {'api_key': self.tmdb_api_key, 'external_source': 'imdb_id'}
        )
        if data:
            results = data.get('tv_results', [])
            if results:
                return results[0].get('id')
        return None

    def _get_plex_show_tmdb_id(self, plex_show) -> Optional[int]:
        """Get TMDB ID for a Plex show with multiple fallback methods"""
        # Check cache first
        cache_key = str(plex_show.ratingKey)
        if cache_key in self.plex_tmdb_cache:
            return self.plex_tmdb_cache[cache_key]

        # Use consolidated utility for TMDB ID lookup
        tmdb_id = get_tmdb_id_for_item(plex_show, self.tmdb_api_key, 'tv', self.plex_tmdb_cache)

        # Update cache if found
        if tmdb_id:
            self.plex_tmdb_cache[cache_key] = tmdb_id
            self._save_watched_cache()
        return tmdb_id

    def _get_plex_show_imdb_id(self, plex_show) -> Optional[str]:
        """Get IMDb ID for a Plex show with fallback to TMDB"""
        # Try extracting from GUIDs first using utility
        ids = extract_ids_from_guids(plex_show)
        if ids['imdb_id']:
            return ids['imdb_id']

        # Fallback: Check legacy guid attribute
        if hasattr(plex_show, 'guid') and plex_show.guid and plex_show.guid.startswith('imdb://'):
            return plex_show.guid.split('imdb://')[1]

        # Fallback to TMDB to get IMDb ID
        tmdb_id = self._get_plex_show_tmdb_id(plex_show)
        if tmdb_id:
            # For TV shows, need to get external_ids endpoint
            data = fetch_tmdb_with_retry(
                f"https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids",
                {'api_key': self.tmdb_api_key}
            )
            if data:
                return data.get('imdb_id')
        return None

    def _get_tmdb_keywords_for_id(self, tmdb_id: int) -> Set[str]:
        """Get keywords for a TV show from TMDB"""
        if not tmdb_id or not self.use_tmdb_keywords or not self.tmdb_api_key:
            return set()

        # Use consolidated utility with local cache
        keywords = get_tmdb_keywords(self.tmdb_api_key, tmdb_id, 'tv', self.tmdb_keywords_cache)
        if keywords:
            self._save_watched_cache()
        return set(keywords)

    def _get_show_language(self, show) -> str:
        """Get show's primary audio language - delegates to ShowCache"""
        return self.show_cache._get_show_language(show)

    def _extract_genres(self, show) -> List[str]:
        """Extract genres from a TV show"""
        return extract_genres(show)

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
            'keywords': show_info.get('tmdb_keywords', [])
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
    # ------------------------------------------------------------------------
    # GET RECOMMENDATIONS
    # ------------------------------------------------------------------------
    def get_recommendations(self) -> List[Dict]:
        if self.cached_watched_count > 0 and not self.watched_show_ids:
            # Force refresh of watched data
            if self.users['plex_users']:
                self.watched_data = self._get_plex_watched_shows_data()
            else:
                self.watched_data = self._get_managed_users_watched_data()
            self.watched_data_counters = self.watched_data
            self._save_watched_cache()

        # Get all shows from cache
        all_shows = self.show_cache.cache['shows']
        
        print(f"\n{YELLOW}Processing recommendations...{RESET}")
        
        # Filter out watched shows and excluded genres
        unwatched_shows = []
        excluded_count = 0
        quality_filtered_count = 0

        # Get user-specific excluded genres
        excluded_genres = get_excluded_genres_for_user(self.exclude_genres, self.user_preferences, self.single_user)

        # Get quality filters from config (Netflix-style)
        quality_filters = self.config.get('quality_filters', {})
        min_rating = quality_filters.get('min_rating', 0.0)
        min_vote_count = quality_filters.get('min_vote_count', 0)

        for show_id, show_info in all_shows.items():
            # Skip if show is watched
            if int(str(show_id)) in self.watched_show_ids:
                continue

            # Skip if show has excluded genres (case-insensitive)
            if any(g.lower() in excluded_genres for g in show_info.get('genres', [])):
                excluded_count += 1
                continue

            # Netflix-style quality filters (no year restriction - recency bias via watch dates)
            rating = show_info.get('rating') or 0.0
            vote_count = show_info.get('vote_count') or 0

            # Skip if show doesn't meet quality thresholds
            if rating < min_rating or vote_count < min_vote_count:
                quality_filtered_count += 1
                continue

            unwatched_shows.append(show_info)

        if excluded_count > 0:
            print(f"Excluded {excluded_count} shows based on genre filters")
        if quality_filtered_count > 0:
            log_warning(f"Filtered {quality_filtered_count} shows below quality thresholds (rating: {min_rating}+, votes: {min_vote_count}+)")
    
        if not unwatched_shows:
            log_warning(f"No unwatched shows found matching your criteria.")
            plex_recs = []
        else:
            print(f"Calculating similarity scores for {len(unwatched_shows)} shows...")
            
            # Calculate similarity scores
            scored_shows = []
            for i, show_info in enumerate(unwatched_shows, 1):
                show_progress("Processing", i, len(unwatched_shows))
                try:
                    similarity_score, breakdown = self._calculate_similarity_from_cache(show_info)
                    show_info['similarity_score'] = similarity_score
                    show_info['score_breakdown'] = breakdown
                    scored_shows.append(show_info)
                except Exception as e:
                    log_warning(f"Error processing {show_info['title']}: {e}")
                    continue
            
            # Sort by similarity score
            scored_shows.sort(key=lambda x: x['similarity_score'], reverse=True)
            
            if self.randomize_recommendations:
                # Take top 10% of shows by similarity score and randomize
                top_count = max(int(len(scored_shows) * TOP_POOL_PERCENTAGE), self.limit_plex_results)
                top_pool = scored_shows[:top_count]
                plex_recs = random.sample(top_pool, min(self.limit_plex_results, len(top_pool)))
            else:
                # Take top shows directly by similarity score
                plex_recs = scored_shows[:self.limit_plex_results]
            
            # Print detailed breakdowns for final recommendations if debug is enabled
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("=== Similarity Score Breakdowns for Recommendations ===")
                for show in plex_recs:
                    self._print_similarity_breakdown(show, show['similarity_score'], show['score_breakdown'])

        print(f"\nRecommendation process completed!")
        return plex_recs
    
    def _user_select_recommendations(self, recommended_shows: List[Dict], operation_label: str) -> List[Dict]:
        """Prompt user to select recommendations - delegates to utility"""
        return user_select_recommendations(recommended_shows, operation_label)

    def manage_plex_labels(self, recommended_shows: List[Dict]) -> None:
        # Check if label management is enabled
        if not self.config.get('collections', {}).get('add_label'):
            return

        # If there are recommendations and confirmation is required, let user select
        if recommended_shows and self.confirm_operations:
            selected_shows = self._user_select_recommendations(recommended_shows, "label in Plex")
            if not selected_shows:
                return
        else:
            # Use all recommendations (or empty list if none)
            selected_shows = recommended_shows

        try:
            shows_section = self.plex.library.section(self.library_title)
            base_label = self.config.get('collections', {}).get('label_name', 'Recommended')
            append_usernames = self.config.get('collections', {}).get('append_usernames', False)
            users = self.users['plex_users'] or self.users['managed_users']
            label_name = build_label_name(base_label, users, self.single_user, append_usernames)

            shows_to_update = []
            for rec in selected_shows:
                plex_show = next(
                    (s for s in shows_section.search(title=rec['title'])
                     if s.year == rec.get('year')),
                    None
                )
                if plex_show:
                    plex_show.reload()
                    shows_to_update.append(plex_show)

            # If no new recommendations, we're done
            if not shows_to_update:
                log_warning(f"No new recommendations to add labels to.")
                return

            # INCREMENTAL UPDATE: Keep unwatched (and fresh), remove watched and stale, fill gaps
            print(f"{GREEN}Starting incremental collection update with staleness check...{RESET}")

            # Ensure label_dates exists (should be initialized in __init__)
            if not hasattr(self, 'label_dates'):
                self.label_dates = {}

            # Get staleness threshold from config
            stale_days = self.config.get('plex', {}).get('stale_removal_days', 7)

            # Get currently labeled shows
            currently_labeled = shows_section.search(label=label_name)
            print(f"Found {len(currently_labeled)} currently labeled shows")

            # Get excluded genres for this user
            excluded_genres = get_excluded_genres_for_user(self.exclude_genres, self.user_preferences, self.single_user)

            # Categorize labeled items using utility
            categories = categorize_labeled_items(
                currently_labeled, self.watched_show_ids, excluded_genres,
                label_name, self.label_dates, stale_days
            )
            unwatched_labeled = categories['fresh']
            watched_labeled = categories['watched']
            stale_labeled = categories['stale']
            excluded_labeled = categories['excluded']

            print(f"{GREEN}Keeping {len(unwatched_labeled)} fresh unwatched recommendations{RESET}")
            print(f"{YELLOW}Removing {len(watched_labeled)} watched shows from recommendations{RESET}")
            print(f"{YELLOW}Removing {len(stale_labeled)} stale recommendations (unwatched > {stale_days} days){RESET}")
            print(f"{YELLOW}Removing {len(excluded_labeled)} shows with excluded genres{RESET}")

            # Remove labels using utilities
            remove_labels_from_items(watched_labeled, label_name, self.label_dates, "watched")
            remove_labels_from_items(stale_labeled, label_name, self.label_dates, "stale")
            remove_labels_from_items(excluded_labeled, label_name, self.label_dates, "excluded genre")

            # Get target count from config
            target_count = self.config['general'].get('limit_plex_results', 20)

            print(f"{GREEN}Building optimal collection of top {target_count} recommendations...{RESET}")

            # Score ALL candidates: existing unwatched + new recommendations
            all_candidates = {}  # show_id -> (plex_show, score)

            # Score existing unwatched items
            for show in unwatched_labeled:
                show_id = int(show.ratingKey)
                show_info = self.show_cache.cache['shows'].get(str(show_id))
                if show_info:
                    try:
                        score, _ = self._calculate_similarity_from_cache(show_info)
                        all_candidates[show_id] = (show, score)
                    except Exception:
                        all_candidates[show_id] = (show, 0.0)

            # Score new recommendations
            for rec in selected_shows:
                plex_show = next(
                    (s for s in shows_to_update if s.title == rec['title'] and s.year == rec.get('year')),
                    None
                )
                if plex_show:
                    show_id = int(plex_show.ratingKey)
                    if show_id not in self.watched_show_ids:
                        score = rec.get('similarity_score', 0.0)
                        # Keep higher score if already exists
                        if show_id not in all_candidates or score > all_candidates[show_id][1]:
                            all_candidates[show_id] = (plex_show, score)

            # Sort by score and take top N
            sorted_candidates = sorted(all_candidates.items(), key=lambda x: x[1][1], reverse=True)
            top_candidates = sorted_candidates[:target_count]
            top_ids = {show_id for show_id, _ in top_candidates}

            # Determine what to add and remove
            current_ids = {int(s.ratingKey) for s in unwatched_labeled}
            ids_to_add = top_ids - current_ids
            ids_to_remove = current_ids - top_ids

            # Remove items that didn't make the cut
            if ids_to_remove:
                shows_to_remove = [s for s in unwatched_labeled if int(s.ratingKey) in ids_to_remove]
                print(f"{YELLOW}Removing {len(shows_to_remove)} lower-scoring items to make room for better ones{RESET}")
                remove_labels_from_items(shows_to_remove, label_name, self.label_dates, "replaced by higher score")

            # Add new high-scoring items
            shows_to_add = [all_candidates[sid][0] for sid in ids_to_add if sid in all_candidates]
            if shows_to_add:
                print(f"{GREEN}Adding {len(shows_to_add)} new high-scoring recommendations{RESET}")
                add_labels_to_items(shows_to_add, label_name, self.label_dates)

            # Save label dates to cache for persistence
            self._save_watched_cache()

            print(f"{GREEN}Collection now has top {len(top_candidates)} recommendations by score{RESET}")

            # Build final collection from top candidates (already sorted by score)
            final_collection_shows = [plex_show for show_id, (plex_show, score) in top_candidates]

            print(f"{GREEN}Final collection size: {len(final_collection_shows)} shows (sorted by similarity){RESET}")
            print(f"{GREEN}Successfully updated labels incrementally{RESET}")

            # Update the Plex collection with sorted shows
            if final_collection_shows:
                # Get display name for collection title
                username = label_name.replace('Recommended_', '')
                if username in self.user_preferences and 'display_name' in self.user_preferences[username]:
                    display_name = self.user_preferences[username]['display_name']
                else:
                    display_name = username.capitalize()

                collection_name = f"📺 {display_name} - Recommendation"
                update_plex_collection(shows_section, collection_name, final_collection_shows, logger)

                # Clean up old collection naming patterns for this user
                cleanup_old_collections(shows_section, collection_name, username, "📺", logger)

        except Exception as e:
            log_error(f"Error managing Plex labels: {e}")
            import traceback
            print(traceback.format_exc())


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
    combine_watch_history = general.get('combine_watch_history', True)

    # Process single user mode
    single_user = args.username
    if single_user:
        log_warning(f"Single user mode: {single_user}")

    # Get all users that need to be processed
    all_users = []
    plex_config = base_config.get('plex_users', {})
    plex_users = plex_config.get('users')
    
    # Check if Plex users are configured and not 'none'
    if plex_users and str(plex_users).lower() != 'none':
        # Process Plex users
        if isinstance(plex_users, str):
            all_users = [u.strip() for u in plex_users.split(',') if u.strip()]
        elif isinstance(plex_users, list):
            all_users = plex_users
    else:
        # Fall back to managed users if Plex users not configured or is 'none'
        managed_users = base_config['plex'].get('managed_users', '')
        all_users = [u.strip() for u in managed_users.split(',') if u.strip()]

    # If single user specified, only process that user
    if single_user:
        all_users = [single_user]

    if combine_watch_history or not all_users:
        # Original behavior - single run
        process_recommendations(base_config, config_path, log_retention_days, single_user=single_user)
    else:
        # Individual runs for each user
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
        if recommendations:
            for i, show in enumerate(recommendations, start=1):
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
        recommender.manage_plex_labels(recommendations)

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