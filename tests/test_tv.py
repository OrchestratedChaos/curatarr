"""
Tests for recommenders/tv.py - TV show recommendation system.
"""

import os
import pytest
from unittest.mock import Mock, patch, MagicMock
from collections import Counter
import json

from recommenders.tv import ShowCache, PlexTVRecommender, format_show_output, adapt_root_config_to_legacy


class TestShowCache:
    """Tests for ShowCache class."""

    @patch('recommenders.base.load_media_cache')
    def test_show_cache_attributes(self, mock_load):
        """Test that ShowCache has correct attributes."""
        mock_load.return_value = {'shows': {}, 'library_count': 0}

        cache = ShowCache('/tmp/cache')

        assert cache.media_type == 'tv'
        assert cache.media_key == 'shows'
        assert cache.cache_filename == 'all_shows_cache.json'

    @patch('recommenders.base.load_media_cache')
    def test_process_item_extracts_title(self, mock_load):
        """Test that _process_item extracts title."""
        mock_load.return_value = {'shows': {}, 'library_count': 0}

        cache = ShowCache('/tmp/cache')

        mock_show = Mock()
        mock_show.title = 'Breaking Bad'
        mock_show.year = 2008
        mock_show.genres = []
        mock_show.studio = 'AMC'
        mock_show.roles = []
        mock_show.summary = 'A teacher becomes a meth dealer'
        mock_show.guids = []

        result = cache._process_item(mock_show, None)

        assert result is not None
        assert result['title'] == 'Breaking Bad'
        assert result['year'] == 2008

    @patch('recommenders.base.load_media_cache')
    def test_process_item_extracts_genres(self, mock_load):
        """Test that _process_item extracts genres."""
        mock_load.return_value = {'shows': {}, 'library_count': 0}

        cache = ShowCache('/tmp/cache')

        mock_genre = Mock()
        mock_genre.tag = 'Drama'
        mock_show = Mock()
        mock_show.title = 'Test Show'
        mock_show.year = 2020
        mock_show.genres = [mock_genre]
        mock_show.studio = 'Netflix'
        mock_show.roles = []
        mock_show.summary = 'A test show'
        mock_show.guids = []

        result = cache._process_item(mock_show, None)

        assert 'drama' in result['genres']

    @patch('recommenders.base.load_media_cache')
    def test_process_item_extracts_cast(self, mock_load):
        """Test that _process_item extracts cast members."""
        mock_load.return_value = {'shows': {}, 'library_count': 0}

        cache = ShowCache('/tmp/cache')

        mock_actor = Mock()
        mock_actor.tag = 'Bryan Cranston'
        mock_show = Mock()
        mock_show.title = 'Test Show'
        mock_show.year = 2020
        mock_show.genres = []
        mock_show.studio = 'Netflix'
        mock_show.roles = [mock_actor]
        mock_show.summary = 'A test show'
        mock_show.guids = []

        result = cache._process_item(mock_show, None)

        assert 'Bryan Cranston' in result['cast']

    @patch('recommenders.base.load_media_cache')
    def test_process_item_extracts_studio(self, mock_load):
        """Test that _process_item extracts studio."""
        mock_load.return_value = {'shows': {}, 'library_count': 0}

        cache = ShowCache('/tmp/cache')

        mock_show = Mock()
        mock_show.title = 'Test Show'
        mock_show.year = 2020
        mock_show.genres = []
        mock_show.studio = 'HBO'
        mock_show.roles = []
        mock_show.summary = 'A test show'
        mock_show.guids = []

        result = cache._process_item(mock_show, None)

        assert result['studio'] == 'HBO'

    @patch('recommenders.base.load_media_cache')
    def test_process_item_handles_missing_attributes(self, mock_load):
        """Test that _process_item handles missing attributes gracefully."""
        mock_load.return_value = {'shows': {}, 'library_count': 0}

        cache = ShowCache('/tmp/cache')

        mock_show = Mock(spec=['title', 'guids'])
        mock_show.title = 'Minimal Show'
        mock_show.guids = []
        # Use del to ensure attributes don't exist
        del mock_show.year
        del mock_show.genres
        del mock_show.studio
        del mock_show.roles
        del mock_show.summary

        result = cache._process_item(mock_show, None)

        assert result['title'] == 'Minimal Show'
        assert result['year'] is None
        assert result['genres'] == []


