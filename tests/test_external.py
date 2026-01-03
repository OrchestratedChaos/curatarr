"""Tests for recommenders/external.py - HTML watchlist and export functionality"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
import os
import tempfile
import json

# Import the functions to test
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recommenders.external import (
    get_imdb_id,
    generate_combined_html,
    generate_markdown,
    get_watch_providers,
    categorize_by_streaming_service,
    is_in_library,
    load_cache,
    save_cache,
    SERVICE_DISPLAY_NAMES,
    TMDB_PROVIDERS,
)


class TestGetImdbId:
    """Tests for get_imdb_id function"""

    @patch('recommenders.external.requests.get')
    def test_returns_imdb_id_for_movie(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'imdb_id': 'tt1234567'}
        mock_get.return_value = mock_response

        result = get_imdb_id('api_key', 12345, 'movie')

        assert result == 'tt1234567'
        mock_get.assert_called_once()
        assert 'movie/12345/external_ids' in mock_get.call_args[0][0]

    @patch('recommenders.external.requests.get')
    def test_returns_imdb_id_for_tv(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'imdb_id': 'tt9876543'}
        mock_get.return_value = mock_response

        result = get_imdb_id('api_key', 54321, 'tv')

        assert result == 'tt9876543'
        assert 'tv/54321/external_ids' in mock_get.call_args[0][0]

    @patch('recommenders.external.requests.get')
    def test_returns_none_on_api_error(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = get_imdb_id('api_key', 12345, 'movie')

        assert result is None

    @patch('recommenders.external.requests.get')
    def test_returns_none_on_missing_imdb_id(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'tvdb_id': 123}  # No imdb_id
        mock_get.return_value = mock_response

        result = get_imdb_id('api_key', 12345, 'movie')

        assert result is None

    @patch('recommenders.external.requests.get')
    def test_returns_none_on_exception(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError("Network error")

        result = get_imdb_id('api_key', 12345, 'movie')

        assert result is None


class TestGetWatchProviders:
    """Tests for get_watch_providers function"""

    @patch('recommenders.external.requests.get')
    def test_returns_providers_list(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': {
                'US': {
                    'flatrate': [
                        {'provider_id': 8, 'provider_name': 'Netflix'},
                        {'provider_id': 337, 'provider_name': 'Disney Plus'}
                    ]
                }
            }
        }
        mock_get.return_value = mock_response

        result = get_watch_providers('api_key', 12345, 'movie')

        assert 'netflix' in result
        assert 'disney_plus' in result

    @patch('recommenders.external.requests.get')
    def test_returns_empty_on_no_us_providers(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': {
                'GB': {'flatrate': [{'provider_id': 8}]}
            }
        }
        mock_get.return_value = mock_response

        result = get_watch_providers('api_key', 12345, 'movie')

        assert result == []

    @patch('recommenders.external.requests.get')
    def test_returns_empty_on_error(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        result = get_watch_providers('api_key', 12345, 'movie')

        assert result == []


class TestCategorizeByStreamingService:
    """Tests for categorize_by_streaming_service function"""

    @patch('recommenders.external.get_watch_providers')
    def test_categorizes_by_user_services(self, mock_providers):
        mock_providers.return_value = ['netflix']

        recommendations = [
            {'tmdb_id': 1, 'title': 'Movie 1', 'year': '2023', 'rating': 7.5, 'score': 0.8, 'added_date': datetime.now().isoformat()}
        ]
        user_services = ['netflix', 'hulu']

        result = categorize_by_streaming_service(recommendations, 'api_key', user_services, 'movie')

        assert 'netflix' in result['user_services']
        assert len(result['user_services']['netflix']) == 1

    @patch('recommenders.external.get_watch_providers')
    def test_categorizes_to_acquire(self, mock_providers):
        mock_providers.return_value = []  # Not on any service

        recommendations = [
            {'tmdb_id': 1, 'title': 'Movie 1', 'year': '2023', 'rating': 7.5, 'score': 0.8, 'added_date': datetime.now().isoformat()}
        ]

        result = categorize_by_streaming_service(recommendations, 'api_key', ['netflix'], 'movie')

        assert len(result['acquire']) == 1

    @patch('recommenders.external.get_watch_providers')
    def test_categorizes_other_services(self, mock_providers):
        mock_providers.return_value = ['disney_plus']

        recommendations = [
            {'tmdb_id': 1, 'title': 'Movie 1', 'year': '2023', 'rating': 7.5, 'score': 0.8, 'added_date': datetime.now().isoformat()}
        ]

        result = categorize_by_streaming_service(recommendations, 'api_key', ['netflix'], 'movie')

        assert 'disney_plus' in result['other_services']


class TestIsInLibrary:
    """Tests for is_in_library function"""

    def test_finds_by_tmdb_id(self):
        library_data = {'tmdb_ids': {12345}, 'titles': set()}

        result = is_in_library(12345, 'Some Movie', '2023', library_data)

        assert result is True

    def test_finds_by_title_and_year(self):
        library_data = {'tmdb_ids': set(), 'titles': {('some movie', 2023)}}

        result = is_in_library(None, 'Some Movie', '2023', library_data)

        assert result is True

    def test_finds_by_title_only(self):
        library_data = {'tmdb_ids': set(), 'titles': {('some movie', 2023)}}

        result = is_in_library(None, 'Some Movie', None, library_data)

        assert result is True

    def test_returns_false_when_not_found(self):
        library_data = {'tmdb_ids': set(), 'titles': set()}

        result = is_in_library(None, 'Unknown Movie', '2023', library_data)

        assert result is False


class TestGenerateCombinedHtml:
    """Tests for generate_combined_html function"""

    @patch('recommenders.external.get_imdb_id')
    def test_generates_html_file(self, mock_get_imdb):
        mock_get_imdb.return_value = 'tt1234567'

        with tempfile.TemporaryDirectory() as tmpdir:
            all_users_data = [
                {
                    'username': 'testuser',
                    'display_name': 'Test User',
                    'movies_categorized': {
                        'user_services': {},
                        'other_services': {},
                        'acquire': [
                            {'tmdb_id': 1, 'title': 'Test Movie', 'year': '2023', 'rating': 7.5, 'score': 0.8, 'added_date': datetime.now().isoformat()}
                        ]
                    },
                    'shows_categorized': {
                        'user_services': {},
                        'other_services': {},
                        'acquire': []
                    }
                }
            ]

            result = generate_combined_html(all_users_data, tmpdir, 'api_key')

            assert os.path.exists(result)
            assert result.endswith('watchlist.html')

            with open(result) as f:
                content = f.read()
                assert 'Test User' in content
                assert 'Test Movie' in content
                assert 'Plex Watchlist' in content

    @patch('recommenders.external.get_imdb_id')
    def test_html_contains_tabs_for_multiple_users(self, mock_get_imdb):
        mock_get_imdb.return_value = 'tt1234567'

        with tempfile.TemporaryDirectory() as tmpdir:
            all_users_data = [
                {
                    'username': 'user1',
                    'display_name': 'User One',
                    'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []},
                    'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}
                },
                {
                    'username': 'user2',
                    'display_name': 'User Two',
                    'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []},
                    'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}
                }
            ]

            result = generate_combined_html(all_users_data, tmpdir, 'api_key')

            with open(result) as f:
                content = f.read()
                assert 'User One' in content
                assert 'User Two' in content
                assert 'tab-btn' in content

    @patch('recommenders.external.get_imdb_id')
    def test_html_contains_export_buttons(self, mock_get_imdb):
        mock_get_imdb.return_value = 'tt1234567'

        with tempfile.TemporaryDirectory() as tmpdir:
            all_users_data = [
                {
                    'username': 'testuser',
                    'display_name': 'Test',
                    'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []},
                    'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}
                }
            ]

            result = generate_combined_html(all_users_data, tmpdir, 'api_key')

            with open(result) as f:
                content = f.read()
                assert 'Export to Radarr' in content
                assert 'Export to Sonarr' in content
                assert 'exportRadarr()' in content
                assert 'exportSonarr()' in content

    @patch('recommenders.external.get_imdb_id')
    def test_html_checkboxes_unchecked_by_default(self, mock_get_imdb):
        mock_get_imdb.return_value = 'tt1234567'

        with tempfile.TemporaryDirectory() as tmpdir:
            all_users_data = [
                {
                    'username': 'testuser',
                    'display_name': 'Test',
                    'movies_categorized': {
                        'user_services': {},
                        'other_services': {},
                        'acquire': [
                            {'tmdb_id': 1, 'title': 'Movie', 'year': '2023', 'rating': 7.0, 'score': 0.5, 'added_date': datetime.now().isoformat()}
                        ]
                    },
                    'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}
                }
            ]

            result = generate_combined_html(all_users_data, tmpdir, 'api_key')

            with open(result) as f:
                content = f.read()
                # Should have unchecked checkboxes (no 'checked' attribute on select-item)
                assert 'class="select-item">' in content or 'class="select-item"' in content
                assert 'class="select-item" checked' not in content


class TestCacheOperations:
    """Tests for cache load/save operations"""

    def test_save_and_load_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch the cache directory
            with patch('recommenders.external.os.path.dirname') as mock_dirname:
                mock_dirname.return_value = tmpdir

                cache_data = {
                    '12345': {
                        'tmdb_id': 12345,
                        'title': 'Test Movie',
                        'year': '2023',
                        'rating': 7.5,
                        'score': 0.8,
                        'added_date': '2023-01-01T00:00:00'
                    }
                }

                # Create cache dir
                os.makedirs(os.path.join(tmpdir, 'cache'), exist_ok=True)

                save_cache('TestUser', 'movies', cache_data)

                loaded = load_cache('TestUser', 'movies')

                assert '12345' in loaded
                assert loaded['12345']['title'] == 'Test Movie'

    def test_load_empty_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('recommenders.external.os.path.dirname') as mock_dirname:
                mock_dirname.return_value = tmpdir

                loaded = load_cache('NonExistent', 'movies')

                assert loaded == {}


class TestServiceDisplayNames:
    """Tests for SERVICE_DISPLAY_NAMES constant"""

    def test_contains_major_services(self):
        assert 'netflix' in SERVICE_DISPLAY_NAMES
        assert 'hulu' in SERVICE_DISPLAY_NAMES
        assert 'disney_plus' in SERVICE_DISPLAY_NAMES
        assert 'amazon_prime' in SERVICE_DISPLAY_NAMES

    def test_display_names_are_readable(self):
        assert SERVICE_DISPLAY_NAMES['netflix'] == 'Netflix'
        assert SERVICE_DISPLAY_NAMES['disney_plus'] == 'Disney+'
        assert SERVICE_DISPLAY_NAMES['amazon_prime'] == 'Amazon Prime Video'


class TestTmdbProviders:
    """Tests for TMDB_PROVIDERS mapping"""

    def test_contains_netflix(self):
        assert 8 in TMDB_PROVIDERS
        assert TMDB_PROVIDERS[8] == 'netflix'

    def test_contains_disney_plus(self):
        assert 337 in TMDB_PROVIDERS
        assert TMDB_PROVIDERS[337] == 'disney_plus'
