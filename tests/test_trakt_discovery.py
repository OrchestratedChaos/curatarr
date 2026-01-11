"""Tests for utils/trakt_discovery.py"""

import pytest
import json
import time
from unittest.mock import Mock, patch, MagicMock


class TestTraktClientDiscoveryMethods:
    """Tests for TraktClient discovery API methods."""

    def test_get_trending_movies(self):
        """Test get_trending returns movie list."""
        from utils.trakt import TraktClient

        client = TraktClient(
            client_id='test_id',
            client_secret='test_secret'
        )

        mock_response = [
            {
                'watchers': 100,
                'movie': {
                    'title': 'Test Movie',
                    'year': 2024,
                    'ids': {'tmdb': 12345, 'imdb': 'tt1234567', 'trakt': 111}
                }
            }
        ]

        with patch.object(client, '_make_request', return_value=mock_response):
            result = client.get_trending('movies', limit=10)

        assert len(result) == 1
        assert result[0]['watchers'] == 100
        assert result[0]['movie']['title'] == 'Test Movie'

    def test_get_trending_shows(self):
        """Test get_trending returns show list."""
        from utils.trakt import TraktClient

        client = TraktClient(
            client_id='test_id',
            client_secret='test_secret'
        )

        mock_response = [
            {
                'watchers': 50,
                'show': {
                    'title': 'Test Show',
                    'year': 2024,
                    'ids': {'tmdb': 54321, 'imdb': 'tt7654321', 'trakt': 222}
                }
            }
        ]

        with patch.object(client, '_make_request', return_value=mock_response):
            result = client.get_trending('shows', limit=10)

        assert len(result) == 1
        assert result[0]['watchers'] == 50
        assert result[0]['show']['title'] == 'Test Show'

    def test_get_popular(self):
        """Test get_popular returns items."""
        from utils.trakt import TraktClient

        client = TraktClient(
            client_id='test_id',
            client_secret='test_secret'
        )

        mock_response = [
            {
                'title': 'Popular Movie',
                'year': 2023,
                'ids': {'tmdb': 99999, 'imdb': 'tt9999999'}
            }
        ]

        with patch.object(client, '_make_request', return_value=mock_response):
            result = client.get_popular('movies', limit=10)

        assert len(result) == 1
        assert result[0]['title'] == 'Popular Movie'

    def test_get_anticipated(self):
        """Test get_anticipated returns upcoming items."""
        from utils.trakt import TraktClient

        client = TraktClient(
            client_id='test_id',
            client_secret='test_secret'
        )

        mock_response = [
            {
                'list_count': 5000,
                'movie': {
                    'title': 'Upcoming Movie',
                    'year': 2025,
                    'ids': {'tmdb': 88888}
                }
            }
        ]

        with patch.object(client, '_make_request', return_value=mock_response):
            result = client.get_anticipated('movies', limit=10)

        assert len(result) == 1
        assert result[0]['list_count'] == 5000

    def test_get_recommendations_requires_auth(self):
        """Test get_recommendations returns empty when not authenticated."""
        from utils.trakt import TraktClient

        client = TraktClient(
            client_id='test_id',
            client_secret='test_secret',
            access_token=None  # Not authenticated
        )

        result = client.get_recommendations('movies', limit=10)
        assert result == []

    def test_get_recommendations_with_auth(self):
        """Test get_recommendations returns items when authenticated."""
        from utils.trakt import TraktClient

        client = TraktClient(
            client_id='test_id',
            client_secret='test_secret',
            access_token='valid_token'
        )

        mock_response = [
            {
                'title': 'Recommended Movie',
                'year': 2024,
                'ids': {'tmdb': 77777}
            }
        ]

        with patch.object(client, '_make_request', return_value=mock_response):
            result = client.get_recommendations('movies', limit=10)

        assert len(result) == 1
        assert result[0]['title'] == 'Recommended Movie'

    def test_get_related(self):
        """Test get_related returns similar items."""
        from utils.trakt import TraktClient

        client = TraktClient(
            client_id='test_id',
            client_secret='test_secret'
        )

        mock_response = [
            {
                'title': 'Related Movie',
                'year': 2023,
                'ids': {'tmdb': 66666}
            }
        ]

        with patch.object(client, '_make_request', return_value=mock_response):
            result = client.get_related('movies', trakt_id=12345, limit=5)

        assert len(result) == 1
        assert result[0]['title'] == 'Related Movie'