class TestPlexTVRecommenderInit:
    """Tests for PlexTVRecommender initialization."""

    @patch('recommenders.tv.ShowCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_init_creates_show_cache(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that PlexTVRecommender creates a ShowCache."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.2, 'studio': 0.15, 'actor': 0.15, 'language': 0.05, 'keyword': 0.45}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')

        mock_cache.assert_called_once()

    @patch('recommenders.tv.ShowCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_init_sets_library_title(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that PlexTVRecommender sets library title from config."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc', 'TV_library_title': 'My TV Shows'},
            'general': {},
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')

        assert recommender.library_title == 'My TV Shows'


class TestPlexTVRecommenderWeights:
    """Tests for PlexTVRecommender weight loading."""

    @patch('recommenders.tv.ShowCache')
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
                'genre': 0.25,
                'studio': 0.20,
                'actor': 0.15,
                'language': 0.10,
                'keyword': 0.30
            }
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')

        assert recommender.weights['genre'] == 0.25
        assert recommender.weights['studio'] == 0.20
        assert recommender.weights['actor'] == 0.15

    @patch('recommenders.tv.ShowCache')
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
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')

        # Should have default weights
        assert 'genre' in recommender.weights
        assert 'studio' in recommender.weights
        assert 'actor' in recommender.weights


class TestPlexTVRecommenderLibraryMethods:
    """Tests for PlexTVRecommender library methods."""

    @patch('recommenders.tv.ShowCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_library_shows_set(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test _get_library_shows_set returns show tuples."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc', 'TV_library_title': 'TV Shows'},
            'general': {},
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}

        mock_show = Mock()
        mock_show.title = 'Breaking Bad'
        mock_show.year = 2008
        mock_show.ratingKey = 123
        mock_section = Mock()
        mock_section.all.return_value = [mock_show]
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')
        result = recommender._get_library_shows_set()

        assert ('breaking bad', 2008) in result

    @patch('recommenders.tv.ShowCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_library_shows_set_handles_embedded_year(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test _get_library_shows_set handles embedded year in title."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc', 'TV_library_title': 'TV Shows'},
            'general': {},
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}

        mock_show = Mock()
        mock_show.title = 'Doctor Who (2005)'
        mock_show.year = 2005
        mock_show.ratingKey = 456
        mock_section = Mock()
        mock_section.all.return_value = [mock_show]
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')
        result = recommender._get_library_shows_set()

        # Should have both versions
        assert ('doctor who (2005)', 2005) in result
        assert ('doctor who', 2005) in result


class TestPlexTVRecommenderSimilarity:
    """Tests for PlexTVRecommender similarity calculation."""

    @patch('recommenders.tv.calculate_similarity_score')
    @patch('recommenders.tv.ShowCache')
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
            'weights': {'genre': 0.2, 'studio': 0.15, 'actor': 0.15, 'language': 0.05, 'keyword': 0.45}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})
        mock_calc.return_value = (0.80, {'genre': 0.30, 'studio': 0.20})

        recommender = PlexTVRecommender('/path/to/config.yml')
        recommender.watched_data = {
            'genres': Counter({'drama': 5}),
            'studio': Counter({'hbo': 3}),
            'actors': Counter(),
            'languages': Counter(),
            'tmdb_keywords': Counter()
        }

        show_info = {
            'title': 'Test Show',
            'genres': ['drama'],
            'studio': 'hbo',
            'cast': [],
            'language': 'english',
            'tmdb_keywords': []
        }

        score, breakdown = recommender._calculate_similarity_from_cache(show_info)

        assert score == 0.80
        mock_calc.assert_called_once()


class TestPlexTVRecommenderWatchedCache:
    """Tests for PlexTVRecommender watched cache methods."""

    @patch('recommenders.base.save_watched_cache')
    @patch('recommenders.tv.ShowCache')
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
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')
        recommender.watched_data_counters = {'genres': Counter({'drama': 5})}
        recommender.watched_ids = {1, 2, 3}
        recommender.cached_watched_count = 10

        mock_save.reset_mock()  # Reset after init
        recommender._save_watched_cache()

        mock_save.assert_called_once()


class TestPlexTVRecommenderWatchedCount:
    """Tests for PlexTVRecommender._get_watched_count method."""

    @patch('recommenders.tv.get_watched_show_count')
    @patch('recommenders.tv.ShowCache')
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
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})
        mock_count.return_value = 25

        recommender = PlexTVRecommender('/path/to/config.yml')
        result = recommender._get_watched_count()

        assert result == 25


