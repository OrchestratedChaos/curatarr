"""Tests for utils/tmdb.py"""

import pytest
from unittest.mock import Mock, patch

from utils.tmdb import (
    LANGUAGE_CODES,
    get_full_language_name,
    get_tmdb_keywords,
    fetch_tmdb_with_retry,
    get_tmdb_id_for_item,
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


class TestFetchTmdbWithRetry:
    """Tests for fetch_tmdb_with_retry function"""

    @patch('utils.tmdb.requests.get')
    def test_successful_request(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'id': 123, 'title': 'Test Movie'}
        mock_get.return_value = mock_response

        result = fetch_tmdb_with_retry('http://test.api', {'api_key': 'key'})

        assert result == {'id': 123, 'title': 'Test Movie'}

    @patch('utils.tmdb.requests.get')
    def test_returns_none_on_non_200(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = fetch_tmdb_with_retry('http://test.api', {'api_key': 'key'})

        assert result is None

    @patch('utils.tmdb.time.sleep')
    @patch('utils.tmdb.requests.get')
    def test_retries_on_rate_limit(self, mock_get, mock_sleep):
        # First call returns 429, second returns 200
        mock_rate_limit = Mock()
        mock_rate_limit.status_code = 429

        mock_success = Mock()
        mock_success.status_code = 200
        mock_success.json.return_value = {'id': 123}

        mock_get.side_effect = [mock_rate_limit, mock_success]

        result = fetch_tmdb_with_retry('http://test.api', {'api_key': 'key'})

        assert result == {'id': 123}
        assert mock_sleep.called

    @patch('utils.tmdb.time.sleep')
    @patch('utils.tmdb.requests.get')
    def test_retries_on_connection_error(self, mock_get, mock_sleep):
        import requests
        # First call raises error, second succeeds
        mock_success = Mock()
        mock_success.status_code = 200
        mock_success.json.return_value = {'id': 456}

        mock_get.side_effect = [
            requests.exceptions.ConnectionError("Connection failed"),
            mock_success
        ]

        result = fetch_tmdb_with_retry('http://test.api', {'api_key': 'key'})

        assert result == {'id': 456}

    @patch('utils.tmdb.time.sleep')
    @patch('utils.tmdb.requests.get')
    def test_returns_none_after_max_retries(self, mock_get, mock_sleep):
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError("Persistent error")

        result = fetch_tmdb_with_retry('http://test.api', {'api_key': 'key'}, max_retries=3)

        assert result is None
        assert mock_get.call_count == 3

    @patch('utils.tmdb.requests.get')
    def test_returns_none_on_generic_exception(self, mock_get):
        mock_get.side_effect = ValueError("Unexpected error")

        result = fetch_tmdb_with_retry('http://test.api', {'api_key': 'key'})

        assert result is None

    @patch('utils.tmdb.time.sleep')
    @patch('utils.tmdb.requests.get')
    def test_retries_on_timeout(self, mock_get, mock_sleep):
        import requests
        mock_success = Mock()
        mock_success.status_code = 200
        mock_success.json.return_value = {'data': 'success'}

        mock_get.side_effect = [
            requests.exceptions.Timeout("Request timed out"),
            mock_success
        ]

        result = fetch_tmdb_with_retry('http://test.api', {'api_key': 'key'})

        assert result == {'data': 'success'}


class TestGetTmdbIdForItem:
    """Tests for get_tmdb_id_for_item function"""

    @patch('utils.plex.extract_ids_from_guids')
    def test_returns_cached_id(self, mock_extract):
        mock_item = Mock()
        mock_item.ratingKey = 12345

        cache = {'12345': 99999}

        result = get_tmdb_id_for_item(mock_item, 'api_key', cache=cache)

        assert result == 99999

    @patch('utils.plex.extract_ids_from_guids')
    def test_returns_tmdb_id_from_guids(self, mock_extract):
        mock_item = Mock()
        mock_item.ratingKey = 12345

        mock_extract.return_value = {'tmdb_id': 77777, 'imdb_id': None}

        result = get_tmdb_id_for_item(mock_item, 'api_key')

        assert result == 77777

    @patch('utils.plex.extract_ids_from_guids')
    def test_caches_result_from_guids(self, mock_extract):
        mock_item = Mock()
        mock_item.ratingKey = 12345

        mock_extract.return_value = {'tmdb_id': 77777, 'imdb_id': None}
        cache = {}

        get_tmdb_id_for_item(mock_item, 'api_key', cache=cache)

        assert cache['12345'] == 77777

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    @patch('utils.plex.extract_ids_from_guids')
    def test_searches_tmdb_api_when_no_guid(self, mock_extract, mock_fetch):
        mock_item = Mock()
        mock_item.ratingKey = 12345
        mock_item.title = 'Test Movie'
        mock_item.year = 2021

        mock_extract.return_value = {'tmdb_id': None, 'imdb_id': None}
        mock_fetch.return_value = {'results': [{'id': 88888}]}

        result = get_tmdb_id_for_item(mock_item, 'api_key', media_type='movie')

        assert result == 88888

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    @patch('utils.plex.extract_ids_from_guids')
    def test_searches_tv_with_first_air_date_year(self, mock_extract, mock_fetch):
        mock_item = Mock()
        mock_item.ratingKey = 12345
        mock_item.title = 'Test Show'
        mock_item.year = 2020

        mock_extract.return_value = {'tmdb_id': None, 'imdb_id': None}
        mock_fetch.return_value = {'results': [{'id': 55555}]}

        result = get_tmdb_id_for_item(mock_item, 'api_key', media_type='tv')

        assert result == 55555
        # Verify the params include first_air_date_year for TV
        call_args = mock_fetch.call_args
        assert 'first_air_date_year' in call_args[0][1]

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    @patch('utils.plex.extract_ids_from_guids')
    def test_falls_back_to_imdb_lookup(self, mock_extract, mock_fetch):
        mock_item = Mock()
        mock_item.ratingKey = 12345
        mock_item.title = 'Test Movie'
        mock_item.year = None

        mock_extract.return_value = {'tmdb_id': None, 'imdb_id': 'tt1234567'}
        # First search returns empty, second (find) returns result
        mock_fetch.side_effect = [
            {'results': []},  # Search returned nothing
            {'movie_results': [{'id': 44444}]}  # Find by IMDb
        ]

        result = get_tmdb_id_for_item(mock_item, 'api_key', media_type='movie')

        assert result == 44444

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    @patch('utils.plex.extract_ids_from_guids')
    def test_falls_back_to_imdb_for_tv(self, mock_extract, mock_fetch):
        mock_item = Mock()
        mock_item.ratingKey = 12345
        mock_item.title = 'Test Show'
        mock_item.year = None

        mock_extract.return_value = {'tmdb_id': None, 'imdb_id': 'tt9876543'}
        mock_fetch.side_effect = [
            {'results': []},  # Search returned nothing
            {'tv_results': [{'id': 33333}]}  # Find by IMDb for TV
        ]

        result = get_tmdb_id_for_item(mock_item, 'api_key', media_type='tv')

        assert result == 33333

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    @patch('utils.plex.extract_ids_from_guids')
    def test_returns_none_when_all_methods_fail(self, mock_extract, mock_fetch):
        mock_item = Mock()
        mock_item.ratingKey = 12345
        mock_item.title = 'Unknown Movie'
        mock_item.year = None

        mock_extract.return_value = {'tmdb_id': None, 'imdb_id': None}
        mock_fetch.return_value = {'results': []}

        result = get_tmdb_id_for_item(mock_item, 'api_key')

        assert result is None

    @patch('utils.plex.extract_ids_from_guids')
    def test_works_without_api_key(self, mock_extract):
        mock_item = Mock()
        mock_item.ratingKey = 12345

        mock_extract.return_value = {'tmdb_id': 66666, 'imdb_id': None}

        result = get_tmdb_id_for_item(mock_item, None)

        assert result == 66666

    @patch('utils.tmdb.fetch_tmdb_with_retry')
    @patch('utils.plex.extract_ids_from_guids')
    def test_caches_api_search_result(self, mock_extract, mock_fetch):
        mock_item = Mock()
        mock_item.ratingKey = 12345
        mock_item.title = 'Test'
        mock_item.year = None

        mock_extract.return_value = {'tmdb_id': None, 'imdb_id': None}
        mock_fetch.return_value = {'results': [{'id': 22222}]}

        cache = {}
        get_tmdb_id_for_item(mock_item, 'api_key', cache=cache)

        assert cache['12345'] == 22222