class TestDiscoveryCache:
    """Tests for discovery cache functions."""

    def test_load_fresh_cache(self, tmp_path):
        """Test loading a fresh cache returns data."""
        from utils.trakt_discovery import _load_discovery_cache, _save_discovery_cache

        items = [{'tmdb_id': 123, 'title': 'Test'}]
        _save_discovery_cache(str(tmp_path), 'trending', 'movies', items)

        result = _load_discovery_cache(str(tmp_path), 'trending', 'movies')
        assert result is not None
        assert result['items'] == items

    def test_load_stale_cache_returns_none(self, tmp_path):
        """Test loading stale cache returns None."""
        from utils.trakt_discovery import _load_discovery_cache, DISCOVERY_CACHE_TTL

        cache_path = tmp_path / 'trakt_trending_movies.json'
        cache_data = {
            'cached_at': time.time() - DISCOVERY_CACHE_TTL - 100,  # Expired
            'items': [{'tmdb_id': 123}]
        }
        cache_path.write_text(json.dumps(cache_data))

        result = _load_discovery_cache(str(tmp_path), 'trending', 'movies')
        assert result is None

    def test_load_missing_cache_returns_none(self, tmp_path):
        """Test loading missing cache returns None."""
        from utils.trakt_discovery import _load_discovery_cache

        result = _load_discovery_cache(str(tmp_path), 'trending', 'movies')
        assert result is None


class TestExtractItemIds:
    """Tests for _extract_item_ids helper."""

    def test_extract_from_trending_format(self):
        """Test extracting IDs from trending response format."""
        from utils.trakt_discovery import _extract_item_ids

        item = {
            'watchers': 100,
            'movie': {
                'title': 'Test Movie',
                'year': 2024,
                'ids': {'tmdb': 12345, 'imdb': 'tt1234567', 'trakt': 111}
            }
        }

        result = _extract_item_ids(item, 'movies')
        assert result['tmdb_id'] == 12345
        assert result['imdb_id'] == 'tt1234567'
        assert result['title'] == 'Test Movie'
        assert result['watchers'] == 100

    def test_extract_from_popular_format(self):
        """Test extracting IDs from popular response format (direct)."""
        from utils.trakt_discovery import _extract_item_ids

        item = {
            'title': 'Popular Movie',
            'year': 2023,
            'ids': {'tmdb': 99999, 'imdb': 'tt9999999'}
        }

        result = _extract_item_ids(item, 'movies')
        assert result['tmdb_id'] == 99999
        assert result['title'] == 'Popular Movie'

    def test_extract_from_anticipated_format(self):
        """Test extracting IDs from anticipated response format."""
        from utils.trakt_discovery import _extract_item_ids

        item = {
            'list_count': 5000,
            'show': {
                'title': 'Upcoming Show',
                'year': 2025,
                'ids': {'tmdb': 88888}
            }
        }

        result = _extract_item_ids(item, 'shows')
        assert result['tmdb_id'] == 88888
        assert result['title'] == 'Upcoming Show'
        assert result['list_count'] == 5000


