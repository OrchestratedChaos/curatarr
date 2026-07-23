"""
Tests for recommenders/base.py - Base cache and recommender classes.
"""

import os
import copy
import pytest
import requests
import plexapi.exceptions
from unittest.mock import Mock, patch, MagicMock
from collections import Counter

from recommenders.base import BaseCache, BaseRecommender


class ConcreteCache(BaseCache):
    """Concrete implementation of BaseCache for testing."""
    media_type = 'movie'
    media_key = 'movies'
    cache_filename = 'test_cache.json'

    def _process_item(self, item, tmdb_api_key):
        return {
            'title': item.title,
            'year': getattr(item, 'year', None),
            'genres': ['action', 'comedy']
        }


class TestBaseCacheInit:
    """Tests for BaseCache initialization."""

    @patch('recommenders.base.load_media_cache')
    def test_init_sets_cache_path(self, mock_load):
        """Test that cache path is set correctly."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = ConcreteCache('/tmp/cache')

        assert cache.cache_path == '/tmp/cache/test_cache.json'

    @patch('recommenders.base.load_media_cache')
    def test_init_loads_cache(self, mock_load):
        """Test that cache is loaded on init."""
        mock_load.return_value = {'movies': {'123': {'title': 'Test'}}, 'library_count': 1}

        cache = ConcreteCache('/tmp/cache')

        mock_load.assert_called_once()
        assert '123' in cache.cache['movies']

    @patch('recommenders.base.load_media_cache')
    def test_init_stores_recommender_reference(self, mock_load):
        """Test that recommender reference is stored."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}
        mock_recommender = Mock()

        cache = ConcreteCache('/tmp/cache', recommender=mock_recommender)

        assert cache.recommender is mock_recommender


class TestBaseCacheSave:
    """Tests for BaseCache save functionality."""

    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_save_cache_adds_version(self, mock_load, mock_save):
        """Test that save adds cache version."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = ConcreteCache('/tmp/cache')
        cache._save_cache()

        assert 'cache_version' in cache.cache
        mock_save.assert_called_once()


class TestBaseCacheUpdate:
    """Tests for BaseCache update functionality."""

    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_update_returns_false_when_up_to_date(self, mock_load, mock_save):
        """Test that update returns False when cache is current."""
        mock_load.return_value = {'movies': {}, 'library_count': 5}

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.all.return_value = [Mock() for _ in range(5)]
        mock_plex.library.section.return_value = mock_section

        cache = ConcreteCache('/tmp/cache')
        result = cache.update_cache(mock_plex, 'Movies')

        assert result is False

    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_update_processes_new_items(self, mock_load, mock_save):
        """Test that update processes new items."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        mock_item = Mock()
        mock_item.ratingKey = '123'
        mock_item.title = 'New Movie'
        mock_item.year = 2024

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.all.return_value = [mock_item]
        mock_plex.library.section.return_value = mock_section

        cache = ConcreteCache('/tmp/cache')
        result = cache.update_cache(mock_plex, 'Movies')

        assert result is True
        assert '123' in cache.cache['movies']

    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_update_removes_deleted_items(self, mock_load, mock_save):
        """Test that update removes items no longer in library."""
        mock_load.return_value = {
            'movies': {'old_id': {'title': 'Old Movie'}},
            'library_count': 0  # Different from current count to trigger update
        }

        mock_item = Mock()
        mock_item.ratingKey = 'new_id'
        mock_item.title = 'New Movie'

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.all.return_value = [mock_item]
        mock_plex.library.section.return_value = mock_section

        cache = ConcreteCache('/tmp/cache')
        cache.update_cache(mock_plex, 'Movies')

        assert 'old_id' not in cache.cache['movies']

    @patch('recommenders.base.log_warning')
    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_update_handles_item_processing_error(self, mock_load, mock_save, mock_warn):
        """Test that update continues when item processing fails."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        mock_item = Mock()
        mock_item.ratingKey = '123'
        mock_item.title = 'Bad Movie'
        mock_item.reload.side_effect = plexapi.exceptions.PlexApiException("Network error")

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.all.return_value = [mock_item]
        mock_plex.library.section.return_value = mock_section

        cache = ConcreteCache('/tmp/cache')
        result = cache.update_cache(mock_plex, 'Movies')

        assert result is True  # Still returns True (cache was updated)
        mock_warn.assert_called()


class TestBaseCacheGetLanguage:
    """Tests for BaseCache._get_language method."""

    @patch('recommenders.base.load_media_cache')
    def test_get_language_returns_na_when_no_media(self, mock_load):
        """Test that N/A is returned when item has no media."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = ConcreteCache('/tmp/cache')
        mock_item = Mock()
        mock_item.media = None

        result = cache._get_language(mock_item)

        assert result == "N/A"

    @patch('recommenders.base.get_full_language_name')
    @patch('recommenders.base.load_media_cache')
    def test_get_language_extracts_from_audio_stream(self, mock_load, mock_lang):
        """Test language extraction from audio stream."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}
        mock_lang.return_value = "English"

        cache = ConcreteCache('/tmp/cache')

        mock_audio = Mock()
        mock_audio.languageTag = 'en'
        mock_part = Mock()
        mock_part.audioStreams.return_value = [mock_audio]
        mock_media = Mock()
        mock_media.parts = [mock_part]
        mock_item = Mock()
        mock_item.media = [mock_media]

        result = cache._get_language(mock_item)

        assert result == "English"

    @patch('recommenders.base.load_media_cache')
    def test_get_language_for_tv_uses_first_episode(self, mock_load):
        """Test that TV shows use first episode for language."""
        mock_load.return_value = {'shows': {}, 'library_count': 0}

        # Create TV cache
        class TVCache(BaseCache):
            media_type = 'tv'
            media_key = 'shows'
            cache_filename = 'test_shows.json'
            def _process_item(self, item, tmdb_api_key):
                return {}

        cache = TVCache('/tmp/cache')

        mock_episode = Mock()
        mock_episode.media = None
        mock_show = Mock()
        mock_show.episodes.return_value = [mock_episode]

        result = cache._get_language(mock_show)

        mock_show.episodes.assert_called_once()

    @patch('recommenders.base.load_media_cache')
    def test_get_language_returns_na_on_exception(self, mock_load):
        """Test that N/A is returned on Plex API or attribute errors."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = ConcreteCache('/tmp/cache')
        mock_item = Mock()
        mock_item.media = Mock()
        mock_item.media.__iter__ = Mock(side_effect=AttributeError("Error"))

        result = cache._get_language(mock_item)

        assert result == "N/A"


