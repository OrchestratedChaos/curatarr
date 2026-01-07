"""Tests for recommenders/external_exports.py - Export functionality"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import os
import sys
import json
import tempfile
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recommenders.external_exports import (
    flatten_categorized,
    get_imdb_id,
    collect_imdb_ids,
    export_to_trakt,
    export_to_radarr,
    export_to_sonarr,
    export_to_mdblist,
    export_to_simkl,
)


class TestFlattenCategorized:
    """Tests for flatten_categorized function"""

    def test_flattens_user_services(self):
        """Test flattens items from user_services."""
        categorized = {
            'user_services': {
                'netflix': [{'title': 'Movie 1', 'tmdb_id': 1}],
                'hulu': [{'title': 'Movie 2', 'tmdb_id': 2}]
            },
            'other_services': {},
            'acquire': []
        }

        result = flatten_categorized(categorized)

        assert len(result) == 2
        titles = [item['title'] for item in result]
        assert 'Movie 1' in titles
        assert 'Movie 2' in titles

    def test_flattens_other_services(self):
        """Test flattens items from other_services."""
        categorized = {
            'user_services': {},
            'other_services': {
                'disney_plus': [{'title': 'Movie 3', 'tmdb_id': 3}]
            },
            'acquire': []
        }

        result = flatten_categorized(categorized)

        assert len(result) == 1
        assert result[0]['title'] == 'Movie 3'

    def test_flattens_acquire(self):
        """Test includes acquire items."""
        categorized = {
            'user_services': {},
            'other_services': {},
            'acquire': [{'title': 'Rare Movie', 'tmdb_id': 4}]
        }

        result = flatten_categorized(categorized)

        assert len(result) == 1
        assert result[0]['title'] == 'Rare Movie'

    def test_flattens_all_items_as_list(self):
        """Test handles all_items as a list key."""
        categorized = {
            'all_items': [
                {'title': 'All 1', 'tmdb_id': 1},
                {'title': 'All 2', 'tmdb_id': 2}
            ],
            'user_services': {},
            'other_services': {},
            'acquire': []
        }

        result = flatten_categorized(categorized)

        # all_items is a list, so it gets extended
        assert len(result) == 2

    def test_empty_categorized(self):
        """Test handles empty categorized dict."""
        categorized = {
            'user_services': {},
            'other_services': {},
            'acquire': []
        }

        result = flatten_categorized(categorized)

        assert result == []

    def test_combines_all_sources(self):
        """Test combines items from all sources."""
        categorized = {
            'user_services': {'netflix': [{'title': 'A', 'tmdb_id': 1}]},
            'other_services': {'hulu': [{'title': 'B', 'tmdb_id': 2}]},
            'acquire': [{'title': 'C', 'tmdb_id': 3}]
        }

        result = flatten_categorized(categorized)

        assert len(result) == 3


class TestGetImdbId:
    """Tests for get_imdb_id function"""

    @patch('recommenders.external_exports.requests.get')
    def test_returns_imdb_id_for_movie(self, mock_get):
        """Test returns IMDB ID for movie."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'imdb_id': 'tt1234567'}
        mock_get.return_value = mock_response

        result = get_imdb_id('api_key', 12345, 'movie')

        assert result == 'tt1234567'
        assert 'movie/12345/external_ids' in mock_get.call_args[0][0]

    @patch('recommenders.external_exports.requests.get')
    def test_returns_imdb_id_for_tv(self, mock_get):
        """Test returns IMDB ID for TV show."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'imdb_id': 'tt9876543'}
        mock_get.return_value = mock_response

        result = get_imdb_id('api_key', 54321, 'tv')

        assert result == 'tt9876543'
        assert 'tv/54321/external_ids' in mock_get.call_args[0][0]

    @patch('recommenders.external_exports.requests.get')
    def test_returns_none_on_api_error(self, mock_get):
        """Test returns None on API error."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = get_imdb_id('api_key', 12345, 'movie')

        assert result is None

    @patch('recommenders.external_exports.requests.get')
    def test_returns_none_on_request_exception(self, mock_get):
        """Test returns None on requests exception."""
        mock_get.side_effect = requests.RequestException("Network error")

        result = get_imdb_id('api_key', 12345, 'movie')

        assert result is None

    @patch('recommenders.external_exports.requests.get')
    def test_returns_none_when_no_imdb_id(self, mock_get):
        """Test returns None when response has no imdb_id."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'tvdb_id': 123}  # No imdb_id
        mock_get.return_value = mock_response

        result = get_imdb_id('api_key', 12345, 'movie')

        assert result is None


class TestCollectImdbIds:
    """Tests for collect_imdb_ids function"""

    @patch('recommenders.external_exports.get_imdb_id')
    def test_collects_ids_from_categorized(self, mock_get_imdb):
        """Test collects IMDB IDs from categorized items."""
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'

        categorized = {
            'user_services': {'netflix': [{'tmdb_id': 111}, {'tmdb_id': 222}]},
            'other_services': {},
            'acquire': []
        }

        result = collect_imdb_ids(categorized, 'api_key', 'movie')

        assert 'tt111' in result
        assert 'tt222' in result

    @patch('recommenders.external_exports.get_imdb_id')
    def test_collects_ids_from_acquire(self, mock_get_imdb):
        """Test collects IMDB IDs from acquire items."""
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'

        categorized = {
            'user_services': {},
            'other_services': {},
            'acquire': [{'tmdb_id': 333}]
        }

        result = collect_imdb_ids(categorized, 'api_key', 'movie')

        assert 'tt333' in result

    @patch('recommenders.external_exports.get_imdb_id')
    def test_skips_items_without_tmdb_id(self, mock_get_imdb):
        """Test skips items without tmdb_id."""
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'

        categorized = {
            'user_services': {'netflix': [{'title': 'No ID'}]},
            'other_services': {},
            'acquire': []
        }

        result = collect_imdb_ids(categorized, 'api_key', 'movie')

        assert result == []
        mock_get_imdb.assert_not_called()

    @patch('recommenders.external_exports.get_imdb_id')
    def test_skips_failed_lookups(self, mock_get_imdb):
        """Test skips items where IMDB lookup fails."""
        mock_get_imdb.return_value = None

        categorized = {
            'user_services': {'netflix': [{'tmdb_id': 111}]},
            'other_services': {},
            'acquire': []
        }

        result = collect_imdb_ids(categorized, 'api_key', 'movie')

        assert result == []


class TestExportToTrakt:
    """Tests for export_to_trakt function"""

    def test_skips_when_trakt_disabled(self):
        """Test skips export when Trakt disabled."""
        config = {'trakt': {'enabled': False}}
        all_users_data = []

        # Should not raise, just return silently
        export_to_trakt(config, all_users_data, 'api_key')

    def test_skips_when_export_disabled(self):
        """Test skips export when export disabled."""
        config = {
            'trakt': {
                'enabled': True,
                'export': {'enabled': False}
            }
        }
        all_users_data = []

        export_to_trakt(config, all_users_data, 'api_key')

    def test_skips_when_auto_sync_disabled(self):
        """Test skips export when auto_sync disabled."""
        config = {
            'trakt': {
                'enabled': True,
                'export': {'enabled': True, 'auto_sync': False}
            }
        }
        all_users_data = []

        export_to_trakt(config, all_users_data, 'api_key')


class TestExportToRadarr:
    """Tests for export_to_radarr function"""

    def test_skips_when_radarr_disabled(self):
        """Test skips export when Radarr disabled."""
        config = {'radarr': {'enabled': False}}
        all_users_data = []

        export_to_radarr(config, all_users_data, 'api_key')

    def test_skips_when_auto_sync_disabled(self):
        """Test skips export when auto_sync disabled."""
        config = {
            'radarr': {
                'enabled': True,
                'auto_sync': False
            }
        }
        all_users_data = []

        export_to_radarr(config, all_users_data, 'api_key')

    @patch('recommenders.external_exports.create_radarr_client')
    def test_skips_when_no_client(self, mock_create):
        """Test skips when client creation fails."""
        mock_create.return_value = None
        config = {
            'radarr': {
                'enabled': True,
                'auto_sync': True
            }
        }

        export_to_radarr(config, [], 'api_key')

        mock_create.assert_called_once()


class TestExportToSonarr:
    """Tests for export_to_sonarr function"""

    def test_skips_when_sonarr_disabled(self):
        """Test skips export when Sonarr disabled."""
        config = {'sonarr': {'enabled': False}}
        all_users_data = []

        export_to_sonarr(config, all_users_data, 'api_key')

    def test_skips_when_auto_sync_disabled(self):
        """Test skips export when auto_sync disabled."""
        config = {
            'sonarr': {
                'enabled': True,
                'auto_sync': False
            }
        }
        all_users_data = []

        export_to_sonarr(config, all_users_data, 'api_key')

    @patch('recommenders.external_exports.create_sonarr_client')
    def test_skips_when_no_client(self, mock_create):
        """Test skips when client creation fails."""
        mock_create.return_value = None
        config = {
            'sonarr': {
                'enabled': True,
                'auto_sync': True
            }
        }

        export_to_sonarr(config, [], 'api_key')

        mock_create.assert_called_once()


class TestExportToMdblist:
    """Tests for export_to_mdblist function"""

    def test_skips_when_mdblist_disabled(self):
        """Test skips export when MDBList disabled."""
        config = {'mdblist': {'enabled': False}}
        all_users_data = []

        export_to_mdblist(config, all_users_data, 'api_key')

    def test_skips_when_auto_sync_disabled(self):
        """Test skips export when auto_sync disabled."""
        config = {
            'mdblist': {
                'enabled': True,
                'auto_sync': False
            }
        }
        all_users_data = []

        export_to_mdblist(config, all_users_data, 'api_key')

    @patch('recommenders.external_exports.create_mdblist_client')
    def test_skips_when_no_client(self, mock_create):
        """Test skips when client creation fails."""
        mock_create.return_value = None
        config = {
            'mdblist': {
                'enabled': True,
                'auto_sync': True
            }
        }

        export_to_mdblist(config, [], 'api_key')

        mock_create.assert_called_once()


class TestExportToSimkl:
    """Tests for export_to_simkl function"""

    def test_skips_when_simkl_disabled(self):
        """Test skips export when Simkl disabled."""
        config = {'simkl': {'enabled': False}}
        all_users_data = []

        export_to_simkl(config, all_users_data, 'api_key')

    def test_skips_when_export_disabled(self):
        """Test skips export when export.enabled is false."""
        config = {
            'simkl': {
                'enabled': True,
                'export': {'enabled': False}
            }
        }
        all_users_data = []

        export_to_simkl(config, all_users_data, 'api_key')

    def test_skips_when_auto_sync_disabled(self):
        """Test skips export when auto_sync disabled."""
        config = {
            'simkl': {
                'enabled': True,
                'export': {'enabled': True, 'auto_sync': False}
            }
        }
        all_users_data = []

        export_to_simkl(config, all_users_data, 'api_key')

    @patch('recommenders.external_exports.create_simkl_client')
    def test_skips_when_no_client(self, mock_create):
        """Test skips when client creation fails."""
        mock_create.return_value = None
        config = {
            'simkl': {
                'enabled': True,
                'export': {'enabled': True, 'auto_sync': True}
            }
        }

        export_to_simkl(config, [], 'api_key')

        mock_create.assert_called_once()