class TestDiscoverFromTrakt:
    """Tests for discover_from_trakt main entry point."""

    def test_returns_empty_when_trakt_disabled(self):
        """Test returns empty results when Trakt disabled."""
        from utils.trakt_discovery import discover_from_trakt

        config = {'trakt': {'enabled': False}}
        result = discover_from_trakt(config, 'movie', '/tmp/cache')

        assert result == {'trending': [], 'popular': [], 'anticipated': [], 'recommendations': []}

    def test_returns_empty_when_discovery_disabled(self):
        """Test returns empty results when discovery disabled."""
        from utils.trakt_discovery import discover_from_trakt

        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'discovery': {'enabled': False}
            }
        }
        result = discover_from_trakt(config, 'movie', '/tmp/cache')

        assert result == {'trending': [], 'popular': [], 'anticipated': [], 'recommendations': []}

    @patch('utils.trakt_discovery.get_authenticated_trakt_client')
    @patch('utils.trakt_discovery.get_trending_items')
    def test_fetches_trending_when_enabled(self, mock_trending, mock_client, tmp_path):
        """Test fetches trending items when use_trending is true."""
        from utils.trakt_discovery import discover_from_trakt

        mock_client.return_value = Mock(is_authenticated=True)
        mock_trending.return_value = [{'tmdb_id': 123, 'title': 'Trending'}]

        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'discovery': {
                    'enabled': True,
                    'use_trending': True,
                    'use_popular': False,
                    'use_anticipated': False,
                    'use_recommendations': False
                }
            }
        }

        result = discover_from_trakt(config, 'movie', str(tmp_path))

        assert len(result['trending']) == 1
        assert result['trending'][0]['tmdb_id'] == 123
        mock_trending.assert_called_once()

    @patch('utils.trakt_discovery.get_authenticated_trakt_client')
    @patch('utils.trakt_discovery.get_popular_items')
    def test_fetches_popular_when_enabled(self, mock_popular, mock_client, tmp_path):
        """Test fetches popular items when use_popular is true."""
        from utils.trakt_discovery import discover_from_trakt

        mock_client.return_value = Mock(is_authenticated=True)
        mock_popular.return_value = [{'tmdb_id': 456, 'title': 'Popular'}]

        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'discovery': {
                    'enabled': True,
                    'use_trending': False,
                    'use_popular': True,
                    'use_anticipated': False,
                    'use_recommendations': False
                }
            }
        }

        result = discover_from_trakt(config, 'movie', str(tmp_path))

        assert len(result['popular']) == 1
        assert result['popular'][0]['tmdb_id'] == 456

    def test_excludes_library_items(self, tmp_path):
        """Test filters out items already in library."""
        from utils.trakt_discovery import discover_from_trakt

        with patch('utils.trakt_discovery.get_authenticated_trakt_client') as mock_client, \
             patch('utils.trakt_discovery.get_trending_items') as mock_trending:

            mock_client.return_value = Mock(is_authenticated=True)
            mock_trending.return_value = [
                {'tmdb_id': 123, 'title': 'In Library'},
                {'tmdb_id': 456, 'title': 'Not In Library'}
            ]

            config = {
                'trakt': {
                    'enabled': True,
                    'client_id': 'id',
                    'client_secret': 'secret',
                    'discovery': {
                        'enabled': True,
                        'use_trending': True,
                        'use_popular': False,
                        'use_anticipated': False,
                        'use_recommendations': False
                    }
                }
            }

            result = discover_from_trakt(
                config,
                'movie',
                str(tmp_path),
                exclude_tmdb_ids={123}  # Exclude 123
            )

            assert len(result['trending']) == 1
            assert result['trending'][0]['tmdb_id'] == 456


class TestGetTraktDiscoveryCandidates:
    """Tests for get_trakt_discovery_candidates."""

    @patch('utils.trakt_discovery.discover_from_trakt')
    def test_converts_to_candidate_format(self, mock_discover, tmp_path):
        """Test converts discovery items to candidate format."""
        from utils.trakt_discovery import get_trakt_discovery_candidates

        mock_discover.return_value = {
            'trending': [
                {'tmdb_id': 123, 'title': 'Trending Movie', 'year': 2024, 'watchers': 100}
            ],
            'popular': [],
            'anticipated': [],
            'recommendations': []
        }

        config = {'trakt': {'enabled': True}}
        result = get_trakt_discovery_candidates(
            config,
            'movie',
            str(tmp_path),
            library_tmdb_ids=set()
        )

        assert 123 in result
        assert result[123]['title'] == 'Trending Movie'
        assert result[123]['source'] == 'trakt_trending'
        assert result[123]['watchers'] == 100

    @patch('utils.trakt_discovery.discover_from_trakt')
    def test_deduplicates_across_sources(self, mock_discover, tmp_path):
        """Test deduplicates items that appear in multiple sources."""
        from utils.trakt_discovery import get_trakt_discovery_candidates

        mock_discover.return_value = {
            'trending': [
                {'tmdb_id': 123, 'title': 'Movie', 'year': 2024}
            ],
            'popular': [
                {'tmdb_id': 123, 'title': 'Movie', 'year': 2024}  # Same movie
            ],
            'anticipated': [],
            'recommendations': []
        }

        config = {'trakt': {'enabled': True}}
        result = get_trakt_discovery_candidates(
            config,
            'movie',
            str(tmp_path),
            library_tmdb_ids=set()
        )

        # Should only have one entry, from higher-tier source (popular > trending)
        assert len(result) == 1
        assert result[123]['source'] == 'trakt_popular'
        assert result[123]['source_tier'] == 2  # Popular tier


