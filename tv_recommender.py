import os
import argparse
import logging
import plexapi.server
from plexapi.server import PlexServer
from plexapi.myplex import MyPlexAccount
import yaml
import sys
import requests
from typing import Dict, List, Set, Optional, Tuple
from collections import Counter
import time
import random
import json
import re
from datetime import datetime, timedelta
import math
import copy

# Import shared utilities
from utils import (
    RED, GREEN, YELLOW, CYAN, RESET,
    RATING_MULTIPLIERS, ANSI_PATTERN,
    get_full_language_name, cleanup_old_logs, setup_logging,
    get_plex_account_ids, get_watched_show_count,
    fetch_plex_watch_history_shows,
    log_warning, log_error, update_plex_collection, cleanup_old_collections,
    load_config, init_plex, get_configured_users, get_current_users,
    get_excluded_genres_for_user, get_user_specific_connection,
    calculate_recency_multiplier, calculate_rewatch_multiplier,
    map_path, show_progress, TeeLogger
)

# Module-level logger - configured by setup_logging() in main()
logger = logging.getLogger('plex_recommender')

__version__ = "1.0.0"

class ShowCache:
    """Cache for TV show metadata including TMDB data, genres, and keywords."""

    def __init__(self, cache_dir: str, recommender=None):
        """Initialize the show cache.

        Args:
            cache_dir: Directory path where cache files are stored
            recommender: Reference to parent PlexTVRecommender instance
        """
        self.all_shows_cache_path = os.path.join(cache_dir, "all_shows_cache.json")
        self.cache = self._load_cache()
        self.recommender = recommender  # Store reference to recommender
        
    def _load_cache(self) -> Dict:
        if os.path.exists(self.all_shows_cache_path):
            try:
                with open(self.all_shows_cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                log_warning(f"Error loading all shows cache: {e}")
                return {'shows': {}, 'last_updated': None, 'library_count': 0}
        return {'shows': {}, 'last_updated': None, 'library_count': 0}
    
    def update_cache(self, plex, library_title: str, tmdb_api_key: Optional[str] = None):
        shows_section = plex.library.section(library_title)
        all_shows = shows_section.all()
        current_count = len(all_shows)
        
        if current_count == self.cache['library_count']:
            print(f"{GREEN}Show cache is up to date{RESET}")
            return False
            
        print(f"\n{YELLOW}Analyzing library shows...{RESET}")
        
        current_shows = set(str(show.ratingKey) for show in all_shows)
        removed = set(self.cache['shows'].keys()) - current_shows
        
        if removed:
            print(f"{YELLOW}Removing {len(removed)} shows from cache that are no longer in library{RESET}")
            for show_id in removed:
                del self.cache['shows'][show_id]
        
        existing_ids = set(self.cache['shows'].keys())
        new_shows = [show for show in all_shows if str(show.ratingKey) not in existing_ids]
        
        if new_shows:
            
            for i, show in enumerate(new_shows, 1):
                msg = f"\r{CYAN}Processing show {i}/{len(new_shows)} ({int((i/len(new_shows))*100)}%){RESET}"
                sys.stdout.write(msg)
                sys.stdout.flush()
                
                show_id = str(show.ratingKey)
                try:
                    show.reload()
                    
                    # Add delay between shows
                    if i > 1 and tmdb_api_key:
                        time.sleep(0.5)  # Basic rate limiting
                    
                    imdb_id = None
                    tmdb_id = None
                    if hasattr(show, 'guids'):
                        for guid in show.guids:
                            if 'imdb://' in guid.id:
                                imdb_id = guid.id.replace('imdb://', '')
                            elif 'themoviedb://' in guid.id:
                                try:
                                    tmdb_id = int(guid.id.split('themoviedb://')[1].split('?')[0])
                                except (ValueError, IndexError):
                                    pass
                    
                    # TMDB ID search with retries
                    if not tmdb_id and tmdb_api_key:
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                params = {
                                    'api_key': tmdb_api_key,
                                    'query': show.title,
                                    'first_air_date_year': getattr(show, 'year', None)
                                }
                                resp = requests.get(
                                    "https://api.themoviedb.org/3/search/tv",
                                    params=params,
                                    timeout=15
                                )
                                
                                if resp.status_code == 429:
                                    sleep_time = 2 * (attempt + 1)
                                    log_warning(f"TMDB rate limit hit, waiting {sleep_time}s...")
                                    time.sleep(sleep_time)
                                    continue
                                    
                                if resp.status_code == 200:
                                    results = resp.json().get('results', [])
                                    if results:
                                        tmdb_id = results[0]['id']
                                    break
                                    
                            except (requests.exceptions.ConnectionError, 
                                   requests.exceptions.Timeout,
                                   requests.exceptions.ChunkedEncodingError) as e:
                                log_warning(f"Connection error, retrying... ({attempt+1}/{max_retries})")
                                time.sleep(1)
                                if attempt == max_retries - 1:
                                    log_warning(f"Failed to get TMDB ID for {show.title} after {max_retries} tries")
                            except Exception as e:
                                log_warning(f"Error getting TMDB ID for {show.title}: {e}")
                                break
    
                    tmdb_keywords = []
                    if tmdb_id and tmdb_api_key:
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                kw_resp = requests.get(
                                    f"https://api.themoviedb.org/3/tv/{tmdb_id}/keywords",
                                    params={'api_key': tmdb_api_key},
                                    timeout=15
                                )
                                
                                if kw_resp.status_code == 429:
                                    sleep_time = 2 * (attempt + 1)
                                    log_warning(f"TMDB rate limit hit, waiting {sleep_time}s...")
                                    time.sleep(sleep_time)
                                    continue
                                    
                                if kw_resp.status_code == 200:
                                    keywords = kw_resp.json().get('results', [])
                                    tmdb_keywords = [k['name'].lower() for k in keywords]
                                    break
                                    
                            except (requests.exceptions.ConnectionError,
                                   requests.exceptions.Timeout,
                                   requests.exceptions.ChunkedEncodingError) as e:
                                log_warning(f"Connection error, retrying... ({attempt+1}/{max_retries})")
                                time.sleep(1)
                                if attempt == max_retries - 1:
                                    log_warning(f"Failed to get keywords for {show.title} after {max_retries} tries")
                            except Exception as e:
                                log_warning(f"Error getting TMDB keywords for {show.title}: {e}")
                                break
    
                    # Store in recommender's caches if available
                    if self.recommender and tmdb_id:
                        self.recommender.plex_tmdb_cache[str(show.ratingKey)] = tmdb_id
                        if tmdb_keywords:
                            self.recommender.tmdb_keywords_cache[str(tmdb_id)] = tmdb_keywords
                    
                    show_info = {
                        'title': show.title,
                        'year': getattr(show, 'year', None),
                        'genres': [g.tag.lower() for g in show.genres] if hasattr(show, 'genres') else [],
                        'studio': getattr(show, 'studio', 'N/A'),
                        'cast': [r.tag for r in show.roles[:3]] if hasattr(show, 'roles') else [],
                        'summary': getattr(show, 'summary', ''),
                        'language': self._get_show_language(show),
                        'tmdb_keywords': tmdb_keywords,
                        'tmdb_id': tmdb_id,
                        'imdb_id': imdb_id
                    }
                    self.cache['shows'][show_id] = show_info
                    
                except Exception as e:
                    log_warning(f"Error processing show {show.title}: {e}")
                    continue
                    
        self.cache['library_count'] = current_count
        self.cache['last_updated'] = datetime.now().isoformat()
        self._save_cache()
        print(f"\n{GREEN}Show cache updated{RESET}")
        return True
        
    def _save_cache(self):
        try:
            with open(self.all_shows_cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=4, ensure_ascii=False)
        except Exception as e:
            log_error(f"Error saving all shows cache: {e}")

    def _get_show_language(self, show) -> str:
        """Get show's primary audio language from first episode"""
        try:
            episodes = show.episodes()
            if not episodes:
                return "N/A"
    
            episode = episodes[0]
            episode.reload()
            
            if not episode.media:
                return "N/A"
                
            for media in episode.media:
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

        except Exception as e:
            pass  # DEBUG removed
        return "N/A"

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
        tmdb_config = self.config.get('TMDB', {})
        self.use_tmdb_keywords = tmdb_config.get('use_TMDB_keywords', True)
        self.tmdb_api_key = tmdb_config.get('api_key', None)
		
        self.cache_dir = os.path.join(os.path.dirname(__file__), 'cache')
        os.makedirs(self.cache_dir, exist_ok=True)
        self.show_cache = ShowCache(self.cache_dir, recommender=self)
        self.show_cache.update_cache(self.plex, self.library_title, self.tmdb_api_key)

        self.confirm_operations = general_config.get('confirm_operations', False)
        self.limit_plex_results = general_config.get('limit_plex_results', 10)
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
            'genre_weight': float(weights_config.get('genre_weight', 0.25)),
            'studio_weight': float(weights_config.get('studio_weight', 0.20)),
            'actor_weight': float(weights_config.get('actor_weight', 0.20)),
            'language_weight': float(weights_config.get('language_weight', 0.10)),
            'keyword_weight': float(weights_config.get('keyword_weight', 0.25))
        }

        total_weight = sum(self.weights.values())
        if not abs(total_weight - 1.0) < 1e-6:
            log_warning(f"Warning: Weights sum to {total_weight}, expected 1.0.")

        # Verify Plex user configuration
        if self.users['plex_users']:
            pass
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

        if os.path.exists(self.watched_cache_path):
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
        counters = {
            'genres': Counter(),
            'studio': Counter(),
            'actors': Counter(),
            'languages': Counter(),
            'tmdb_keywords': Counter(),
            'tmdb_ids': set()
        }
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
    
        counters = {
            'genres': Counter(),
            'studio': Counter(),
            'actors': Counter(),
            'languages': Counter(),
            'tmdb_keywords': Counter(),
            'tmdb_ids': set()  # Initialize as a set for unique IDs
        }
        
        account = MyPlexAccount(token=self.config['plex']['token'])
        admin_user = self.users['admin_user']
        
        # Determine which users to process
        if self.single_user:
            pass
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
        try:
            # Create a copy of the watched data to modify for serialization
            watched_data_for_cache = copy.deepcopy(self.watched_data_counters)
            
            # Convert any set objects to lists for JSON serialization
            if 'tmdb_ids' in watched_data_for_cache and isinstance(watched_data_for_cache['tmdb_ids'], set):
                watched_data_for_cache['tmdb_ids'] = list(watched_data_for_cache['tmdb_ids'])
            
            cache_data = {
                'watched_count': self.cached_watched_count,
                'watched_data_counters': watched_data_for_cache,
                'plex_tmdb_cache': {str(k): v for k, v in self.plex_tmdb_cache.items()},
                'tmdb_keywords_cache': {str(k): v for k, v in self.tmdb_keywords_cache.items()},
                'watched_show_ids': list(self.watched_show_ids),
                'label_dates': getattr(self, 'label_dates', {}),
                'last_updated': datetime.now().isoformat()
            }
            
            with open(self.watched_cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=4, ensure_ascii=False)

            logger.debug(f"Saved watched cache: {self.cached_watched_count} shows, {len(self.watched_show_ids)} IDs")

        except Exception as e:
            log_warning(f"Error saving watched cache: {e}")

    def _save_cache(self):
        self._save_watched_cache()

    def _process_show_counters_from_cache(self, show_info: Dict, counters: Dict, rewatch_multiplier: float = 1.0) -> None:
        try:
            rating = float(show_info.get('user_rating', 0))
            if not rating:
                rating = float(show_info.get('audience_rating', 5.0))
            rating = max(0, min(10, int(round(rating))))
            multiplier = RATING_MULTIPLIERS.get(rating, 1.0) * rewatch_multiplier
    
            # Process all counters using cached data
            for genre in show_info.get('genres', []):
                counters['genres'][genre] += multiplier
            
            if studio := show_info.get('studio'):
                counters['studio'][studio.lower()] += multiplier
                
            for actor in show_info.get('cast', [])[:3]:
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
                  for lib_title, lib_year in self.library_shows)

    def _process_show_counters(self, show, counters):
        show_details = self.get_show_details(show)
        
        try:
            rating = float(getattr(show, 'userRating', 0))
        except (TypeError, ValueError):
            try:
                rating = float(getattr(show, 'audienceRating', 5.0))
            except (TypeError, ValueError):
                rating = 5.0
    
        rating = max(0, min(10, int(round(rating))))
        multiplier = RATING_MULTIPLIERS.get(rating, 1.0)
    
        # Process all the existing counters...
        for genre in show_details.get('genres', []):
            counters['genres'][genre] += multiplier
        
        if hasattr(show, 'studio') and show.studio:
            counters['studio'][show.studio.lower()] += multiplier
            
        for actor in show_details.get('cast', [])[:3]:
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
                        pass
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
        imdb_ids = set()
        try:
            shows = self.plex.library.section(self.library_title).all()
            for show in shows:
                if hasattr(show, 'guids'):
                    for guid in show.guids:
                        if guid.id.startswith('imdb://'):
                            imdb_ids.add(guid.id.replace('imdb://', ''))
                            break
        except Exception as e:
            log_warning(f"Error retrieving IMDb IDs from library: {e}")
        return imdb_ids

    def get_show_details(self, show) -> Dict:
        try:
            show.reload()
            
            imdb_id = None
            audience_rating = 0
            tmdb_keywords = []
            
            if hasattr(show, 'guids'):
                for guid in show.guids:
                    if 'imdb://' in guid.id:
                        imdb_id = guid.id.replace('imdb://', '')
                        break
            
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
                show_info['cast'] = [r.tag for r in show.roles[:3]]
                
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
    
        try:
            url = f"https://api.themoviedb.org/3/find/{imdb_id}"
            params = {'api_key': self.tmdb_api_key, 'external_source': 'imdb_id'}
            resp = requests.get(url, params=params)
            resp.raise_for_status()
            return resp.json().get('tv_results', [{}])[0].get('id')
        except Exception as e:
            log_warning(f"IMDb fallback failed: {e}")
            return None

    def _get_plex_show_tmdb_id(self, plex_show) -> Optional[int]:
        # Recursion guard and cache check
        if hasattr(plex_show, '_tmdb_fallback_attempted'):
            return self.plex_tmdb_cache.get(plex_show.ratingKey)
        
        if plex_show.ratingKey in self.plex_tmdb_cache:
            return self.plex_tmdb_cache[plex_show.ratingKey]
    
        tmdb_id = None
        show_title = plex_show.title
        show_year = getattr(plex_show, 'year', None)
    
        # Method 1: Check Plex GUIDs
        if hasattr(plex_show, 'guids'):
            for guid in plex_show.guids:
                if 'themoviedb' in guid.id:
                    try:
                        tmdb_id = int(guid.id.split('themoviedb://')[1].split('?')[0])
                        break
                    except (ValueError, IndexError) as e:
                        continue
    
        # Method 2: TMDB API Search
        if not tmdb_id and self.tmdb_api_key:
            try:
                params = {
                    'api_key': self.tmdb_api_key,
                    'query': show_title,
                    'include_adult': False
                }
                if show_year:
                    params['first_air_date_year'] = show_year
    
                resp = requests.get(
                    "https://api.themoviedb.org/3/search/tv",
                    params=params,
                    timeout=10
                )
                resp.raise_for_status()
                
                results = resp.json().get('results', [])
                if results:
                    exact_match = next(
                        (r for r in results 
                         if r.get('name', '').lower() == show_title.lower()
                         and str(r.get('first_air_date', '')[:4]) == str(show_year)),
                        None
                    )
                    
                    tmdb_id = exact_match['id'] if exact_match else results[0]['id']
    
            except Exception as e:
                log_warning(f"TMDB search failed for {show_title}: {e}")
    
        # Method 3: Single Fallback Attempt via IMDb
        if not tmdb_id and not hasattr(plex_show, '_tmdb_fallback_attempted'):
            plex_show._tmdb_fallback_attempted = True
            tmdb_id = self._get_tmdb_id_via_imdb(plex_show)
    
        # Update cache even if None to prevent repeat lookups
        if tmdb_id:
            logger.debug(f"Cached TMDB ID {tmdb_id} for Plex show {plex_show.ratingKey}")
            self.plex_tmdb_cache[str(plex_show.ratingKey)] = tmdb_id
            self._save_watched_cache()
        return tmdb_id

    def _get_plex_show_imdb_id(self, plex_show) -> Optional[str]:
        if not plex_show.guid:
            return None
        guid = plex_show.guid
        if guid.startswith('imdb://'):
            return guid.split('imdb://')[1]
        
        tmdb_id = self._get_plex_show_tmdb_id(plex_show)
        if not tmdb_id:
            return None
        try:
            url = f"https://api.themoviedb.org/3/tv/{tmdb_id}"
            params = {'api_key': self.tmdb_api_key}
            resp = requests.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('external_ids', {}).get('imdb_id')
            else:
                log_warning(f"Failed to fetch IMDb ID from TMDB for show '{plex_show.title}'. Status Code: {resp.status_code}")
        except Exception as e:
            log_warning(f"Error fetching IMDb ID for TMDB ID {tmdb_id}: {e}")
        return None

    def _get_tmdb_keywords_for_id(self, tmdb_id: int) -> Set[str]:
        if not tmdb_id or not self.use_tmdb_keywords or not self.tmdb_api_key:
            return set()

        if tmdb_id in self.tmdb_keywords_cache:
            return set(self.tmdb_keywords_cache[tmdb_id])

        kw_set = set()
        try:
            url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/keywords"
            params = {'api_key': self.tmdb_api_key}
            resp = requests.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                keywords = data.get('results', [])
                kw_set = {k['name'].lower() for k in keywords}
        except Exception as e:
            log_warning(f"Error fetching TMDB keywords for ID {tmdb_id}: {e}")

        if kw_set:
            logger.debug(f"Cached {len(kw_set)} keywords for TMDB ID {tmdb_id}")
            self.tmdb_keywords_cache[str(tmdb_id)] = list(kw_set)  # Convert key to string
            self._save_watched_cache()
        return kw_set

    def _get_show_language(self, show) -> str:
        """Get show's primary audio language - delegates to ShowCache"""
        return self.show_cache._get_show_language(show)

    def _extract_genres(self, show) -> List[str]:
        genres = []
        try:
            if not hasattr(show, 'genres') or not show.genres:
                return genres
                
            for genre in show.genres:
                if isinstance(genre, plexapi.media.Genre):
                    if hasattr(genre, 'tag'):
                        genres.append(genre.tag.lower())
                elif isinstance(genre, str):
                    genres.append(genre.lower())
                else:
                    pass
        except Exception as e:
            pass
        return genres

    # ------------------------------------------------------------------------
    # CALCULATE SCORES
    # ------------------------------------------------------------------------
    def _calculate_similarity_from_cache(self, show_info: Dict) -> Tuple[float, Dict]:
        """Calculate similarity score using cached show data and return score with breakdown"""
        try:
            score = 0.0
            score_breakdown = {
                'genre_score': 0.0,
                'studio_score': 0.0,
                'actor_score': 0.0,
                'language_score': 0.0,
                'keyword_score': 0.0,
                'details': {
                    'genres': [],
                    'studio': None,
                    'actors': [],
                    'language': None,
                    'keywords': []
                }
            }
            
            weights = self.weights
            user_prefs = {
                'genres': Counter(self.watched_data.get('genres', {})),
                'studio': Counter(self.watched_data.get('studio', {})),
                'actors': Counter(self.watched_data.get('actors', {})),
                'languages': Counter(self.watched_data.get('languages', {})),
                'keywords': Counter(self.watched_data.get('tmdb_keywords', {}))
            }
            
            max_counts = {
                'genres': max(user_prefs['genres'].values()) if user_prefs['genres'] else 1,
                'studio': max(user_prefs['studio'].values()) if user_prefs['studio'] else 1,
                'actors': max(user_prefs['actors'].values()) if user_prefs['actors'] else 1,
                'languages': max(user_prefs['languages'].values()) if user_prefs['languages'] else 1,
                'keywords': max(user_prefs['keywords'].values()) if user_prefs['keywords'] else 1
            }
    
            # Genre Score
            show_genres = set(show_info.get('genres', []))
            if show_genres:
                genre_scores = []
                for genre in show_genres:
                    genre_count = user_prefs['genres'].get(genre, 0)
                    if genre_count > 0:
                        if self.normalize_counters:
                            # Enhanced normalization with square root to strengthen effect
                            # This will boost lower values more significantly
                            normalized_score = math.sqrt(genre_count / max_counts['genres'])
                            genre_scores.append(normalized_score)
                            score_breakdown['details']['genres'].append(
                                f"{genre} (count: {genre_count}, norm: {round(normalized_score, 2)})"
                            )
                        else:
                            # When not normalizing, use raw relative proportion
                            normalized_score = min(genre_count / max_counts['genres'], 1.0)
                            genre_scores.append(normalized_score)
                            score_breakdown['details']['genres'].append(
                                f"{genre} (count: {genre_count}, norm: {round(normalized_score, 2)})"
                            )
                if genre_scores:
                    genre_final = (sum(genre_scores) / len(genre_scores)) * weights.get('genre_weight', 0.25)
                    score += genre_final
                    score_breakdown['genre_score'] = round(genre_final, 3)
    
            # Studio Score
            if show_info.get('studio') and show_info['studio'] != 'N/A':
                studio_count = user_prefs['studio'].get(show_info['studio'].lower(), 0)
                if studio_count > 0:
                    if self.normalize_counters:
                        normalized_score = math.sqrt(studio_count / max_counts['studio'])
                    else:
                        normalized_score = min(studio_count / max_counts['studio'], 1.0)
                    
                    studio_final = normalized_score * weights.get('studio_weight', 0.20)
                    score += studio_final
                    score_breakdown['studio_score'] = round(studio_final, 3)
                    score_breakdown['details']['studio'] = f"{show_info['studio']} (count: {studio_count}, norm: {round(normalized_score, 2)})"
    
            # Actor Score
            show_cast = show_info.get('cast', [])
            if show_cast:
                actor_scores = []
                matched_actors = 0
                for actor in show_cast:
                    actor_count = user_prefs['actors'].get(actor, 0)
                    if actor_count > 0:
                        matched_actors += 1
                        if self.normalize_counters:
                            normalized_score = math.sqrt(actor_count / max_counts['actors'])
                        else:
                            normalized_score = min(actor_count / max_counts['actors'], 1.0)
                            
                        actor_scores.append(normalized_score)
                        score_breakdown['details']['actors'].append(
                            f"{actor} (count: {actor_count}, norm: {round(normalized_score, 2)})"
                        )
                if matched_actors > 0:
                    actor_score = sum(actor_scores) / matched_actors
                    if matched_actors > 3:
                        actor_score *= (3 / matched_actors)  # Normalize if many matches
                    actor_final = actor_score * weights.get('actor_weight', 0.20)
                    score += actor_final
                    score_breakdown['actor_score'] = round(actor_final, 3)
    
            # Language Score
            show_language = show_info.get('language', 'N/A')
            if show_language != 'N/A':
                show_lang_lower = show_language.lower()
                            
                lang_count = user_prefs['languages'].get(show_lang_lower, 0)
                
                if lang_count > 0:
                    if self.normalize_counters:
                        normalized_score = math.sqrt(lang_count / max_counts['languages'])
                    else:
                        normalized_score = min(lang_count / max_counts['languages'], 1.0)
                    
                    lang_final = normalized_score * weights.get('language_weight', 0.10)
                    score += lang_final
                    score_breakdown['language_score'] = round(lang_final, 3)
                    score_breakdown['details']['language'] = f"{show_language} (count: {lang_count}, norm: {round(normalized_score, 2)})"
    
            # TMDB Keywords Score
            if self.use_tmdb_keywords and show_info.get('tmdb_keywords'):
                keyword_scores = []
                for kw in show_info['tmdb_keywords']:
                    count = user_prefs['keywords'].get(kw, 0)
                    if count > 0:
                        if self.normalize_counters:
                            normalized_score = math.sqrt(count / max_counts['keywords'])
                        else:
                            normalized_score = min(count / max_counts['keywords'], 1.0)
                            
                        keyword_scores.append(normalized_score)
                        score_breakdown['details']['keywords'].append(
                            f"{kw} (count: {count}, norm: {round(normalized_score, 2)})"
                        )
                if keyword_scores:
                    keyword_final = (sum(keyword_scores) / len(keyword_scores)) * weights.get('keyword_weight', 0.25)
                    score += keyword_final
                    score_breakdown['keyword_score'] = round(keyword_final, 3)
    
            # Ensure final score doesn't exceed 1.0 (100%)
            score = min(score, 1.0)
    
            return score, score_breakdown
    
        except Exception as e:
            log_warning(f"Error calculating similarity score for {show_info.get('title', 'Unknown')}: {e}")
            return 0.0, score_breakdown

    def _print_similarity_breakdown(self, show_info: Dict, score: float, breakdown: Dict):
        """Print detailed breakdown of similarity score calculation"""
        print(f"\n{CYAN}Similarity Score Breakdown for '{show_info['title']}'{RESET}")
        print(f"Total Score: {round(score * 100, 1)}%")
        print(f"├─ Genre Score: {round(breakdown['genre_score'] * 100, 1)}%")
        if breakdown['details']['genres']:
            print(f"│  └─ Matching genres: {', '.join(breakdown['details']['genres'])}")
        print(f"├─ Studio Score: {round(breakdown['studio_score'] * 100, 1)}%")
        if breakdown['details']['studio']:
            print(f"│  └─ Studio match: {breakdown['details']['studio']}")
        print(f"├─ Actor Score: {round(breakdown['actor_score'] * 100, 1)}%")
        if breakdown['details']['actors']:
            print(f"│  └─ Matching actors: {', '.join(breakdown['details']['actors'])}")
        print(f"├─ Language Score: {round(breakdown['language_score'] * 100, 1)}%")
        if breakdown['details']['language']:
            print(f"│  └─ Language match: {breakdown['details']['language']}")
        print(f"└─ Keyword Score: {round(breakdown['keyword_score'] * 100, 1)}%")
        if breakdown['details']['keywords']:
            print(f"   └─ Matching keywords: {', '.join(breakdown['details']['keywords'])}")
        print("")
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
                pass
                # Take top 10% of shows by similarity score and randomize
                top_count = max(int(len(scored_shows) * 0.1), self.limit_plex_results)
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
        prompt = (
            f"\nWhich recommendations would you like to {operation_label}?\n"
            "Enter 'all' or 'y' to select ALL,\n"
            "Enter 'none' or 'n' to skip them,\n"
            "Or enter a comma-separated list of numbers (e.g. 1,3,5). "
            "\nYour choice: "
        )
        choice = input(prompt).strip().lower()

        if choice in ("n", "no", "none", ""):
            log_warning(f"Skipping {operation_label} as per user choice.")
            return []
        if choice in ("y", "yes", "all"):
            return recommended_shows

        indices_str = re.split(r'[,\s]+', choice)
        chosen = []
        for idx_str in indices_str:
            idx_str = idx_str.strip()
            if not idx_str.isdigit():
                log_warning(f"Skipping invalid index: {idx_str}")
                continue
            idx = int(idx_str)
            if 1 <= idx <= len(recommended_shows):
                chosen.append(idx)
            else:
                log_warning(f"Skipping out-of-range index: {idx}")

        if not chosen:
            log_warning(f"No valid indices selected, skipping {operation_label}.")
            return []

        subset = []
        for c in chosen:
            subset.append(recommended_shows[c - 1])
        return subset

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
            label_name = self.config.get('collections', {}).get('label_name', 'Recommended')

            # Handle username appending for labels
            if self.config.get('collections', {}).get('append_usernames', False):
                if self.single_user:
                    # For single user mode, only append the current user
                    user_suffix = re.sub(r'\W+', '_', self.single_user.strip())
                    label_name = f"{label_name}_{user_suffix}"
                else:
                    # For combined mode, append all users
                    users = []
                    if self.users['plex_users']:
                        users = self.users['plex_users']
                    else:
                        users = self.users['managed_users']
                    
                    if users:
                        sanitized_users = [re.sub(r'\W+', '_', user.strip()) for user in users]
                        user_suffix = '_'.join(sanitized_users)
                        label_name = f"{label_name}_{user_suffix}"
    
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
            from datetime import datetime, timedelta
            stale_threshold = datetime.now() - timedelta(days=stale_days)

            # Get currently labeled shows
            currently_labeled = shows_section.search(label=label_name)
            print(f"Found {len(currently_labeled)} currently labeled shows")

            # Get excluded genres for this user (for checking existing items)
            excluded_genres = get_excluded_genres_for_user(self.exclude_genres, self.user_preferences, self.single_user)

            # Separate into watched, unwatched-fresh, stale, and excluded
            unwatched_labeled = []
            watched_labeled = []
            stale_labeled = []
            excluded_labeled = []

            for show in currently_labeled:
                show.reload()  # Ensure fresh data
                show_id = int(show.ratingKey)
                label_key = f"{show_id}_{label_name}"

                # Check if this show has excluded genres
                show_genres = [g.tag.lower() for g in show.genres]
                if any(g in excluded_genres for g in show_genres):
                    excluded_labeled.append(show)
                    continue

                # Check if this show has been watched by any of the users
                if show_id in self.watched_show_ids:
                    watched_labeled.append(show)
                else:
                    # Check if stale (unwatched for > stale_days)
                    label_date_str = self.label_dates.get(label_key)
                    if label_date_str:
                        label_date = datetime.fromisoformat(label_date_str)
                        if label_date < stale_threshold:
                            stale_labeled.append(show)
                        else:
                            unwatched_labeled.append(show)
                    else:
                        # No date tracked - assume it's new (keep it)
                        unwatched_labeled.append(show)
                        # Track it now for future runs
                        self.label_dates[label_key] = datetime.now().isoformat()

            print(f"{GREEN}Keeping {len(unwatched_labeled)} fresh unwatched recommendations{RESET}")
            print(f"{YELLOW}Removing {len(watched_labeled)} watched shows from recommendations{RESET}")
            print(f"{YELLOW}Removing {len(stale_labeled)} stale recommendations (unwatched > {stale_days} days){RESET}")
            print(f"{YELLOW}Removing {len(excluded_labeled)} shows with excluded genres{RESET}")

            # Remove labels from watched shows
            for show in watched_labeled:
                show.removeLabel(label_name)
                label_key = f"{int(show.ratingKey)}_{label_name}"
                if label_key in self.label_dates:
                    del self.label_dates[label_key]
                log_warning(f"Removed (watched): {show.title}")

            # Remove labels from stale shows
            for show in stale_labeled:
                show.removeLabel(label_name)
                label_key = f"{int(show.ratingKey)}_{label_name}"
                if label_key in self.label_dates:
                    del self.label_dates[label_key]
                log_warning(f"Removed (stale): {show.title}")

            # Remove labels from excluded genre shows
            for show in excluded_labeled:
                show.removeLabel(label_name)
                label_key = f"{int(show.ratingKey)}_{label_name}"
                if label_key in self.label_dates:
                    del self.label_dates[label_key]
                log_warning(f"Removed (excluded genre): {show.title}")

            # Get target count from config
            target_count = self.config['general'].get('limit_plex_results', 20)

            # Calculate how many new recommendations we need
            current_unwatched_count = len(unwatched_labeled)
            slots_available = target_count - current_unwatched_count

            print(f"{GREEN}Collection capacity: {current_unwatched_count}/{target_count} (need {max(0, slots_available)} more){RESET}")

            # Get IDs of shows already in collection
            already_labeled_ids = {int(s.ratingKey) for s in unwatched_labeled}

            # Filter new recommendations to exclude already labeled shows
            new_recommendations = []
            for show in shows_to_update:
                show_id = int(show.ratingKey)
                if show_id not in already_labeled_ids and show_id not in self.watched_show_ids:
                    new_recommendations.append(show)

            # Take only what we need to fill gaps
            shows_to_add = new_recommendations[:max(0, slots_available)]

            print(f"{GREEN}Adding {len(shows_to_add)} new recommendations to fill gaps{RESET}")

            # Add labels to new recommendations
            for show in shows_to_add:
                current_labels = [label.tag for label in show.labels]
                if label_name not in current_labels:
                    show.addLabel(label_name)
                    # Track label date
                    label_key = f"{int(show.ratingKey)}_{label_name}"
                    self.label_dates[label_key] = datetime.now().isoformat()
                    print(f"{GREEN}Added: {show.title}{RESET}")

            # Save label dates to cache for persistence
            self._save_watched_cache()

            # RE-SORT: Calculate similarity for all shows and sort by score
            print(f"{GREEN}Re-calculating similarity scores for entire collection...{RESET}")

            # Create a mapping of show_id -> similarity_score from selected_shows (new recommendations)
            similarity_scores = {}
            for rec in selected_shows:
                # Find the Plex object that matches this recommendation
                matching_plex = next(
                    (s for s in shows_to_update if s.title == rec['title'] and s.year == rec.get('year')),
                    None
                )
                if matching_plex:
                    similarity_scores[int(matching_plex.ratingKey)] = rec.get('similarity_score', 0.0)

            # Calculate similarity for unwatched shows from previous runs
            for show in unwatched_labeled:
                show_id = int(show.ratingKey)
                if show_id not in similarity_scores:
                    pass
                    # Get show from cache
                    show_info = self.show_cache.cache['shows'].get(str(show_id))
                    if show_info:
                        try:
                            similarity_score, _ = self._calculate_similarity_from_cache(show_info)
                            similarity_scores[show_id] = similarity_score
                        except Exception:
                            similarity_scores[show_id] = 0.0
                    else:
                        similarity_scores[show_id] = 0.0

            # Combine all labeled shows (unwatched + newly added)
            final_collection_shows = unwatched_labeled + shows_to_add

            # Sort by similarity score (highest first)
            final_collection_shows.sort(
                key=lambda s: similarity_scores.get(int(s.ratingKey), 0.0),
                reverse=True
            )

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
    bullet = f"{index}. " if index is not None else "- "
    output = f"{bullet}{CYAN}{show['title']}{RESET} ({show.get('year', 'N/A')})"

    if 'similarity_score' in show:
        score_percentage = round(show['similarity_score'] * 100, 1)
        output += f" - Similarity: {YELLOW}{score_percentage}%{RESET}"
		
    if show.get('genres'):
        output += f"\n  {YELLOW}Genres:{RESET} {', '.join(show['genres'])}"

    if show_summary and show.get('summary'):
        output += f"\n  {YELLOW}Summary:{RESET} {show['summary']}"

    if show_cast and show.get('cast'):
        output += f"\n  {YELLOW}Cast:{RESET} {', '.join(show['cast'])}"

    if show_language and show.get('language') != "N/A":
        output += f"\n  {YELLOW}Language:{RESET} {show['language']}"

    if show_rating and show.get('ratings', {}).get('audience_rating', 0) > 0:
        rating = show['ratings']['audience_rating']
        output += f"\n  {YELLOW}Rating:{RESET} {rating}/10"

    if show_imdb_link and show.get('imdb_id'):
        imdb_link = f"https://www.imdb.com/title/{show['imdb_id']}/"
        output += f"\n  {YELLOW}IMDb Link:{RESET} {imdb_link}"

    return output

