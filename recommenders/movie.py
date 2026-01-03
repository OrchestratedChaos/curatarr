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
    RATING_MULTIPLIERS, ANSI_PATTERN, CACHE_VERSION, check_cache_version,
    get_full_language_name, cleanup_old_logs, setup_logging, get_tmdb_config,
    get_plex_account_ids, fetch_plex_watch_history_movies, get_watched_movie_count,
    log_warning, log_error, update_plex_collection, cleanup_old_collections,
    load_config, init_plex, get_configured_users, get_current_users,
    get_excluded_genres_for_user, get_user_specific_connection,
    calculate_recency_multiplier, calculate_rewatch_multiplier,
    calculate_similarity_score, find_plex_movie,
    map_path, show_progress, TeeLogger,
    # Consolidated utilities
    extract_genres, extract_ids_from_guids, fetch_tmdb_with_retry,
    get_tmdb_id_for_item, get_tmdb_keywords, adapt_config_for_media_type,
    save_json_cache, load_json_cache,
    # Additional consolidated utilities
    user_select_recommendations, extract_rating, format_media_output,
    build_label_name, categorize_labeled_items, remove_labels_from_items, add_labels_to_items,
    get_library_imdb_ids, print_similarity_breakdown, process_counters_from_cache,
    load_media_cache, save_media_cache, create_empty_counters,
    get_plex_user_ids as get_plex_user_ids_util, save_watched_cache
)

# Module-level logger - configured by setup_logging() in main()
logger = logging.getLogger('plex_recommender')

__version__ = "1.2.0"

class MovieCache:
    """Cache for movie metadata including TMDB data, genres, and keywords."""

    def __init__(self, cache_dir: str, recommender=None):
        """Initialize the movie cache.

        Args:
            cache_dir: Directory path where cache files are stored
            recommender: Reference to parent PlexMovieRecommender instance
        """
        self.all_movies_cache_path = os.path.join(cache_dir, "all_movies_cache.json")
        self.cache = self._load_cache()
        self.recommender = recommender  # Store reference to recommender
        
    def _load_cache(self) -> Dict:
        """Load movie cache from file"""
        return load_media_cache(self.all_movies_cache_path, 'movies')
    
    def update_cache(self, plex, library_title: str, tmdb_api_key: Optional[str] = None):
        """Update movie cache with current library contents and TMDB metadata.

        Args:
            plex: PlexServer instance
            library_title: Name of the movies library section
            tmdb_api_key: Optional TMDB API key for fetching additional metadata

        Returns:
            bool: True if cache was updated, False if already up to date
        """
        movies_section = plex.library.section(library_title)
        all_movies = movies_section.all()
        current_count = len(all_movies)
        
        if current_count == self.cache['library_count']:
            print(f"{GREEN}Movie cache is up to date{RESET}")
            return False
            
        print(f"\n{YELLOW}Analyzing library movies...{RESET}")
        
        current_movies = set(str(movie.ratingKey) for movie in all_movies)
        removed = set(self.cache['movies'].keys()) - current_movies
        
        if removed:
            print(f"{YELLOW}Removing {len(removed)} movies from cache that are no longer in library{RESET}")
            for movie_id in removed:
                del self.cache['movies'][movie_id]
        
        existing_ids = set(self.cache['movies'].keys())
        new_movies = [movie for movie in all_movies if str(movie.ratingKey) not in existing_ids]
        
        if new_movies:
            print(f"Found {len(new_movies)} new movies to analyze")
            
            for i, movie in enumerate(new_movies, 1):
                msg = f"\r{CYAN}Processing movie {i}/{len(new_movies)} ({int((i/len(new_movies))*100)}%){RESET}"
                sys.stdout.write(msg)
                sys.stdout.flush()
                
                movie_id = str(movie.ratingKey)
                try:
                    movie.reload()
                    
                    # Add delay between movies
                    if i > 1 and tmdb_api_key:
                        time.sleep(0.5)  # Basic rate limiting
                    
                    # Extract IDs from GUIDs using utility
                    ids = extract_ids_from_guids(movie)
                    imdb_id = ids['imdb_id']
                    tmdb_id = ids['tmdb_id']

                    # Get TMDB ID if not found in GUIDs (with fallback methods)
                    if not tmdb_id and tmdb_api_key:
                        tmdb_id = get_tmdb_id_for_item(movie, tmdb_api_key, 'movie')
    
                    # Fetch TMDB metadata (rating, votes, keywords)
                    tmdb_keywords = []
                    tmdb_rating = None
                    tmdb_vote_count = None

                    if tmdb_id and tmdb_api_key:
                        # Get movie details (includes rating and vote_count)
                        detail_data = fetch_tmdb_with_retry(
                            f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                            {'api_key': tmdb_api_key}
                        )
                        if detail_data:
                            tmdb_rating = detail_data.get('vote_average')
                            tmdb_vote_count = detail_data.get('vote_count')

                        # Get keywords using utility
                        tmdb_keywords = get_tmdb_keywords(tmdb_api_key, tmdb_id, 'movie')
    
                    # Store in recommender's caches if available
                    if self.recommender and tmdb_id:
                        self.recommender.plex_tmdb_cache[str(movie.ratingKey)] = tmdb_id
                        if tmdb_keywords:
                            self.recommender.tmdb_keywords_cache[str(tmdb_id)] = tmdb_keywords
                    
                    # Get directors
                    directors = []
                    if hasattr(movie, 'directors'):
                        directors = [d.tag for d in movie.directors]
                    
                    # Extract ratings
                    audience_rating = 0
                    try:
                        # Try to get userRating first (personal rating)
                        if hasattr(movie, 'userRating') and movie.userRating:
                            audience_rating = float(movie.userRating)
                        # Then try audienceRating (community rating)
                        elif hasattr(movie, 'audienceRating') and movie.audienceRating:
                            audience_rating = float(movie.audienceRating)
                        # Finally check ratings collection
                        elif hasattr(movie, 'ratings'):
                            for rating in movie.ratings:
                                if hasattr(rating, 'value') and rating.value:
                                    if (getattr(rating, 'image', '') == 'imdb://image.rating' or
                                        getattr(rating, 'type', '') == 'audience'):
                                        try:
                                            audience_rating = float(rating.value)
                                            break
                                        except (ValueError, AttributeError):
                                            pass
                    except Exception as e:
                        logger.debug(f"Error fetching ratings for movie: {e}")

                    # Add the rating to the movie_info
                    movie_info = {
                        'title': movie.title,
                        'year': getattr(movie, 'year', None),
                        'genres': [g.tag.lower() for g in movie.genres] if hasattr(movie, 'genres') else [],
                        'directors': directors,
                        'cast': [r.tag for r in movie.roles[:3]] if hasattr(movie, 'roles') else [],
                        'summary': getattr(movie, 'summary', ''),
                        'language': self._get_movie_language(movie),
                        'tmdb_keywords': tmdb_keywords,
                        'tmdb_id': tmdb_id,
                        'imdb_id': imdb_id,
                        'rating': tmdb_rating,  # TMDB rating (0-10 scale)
                        'vote_count': tmdb_vote_count,  # TMDB vote count
                        'ratings': {
                            'audience_rating': audience_rating
                        } if audience_rating > 0 else {}
                    }
                    
                    self.cache['movies'][movie_id] = movie_info
                    
                except Exception as e:
                    log_warning(f"Error processing movie {movie.title}: {e}")
                    continue
                    
        self.cache['library_count'] = current_count
        self.cache['last_updated'] = datetime.now().isoformat()
        self._save_cache()
        print(f"\n{GREEN}Movie cache updated{RESET}")
        return True
        
    def _save_cache(self):
        """Save movie cache to file"""
        self.cache['cache_version'] = CACHE_VERSION
        save_media_cache(self.all_movies_cache_path, self.cache, 'movies')

    def _get_movie_language(self, movie) -> str:
        """Get movie's primary audio language"""
        try:
            if not movie.media:
                return "N/A"
                
            for media in movie.media:
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
            pass
        return "N/A"
			