class TestDiscoveryCacheVersioning:
    """Tests for Trakt discovery cache versioning."""

    def test_load_cache_returns_none_for_old_version(self, tmp_path):
        """Test returns None when cache has old version."""
        from utils.trakt_discovery import _load_discovery_cache, TRAKT_DISCOVERY_CACHE_VERSION

        cache_path = tmp_path / 'trakt_trending_movie.json'
        cache_data = {
            'version': 0,  # Old version
            'cached_at': time.time(),
            'items': [{'tmdb_id': 123, 'title': 'Test'}]
        }
        cache_path.write_text(json.dumps(cache_data))

        result = _load_discovery_cache(str(tmp_path), 'trending', 'movie')
        assert result is None

    def test_load_cache_returns_data_for_current_version(self, tmp_path):
        """Test returns cache data when version matches."""
        from utils.trakt_discovery import _load_discovery_cache, TRAKT_DISCOVERY_CACHE_VERSION

        cache_path = tmp_path / 'trakt_trending_movie.json'
        cache_data = {
            'version': TRAKT_DISCOVERY_CACHE_VERSION,
            'cached_at': time.time(),
            'items': [{'tmdb_id': 123, 'title': 'Test'}]
        }
        cache_path.write_text(json.dumps(cache_data))

        result = _load_discovery_cache(str(tmp_path), 'trending', 'movie')
        assert result is not None
        assert len(result['items']) == 1

    def test_save_cache_includes_version(self, tmp_path):
        """Test save adds version to cache."""
        from utils.trakt_discovery import _save_discovery_cache, TRAKT_DISCOVERY_CACHE_VERSION

        items = [{'tmdb_id': 123, 'title': 'Test Movie'}]
        _save_discovery_cache(str(tmp_path), 'trending', 'movie', items)

        cache_path = tmp_path / 'trakt_trending_movie.json'
        with open(cache_path) as f:
            saved = json.load(f)

        assert 'version' in saved
        assert saved['version'] == TRAKT_DISCOVERY_CACHE_VERSION
        assert 'cached_at' in saved
        assert saved['items'] == items

    def test_load_cache_returns_none_for_missing_version(self, tmp_path):
        """Test returns None when cache has no version (pre-versioning format)."""
        from utils.trakt_discovery import _load_discovery_cache

        cache_path = tmp_path / 'trakt_popular_movie.json'
        cache_data = {
            'cached_at': time.time(),
            'items': [{'tmdb_id': 456, 'title': 'Old Cache'}]
        }
        cache_path.write_text(json.dumps(cache_data))

        result = _load_discovery_cache(str(tmp_path), 'popular', 'movie')
        assert result is None


