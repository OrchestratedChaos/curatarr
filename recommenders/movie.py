import os
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging
import math
from plexapi.myplex import MyPlexAccount
import yaml
from typing import Dict, List, Set, Optional, Tuple
import copy
from datetime import datetime

# Import shared utilities
from utils import (
    RED, GREEN, YELLOW, CYAN, RESET,
    TOP_CAST_COUNT,
    DEFAULT_NEGATIVE_THRESHOLD,
    cleanup_old_logs, setup_logging,
    get_plex_account_ids, fetch_plex_watch_history_movies, get_watched_movie_count,
    log_warning, log_error,
    get_negative_multiplier,
    calculate_recency_multiplier, calculate_rewatch_multiplier,
    calculate_similarity_score, find_plex_movie,
    show_progress, TeeLogger,
    extract_genres, extract_ids_from_guids,
    adapt_config_for_media_type,
    format_media_output,
    print_similarity_breakdown,
    create_empty_counters, process_counters_from_cache,
    compute_profile_hash
)

# Module-level logger - configured by setup_logging() in main()
logger = logging.getLogger('plex_recommender')

__version__ = "1.6.18"

# Import base classes
from recommenders.base import BaseCache, BaseRecommender


class MovieCache(BaseCache):
    """Cache for movie metadata including TMDB data, genres, and keywords."""

    media_type = 'movie'
    media_key = 'movies'
    cache_filename = 'all_movies_cache.json'

    def _process_item(self, movie, tmdb_api_key: Optional[str]) -> Optional[Dict]:
        """Process a single movie and return its info dict.

        Args:
            movie: Plex movie item
            tmdb_api_key: Optional TMDB API key

        Returns:
            Dict with movie metadata or None on error
        """
        # Get TMDB data using base class method
        tmdb_data = self._get_tmdb_data(movie, tmdb_api_key) if tmdb_api_key else {
            'tmdb_id': None, 'imdb_id': None, 'keywords': [], 'rating': None, 'vote_count': None
        }

        # Get directors (movie-specific)
        directors = []
        if hasattr(movie, 'directors'):
            directors = [d.tag for d in movie.directors]

        # Extract ratings
        audience_rating = 0
        try:
            if hasattr(movie, 'userRating') and movie.userRating:
                audience_rating = float(movie.userRating)
            elif hasattr(movie, 'audienceRating') and movie.audienceRating:
                audience_rating = float(movie.audienceRating)
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

        return {
            'title': movie.title,
            'year': getattr(movie, 'year', None),
            'genres': [g.tag.lower() for g in movie.genres] if hasattr(movie, 'genres') else [],
            'directors': directors,
            'cast': [r.tag for r in movie.roles[:TOP_CAST_COUNT]] if hasattr(movie, 'roles') else [],
            'summary': getattr(movie, 'summary', ''),
            'language': self._get_language(movie),
            'tmdb_keywords': tmdb_data['keywords'],
            'tmdb_id': tmdb_data['tmdb_id'],
            'imdb_id': tmdb_data['imdb_id'],
            'rating': tmdb_data['rating'],
            'vote_count': tmdb_data['vote_count'],
            'collection_id': tmdb_data.get('collection_id'),
            'collection_name': tmdb_data.get('collection_name'),
            'ratings': {'audience_rating': audience_rating} if audience_rating > 0 else {}
        }


