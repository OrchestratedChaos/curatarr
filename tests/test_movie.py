"""
Tests for recommenders/movie.py - Movie recommendation system.
"""

import os
import copy
import pytest
from unittest.mock import Mock, patch, MagicMock
from collections import Counter
import json

from recommenders.movie import (
    MovieCache, PlexMovieRecommender, format_movie_output, adapt_root_config_to_legacy,
    process_recommendations, main,
)


class TestMovieCache:
    """Tests for MovieCache class."""

    @patch('recommenders.base.load_media_cache')
    def test_movie_cache_attributes(self, mock_load):
        """Test that MovieCache has correct attributes."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = MovieCache('/tmp/cache')

        assert cache.media_type == 'movie'
        assert cache.media_key == 'movies'
        assert cache.cache_filename == 'all_movies_cache.json'

    @patch('recommenders.base.load_media_cache')
    def test_process_item_extracts_directors(self, mock_load):
        """Test that _process_item extracts directors."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = MovieCache('/tmp/cache')

        mock_director = Mock()
        mock_director.tag = 'Steven Spielberg'
        mock_movie = Mock()
        mock_movie.title = 'Test Movie'
        mock_movie.year = 2024
        mock_movie.summary = 'A test movie'
        mock_movie.directors = [mock_director]
        mock_movie.genres = []
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = 7.5
        mock_movie.media = []

        result = cache._process_item(mock_movie, None)

        assert result is not None
        assert 'Steven Spielberg' in result['directors']

    @patch('recommenders.base.load_media_cache')
    def test_process_item_extracts_genres(self, mock_load):
        """Test that _process_item extracts genres."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = MovieCache('/tmp/cache')

        mock_genre = Mock()
        mock_genre.tag = 'Action'
        mock_movie = Mock()
        mock_movie.title = 'Test Movie'
        mock_movie.year = 2024
        mock_movie.summary = 'A test movie'
        mock_movie.directors = []
        mock_movie.genres = [mock_genre]
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = None
        mock_movie.media = []

        result = cache._process_item(mock_movie, None)

        assert 'action' in result['genres']

    @patch('recommenders.base.load_media_cache')
    def test_process_item_extracts_cast(self, mock_load):
        """Test that _process_item extracts cast members."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = MovieCache('/tmp/cache')

        mock_actor = Mock()
        mock_actor.tag = 'Tom Hanks'
        mock_movie = Mock()
        mock_movie.title = 'Test Movie'
        mock_movie.year = 2024
        mock_movie.summary = 'A test movie'
        mock_movie.directors = []
        mock_movie.genres = []
        mock_movie.roles = [mock_actor]
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = None
        mock_movie.media = []

        result = cache._process_item(mock_movie, None)

        assert 'Tom Hanks' in result['cast']