class TestGetTrendingItems:
    """Tests for get_trending_items function."""

    def test_returns_cached_items(self, tmp_path):
        """Test returns cached items when available."""
        from utils.trakt_discovery import get_trending_items, _save_discovery_cache

        mock_client = Mock()
        cached_items = [{'tmdb_id': 123, 'title': 'Cached Movie'}]
        _save_discovery_cache(str(tmp_path), 'trending', 'movies', cached_items)

        result = get_trending_items(mock_client, 'movies', str(tmp_path))

        assert result == cached_items
        mock_client.get_trending.assert_not_called()

    def test_fetches_from_api_when_no_cache(self, tmp_path):
        """Test fetches from API when no cache exists."""
        from utils.trakt_discovery import get_trending_items

        mock_client = Mock()
        mock_client.get_trending.return_value = [
            {
                'watchers': 100,
                'movie': {
                    'title': 'API Movie',
                    'year': 2024,
                    'ids': {'tmdb': 456, 'imdb': 'tt456'}
                }
            }
        ]

        result = get_trending_items(mock_client, 'movies', str(tmp_path))

        assert len(result) == 1
        assert result[0]['tmdb_id'] == 456
        mock_client.get_trending.assert_called_once()

    def test_filters_items_without_tmdb_id(self, tmp_path):
        """Test filters out items without TMDB ID."""
        from utils.trakt_discovery import get_trending_items

        mock_client = Mock()
        mock_client.get_trending.return_value = [
            {'movie': {'title': 'No TMDB', 'ids': {'imdb': 'tt123'}}},
            {'movie': {'title': 'Has TMDB', 'ids': {'tmdb': 789}}}
        ]

        result = get_trending_items(mock_client, 'movies', str(tmp_path))

        assert len(result) == 1
        assert result[0]['tmdb_id'] == 789

    def test_returns_empty_on_api_error(self, tmp_path):
        """Test returns empty list on API error."""
        from utils.trakt_discovery import get_trending_items
        from utils.trakt import TraktAPIError

        mock_client = Mock()
        mock_client.get_trending.side_effect = TraktAPIError("API Error")

        result = get_trending_items(mock_client, 'movies', str(tmp_path))

        assert result == []


class TestGetPopularItems:
    """Tests for get_popular_items function."""

    def test_returns_cached_items(self, tmp_path):
        """Test returns cached items when available."""
        from utils.trakt_discovery import get_popular_items, _save_discovery_cache

        mock_client = Mock()
        cached_items = [{'tmdb_id': 123, 'title': 'Popular Movie'}]
        _save_discovery_cache(str(tmp_path), 'popular', 'movies', cached_items)

        result = get_popular_items(mock_client, 'movies', str(tmp_path))

        assert result == cached_items
        mock_client.get_popular.assert_not_called()

    def test_fetches_from_api_when_no_cache(self, tmp_path):
        """Test fetches from API when no cache exists."""
        from utils.trakt_discovery import get_popular_items

        mock_client = Mock()
        mock_client.get_popular.return_value = [
            {
                'title': 'Popular Movie',
                'year': 2024,
                'ids': {'tmdb': 456, 'imdb': 'tt456'}
            }
        ]

        result = get_popular_items(mock_client, 'movies', str(tmp_path))

        assert len(result) == 1
        assert result[0]['tmdb_id'] == 456

    def test_returns_empty_on_api_error(self, tmp_path):
        """Test returns empty list on API error."""
        from utils.trakt_discovery import get_popular_items
        from utils.trakt import TraktAPIError

        mock_client = Mock()
        mock_client.get_popular.side_effect = TraktAPIError("API Error")

        result = get_popular_items(mock_client, 'movies', str(tmp_path))

        assert result == []


class TestGetAnticipatedItems:
    """Tests for get_anticipated_items function."""

    def test_returns_cached_items(self, tmp_path):
        """Test returns cached items when available."""
        from utils.trakt_discovery import get_anticipated_items, _save_discovery_cache

        mock_client = Mock()
        cached_items = [{'tmdb_id': 123, 'title': 'Anticipated Movie'}]
        _save_discovery_cache(str(tmp_path), 'anticipated', 'movies', cached_items)

        result = get_anticipated_items(mock_client, 'movies', str(tmp_path))

        assert result == cached_items
        mock_client.get_anticipated.assert_not_called()

    def test_fetches_from_api_when_no_cache(self, tmp_path):
        """Test fetches from API when no cache exists."""
        from utils.trakt_discovery import get_anticipated_items

        mock_client = Mock()
        mock_client.get_anticipated.return_value = [
            {
                'list_count': 5000,
                'movie': {
                    'title': 'Upcoming Movie',
                    'year': 2025,
                    'ids': {'tmdb': 789}
                }
            }
        ]

        result = get_anticipated_items(mock_client, 'movies', str(tmp_path))

        assert len(result) == 1
        assert result[0]['tmdb_id'] == 789
        assert result[0]['list_count'] == 5000

    def test_returns_empty_on_api_error(self, tmp_path):
        """Test returns empty list on API error."""
        from utils.trakt_discovery import get_anticipated_items
        from utils.trakt import TraktAPIError

        mock_client = Mock()
        mock_client.get_anticipated.side_effect = TraktAPIError("API Error")

        result = get_anticipated_items(mock_client, 'movies', str(tmp_path))

        assert result == []