class TestPlexTVRecommenderTmdbMethods:
    """Tests for PlexTVRecommender TMDB-related methods."""

    @patch('recommenders.tv.ShowCache')
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
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')
        recommender.plex_tmdb_cache = {'123': 456}

        mock_show = Mock()
        mock_show.ratingKey = 123

        result = recommender._get_plex_item_tmdb_id(mock_show)

        assert result == 456

    @patch('recommenders.tv.ShowCache')
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
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')

        mock_guid = Mock()
        mock_guid.id = 'imdb://tt0903747'
        mock_show = Mock()
        mock_show.guids = [mock_guid]

        result = recommender._get_plex_item_imdb_id(mock_show)

        assert result == 'tt0903747'

    @patch('recommenders.base.get_tmdb_keywords')
    @patch('recommenders.tv.ShowCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_tmdb_keywords_for_id(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_keywords):
        """Test _get_tmdb_keywords_for_id returns keywords."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'testkey'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})
        mock_keywords.return_value = ['crime', 'drug dealer', 'chemistry']

        recommender = PlexTVRecommender('/path/to/config.yml')
        recommender._save_watched_cache = Mock()

        result = recommender._get_tmdb_keywords_for_id(12345)

        assert 'crime' in result
        assert 'drug dealer' in result


class TestPlexTVRecommenderRefreshWatchedData:
    """Tests for PlexTVRecommender._refresh_watched_data method."""

    @patch('recommenders.tv.ShowCache')
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
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')
        recommender.watched_ids = {1, 2, 3}
        recommender.watched_data_counters = {'genres': Counter({'drama': 5})}

        # Mock the methods called during refresh
        recommender._get_plex_watched_shows_data = Mock(return_value={'genres': Counter()})
        recommender._save_watched_cache = Mock()

        recommender._refresh_watched_data()

        assert len(recommender.watched_ids) == 0


class TestPlexTVRecommenderGetRecommendations:
    """Tests for PlexTVRecommender.get_recommendations method."""

    @patch('recommenders.tv.ShowCache')
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
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')
        recommender.watched_ids = set()
        recommender.cached_watched_count = 0
        recommender.watched_data = {'genres': Counter(), 'studio': Counter(), 'actors': Counter(), 'languages': Counter(), 'tmdb_keywords': Counter()}

        result = recommender.get_recommendations()

        assert isinstance(result, dict)
        assert 'plex_recommendations' in result

    @patch('recommenders.tv.ShowCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_recommendations_excludes_watched(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test get_recommendations excludes watched shows."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {'limit_plex_results': 10},
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst

        # Set up cache with shows
        mock_cache_inst = Mock()
        mock_cache_inst.cache = {
            'shows': {
                '1': {'title': 'Watched Show', 'genres': [], 'studio': '', 'cast': [], 'language': '', 'tmdb_keywords': []},
                '2': {'title': 'Unwatched Show', 'genres': [], 'studio': '', 'cast': [], 'language': '', 'tmdb_keywords': []},
            }
        }
        mock_cache_inst._save_cache = Mock()
        mock_cache.return_value = mock_cache_inst

        recommender = PlexTVRecommender('/path/to/config.yml')
        recommender.watched_ids = {1}  # Show 1 is watched
        recommender.cached_watched_count = 1
        recommender.watched_data = {'genres': Counter(), 'studio': Counter(), 'actors': Counter(), 'languages': Counter(), 'tmdb_keywords': Counter()}
        recommender._calculate_similarity_from_cache = Mock(return_value=(0.5, {}))

        result = recommender.get_recommendations()

        # Only the unwatched show should be in recommendations
        rec_titles = [r['title'] for r in result['plex_recommendations']]
        assert 'Watched Show' not in rec_titles


class TestFormatShowOutput:
    """Tests for format_show_output function."""

    def test_format_basic_show(self):
        """Test basic show formatting."""
        show = {
            'title': 'Breaking Bad',
            'year': 2008,
            'similarity_score': 0.92
        }

        result = format_show_output(show, index=1)

        assert 'Breaking Bad' in result
        assert '2008' in result
        assert '92' in result

    def test_format_with_genres(self):
        """Test show formatting with genres."""
        show = {
            'title': 'Breaking Bad',
            'year': 2008,
            'similarity_score': 0.85,
            'genres': ['drama', 'crime']
        }

        result = format_show_output(show, index=1)

        assert 'drama' in result or 'Drama' in result

    def test_format_with_cast(self):
        """Test show formatting with cast."""
        show = {
            'title': 'Breaking Bad',
            'year': 2008,
            'similarity_score': 0.85,
            'cast': ['Bryan Cranston', 'Aaron Paul']
        }

        result = format_show_output(show, index=1, show_cast=True)

        assert 'Bryan Cranston' in result

    def test_format_with_imdb_link(self):
        """Test show formatting with IMDB link."""
        show = {
            'title': 'Breaking Bad',
            'year': 2008,
            'similarity_score': 0.85,
            'imdb_id': 'tt0903747'
        }

        result = format_show_output(show, index=1, show_imdb_link=True)

        assert 'imdb.com' in result
        assert 'tt0903747' in result

    def test_format_with_summary(self):
        """Test show formatting with summary."""
        show = {
            'title': 'Breaking Bad',
            'year': 2008,
            'similarity_score': 0.85,
            'summary': 'A chemistry teacher turns to cooking meth.'
        }

        result = format_show_output(show, index=1, show_summary=True)

        assert 'chemistry' in result.lower() or 'meth' in result.lower()


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


class TestPlexTVRecommenderShowDetails:
    """Tests for PlexTVRecommender.get_show_details method."""

    @patch('recommenders.tv.ShowCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_show_details_returns_dict(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test get_show_details returns a dict with show info."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': False, 'api_key': None}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')

        mock_show = Mock()
        mock_show.title = 'Test Show'
        mock_show.year = 2020
        mock_show.summary = 'A test summary'
        mock_show.studio = 'Test Studio'
        mock_show.guids = []
        mock_show.genres = []
        mock_show.roles = []
        mock_show.reload = Mock()

        result = recommender.get_show_details(mock_show)

        assert isinstance(result, dict)
        assert result['title'] == 'Test Show'
        assert result['year'] == 2020


class TestPlexTVRecommenderPlexAccountIds:
    """Tests for PlexTVRecommender Plex account ID methods."""

    @patch('recommenders.tv.get_plex_account_ids')
    @patch('recommenders.tv.ShowCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_plex_account_ids_calls_utility(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_get_ids):
        """Test that _get_plex_account_ids uses utility function."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})
        mock_get_ids.return_value = {'user1': '12345'}

        recommender = PlexTVRecommender('/path/to/config.yml')
        result = recommender._get_plex_account_ids()

        assert result == {'user1': '12345'}
        mock_get_ids.assert_called()


class TestPlexTVRecommenderManageLabels:
    """Tests for PlexTVRecommender.manage_plex_labels method."""

    @patch('recommenders.tv.ShowCache')
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
            'weights': {},
            'collections': {'add_label': False}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        recommender = PlexTVRecommender('/path/to/config.yml')

        # Should not raise and should not call library methods
        recommender.manage_plex_labels([{'title': 'Test', 'year': 2020}])


class TestPlexTVRecommenderExcludedGenres:
    """Tests for genre exclusion in recommendations."""

    @patch('recommenders.tv.ShowCache')
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
            'weights': {}
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
            'shows': {
                '1': {'title': 'Horror Show', 'genres': ['horror'], 'studio': '', 'cast': [], 'language': '', 'tmdb_keywords': []},
                '2': {'title': 'Drama Show', 'genres': ['drama'], 'studio': '', 'cast': [], 'language': '', 'tmdb_keywords': []},
            }
        }
        mock_cache_inst._save_cache = Mock()
        mock_cache.return_value = mock_cache_inst

        recommender = PlexTVRecommender('/path/to/config.yml')
        recommender.watched_ids = set()
        recommender.cached_watched_count = 0
        recommender.watched_data = {'genres': Counter(), 'studio': Counter(), 'actors': Counter(), 'languages': Counter(), 'tmdb_keywords': Counter()}
        recommender._calculate_similarity_from_cache = Mock(return_value=(0.5, {}))

        result = recommender.get_recommendations()

        rec_titles = [r['title'] for r in result['plex_recommendations']]
        assert 'Horror Show' not in rec_titles
        assert 'Drama Show' in rec_titles


class TestExtractGenresFromShow:
    """Tests for extract_genres utility with TV shows."""

    def test_extract_genres_from_show(self):
        """Test extract_genres returns list of genres from show."""
        from utils import extract_genres

        mock_genre1 = Mock()
        mock_genre1.tag = 'Drama'
        mock_genre2 = Mock()
        mock_genre2.tag = 'Thriller'
        mock_show = Mock()
        mock_show.genres = [mock_genre1, mock_genre2]

        result = extract_genres(mock_show)

        assert 'drama' in result
        assert 'thriller' in result