class PlexMovieRecommender:
    """Generates personalized movie recommendations based on Plex watch history.

    Analyzes watched movies to build preference profiles based on genres, directors,
    actors, languages, and TMDB keywords. Uses similarity scoring to rank unwatched
    movies in the Plex library.
    """

    def __init__(self, config_path: str, single_user: str = None):
        """Initialize the movie recommender.

        Args:
            config_path: Path to the config.yml configuration file
            single_user: Optional username to generate recommendations for a single user
        """
        self.single_user = single_user
        self.config = load_config(config_path)
        self.library_title = self.config['plex'].get('movie_library_title', 'Movies')
        
        # Initialize counters and caches
        self.cached_watched_count = 0
        self.cached_unwatched_count = 0
        self.cached_library_movie_count = 0
        self.watched_data_counters = {}
        self.synced_movie_ids = set()
        self.cached_unwatched_movies = []
        self.plex_tmdb_cache = {}
        self.tmdb_keywords_cache = {}
        self.plex_watched_rating_keys = set()
        self.watched_movie_ids = set()
        self.label_dates = {}
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
        self.movie_cache = MovieCache(self.cache_dir, recommender=self)
        self.movie_cache.update_cache(self.plex, self.library_title, self.tmdb_api_key)
    
        self.confirm_operations = general_config.get('confirm_operations', False)
        self.limit_plex_results = general_config.get('limit_plex_results', 10)
        self.combine_watch_history = general_config.get('combine_watch_history', True)
        self.randomize_recommendations = general_config.get('randomize_recommendations', True)
        self.normalize_counters = general_config.get('normalize_counters', True)
        self.show_summary = general_config.get('show_summary', False)
        self.show_genres = general_config.get('show_genres', True)
        self.show_cast = general_config.get('show_cast', False)
        self.show_director = general_config.get('show_director', False)
        self.show_language = general_config.get('show_language', False)
        self.show_rating = general_config.get('show_rating', False)
        self.show_imdb_link = general_config.get('show_imdb_link', False)
        
        exclude_genre_str = general_config.get('exclude_genre', '')
        self.exclude_genres = [g.strip().lower() for g in exclude_genre_str.split(',') if g.strip()] if exclude_genre_str else []

        # Load user preferences for per-user customization
        self.user_preferences = self.config.get('users', {}).get('preferences', {})

        weights_config = self.config.get('weights', {})
        self.weights = {
            'genre': float(weights_config.get('genre', 0.20)),
            'director': float(weights_config.get('director', 0.15)),
            'actor': float(weights_config.get('actor', 0.15)),
            'language': float(weights_config.get('language', 0.05)),
            'keyword': float(weights_config.get('keyword', 0.45))
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
            raise ValueError(f"Movie library '{self.library_title}' not found in Plex")

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
        self.watched_cache_path = os.path.join(self.cache_dir, f"watched_cache_{safe_ctx}.json")
         
        # Load watched cache (check version first)
        watched_cache = {}
        cache_valid = check_cache_version(self.watched_cache_path, "Watched cache")
        if cache_valid and os.path.exists(self.watched_cache_path):
            try:
                with open(self.watched_cache_path, 'r', encoding='utf-8') as f:
                    watched_cache = json.load(f)
                    self.cached_watched_count = watched_cache.get('watched_count', 0)
                    self.watched_data_counters = watched_cache.get('watched_data_counters', {})
                    self.plex_tmdb_cache = {str(k): v for k, v in watched_cache.get('plex_tmdb_cache', {}).items()}
                    self.tmdb_keywords_cache = {str(k): v for k, v in watched_cache.get('tmdb_keywords_cache', {}).items()}
                    self.label_dates = watched_cache.get('label_dates', {})
                    
                    # Load watched movie IDs
                    watched_ids = watched_cache.get('watched_movie_ids', [])
                    if isinstance(watched_ids, list):
                        self.watched_movie_ids = {int(id_) for id_ in watched_ids if str(id_).isdigit()}
                    else:
                        log_warning(f"Warning: Invalid watched_movie_ids format in cache")
                        self.watched_movie_ids = set()
                    
                    if not self.watched_movie_ids and self.cached_watched_count > 0:
                        log_error(f"Warning: Cached watched count is {self.cached_watched_count} but no valid IDs loaded")
                        # Force a refresh of watched data
                        self._refresh_watched_data()
                    
            except Exception as e:
                log_warning(f"Error loading watched cache: {e}")
                self._refresh_watched_data()  
        current_library_ids = self._get_library_movies_set()
        
        # Clean up both watched movie tracking mechanisms
        self.plex_watched_rating_keys = {
            rk for rk in self.plex_watched_rating_keys 
            if int(rk) in current_library_ids
        }
        self.watched_movie_ids = {
            movie_id for movie_id in self.watched_movie_ids
            if movie_id in current_library_ids
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
            self.watched_movie_ids = set()
            if self.users['plex_users']:
                self.watched_data = self._get_plex_watched_data()
            else:
                self.watched_data = self._get_managed_users_watched_data()
            self.watched_data_counters = self.watched_data
            self.cached_watched_count = current_watched_count
            self._save_watched_cache()
        else:
            print(f"Watched count unchanged. Using cached data for {self.cached_watched_count} movies")
            self.watched_data = self.watched_data_counters
            # Ensure watched_movie_ids are preserved
            if not self.watched_movie_ids and 'watched_movie_ids' in watched_cache:
                self.watched_movie_ids = {int(id_) for id_ in watched_cache['watched_movie_ids'] if str(id_).isdigit()}
            logger.debug(f"Using cached data: {self.cached_watched_count} watched movies, {len(self.watched_movie_ids)} IDs")

        print("Fetching library metadata (for existing Movies checks)...")
        self.library_movies = self._get_library_movies_set()
        self.library_movie_titles = self._get_library_movie_titles()
        self.library_imdb_ids = self._get_library_imdb_ids()

    def _get_watched_count(self) -> int:
        """Get count of watched movies from Plex (for cache invalidation)"""
        users_to_check = [self.single_user] if self.single_user else self.users['plex_users']
        return get_watched_movie_count(self.config, users_to_check)

    def _calculate_rating_multiplier(self, user_rating):
        """Calculate rating multiplier based on user's star rating (0-10 scale in Plex)

        Rating scale:
        - 9-10 (5 stars): 1.0x weight - love it, strong preference
        - 7-8 (4 stars): 0.75x weight - like it, moderate preference
        - 5-6 (3 stars): 0.5x weight - neutral, weak preference
        - 1-4 (1-2 stars): 0.25x weight - dislike it, very weak preference
        - None/0 (unrated): 0.6x weight - default, slightly lower than neutral
        """
        if not user_rating or user_rating == 0:
            return 0.6  # Default for unrated content

        if user_rating >= 9.0:  # 5 stars
            return 1.0
        elif user_rating >= 7.0:  # 4 stars
            return 0.75
        elif user_rating >= 5.0:  # 3 stars
            return 0.5
        else:  # 1-2 stars
            return 0.25

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

    def _get_plex_watched_data(self) -> Dict:
        """Get watched movie data from Plex's native history (using Plex API)"""
        if not self.single_user and hasattr(self, 'watched_data_counters') and self.watched_data_counters:
            return self.watched_data_counters

        movies_section = self.plex.library.section(self.library_title)
        counters = create_empty_counters('movie')
        watched_movie_ids = set()
        watched_movie_dates = {}  # Store watch timestamps for recency decay
        user_ratings = {}  # Store user ratings for each movie
        watched_movie_views = {}  # Store view counts for rewatch weighting
        not_found_count = 0

        # Get account IDs for users to process
        users_to_match = [self.single_user] if self.single_user else self.users['plex_users']
        account_ids = get_plex_account_ids(self.config, users_to_match)

        if not account_ids:
            log_error(f"No valid users found!")
            return counters

        # Fetch watch history using the history API (properly per-user)
        history_items, _ = fetch_plex_watch_history_movies(self.config, account_ids, movies_section)

        # Process history items to extract IDs, dates, and ratings
        for item in history_items:
            movie_id = int(item.ratingKey)
            watched_movie_ids.add(movie_id)

            # Get watch date
            if hasattr(item, 'viewedAt') and item.viewedAt:
                viewed_at = int(item.viewedAt.timestamp())
                if movie_id not in watched_movie_dates or viewed_at > int(watched_movie_dates.get(movie_id, 0)):
                    watched_movie_dates[movie_id] = str(viewed_at)

            # Get user rating if available
            if hasattr(item, 'userRating') and item.userRating:
                user_rating = float(item.userRating)
                if movie_id not in user_ratings or user_rating > user_ratings[movie_id]:
                    user_ratings[movie_id] = user_rating

        # Get view counts from library (history API doesn't provide this)
        try:
            for movie in movies_section.all():
                movie_id = int(movie.ratingKey)
                if movie_id in watched_movie_ids and hasattr(movie, 'viewCount') and movie.viewCount:
                    watched_movie_views[movie_id] = int(movie.viewCount)
        except Exception:
            pass  # Fall back to no rewatch weighting if this fails

        print(f"Found {len(watched_movie_ids)} unique watched movies from history API")

        # Store watched movie IDs
        self.watched_movie_ids.update(watched_movie_ids)

        # Process movie metadata from cache WITH recency decay AND user rating weighting
        print(f"")
        print(f"Processing {len(watched_movie_ids)} unique watched movies with recency decay and rating weighting:")
        for i, movie_id in enumerate(watched_movie_ids, 1):
            show_progress("Processing", i, len(watched_movie_ids))

            movie_info = self.movie_cache.cache['movies'].get(str(movie_id))
            if movie_info:
                pass
                # Calculate recency multiplier for this movie
                viewed_at = watched_movie_dates.get(movie_id)
                recency_multiplier = calculate_recency_multiplier(viewed_at, self.config.get('recency_decay', {})) if viewed_at else 1.0

                # Calculate rating multiplier based on user's star rating
                rating_multiplier = self._calculate_rating_multiplier(user_ratings.get(movie_id))

                # Calculate rewatch multiplier based on view count
                rewatch_multiplier = calculate_rewatch_multiplier(watched_movie_views.get(movie_id, 1))

                # Combine all multipliers
                multiplier = recency_multiplier * rating_multiplier * rewatch_multiplier

                # Process with weighted counters
                self._process_movie_counters_from_cache(movie_info, counters, multiplier)

                if tmdb_id := movie_info.get('tmdb_id'):
                    counters['tmdb_ids'].add(tmdb_id)
            else:
                not_found_count += 1

        logger.debug(f"Watched movies not in cache: {not_found_count}, TMDB IDs collected: {len(counters['tmdb_ids'])}")

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
    
        counters = create_empty_counters('movie')

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
                
                watched_movies = user_plex.library.section(self.library_title).search(unwatched=False)
                
                print(f"\nScanning watched movies for {username}")
                for i, movie in enumerate(watched_movies, 1):
                    show_progress(f"Processing {username}'s watched", i, len(watched_movies))
                    self.watched_movie_ids.add(int(movie.ratingKey))
                    
                    movie_info = self.movie_cache.cache['movies'].get(str(movie.ratingKey))
                    if movie_info:
                        self._process_movie_counters_from_cache(movie_info, counters)
                        
                        # Explicitly add TMDB ID to the set if available
                        if tmdb_id := movie_info.get('tmdb_id'):
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
        """Save watched movie cache using utility"""
        save_watched_cache(
            cache_path=self.watched_cache_path,
            watched_data_counters=self.watched_data_counters,
            plex_tmdb_cache=self.plex_tmdb_cache,
            tmdb_keywords_cache=self.tmdb_keywords_cache,
            watched_ids=self.watched_movie_ids,
            label_dates=getattr(self, 'label_dates', {}),
            watched_count=len(self.watched_movie_ids),
            media_type='movie'
        )

    def _save_cache(self):
        self._save_watched_cache()

    def _process_movie_counters_from_cache(self, movie_info: Dict, counters: Dict, recency_multiplier: float = 1.0) -> None:
        try:
            rating = float(movie_info.get('user_rating', 0))
            if not rating:
                rating = float(movie_info.get('audience_rating', 5.0))
            rating = max(0, min(10, int(round(rating))))
            rating_multiplier = RATING_MULTIPLIERS.get(rating, 1.0)

            # Combine recency and rating multipliers
            multiplier = recency_multiplier * rating_multiplier
    
            # Process all counters using cached data
            for genre in movie_info.get('genres', []):
                counters['genres'][genre] += multiplier
            
            for director in movie_info.get('directors', []):
                counters['directors'][director] += multiplier
                
            for actor in movie_info.get('cast', [])[:3]:
                counters['actors'][actor] += multiplier
                
            if language := movie_info.get('language'):
                counters['languages'][language.lower()] += multiplier
                
            # Store TMDB data in caches if available
            if tmdb_id := movie_info.get('tmdb_id'):
                # Using the movie_id from the cache key instead of ratingKey
                movie_id = next((k for k, v in self.movie_cache.cache['movies'].items() 
                              if v.get('title') == movie_info['title'] and 
                              v.get('year') == movie_info.get('year')), None)
                if movie_id:
                    self.plex_tmdb_cache[str(movie_id)] = tmdb_id
                    if keywords := movie_info.get('tmdb_keywords', []):
                        self.tmdb_keywords_cache[str(tmdb_id)] = keywords
                        counters['tmdb_keywords'].update({k: multiplier for k in keywords})
    
        except Exception as e:
            log_warning(f"Error processing counters for {movie_info.get('title')}: {e}")
    
    def _refresh_watched_data(self):
        """Force refresh of watched data"""
        # Clear existing data to force actual refresh (prevents early returns in fetch functions)
        self.watched_data_counters = None
        self.watched_movie_ids = set()

        if self.users['plex_users']:
            self.watched_data = self._get_plex_watched_data()
        else:
            self.watched_data = self._get_managed_users_watched_data()
        self.watched_data_counters = self.watched_data
        self._save_watched_cache()

    # ------------------------------------------------------------------------
    # LIBRARY UTILITIES
    # ------------------------------------------------------------------------
    def _get_library_movies_set(self) -> Set[int]:
        """Get set of all movie IDs in the library"""
        try:
            movies = self.plex.library.section(self.library_title)
            return {int(movie.ratingKey) for movie in movies.all()}
        except Exception as e:
            log_error(f"Error getting library movies: {e}")
            return set()
    
    def _get_library_movie_titles(self) -> Set[Tuple[str, Optional[int]]]:
        """Get set of (title, year) tuples for all movies in the library"""
        try:
            movies = self.plex.library.section(self.library_title)
            return {(movie.title.lower(), getattr(movie, 'year', None)) for movie in movies.all()}
        except Exception as e:
            log_error(f"Error getting library movie titles: {e}")
            return set()
    
    def _is_movie_in_library(self, title: str, year: Optional[int], tmdb_id: Optional[int] = None, imdb_id: Optional[str] = None) -> bool:
        """Check if a movie is already in the library by ID first, then by title/year"""
        # If no title provided, we can only check by ID
        if not title:
            pass
            # Check IDs if available
            if tmdb_id or imdb_id:
                all_movies = self.movie_cache.cache['movies']
                
                for movie_id, movie_data in all_movies.items():
                    # Check TMDb ID match
                    if tmdb_id and movie_data.get('tmdb_id') and str(movie_data['tmdb_id']) == str(tmdb_id):
                        logger.debug(f"Movie in library (TMDB match): {title} [{tmdb_id}]")
                        return True

                    # Check IMDb ID match
                    if imdb_id and movie_data.get('imdb_id') and movie_data['imdb_id'] == imdb_id:
                        logger.debug(f"Movie in library (IMDB match): {title} [{imdb_id}]")
                        return True
            return False

        # Convert title to lowercase for comparison
        title_lower = title.lower()

        # Check IDs which are most reliable
        if tmdb_id or imdb_id:
            all_movies = self.movie_cache.cache['movies']

            for movie_id, movie_data in all_movies.items():
                # Check TMDb ID match
                if tmdb_id and movie_data.get('tmdb_id') and str(movie_data['tmdb_id']) == str(tmdb_id):
                    logger.debug(f"Movie in library (TMDB match via cache): {title} [{tmdb_id}]")
                    return True

                # Check IMDb ID match
                if imdb_id and movie_data.get('imdb_id') and movie_data['imdb_id'] == imdb_id:
                    logger.debug(f"Movie in library (IMDB match via cache): {title} [{imdb_id}]")
                    return True
        
        # If no ID match, fall back to title matching
        
        # Initialize library_movie_titles if not already done
        if not hasattr(self, 'library_movie_titles'):
            self.library_movie_titles = self._get_library_movie_titles()
        
        # Check for year in title and strip it if found
        year_match = re.search(r'\s*\((\d{4})\)$', title_lower)
        if year_match:
            clean_title = title_lower.replace(year_match.group(0), '').strip()
            embedded_year = int(year_match.group(1))
            if (clean_title, embedded_year) in self.library_movie_titles:
                return True
        
        # Check both with and without year
        if (title_lower, year) in self.library_movie_titles:
            return True
            
        # Check title-only matches
        return any(lib_title == title_lower or 
                  lib_title == f"{title_lower} ({year})" or
                  lib_title.replace(f" ({year})", "") == title_lower 
                  for lib_title, lib_year in self.library_movie_titles)
    
    def _process_movie_counters(self, movie, counters):
        """Extract and count attributes from a movie"""
        movie_details = self.get_movie_details(movie)
        
        try:
            rating = float(getattr(movie, 'userRating', 0))
        except (TypeError, ValueError):
            try:
                rating = float(getattr(movie, 'audienceRating', 5.0))
            except (TypeError, ValueError):
                rating = 5.0
    
        rating = max(0, min(10, int(round(rating))))
        multiplier = RATING_MULTIPLIERS.get(rating, 1.0)
    
        # Process all the existing counters...
        for genre in movie_details.get('genres', []):
            counters['genres'][genre] += multiplier
        
        for director in movie_details.get('directors', []):
            counters['directors'][director] += multiplier
            
        for actor in movie_details.get('cast', [])[:3]:
            counters['actors'][actor] += multiplier
            
        if language := movie_details.get('language'):
            counters['languages'][language.lower()] += multiplier
            
        for keyword in movie_details.get('tmdb_keywords', []):
            counters['tmdb_keywords'][keyword] += multiplier
    
        # Get TMDB ID if available
        if 'tmdb_id' in movie_details and movie_details['tmdb_id']:
            if 'tmdb_ids' not in counters:
                counters['tmdb_ids'] = set()
            counters['tmdb_ids'].add(movie_details['tmdb_id'])
            
            # Store in cache for future use
            self.plex_tmdb_cache[str(movie.ratingKey)] = movie_details['tmdb_id']
            
            # Store keywords in cache if available
            if 'tmdb_keywords' in movie_details and movie_details['tmdb_keywords']:
                self.tmdb_keywords_cache[str(movie_details['tmdb_id'])] = movie_details['tmdb_keywords']
       
    def _get_library_imdb_ids(self) -> Set[str]:
        """Get set of all IMDb IDs in the library"""
        return get_library_imdb_ids(self.plex.library.section(self.library_title))
    
    def get_movie_details(self, movie) -> Dict:
        """Extract comprehensive details from a movie object"""
        try:
            movie.reload()

            # Extract IDs using utility
            ids = extract_ids_from_guids(movie)
            imdb_id = ids['imdb_id']
            audience_rating = 0
            tmdb_keywords = []
            directors = []
            
            # Improved rating extraction logic
            if self.show_rating:
                pass
                # Try to get userRating first (personal rating)
                if hasattr(movie, 'userRating') and movie.userRating:
                    audience_rating = float(movie.userRating)
                # Then try audienceRating (community rating)
                elif hasattr(movie, 'audienceRating') and movie.audienceRating:
                    audience_rating = float(movie.audienceRating)
                # Finally check ratings collection
                elif hasattr(movie, 'ratings'):
                    for rating in movie.ratings:
                        if hasattr(rating, 'value') and rating.value:
                            if (getattr(rating, 'image', '') == 'imdb://image.rating' or
                                getattr(rating, 'type', '') == 'audience'):
                                try:
                                    audience_rating = float(rating.value)
                                    break
                                except (ValueError, AttributeError):
                                    pass
            
            if hasattr(movie, 'directors') and movie.directors:
                directors = [d.tag for d in movie.directors]
                            
            if self.use_tmdb_keywords and self.tmdb_api_key:
                tmdb_id = self._get_plex_movie_tmdb_id(movie)
                if tmdb_id:
                    tmdb_keywords = list(self._get_tmdb_keywords_for_id(tmdb_id))
            
            movie_info = {
                'title': movie.title,
                'year': getattr(movie, 'year', None),
                'genres': self._extract_genres(movie),
                'summary': getattr(movie, 'summary', ''),
                'directors': directors,
                'language': self._get_movie_language(movie),
                'imdb_id': imdb_id,
                'ratings': {
                    'audience_rating': audience_rating
                } if audience_rating > 0 else {},
                'cast': [],
                'tmdb_keywords': tmdb_keywords
            }
            
            if self.show_cast and hasattr(movie, 'roles'):
                movie_info['cast'] = [r.tag for r in movie.roles[:3]]
                
            return movie_info
                
        except Exception as e:
            log_warning(f"Error getting movie details for {movie.title}: {e}")
            return {}
    
    def _extract_genres(self, movie) -> List[str]:
        """Extract genres from a movie"""
        return extract_genres(movie)
    
    def _get_movie_language(self, movie) -> str:
        """Get movie's primary audio language - delegates to MovieCache"""
        return self.movie_cache._get_movie_language(movie)

    # ------------------------------------------------------------------------
    # TMDB HELPER METHODS
    # ------------------------------------------------------------------------
    def _get_tmdb_id_via_imdb(self, plex_movie) -> Optional[int]:
        """Get TMDB ID using IMDb ID as a fallback method"""
        imdb_id = self._get_plex_movie_imdb_id(plex_movie)
        if not imdb_id or not self.tmdb_api_key:
            return None

        data = fetch_tmdb_with_retry(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            {'api_key': self.tmdb_api_key, 'external_source': 'imdb_id'}
        )
        if data:
            results = data.get('movie_results', [])
            if results:
                return results[0].get('id')
        return None
    
    def _get_plex_movie_tmdb_id(self, plex_movie) -> Optional[int]:
        """Get TMDB ID for a Plex movie with multiple fallback methods"""
        # Check cache first
        cache_key = str(plex_movie.ratingKey)
        if cache_key in self.plex_tmdb_cache:
            return self.plex_tmdb_cache[cache_key]

        # Use consolidated utility for TMDB ID lookup
        tmdb_id = get_tmdb_id_for_item(plex_movie, self.tmdb_api_key, 'movie', self.plex_tmdb_cache)

        # Update cache if found
        if tmdb_id:
            self.plex_tmdb_cache[cache_key] = tmdb_id
            self._save_watched_cache()
        return tmdb_id
    
    def _get_plex_movie_imdb_id(self, plex_movie) -> Optional[str]:
        """Get IMDb ID for a Plex movie with fallback to TMDB"""
        # Try extracting from GUIDs first using utility
        ids = extract_ids_from_guids(plex_movie)
        if ids['imdb_id']:
            return ids['imdb_id']

        # Fallback: Check legacy guid attribute
        if hasattr(plex_movie, 'guid') and plex_movie.guid and plex_movie.guid.startswith('imdb://'):
            return plex_movie.guid.split('imdb://')[1]

        # Fallback to TMDB to get IMDb ID
        tmdb_id = self._get_plex_movie_tmdb_id(plex_movie)
        if tmdb_id:
            return self._get_imdb_id_from_tmdb(tmdb_id)
        return None
    
    def _get_tmdb_keywords_for_id(self, tmdb_id: int) -> Set[str]:
        """Get keywords for a movie from TMDB"""
        if not tmdb_id or not self.use_tmdb_keywords or not self.tmdb_api_key:
            return set()

        # Use consolidated utility with local cache
        keywords = get_tmdb_keywords(self.tmdb_api_key, tmdb_id, 'movie', self.tmdb_keywords_cache)
        if keywords:
            self._save_watched_cache()
        return set(keywords)

    def _get_imdb_id_from_tmdb(self, tmdb_id: int) -> Optional[str]:
        """Get IMDb ID directly from TMDB"""
        data = fetch_tmdb_with_retry(
            f"https://api.themoviedb.org/3/movie/{tmdb_id}",
            {'api_key': self.tmdb_api_key}
        )
        return data.get('imdb_id') if data else None

    # ------------------------------------------------------------------------
    # CALCULATE SCORES
    # ------------------------------------------------------------------------
    def _calculate_similarity_from_cache(self, movie_info: Dict) -> Tuple[float, Dict]:
        """Calculate similarity score using cached movie data and return score with breakdown"""
        # Build user profile from watched data
        user_profile = {
            'genres': self.watched_data.get('genres', {}),
            'directors': self.watched_data.get('directors', {}),
            'actors': self.watched_data.get('actors', {}),
            'languages': self.watched_data.get('languages', {}),
            'keywords': self.watched_data.get('tmdb_keywords', {})
        }

        # Build content info dict
        content_info = {
            'genres': movie_info.get('genres', []),
            'directors': movie_info.get('directors', []),
            'cast': movie_info.get('cast', []),
            'language': movie_info.get('language', 'N/A'),
            'keywords': movie_info.get('tmdb_keywords', [])
        }

        # Use shared scoring function
        return calculate_similarity_score(
            content_info=content_info,
            user_profile=user_profile,
            media_type='movie',
            weights=self.weights,
            normalize_counters=self.normalize_counters,
            use_fuzzy_keywords=self.use_tmdb_keywords
        )
    
    def _print_similarity_breakdown(self, movie_info: Dict, score: float, breakdown: Dict):
        """Print detailed breakdown of similarity score calculation"""
        print_similarity_breakdown(movie_info, score, breakdown, 'movie')

    # ------------------------------------------------------------------------
    # GET RECOMMENDATIONS
    # ------------------------------------------------------------------------
    def get_recommendations(self) -> Dict[str, List[Dict]]:
        if self.cached_watched_count > 0 and not self.watched_movie_ids:
            # Force refresh of watched data
            if self.users['plex_users']:
                self.watched_data = self._get_plex_watched_data()
            else:
                self.watched_data = self._get_managed_users_watched_data()
            self.watched_data_counters = self.watched_data
            self._save_watched_cache()

        # Get all movies from cache
        all_movies = self.movie_cache.cache['movies']
        
        print(f"\n{YELLOW}Processing recommendations...{RESET}")
        
        # Filter out watched movies and excluded genres
        unwatched_movies = []
        excluded_count = 0
        quality_filtered_count = 0
        watched_count = 0

        # Get quality filters from config (Netflix-style)
        quality_filters = self.config.get('quality_filters', {})
        min_rating = quality_filters.get('min_rating', 0.0)
        min_vote_count = quality_filters.get('min_vote_count', 0)


        for movie_id, movie_info in all_movies.items():
            # Skip if movie is watched
            movie_id_int = int(str(movie_id))
            if movie_id_int in self.watched_movie_ids:
                watched_count += 1
                continue

            # Skip if movie has excluded genres (including user-specific exclusions)
            excluded_genres = get_excluded_genres_for_user(self.exclude_genres, self.user_preferences, self.single_user)
            if excluded_count == 0:  # Debug: print excluded genres once
                pass
            movie_genres = movie_info.get('genres', [])
            if any(g.lower() in excluded_genres for g in movie_genres):
                excluded_count += 1
                continue

            # Netflix-style quality filters (no year restriction - recency bias via watch dates)
            rating = movie_info.get('rating') or 0.0
            vote_count = movie_info.get('vote_count') or 0

            # Skip if movie doesn't meet quality thresholds
            if rating < min_rating or vote_count < min_vote_count:
                quality_filtered_count += 1
                continue

            unwatched_movies.append(movie_info)

        if excluded_count > 0:
            print(f"Excluded {excluded_count} movies based on genre filters")
        if quality_filtered_count > 0:
            log_warning(f"Filtered {quality_filtered_count} movies below quality thresholds (rating: {min_rating}+, votes: {min_vote_count}+)")
    
        if not unwatched_movies:
            log_warning(f"No unwatched movies found matching your criteria.")
            plex_recs = []
        else:
            print(f"Calculating similarity scores for {len(unwatched_movies)} movies...")
            
            # Calculate similarity scores
            scored_movies = []
            for i, movie_info in enumerate(unwatched_movies, 1):
                show_progress("Processing", i, len(unwatched_movies))
                try:
                    similarity_score, breakdown = self._calculate_similarity_from_cache(movie_info)
                    movie_info['similarity_score'] = similarity_score
                    movie_info['score_breakdown'] = breakdown
                    scored_movies.append(movie_info)
                except Exception as e:
                    log_warning(f"Error processing {movie_info['title']}: {e}")
                    continue
            
            # Sort by similarity score
            scored_movies.sort(key=lambda x: x['similarity_score'], reverse=True)
            
            if self.randomize_recommendations:
                pass
                # Take top 10% of movies by similarity score and randomize
                top_count = max(int(len(scored_movies) * 0.1), self.limit_plex_results)
                top_pool = scored_movies[:top_count]
                plex_recs = random.sample(top_pool, min(self.limit_plex_results, len(top_pool)))
            else:
                # Take top movies directly by similarity score
                plex_recs = scored_movies[:self.limit_plex_results]
            
            # Print detailed breakdowns for final recommendations if debug is enabled
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("=== Similarity Score Breakdowns for Recommendations ===")
                for movie in plex_recs:
                    self._print_similarity_breakdown(movie, movie['similarity_score'], movie['score_breakdown'])

        print(f"\nRecommendation process completed!")
        return {
            'plex_recommendations': plex_recs
        }
    
    def _user_select_recommendations(self, recommended_movies: List[Dict], operation_label: str) -> List[Dict]:
        """Prompt user to select recommendations - delegates to utility"""
        return user_select_recommendations(recommended_movies, operation_label)

    # ------------------------------------------------------------------------
    # PLEX LABELS
    # ------------------------------------------------------------------------
    def manage_plex_labels(self, recommended_movies: List[Dict]) -> None:
        if not self.config.get('collections', {}).get('add_label'):
            return

        # Ensure recommended_movies is always a list (even if empty)
        recommended_movies = recommended_movies or []

        if self.confirm_operations and recommended_movies:
            selected_movies = self._user_select_recommendations(recommended_movies, "label in Plex")
            if not selected_movies:
                selected_movies = []
        else:
            selected_movies = recommended_movies

        try:
            movies_section = self.plex.library.section(self.library_title)
            base_label = self.config.get('collections', {}).get('label_name', 'Recommended')
            append_usernames = self.config.get('collections', {}).get('append_usernames', False)
            users = self.users['plex_users'] or self.users['managed_users']
            label_name = build_label_name(base_label, users, self.single_user, append_usernames)

            # Find new movies in Plex (if any were recommended)
            movies_to_update = []
            skipped_movies = []
            for rec in selected_movies:
                # Use fuzzy matching to handle titles like "Jason Bourne 4K"
                plex_movie = find_plex_movie(movies_section, rec['title'], rec.get('year'))
                if plex_movie:
                    plex_movie.reload()
                    movies_to_update.append(plex_movie)
                else:
                    skipped_movies.append(f"{rec['title']} ({rec.get('year', 'N/A')})")

            if skipped_movies:
                log_warning(f"Skipped {len(skipped_movies)} movies not found in Plex:")
                for movie in skipped_movies[:5]:  # Show first 5
                    print(f"  - {movie}")
                if len(skipped_movies) > 5:
                    print(f"  ... and {len(skipped_movies) - 5} more")

            # ALWAYS run cleanup to remove watched/stale movies, even if no new recommendations
            # INCREMENTAL UPDATE: Keep unwatched (and fresh), remove watched and stale, fill gaps
            print(f"{GREEN}Starting incremental collection update with staleness check...{RESET}")

            # Load label dates from cache (track when each label was added)
            if not hasattr(self, 'label_dates') or not self.label_dates:
                self.label_dates = {}

            # Get staleness threshold from config
            stale_days = self.config.get('collections', {}).get('stale_removal_days', 7)

            # Get currently labeled movies
            currently_labeled = movies_section.search(label=label_name)
            print(f"Found {len(currently_labeled)} currently labeled movies")

            # Get excluded genres for this user
            excluded_genres = get_excluded_genres_for_user(self.exclude_genres, self.user_preferences, self.single_user)

            # Categorize labeled items using utility
            categories = categorize_labeled_items(
                currently_labeled, self.watched_movie_ids, excluded_genres,
                label_name, self.label_dates, stale_days
            )
            unwatched_labeled = categories['fresh']
            watched_labeled = categories['watched']
            stale_labeled = categories['stale']
            excluded_labeled = categories['excluded']

            print(f"{GREEN}Keeping {len(unwatched_labeled)} fresh unwatched recommendations{RESET}")
            print(f"{YELLOW}Removing {len(watched_labeled)} watched movies from recommendations{RESET}")
            print(f"{YELLOW}Removing {len(stale_labeled)} stale recommendations (unwatched > {stale_days} days){RESET}")
            print(f"{YELLOW}Removing {len(excluded_labeled)} movies with excluded genres{RESET}")

            # Remove labels using utilities
            remove_labels_from_items(watched_labeled, label_name, self.label_dates, "watched")
            remove_labels_from_items(stale_labeled, label_name, self.label_dates, "stale")
            remove_labels_from_items(excluded_labeled, label_name, self.label_dates, "excluded genre")

            # Get target count from config
            target_count = self.config['general'].get('limit_plex_results', 50)

            print(f"{GREEN}Building optimal collection of top {target_count} recommendations...{RESET}")

            # Score ALL candidates: existing unwatched + new recommendations
            all_candidates = {}  # movie_id -> (plex_movie, score)

            # Score existing unwatched items
            for movie in unwatched_labeled:
                movie_id = int(movie.ratingKey)
                movie_info = self.movie_cache.cache['movies'].get(str(movie_id))
                if movie_info:
                    try:
                        score, _ = self._calculate_similarity_from_cache(movie_info)
                        all_candidates[movie_id] = (movie, score)
                    except Exception:
                        all_candidates[movie_id] = (movie, 0.0)

            # Score new recommendations
            for rec in selected_movies:
                plex_movie = next(
                    (m for m in movies_to_update if m.title == rec['title'] and m.year == rec.get('year')),
                    None
                )
                if plex_movie:
                    movie_id = int(plex_movie.ratingKey)
                    if movie_id not in self.watched_movie_ids:
                        score = rec.get('similarity_score', 0.0)
                        # Keep higher score if already exists
                        if movie_id not in all_candidates or score > all_candidates[movie_id][1]:
                            all_candidates[movie_id] = (plex_movie, score)

            # Sort by score and take top N
            sorted_candidates = sorted(all_candidates.items(), key=lambda x: x[1][1], reverse=True)
            top_candidates = sorted_candidates[:target_count]
            top_ids = {movie_id for movie_id, _ in top_candidates}

            # Determine what to add and remove
            current_ids = {int(m.ratingKey) for m in unwatched_labeled}
            ids_to_add = top_ids - current_ids
            ids_to_remove = current_ids - top_ids

            # Remove items that didn't make the cut
            if ids_to_remove:
                movies_to_remove = [m for m in unwatched_labeled if int(m.ratingKey) in ids_to_remove]
                print(f"{YELLOW}Removing {len(movies_to_remove)} lower-scoring items to make room for better ones{RESET}")
                remove_labels_from_items(movies_to_remove, label_name, self.label_dates, "replaced by higher score")

            # Add new high-scoring items
            movies_to_add = [all_candidates[mid][0] for mid in ids_to_add if mid in all_candidates]
            if movies_to_add:
                print(f"{GREEN}Adding {len(movies_to_add)} new high-scoring recommendations{RESET}")
                add_labels_to_items(movies_to_add, label_name, self.label_dates)

            print(f"{GREEN}Collection now has top {len(top_candidates)} recommendations by score{RESET}")

            # Build final collection from top candidates (already sorted by score)
            similarity_scores = {movie_id: score for movie_id, (_, score) in top_candidates}
            final_collection_movies = [plex_movie for movie_id, (plex_movie, score) in top_candidates]

            print(f"{GREEN}Final collection size: {len(final_collection_movies)} movies (sorted by similarity){RESET}")
            print(f"{GREEN}Successfully updated labels incrementally{RESET}")

            # Update the Plex collection with sorted movies
            if final_collection_movies:
                # Get display name for collection title
                username = label_name.replace('Recommended_', '')
                if username in self.user_preferences and 'display_name' in self.user_preferences[username]:
                    display_name = self.user_preferences[username]['display_name']
                else:
                    display_name = username.capitalize()

                collection_name = f"🎬 {display_name} - Recommendation"
                update_plex_collection(movies_section, collection_name, final_collection_movies, logger)

                # Clean up old collection naming patterns for this user
                cleanup_old_collections(movies_section, collection_name, username, "🎬", logger)

        except Exception as e:
            log_error(f"Error managing Plex labels: {e}")
            import traceback
            print(traceback.format_exc())


# ------------------------------------------------------------------------
# OUTPUT FORMATTING
# ------------------------------------------------------------------------
def format_movie_output(movie: Dict,
                      show_summary: bool = False,
                      index: Optional[int] = None,
                      show_cast: bool = False,
                      show_director: bool = False,
                      show_language: bool = False,
                      show_rating: bool = False,
                      show_genres: bool = True,
                      show_imdb_link: bool = False) -> str:
    """Format movie for display - delegates to shared utility"""
    return format_media_output(
        media=movie,
        media_type='movie',
        show_summary=show_summary,
        index=index,
        show_cast=show_cast,
        show_director=show_director,
        show_language=show_language,
        show_rating=show_rating,
        show_genres=show_genres,
        show_imdb_link=show_imdb_link
    )


# ------------------------------------------------------------------------
# CONFIG ADAPTER
# ------------------------------------------------------------------------
def adapt_root_config_to_legacy(root_config):
    """Convert root config.yml format to legacy MRFP format"""
    return adapt_config_for_media_type(root_config, 'movies')

# ------------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------------
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
        recommender = PlexMovieRecommender(config_path, single_user=single_user)
        
        # Check for debug mode
        if config.get('general', {}).get('debug', False):
            recommender.debug = True
        
        recommendations = recommender.get_recommendations()
        
        print(f"\n{GREEN}=== Recommended Unwatched Movies in Your Library ==={RESET}")
        plex_recs = recommendations.get('plex_recommendations', [])
        if plex_recs:
            for i, movie in enumerate(plex_recs, start=1):
                print(format_movie_output(
                    movie,
                    show_summary=recommender.show_summary,
                    index=i,
                    show_cast=recommender.show_cast,
                    show_director=recommender.show_director,
                    show_language=recommender.show_language,
                    show_rating=recommender.show_rating,
                    show_genres=recommender.show_genres,
                    show_imdb_link=recommender.show_imdb_link
                ))
                print()
            recommender.manage_plex_labels(plex_recs)
        else:
            log_warning(f"No recommendations found in your Plex library matching your criteria.")

        recommender._save_cache()

    except Exception as e:
        print(f"\n{RED}An error occurred: {e}{RESET}")
        import traceback
        print(traceback.format_exc())

        # Check if this is a fatal error that should stop all processing
        error_msg = str(e).lower()
        fatal_keywords = ['connection', 'plex server', 'unauthorized', 'authentication', 'config']
        is_fatal = any(keyword in error_msg for keyword in fatal_keywords)

        if is_fatal:
            log_error(f"Fatal error detected - stopping execution")
            sys.exit(1)

    finally:
        if log_retention_days > 0 and sys.stdout is not original_stdout:
            try:
                sys.stdout.logfile.close()
                sys.stdout = original_stdout
            except Exception as e:
                log_warning(f"Error closing log file: {e}")

def main():
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Movie Recommendations for Plex')
    parser.add_argument('username', nargs='?', help='Process recommendations for only this user')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    start_time = datetime.now()
    print(f"{CYAN}Movie Recommendations for Plex v{__version__}{RESET}")
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

    # If single user specified via command line, override the user list
    if single_user:
        all_users = [single_user]
        combine_watch_history = True  # Force combined mode for single user

    if combine_watch_history or not all_users:
        pass
        # Original behavior - single run
        process_recommendations(base_config, config_path, log_retention_days, single_user)
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
            process_recommendations(user_config, config_path, log_retention_days, resolved_user)
            print(f"\n{GREEN}Completed processing for user: {resolved_user}{RESET}")
            print("-" * 50)

    runtime = datetime.now() - start_time
    hours = runtime.seconds // 3600
    minutes = (runtime.seconds % 3600) // 60
    seconds = runtime.seconds % 60
    print(f"\n{GREEN}All processing completed!{RESET}")
    print(f"Total runtime: {hours:02d}:{minutes:02d}:{seconds:02d}")

if __name__ == "__main__":
    main()