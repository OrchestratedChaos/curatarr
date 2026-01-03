"""Tests for utils/tmdb.py"""

import pytest
from unittest.mock import Mock, patch

from utils.tmdb import (
    LANGUAGE_CODES,
    get_full_language_name,
    get_tmdb_keywords,
)


class TestLanguageCodes:
    """Tests for LANGUAGE_CODES constant"""

    def test_contains_common_languages(self):
        assert 'en' in LANGUAGE_CODES
        assert 'es' in LANGUAGE_CODES
        assert 'fr' in LANGUAGE_CODES
        assert 'ja' in LANGUAGE_CODES
        assert 'ko' in LANGUAGE_CODES

    def test_maps_to_full_names(self):
        assert LANGUAGE_CODES['en'] == 'English'
        assert LANGUAGE_CODES['es'] == 'Spanish'
        assert LANGUAGE_CODES['ja'] == 'Japanese'


class TestGetFullLanguageName:
    """Tests for get_full_language_name function"""

    def test_known_language_code(self):
        assert get_full_language_name('en') == 'English'
        assert get_full_language_name('es') == 'Spanish'
        assert get_full_language_name('fr') == 'French'

    def test_case_insensitive(self):
        assert get_full_language_name('EN') == 'English'
        assert get_full_language_name('Es') == 'Spanish'

    def test_unknown_code_capitalized(self):
        assert get_full_language_name('xyz') == 'Xyz'
        assert get_full_language_name('abc') == 'Abc'

    def test_handles_mixed_case_unknown(self):
        assert get_full_language_name('XYZ') == 'Xyz'


class TestGetTmdbKeywords:
    """Tests for get_tmdb_keywords function"""

    def test_returns_empty_list_without_api_key(self):
        result = get_tmdb_keywords(None, 12345, 'movie')
        assert result == []

    def test_returns_empty_list_without_tmdb_id(self):
        result = get_tmdb_keywords('api_key', None, 'movie')
        assert result == []

    def test_returns_cached_keywords(self):
        cache = {'12345': ['action', 'adventure']}
        result = get_tmdb_keywords('api_key', 12345, 'movie', cache)
        assert result == ['action', 'adventure']

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    def test_fetches_movie_keywords(self, mock_fetch):
        mock_fetch.return_value = {
            'keywords': [
                {'id': 1, 'name': 'Action'},
                {'id': 2, 'name': 'Adventure'}
            ]
        }
        result = get_tmdb_keywords('api_key', 12345, 'movie')
        assert result == ['action', 'adventure']
        mock_fetch.assert_called_once()

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    def test_fetches_tv_keywords(self, mock_fetch):
        mock_fetch.return_value = {
            'results': [
                {'id': 1, 'name': 'Drama'},
                {'id': 2, 'name': 'Thriller'}
            ]
        }
        result = get_tmdb_keywords('api_key', 12345, 'tv')
        assert result == ['drama', 'thriller']

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    def test_caches_fetched_keywords(self, mock_fetch):
        mock_fetch.return_value = {
            'keywords': [{'id': 1, 'name': 'Comedy'}]
        }
        cache = {}
        get_tmdb_keywords('api_key', 12345, 'movie', cache)
        assert '12345' in cache
        assert cache['12345'] == ['comedy']

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    def test_returns_empty_list_on_api_failure(self, mock_fetch):
        mock_fetch.return_value = None
        result = get_tmdb_keywords('api_key', 12345, 'movie')
        assert result == []

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    def test_handles_empty_keywords_response(self, mock_fetch):
        mock_fetch.return_value = {'keywords': []}
        result = get_tmdb_keywords('api_key', 12345, 'movie')
        assert result == []

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    def test_keywords_are_lowercase(self, mock_fetch):
        mock_fetch.return_value = {
            'keywords': [
                {'id': 1, 'name': 'UPPERCASE'},
                {'id': 2, 'name': 'MixedCase'}
            ]
        }
        result = get_tmdb_keywords('api_key', 12345, 'movie')
        assert result == ['uppercase', 'mixedcase']
