"""
Tests for recommenders/base.py - Base cache and recommender classes.
"""

import os
import pytest
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
        mock_item.reload.side_effect = Exception("Network error")

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
        """Test that N/A is returned on any exception."""
        mock_load.return_value = {'movies': {}, 'library_count': 0}

        cache = ConcreteCache('/tmp/cache')
        mock_item = Mock()
        mock_item.media = Mock()
        mock_item.media.__iter__ = Mock(side_effect=Exception("Error"))

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
    library_config_key = 'movie_library_title'
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
        mock_fetch.side_effect = [Exception("Network error"), {'belongs_to_collection': {'id': 1, 'name': 'Collection'}}]

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
