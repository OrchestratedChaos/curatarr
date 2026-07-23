"""
Tests for recommenders/tv.py - TV show recommendation system.
"""

import os
import copy
import pytest
from unittest.mock import Mock, patch, MagicMock
from collections import Counter
import json

from recommenders.tv import (
    ShowCache, PlexTVRecommender, format_show_output, adapt_root_config_to_legacy,
    process_recommendations, main,
)


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
            'plex': {'url': 'http://localhost', 'token': 'abc', 'tv_library': 'My TV Shows'},
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


class TestPlexTVRecommenderLibraryParam:
    """Tests for PlexTVRecommender library threading (#157 Phase 3)."""

    @patch('recommenders.tv.ShowCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_library_forwarded_to_base(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that the library param reaches BaseRecommender and sets
        library_id/library_title from the library dict, not the legacy
        tv_library config key."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc', 'tv_library': 'TV Shows'},
            'general': {},
            'weights': {},
            'libraries': [
                {'id': 'tv-shows', 'name': 'TV Shows', 'section': 'TV Shows', 'media_type': 'tv'},
                {'id': 'anime', 'name': 'Anime', 'section': 'Anime', 'media_type': 'tv'},
            ],
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={'shows': {}})

        library = {'id': 'anime', 'name': 'Anime', 'section': 'Anime', 'media_type': 'tv'}
        recommender = PlexTVRecommender('/path/to/config.yml', library=library)

        assert recommender.library_id == 'anime'
        assert recommender.library_title == 'Anime'

    @patch('recommenders.tv.ShowCache')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_no_library_keeps_legacy_title(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """library=None (default) resolves library_title from the legacy
        tv_library config key, unchanged from before Phase 3."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc', 'tv_library': 'TV Shows'},
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

        assert recommender.library_id is None
        assert recommender.library_title == 'TV Shows'


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


class TestProcessRecommendationsLibraryParam:
    """Tests for process_recommendations library forwarding (#157 Phase 3)."""

    @patch('recommenders.tv.PlexTVRecommender')
    @patch('recommenders.tv.teardown_log_file')
    @patch('recommenders.tv.setup_log_file')
    def test_forwards_library_to_recommender(self, mock_setup_log, mock_teardown, mock_recommender_cls):
        """process_recommendations passes library through to
        PlexTVRecommender's constructor unchanged."""
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {'plex_recommendations': []}
        mock_instance.config = {'general': {}}
        mock_recommender_cls.return_value = mock_instance

        library = {'id': 'anime', 'name': 'Anime', 'section': 'Anime', 'media_type': 'tv'}
        process_recommendations({'general': {}}, '/path/to/config.yml', 0, single_user='alice', library=library)

        mock_recommender_cls.assert_called_once_with('/path/to/config.yml', 'alice', library=library)

    @patch('recommenders.tv.PlexTVRecommender')
    @patch('recommenders.tv.teardown_log_file')
    @patch('recommenders.tv.setup_log_file')
    def test_defaults_library_to_none(self, mock_setup_log, mock_teardown, mock_recommender_cls):
        """process_recommendations defaults library=None (legacy callers)."""
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {'plex_recommendations': []}
        mock_instance.config = {'general': {}}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({'general': {}}, '/path/to/config.yml', 0, single_user='alice')

        mock_recommender_cls.assert_called_once_with('/path/to/config.yml', 'alice', library=None)


class TestMainMediaTypeKey:
    """Tests for main() passing media_type_key (#157 Phase 3)."""

    @patch('recommenders.tv.run_recommender_main')
    def test_main_passes_tv_media_type_key(self, mock_run_main):
        main()

        assert mock_run_main.call_count == 1
        _, kwargs = mock_run_main.call_args
        assert kwargs['media_type_key'] == 'tv'
        assert kwargs['process_func'] is process_recommendations


# ------------------------------------------------------------------------
# Core recommendation-engine coverage: rating-tier weighting, watched-history
# collection (incl. dropped-show negative signals), show detail extraction,
# and the per-user/per-library process_recommendations orchestration entry
# point (#157).
# ------------------------------------------------------------------------

TV_TEST_CONFIG = {
    'plex': {'url': 'http://localhost', 'token': 'abc'},
    'general': {},
    'weights': {'genre': 0.2, 'actor': 0.15, 'studio': 0.15, 'keyword': 0.45, 'language': 0.05},
}


def _make_tv_recommender(config=None, users=None, show_cache_data=None, config_path='/path/to/config.yml'):
    """Build a fully-initialized PlexTVRecommender with all I/O mocked out.

    See _make_movie_recommender in test_movie.py for the rationale (MagicMock
    Plex client for safe default iteration, __init__ eagerly gathers watched
    data for plex_users installs).
    """
    config = copy.deepcopy(config if config is not None else TV_TEST_CONFIG)
    users = users or {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
    show_cache = MagicMock()
    show_cache.cache = {'shows': show_cache_data or {}}
    with patch('recommenders.tv.ShowCache', return_value=show_cache), \
         patch('recommenders.base.init_plex', return_value=MagicMock()), \
         patch('recommenders.base.get_configured_users', return_value=users), \
         patch('recommenders.base.get_tmdb_config', return_value={'use_keywords': True, 'api_key': 'key'}), \
         patch('recommenders.base.load_config', return_value=config), \
         patch('os.makedirs'):
        recommender = PlexTVRecommender(config_path)
    return recommender


class TestCalculateRatingMultiplierTV:
    """Tests for PlexTVRecommender._calculate_rating_multiplier (rating-tier weighting)."""

    def test_none_rating_returns_unrated_default(self):
        recommender = _make_tv_recommender()
        assert recommender._calculate_rating_multiplier(None) == 0.6

    def test_five_star_rating_returns_full_weight(self):
        recommender = _make_tv_recommender()
        assert recommender._calculate_rating_multiplier(9.5) == 1.0

    def test_four_star_rating(self):
        recommender = _make_tv_recommender()
        assert recommender._calculate_rating_multiplier(7.5) == 0.75

    def test_three_star_rating(self):
        recommender = _make_tv_recommender()
        assert recommender._calculate_rating_multiplier(5.5) == 0.5

    def test_two_star_rating_above_threshold(self):
        recommender = _make_tv_recommender()
        assert recommender._calculate_rating_multiplier(4) == 0.25

    def test_low_rating_with_negative_signals_enabled_is_negative(self):
        recommender = _make_tv_recommender()
        result = recommender._calculate_rating_multiplier(1.0)
        assert result < 0

    def test_low_rating_with_negative_signals_globally_disabled(self):
        recommender = _make_tv_recommender()
        recommender.config['negative_signals'] = {'enabled': False}
        result = recommender._calculate_rating_multiplier(1.0)
        assert result == 0.25


class TestGetWatchedCountTV:
    """Tests for PlexTVRecommender._get_watched_count user-source branches."""

    @patch('recommenders.tv.get_watched_show_count', return_value=3)
    def test_uses_single_user_when_set(self, mock_count):
        recommender = _make_tv_recommender()
        recommender.single_user = 'alice'

        recommender._get_watched_count()

        assert mock_count.call_args[0][1] == ['alice']

    @patch('recommenders.base.MyPlexAccount')
    @patch('recommenders.tv.get_watched_show_count', return_value=3)
    def test_uses_managed_users_when_no_plex_users(self, mock_count, mock_account_cls):
        recommender = _make_tv_recommender(
            users={'plex_users': [], 'managed_users': ['bob'], 'admin_user': 'admin'}
        )

        recommender._get_watched_count()

        assert mock_count.call_args[0][1] == ['bob']


class TestGetPlexWatchedShowsData:
    """Tests for PlexTVRecommender._get_plex_watched_shows_data.

    __init__ eagerly calls this (plex_users installs gather watched data at
    construction time), so these tests patch the network-touching utilities
    *before* constructing the recommender and assert on the resulting state.
    """

    @patch('os.path.exists', return_value=False)
    @patch('recommenders.tv.get_plex_account_ids', return_value=[])
    @patch('recommenders.tv.get_watched_show_count', return_value=0)
    def test_returns_cached_counters_when_not_single_user_and_recalled(self, mock_count, mock_account_ids, mock_exists):
        recommender = _make_tv_recommender()
        cached = recommender.watched_data_counters
        assert cached

        result = recommender._get_plex_watched_shows_data()

        assert result is cached

    @patch('os.path.exists', return_value=False)
    @patch('recommenders.tv.process_counters_from_cache')
    @patch('recommenders.tv.calculate_rewatch_multiplier', return_value=1.0)
    @patch('recommenders.tv.calculate_recency_multiplier', return_value=1.0)
    @patch('recommenders.tv.fetch_plex_watch_history_shows')
    @patch('recommenders.tv.get_plex_account_ids')
    @patch('recommenders.tv.get_watched_show_count', return_value=1)
    def test_processes_watch_history_and_updates_counters(
        self, mock_count, mock_account_ids, mock_history, mock_recency, mock_rewatch,
        mock_process_counters, mock_exists
    ):
        mock_account_ids.return_value = ['acct1']
        mock_history.return_value = ({99}, {99: 1700000000})

        config = copy.deepcopy(TV_TEST_CONFIG)
        config['negative_signals'] = {'dropped_shows': {'enabled': False}}
        recommender = _make_tv_recommender(
            config=config, show_cache_data={'99': {'title': 'Watched Show', 'tmdb_id': 888}}
        )

        assert 99 in recommender.watched_ids
        mock_process_counters.assert_called_once()
        assert 888 in recommender.watched_data_counters['tmdb_ids']

    @patch('os.path.exists', return_value=False)
    @patch('recommenders.tv.log_error')
    @patch('recommenders.tv.get_plex_account_ids', return_value=[])
    @patch('recommenders.tv.get_watched_show_count', return_value=1)
    def test_no_account_ids_logs_error(self, mock_count, mock_account_ids, mock_log_error, mock_exists):
        _make_tv_recommender()

        mock_log_error.assert_called()

    @patch('os.path.exists', return_value=False)
    @patch('recommenders.tv.process_counters_from_cache')
    @patch('recommenders.tv.calculate_rewatch_multiplier', return_value=1.0)
    @patch('recommenders.tv.calculate_recency_multiplier', return_value=1.0)
    @patch('recommenders.tv.identify_dropped_shows')
    @patch('recommenders.tv.fetch_show_completion_data')
    @patch('recommenders.tv.fetch_plex_watch_history_shows')
    @patch('recommenders.tv.get_plex_account_ids')
    @patch('recommenders.tv.get_watched_show_count', return_value=1)
    def test_dropped_shows_processed_as_negative_signal(
        self, mock_count, mock_account_ids, mock_history, mock_completion, mock_identify,
        mock_recency, mock_rewatch, mock_process_counters, mock_exists
    ):
        mock_account_ids.return_value = ['acct1']
        mock_history.return_value = ({50}, {50: 1700000000})
        mock_completion.return_value = {50: {'watched_episodes': 2, 'total_episodes': 10, 'completion_percent': 20}}
        mock_identify.return_value = {50}

        recommender = _make_tv_recommender(show_cache_data={'50': {'title': 'Dropped Show', 'tmdb_id': 777}})

        # Dropped show still tracked (so it isn't re-recommended) but is
        # processed with a negative weight, not as a normal positive signal.
        assert 777 in recommender.watched_data_counters['tmdb_ids']
        mock_process_counters.assert_called_once()
        call_kwargs = mock_process_counters.call_args
        assert call_kwargs[1]['weight'] < 0

    @patch('os.path.exists', return_value=False)
    @patch('recommenders.tv.merge_show_watched_data')
    @patch('recommenders.tv.fetch_tautulli_show_watched_data')
    @patch('recommenders.tv.fetch_plex_watch_history_shows')
    @patch('recommenders.tv.get_plex_account_ids')
    @patch('recommenders.tv.get_watched_show_count', return_value=1)
    def test_merges_tautulli_history_when_enabled(
        self, mock_count, mock_account_ids, mock_history, mock_tautulli, mock_merge, mock_exists
    ):
        mock_account_ids.return_value = ['acct1']
        mock_history.return_value = (set(), {})
        mock_tautulli.return_value = ({60}, {60: 1700000000})
        mock_merge.return_value = ({60}, {60: 1700000000})

        config = copy.deepcopy(TV_TEST_CONFIG)
        config['tautulli'] = {'enabled': True}
        config['negative_signals'] = {'dropped_shows': {'enabled': False}}
        _make_tv_recommender(config=config, show_cache_data={})

        mock_merge.assert_called_once()


class TestGetShowDetails:
    """Tests for PlexTVRecommender.get_show_details."""

    @patch('recommenders.tv.extract_genres', return_value=['Drama'])
    @patch('recommenders.tv.extract_rating', return_value=7.5)
    @patch('recommenders.tv.extract_ids_from_guids', return_value={'imdb_id': 'tt1', 'tmdb_id': 1})
    def test_extracts_full_details(self, mock_ids, mock_rating, mock_genres):
        recommender = _make_tv_recommender()
        recommender.show_rating = True
        recommender.show_cast = True
        recommender.use_tmdb_keywords = False
        actor = Mock(tag='Actor A')
        show = Mock(title='Show A', year=2020, summary='Summary', studio='Studio A', roles=[actor])

        result = recommender.get_show_details(show)

        assert result['title'] == 'Show A'
        assert result['studio'] == 'Studio A'
        assert result['ratings']['audience_rating'] == 7.5
        assert 'Actor A' in result['cast']
        show.reload.assert_called_once()

    @patch('recommenders.tv.extract_ids_from_guids', return_value={'imdb_id': None, 'tmdb_id': None})
    def test_fetches_tmdb_keywords_when_enabled(self, mock_ids):
        recommender = _make_tv_recommender()
        recommender.use_tmdb_keywords = True
        recommender.tmdb_api_key = 'key'
        recommender._get_plex_item_tmdb_id = Mock(return_value=66)
        recommender._get_tmdb_keywords_for_id = Mock(return_value={'kw1'})
        show = Mock(title='Show B', year=2021, summary='', studio='N/A', roles=[])

        result = recommender.get_show_details(show)

        assert set(result['tmdb_keywords']) == {'kw1'}

    def test_handles_exception_returns_empty_dict(self):
        recommender = _make_tv_recommender()
        show = Mock()
        show.reload.side_effect = Exception("plex error")

        result = recommender.get_show_details(show)

        assert result == {}


class TestFindPlexItemAndWatchedDataSelectionTV:
    """Tests for _find_plex_item matching and _get_watched_data branch selection."""

    def test_find_plex_item_matches_title_and_year(self):
        recommender = _make_tv_recommender()
        section = Mock()
        match = Mock(title='X', year=2020)
        other = Mock(title='X', year=1999)
        section.search.return_value = [other, match]

        result = recommender._find_plex_item(section, {'title': 'X', 'year': 2020})

        assert result is match
        section.search.assert_called_once_with(title='X')

    def test_find_plex_item_returns_none_when_no_year_match(self):
        recommender = _make_tv_recommender()
        section = Mock()
        section.search.return_value = [Mock(title='X', year=1999)]

        result = recommender._find_plex_item(section, {'title': 'X', 'year': 2020})

        assert result is None

    def test_get_watched_data_uses_plex_history_when_plex_users_configured(self):
        recommender = _make_tv_recommender(
            users={'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        )
        recommender._get_plex_watched_shows_data = Mock(return_value={'genres': {}})
        recommender._get_managed_users_watched_data = Mock(return_value={'genres': {}})

        recommender._get_watched_data()

        recommender._get_plex_watched_shows_data.assert_called_once()
        recommender._get_managed_users_watched_data.assert_not_called()

    @patch('recommenders.base.MyPlexAccount')
    @patch('recommenders.tv.get_watched_show_count', return_value=0)
    def test_get_watched_data_uses_managed_users_when_no_plex_users(self, mock_count, mock_account_cls):
        recommender = _make_tv_recommender(
            users={'plex_users': [], 'managed_users': ['bob'], 'admin_user': 'admin'}
        )
        recommender._get_plex_watched_shows_data = Mock(return_value={'genres': {}})
        recommender._get_managed_users_watched_data = Mock(return_value={'genres': {}})

        recommender._get_watched_data()

        recommender._get_managed_users_watched_data.assert_called_once()
        recommender._get_plex_watched_shows_data.assert_not_called()


class TestGetLibraryShowsSetError:
    """Tests for PlexTVRecommender._get_library_shows_set exception handling."""

    def test_returns_empty_set_on_error(self):
        recommender = _make_tv_recommender()
        recommender.plex.library.section.side_effect = Exception("boom")

        result = recommender._get_library_shows_set()

        assert result == set()


class TestSaveCacheTV:
    """Tests for PlexTVRecommender._save_cache."""

    def test_save_cache_calls_save_watched_cache(self):
        recommender = _make_tv_recommender()
        recommender._save_watched_cache = Mock()

        recommender._save_cache()

        recommender._save_watched_cache.assert_called_once()


class TestCalculateSimilarityFranchiseBonus:
    """Tests for PlexTVRecommender._calculate_similarity_from_cache franchise/spinoff bonus."""

    @patch('recommenders.tv.calculate_similarity_score', return_value=(0.5, {'details': {}}))
    def test_shared_production_company_applies_bonus(self, mock_calc):
        recommender = _make_tv_recommender()
        recommender.watched_data = {
            'genres': {}, 'studios': {}, 'actors': {}, 'languages': {}, 'tmdb_keywords': {},
            'production_companies': {42: 3.0},
        }
        show_info = {'genres': [], 'studio': 'N/A', 'cast': [], 'language': 'N/A',
                     'tmdb_keywords': [], 'production_company_ids': [42]}

        score, breakdown = recommender._calculate_similarity_from_cache(show_info)

        assert score > 0.5
        assert 'franchise_bonus' in breakdown

    @patch('recommenders.tv.calculate_similarity_score', return_value=(0.5, {'details': {}}))
    def test_no_shared_production_company_no_bonus(self, mock_calc):
        recommender = _make_tv_recommender()
        recommender.watched_data = {
            'genres': {}, 'studios': {}, 'actors': {}, 'languages': {}, 'tmdb_keywords': {},
            'production_companies': {},
        }
        show_info = {'genres': [], 'studio': 'N/A', 'cast': [], 'language': 'N/A',
                     'tmdb_keywords': [], 'production_company_ids': [42]}

        score, breakdown = recommender._calculate_similarity_from_cache(show_info)

        assert score == 0.5
        assert 'franchise_bonus' not in breakdown


class TestProcessRecommendationsTV:
    """Tests for tv.process_recommendations (per-user/per-library orchestration, #157)."""

    @patch('recommenders.tv.format_show_output', return_value='formatted')
    @patch('recommenders.tv.PlexTVRecommender')
    @patch('recommenders.tv.teardown_log_file')
    @patch('recommenders.tv.setup_log_file')
    def test_happy_path_prints_and_manages_labels(self, mock_setup, mock_teardown, mock_recommender_cls, mock_format):
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {'plex_recommendations': [{'title': 'A', 'year': 2020}]}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({'general': {}}, '/path/to/config.yml', 0)

        mock_instance.manage_plex_labels.assert_called_once_with([{'title': 'A', 'year': 2020}])

    @patch('recommenders.tv.log_warning')
    @patch('recommenders.tv.PlexTVRecommender')
    @patch('recommenders.tv.teardown_log_file')
    @patch('recommenders.tv.setup_log_file')
    def test_no_recommendations_still_manages_labels(self, mock_setup, mock_teardown, mock_recommender_cls, mock_warn):
        """Unlike movie.process_recommendations, TV always calls
        manage_plex_labels (even with no new recs) to remove stale labels."""
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {'plex_recommendations': []}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({'general': {}}, '/path/to/config.yml', 0)

        mock_instance.manage_plex_labels.assert_called_once_with([])
        mock_warn.assert_called()

    @patch('recommenders.tv.PlexTVRecommender')
    @patch('recommenders.tv.teardown_log_file')
    @patch('recommenders.tv.setup_log_file')
    def test_exception_is_caught_and_printed(self, mock_setup, mock_teardown, mock_recommender_cls):
        mock_recommender_cls.side_effect = RuntimeError("boom")

        # Should not raise - exception is caught and logged.
        process_recommendations({'general': {}}, '/path/to/config.yml', 0)

        mock_teardown.assert_called_once()