# ------------------------------------------------------------------------
# CONFIG ADAPTER
# ------------------------------------------------------------------------
def adapt_root_config_to_legacy(root_config):
    """Convert root config.yml format to legacy TRFP format"""
    media_type = 'tv'

    # Build legacy config structure
    adapted = {
        'general': {
            'confirm_operations': root_config.get('general', {}).get('confirm_operations', False),
            'plex_only': root_config.get('general', {}).get('plex_only', True),
            'combine_watch_history': root_config.get('general', {}).get('combine_watch_history', False),
            'log_retention_days': root_config.get('general', {}).get('log_retention_days', 7),
            'limit_plex_results': root_config.get(media_type, {}).get('limit_results', 20),
            'exclude_genre': root_config.get('general', {}).get('exclude_genre', 'none'),
            'randomize_recommendations': root_config.get(media_type, {}).get('randomize_recommendations', False),
            'normalize_counters': root_config.get(media_type, {}).get('normalize_counters', True),
            'show_summary': root_config.get(media_type, {}).get('show_summary', True),
            'show_cast': root_config.get(media_type, {}).get('show_cast', True),
            'show_language': root_config.get(media_type, {}).get('show_language', True),
            'show_rating': root_config.get(media_type, {}).get('show_rating', True),
            'show_imdb_link': root_config.get(media_type, {}).get('show_imdb_link', True),
        },
        'plex': {
            'url': root_config.get('plex', {}).get('url', ''),
            'token': root_config.get('plex', {}).get('token', ''),
            'TV_library_title': root_config.get('plex', {}).get('tv_library', 'TV Shows'),
            'managed_users': root_config.get('plex', {}).get('managed_users', 'Admin'),
        },
        'collections': {
            'add_label': root_config.get('collections', {}).get('add_label', True),
            'label_name': root_config.get('collections', {}).get('label_name', 'Recommended'),
            'append_usernames': root_config.get('collections', {}).get('append_usernames', True),
            'remove_previous_recommendations': root_config.get('collections', {}).get('remove_previous_recommendations', False),
            'stale_removal_days': root_config.get('collections', {}).get('stale_removal_days', 7),
        },
        'TMDB': {
            'api_key': root_config.get('tmdb', {}).get('api_key', ''),
        },
        'plex_users': {
            'users': root_config.get('users', {}).get('list', ''),
        },
        'user_preferences': root_config.get('users', {}).get('preferences', {}),
        'weights': root_config.get(media_type, {}).get('weights', {}),
        'quality_filters': root_config.get(media_type, {}).get('quality_filters', {}),
        'recency_decay': root_config.get('recency_decay', {}),
        'paths': root_config.get('platform', {}),
    }

    return adapted

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='TV Show Recommendations for Plex')
    parser.add_argument('username', nargs='?', help='Process recommendations for only this user')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    start_time = datetime.now()
    print(f"{CYAN}TV Show Recommendations for Plex v{__version__}{RESET}")
    print("-" * 50)

    # Load config from project root
    config_path = os.path.join(os.path.dirname(__file__), 'config.yml')
    config_path = os.path.normpath(config_path)  # Clean up path

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
        pass
        # Process Plex users
        if isinstance(plex_users, str):
            all_users = [u.strip() for u in plex_users.split(',') if u.strip()]
        elif isinstance(plex_users, list):
            all_users = plex_users
    else:
        # Fall back to managed users if Plex users not configured or is 'none'
        managed_users = base_config['plex'].get('managed_users', '')
        all_users = [u.strip() for u in managed_users.split(',') if u.strip()]

    if combine_watch_history or not all_users:
        pass
        # Original behavior - single run
        process_recommendations(base_config, config_path, log_retention_days)
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
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')

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