class PlexMovieRecommender(BaseRecommender):
    """Generates personalized movie recommendations based on Plex watch history.

    Analyzes watched movies to build preference profiles based on genres, directors,
    actors, languages, and TMDB keywords. Uses similarity scoring to rank unwatched
    movies in the Plex library.
    """

    # Required class attributes for BaseRecommender
    media_type = 'movie'
    media_key = 'movies'
    library_config_key = 'movie_library_title'
    default_library_name = 'Movies'

    def _load_weights(self, weights_config: Dict) -> Dict:
        """Load movie-specific scoring weights from config."""
        return {
            'genre': weights_config.get('genre', weights_config.get('genre_weight', 0.25)),
            'actor': weights_config.get('actor', weights_config.get('actor_weight', 0.20)),
            'director': weights_config.get('director', weights_config.get('director_weight', 0.05)),
            'keyword': weights_config.get('keyword', weights_config.get('keyword_weight', 0.50)),
            'language': weights_config.get('language', weights_config.get('language_weight', 0.0)),
        }

    def __init__(self, config_path: str, single_user: str = None):
        """Initialize the movie recommender.

        Args:
            config_path: Path to the config.yml configuration file
            single_user: Optional username to generate recommendations for a single user
        """
        # Initialize base class (config, plex, display options, weights, etc.)
        super().__init__(config_path, single_user)

        # Movie-specific initialization
        self.cached_unwatched_count = 0
        self.cached_library_movie_count = 0
        self.synced_movie_ids = set()
        self.cached_unwatched_movies = []
        self.plex_watched_rating_keys = set()
        self.show_director = self.config.get('general', {}).get('show_director', False)

        # Create movie cache
        self.movie_cache = MovieCache(self.cache_dir, recommender=self)
        self.movie_cache.update_cache(self.plex, self.library_title, self.tmdb_api_key)

        # Verify Plex user configuration
        if self.users['plex_users']:
            users_to_process = [self.single_user] if self.single_user else self.users['plex_users']
            print(f"{GREEN}Processing recommendations for Plex users: {users_to_process}{RESET}")
    
        # Verify library exists
        if not self.plex.library.section(self.library_title):
            raise ValueError(f"Movie library '{self.library_title}' not found in Plex")

        # Update cache paths to be user-specific (uses base class method)
        self.watched_cache_path = os.path.join(self.cache_dir, f"watched_cache_{self._get_user_context()}.json")

        # Load watched cache using base class method
        watched_cache = self._load_watched_cache()

        current_library_ids = self._get_library_movies_set()
        
        # Clean up both watched movie tracking mechanisms
        self.plex_watched_rating_keys = {
            rk for rk in self.plex_watched_rating_keys 
            if int(rk) in current_library_ids
        }
        self.watched_ids = {
            movie_id for movie_id in self.watched_ids
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
            self.watched_ids = set()
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
            # Ensure watched_ids are preserved (cache file uses 'watched_movie_ids' key)
            if not self.watched_ids and 'watched_movie_ids' in watched_cache:
                self.watched_ids = {int(id_) for id_ in watched_cache['watched_movie_ids'] if str(id_).isdigit()}
            logger.debug(f"Using cached data: {self.cached_watched_count} watched movies, {len(self.watched_ids)} IDs")

        # Compute profile hash for score caching
        self.profile_hash = compute_profile_hash(self.watched_data_counters)

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
            return 0.6  # Default for unrated content

        rating_int = int(round(user_rating))

        # Check if negative signals are enabled
        ns_config = self.config.get('negative_signals', {})
        bad_ratings_config = ns_config.get('bad_ratings', {})
        ns_enabled = ns_config.get('enabled', True) and bad_ratings_config.get('enabled', True)
        threshold = bad_ratings_config.get('threshold', DEFAULT_NEGATIVE_THRESHOLD)

        # Return negative multiplier for low ratings if enabled
        if ns_enabled and rating_int <= threshold:
            return get_negative_multiplier(rating_int)

        # Positive multipliers for higher ratings
        if user_rating >= 9.0:  # 5 stars
            return 1.0
        elif user_rating >= 7.0:  # 4 stars
            return 0.75
        elif user_rating >= 5.0:  # 3 stars
            return 0.5
        else:  # 2 stars (rating 4)
            return 0.25

    def _get_plex_watched_data(self) -> Dict:
        """Get watched movie data from Plex's native history (using Plex API)"""
        if not self.single_user and hasattr(self, 'watched_data_counters') and self.watched_data_counters:
            return self.watched_data_counters

        movies_section = self.plex.library.section(self.library_title)
        counters = create_empty_counters('movie')
        watched_ids = set()
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
            watched_ids.add(movie_id)

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
                if movie_id in watched_ids and hasattr(movie, 'viewCount') and movie.viewCount:
                    watched_movie_views[movie_id] = int(movie.viewCount)
        except Exception:
            pass  # Fall back to no rewatch weighting if this fails

        print(f"Found {len(watched_ids)} unique watched movies from history API")

        # Store watched movie IDs
        self.watched_ids.update(watched_ids)

        # Process movie metadata from cache WITH recency decay AND user rating weighting
        print(f"")
        print(f"Processing {len(watched_ids)} unique watched movies with recency decay and rating weighting:")
        negative_signal_count = 0

        for i, movie_id in enumerate(watched_ids, 1):
            show_progress("Processing", i, len(watched_ids))

            movie_info = self.movie_cache.cache['movies'].get(str(movie_id))
            if movie_info:
                # Calculate recency multiplier for this movie
                viewed_at = watched_movie_dates.get(movie_id)
                recency_multiplier = calculate_recency_multiplier(viewed_at, self.config.get('recency_decay', {})) if viewed_at else 1.0

                # Calculate rating multiplier based on user's star rating (can be negative for disliked content)
                rating_multiplier = self._calculate_rating_multiplier(user_ratings.get(movie_id))

                # Calculate rewatch multiplier based on view count
                rewatch_multiplier = calculate_rewatch_multiplier(watched_movie_views.get(movie_id, 1))

                # Combine all multipliers
                multiplier = recency_multiplier * rating_multiplier * rewatch_multiplier

                # Track negative signals for logging
                if multiplier < 0:
                    negative_signal_count += 1
                    logger.debug(f"Negative signal: {movie_info.get('title')} (rating: {user_ratings.get(movie_id)}, weight: {multiplier:.2f})")

                # Process with weighted counters
                ns_config = self.config.get('negative_signals', {})
                cap_penalty = ns_config.get('bad_ratings', {}).get('cap_penalty', 0.5)
                process_counters_from_cache(movie_info, counters, media_type='movie', weight=multiplier, cap_penalty=cap_penalty)

                if tmdb_id := movie_info.get('tmdb_id'):
                    counters['tmdb_ids'].add(tmdb_id)
            else:
                not_found_count += 1

        logger.debug(f"Watched movies not in cache: {not_found_count}, TMDB IDs collected: {len(counters['tmdb_ids'])}")
        if negative_signal_count > 0:
            logger.info(f"Processed {negative_signal_count} movies as negative signals (low ratings)")

        return counters

    # ------------------------------------------------------------------------
    # CACHING LOGIC
    # ------------------------------------------------------------------------
    def _save_watched_cache(self):
        """Save watched movie cache using base class utility."""
        self._do_save_watched_cache()

    def _save_cache(self):
        self._save_watched_cache()

    def _get_media_cache(self):
        """Return the movie cache instance."""
        return self.movie_cache

    def _find_plex_item(self, section, rec: Dict):
        """Find a Plex movie matching the recommendation using fuzzy matching."""
        return find_plex_movie(section, rec['title'], rec.get('year'))

    def _get_watched_data(self) -> Dict:
        """Get watched movie data from Plex (implements abstract method from base)."""
        if self.users['plex_users']:
            return self._get_plex_watched_data()
        return self._get_managed_users_watched_data()

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
    
    # _get_library_imdb_ids() inherited from BaseRecommender

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
                tmdb_id = self._get_plex_item_tmdb_id(movie)
                if tmdb_id:
                    tmdb_keywords = list(self._get_tmdb_keywords_for_id(tmdb_id))
            
            movie_info = {
                'title': movie.title,
                'year': getattr(movie, 'year', None),
                'genres': extract_genres(movie),
                'summary': getattr(movie, 'summary', ''),
                'directors': directors,
                'language': self.movie_cache._get_language(movie),
                'imdb_id': imdb_id,
                'ratings': {
                    'audience_rating': audience_rating
                } if audience_rating > 0 else {},
                'cast': [],
                'tmdb_keywords': tmdb_keywords
            }
            
            if self.show_cast and hasattr(movie, 'roles'):
                movie_info['cast'] = [r.tag for r in movie.roles[:TOP_CAST_COUNT]]
                
            return movie_info
                
        except Exception as e:
            log_warning(f"Error getting movie details for {movie.title}: {e}")
            return {}
    
    # TMDB methods inherited from BaseRecommender:
    # - _get_plex_item_tmdb_id()
    # - _get_plex_item_imdb_id()
    # - _get_tmdb_id_via_imdb()
    # - _get_tmdb_keywords_for_id()

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
            'keywords': movie_info.get('tmdb_keywords', []),
            'vote_count': movie_info.get('vote_count', 0),
            'collection_id': movie_info.get('collection_id')
        }

        # Use shared scoring function
        score, breakdown = calculate_similarity_score(
            content_info=content_info,
            user_profile=user_profile,
            media_type='movie',
            weights=self.weights,
            normalize_counters=self.normalize_counters,
            use_fuzzy_keywords=self.use_tmdb_keywords
        )

        # Apply collection bonus for sequels/prequels
        collection_id = movie_info.get('collection_id')
        user_collections = self.watched_data.get('collections', {})
        if collection_id and collection_id in user_collections:
            # User has watched other movies in this collection - apply bonus
            collection_count = user_collections[collection_id]
            # Logarithmic bonus: 1 movie = 5%, 2 = 7.5%, 4 = 10%, etc.
            import math
            bonus = 0.05 * (1 + math.log2(max(1, collection_count)) * 0.5)
            bonus = min(bonus, 0.15)  # Cap at 15% bonus
            score = min(1.0, score * (1 + bonus))
            breakdown['collection_bonus'] = round(bonus, 3)
            breakdown['details']['collection'] = f"{movie_info.get('collection_name', 'Unknown')} (watched: {collection_count:.1f}, bonus: {round(bonus * 100, 1)}%)"

        return score, breakdown
    
    def _print_similarity_breakdown(self, movie_info: Dict, score: float, breakdown: Dict):
        """Print detailed breakdown of similarity score calculation"""
        print_similarity_breakdown(movie_info, score, breakdown, 'movie')

    # get_recommendations() and manage_plex_labels() are inherited from BaseRecommender


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