class TestGetRecommendedItems:
    """Tests for get_recommended_items function."""

    def test_returns_empty_when_not_authenticated(self, tmp_path):
        """Test returns empty list when client not authenticated."""
        from utils.trakt_discovery import get_recommended_items

        mock_client = Mock()
        mock_client.is_authenticated = False

        result = get_recommended_items(mock_client, 'movies', str(tmp_path))

        assert result == []

    def test_returns_cached_items_when_authenticated(self, tmp_path):
        """Test returns cached items when authenticated."""
        from utils.trakt_discovery import get_recommended_items, _save_discovery_cache

        mock_client = Mock()
        mock_client.is_authenticated = True
        cached_items = [{'tmdb_id': 123, 'title': 'Recommended Movie'}]
        _save_discovery_cache(str(tmp_path), 'recommendations', 'movies', cached_items)

        result = get_recommended_items(mock_client, 'movies', str(tmp_path))

        assert result == cached_items

    def test_fetches_from_api_when_authenticated(self, tmp_path):
        """Test fetches from API when authenticated and no cache."""
        from utils.trakt_discovery import get_recommended_items

        mock_client = Mock()
        mock_client.is_authenticated = True
        mock_client.get_recommendations.return_value = [
            {
                'title': 'Recommended Movie',
                'year': 2024,
                'ids': {'tmdb': 999}
            }
        ]

        result = get_recommended_items(mock_client, 'movies', str(tmp_path))

        assert len(result) == 1
        assert result[0]['tmdb_id'] == 999

    def test_returns_empty_on_api_error(self, tmp_path):
        """Test returns empty list on API error."""
        from utils.trakt_discovery import get_recommended_items
        from utils.trakt import TraktAPIError

        mock_client = Mock()
        mock_client.is_authenticated = True
        mock_client.get_recommendations.side_effect = TraktAPIError("API Error")

        result = get_recommended_items(mock_client, 'movies', str(tmp_path))

        assert result == []


class TestDiscoverFromTraktFallback:
    """Tests for discover_from_trakt fallback client."""

    @patch('utils.trakt_discovery.get_authenticated_trakt_client')
    @patch('utils.trakt.create_trakt_client')
    def test_uses_unauthenticated_client_as_fallback(self, mock_create, mock_auth, tmp_path):
        """Test uses unauthenticated client when auth fails."""
        from utils.trakt_discovery import discover_from_trakt

        mock_auth.return_value = None
        mock_unauth_client = Mock()
        mock_unauth_client.is_authenticated = False
        mock_unauth_client.get_trending.return_value = []
        mock_create.return_value = mock_unauth_client

        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'discovery': {
                    'enabled': True,
                    'use_trending': True,
                    'use_popular': False,
                    'use_anticipated': False,
                    'use_recommendations': False
                }
            }
        }

        discover_from_trakt(config, 'movie', str(tmp_path))

        mock_create.assert_called_once()

    @patch('utils.trakt_discovery.get_authenticated_trakt_client')
    @patch('utils.trakt.create_trakt_client')
    def test_returns_empty_when_no_client_available(self, mock_create, mock_auth, tmp_path):
        """Test returns empty results when no client available."""
        from utils.trakt_discovery import discover_from_trakt

        mock_auth.return_value = None
        mock_create.return_value = None

        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'discovery': {'enabled': True}
            }
        }

        result = discover_from_trakt(config, 'movie', str(tmp_path))

        assert result == {'trending': [], 'popular': [], 'anticipated': [], 'recommendations': []}