class TestBaseCacheGetTmdbData:
    """Tests for BaseCache._get_tmdb_data method."""

    @patch('recommenders.base.extract_ids_from_guids')
    @patch('recommenders.base.load_media_cache')
    def test_get_tmdb_data_extracts_ids_from_guids(self, mock_load, mock_extract):
        """Test that IDs are extracted from GUIDs."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}
        mock_extract.return_value = {'imdb_id': 'tt123', 'tmdb_id': 456}

        cache = ConcreteCache('/tmp/cache')
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, None)

        assert result['imdb_id'] == 'tt123'
        assert result['tmdb_id'] == 456

    @patch('recommenders.base.get_tmdb_keywords')
    @patch('recommenders.base.extract_ids_from_guids')
    @patch('recommenders.base.load_media_cache')
    def test_get_tmdb_data_fetches_keywords(self, mock_load, mock_extract, mock_keywords):
        """Test that keywords are fetched from TMDB."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}
        mock_extract.return_value = {'imdb_id': None, 'tmdb_id': 123}
        mock_keywords.return_value = ['action', 'hero']

        cache = ConcreteCache('/tmp/cache')
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, 'api_key')

        assert result['keywords'] == ['action', 'hero']

    @patch('recommenders.base.fetch_tmdb_with_retry')
    @patch('recommenders.base.get_tmdb_keywords')
    @patch('recommenders.base.extract_ids_from_guids')
    @patch('recommenders.base.load_media_cache')
    def test_get_tmdb_data_fetches_movie_rating(self, mock_load, mock_extract, mock_keywords, mock_fetch):
        """Test that movie rating is fetched from TMDB."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}
        mock_extract.return_value = {'imdb_id': None, 'tmdb_id': 123}
        mock_keywords.return_value = []
        mock_fetch.return_value = {'vote_average': 7.5, 'vote_count': 1000}

        cache = ConcreteCache('/tmp/cache')
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, 'api_key')

        assert result['rating'] == 7.5
        assert result['vote_count'] == 1000

    @patch('recommenders.base.get_tmdb_id_for_item')
    @patch('recommenders.base.extract_ids_from_guids')
    @patch('recommenders.base.load_media_cache')
    def test_get_tmdb_data_falls_back_to_search(self, mock_load, mock_extract, mock_get_id):
        """Test fallback to TMDB search when no ID in GUIDs."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}
        mock_extract.return_value = {'imdb_id': None, 'tmdb_id': None}
        mock_get_id.return_value = 789

        cache = ConcreteCache('/tmp/cache')
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, 'api_key')

        mock_get_id.assert_called_once()
        assert result['tmdb_id'] == 789

    @patch('recommenders.base.extract_ids_from_guids')
    @patch('recommenders.base.load_media_cache')
    def test_get_tmdb_data_updates_recommender_caches(self, mock_load, mock_extract):
        """Test that recommender caches are updated."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}
        mock_extract.return_value = {'imdb_id': None, 'tmdb_id': 123}

        mock_recommender = Mock()
        mock_recommender.plex_tmdb_cache = {}
        mock_recommender.tmdb_keywords_cache = {}

        cache = ConcreteCache('/tmp/cache', recommender=mock_recommender)
        mock_item = Mock()
        mock_item.ratingKey = '456'

        cache._get_tmdb_data(mock_item, None)

        assert mock_recommender.plex_tmdb_cache['456'] == 123


class ConcreteRecommender(BaseRecommender):
    """Concrete implementation of BaseRecommender for testing."""
    media_type = 'movie'
    media_key = 'movies'
    library_config_key = 'movie_library'
    default_library_name = 'Movies'

    def _load_weights(self, weights_config):
        return {'genre': 0.5, 'actor': 0.5}

    def _get_watched_data(self):
        return {'genres': Counter(), 'actors': Counter()}

    def _get_watched_count(self):
        return 0

    def _save_watched_cache(self):
        pass

    def _get_media_cache(self):
        return Mock()

    def _find_plex_item(self, section, rec):
        return None

    def _calculate_similarity_from_cache(self, item_info):
        return (0.5, {})

    def _print_similarity_breakdown(self, item_info, score, breakdown):
        pass


class TestBaseRecommenderInit:
    """Tests for BaseRecommender initialization."""

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_init_loads_config(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test that config is loaded on init."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.5, 'actor': 0.5}
        }
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml')

        mock_load.assert_called_once_with('/path/to/config.yml')

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_init_connects_to_plex(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test that Plex connection is established."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.5, 'actor': 0.5}
        }
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml')

        mock_plex.assert_called_once()

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_init_loads_display_options(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test that display options are loaded from config."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {
                'show_summary': True,
                'show_cast': True,
                'limit_plex_results': 25
            },
            'weights': {'genre': 0.5, 'actor': 0.5}
        }
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml')

        assert recommender.show_summary is True
        assert recommender.show_cast is True
        assert recommender.limit_plex_results == 25

    @patch('recommenders.base.log_warning')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_init_warns_on_invalid_weights(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_warn):
        """Test that warning is logged when weights don't sum to 1.0."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.3, 'actor': 0.3}  # Sums to 0.6
        }
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        # Override _load_weights to return bad weights
        class BadWeightsRecommender(ConcreteRecommender):
            def _load_weights(self, weights_config):
                return {'genre': 0.3, 'actor': 0.3}

        recommender = BadWeightsRecommender('/path/to/config.yml')

        mock_warn.assert_called()


