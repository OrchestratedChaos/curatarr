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
from urllib.parse import quote
import re
from datetime import datetime, timedelta
import math
import copy

# Import shared utilities
from utils import (
    RED, GREEN, YELLOW, CYAN, RESET,
    RATING_MULTIPLIERS, ANSI_PATTERN,
    get_full_language_name, cleanup_old_logs, setup_logging,
    get_plex_account_ids, fetch_plex_watch_history_movies, get_watched_movie_count,
    log_warning, log_error, update_plex_collection, cleanup_old_collections,
    load_config, init_plex, get_configured_users, get_current_users,
    get_excluded_genres_for_user, get_user_specific_connection,
    calculate_recency_multiplier, calculate_rewatch_multiplier,
    map_path, show_progress, TeeLogger
)

# Module-level logger - configured by setup_logging() in main()
logger = logging.getLogger('plex_recommender')

__version__ = "1.0.0"

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
        if os.path.exists(self.all_movies_cache_path):
            try:
                with open(self.all_movies_cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                log_warning(f"Error loading all movies cache: {e}")
                return {'movies': {}, 'last_updated': None, 'library_count': 0}
        return {'movies': {}, 'last_updated': None, 'library_count': 0}
    
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
                    
                    imdb_id = None
                    tmdb_id = None
                    if hasattr(movie, 'guids'):
                        for guid in movie.guids:
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
                                    'query': movie.title,
                                    'year': getattr(movie, 'year', None)
                                }
                                resp = requests.get(
                                    "https://api.themoviedb.org/3/search/movie",
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
                                    log_warning(f"Failed to get TMDB ID for {movie.title} after {max_retries} tries")
                            except Exception as e:
                                log_warning(f"Error getting TMDB ID for {movie.title}: {e}")
                                break
    
                    # Fetch TMDB metadata (rating, votes, keywords)
                    tmdb_keywords = []
                    tmdb_rating = None
                    tmdb_vote_count = None

                    if tmdb_id and tmdb_api_key:
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                # Get movie details (includes rating and vote_count)
                                detail_resp = requests.get(
                                    f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                                    params={'api_key': tmdb_api_key},
                                    timeout=15
                                )

                                if detail_resp.status_code == 429:
                                    sleep_time = 2 * (attempt + 1)
                                    log_warning(f"TMDB rate limit hit, waiting {sleep_time}s...")
                                    time.sleep(sleep_time)
                                    continue

                                if detail_resp.status_code == 200:
                                    detail_data = detail_resp.json()
                                    tmdb_rating = detail_data.get('vote_average')
                                    tmdb_vote_count = detail_data.get('vote_count')
                                    break

                            except (requests.exceptions.ConnectionError,
                                   requests.exceptions.Timeout,
                                   requests.exceptions.ChunkedEncodingError) as e:
                                log_warning(f"Connection error, retrying... ({attempt+1}/{max_retries})")
                                time.sleep(1)
                            except Exception as e:
                                log_warning(f"Error getting TMDB details for {movie.title}: {e}")
                                break

                        # Get keywords
                        for attempt in range(max_retries):
                            try:
                                kw_resp = requests.get(
                                    f"https://api.themoviedb.org/3/movie/{tmdb_id}/keywords",
                                    params={'api_key': tmdb_api_key},
                                    timeout=15
                                )

                                if kw_resp.status_code == 429:
                                    sleep_time = 2 * (attempt + 1)
                                    log_warning(f"TMDB rate limit hit, waiting {sleep_time}s...")
                                    time.sleep(sleep_time)
                                    continue

                                if kw_resp.status_code == 200:
                                    keywords = kw_resp.json().get('keywords', [])
                                    tmdb_keywords = [k['name'].lower() for k in keywords]
                                    break

                            except (requests.exceptions.ConnectionError,
                                   requests.exceptions.Timeout,
                                   requests.exceptions.ChunkedEncodingError) as e:
                                log_warning(f"Connection error, retrying... ({attempt+1}/{max_retries})")
                                time.sleep(1)
                                if attempt == max_retries - 1:
                                    log_warning(f"Failed to get keywords for {movie.title} after {max_retries} tries")
                            except Exception as e:
                                log_warning(f"Error getting TMDB keywords for {movie.title}: {e}")
                                break
    
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
        try:
            with open(self.all_movies_cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=4, ensure_ascii=False)
        except Exception as e:
            log_error(f"Error saving all movies cache: {e}")

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
        tmdb_config = self.config.get('TMDB', {})
        self.use_tmdb_keywords = tmdb_config.get('use_TMDB_keywords', True)
        self.tmdb_api_key = tmdb_config.get('api_key', None)
        
        self.cache_dir = os.path.join(os.path.dirname(__file__), 'cache')
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
            'genre_weight': float(weights_config.get('genre_weight', 0.25)),
            'director_weight': float(weights_config.get('director_weight', 0.20)),
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
         
        # Load watched cache 
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
        counters = {
            'genres': Counter(),
            'directors': Counter(),
            'actors': Counter(),
            'languages': Counter(),
            'tmdb_keywords': Counter(),
            'tmdb_ids': set()
        }
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
    
        counters = {
            'genres': Counter(),
            'directors': Counter(),
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
        try:
            # Create a copy of the watched data to modify for serialization
            watched_data_for_cache = copy.deepcopy(self.watched_data_counters)
            
            # Convert any set objects to lists for JSON serialization
            if 'tmdb_ids' in watched_data_for_cache and isinstance(watched_data_for_cache['tmdb_ids'], set):
                watched_data_for_cache['tmdb_ids'] = list(watched_data_for_cache['tmdb_ids'])
            
            cache_data = {
                'watched_count': len(self.watched_movie_ids),  # Save actual count of watched movies
                'watched_data_counters': watched_data_for_cache,
                'plex_tmdb_cache': {str(k): v for k, v in self.plex_tmdb_cache.items()},
                'tmdb_keywords_cache': {str(k): v for k, v in self.tmdb_keywords_cache.items()},
                'watched_movie_ids': list(self.watched_movie_ids),
                'label_dates': self.label_dates if hasattr(self, 'label_dates') else {},
                'last_updated': datetime.now().isoformat()
            }
            
            with open(self.watched_cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=4, ensure_ascii=False)

            logger.debug(f"Saved watched cache: {self.cached_watched_count} movies, {len(self.watched_movie_ids)} IDs")

        except Exception as e:
            log_warning(f"Error saving watched cache: {e}")

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
        imdb_ids = set()
        try:
            movies = self.plex.library.section(self.library_title).all()
            for movie in movies:
                if hasattr(movie, 'guids'):
                    for guid in movie.guids:
                        if guid.id.startswith('imdb://'):
                            imdb_ids.add(guid.id.replace('imdb://', ''))
                            break
        except Exception as e:
            log_warning(f"Error retrieving IMDb IDs from library: {e}")
        return imdb_ids
    
    def get_movie_details(self, movie) -> Dict:
        """Extract comprehensive details from a movie object"""
        try:
            movie.reload()
            
            imdb_id = None
            audience_rating = 0
            tmdb_keywords = []
            directors = []
            
            if hasattr(movie, 'guids'):
                for guid in movie.guids:
                    if 'imdb://' in guid.id:
                        imdb_id = guid.id.replace('imdb://', '')
                        break
            
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
        genres = []
        try:
            if not hasattr(movie, 'genres') or not movie.genres:
                return genres
                
            for genre in movie.genres:
                if isinstance(genre, plexapi.media.Genre):
                    if hasattr(genre, 'tag'):
                        genres.append(genre.tag.lower())
                elif isinstance(genre, str):
                    genres.append(genre.lower())
                else:
                    pass
        except Exception as e:
            pass  # Empty except block
        return genres
    
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
    
        try:
            url = f"https://api.themoviedb.org/3/find/{imdb_id}"
            params = {'api_key': self.tmdb_api_key, 'external_source': 'imdb_id'}
            resp = requests.get(url, params=params)
            resp.raise_for_status()
            return resp.json().get('movie_results', [{}])[0].get('id')
        except Exception as e:
            log_warning(f"IMDb fallback failed: {e}")
            return None
    
    def _get_plex_movie_tmdb_id(self, plex_movie) -> Optional[int]:
        """Get TMDB ID for a Plex movie with multiple fallback methods"""
        # Recursion guard and cache check
        if hasattr(plex_movie, '_tmdb_fallback_attempted'):
            return self.plex_tmdb_cache.get(plex_movie.ratingKey)
        
        if plex_movie.ratingKey in self.plex_tmdb_cache:
            return self.plex_tmdb_cache[plex_movie.ratingKey]
    
        tmdb_id = None
        movie_title = plex_movie.title
        movie_year = getattr(plex_movie, 'year', None)
    
        # Method 1: Check Plex GUIDs
        if hasattr(plex_movie, 'guids'):
            for guid in plex_movie.guids:
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
                    'query': movie_title,
                    'include_adult': False
                }
                if movie_year:
                    params['year'] = movie_year
    
                resp = requests.get(
                    "https://api.themoviedb.org/3/search/movie",
                    params=params,
                    timeout=10
                )
                resp.raise_for_status()
                
                results = resp.json().get('results', [])
                if results:
                    exact_match = next(
                        (r for r in results 
                         if r.get('title', '').lower() == movie_title.lower()
                         and str(r.get('release_date', '')[:4]) == str(movie_year)),
                        None
                    )
                    
                    tmdb_id = exact_match['id'] if exact_match else results[0]['id']
    
            except Exception as e:
                log_warning(f"TMDB search failed for {movie_title}: {e}")
    
        # Method 3: Single Fallback Attempt via IMDb
        if not tmdb_id and not hasattr(plex_movie, '_tmdb_fallback_attempted'):
            plex_movie._tmdb_fallback_attempted = True
            tmdb_id = self._get_tmdb_id_via_imdb(plex_movie)
    
        # Update cache even if None to prevent repeat lookups
        if tmdb_id:
            logger.debug(f"Cached TMDB ID {tmdb_id} for Plex movie {plex_movie.ratingKey}")
            self.plex_tmdb_cache[str(plex_movie.ratingKey)] = tmdb_id
            self._save_watched_cache()
        return tmdb_id
    
    def _get_plex_movie_imdb_id(self, plex_movie) -> Optional[str]:
        """Get IMDb ID for a Plex movie with fallback to TMDB"""
        if not plex_movie.guid:
            return None
        guid = plex_movie.guid
        if guid.startswith('imdb://'):
            return guid.split('imdb://')[1]
        
        # Check in guids attribute
        if hasattr(plex_movie, 'guids'):
            for guid in plex_movie.guids:
                if guid.id.startswith('imdb://'):
                    return guid.id.replace('imdb://', '')
        
        # Fallback to TMDB
        tmdb_id = self._get_plex_movie_tmdb_id(plex_movie)
        if not tmdb_id:
            return None
        try:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            params = {'api_key': self.tmdb_api_key}
            resp = requests.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('imdb_id')
            else:
                log_warning(f"Failed to fetch IMDb ID from TMDB for movie '{plex_movie.title}'. Status Code: {resp.status_code}")
        except Exception as e:
            log_warning(f"Error fetching IMDb ID for TMDB ID {tmdb_id}: {e}")
        return None
    
    def _get_tmdb_keywords_for_id(self, tmdb_id: int) -> Set[str]:
        """Get keywords for a movie from TMDB"""
        if not tmdb_id or not self.use_tmdb_keywords or not self.tmdb_api_key:
            return set()
    
        if tmdb_id in self.tmdb_keywords_cache:
            return set(self.tmdb_keywords_cache[tmdb_id])
    
        kw_set = set()
        try:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/keywords"
            params = {'api_key': self.tmdb_api_key}
            resp = requests.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                keywords = data.get('keywords', [])
                kw_set = {k['name'].lower() for k in keywords}
        except Exception as e:
            log_warning(f"Error fetching TMDB keywords for ID {tmdb_id}: {e}")
    
        if kw_set:
            logger.debug(f"Cached {len(kw_set)} keywords for TMDB ID {tmdb_id}")
            self.tmdb_keywords_cache[str(tmdb_id)] = list(kw_set)  # Convert key to string
            self._save_watched_cache()
        return kw_set

    def _get_imdb_id_from_tmdb(self, tmdb_id: int) -> Optional[str]:
        """Get IMDb ID directly from TMDB"""
        try:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            params = {'api_key': self.tmdb_api_key}
            response = requests.get(url, params=params)
            if response.status_code == 200:
                return response.json().get('imdb_id')
        except Exception as e:
            log_warning(f"TMDB API Error: {e}")
        return None

    # ------------------------------------------------------------------------
    # CALCULATE SCORES
    # ------------------------------------------------------------------------
    def _calculate_similarity_from_cache(self, movie_info: Dict) -> Tuple[float, Dict]:
        """Calculate similarity score using cached movie data and return score with breakdown"""
        try:
            score = 0.0
            score_breakdown = {
                'genre_score': 0.0,
                'director_score': 0.0,
                'actor_score': 0.0,
                'language_score': 0.0,
                'keyword_score': 0.0,
                'details': {
                    'genres': [],
                    'directors': [],
                    'actors': [],
                    'language': None,
                    'keywords': []
                }
            }
            
            weights = self.weights
            user_prefs = {
                'genres': Counter(self.watched_data.get('genres', {})),
                'directors': Counter(self.watched_data.get('directors', {})),
                'actors': Counter(self.watched_data.get('actors', {})),
                'languages': Counter(self.watched_data.get('languages', {})),
                'keywords': Counter(self.watched_data.get('tmdb_keywords', {}))
            }
            
            max_counts = {
                'genres': max(user_prefs['genres'].values()) if user_prefs['genres'] else 1,
                'directors': max(user_prefs['directors'].values()) if user_prefs['directors'] else 1,
                'actors': max(user_prefs['actors'].values()) if user_prefs['actors'] else 1,
                'languages': max(user_prefs['languages'].values()) if user_prefs['languages'] else 1,
                'keywords': max(user_prefs['keywords'].values()) if user_prefs['keywords'] else 1
            }
    
            # Genre Score
            movie_genres = set(movie_info.get('genres', []))
            if movie_genres:
                genre_scores = []
                for genre in movie_genres:
                    genre_count = user_prefs['genres'].get(genre, 0)
                    if genre_count > 0:
                        if self.normalize_counters:
                            # Enhanced normalization with square root to strengthen effect
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
    
            # Director Score
            movie_directors = movie_info.get('directors', [])
            if movie_directors:
                director_scores = []
                for director in movie_directors:
                    director_count = user_prefs['directors'].get(director, 0)
                    if director_count > 0:
                        if self.normalize_counters:
                            normalized_score = math.sqrt(director_count / max_counts['directors'])
                        else:
                            normalized_score = min(director_count / max_counts['directors'], 1.0)
                        
                        director_scores.append(normalized_score)
                        score_breakdown['details']['directors'].append(
                            f"{director} (count: {director_count}, norm: {round(normalized_score, 2)})"
                        )
                if director_scores:
                    director_final = (sum(director_scores) / len(director_scores)) * weights.get('director_weight', 0.20)
                    score += director_final
                    score_breakdown['director_score'] = round(director_final, 3)
    
            # Actor Score
            movie_cast = movie_info.get('cast', [])
            if movie_cast:
                actor_scores = []
                matched_actors = 0
                for actor in movie_cast:
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
            movie_language = movie_info.get('language', 'N/A')
            if movie_language != 'N/A':
                movie_lang_lower = movie_language.lower()
                            
                lang_count = user_prefs['languages'].get(movie_lang_lower, 0)
                
                if lang_count > 0:
                    if self.normalize_counters:
                        normalized_score = math.sqrt(lang_count / max_counts['languages'])
                    else:
                        normalized_score = min(lang_count / max_counts['languages'], 1.0)
                    
                    lang_final = normalized_score * weights.get('language_weight', 0.10)
                    score += lang_final
                    score_breakdown['language_score'] = round(lang_final, 3)
                    score_breakdown['details']['language'] = f"{movie_language} (count: {lang_count}, norm: {round(normalized_score, 2)})"
    
            # TMDB Keywords Score
            if self.use_tmdb_keywords and movie_info.get('tmdb_keywords'):
                keyword_scores = []
                for kw in movie_info['tmdb_keywords']:
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
            log_warning(f"Error calculating similarity score for {movie_info.get('title', 'Unknown')}: {e}")
            return 0.0, score_breakdown
    
    def _print_similarity_breakdown(self, movie_info: Dict, score: float, breakdown: Dict):
        """Print detailed breakdown of similarity score calculation"""
        print(f"\n{CYAN}Similarity Score Breakdown for '{movie_info['title']}'{RESET}")
        print(f"Total Score: {round(score * 100, 1)}%")
        print(f"├─ Genre Score: {round(breakdown['genre_score'] * 100, 1)}%")
        if breakdown['details']['genres']:
            print(f"│  └─ Matching genres: {', '.join(breakdown['details']['genres'])}")
        print(f"├─ Director Score: {round(breakdown['director_score'] * 100, 1)}%")
        if breakdown['details']['directors']:
            print(f"│  └─ Director match: {', '.join(breakdown['details']['directors'])}")
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
            return recommended_movies
    
        indices_str = re.split(r'[,\s]+', choice)
        chosen = []
        for idx_str in indices_str:
            idx_str = idx_str.strip()
            if not idx_str.isdigit():
                log_warning(f"Skipping invalid index: {idx_str}")
                continue
            idx = int(idx_str)
            if 1 <= idx <= len(recommended_movies):
                chosen.append(idx)
            else:
                log_warning(f"Skipping out-of-range index: {idx}")
    
        if not chosen:
            log_warning(f"No valid indices selected, skipping {operation_label}.")
            return []
    
        subset = []
        for c in chosen:
            subset.append(recommended_movies[c - 1])
        return subset

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

            # Find new movies in Plex (if any were recommended)
            movies_to_update = []
            skipped_movies = []
            for rec in selected_movies:
                plex_movie = next(
                    (m for m in movies_section.search(title=rec['title'])
                     if m.year == rec.get('year')),
                    None
                )
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
            from datetime import datetime, timedelta
            stale_threshold = datetime.now() - timedelta(days=stale_days)

            # Get currently labeled movies
            currently_labeled = movies_section.search(label=label_name)
            print(f"Found {len(currently_labeled)} currently labeled movies")

            # Get excluded genres for this user (for checking existing items)
            excluded_genres = get_excluded_genres_for_user(self.exclude_genres, self.user_preferences, self.single_user)

            # Separate into watched, unwatched-fresh, stale, and excluded
            unwatched_labeled = []
            watched_labeled = []
            stale_labeled = []
            excluded_labeled = []

            for movie in currently_labeled:
                movie.reload()  # Ensure fresh data
                movie_id = int(movie.ratingKey)
                label_key = f"{movie_id}_{label_name}"

                # Check if this movie has excluded genres
                movie_genres = [g.tag.lower() for g in movie.genres]
                if any(g in excluded_genres for g in movie_genres):
                    excluded_labeled.append(movie)
                    continue

                # Check if this movie has been watched by any of the users
                if movie_id in self.watched_movie_ids:
                    watched_labeled.append(movie)
                else:
                    # Check if stale (unwatched for > stale_days)
                    label_date_str = self.label_dates.get(label_key)
                    if label_date_str:
                        label_date = datetime.fromisoformat(label_date_str)
                        if label_date < stale_threshold:
                            stale_labeled.append(movie)
                        else:
                            unwatched_labeled.append(movie)
                    else:
                        # No date tracked - assume it's new (keep it)
                        unwatched_labeled.append(movie)
                        # Track it now for future runs
                        self.label_dates[label_key] = datetime.now().isoformat()

            print(f"{GREEN}Keeping {len(unwatched_labeled)} fresh unwatched recommendations{RESET}")
            print(f"{YELLOW}Removing {len(watched_labeled)} watched movies from recommendations{RESET}")
            print(f"{YELLOW}Removing {len(stale_labeled)} stale recommendations (unwatched > {stale_days} days){RESET}")
            print(f"{YELLOW}Removing {len(excluded_labeled)} movies with excluded genres{RESET}")

            # Remove labels from watched movies
            for movie in watched_labeled:
                movie.removeLabel(label_name)
                label_key = f"{int(movie.ratingKey)}_{label_name}"
                if label_key in self.label_dates:
                    del self.label_dates[label_key]
                log_warning(f"Removed (watched): {movie.title}")

            # Remove labels from stale movies
            for movie in stale_labeled:
                movie.removeLabel(label_name)
                label_key = f"{int(movie.ratingKey)}_{label_name}"
                if label_key in self.label_dates:
                    del self.label_dates[label_key]
                log_warning(f"Removed (stale): {movie.title}")

            # Remove labels from excluded genre movies
            for movie in excluded_labeled:
                movie.removeLabel(label_name)
                label_key = f"{int(movie.ratingKey)}_{label_name}"
                if label_key in self.label_dates:
                    del self.label_dates[label_key]
                log_warning(f"Removed (excluded genre): {movie.title}")

            # Get target count from config
            target_count = self.config['general'].get('limit_plex_results', 50)

            # Calculate how many new recommendations we need
            current_unwatched_count = len(unwatched_labeled)
            slots_available = target_count - current_unwatched_count

            print(f"{GREEN}Collection capacity: {current_unwatched_count}/{target_count} (need {max(0, slots_available)} more){RESET}")

            # Get IDs of movies already in collection
            already_labeled_ids = {int(m.ratingKey) for m in unwatched_labeled}

            # Filter new recommendations to exclude already labeled movies
            new_recommendations = []
            if movies_to_update:
                for movie in movies_to_update:
                    movie_id = int(movie.ratingKey)
                    if movie_id not in already_labeled_ids and movie_id not in self.watched_movie_ids:
                        new_recommendations.append(movie)
            else:
                print(f"{YELLOW}No new recommendations available - cleanup only mode{RESET}")

            # Take only what we need to fill gaps
            movies_to_add = new_recommendations[:max(0, slots_available)]

            if movies_to_add:
                print(f"{GREEN}Adding {len(movies_to_add)} new recommendations to fill gaps{RESET}")
            elif slots_available > 0:
                print(f"{YELLOW}Need {slots_available} more movies but none available to add{RESET}")

            # Add labels to new recommendations
            for movie in movies_to_add:
                current_labels = [label.tag for label in movie.labels]
                if label_name not in current_labels:
                    movie.addLabel(label_name)
                    # Track label date
                    label_key = f"{int(movie.ratingKey)}_{label_name}"
                    self.label_dates[label_key] = datetime.now().isoformat()
                    print(f"{GREEN}Added: {movie.title}{RESET}")

            # Note: label_dates will be saved in _save_watched_cache()

            # RE-SORT: Calculate similarity for all movies and sort by score
            print(f"{GREEN}Re-calculating similarity scores for entire collection...{RESET}")

            # Create a mapping of movie_id -> similarity_score from selected_movies (new recommendations)
            similarity_scores = {}
            for rec in selected_movies:
                # Find the Plex object that matches this recommendation
                matching_plex = next(
                    (m for m in movies_to_update if m.title == rec['title'] and m.year == rec.get('year')),
                    None
                )
                if matching_plex:
                    similarity_scores[int(matching_plex.ratingKey)] = rec.get('similarity_score', 0.0)

            # Calculate similarity for unwatched movies from previous runs
            for movie in unwatched_labeled:
                movie_id = int(movie.ratingKey)
                if movie_id not in similarity_scores:
                    pass
                    # Get movie from cache
                    movie_info = self.movie_cache.cache['movies'].get(str(movie_id))
                    if movie_info:
                        try:
                            similarity_score, _ = self._calculate_similarity_from_cache(movie_info)
                            similarity_scores[movie_id] = similarity_score
                        except Exception:
                            similarity_scores[movie_id] = 0.0
                    else:
                        similarity_scores[movie_id] = 0.0

            # Combine all labeled movies (unwatched + newly added)
            final_collection_movies = unwatched_labeled + movies_to_add

            # Sort by similarity score (highest first)
            final_collection_movies.sort(
                key=lambda m: similarity_scores.get(int(m.ratingKey), 0.0),
                reverse=True
            )

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
    bullet = f"{index}. " if index is not None else "- "
    output = f"{bullet}{CYAN}{movie['title']}{RESET} ({movie.get('year', 'N/A')})"

    if 'similarity_score' in movie:
        score_percentage = round(movie['similarity_score'] * 100, 1)
        output += f" - Similarity: {YELLOW}{score_percentage}%{RESET}"
        
    # Only add genres once and only if show_genres is True
    if show_genres and movie.get('genres'):
        output += f"\n  {YELLOW}Genres:{RESET} {', '.join(movie['genres'])}"

    if show_summary and movie.get('summary'):
        output += f"\n  {YELLOW}Summary:{RESET} {movie['summary']}"

    if show_cast and movie.get('cast'):
        output += f"\n  {YELLOW}Cast:{RESET} {', '.join(movie['cast'])}"

    if show_director and movie.get('directors'):
        if isinstance(movie['directors'], list):
            output += f"\n  {YELLOW}Director:{RESET} {', '.join(movie['directors'])}"
        else:
            output += f"\n  {YELLOW}Director:{RESET} {movie['directors']}"

    if show_language and movie.get('language') != "N/A":
        output += f"\n  {YELLOW}Language:{RESET} {movie['language']}"

    if show_rating and movie.get('ratings', {}).get('audience_rating', 0) > 0:
        rating = movie['ratings']['audience_rating']
        output += f"\n  {YELLOW}Rating:{RESET} {rating}/10"

    if show_imdb_link and movie.get('imdb_id'):
        imdb_link = f"https://www.imdb.com/title/{movie['imdb_id']}/"
        output += f"\n  {YELLOW}IMDb Link:{RESET} {imdb_link}"

    return output


# ------------------------------------------------------------------------
# CONFIG ADAPTER
# ------------------------------------------------------------------------
def adapt_root_config_to_legacy(root_config):
    """Convert root config.yml format to legacy MRFP format"""
    media_type = 'movies'

    # Build legacy config structure
    adapted = {
        'general': {
            'confirm_operations': root_config.get('general', {}).get('confirm_operations', False),
            'plex_only': root_config.get('general', {}).get('plex_only', True),
            'combine_watch_history': root_config.get('general', {}).get('combine_watch_history', False),
            'log_retention_days': root_config.get('general', {}).get('log_retention_days', 7),
            'limit_plex_results': root_config.get(media_type, {}).get('limit_results', 50),
            'exclude_genre': root_config.get('general', {}).get('exclude_genre', None),
            'randomize_recommendations': root_config.get(media_type, {}).get('randomize_recommendations', False),
            'normalize_counters': root_config.get(media_type, {}).get('normalize_counters', False),
            'show_summary': root_config.get(media_type, {}).get('show_summary', True),
            'show_cast': root_config.get(media_type, {}).get('show_cast', True),
            'show_director': root_config.get(media_type, {}).get('show_director', True),
            'show_genres': root_config.get(media_type, {}).get('show_genres', True),
            'show_language': root_config.get(media_type, {}).get('show_language', True),
            'show_rating': root_config.get(media_type, {}).get('show_rating', True),
            'show_imdb_link': root_config.get(media_type, {}).get('show_imdb_link', True),
        },
        'plex': {
            'url': root_config.get('plex', {}).get('url', ''),
            'token': root_config.get('plex', {}).get('token', ''),
            'movie_library_title': root_config.get('plex', {}).get('movie_library', 'Movies'),
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
            'use_TMDB_keywords': root_config.get('tmdb', {}).get('use_TMDB_keywords', True),
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

# ------------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------------
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