class TestDiscoverFromTraktFiltering:
    """Tests for discover_from_trakt filtering logic."""

    @patch('utils.trakt_discovery.get_authenticated_trakt_client')
    @patch('utils.trakt_discovery.get_trending_items')
    def test_filters_by_imdb_id(self, mock_trending, mock_client, tmp_path):
        """Test filters out items by IMDB ID."""
        from utils.trakt_discovery import discover_from_trakt

        mock_client.return_value = Mock(is_authenticated=True)
        mock_trending.return_value = [
            {'tmdb_id': 123, 'imdb_id': 'tt123', 'title': 'Exclude Me'},
            {'tmdb_id': 456, 'imdb_id': 'tt456', 'title': 'Keep Me'}
        ]

        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'discovery': {
                    'enabled': True,
                    'use_trending': True,
                    'use_popular': False,
                    'use_anticipated': False,
                    'use_recommendations': False
                }
            }
        }

        result = discover_from_trakt(
            config, 'movie', str(tmp_path),
            exclude_imdb_ids={'tt123'}
        )

        assert len(result['trending']) == 1
        assert result['trending'][0]['tmdb_id'] == 456

    @patch('utils.trakt_discovery.get_authenticated_trakt_client')
    @patch('utils.trakt_discovery.get_anticipated_items')
    def test_fetches_anticipated_when_enabled(self, mock_anticipated, mock_client, tmp_path):
        """Test fetches anticipated items when enabled."""
        from utils.trakt_discovery import discover_from_trakt

        mock_client.return_value = Mock(is_authenticated=True)
        mock_anticipated.return_value = [
            {'tmdb_id': 789, 'title': 'Anticipated'}
        ]

        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'discovery': {
                    'enabled': True,
                    'use_trending': False,
                    'use_popular': False,
                    'use_anticipated': True,
                    'use_recommendations': False
                }
            }
        }

        result = discover_from_trakt(config, 'movie', str(tmp_path))

        assert len(result['anticipated']) == 1
        mock_anticipated.assert_called_once()

    @patch('utils.trakt_discovery.get_authenticated_trakt_client')
    @patch('utils.trakt_discovery.get_recommended_items')
    def test_fetches_recommendations_when_authenticated(self, mock_recs, mock_client, tmp_path):
        """Test fetches recommendations when authenticated and enabled."""
        from utils.trakt_discovery import discover_from_trakt

        mock_client_instance = Mock(is_authenticated=True)
        mock_client.return_value = mock_client_instance
        mock_recs.return_value = [{'tmdb_id': 999, 'title': 'Recommended'}]

        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'discovery': {
                    'enabled': True,
                    'use_trending': False,
                    'use_popular': False,
                    'use_anticipated': False,
                    'use_recommendations': True
                }
            }
        }

        result = discover_from_trakt(config, 'movie', str(tmp_path))

        assert len(result['recommendations']) == 1
        mock_recs.assert_called_once()

    @patch('utils.trakt_discovery.get_authenticated_trakt_client')
    def test_skips_recommendations_when_not_authenticated(self, mock_client, tmp_path):
        """Test skips recommendations when not authenticated."""
        from utils.trakt_discovery import discover_from_trakt

        mock_client.return_value = Mock(is_authenticated=False)

        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'discovery': {
                    'enabled': True,
                    'use_trending': False,
                    'use_popular': False,
                    'use_anticipated': False,
                    'use_recommendations': True
                }
            }
        }

        result = discover_from_trakt(config, 'movie', str(tmp_path))

        # Recommendations should be empty since not authenticated
        assert result['recommendations'] == []


class TestSaveDiscoveryCacheExceptionHandling:
    """Tests for _save_discovery_cache exception handling."""

    @patch('utils.trakt_discovery.json.dump')
    def test_handles_save_exception(self, mock_dump, tmp_path):
        """Test handles exception during cache save."""
        from utils.trakt_discovery import _save_discovery_cache

        mock_dump.side_effect = IOError("Write failed")

        # Should not raise, just log warning
        _save_discovery_cache(str(tmp_path), 'trending', 'movies', [])


class TestLoadDiscoveryCacheExceptionHandling:
    """Tests for _load_discovery_cache exception handling."""

    def test_handles_invalid_json(self, tmp_path):
        """Test handles invalid JSON in cache file."""
        from utils.trakt_discovery import _load_discovery_cache

        cache_path = tmp_path / 'trakt_trending_movies.json'
        cache_path.write_text('not valid json{{{')

        result = _load_discovery_cache(str(tmp_path), 'trending', 'movies')

        assert result is None