class TestPlexMovieRecommenderInit:
    """Tests for PlexMovieRecommender initialization."""

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_init_creates_movie_cache(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that PlexMovieRecommender creates a MovieCache."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'keyword': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')

        mock_cache.assert_called_once()

class TestPlexMovieRecommenderLibraryParam:
    """Tests for PlexMovieRecommender library threading (#157 Phase 3)."""

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_library_forwarded_to_base(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that the library param reaches BaseRecommender and sets
        library_id/library_title from the library dict, not the legacy
        movie_library config key."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc', 'movie_library': 'Movies'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'keyword': 0.2},
            'libraries': [
                {'id': 'movies', 'name': 'Movies', 'section': 'Movies', 'media_type': 'movie'},
                {'id': 'movies-4k', 'name': 'Movies 4K', 'section': 'Movies 4K', 'media_type': 'movie'},
            ],
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})

        library = {'id': 'movies-4k', 'name': 'Movies 4K', 'section': 'Movies 4K', 'media_type': 'movie'}
        recommender = PlexMovieRecommender('/path/to/config.yml', library=library)

        assert recommender.library_id == 'movies-4k'
        assert recommender.library_title == 'Movies 4K'

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_no_library_keeps_legacy_title(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """library=None (default) resolves library_title from the legacy
        movie_library config key, unchanged from before Phase 3."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc', 'movie_library': 'Movies'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'keyword': 0.2},
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')

        assert recommender.library_id is None
        assert recommender.library_title == 'Movies'


class TestPlexMovieRecommenderWeights:
    """Tests for PlexMovieRecommender weight loading."""

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_loads_weights_from_config(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that weights are loaded from config."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {
                'genre': 0.35,
                'director': 0.15,
                'actor': 0.20,
                'language': 0.10,
                'tmdb_keywords': 0.20
            }
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')

        assert recommender.weights['genre'] == 0.35
        assert recommender.weights['director'] == 0.15
        assert recommender.weights['actor'] == 0.20

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_uses_default_weights_when_missing(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that default weights are used when not in config."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {}  # Empty weights
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')

        # Should have default weights
        assert 'genre' in recommender.weights
        assert 'director' in recommender.weights
        assert 'actor' in recommender.weights


class TestPlexMovieRecommenderLibraryMethods:
    """Tests for PlexMovieRecommender library methods."""

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_library_movies_set(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test _get_library_movies_set returns movie IDs."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc', 'movie_library': 'Movies'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'tmdb_keywords': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}

        mock_movie = Mock()
        mock_movie.ratingKey = 123
        mock_section = Mock()
        mock_section.all.return_value = [mock_movie]
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'movies': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')
        result = recommender._get_library_movies_set()

        assert 123 in result

class TestPlexMovieRecommenderSimilarity:
    """Tests for PlexMovieRecommender similarity calculation."""

    @patch('recommenders.movie.calculate_similarity_score')
    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_calculate_similarity_from_cache(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_calc):
        """Test _calculate_similarity_from_cache uses cached data."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'tmdb_keywords': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})
        mock_calc.return_value = (0.75, {'genre': 0.25, 'director': 0.15})

        recommender = PlexMovieRecommender('/path/to/config.yml')
        recommender.watched_data_counters = {
            'genres': Counter({'action': 5}),
            'directors': Counter({'spielberg': 3}),
            'actors': Counter(),
            'languages': Counter(),
            'tmdb_keywords': Counter()
        }

        movie_info = {
            'title': 'Test Movie',
            'genres': ['action'],
            'directors': ['spielberg'],
            'cast': [],
            'language': 'english',
            'tmdb_keywords': []
        }

        score, breakdown = recommender._calculate_similarity_from_cache(movie_info)

        assert score == 0.75
        mock_calc.assert_called_once()


class TestPlexMovieRecommenderWatchedCache:
    """Tests for PlexMovieRecommender watched cache methods."""

    @patch('recommenders.base.save_watched_cache')
    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_save_watched_cache(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_save):
        """Test _save_watched_cache saves data correctly."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'tmdb_keywords': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')
        recommender.watched_data_counters = {'genres': Counter({'action': 5})}
        recommender.watched_movie_ids = {1, 2, 3}
        recommender.cached_watched_count = 10

        mock_save.reset_mock()  # Reset after init
        recommender._save_watched_cache()

        mock_save.assert_called_once()


class TestFormatMovieOutput:
    """Tests for format_movie_output function."""

    def test_format_basic_movie(self):
        """Test basic movie formatting."""
        movie = {
            'title': 'Test Movie',
            'year': 2024,
            'similarity_score': 0.85
        }

        result = format_movie_output(movie, 1)

        assert 'Test Movie' in result
        assert '2024' in result
        assert '85' in result

    def test_format_with_genres(self):
        """Test movie formatting with genres."""
        movie = {
            'title': 'Test Movie',
            'year': 2024,
            'similarity_score': 0.75,
            'genres': ['action', 'comedy']
        }

        result = format_movie_output(movie, 1, show_genres=True)

        assert 'action' in result
        assert 'comedy' in result

    def test_format_with_cast(self):
        """Test movie formatting with cast."""
        movie = {
            'title': 'Test Movie',
            'year': 2024,
            'similarity_score': 0.75,
            'cast': ['Tom Hanks', 'Meg Ryan']
        }

        result = format_movie_output(movie, 1, show_cast=True)

        assert 'Tom Hanks' in result

    def test_format_with_imdb_link(self):
        """Test movie formatting with IMDB link."""
        movie = {
            'title': 'Test Movie',
            'year': 2024,
            'similarity_score': 0.75,
            'imdb_id': 'tt1234567'
        }

        result = format_movie_output(movie, 1, show_imdb_link=True)

        assert 'imdb.com' in result
        assert 'tt1234567' in result


class TestAdaptRootConfigToLegacy:
    """Tests for adapt_root_config_to_legacy function."""

    def test_adapt_preserves_plex_key(self):
        """Test that config with 'plex' key preserves it."""
        config = {'plex': {'url': 'http://localhost', 'token': 'abc'}}

        result = adapt_root_config_to_legacy(config)

        assert 'plex' in result
        assert result['plex']['url'] == 'http://localhost'

    def test_adapt_returns_dict(self):
        """Test that function returns a dict."""
        config = {'plex': {'url': 'http://localhost'}}

        result = adapt_root_config_to_legacy(config)

        assert isinstance(result, dict)


class TestProcessRecommendationsLibraryParam:
    """Tests for process_recommendations library forwarding (#157 Phase 3)."""

    @patch('recommenders.movie.PlexMovieRecommender')
    @patch('recommenders.movie.teardown_log_file')
    @patch('recommenders.movie.setup_log_file')
    def test_forwards_library_to_recommender(self, mock_setup_log, mock_teardown, mock_recommender_cls):
        """process_recommendations passes library through to
        PlexMovieRecommender's constructor unchanged."""
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {'plex_recommendations': []}
        mock_instance.config = {'general': {}}
        mock_recommender_cls.return_value = mock_instance

        library = {'id': 'movies-4k', 'name': 'Movies 4K', 'section': 'Movies 4K', 'media_type': 'movie'}
        process_recommendations({'general': {}}, '/path/to/config.yml', 0, single_user='alice', library=library)

        mock_recommender_cls.assert_called_once_with('/path/to/config.yml', single_user='alice', library=library)

    @patch('recommenders.movie.PlexMovieRecommender')
    @patch('recommenders.movie.teardown_log_file')
    @patch('recommenders.movie.setup_log_file')
    def test_defaults_library_to_none(self, mock_setup_log, mock_teardown, mock_recommender_cls):
        """process_recommendations defaults library=None (legacy callers)."""
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {'plex_recommendations': []}
        mock_instance.config = {'general': {}}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({'general': {}}, '/path/to/config.yml', 0, single_user='alice')

        mock_recommender_cls.assert_called_once_with('/path/to/config.yml', single_user='alice', library=None)


class TestMainMediaTypeKey:
    """Tests for main() passing media_type_key (#157 Phase 3)."""

    @patch('recommenders.movie.run_recommender_main')
    def test_main_passes_movie_media_type_key(self, mock_run_main):
        main()

        assert mock_run_main.call_count == 1
        _, kwargs = mock_run_main.call_args
        assert kwargs['media_type_key'] == 'movie'
        assert kwargs['process_func'] is process_recommendations


class TestPlexMovieRecommenderWatchedCount:
    """Tests for PlexMovieRecommender._get_watched_count method."""

    @patch('recommenders.movie.get_watched_movie_count')
    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_watched_count_calls_utility(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_count):
        """Test that _get_watched_count uses utility function."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'tmdb_keywords': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})
        mock_count.return_value = 42

        recommender = PlexMovieRecommender('/path/to/config.yml')
        result = recommender._get_watched_count()

        assert result == 42


class TestPlexMovieRecommenderTmdbMethods:
    """Tests for PlexMovieRecommender TMDB-related methods."""

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_plex_item_tmdb_id_from_cache(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test _get_plex_item_tmdb_id returns from cache."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'tmdb_keywords': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')
        recommender.plex_tmdb_cache = {'123': 456}

        mock_movie = Mock()
        mock_movie.ratingKey = 123

        result = recommender._get_plex_item_tmdb_id(mock_movie)

        assert result == 456

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_plex_item_imdb_id_from_guids(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test _get_plex_item_imdb_id extracts from guids."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'tmdb_keywords': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')

        mock_guid = Mock()
        mock_guid.id = 'imdb://tt1234567'
        mock_movie = Mock()
        mock_movie.guids = [mock_guid]

        result = recommender._get_plex_item_imdb_id(mock_movie)

        assert result == 'tt1234567'


class TestPlexMovieRecommenderRefreshWatchedData:
    """Tests for PlexMovieRecommender._refresh_watched_data method."""

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_refresh_clears_data(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test _refresh_watched_data clears existing data."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'keyword': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')
        recommender.watched_ids = {1, 2, 3}
        recommender.watched_data_counters = {'genres': Counter({'action': 5})}

        # Mock the methods called during refresh (base class calls _get_watched_data)
        recommender._get_watched_data = Mock(return_value={'genres': Counter()})
        recommender._save_watched_cache = Mock()

        recommender._refresh_watched_data()

        assert len(recommender.watched_ids) == 0


class TestPlexMovieRecommenderGetRecommendations:
    """Tests for PlexMovieRecommender.get_recommendations method."""

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_recommendations_returns_dict(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test get_recommendations returns a dict with plex_recommendations."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {'limit_plex_results': 10},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'tmdb_keywords': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'movies': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')
        recommender.watched_movie_ids = set()
        recommender.cached_watched_count = 0
        recommender.watched_data = {
            'genres': Counter(), 'directors': Counter(), 'actors': Counter(),
            'languages': Counter(), 'tmdb_keywords': Counter(), 'collections': Counter()
        }

        result = recommender.get_recommendations()

        assert isinstance(result, dict)
        assert 'plex_recommendations' in result

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_recommendations_excludes_watched(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test get_recommendations excludes watched movies."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {'limit_plex_results': 10},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'keyword': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst

        mock_cache_inst = Mock()
        mock_cache_inst.cache = {
            'movies': {
                '1': {'title': 'Watched Movie', 'genres': [], 'directors': [], 'cast': [], 'language': '', 'tmdb_keywords': []},
                '2': {'title': 'Unwatched Movie', 'genres': [], 'directors': [], 'cast': [], 'language': '', 'tmdb_keywords': []},
            }
        }
        mock_cache_inst._save_cache = Mock()
        mock_cache.return_value = mock_cache_inst

        recommender = PlexMovieRecommender('/path/to/config.yml')
        recommender.watched_ids = {1}
        recommender.cached_watched_count = 1
        recommender.watched_data = {
            'genres': Counter(), 'directors': Counter(), 'actors': Counter(),
            'languages': Counter(), 'tmdb_keywords': Counter(), 'collections': Counter()
        }
        recommender._calculate_similarity_from_cache = Mock(return_value=(0.5, {}))

        result = recommender.get_recommendations()

        rec_titles = [r['title'] for r in result['plex_recommendations']]
        assert 'Watched Movie' not in rec_titles


class TestPlexMovieRecommenderCollectionBonus:
    """Tests for collection bonus in similarity calculation."""

    @patch('recommenders.movie.calculate_similarity_score')
    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_collection_bonus_applied(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_calc):
        """Test that collection bonus is applied for movies in watched collections."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'tmdb_keywords': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={'movies': {}})
        mock_calc.return_value = (0.70, {'genre': 0.20, 'director': 0.10, 'details': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')
        recommender.watched_data = {
            'genres': Counter({'action': 5}),
            'directors': Counter(),
            'actors': Counter(),
            'languages': Counter(),
            'tmdb_keywords': Counter(),
            'collections': Counter({789: 3.0})  # User watched 3 movies from collection 789
        }

        movie_info = {
            'title': 'Sequel Movie',
            'genres': ['action'],
            'directors': [],
            'cast': [],
            'language': 'english',
            'tmdb_keywords': [],
            'collection_id': 789,
            'collection_name': 'Action Franchise'
        }

        score, breakdown = recommender._calculate_similarity_from_cache(movie_info)

        # Score should be boosted due to collection bonus
        assert score > 0.70
        assert 'collection_bonus' in breakdown


class TestMovieCacheRatingExtraction:
    """Tests for MovieCache rating extraction."""

    @patch('recommenders.base.load_media_cache')
    def test_extracts_user_rating(self, mock_load):
        """Test that userRating is extracted."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = MovieCache('/tmp/cache')

        mock_movie = Mock()
        mock_movie.title = 'Test Movie'
        mock_movie.year = 2024
        mock_movie.summary = ''
        mock_movie.directors = []
        mock_movie.genres = []
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = 8.5
        mock_movie.audienceRating = None

        result = cache._process_item(mock_movie, None)

        assert result['ratings']['audience_rating'] == 8.5

    @patch('recommenders.base.load_media_cache')
    def test_extracts_audience_rating_as_fallback(self, mock_load):
        """Test that audienceRating is used as fallback."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = MovieCache('/tmp/cache')

        mock_movie = Mock()
        mock_movie.title = 'Test Movie'
        mock_movie.year = 2024
        mock_movie.summary = ''
        mock_movie.directors = []
        mock_movie.genres = []
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = 7.0

        result = cache._process_item(mock_movie, None)

        assert result['ratings']['audience_rating'] == 7.0

    @patch('recommenders.base.load_media_cache')
    def test_handles_no_rating(self, mock_load):
        """Test handling when no rating is available."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = MovieCache('/tmp/cache')

        mock_movie = Mock()
        mock_movie.title = 'Test Movie'
        mock_movie.year = 2024
        mock_movie.summary = ''
        mock_movie.directors = []
        mock_movie.genres = []
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = None

        result = cache._process_item(mock_movie, None)

        assert result['ratings'] == {}


class TestPlexMovieRecommenderExcludedGenres:
    """Tests for genre exclusion in recommendations."""

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_excludes_configured_genres(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that configured excluded genres are filtered."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {'exclude_genre': 'horror,documentary', 'limit_plex_results': 10},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'tmdb_keywords': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst

        mock_cache_inst = Mock()
        mock_cache_inst.cache = {
            'movies': {
                '1': {'title': 'Horror Movie', 'genres': ['horror'], 'directors': [], 'cast': [], 'language': '', 'tmdb_keywords': []},
                '2': {'title': 'Drama Movie', 'genres': ['drama'], 'directors': [], 'cast': [], 'language': '', 'tmdb_keywords': []},
            }
        }
        mock_cache_inst._save_cache = Mock()
        mock_cache.return_value = mock_cache_inst

        recommender = PlexMovieRecommender('/path/to/config.yml')
        recommender.watched_movie_ids = set()
        recommender.cached_watched_count = 0
        recommender.watched_data = {
            'genres': Counter(), 'directors': Counter(), 'actors': Counter(),
            'languages': Counter(), 'tmdb_keywords': Counter(), 'collections': Counter()
        }
        recommender._calculate_similarity_from_cache = Mock(return_value=(0.5, {}))

        result = recommender.get_recommendations()

        rec_titles = [r['title'] for r in result['plex_recommendations']]
        assert 'Horror Movie' not in rec_titles
        assert 'Drama Movie' in rec_titles


class TestMovieCacheCollectionData:
    """Tests for MovieCache collection data handling."""

    @patch('recommenders.base.load_media_cache')
    def test_extracts_collection_id(self, mock_load):
        """Test that collection_id is stored in cache."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = MovieCache('/tmp/cache')

        mock_movie = Mock()
        mock_movie.title = 'Test Movie'
        mock_movie.year = 2024
        mock_movie.summary = ''
        mock_movie.directors = []
        mock_movie.genres = []
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = None

        # Mock _get_tmdb_data to return collection info
        cache._get_tmdb_data = Mock(return_value={
            'tmdb_id': 123,
            'imdb_id': 'tt1234567',
            'keywords': [],
            'rating': 7.5,
            'vote_count': 1000,
            'collection_id': 789,
            'collection_name': 'Test Collection'
        })

        result = cache._process_item(mock_movie, 'api_key')

        assert result['collection_id'] == 789
        assert result['collection_name'] == 'Test Collection'


class TestPlexMovieRecommenderManageLabels:
    """Tests for PlexMovieRecommender.manage_plex_labels method."""

    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_manage_labels_skips_when_disabled(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test manage_plex_labels does nothing when disabled."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'tmdb_keywords': 0.2},
            'collections': {'add_label': False}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'movies': {}})

        recommender = PlexMovieRecommender('/path/to/config.yml')

        # Should not raise and should not call library methods
        recommender.manage_plex_labels([{'title': 'Test', 'year': 2020}])


class TestFormatMovieOutputExtended:
    """Extended tests for format_movie_output function."""

    def test_format_with_summary(self):
        """Test movie formatting with summary."""
        movie = {
            'title': 'Test Movie',
            'year': 2024,
            'similarity_score': 0.85,
            'summary': 'This is a great action movie about heroes.'
        }

        result = format_movie_output(movie, show_summary=True, index=1)

        assert 'action' in result.lower() or 'heroes' in result.lower()

    def test_format_with_language(self):
        """Test movie formatting with language."""
        movie = {
            'title': 'Test Movie',
            'year': 2024,
            'similarity_score': 0.85,
            'language': 'English'
        }

        result = format_movie_output(movie, index=1, show_language=True)

        assert 'English' in result

    def test_format_with_rating(self):
        """Test movie formatting with rating."""
        movie = {
            'title': 'Test Movie',
            'year': 2024,
            'similarity_score': 0.85,
            'ratings': {'audience_rating': 8.5}
        }

        result = format_movie_output(movie, index=1, show_rating=True)

        assert '8.5' in result or '8' in result


class TestPlexMovieRecommenderLibraryImdbIds:
    """Tests for PlexMovieRecommender._get_library_imdb_ids method (inherited from BaseRecommender)."""

    @patch('recommenders.base.get_library_imdb_ids')
    @patch('recommenders.movie.MovieCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_library_imdb_ids_calls_utility(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_get_ids):
        """Test that _get_library_imdb_ids uses utility function."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'tmdb_keywords': 0.2}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'movies': {}})
        mock_get_ids.return_value = {'tt1234567', 'tt7654321'}

        recommender = PlexMovieRecommender('/path/to/config.yml')
        result = recommender._get_library_imdb_ids()

        assert 'tt1234567' in result
        mock_get_ids.assert_called()


# ------------------------------------------------------------------------
# Core recommendation-engine coverage: rating-tier weighting, watched-history
# collection, movie detail extraction, and the per-user/per-library
# process_recommendations orchestration entry point (#157).
# ------------------------------------------------------------------------

MOVIE_TEST_CONFIG = {
    'plex': {'url': 'http://localhost', 'token': 'abc'},
    'general': {},
    'weights': {'genre': 0.3, 'director': 0.2, 'actor': 0.2, 'language': 0.1, 'keyword': 0.2},
}


def _make_movie_recommender(config=None, users=None, movie_cache_data=None, config_path='/path/to/config.yml'):
    """Build a fully-initialized PlexMovieRecommender with all I/O mocked out.

    Note: __init__ eagerly gathers watched data (real _get_plex_watched_data /
    _get_managed_users_watched_data call) and touches the library, so the Plex
    client is a MagicMock (safe default iteration -> []) and the media cache
    is likewise a MagicMock. Tests that care about the specifics of the
    watched-history gathering should patch the relevant
    recommenders.movie.* utility functions *before* calling this helper so
    construction picks them up.
    """
    config = copy.deepcopy(config if config is not None else MOVIE_TEST_CONFIG)
    users = users or {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
    movie_cache = MagicMock()
    movie_cache.cache = {'movies': movie_cache_data or {}}
    with patch('recommenders.movie.MovieCache', return_value=movie_cache), \
         patch('recommenders.base.init_plex', return_value=MagicMock()), \
         patch('recommenders.base.get_configured_users', return_value=users), \
         patch('recommenders.base.get_tmdb_config', return_value={'use_keywords': True, 'api_key': 'key'}), \
         patch('recommenders.base.load_config', return_value=config), \
         patch('os.makedirs'):
        recommender = PlexMovieRecommender(config_path)
    return recommender


class TestCalculateRatingMultiplier:
    """Tests for PlexMovieRecommender._calculate_rating_multiplier (rating-tier weighting)."""

    def test_none_rating_returns_unrated_default(self):
        recommender = _make_movie_recommender()
        assert recommender._calculate_rating_multiplier(None) == 0.6

    def test_zero_rating_returns_unrated_default(self):
        recommender = _make_movie_recommender()
        assert recommender._calculate_rating_multiplier(0) == 0.6

    def test_five_star_rating_returns_full_weight(self):
        recommender = _make_movie_recommender()
        assert recommender._calculate_rating_multiplier(9.5) == 1.0

    def test_four_star_rating(self):
        recommender = _make_movie_recommender()
        assert recommender._calculate_rating_multiplier(7.5) == 0.75

    def test_three_star_rating(self):
        recommender = _make_movie_recommender()
        assert recommender._calculate_rating_multiplier(5.5) == 0.5

    def test_two_star_rating_above_threshold(self):
        recommender = _make_movie_recommender()
        # threshold defaults to 3, so rating 4 is above threshold - positive tier path
        assert recommender._calculate_rating_multiplier(4) == 0.25

    def test_low_rating_with_negative_signals_enabled_is_negative(self):
        recommender = _make_movie_recommender()
        result = recommender._calculate_rating_multiplier(1.0)
        assert result < 0

    def test_low_rating_with_negative_signals_globally_disabled(self):
        recommender = _make_movie_recommender()
        recommender.config['negative_signals'] = {'enabled': False}
        result = recommender._calculate_rating_multiplier(1.0)
        assert result == 0.25

    def test_low_rating_with_bad_ratings_disabled(self):
        recommender = _make_movie_recommender()
        recommender.config['negative_signals'] = {'enabled': True, 'bad_ratings': {'enabled': False}}
        result = recommender._calculate_rating_multiplier(1.0)
        assert result == 0.25


class TestGetPlexWatchedDataMovie:
    """Tests for PlexMovieRecommender._get_plex_watched_data.

    __init__ eagerly calls this (plex_users installs gather watched data at
    construction time), so these tests patch the network-touching utilities
    *before* constructing the recommender and assert on the resulting state.
    """

    @patch('os.path.exists', return_value=False)
    @patch('recommenders.movie.get_plex_account_ids', return_value=[])
    @patch('recommenders.movie.get_watched_movie_count', return_value=0)
    def test_returns_cached_counters_when_not_single_user_and_recalled(self, mock_count, mock_account_ids, mock_exists):
        recommender = _make_movie_recommender()
        # __init__ already populated watched_data_counters via a real call;
        # a direct re-call (not single_user) must short-circuit to the cache.
        cached = recommender.watched_data_counters
        assert cached

        result = recommender._get_plex_watched_data()

        assert result is cached

    @patch('os.path.exists', return_value=False)
    @patch('recommenders.movie.process_counters_from_cache')
    @patch('recommenders.movie.calculate_rewatch_multiplier', return_value=1.0)
    @patch('recommenders.movie.calculate_recency_multiplier', return_value=1.0)
    @patch('recommenders.movie.fetch_plex_watch_history_movies')
    @patch('recommenders.movie.get_plex_account_ids')
    @patch('recommenders.movie.get_watched_movie_count', return_value=1)
    def test_processes_watch_history_and_updates_counters(
        self, mock_count, mock_account_ids, mock_history, mock_recency, mock_rewatch, mock_process_counters, mock_exists
    ):
        mock_account_ids.return_value = ['acct1']
        history_item = Mock(ratingKey=42, viewedAt=None, userRating=None)
        mock_history.return_value = ([history_item], {})

        recommender = _make_movie_recommender(movie_cache_data={'42': {'title': 'Watched Movie', 'tmdb_id': 999}})

        assert 42 in recommender.watched_ids
        mock_process_counters.assert_called_once()
        assert 999 in recommender.watched_data_counters['tmdb_ids']

    @patch('os.path.exists', return_value=False)
    @patch('recommenders.movie.log_error')
    @patch('recommenders.movie.get_plex_account_ids', return_value=[])
    @patch('recommenders.movie.get_watched_movie_count', return_value=1)
    def test_no_account_ids_logs_error(self, mock_count, mock_account_ids, mock_log_error, mock_exists):
        _make_movie_recommender()

        mock_log_error.assert_called()

    @patch('os.path.exists', return_value=False)
    @patch('recommenders.movie.process_counters_from_cache')
    @patch('recommenders.movie.calculate_rewatch_multiplier', return_value=1.0)
    @patch('recommenders.movie.calculate_recency_multiplier', return_value=1.0)
    @patch('recommenders.movie.merge_movie_history')
    @patch('recommenders.movie.fetch_tautulli_movie_history')
    @patch('recommenders.movie.fetch_plex_watch_history_movies')
    @patch('recommenders.movie.get_plex_account_ids')
    @patch('recommenders.movie.get_watched_movie_count', return_value=1)
    def test_merges_tautulli_history_when_enabled(
        self, mock_count, mock_account_ids, mock_history, mock_tautulli, mock_merge,
        mock_recency, mock_rewatch, mock_process_counters, mock_exists
    ):
        mock_account_ids.return_value = ['acct1']
        mock_history.return_value = ([], {})
        mock_tautulli.return_value = [Mock(ratingKey=7)]
        mock_merge.return_value = [Mock(ratingKey=7, viewedAt=None, userRating=None)]

        config = copy.deepcopy(MOVIE_TEST_CONFIG)
        config['tautulli'] = {'enabled': True}
        _make_movie_recommender(config=config, movie_cache_data={})

        mock_merge.assert_called_once()


class TestGetMovieDetails:
    """Tests for PlexMovieRecommender.get_movie_details."""

    @patch('recommenders.movie.extract_genres', return_value=['Action'])
    @patch('recommenders.movie.extract_rating', return_value=8.0)
    @patch('recommenders.movie.extract_ids_from_guids', return_value={'imdb_id': 'tt1', 'tmdb_id': 1})
    def test_extracts_full_details(self, mock_ids, mock_rating, mock_genres):
        recommender = _make_movie_recommender()
        recommender.show_rating = True
        recommender.show_cast = True
        recommender.use_tmdb_keywords = False
        director = Mock(tag='Director A')
        actor = Mock(tag='Actor A')
        movie = Mock(title='Movie A', year=2020, summary='Summary', directors=[director], roles=[actor])

        result = recommender.get_movie_details(movie)

        assert result['title'] == 'Movie A'
        assert 'Director A' in result['directors']
        assert result['ratings']['audience_rating'] == 8.0
        assert 'Actor A' in result['cast']
        movie.reload.assert_called_once()

    @patch('recommenders.movie.extract_ids_from_guids', return_value={'imdb_id': None, 'tmdb_id': None})
    def test_fetches_tmdb_keywords_when_enabled(self, mock_ids):
        recommender = _make_movie_recommender()
        recommender.use_tmdb_keywords = True
        recommender.tmdb_api_key = 'key'
        recommender._get_plex_item_tmdb_id = Mock(return_value=55)
        recommender._get_tmdb_keywords_for_id = Mock(return_value={'kw1', 'kw2'})
        movie = Mock(title='Movie B', year=2021, summary='', directors=[], roles=[])

        result = recommender.get_movie_details(movie)

        assert set(result['tmdb_keywords']) == {'kw1', 'kw2'}

    def test_handles_exception_returns_empty_dict(self):
        recommender = _make_movie_recommender()
        movie = Mock()
        movie.reload.side_effect = Exception("plex error")

        result = recommender.get_movie_details(movie)

        assert result == {}


class TestFindPlexItemAndWatchedDataSelectionMovie:
    """Tests for _find_plex_item delegation and _get_watched_data branch selection."""

    @patch('recommenders.movie.find_plex_movie')
    def test_find_plex_item_delegates_to_utility(self, mock_find):
        recommender = _make_movie_recommender()
        mock_find.return_value = 'found'
        section = Mock()
        rec = {'title': 'X', 'year': 2020}

        result = recommender._find_plex_item(section, rec)

        assert result == 'found'
        mock_find.assert_called_once_with(section, 'X', 2020)

    def test_get_watched_data_uses_plex_history_when_plex_users_configured(self):
        recommender = _make_movie_recommender(
            users={'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        )
        recommender._get_plex_watched_data = Mock(return_value={'genres': {}})
        recommender._get_managed_users_watched_data = Mock(return_value={'genres': {}})

        recommender._get_watched_data()

        recommender._get_plex_watched_data.assert_called_once()
        recommender._get_managed_users_watched_data.assert_not_called()

    @patch('recommenders.base.MyPlexAccount')
    @patch('recommenders.movie.get_watched_movie_count', return_value=0)
    def test_get_watched_data_uses_managed_users_when_no_plex_users(self, mock_count, mock_account_cls):
        recommender = _make_movie_recommender(
            users={'plex_users': [], 'managed_users': ['bob'], 'admin_user': 'admin'}
        )
        recommender._get_plex_watched_data = Mock(return_value={'genres': {}})
        recommender._get_managed_users_watched_data = Mock(return_value={'genres': {}})

        recommender._get_watched_data()

        recommender._get_managed_users_watched_data.assert_called_once()
        recommender._get_plex_watched_data.assert_not_called()


class TestSaveCacheMovie:
    """Tests for PlexMovieRecommender._save_cache."""

    def test_save_cache_calls_save_watched_cache(self):
        recommender = _make_movie_recommender()
        recommender._save_watched_cache = Mock()

        recommender._save_cache()

        recommender._save_watched_cache.assert_called_once()


class TestProcessRecommendationsMovie:
    """Tests for movie.process_recommendations (per-user/per-library orchestration, #157)."""

    @patch('recommenders.movie.format_movie_output', return_value='formatted')
    @patch('recommenders.movie.PlexMovieRecommender')
    @patch('recommenders.movie.teardown_log_file')
    @patch('recommenders.movie.setup_log_file')
    def test_happy_path_prints_and_manages_labels(self, mock_setup, mock_teardown, mock_recommender_cls, mock_format):
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {'plex_recommendations': [{'title': 'A', 'year': 2020}]}
        mock_instance.config = {'general': {}}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({'general': {}}, '/path/to/config.yml', 0)

        mock_instance.manage_plex_labels.assert_called_once()
        mock_instance._save_cache.assert_called_once()

    @patch('recommenders.movie.log_warning')
    @patch('recommenders.movie.PlexMovieRecommender')
    @patch('recommenders.movie.teardown_log_file')
    @patch('recommenders.movie.setup_log_file')
    def test_no_recommendations_warns_and_skips_labels(self, mock_setup, mock_teardown, mock_recommender_cls, mock_warn):
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {'plex_recommendations': []}
        mock_instance.config = {'general': {}}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({'general': {}}, '/path/to/config.yml', 0)

        mock_instance.manage_plex_labels.assert_not_called()
        mock_warn.assert_called()

    @patch('recommenders.movie.PlexMovieRecommender')
    @patch('recommenders.movie.teardown_log_file')
    @patch('recommenders.movie.setup_log_file')
    def test_debug_flag_propagated_from_config(self, mock_setup, mock_teardown, mock_recommender_cls):
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {'plex_recommendations': []}
        mock_instance.config = {'general': {}}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({'general': {'debug': True}}, '/path/to/config.yml', 0)

        assert mock_instance.debug is True

    @patch('recommenders.movie.PlexMovieRecommender')
    @patch('recommenders.movie.teardown_log_file')
    @patch('recommenders.movie.setup_log_file')
    def test_fatal_error_exits(self, mock_setup, mock_teardown, mock_recommender_cls):
        mock_recommender_cls.side_effect = RuntimeError("Plex server unreachable")

        with patch('recommenders.movie.sys.exit') as mock_exit:
            process_recommendations({'general': {}}, '/path/to/config.yml', 0)

        mock_exit.assert_called_once_with(1)

    @patch('recommenders.movie.PlexMovieRecommender')
    @patch('recommenders.movie.teardown_log_file')
    @patch('recommenders.movie.setup_log_file')
    def test_non_fatal_error_does_not_exit(self, mock_setup, mock_teardown, mock_recommender_cls):
        mock_recommender_cls.side_effect = ValueError("Something else broke")

        with patch('recommenders.movie.sys.exit') as mock_exit:
            process_recommendations({'general': {}}, '/path/to/config.yml', 0)

        mock_exit.assert_not_called()