class TestBaseRecommenderGetUserContext:
    """Tests for BaseRecommender._get_user_context method."""

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_user_context_single_user(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test user context for single user mode."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.5, 'actor': 0.5}
        }
        mock_users.return_value = {'plex_users': ['user1'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml', single_user='testuser')
        result = recommender._get_user_context()

        assert result == 'plex_testuser'

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_user_context_plex_users(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test user context for plex users."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.5, 'actor': 0.5}
        }
        mock_users.return_value = {'plex_users': ['user1', 'user2'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml')
        result = recommender._get_user_context()

        assert result == 'plex_user1_user2'

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_get_user_context_sanitizes_special_chars(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test that special characters are removed from user context."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.5, 'actor': 0.5}
        }
        mock_users.return_value = {'plex_users': [], 'managed_users': ['user@email.com'], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml')
        result = recommender._get_user_context()

        assert '@' not in result
        assert '.' not in result


class TestBaseRecommenderRefreshWatchedData:
    """Tests for BaseRecommender._refresh_watched_data method."""

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_refresh_clears_existing_data(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test that refresh clears existing watched data."""
        mock_load.return_value = {
            'plex': {'url': 'http://localhost', 'token': 'abc'},
            'general': {},
            'weights': {'genre': 0.5, 'actor': 0.5}
        }
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml')
        recommender.watched_data_counters = {'genres': Counter({'action': 5})}
        recommender.watched_ids = {1, 2, 3}

        recommender._refresh_watched_data()

        assert len(recommender.watched_ids) == 0


class TestBaseCacheBackfillCollectionData:
    """Tests for BaseCache._backfill_collection_data method."""

    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_backfill_returns_false_when_no_movies_need_update(self, mock_load, mock_save):
        """Test backfill returns False when all movies have collection_id."""
        mock_load.return_value = {
            'movies': {
                '123': {'tmdb_id': 456, 'collection_id': 789, 'collection_name': 'Test Collection'}
            },
            'library_count': 1
        }

        cache = ConcreteCache('/tmp/cache')
        result = cache._backfill_collection_data('api_key')

        assert result is False

    @patch('recommenders.base.time.sleep')
    @patch('recommenders.base.fetch_tmdb_with_retry')
    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_backfill_updates_movies_missing_collection_id(self, mock_load, mock_save, mock_fetch, mock_sleep):
        """Test backfill adds collection data to movies missing it."""
        mock_load.return_value = {
            'movies': {
                '123': {'tmdb_id': 456, 'title': 'Test Movie'}  # No collection_id
            },
            'library_count': 1
        }
        mock_fetch.return_value = {
            'belongs_to_collection': {
                'id': 789,
                'name': 'Test Collection'
            }
        }

        cache = ConcreteCache('/tmp/cache')
        result = cache._backfill_collection_data('api_key')

        assert result is True
        assert cache.cache['movies']['123']['collection_id'] == 789
        assert cache.cache['movies']['123']['collection_name'] == 'Test Collection'

    @patch('recommenders.base.time.sleep')
    @patch('recommenders.base.fetch_tmdb_with_retry')
    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_backfill_sets_none_when_no_collection(self, mock_load, mock_save, mock_fetch, mock_sleep):
        """Test backfill sets None when movie has no collection."""
        mock_load.return_value = {
            'movies': {
                '123': {'tmdb_id': 456, 'title': 'Standalone Movie'}
            },
            'library_count': 1
        }
        mock_fetch.return_value = {'id': 456, 'title': 'Standalone Movie'}  # No belongs_to_collection key

        cache = ConcreteCache('/tmp/cache')
        result = cache._backfill_collection_data('api_key')

        assert result is True
        assert cache.cache['movies']['123']['collection_id'] is None
        assert cache.cache['movies']['123']['collection_name'] is None

    @patch('recommenders.base.time.sleep')
    @patch('recommenders.base.fetch_tmdb_with_retry')
    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_backfill_handles_fetch_error(self, mock_load, mock_save, mock_fetch, mock_sleep):
        """Test backfill continues when fetch fails."""
        mock_load.return_value = {
            'movies': {
                '123': {'tmdb_id': 456, 'title': 'Movie 1'},
                '124': {'tmdb_id': 457, 'title': 'Movie 2'}
            },
            'library_count': 2
        }
        mock_fetch.side_effect = [requests.RequestException("Network error"), {'belongs_to_collection': {'id': 1, 'name': 'Collection'}}]

        cache = ConcreteCache('/tmp/cache')
        result = cache._backfill_collection_data('api_key')

        # Should still return True as some movies were processed
        assert result is True

    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_backfill_skips_movies_without_tmdb_id(self, mock_load, mock_save):
        """Test backfill skips movies without TMDB ID."""
        mock_load.return_value = {
            'movies': {
                '123': {'title': 'No TMDB Movie'}  # No tmdb_id
            },
            'library_count': 1
        }

        cache = ConcreteCache('/tmp/cache')
        result = cache._backfill_collection_data('api_key')

        assert result is False


class TestBaseCacheUpdateWithBackfill:
    """Tests for BaseCache.update_cache with backfill integration."""

    @patch('recommenders.base.time.sleep')
    @patch('recommenders.base.fetch_tmdb_with_retry')
    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_update_triggers_backfill_when_cache_up_to_date(self, mock_load, mock_save, mock_fetch, mock_sleep):
        """Test that backfill runs even when cache is up to date."""
        mock_load.return_value = {
            'movies': {
                '123': {'tmdb_id': 456, 'title': 'Test Movie'}  # No collection_id
            },
            'library_count': 1
        }
        mock_fetch.return_value = {'belongs_to_collection': {'id': 789, 'name': 'Collection'}}

        mock_plex = Mock()
        mock_item = Mock()
        mock_item.ratingKey = '123'
        mock_section = Mock()
        mock_section.all.return_value = [mock_item]
        mock_plex.library.section.return_value = mock_section

        cache = ConcreteCache('/tmp/cache')
        result = cache.update_cache(mock_plex, 'Movies', tmdb_api_key='api_key')

        # Returns False (cache was up to date) but backfill should have run
        assert result is False
        assert cache.cache['movies']['123']['collection_id'] == 789


class TestBaseCacheGetTmdbDataWithCollection:
    """Tests for BaseCache._get_tmdb_data collection data extraction."""

    @patch('recommenders.base.fetch_tmdb_with_retry')
    @patch('recommenders.base.get_tmdb_keywords')
    @patch('recommenders.base.extract_ids_from_guids')
    @patch('recommenders.base.load_media_cache')
    def test_get_tmdb_data_extracts_collection_info(self, mock_load, mock_extract, mock_keywords, mock_fetch):
        """Test that collection info is extracted from TMDB response."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}
        mock_extract.return_value = {'imdb_id': None, 'tmdb_id': 123}
        mock_keywords.return_value = []
        mock_fetch.return_value = {
            'vote_average': 7.5,
            'vote_count': 1000,
            'belongs_to_collection': {
                'id': 456,
                'name': 'Marvel Collection'
            }
        }

        cache = ConcreteCache('/tmp/cache')
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, 'api_key')

        assert result['collection_id'] == 456
        assert result['collection_name'] == 'Marvel Collection'

    @patch('recommenders.base.fetch_tmdb_with_retry')
    @patch('recommenders.base.get_tmdb_keywords')
    @patch('recommenders.base.extract_ids_from_guids')
    @patch('recommenders.base.load_media_cache')
    def test_get_tmdb_data_handles_no_collection(self, mock_load, mock_extract, mock_keywords, mock_fetch):
        """Test that no collection is handled gracefully."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}
        mock_extract.return_value = {'imdb_id': None, 'tmdb_id': 123}
        mock_keywords.return_value = []
        mock_fetch.return_value = {'vote_average': 7.5, 'vote_count': 1000}

        cache = ConcreteCache('/tmp/cache')
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, 'api_key')

        assert result['collection_id'] is None
        assert result['collection_name'] is None


class TestBaseCacheGetTmdbDataKeywordsCache:
    """Tests for BaseCache._get_tmdb_data updating keyword caches."""

    @patch('recommenders.base.get_tmdb_keywords')
    @patch('recommenders.base.extract_ids_from_guids')
    @patch('recommenders.base.load_media_cache')
    def test_get_tmdb_data_updates_keywords_cache(self, mock_load, mock_extract, mock_keywords):
        """Test that TMDB keywords are cached on recommender."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}
        mock_extract.return_value = {'imdb_id': None, 'tmdb_id': 123}
        mock_keywords.return_value = ['action', 'hero', 'superhero']

        mock_recommender = Mock()
        mock_recommender.plex_tmdb_cache = {}
        mock_recommender.tmdb_keywords_cache = {}

        cache = ConcreteCache('/tmp/cache', recommender=mock_recommender)
        mock_item = Mock()
        mock_item.ratingKey = '456'

        cache._get_tmdb_data(mock_item, 'api_key')

        assert '123' in mock_recommender.tmdb_keywords_cache
        assert mock_recommender.tmdb_keywords_cache['123'] == ['action', 'hero', 'superhero']


class TestBaseCacheTVShowBackfill:
    """Tests for backfill behavior with TV shows."""

    @patch('recommenders.base.save_media_cache')
    @patch('recommenders.base.load_media_cache')
    def test_backfill_skips_tv_shows(self, mock_load, mock_save):
        """Test that backfill does not run for TV show caches."""
        mock_load.return_value = {'shows': {}, 'library_count': 1}

        class TVCache(BaseCache):
            media_type = 'tv'  # Not 'movie'
            media_key = 'shows'
            cache_filename = 'test_shows.json'
            def _process_item(self, item, tmdb_api_key):
                return {}

        mock_plex = Mock()
        mock_item = Mock()
        mock_item.ratingKey = '123'
        mock_section = Mock()
        mock_section.all.return_value = [mock_item]
        mock_plex.library.section.return_value = mock_section

        cache = TVCache('/tmp/cache')
        # TV caches don't have _backfill_collection_data called (only movies)
        # This test confirms the method exists but the update_cache only calls it for movies
        result = cache.update_cache(mock_plex, 'TV Shows', tmdb_api_key='api_key')

        # Should not error - backfill isn't called for TV
        assert result is False  # Cache was up to date


# ------------------------------------------------------------------------
# #157 Phase 3: per-library recommendation loop - library threading,
# cache-key back-compat, and collection/label naming.
# ------------------------------------------------------------------------

SINGLE_MOVIE_LIBRARY_CONFIG = {
    'plex': {'url': 'http://localhost', 'token': 'abc', 'movie_library': 'Movies'},
    'general': {},
    'weights': {'genre': 0.5, 'actor': 0.5},
}

MULTI_MOVIE_LIBRARY_CONFIG = {
    'plex': {'url': 'http://localhost', 'token': 'abc'},
    'general': {},
    'weights': {'genre': 0.5, 'actor': 0.5},
    'libraries': [
        {'id': 'movies', 'name': 'Movies', 'section': 'Movies', 'media_type': 'movie'},
        {'id': 'movies-4k', 'name': 'Movies 4K', 'section': 'Movies 4K', 'media_type': 'movie'},
    ],
}

LIB_MOVIES_4K = {'id': 'movies-4k', 'name': 'Movies 4K', 'section': 'Movies 4K', 'media_type': 'movie'}
LIB_MOVIES = {'id': 'movies', 'name': 'Movies', 'section': 'Movies', 'media_type': 'movie'}


class TestBaseRecommenderLibraryInit:
    """Tests for BaseRecommender.__init__ library threading (#157 Phase 3)."""

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_no_library_uses_legacy_resolution(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """library=None (default) keeps the legacy library_config_key lookup."""
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml')

        assert recommender.library is None
        assert recommender.library_id is None
        assert recommender.library_title == 'Movies'
        assert recommender._is_multi_library is False

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_synthesized_single_library_passed_stays_single(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Even when the (single, synthesized) library object IS passed
        through (as the new cli.py matrix loop always does), a single
        library for this media type must NOT trigger multi-library
        naming/cache behavior."""
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        synthesized = {'id': 'movies', 'name': 'Movies', 'section': 'Movies', 'media_type': 'movie'}
        recommender = ConcreteRecommender('/path/to/config.yml', library=synthesized)

        assert recommender.library == synthesized
        assert recommender.library_id == 'movies'
        assert recommender.library_title == 'Movies'
        assert recommender._is_multi_library is False

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_multi_library_sets_library_fields(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """When >1 library shares this media type, the given library's
        section/id are used and multi-library mode is flagged."""
        mock_load.return_value = MULTI_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml', library=LIB_MOVIES_4K)

        assert recommender.library_id == 'movies-4k'
        assert recommender.library_title == 'Movies 4K'
        assert recommender._is_multi_library is True


class TestBaseRecommenderCacheLibraryPrefix:
    """Tests for per-library cache filename back-compat (#157 Phase 3)."""

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_single_library_user_context_unprefixed(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Single-library install (legacy, no library passed): user context
        has no library prefix, so watched_cache_{user}.json is unchanged."""
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {'plex_users': ['jason'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml')

        assert recommender._get_user_context() == 'plex_jason'

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_synthesized_single_library_passed_stays_unprefixed(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Same as above but library IS passed (matrix loop always passes
        one) - still unprefixed because it's the sole library for the
        media type. This is the single-library byte-identical proof for
        cache filenames."""
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {'plex_users': ['jason'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        synthesized = {'id': 'movies', 'name': 'Movies', 'section': 'Movies', 'media_type': 'movie'}
        recommender = ConcreteRecommender('/path/to/config.yml', library=synthesized)

        assert recommender._get_user_context() == 'plex_jason'

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_multi_library_user_context_prefixed(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Multi-library install: user context gets a library-id prefix, so
        watched caches for different libraries never collide."""
        mock_load.return_value = MULTI_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {'plex_users': ['jason'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        movies_recommender = ConcreteRecommender('/path/to/config.yml', library=LIB_MOVIES)
        movies_4k_recommender = ConcreteRecommender('/path/to/config.yml', library=LIB_MOVIES_4K)

        assert movies_recommender._get_user_context() == 'movies_plex_jason'
        assert movies_4k_recommender._get_user_context() == 'movies-4k_plex_jason'
        # Distinct filenames - no cross-library cache collision
        assert movies_recommender._get_user_context() != movies_4k_recommender._get_user_context()


class TestBaseRecommenderCollectionNaming:
    """Tests for per-library collection/label naming (#157 Phase 3)."""

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_single_library_no_suffix(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml')

        assert recommender._library_suffix_for_collection_name() == ''
        assert recommender._library_suffix_for_label() == ''
        assert recommender._cache_library_prefix() == ''

    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_multi_library_adds_suffixes(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        mock_load.return_value = MULTI_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender('/path/to/config.yml', library=LIB_MOVIES_4K)

        assert recommender._library_suffix_for_collection_name() == ' (Movies 4K)'
        assert recommender._library_suffix_for_label() == '_movies-4k'
        assert recommender._cache_library_prefix() == 'movies-4k_'

    @patch('recommenders.base.cleanup_old_collections')
    @patch('recommenders.base.update_plex_collection')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_sync_plex_collection_single_library_name_unchanged(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup
    ):
        """Single-library install: collection name is byte-identical to
        pre-Phase-3 (no suffix)."""
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender('/path/to/config.yml')
        section = Mock()

        recommender._sync_plex_collection(section, 'Recommended_alice', [Mock()])

        collection_name = mock_update.call_args[0][1]
        assert collection_name == "🎬 Alice - Recommendation"
        mock_cleanup.assert_called_once()
        assert mock_cleanup.call_args[0][1] == "🎬 Alice - Recommendation"

    @patch('recommenders.base.cleanup_old_collections')
    @patch('recommenders.base.update_plex_collection')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_sync_plex_collection_multi_library_adds_suffix(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup
    ):
        """Multi-library install: collection name is suffixed with the
        library name so same-named collections across libraries are
        distinguishable."""
        mock_load.return_value = MULTI_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender('/path/to/config.yml', library=LIB_MOVIES_4K)
        section = Mock()

        recommender._sync_plex_collection(section, 'Recommended_alice', [Mock()])

        collection_name = mock_update.call_args[0][1]
        assert collection_name == "🎬 Alice - Recommendation (Movies 4K)"

    @patch('recommenders.base.build_label_name')
    @patch('recommenders.base.init_plex')
    @patch('recommenders.base.get_configured_users')
    @patch('recommenders.base.get_tmdb_config')
    @patch('recommenders.base.load_config')
    @patch('os.makedirs')
    def test_manage_plex_labels_qualifies_label_for_multi_library(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_build_label
    ):
        """Multi-library install: the internal Plex label is qualified with
        the library id so labeling doesn't collide across libraries."""
        mock_load.return_value = MULTI_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {'plex_users': ['alice'], 'managed_users': [], 'admin_user': 'admin'}
        mock_tmdb.return_value = {'use_keywords': True, 'api_key': 'key'}
        mock_plex.return_value = Mock()
        mock_build_label.return_value = 'Recommended_movies-4k_alice'

        recommender = ConcreteRecommender('/path/to/config.yml', library=LIB_MOVIES_4K)
        recommender.plex = Mock()
        # Short-circuit deep inside manage_plex_labels right after label_name
        # is built, by making item-finding raise - we only care about the
        # base_label passed into build_label_name.
        recommender._find_plex_items_for_recs = Mock(side_effect=RuntimeError("stop"))

        try:
            recommender.manage_plex_labels([{'title': 'Test', 'year': 2020}])
        except Exception:
            pass

        assert mock_build_label.called
        base_label_arg = mock_build_label.call_args[0][0]
        assert base_label_arg == 'Recommended_movies-4k'


# ------------------------------------------------------------------------
# Core recommendation-engine coverage: label management, candidate scoring,
# and the TMDB/IMDb id-resolution chain shared by movie.py and tv.py.
# ------------------------------------------------------------------------

class ConcreteTVRecommender(BaseRecommender):
    """Concrete TV implementation of BaseRecommender for testing shared logic."""
    media_type = 'tv'
    media_key = 'shows'
    library_config_key = 'tv_library'
    default_library_name = 'TV Shows'

    def _load_weights(self, weights_config):
        return {'genre': 0.5, 'actor': 0.5}

    def _get_watched_data(self):
        return {'genres': Counter(), 'actors': Counter()}

    def _get_watched_count(self):
        return 0

    def _save_watched_cache(self):
        pass

    def _get_media_cache(self):
        return Mock()

    def _find_plex_item(self, section, rec):
        return None

    def _calculate_similarity_from_cache(self, item_info):
        return (0.5, {})

    def _print_similarity_breakdown(self, item_info, score, breakdown):
        pass


def _make_recommender(config=None, users=None, library=None, recommender_cls=ConcreteRecommender,
                       config_path='/path/to/config.yml'):
    """Build a fully-initialized recommender with Plex/TMDB/config init mocked out.

    Deep-copies the config so tests are free to mutate recommender.config
    without polluting the shared module-level fixture dicts.
    """
    config = copy.deepcopy(config if config is not None else SINGLE_MOVIE_LIBRARY_CONFIG)
    users = users or {'plex_users': [], 'managed_users': [], 'admin_user': 'admin'}
    with patch('recommenders.base.load_config', return_value=config), \
         patch('recommenders.base.get_configured_users', return_value=users), \
         patch('recommenders.base.get_tmdb_config', return_value={'use_keywords': True, 'api_key': 'key'}), \
         patch('recommenders.base.init_plex', return_value=Mock()), \
         patch('os.makedirs'):
        return recommender_cls(config_path, library=library)


class TestGetManagedUsersWatchedData:
    """Tests for BaseRecommender._get_managed_users_watched_data."""

    def test_returns_cached_data_when_not_single_user(self):
        recommender = _make_recommender()
        recommender.watched_data_counters = {'genres': Counter({'a': 1})}
        result = recommender._get_managed_users_watched_data()
        assert result == recommender.watched_data_counters

    def test_returns_cached_data_when_single_user(self):
        recommender = _make_recommender()
        recommender.single_user = 'alice'
        recommender.watched_data_counters = {'genres': Counter({'a': 1})}
        result = recommender._get_managed_users_watched_data()
        assert result == recommender.watched_data_counters

    @patch('recommenders.base.MyPlexAccount')
    def test_admin_user_uses_direct_plex_connection(self, mock_account_cls):
        recommender = _make_recommender(users={'plex_users': [], 'managed_users': ['admin'], 'admin_user': 'admin'})
        recommender.watched_data_counters = {}
        recommender.plex = Mock()
        item = Mock(ratingKey='10')
        recommender.plex.library.section.return_value.search.return_value = [item]
        media_cache = Mock()
        media_cache.cache = {'movies': {'10': {'tmdb_id': 555}}}
        recommender._get_media_cache = Mock(return_value=media_cache)

        result = recommender._get_managed_users_watched_data()

        assert 10 in recommender.watched_ids
        assert 555 in result['tmdb_ids']
        mock_account_cls.return_value.user.assert_not_called()

    @patch('recommenders.base.MyPlexAccount')
    def test_non_admin_user_switches_user(self, mock_account_cls):
        recommender = _make_recommender(users={'plex_users': [], 'managed_users': ['bob'], 'admin_user': 'admin'})
        recommender.watched_data_counters = {}
        recommender.plex = Mock()
        switched_plex = Mock()
        recommender.plex.switchUser.return_value = switched_plex
        switched_plex.library.section.return_value.search.return_value = []
        media_cache = Mock()
        media_cache.cache = {'movies': {}}
        recommender._get_media_cache = Mock(return_value=media_cache)

        recommender._get_managed_users_watched_data()

        recommender.plex.switchUser.assert_called_once()

    @patch('recommenders.base.log_error')
    @patch('recommenders.base.MyPlexAccount')
    def test_user_processing_error_continues_to_next_user(self, mock_account_cls, mock_log_error):
        recommender = _make_recommender(
            users={'plex_users': [], 'managed_users': ['bob', 'admin'], 'admin_user': 'admin'}
        )
        recommender.watched_data_counters = {}
        recommender.plex = Mock()
        recommender.plex.switchUser.side_effect = plexapi.exceptions.PlexApiException("fail")
        recommender.plex.library.section.return_value.search.return_value = []
        media_cache = Mock()
        media_cache.cache = {'movies': {}}
        recommender._get_media_cache = Mock(return_value=media_cache)

        recommender._get_managed_users_watched_data()

        mock_log_error.assert_called()

    @patch('recommenders.base.MyPlexAccount')
    def test_single_user_admin_alias_uses_admin_user(self, mock_account_cls):
        recommender = _make_recommender(users={'plex_users': [], 'managed_users': [], 'admin_user': 'admin'})
        recommender.single_user = 'Administrator'
        recommender.watched_data_counters = {}
        recommender.plex = Mock()
        recommender.plex.library.section.return_value.search.return_value = []
        media_cache = Mock()
        media_cache.cache = {'movies': {}}
        recommender._get_media_cache = Mock(return_value=media_cache)

        recommender._get_managed_users_watched_data()

        recommender.plex.switchUser.assert_not_called()


class TestFindPlexItemsForRecs:
    """Tests for BaseRecommender._find_plex_items_for_recs."""

    def test_finds_by_rating_key(self):
        recommender = _make_recommender()
        recommender.plex = Mock()
        found_item = Mock()
        recommender.plex.fetchItem.return_value = found_item
        section = Mock()
        selected = [{'title': 'A', 'year': 2020, 'plex_rating_key': 123}]

        items_found, skipped = recommender._find_plex_items_for_recs(section, selected)

        assert items_found == [found_item]
        assert skipped == []
        found_item.reload.assert_called_once()

    def test_falls_back_to_fuzzy_search_on_fetch_error(self):
        recommender = _make_recommender()
        recommender.plex = Mock()
        recommender.plex.fetchItem.side_effect = Exception("not found")
        found_item = Mock()
        recommender._find_plex_item = Mock(return_value=found_item)
        section = Mock()
        selected = [{'title': 'A', 'year': 2020, 'plex_rating_key': 123}]

        items_found, skipped = recommender._find_plex_items_for_recs(section, selected)

        assert items_found == [found_item]
        recommender._find_plex_item.assert_called_once_with(section, selected[0])

    def test_no_rating_key_uses_fuzzy_search(self):
        recommender = _make_recommender()
        recommender.plex = Mock()
        recommender._find_plex_item = Mock(return_value=None)
        section = Mock()
        selected = [{'title': 'Missing', 'year': 2019}]

        items_found, skipped = recommender._find_plex_items_for_recs(section, selected)

        assert items_found == []
        assert skipped == ['Missing (2019)']


class TestRemoveOutdatedLabels:
    """Tests for BaseRecommender._remove_outdated_labels."""

    @patch('recommenders.base.remove_labels_from_items')
    @patch('recommenders.base.categorize_labeled_items')
    @patch('recommenders.base.get_excluded_genres_for_user', return_value=[])
    def test_removes_watched_and_excluded_returns_fresh(self, mock_excl, mock_categorize, mock_remove):
        recommender = _make_recommender()
        section = Mock()
        fresh_item, watched_item, excluded_item = Mock(), Mock(), Mock()
        section.search.return_value = [fresh_item, watched_item, excluded_item]
        mock_categorize.return_value = {
            'fresh': [fresh_item], 'watched': [watched_item], 'excluded': [excluded_item], 'stale': []
        }

        result = recommender._remove_outdated_labels(section, 'Recommended_alice', 7)

        assert result == [fresh_item]
        assert mock_remove.call_count == 2
        reasons = {call.args[3] for call in mock_remove.call_args_list}
        assert reasons == {'watched', 'excluded genre'}


class TestBuildScoredCandidates:
    """Tests for BaseRecommender._build_scored_candidates."""

    def test_scores_labeled_items_from_cache(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {'movies': {'1': {'title': 'A'}}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender._calculate_similarity_from_cache = Mock(return_value=(0.8, {}))
        labeled_item = Mock(ratingKey=1, title='A')

        result = recommender._build_scored_candidates([labeled_item], [], [])

        assert result[1] == (labeled_item, 0.8)

    def test_labeled_item_not_in_cache_gets_zero_score(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {'movies': {}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        labeled_item = Mock(ratingKey=2, title='B')

        result = recommender._build_scored_candidates([labeled_item], [], [])

        assert result[2] == (labeled_item, 0.0)

    def test_scoring_exception_defaults_to_zero(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {'movies': {'3': {'title': 'C'}}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender._calculate_similarity_from_cache = Mock(side_effect=Exception("boom"))
        labeled_item = Mock(ratingKey=3, title='C')

        result = recommender._build_scored_candidates([labeled_item], [], [])

        assert result[3] == (labeled_item, 0.0)

    def test_selected_items_matched_by_rating_key(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {'movies': {}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender.watched_ids = set()
        plex_item = Mock(ratingKey=5, isPlayed=False)
        selected = [{'title': 'D', 'year': 2021, 'plex_rating_key': 5, 'similarity_score': 0.6}]

        result = recommender._build_scored_candidates([], selected, [plex_item])

        assert result[5] == (plex_item, 0.6)

    def test_selected_items_fallback_title_year_match(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {'movies': {}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender.watched_ids = set()
        plex_item = Mock(ratingKey=6, title='E', year=2018, isPlayed=False)
        selected = [{'title': 'E', 'year': 2018, 'similarity_score': 0.4}]

        result = recommender._build_scored_candidates([], selected, [plex_item])

        assert result[6] == (plex_item, 0.4)

    def test_watched_selected_item_excluded(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {'movies': {}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender.watched_ids = {7}
        plex_item = Mock(ratingKey=7, isPlayed=False)
        selected = [{'title': 'F', 'year': 2020, 'plex_rating_key': 7, 'similarity_score': 0.9}]

        result = recommender._build_scored_candidates([], selected, [plex_item])

        assert 7 not in result


class TestFilterCandidatesByRating:
    """Tests for BaseRecommender._filter_candidates_by_rating."""

    def test_no_max_rating_returns_unchanged(self):
        recommender = _make_recommender()
        candidates = {1: (Mock(), 0.5)}

        result = recommender._filter_candidates_by_rating(candidates, None)

        assert result is candidates

    @patch('recommenders.base.is_rating_allowed')
    def test_filters_disallowed_ratings(self, mock_allowed):
        recommender = _make_recommender()
        allowed_item = Mock(contentRating='PG-13')
        blocked_item = Mock(contentRating='R')
        mock_allowed.side_effect = lambda rating, max_rating, media_type: rating == 'PG-13'
        candidates = {1: (allowed_item, 0.5), 2: (blocked_item, 0.7)}

        result = recommender._filter_candidates_by_rating(candidates, 'PG-13')

        assert 1 in result
        assert 2 not in result


class TestUpdateLabelsByRank:
    """Tests for BaseRecommender._update_labels_by_rank."""

    @patch('recommenders.base.add_labels_to_items')
    @patch('recommenders.base.remove_labels_from_items')
    def test_keeps_top_scoring_and_evicts_rest(self, mock_remove, mock_add):
        recommender = _make_recommender()
        item_high = Mock(ratingKey=1)
        item_low = Mock(ratingKey=2)
        item_new = Mock(ratingKey=3)
        candidates = {1: (item_high, 0.9), 2: (item_low, 0.1), 3: (item_new, 0.8)}
        unwatched_labeled = [item_high, item_low]

        result = recommender._update_labels_by_rank(candidates, unwatched_labeled, 'Recommended_alice', target_count=2)

        result_keys = {int(i.ratingKey) for i in result}
        assert result_keys == {1, 3}
        mock_remove.assert_called_once()
        mock_add.assert_called_once()

    @patch('recommenders.base.add_labels_to_items')
    @patch('recommenders.base.remove_labels_from_items')
    def test_no_changes_when_already_optimal(self, mock_remove, mock_add):
        recommender = _make_recommender()
        item = Mock(ratingKey=1)
        candidates = {1: (item, 0.9)}

        result = recommender._update_labels_by_rank(candidates, [item], 'Recommended_alice', target_count=1)

        mock_remove.assert_not_called()
        mock_add.assert_not_called()
        assert result == [item]


class TestSyncPlexCollectionEmpty:
    """Tests for BaseRecommender._sync_plex_collection with no items."""

    @patch('recommenders.base.update_plex_collection')
    def test_returns_false_when_no_final_items(self, mock_update):
        recommender = _make_recommender()

        result = recommender._sync_plex_collection(Mock(), 'Recommended_alice', [])

        assert result is False
        mock_update.assert_not_called()


class TestManagePlexLabelsFullFlow:
    """Tests for BaseRecommender.manage_plex_labels orchestration."""

    def _base_recommender(self, users=None):
        users = users or {'plex_users': ['alice'], 'managed_users': [], 'admin_user': 'admin'}
        recommender = _make_recommender(users=users)
        recommender.plex = Mock()
        recommender.config['collections'] = {
            'add_label': True, 'label_name': 'Recommended',
            'append_usernames': False, 'private_collections': False,
        }
        recommender.confirm_operations = False
        recommender._find_plex_items_for_recs = Mock(return_value=([Mock()], []))
        recommender._remove_outdated_labels = Mock(return_value=[])
        recommender._build_scored_candidates = Mock(return_value={1: (Mock(), 0.9)})
        recommender._update_labels_by_rank = Mock(return_value=[Mock()])
        recommender._sync_plex_collection = Mock(return_value=True)
        recommender._save_watched_cache = Mock()
        return recommender

    @patch('recommenders.base.build_label_name', return_value='Recommended_alice')
    def test_happy_path_returns_sync_result(self, mock_build_label):
        recommender = self._base_recommender()

        result = recommender.manage_plex_labels([{'title': 'Movie', 'year': 2020}])

        assert result is True
        recommender._sync_plex_collection.assert_called_once()

    @patch('recommenders.base.build_label_name', return_value='Recommended_alice')
    def test_no_items_found_returns_false(self, mock_build_label):
        recommender = self._base_recommender()
        recommender._find_plex_items_for_recs = Mock(return_value=([], ['Skipped Movie (2020)']))

        result = recommender.manage_plex_labels([{'title': 'Movie', 'year': 2020}])

        assert result is False
        recommender._sync_plex_collection.assert_not_called()

    @patch('recommenders.base.build_label_name', return_value='Recommended_alice')
    def test_confirm_operations_uses_user_selection(self, mock_build_label):
        recommender = self._base_recommender()
        recommender.confirm_operations = True
        recommender._user_select_recommendations = Mock(return_value=[{'title': 'Movie', 'year': 2020}])

        recommender.manage_plex_labels([{'title': 'Movie', 'year': 2020}])

        recommender._user_select_recommendations.assert_called_once()

    @patch('recommenders.base.build_label_name', return_value='Recommended_alice')
    def test_confirm_operations_empty_selection_passes_empty_list(self, mock_build_label):
        recommender = self._base_recommender()
        recommender.confirm_operations = True
        recommender._user_select_recommendations = Mock(return_value=[])

        recommender.manage_plex_labels([{'title': 'Movie', 'year': 2020}])

        args = recommender._find_plex_items_for_recs.call_args[0]
        assert args[1] == []

    @patch('recommenders.base.apply_user_label_restrictions')
    @patch('recommenders.base.build_label_name', return_value='Recommended_alice')
    def test_private_collections_applies_restrictions(self, mock_build_label, mock_apply):
        recommender = self._base_recommender()
        recommender.config['collections']['private_collections'] = True

        recommender.manage_plex_labels([{'title': 'Movie', 'year': 2020}])

        mock_apply.assert_called_once()

    @patch('recommenders.base.get_max_rating_for_user', return_value='PG-13')
    @patch('recommenders.base.build_label_name', return_value='Recommended_alice')
    def test_max_rating_filters_candidates(self, mock_build_label, mock_max_rating):
        recommender = self._base_recommender()
        recommender._filter_candidates_by_rating = Mock(return_value={1: (Mock(), 0.9)})

        recommender.manage_plex_labels([{'title': 'Movie', 'year': 2020}])

        recommender._filter_candidates_by_rating.assert_called_once()

    def test_no_recommendations_returns_false(self):
        recommender = self._base_recommender()

        result = recommender.manage_plex_labels([])

        assert result is False

    def test_add_label_disabled_returns_false(self):
        recommender = self._base_recommender()
        recommender.config['collections']['add_label'] = False

        result = recommender.manage_plex_labels([{'title': 'Movie'}])

        assert result is False


class TestManagePlexLabelsExceptionHandling:
    """Tests for BaseRecommender.manage_plex_labels error handling."""

    def test_plex_exception_returns_false(self):
        recommender = _make_recommender(users={'plex_users': ['alice'], 'managed_users': [], 'admin_user': 'admin'})
        recommender.plex = Mock()
        recommender.plex.library.section.side_effect = plexapi.exceptions.PlexApiException("boom")

        result = recommender.manage_plex_labels([{'title': 'Movie', 'year': 2020}])

        assert result is False


class TestGetPlexItemTmdbId:
    """Tests for BaseRecommender._get_plex_item_tmdb_id cache-miss path."""

    @patch('recommenders.base.get_tmdb_id_for_item')
    def test_cache_miss_saves_and_returns_id(self, mock_get_id):
        recommender = _make_recommender()
        recommender._save_watched_cache = Mock()
        mock_get_id.return_value = 999
        plex_item = Mock(ratingKey='42')

        result = recommender._get_plex_item_tmdb_id(plex_item)

        assert result == 999
        assert recommender.plex_tmdb_cache['42'] == 999
        recommender._save_watched_cache.assert_called_once()

    @patch('recommenders.base.get_tmdb_id_for_item')
    def test_cache_miss_no_id_found_does_not_save(self, mock_get_id):
        recommender = _make_recommender()
        recommender._save_watched_cache = Mock()
        mock_get_id.return_value = None
        plex_item = Mock(ratingKey='42')

        result = recommender._get_plex_item_tmdb_id(plex_item)

        assert result is None
        recommender._save_watched_cache.assert_not_called()


class TestGetPlexItemImdbId:
    """Tests for BaseRecommender._get_plex_item_imdb_id fallback chain."""

    @patch('recommenders.base.extract_ids_from_guids')
    def test_returns_imdb_from_guids(self, mock_extract):
        recommender = _make_recommender()
        mock_extract.return_value = {'imdb_id': 'tt111', 'tmdb_id': None}

        result = recommender._get_plex_item_imdb_id(Mock())

        assert result == 'tt111'

    @patch('recommenders.base.extract_ids_from_guids')
    def test_falls_back_to_legacy_guid_attribute(self, mock_extract):
        recommender = _make_recommender()
        mock_extract.return_value = {'imdb_id': None, 'tmdb_id': None}
        plex_item = Mock(guid='imdb://tt222')

        result = recommender._get_plex_item_imdb_id(plex_item)

        assert result == 'tt222'

    @patch('recommenders.base.fetch_tmdb_with_retry')
    @patch('recommenders.base.extract_ids_from_guids')
    def test_falls_back_to_tmdb_movie_lookup(self, mock_extract, mock_fetch):
        recommender = _make_recommender()  # media_type == 'movie'
        mock_extract.return_value = {'imdb_id': None, 'tmdb_id': None}
        recommender._get_plex_item_tmdb_id = Mock(return_value=555)
        mock_fetch.return_value = {'imdb_id': 'tt333'}
        plex_item = Mock(guid=None)

        result = recommender._get_plex_item_imdb_id(plex_item)

        assert result == 'tt333'
        assert 'movie/555' in mock_fetch.call_args[0][0]

    @patch('recommenders.base.fetch_tmdb_with_retry')
    @patch('recommenders.base.extract_ids_from_guids')
    def test_falls_back_to_tmdb_tv_external_ids(self, mock_extract, mock_fetch):
        recommender = _make_recommender(recommender_cls=ConcreteTVRecommender)
        mock_extract.return_value = {'imdb_id': None, 'tmdb_id': None}
        recommender._get_plex_item_tmdb_id = Mock(return_value=555)
        mock_fetch.return_value = {'imdb_id': 'tt444'}
        plex_item = Mock(guid=None)

        result = recommender._get_plex_item_imdb_id(plex_item)

        assert result == 'tt444'
        assert 'tv/555/external_ids' in mock_fetch.call_args[0][0]

    @patch('recommenders.base.extract_ids_from_guids')
    def test_returns_none_when_no_tmdb_id_available(self, mock_extract):
        recommender = _make_recommender()
        mock_extract.return_value = {'imdb_id': None, 'tmdb_id': None}
        recommender._get_plex_item_tmdb_id = Mock(return_value=None)
        plex_item = Mock(guid=None)

        result = recommender._get_plex_item_imdb_id(plex_item)

        assert result is None


class TestGetTmdbIdViaImdb:
    """Tests for BaseRecommender._get_tmdb_id_via_imdb."""

    @patch('recommenders.base.fetch_tmdb_with_retry')
    def test_returns_tmdb_id_for_movie(self, mock_fetch):
        recommender = _make_recommender()
        recommender._get_plex_item_imdb_id = Mock(return_value='tt123')
        recommender.tmdb_api_key = 'key'
        mock_fetch.return_value = {'movie_results': [{'id': 42}]}

        result = recommender._get_tmdb_id_via_imdb(Mock())

        assert result == 42

    def test_returns_none_without_imdb_id(self):
        recommender = _make_recommender()
        recommender._get_plex_item_imdb_id = Mock(return_value=None)

        result = recommender._get_tmdb_id_via_imdb(Mock())

        assert result is None

    @patch('recommenders.base.fetch_tmdb_with_retry')
    def test_returns_none_when_no_results(self, mock_fetch):
        recommender = _make_recommender()
        recommender._get_plex_item_imdb_id = Mock(return_value='tt123')
        recommender.tmdb_api_key = 'key'
        mock_fetch.return_value = {'movie_results': []}

        result = recommender._get_tmdb_id_via_imdb(Mock())

        assert result is None


class TestGetTmdbKeywordsForId:
    """Tests for BaseRecommender._get_tmdb_keywords_for_id."""

    def test_returns_empty_set_without_tmdb_id(self):
        recommender = _make_recommender()

        assert recommender._get_tmdb_keywords_for_id(None) == set()

    def test_returns_empty_set_when_keywords_disabled(self):
        recommender = _make_recommender()
        recommender.use_tmdb_keywords = False

        assert recommender._get_tmdb_keywords_for_id(123) == set()

    @patch('recommenders.base.get_tmdb_keywords')
    def test_fetches_and_saves_keywords(self, mock_keywords):
        recommender = _make_recommender()
        recommender._save_watched_cache = Mock()
        mock_keywords.return_value = ['a', 'b']

        result = recommender._get_tmdb_keywords_for_id(123)

        assert result == {'a', 'b'}
        recommender._save_watched_cache.assert_called_once()


class TestGetRecommendationsBranches:
    """Additional branch coverage for BaseRecommender.get_recommendations."""

    def _recommender_with_cache(self, items):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {'movies': items}
        media_cache._save_cache = Mock()
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender.watched_ids = set()
        recommender.profile_hash = 'hash1'
        recommender.exclude_genres = []
        recommender.user_preferences = {}
        recommender.randomize_recommendations = False
        return recommender, media_cache

    @patch('recommenders.base.get_excluded_genres_for_user', return_value=[])
    def test_quality_filter_excludes_low_rated(self, mock_excl):
        items = {
            '1': {'title': 'Good', 'rating': 8.0, 'vote_count': 500, 'genres': []},
            '2': {'title': 'Bad', 'rating': 2.0, 'vote_count': 5, 'genres': []},
        }
        recommender, media_cache = self._recommender_with_cache(items)
        recommender.config['quality_filters'] = {'min_rating': 5.0, 'min_vote_count': 100}

        result = recommender.get_recommendations()

        titles = [i['title'] for i in result['plex_recommendations']]
        assert 'Bad' not in titles

    @patch('recommenders.base.get_excluded_genres_for_user', return_value=[])
    def test_uses_cached_score_when_profile_hash_matches(self, mock_excl):
        items = {'1': {'title': 'Cached', 'rating': 8, 'vote_count': 500, 'genres': [],
                        'profile_hash': 'hash1', 'cached_score': 0.77, 'score_breakdown': {}}}
        recommender, media_cache = self._recommender_with_cache(items)
        recommender._calculate_similarity_from_cache = Mock(side_effect=AssertionError("should not recompute"))

        result = recommender.get_recommendations()

        assert result['plex_recommendations'][0]['similarity_score'] == 0.77
        media_cache._save_cache.assert_not_called()

    @patch('recommenders.base.get_excluded_genres_for_user', return_value=[])
    def test_scoring_error_skips_item(self, mock_excl):
        items = {'1': {'title': 'Bad Score', 'rating': 8, 'vote_count': 500, 'genres': []}}
        recommender, media_cache = self._recommender_with_cache(items)
        recommender._calculate_similarity_from_cache = Mock(side_effect=KeyError('boom'))

        result = recommender.get_recommendations()

        assert result['plex_recommendations'] == []

    @patch('recommenders.base.get_excluded_genres_for_user', return_value=[])
    def test_no_unwatched_items_returns_empty(self, mock_excl):
        recommender, media_cache = self._recommender_with_cache({})

        result = recommender.get_recommendations()

        assert result == {'plex_recommendations': []}

    @patch('recommenders.base.select_tiered_recommendations')
    @patch('recommenders.base.get_excluded_genres_for_user', return_value=[])
    def test_randomize_recommendations_uses_tiered_selection(self, mock_excl, mock_tiered):
        items = {'1': {'title': 'A', 'rating': 8, 'vote_count': 500, 'genres': []}}
        recommender, media_cache = self._recommender_with_cache(items)
        recommender.randomize_recommendations = True
        mock_tiered.return_value = [items['1']]

        recommender.get_recommendations()

        mock_tiered.assert_called_once()

    @patch('recommenders.base.get_excluded_genres_for_user', return_value=[])
    def test_debug_logging_prints_breakdown(self, mock_excl):
        items = {'1': {'title': 'A', 'rating': 8, 'vote_count': 500, 'genres': []}}
        recommender, media_cache = self._recommender_with_cache(items)
        recommender._print_similarity_breakdown = Mock()
        with patch('recommenders.base.logger') as mock_logger:
            mock_logger.isEnabledFor.return_value = True
            recommender.get_recommendations()

        recommender._print_similarity_breakdown.assert_called()

    @patch('recommenders.base.get_excluded_genres_for_user', return_value=[])
    def test_refreshes_watched_data_when_ids_missing(self, mock_excl):
        recommender, media_cache = self._recommender_with_cache({})
        recommender.cached_watched_count = 5
        recommender.watched_ids = set()
        recommender._get_watched_data = Mock(return_value={'genres': {}})
        recommender._save_watched_cache = Mock()

        recommender.get_recommendations()

        recommender._get_watched_data.assert_called_once()
        recommender._save_watched_cache.assert_called_once()

    @patch('recommenders.base.get_excluded_genres_for_user')
    def test_excluded_genres_filtered_and_counted(self, mock_excl):
        mock_excl.return_value = ['horror']
        items = {
            '1': {'title': 'Scary', 'rating': 8, 'vote_count': 500, 'genres': ['Horror']},
            '2': {'title': 'Fine', 'rating': 8, 'vote_count': 500, 'genres': ['Comedy']},
        }
        recommender, media_cache = self._recommender_with_cache(items)

        result = recommender.get_recommendations()

        titles = [i['title'] for i in result['plex_recommendations']]
        assert titles == ['Fine']


class TestLoadWatchedCache:
    """Tests for BaseRecommender._load_watched_cache."""

    def _recommender_with_cache_path(self, tmp_path):
        recommender = _make_recommender()
        recommender.watched_cache_path = str(tmp_path / 'watched_cache.json')
        return recommender

    @patch('recommenders.base.check_cache_version', return_value=False)
    def test_invalid_cache_version_returns_empty_without_reading_file(self, mock_valid, tmp_path):
        recommender = self._recommender_with_cache_path(tmp_path)
        with open(recommender.watched_cache_path, 'w') as f:
            f.write('{"watched_count": 3}')

        result = recommender._load_watched_cache()

        assert result == {}
        assert recommender.cached_watched_count == 0

    @patch('recommenders.base.check_cache_version', return_value=True)
    def test_loads_valid_cache_fields(self, mock_valid, tmp_path):
        recommender = self._recommender_with_cache_path(tmp_path)
        cache_data = {
            'watched_count': 2,
            'watched_data_counters': {'genres': {'Action': 2}},
            'plex_tmdb_cache': {1: 100},
            'tmdb_keywords_cache': {100: ['x']},
            'label_dates': {'a': '2024-01-01'},
            'watched_movie_ids': [1, 2],
        }
        import json as _json
        with open(recommender.watched_cache_path, 'w') as f:
            _json.dump(cache_data, f)

        result = recommender._load_watched_cache()

        assert recommender.cached_watched_count == 2
        assert recommender.watched_ids == {1, 2}
        assert recommender.plex_tmdb_cache == {'1': 100}
        assert result['watched_count'] == 2

    @patch('recommenders.base.log_warning')
    @patch('recommenders.base.check_cache_version', return_value=True)
    def test_invalid_watched_ids_format_warns_and_clears(self, mock_valid, mock_warn, tmp_path):
        recommender = self._recommender_with_cache_path(tmp_path)
        cache_data = {'watched_count': 0, 'watched_movie_ids': 'not-a-list'}
        import json as _json
        with open(recommender.watched_cache_path, 'w') as f:
            _json.dump(cache_data, f)

        recommender._load_watched_cache()

        mock_warn.assert_called()
        assert recommender.watched_ids == set()

    @patch('recommenders.base.check_cache_version', return_value=True)
    def test_missing_ids_with_positive_count_triggers_refresh(self, mock_valid, tmp_path):
        recommender = self._recommender_with_cache_path(tmp_path)
        cache_data = {'watched_count': 5, 'watched_movie_ids': []}
        import json as _json
        with open(recommender.watched_cache_path, 'w') as f:
            _json.dump(cache_data, f)
        recommender._refresh_watched_data = Mock()

        recommender._load_watched_cache()

        recommender._refresh_watched_data.assert_called_once()

    @patch('recommenders.base.log_warning')
    @patch('recommenders.base.check_cache_version', return_value=True)
    def test_corrupt_json_triggers_refresh(self, mock_valid, mock_warn, tmp_path):
        recommender = self._recommender_with_cache_path(tmp_path)
        with open(recommender.watched_cache_path, 'w') as f:
            f.write('{not valid json')
        recommender._refresh_watched_data = Mock()

        recommender._load_watched_cache()

        mock_warn.assert_called()
        recommender._refresh_watched_data.assert_called_once()
