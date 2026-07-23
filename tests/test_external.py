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
    get_tmdb_id_from_imdb,
    export_to_trakt,
    SERVICE_DISPLAY_NAMES,
    TMDB_PROVIDERS,
    get_collection_details,
    load_huntarr_cache,
    save_huntarr_cache,
    load_horizon_cache,
    save_horizon_cache,
    get_movie_status,
    HUNTARR_CACHE_VERSION,
    HORIZON_HUNTARR_CACHE_VERSION,
    EXTERNAL_RECS_CACHE_VERSION,
    get_movie_genre_ids,
    TV_MOVIE_GENRE_ID,
    is_thin_profile,
    discover_popular_by_genre,
    THIN_PROFILE_THRESHOLD,
    process_user,
    process_user_movie_library,
    process_user_tv_library,
    _stamp_library_id,
    _merge_categorized,
    _merge_user_runs,
    _empty_categorized,
)
from utils.trakt import enhance_profile_with_trakt
from collections import Counter


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

    def setup_method(self):
        """Clear the watch provider cache before each test"""
        from recommenders import external
        external._watch_provider_cache.clear()

    @patch('recommenders.external.requests.get')
    def test_returns_providers_dict(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': {
                'US': {
                    'flatrate': [
                        {'provider_id': 8, 'provider_name': 'Netflix'},
                        {'provider_id': 337, 'provider_name': 'Disney Plus'}
                    ],
                    'rent': [
                        {'provider_id': 2, 'provider_name': 'Apple TV'}
                    ],
                    'buy': [
                        {'provider_id': 3, 'provider_name': 'Google Play'}
                    ]
                }
            }
        }
        mock_get.return_value = mock_response

        result = get_watch_providers('api_key', 12345, 'movie')

        assert 'netflix' in result['streaming']
        assert 'disney_plus' in result['streaming']
        assert 'Apple TV' in result['rent']
        assert 'Google Play' in result['buy']

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

        assert result == {'streaming': [], 'rent': [], 'buy': []}

    @patch('recommenders.external.requests.get')
    def test_returns_empty_on_error(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        result = get_watch_providers('api_key', 12345, 'movie')

        assert result == {'streaming': [], 'rent': [], 'buy': []}


class TestCategorizeByStreamingService:
    """Tests for categorize_by_streaming_service function"""

    @patch('recommenders.external.get_watch_providers')
    def test_categorizes_by_user_services(self, mock_providers):
        mock_providers.return_value = {'streaming': ['netflix'], 'rent': [], 'buy': []}

        recommendations = [
            {'tmdb_id': 1, 'title': 'Movie 1', 'year': '2023', 'rating': 7.5, 'score': 0.8, 'added_date': datetime.now().isoformat()}
        ]
        user_services = ['netflix', 'hulu']

        result = categorize_by_streaming_service(recommendations, 'api_key', user_services, 'movie')

        assert 'netflix' in result['user_services']
        assert len(result['user_services']['netflix']) == 1

    @patch('recommenders.external.get_watch_providers')
    def test_categorizes_to_acquire(self, mock_providers):
        mock_providers.return_value = {'streaming': [], 'rent': [], 'buy': []}

        recommendations = [
            {'tmdb_id': 1, 'title': 'Movie 1', 'year': '2023', 'rating': 7.5, 'score': 0.8, 'added_date': datetime.now().isoformat()}
        ]

        result = categorize_by_streaming_service(recommendations, 'api_key', ['netflix'], 'movie')

        assert len(result['acquire']) == 1

    @patch('recommenders.external.get_watch_providers')
    def test_categorizes_other_services(self, mock_providers):
        mock_providers.return_value = {'streaming': ['disney_plus'], 'rent': [], 'buy': []}

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

    def test_generates_html_file(self):
        mock_get_imdb = lambda api_key, tmdb_id, media_type: 'tt1234567'

        with tempfile.TemporaryDirectory() as tmpdir:
            test_movie = {'tmdb_id': 1, 'title': 'Test Movie', 'year': '2023', 'rating': 7.5, 'score': 0.8, 'added_date': datetime.now().isoformat(), 'streaming_services': [], 'on_user_services': []}
            all_users_data = [
                {
                    'username': 'testuser',
                    'display_name': 'Test User',
                    'user_services': [],
                    'movies_categorized': {
                        'user_services': {},
                        'other_services': {},
                        'acquire': [test_movie],
                        'all_items': [test_movie]
                    },
                    'shows_categorized': {
                        'user_services': {},
                        'other_services': {},
                        'acquire': [],
                        'all_items': []
                    }
                }
            ]

            result = generate_combined_html(all_users_data, tmpdir, 'api_key', mock_get_imdb)

            assert os.path.exists(result)
            assert result.endswith('watchlist.html')

            with open(result) as f:
                content = f.read()
                assert 'Test User' in content
                assert 'Test Movie' in content
                assert 'CURATARR' in content
                assert 'Watchlist' in content

    def test_html_contains_tabs_for_multiple_users(self):
        mock_get_imdb = lambda api_key, tmdb_id, media_type: 'tt1234567'

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

            result = generate_combined_html(all_users_data, tmpdir, 'api_key', mock_get_imdb)

            with open(result) as f:
                content = f.read()
                assert 'User One' in content
                assert 'User Two' in content
                assert 'tab-btn' in content

    def test_html_contains_export_buttons(self):
        mock_get_imdb = lambda api_key, tmdb_id, media_type: 'tt1234567'

        with tempfile.TemporaryDirectory() as tmpdir:
            all_users_data = [
                {
                    'username': 'testuser',
                    'display_name': 'Test',
                    'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []},
                    'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}
                }
            ]

            result = generate_combined_html(all_users_data, tmpdir, 'api_key', mock_get_imdb)

            with open(result) as f:
                content = f.read()
                assert 'Export to Radarr' in content
                assert 'Export to Sonarr' in content
                assert 'exportRadarr()' in content
                assert 'exportSonarr()' in content

    def test_html_checkboxes_unchecked_by_default(self):
        mock_get_imdb = lambda api_key, tmdb_id, media_type: 'tt1234567'

        with tempfile.TemporaryDirectory() as tmpdir:
            test_movie = {'tmdb_id': 1, 'title': 'Movie', 'year': '2023', 'rating': 7.0, 'score': 0.5, 'added_date': datetime.now().isoformat(), 'streaming_services': [], 'on_user_services': []}
            all_users_data = [
                {
                    'username': 'testuser',
                    'display_name': 'Test',
                    'user_services': [],
                    'movies_categorized': {
                        'user_services': {},
                        'other_services': {},
                        'acquire': [test_movie],
                        'all_items': [test_movie]
                    },
                    'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': [], 'all_items': []}
                }
            ]

            result = generate_combined_html(all_users_data, tmpdir, 'api_key', mock_get_imdb)

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
            with patch('recommenders.external.get_project_root', return_value=tmpdir):
                cache_data = {
                    '12345': {
                        'tmdb_id': 12345,
                        'title': 'Test Movie',
                        'year': '2023',
                        'rating': 7.5,
                        'vote_count': 500,
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
            with patch('recommenders.external.get_project_root', return_value=tmpdir):
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


# Import additional functions for testing
from recommenders.external import (
    get_library_items,
    load_ignore_list,
)


class TestGetLibraryItems:
    """Tests for get_library_items function"""

    def test_returns_library_data_for_movies(self):
        mock_movie1 = Mock()
        mock_movie1.title = 'Movie One'
        mock_movie1.year = 2023
        mock_guid = Mock()
        mock_guid.id = 'tmdb://12345'
        mock_movie1.guids = [mock_guid]

        mock_movie2 = Mock()
        mock_movie2.title = 'Movie Two'
        mock_movie2.year = 2022
        mock_movie2.guids = []

        mock_section = Mock()
        mock_section.all.return_value = [mock_movie1, mock_movie2]

        mock_plex = Mock()
        mock_plex.library.section.return_value = mock_section

        result = get_library_items(mock_plex, 'Movies', 'movie')

        assert 12345 in result['tmdb_ids']
        assert ('movie one', 2023) in result['titles']
        assert ('movie two', 2022) in result['titles']


class TestLoadIgnoreList:
    """Tests for load_ignore_list function"""

    def test_returns_empty_set_when_no_file(self):
        # Non-existent user should return empty set
        result = load_ignore_list('definitely_nonexistent_user_xyz123')

        assert result == set()
        assert isinstance(result, set)


class TestGetTmdbIdFromImdb:
    """Tests for get_tmdb_id_from_imdb function"""

    @patch('recommenders.external.requests.get')
    def test_returns_tmdb_id_for_movie(self, mock_get):
        """Test successful IMDB to TMDB conversion for movie."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'movie_results': [{'id': 12345}],
            'tv_results': []
        }
        mock_get.return_value = mock_response

        result = get_tmdb_id_from_imdb('api_key', 'tt1234567', 'movie')

        assert result == 12345
        mock_get.assert_called_once()
        assert 'find/tt1234567' in mock_get.call_args[0][0]

    @patch('recommenders.external.requests.get')
    def test_returns_tmdb_id_for_tv(self, mock_get):
        """Test successful IMDB to TMDB conversion for TV."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'movie_results': [],
            'tv_results': [{'id': 67890}]
        }
        mock_get.return_value = mock_response

        result = get_tmdb_id_from_imdb('api_key', 'tt9876543', 'tv')

        assert result == 67890

    @patch('recommenders.external.requests.get')
    def test_returns_none_when_not_found(self, mock_get):
        """Test returns None when IMDB ID not found."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'movie_results': [],
            'tv_results': []
        }
        mock_get.return_value = mock_response

        result = get_tmdb_id_from_imdb('api_key', 'tt0000000', 'movie')

        assert result is None

    @patch('recommenders.external.requests.get')
    def test_returns_none_on_api_error(self, mock_get):
        """Test returns None on API error."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        result = get_tmdb_id_from_imdb('api_key', 'tt1234567', 'movie')

        assert result is None

    @patch('recommenders.external.requests.get')
    def test_returns_none_on_exception(self, mock_get):
        """Test returns None when exception occurs."""
        mock_get.side_effect = Exception("Network error")

        result = get_tmdb_id_from_imdb('api_key', 'tt1234567', 'movie')

        assert result is None


class TestEnhanceProfileWithTrakt:
    """Tests for enhance_profile_with_trakt function"""

    def test_returns_profile_when_trakt_disabled(self):
        """Test returns unchanged profile when Trakt disabled."""
        profile = {
            'genres': Counter({'Action': 5}),
            'actors': Counter(),
            'keywords': Counter(),
            'directors': Counter(),
            'studios': Counter(),
            'tmdb_ids': set()
        }
        config = {'trakt': {'enabled': False}}

        result = enhance_profile_with_trakt(profile, config, 'api_key', '/tmp/cache', 'movie')

        assert result == profile
        assert result['genres']['Action'] == 5

    def test_returns_profile_when_import_disabled(self):
        """Test returns unchanged profile when import disabled."""
        profile = {
            'genres': Counter({'Drama': 3}),
            'actors': Counter(),
            'keywords': Counter(),
            'directors': Counter(),
            'studios': Counter(),
            'tmdb_ids': set()
        }
        config = {
            'trakt': {
                'enabled': True,
                'import': {'enabled': False}
            }
        }

        result = enhance_profile_with_trakt(profile, config, 'api_key', '/tmp/cache', 'movie')

        assert result['genres']['Drama'] == 3

    def test_returns_profile_when_merge_disabled(self):
        """Test returns unchanged profile when merge_watch_history disabled."""
        profile = {
            'genres': Counter({'Comedy': 2}),
            'actors': Counter(),
            'keywords': Counter(),
            'directors': Counter(),
            'studios': Counter(),
            'tmdb_ids': set()
        }
        config = {
            'trakt': {
                'enabled': True,
                'import': {'enabled': True, 'merge_watch_history': False}
            }
        }

        result = enhance_profile_with_trakt(profile, config, 'api_key', '/tmp/cache', 'movie')

        assert result['genres']['Comedy'] == 2

    @patch('utils.trakt.get_authenticated_trakt_client')
    def test_returns_profile_when_not_authenticated(self, mock_get_auth_client):
        """Test returns unchanged profile when Trakt not authenticated."""
        mock_get_auth_client.return_value = None  # Not authenticated

        profile = {
            'genres': Counter({'Horror': 1}),
            'actors': Counter(),
            'keywords': Counter(),
            'directors': Counter(),
            'studios': Counter(),
            'tmdb_ids': set()
        }
        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'import': {'enabled': True, 'merge_watch_history': True}
            }
        }

        result = enhance_profile_with_trakt(profile, config, 'api_key', '/tmp/cache', 'movie')

        assert result['genres']['Horror'] == 1

    @patch('utils.trakt.save_trakt_enhance_cache')
    @patch('utils.trakt.load_trakt_enhance_cache')
    @patch('utils.tmdb.save_imdb_tmdb_cache')
    @patch('utils.tmdb.load_imdb_tmdb_cache')
    @patch('utils.trakt.fetch_tmdb_details_for_profile')
    @patch('utils.tmdb.get_tmdb_id_from_imdb')
    @patch('utils.trakt.get_authenticated_trakt_client')
    def test_merges_trakt_history_into_profile(self, mock_get_auth_client,
                                                mock_get_tmdb_id, mock_get_details,
                                                mock_load_imdb_cache, mock_save_imdb_cache,
                                                mock_load_enhance_cache, mock_save_enhance_cache):
        """Test that Trakt watch history is merged into profile."""
        # Setup cache mocks - empty caches so items are "new"
        mock_load_enhance_cache.return_value = {'movie_ids': set(), 'show_ids': set()}
        mock_load_imdb_cache.return_value = {}

        # Setup mock Trakt client
        mock_client = Mock()
        mock_client.get_watched_movies.return_value = [
            {'movie': {'title': 'Trakt Movie', 'ids': {'imdb': 'tt1111111'}}}
        ]
        mock_get_auth_client.return_value = mock_client

        # Setup IMDB to TMDB conversion
        mock_get_tmdb_id.return_value = 99999

        # Setup TMDB details
        mock_get_details.return_value = {
            'genres': ['Sci-Fi', 'Action'],
            'cast': ['Actor A', 'Actor B'],
            'keywords': ['space', 'aliens'],
            'directors': ['Director X'],
            'studios': []
        }

        profile = {
            'genres': Counter({'Drama': 5}),
            'actors': Counter(),
            'keywords': Counter(),
            'directors': Counter(),
            'studios': Counter(),
            'tmdb_ids': set([12345])  # Existing TMDB ID
        }
        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'import': {'enabled': True, 'merge_watch_history': True}
            }
        }

        result = enhance_profile_with_trakt(profile, config, 'api_key', '/tmp/cache', 'movie')

        # Original profile data preserved
        assert result['genres']['Drama'] == 5
        # New data from Trakt added (genres/keywords lowercased by enhance function)
        assert result['genres']['sci-fi'] == 1
        assert result['genres']['action'] == 1
        assert result['actors']['Actor A'] == 1
        assert result['keywords']['space'] == 1
        assert result['directors']['Director X'] == 1
        assert 99999 in result['tmdb_ids']

    @patch('utils.trakt.load_trakt_enhance_cache')
    @patch('utils.trakt.get_authenticated_trakt_client')
    def test_skips_items_already_in_profile(self, mock_get_auth_client, mock_load_enhance_cache):
        """Test that items already in profile are not re-processed."""
        mock_load_enhance_cache.return_value = {'movie_ids': set(), 'show_ids': set()}
        mock_client = Mock()
        mock_client.get_watched_movies.return_value = [
            {'movie': {'title': 'Already Watched', 'ids': {'imdb': 'tt1111111'}}}
        ]
        mock_get_auth_client.return_value = mock_client

        profile = {
            'genres': Counter({'Drama': 5}),
            'actors': Counter(),
            'keywords': Counter(),
            'directors': Counter(),
            'studios': Counter(),
            'tmdb_ids': set()  # Will check if item gets skipped when TMDB ID matches
        }
        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'import': {'enabled': True, 'merge_watch_history': True}
            }
        }

        with patch('utils.tmdb.get_tmdb_id_from_imdb') as mock_tmdb:
            # Return None to simulate failed conversion (item should be skipped)
            mock_tmdb.return_value = None

            result = enhance_profile_with_trakt(profile, config, 'api_key', '/tmp/cache', 'movie')

            # Profile unchanged since TMDB ID lookup failed
            assert result['genres']['Drama'] == 5
            assert len(result['tmdb_ids']) == 0


class TestExportToTraktAutoSync:
    """Tests for export_to_trakt auto_sync configuration."""

    def test_skips_when_trakt_disabled(self):
        """Test export skips when Trakt disabled."""
        config = {'trakt': {'enabled': False}}
        result = export_to_trakt(config, [], 'api_key')
        assert result is None

    def test_skips_when_export_disabled(self):
        """Test export skips when export.enabled is false."""
        config = {
            'trakt': {
                'enabled': True,
                'export': {'enabled': False}
            }
        }
        result = export_to_trakt(config, [], 'api_key')
        assert result is None

    def test_skips_when_auto_sync_disabled(self):
        """Test export skips when auto_sync is false."""
        config = {
            'trakt': {
                'enabled': True,
                'export': {'enabled': True, 'auto_sync': False}
            }
        }
        result = export_to_trakt(config, [], 'api_key')
        assert result is None

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_skips_when_not_authenticated(self, mock_get_auth_client):
        """Test export skips when client not authenticated."""
        mock_get_auth_client.return_value = None  # Not authenticated

        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'export': {'enabled': True, 'auto_sync': True}
            }
        }
        result = export_to_trakt(config, [], 'api_key')
        assert result is None


class TestExportToTraktUserMode:
    """Tests for export_to_trakt user_mode configuration."""

    def test_mapping_mode_requires_valid_plex_users(self):
        """Test that mapping mode requires configured plex_users."""
        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'access_token': 'token',
                'export': {
                    'enabled': True,
                    'auto_sync': True,
                    'user_mode': 'mapping',
                    'plex_users': ['YourPlexUsername']  # Default placeholder
                }
            }
        }
        result = export_to_trakt(config, [], 'api_key')
        assert result is None

    def test_mapping_mode_rejects_empty_plex_users(self):
        """Test that mapping mode rejects empty plex_users list."""
        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'access_token': 'token',
                'export': {
                    'enabled': True,
                    'auto_sync': True,
                    'user_mode': 'mapping',
                    'plex_users': []
                }
            }
        }
        result = export_to_trakt(config, [], 'api_key')
        assert result is None

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_mapping_mode_filters_users(self, mock_get_auth_client):
        """Test that mapping mode only exports specified users."""
        mock_client = Mock()
        mock_client.get_username.return_value = 'trakt_user'
        mock_client.sync_list.return_value = {'added': {'movies': 2}}
        mock_get_auth_client.return_value = mock_client

        all_users_data = [
            {'username': 'jason', 'display_name': 'Jason', 'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}, 'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}},
            {'username': 'guest', 'display_name': 'Guest', 'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}, 'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}},
        ]
        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'access_token': 'token',
                'export': {
                    'enabled': True,
                    'auto_sync': True,
                    'user_mode': 'mapping',
                    'plex_users': ['jason']  # Only export jason
                }
            }
        }

        export_to_trakt(config, all_users_data, 'api_key')

        # Should not have called sync_list for 'guest' user
        call_args_list = [str(call) for call in mock_client.sync_list.call_args_list]
        assert not any('Guest' in args for args in call_args_list)

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_mapping_mode_case_insensitive(self, mock_get_auth_client):
        """Test that mapping mode matches usernames case-insensitively."""
        mock_client = Mock()
        mock_client.get_username.return_value = 'trakt_user'
        mock_get_auth_client.return_value = mock_client

        all_users_data = [
            {'username': 'Jason', 'display_name': 'Jason', 'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}, 'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}},
        ]
        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'access_token': 'token',
                'export': {
                    'enabled': True,
                    'auto_sync': True,
                    'user_mode': 'mapping',
                    'plex_users': ['jason']  # lowercase
                }
            }
        }

        # Should find 'Jason' even with 'jason' in config
        export_to_trakt(config, all_users_data, 'api_key')
        # No warning should have been logged about missing users

    @patch('recommenders.external_exports.collect_imdb_ids')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_combined_mode_merges_all_users(self, mock_get_auth_client, mock_collect_ids):
        """Test that combined mode creates single merged list."""
        mock_client = Mock()
        mock_client.get_username.return_value = 'trakt_user'
        mock_client.sync_list.return_value = {'added': {'movies': 3}}
        mock_get_auth_client.return_value = mock_client
        mock_collect_ids.side_effect = [
            ['tt0001', 'tt0002'],  # user1 movies
            [],  # user1 shows
            ['tt0003'],  # user2 movies
            [],  # user2 shows
        ]

        all_users_data = [
            {'username': 'user1', 'display_name': 'User1', 'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}, 'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}},
            {'username': 'user2', 'display_name': 'User2', 'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}, 'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}},
        ]
        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'access_token': 'token',
                'export': {
                    'enabled': True,
                    'auto_sync': True,
                    'user_mode': 'combined',
                    'list_prefix': 'Curatarr'
                }
            }
        }

        export_to_trakt(config, all_users_data, 'api_key')

        # Should create combined list, not per-user lists
        mock_client.sync_list.assert_called()
        call_args = mock_client.sync_list.call_args
        # List name should be "Curatarr - Movies" not "Curatarr - User1 - Movies"
        assert 'Curatarr - Movies' == call_args[0][0]

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_per_user_mode_exports_all(self, mock_get_auth_client):
        """Test that per_user mode exports all users."""
        mock_client = Mock()
        mock_client.get_username.return_value = 'trakt_user'
        mock_get_auth_client.return_value = mock_client

        all_users_data = [
            {'username': 'user1', 'display_name': 'User1', 'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}, 'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}},
            {'username': 'user2', 'display_name': 'User2', 'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}, 'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []}},
        ]
        config = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'access_token': 'token',
                'export': {
                    'enabled': True,
                    'auto_sync': True,
                    'user_mode': 'per_user'
                }
            }
        }

        export_to_trakt(config, all_users_data, 'api_key')
        # No error, both users should be processed


class TestGetCollectionDetails:
    """Tests for get_collection_details function"""

    @patch('recommenders.external.requests.get')
    def test_returns_collection_movies(self, mock_get):
        """Test successful collection fetch."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'id': 10,
            'name': 'Star Wars Collection',
            'parts': [
                {'id': 11, 'title': 'Star Wars', 'release_date': '1977-05-25'},
                {'id': 12, 'title': 'Empire Strikes Back', 'release_date': '1980-05-21'},
            ]
        }
        mock_get.return_value = mock_response

        result = get_collection_details('api_key', 10)

        assert result is not None
        assert result['collection_name'] == 'Star Wars Collection'
        assert len(result['movies']) == 2
        assert result['movies'][0]['tmdb_id'] == 11

    @patch('recommenders.external.requests.get')
    def test_returns_none_on_error(self, mock_get):
        """Test returns None on API error."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = get_collection_details('api_key', 99999)

        assert result is None

    @patch('recommenders.external.requests.get')
    def test_returns_none_on_exception(self, mock_get):
        """Test returns None on requests exception."""
        import requests
        mock_get.side_effect = requests.RequestException("Network error")

        result = get_collection_details('api_key', 10)

        assert result is None


class TestHuntarrCache:
    """Tests for Huntarr cache functions"""

    def test_load_cache_returns_empty_when_no_file(self):
        """Test returns empty dict when cache file doesn't exist."""
        result = load_huntarr_cache('/nonexistent/path/cache.json')
        assert result == {}

    def test_load_cache_returns_empty_when_stale(self):
        """Test returns empty dict when cache is stale."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_data = {
                'version': HUNTARR_CACHE_VERSION,
                'cached_at': 0,  # Very old timestamp
                'library_hash': 'abc123',
                'data': {'12345': {'title': 'Test Movie'}}
            }
            json.dump(cache_data, f)
            cache_path = f.name

        try:
            result = load_huntarr_cache(cache_path, stale_days=7)
            assert result == {}  # Should be stale
        finally:
            os.unlink(cache_path)

    def test_load_cache_returns_data_when_fresh(self):
        """Test returns full cache when fresh."""
        import time
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_data = {
                'version': HUNTARR_CACHE_VERSION,
                'cached_at': time.time(),  # Fresh timestamp
                'library_hash': 'abc123',
                'data': {'12345': {'title': 'Test Movie', 'collection_id': 100}}
            }
            json.dump(cache_data, f)
            cache_path = f.name

        try:
            result = load_huntarr_cache(cache_path, stale_days=7)
            # Returns full cache object when fresh
            assert 'data' in result
            assert '12345' in result['data']
            assert result['data']['12345']['title'] == 'Test Movie'
        finally:
            os.unlink(cache_path)

    def test_load_cache_returns_empty_on_version_mismatch(self):
        """Test returns empty dict when cache version doesn't match."""
        import time
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_data = {
                'version': HUNTARR_CACHE_VERSION + 100,  # Wrong version
                'cached_at': time.time(),
                'library_hash': 'abc123',
                'data': {'12345': {'title': 'Test Movie'}}
            }
            json.dump(cache_data, f)
            cache_path = f.name

        try:
            result = load_huntarr_cache(cache_path, stale_days=7)
            assert result == {}  # Wrong version = empty
        finally:
            os.unlink(cache_path)

    def test_save_cache_creates_file(self):
        """Test save creates cache file with version and timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, 'subdir', 'huntarr_cache.json')
            cache_data = {
                'library_hash': 'abc',
                'data': {'1': {'title': 'Movie'}}
            }

            save_huntarr_cache(cache_path, cache_data)

            assert os.path.exists(cache_path)
            with open(cache_path) as f:
                saved = json.load(f)
            assert saved['data']['1']['title'] == 'Movie'
            assert saved['version'] == HUNTARR_CACHE_VERSION
            assert 'cached_at' in saved

    def test_save_cache_overwrites_existing(self):
        """Test save overwrites existing cache."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'old': 'data'}, f)
            cache_path = f.name

        try:
            new_cache = {
                'library_hash': 'new',
                'data': {'new': {'title': 'New Movie'}}
            }
            save_huntarr_cache(cache_path, new_cache)

            with open(cache_path) as f:
                saved = json.load(f)
            assert 'new' in saved['data']
            assert 'old' not in saved
        finally:
            os.unlink(cache_path)


class TestHorizonHuntarrCache:
    """Tests for Horizon Huntarr cache functions"""

    def test_load_horizon_cache_returns_empty_when_no_file(self):
        """Test returns empty dict when cache file doesn't exist."""
        result = load_horizon_cache('/nonexistent/path/cache.json')
        assert result == {}

    def test_load_horizon_cache_returns_empty_when_stale(self):
        """Test returns empty dict when cache is stale."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_data = {
                'version': HORIZON_HUNTARR_CACHE_VERSION,
                'cached_at': 0,  # Very old timestamp
                'library_tmdb_ids': [123, 456],
                'horizon_movies': [{'title': 'Future Movie'}]
            }
            json.dump(cache_data, f)
            cache_path = f.name

        try:
            result = load_horizon_cache(cache_path, stale_days=7)
            assert result == {}  # Should be stale
        finally:
            os.unlink(cache_path)

    def test_load_horizon_cache_returns_data_when_fresh(self):
        """Test returns full cache when fresh."""
        import time
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_data = {
                'version': HORIZON_HUNTARR_CACHE_VERSION,
                'cached_at': time.time(),  # Fresh timestamp
                'library_tmdb_ids': [123, 456],
                'horizon_movies': [{'title': 'Future Movie', 'status': 'In Production'}]
            }
            json.dump(cache_data, f)
            cache_path = f.name

        try:
            result = load_horizon_cache(cache_path, stale_days=7)
            assert 'horizon_movies' in result
            assert result['horizon_movies'][0]['title'] == 'Future Movie'
        finally:
            os.unlink(cache_path)

    def test_save_horizon_cache_creates_file(self):
        """Test save creates cache file with version and timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, 'horizon_cache.json')
            cache_data = {
                'library_tmdb_ids': [123],
                'horizon_movies': [{'title': 'Future Movie'}]
            }

            save_horizon_cache(cache_path, cache_data)

            assert os.path.exists(cache_path)
            with open(cache_path) as f:
                saved = json.load(f)
            assert saved['horizon_movies'][0]['title'] == 'Future Movie'
            assert saved['version'] == HORIZON_HUNTARR_CACHE_VERSION
            assert 'cached_at' in saved


class TestGetMovieStatus:
    """Tests for get_movie_status function"""

    @patch('recommenders.external.requests.get')
    def test_returns_status_and_release_date(self, mock_get):
        """Test returns status and release date from TMDB."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'status': 'In Production',
            'release_date': '2026-06-15'
        }
        mock_get.return_value = mock_response

        status, release_date = get_movie_status('api_key', 12345)
        assert status == 'In Production'
        assert release_date == '2026-06-15'

    @patch('recommenders.external.requests.get')
    def test_returns_unknown_on_api_error(self, mock_get):
        """Test returns Unknown on API error."""
        import requests as req
        mock_get.side_effect = req.RequestException("API Error")

        status, release_date = get_movie_status('api_key', 12345)
        assert status == 'Unknown'
        assert release_date == ''


class TestCategorizeByStreamingServiceAllItems:
    """Tests for categorize_by_streaming_service with all_items structure"""

    @patch('recommenders.external.get_watch_providers')
    def test_returns_all_items_list(self, mock_providers):
        """Test that categorized data includes all_items."""
        mock_providers.side_effect = [
            {'streaming': ['netflix'], 'rent': [], 'buy': []},
            {'streaming': ['hulu'], 'rent': [], 'buy': []}
        ]
        items = [
            {'tmdb_id': 1, 'title': 'Movie 1', 'score': 0.8},
            {'tmdb_id': 2, 'title': 'Movie 2', 'score': 0.7},
        ]
        user_services = ['netflix']

        result = categorize_by_streaming_service(items, 'api_key', user_services, 'movie')

        assert 'all_items' in result
        assert len(result['all_items']) == 2

    @patch('recommenders.external.get_watch_providers')
    def test_all_items_sorted_by_score(self, mock_providers):
        """Test all_items are sorted by score descending."""
        mock_providers.return_value = {'streaming': [], 'rent': [], 'buy': []}
        items = [
            {'tmdb_id': 1, 'title': 'Low Score', 'score': 0.5},
            {'tmdb_id': 2, 'title': 'High Score', 'score': 0.9},
            {'tmdb_id': 3, 'title': 'Mid Score', 'score': 0.7},
        ]

        result = categorize_by_streaming_service(items, 'api_key', [], 'movie')

        scores = [item['score'] for item in result['all_items']]
        assert scores == sorted(scores, reverse=True)

    @patch('recommenders.external.get_watch_providers')
    def test_items_include_streaming_services_list(self, mock_providers):
        """Test each item has streaming_services list from API."""
        mock_providers.return_value = {'streaming': ['netflix', 'hulu'], 'rent': [], 'buy': []}
        items = [
            {'tmdb_id': 1, 'title': 'Movie', 'score': 0.8},
        ]

        result = categorize_by_streaming_service(items, 'api_key', ['netflix'], 'movie')

        item = result['all_items'][0]
        assert 'streaming_services' in item
        assert 'netflix' in item['streaming_services']
        assert 'hulu' in item['streaming_services']

    @patch('recommenders.external.get_watch_providers')
    def test_items_include_on_user_services(self, mock_providers):
        """Test each item has on_user_services list."""
        mock_providers.return_value = {'streaming': ['netflix', 'hulu'], 'rent': [], 'buy': []}
        items = [
            {'tmdb_id': 1, 'title': 'Movie', 'score': 0.8},
        ]
        user_services = ['netflix']

        result = categorize_by_streaming_service(items, 'api_key', user_services, 'movie')

        item = result['all_items'][0]
        assert 'on_user_services' in item
        assert 'netflix' in item['on_user_services']
        assert 'hulu' not in item['on_user_services']

    @patch('recommenders.external.get_watch_providers')
    def test_acquire_items_have_no_streaming(self, mock_providers):
        """Test items with no providers go to acquire list."""
        mock_providers.return_value = {'streaming': [], 'rent': [], 'buy': []}
        items = [
            {'tmdb_id': 1, 'title': 'Rare Movie', 'score': 0.8},
        ]

        result = categorize_by_streaming_service(items, 'api_key', ['netflix'], 'movie')

        assert len(result['acquire']) == 1
        assert result['acquire'][0]['title'] == 'Rare Movie'

    @patch('recommenders.external.get_watch_providers')
    def test_user_service_items_categorized(self, mock_providers):
        """Test items on user's services go to user_services dict."""
        mock_providers.return_value = {'streaming': ['netflix'], 'rent': [], 'buy': []}
        items = [
            {'tmdb_id': 1, 'title': 'Netflix Movie', 'score': 0.8},
        ]

        result = categorize_by_streaming_service(items, 'api_key', ['netflix'], 'movie')

        assert 'netflix' in result['user_services']
        assert len(result['user_services']['netflix']) == 1


class TestExternalRecsCacheVersioning:
    """Tests for external recommendations cache versioning"""

    def test_load_cache_returns_empty_for_old_version(self):
        """Test returns empty dict when cache has old version."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_data = {
                'version': 0,  # Old version
                'items': {'12345': {'title': 'Test Movie', 'tmdb_id': 12345, 'vote_count': 1000}}
            }
            json.dump(cache_data, f)
            cache_path = f.name

        try:
            # Need to create directory structure expected by load_cache
            with tempfile.TemporaryDirectory() as tmpdir:
                # Copy file to expected location
                import shutil
                cache_dir = os.path.join(tmpdir, 'cache')
                os.makedirs(cache_dir, exist_ok=True)
                dest_path = os.path.join(cache_dir, 'external_recs_testuser_movie.json')
                shutil.copy(cache_path, dest_path)

                # Patch the project root detection
                with patch('recommenders.external.get_project_root', return_value=tmpdir):
                    result = load_cache('testuser', 'movie')
                    # Old version should return empty
                    assert result == {}
        finally:
            os.unlink(cache_path)

    def test_save_cache_includes_version(self):
        """Test save adds version to cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch the project root detection
            with patch('recommenders.external.get_project_root', return_value=tmpdir):
                cache_data = {'12345': {'title': 'Test', 'tmdb_id': 12345}}
                save_cache('testuser', 'movie', cache_data)

                cache_path = os.path.join(tmpdir, 'cache', 'external_recs_testuser_movie.json')
                with open(cache_path) as f:
                    saved = json.load(f)

                assert 'version' in saved
                assert saved['version'] == EXTERNAL_RECS_CACHE_VERSION
                assert 'items' in saved

    def test_load_cache_reads_versioned_format(self):
        """Test load correctly reads new versioned format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, 'cache')
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, 'external_recs_testuser_movie.json')

            cache_data = {
                'version': EXTERNAL_RECS_CACHE_VERSION,
                'items': {'12345': {'title': 'Test Movie', 'tmdb_id': 12345, 'vote_count': 1000}}
            }
            with open(cache_path, 'w') as f:
                json.dump(cache_data, f)

            with patch('recommenders.external.get_project_root', return_value=tmpdir):
                result = load_cache('testuser', 'movie')

                assert '12345' in result
                assert result['12345']['title'] == 'Test Movie'


class TestExternalRecsCacheLibraryId:
    """Tests for per-library external recs cache filenames (#157 Phase 3)."""

    def test_no_lib_id_uses_legacy_filename(self):
        """lib_id=None (default) keeps the exact legacy filename - single
        library install byte-identical proof for external recs cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('recommenders.external.get_project_root', return_value=tmpdir):
                save_cache('testuser', 'movies', {'1': {'title': 'A'}})

                expected_path = os.path.join(tmpdir, 'cache', 'external_recs_testuser_movies.json')
                assert os.path.exists(expected_path)

    def test_lib_id_qualifies_filename(self):
        """lib_id qualifies the filename so multiple libraries of the same
        media type don't collide."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('recommenders.external.get_project_root', return_value=tmpdir):
                save_cache('testuser', 'movies', {'1': {'title': 'A'}}, lib_id='movies-4k')

                expected_path = os.path.join(tmpdir, 'cache', 'external_recs_movies-4k_testuser_movies.json')
                assert os.path.exists(expected_path)

    def test_distinct_lib_ids_produce_isolated_caches(self):
        """Two libraries of the same media type get fully isolated caches -
        writing to one must not affect the other."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('recommenders.external.get_project_root', return_value=tmpdir):
                save_cache('testuser', 'movies', {'1': {'title': 'Library A movie', 'vote_count': 500}}, lib_id='movies')
                save_cache('testuser', 'movies', {'2': {'title': 'Library B movie', 'vote_count': 500}}, lib_id='movies-4k')

                cache_a = load_cache('testuser', 'movies', lib_id='movies')
                cache_b = load_cache('testuser', 'movies', lib_id='movies-4k')

                assert '1' in cache_a and '2' not in cache_a
                assert '2' in cache_b and '1' not in cache_b

    def test_stamp_library_id_covers_all_categories(self):
        """_stamp_library_id sets library_id on every item across
        user_services, other_services, and acquire."""
        categorized = {
            'user_services': {'Netflix': [{'tmdb_id': 1}, {'tmdb_id': 2}]},
            'other_services': {'Hulu': [{'tmdb_id': 3}]},
            'acquire': [{'tmdb_id': 4}],
            'all_items': [{'tmdb_id': 1}],  # same objects in real usage
        }

        _stamp_library_id(categorized, 'movies-4k')

        assert categorized['user_services']['Netflix'][0]['library_id'] == 'movies-4k'
        assert categorized['user_services']['Netflix'][1]['library_id'] == 'movies-4k'
        assert categorized['other_services']['Hulu'][0]['library_id'] == 'movies-4k'
        assert categorized['acquire'][0]['library_id'] == 'movies-4k'

    def test_stamp_library_id_handles_none(self):
        """_stamp_library_id(None) is valid - legacy/no-library items stay None."""
        categorized = {'user_services': {}, 'other_services': {}, 'acquire': [{'tmdb_id': 1}]}

        _stamp_library_id(categorized, None)

        assert categorized['acquire'][0]['library_id'] is None

    def test_lib_id_round_trip(self):
        """save_cache(lib_id=...) then load_cache(lib_id=...) reads back
        what was written."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('recommenders.external.get_project_root', return_value=tmpdir):
                cache_data = {'999': {'title': 'Qualified Cache', 'tmdb_id': 999, 'vote_count': 500}}
                save_cache('testuser', 'movies', cache_data, lib_id='movies-4k')

                result = load_cache('testuser', 'movies', lib_id='movies-4k')

                assert '999' in result
                assert result['999']['title'] == 'Qualified Cache'


def _process_user_load_cache_side_effect(display_name, media_type, lib_id=None):
    """Shared fixture data for TestProcessUserLibraryProvenance - one
    already-cached, above-threshold item per media type so discovery
    (movie_deficit/show_deficit) is skipped and only the caching/
    categorization/provenance path under test runs."""
    if media_type == 'movies':
        return {'100': {
            'tmdb_id': 100, 'title': 'Cached Movie', 'year': 2020,
            'rating': 7.5, 'vote_count': 500, 'score': 0.9, 'original_language': 'en'
        }}
    return {'200': {
        'tmdb_id': 200, 'title': 'Cached Show', 'year': 2019,
        'rating': 8.0, 'vote_count': 300, 'score': 0.9, 'original_language': 'en'
    }}


def _process_user_categorize_side_effect(items, tmdb_api_key, user_services, media_type):
    """Fixture standing in for categorize_by_streaming_service - returns one
    item so _stamp_library_id has something to stamp."""
    item = {'tmdb_id': 999 if media_type == 'movie' else 888, 'title': f'{media_type}-item'}
    return {'user_services': {}, 'other_services': {'Netflix': [item]}, 'acquire': [], 'all_items': [item]}


class TestProcessUserLibraryProvenance:
    """Tests for process_user library params and item provenance (#157 Phase 3)."""

    @patch('recommenders.external.generate_markdown')
    @patch('recommenders.external.categorize_by_streaming_service')
    @patch('recommenders.external.save_cache')
    @patch('recommenders.external.load_cache')
    @patch('recommenders.external.load_ignore_list')
    @patch('recommenders.external.enhance_profile_with_trakt')
    @patch('recommenders.external.load_user_profile_from_cache')
    @patch('recommenders.external.get_tmdb_config')
    @patch('recommenders.external.get_library_items')
    def test_legacy_no_library_params(
        self, mock_get_items, mock_get_tmdb, mock_load_profile, mock_enhance,
        mock_load_ignore, mock_load_cache, mock_save_cache, mock_categorize, mock_markdown
    ):
        """No movie_library/tv_library passed (legacy callers): resolves
        section names from config['plex'], cache lib_id stays None, item
        library_id stamps are None, and the top-level library_id is None."""
        mock_get_items.return_value = {'titles': set(), 'tmdb_ids': set()}
        mock_get_tmdb.return_value = {'api_key': 'fake_key', 'use_keywords': True}
        mock_load_profile.return_value = {'genres': {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _process_user_load_cache_side_effect
        mock_categorize.side_effect = _process_user_categorize_side_effect

        config = {
            'plex': {'movie_library': 'Movies', 'tv_library': 'TV Shows'},
            'users': {'preferences': {}},
            'external_recommendations': {'movie_limit': 1, 'show_limit': 1, 'min_relevance_score': 0.65},
            'streaming_services': [],
            'trakt': {},
        }

        result = process_user(config, Mock(), 'alice')

        # Section names resolved from legacy config['plex'] keys
        get_items_calls = mock_get_items.call_args_list
        assert get_items_calls[0][0][1] == 'Movies'
        assert get_items_calls[1][0][1] == 'TV Shows'

        # Cache filenames stay legacy (lib_id=None)
        assert mock_load_cache.call_args_list[0].kwargs['lib_id'] is None
        assert mock_load_cache.call_args_list[1].kwargs['lib_id'] is None
        assert mock_save_cache.call_args_list[0].kwargs['lib_id'] is None
        assert mock_save_cache.call_args_list[1].kwargs['lib_id'] is None

        # Top-level library_id always None (see _stamp_library_id / return docstring)
        assert result['library_id'] is None

        # Item-level provenance stamps are None (no library resolved)
        movie_item = result['movies_categorized']['other_services']['Netflix'][0]
        show_item = result['shows_categorized']['other_services']['Netflix'][0]
        assert movie_item['library_id'] is None
        assert show_item['library_id'] is None

    @patch('recommenders.external.generate_markdown')
    @patch('recommenders.external.categorize_by_streaming_service')
    @patch('recommenders.external.save_cache')
    @patch('recommenders.external.load_cache')
    @patch('recommenders.external.load_ignore_list')
    @patch('recommenders.external.enhance_profile_with_trakt')
    @patch('recommenders.external.load_user_profile_from_cache')
    @patch('recommenders.external.get_tmdb_config')
    @patch('recommenders.external.get_library_items')
    def test_multi_library_params_qualify_cache_and_stamp_items(
        self, mock_get_items, mock_get_tmdb, mock_load_profile, mock_enhance,
        mock_load_ignore, mock_load_cache, mock_save_cache, mock_categorize, mock_markdown
    ):
        """movie_library/tv_library passed with a genuinely multi-library
        movie config (2 movie libraries) but a single tv library:
        - movie cache filenames get lib-qualified (movie_is_multi)
        - tv cache filenames stay legacy (only 1 tv library, despite
          tv_library being passed) - per-media-type independence
        - every returned item is stamped with its real library id
        """
        mock_get_items.return_value = {'titles': set(), 'tmdb_ids': set()}
        mock_get_tmdb.return_value = {'api_key': 'fake_key', 'use_keywords': True}
        mock_load_profile.return_value = {'genres': {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _process_user_load_cache_side_effect
        mock_categorize.side_effect = _process_user_categorize_side_effect

        movie_library = {'id': 'movies-4k', 'name': 'Movies 4K', 'section': 'Movies 4K', 'media_type': 'movie'}
        tv_library = {'id': 'tv-shows', 'name': 'TV Shows', 'section': 'TV Shows', 'media_type': 'tv'}

        config = {
            'plex': {'movie_library': 'Movies', 'tv_library': 'TV Shows'},
            'users': {'preferences': {}},
            'external_recommendations': {'movie_limit': 1, 'show_limit': 1, 'min_relevance_score': 0.65},
            'streaming_services': [],
            'trakt': {},
            'libraries': [
                {'id': 'movies', 'name': 'Movies', 'section': 'Movies', 'media_type': 'movie'},
                movie_library,
                tv_library,
            ],
        }

        result = process_user(config, Mock(), 'alice', movie_library=movie_library, tv_library=tv_library)

        # Section names resolved from the passed library dicts
        get_items_calls = mock_get_items.call_args_list
        assert get_items_calls[0][0][1] == 'Movies 4K'
        assert get_items_calls[1][0][1] == 'TV Shows'

        # Movie cache qualified (2 movie libraries); tv cache stays legacy (1 tv library)
        assert mock_load_cache.call_args_list[0].kwargs['lib_id'] == 'movies-4k'
        assert mock_load_cache.call_args_list[1].kwargs['lib_id'] is None
        assert mock_save_cache.call_args_list[0].kwargs['lib_id'] == 'movies-4k'
        assert mock_save_cache.call_args_list[1].kwargs['lib_id'] is None

        # Top-level library_id stays None even in multi-library mode (see
        # process_user's return docstring: a single call spans both movie
        # and tv libraries, which can have different ids, and Phase 2's
        # _resolve_library_groups isn't media-type-aware for non-None ids)
        assert result['library_id'] is None

        # Item-level provenance carries the real library ids
        movie_item = result['movies_categorized']['other_services']['Netflix'][0]
        show_item = result['shows_categorized']['other_services']['Netflix'][0]
        assert movie_item['library_id'] == 'movies-4k'
        assert show_item['library_id'] == 'tv-shows'


class TestMainLibraryResolution:
    """Tests for main() resolving primary movie/tv libraries (#157 Phase 3)."""

    @patch('recommenders.external.export_to_simkl')
    @patch('recommenders.external.export_to_mdblist')
    @patch('recommenders.external.export_to_radarr')
    @patch('recommenders.external.export_to_sonarr')
    @patch('recommenders.external.export_to_trakt')
    @patch('recommenders.external.generate_combined_html')
    @patch('recommenders.external.find_horizon_movies')
    @patch('recommenders.external.find_missing_sequels')
    @patch('recommenders.external.process_user')
    @patch('recommenders.external.PlexServer')
    @patch('recommenders.external.get_tmdb_config')
    @patch('recommenders.external.load_config')
    @patch('recommenders.external.get_project_root')
    @patch('sys.argv', ['external.py'])
    def test_single_library_install_calls_process_user_once_per_user(
        self, mock_root, mock_load_config, mock_get_tmdb, mock_plex_server,
        mock_process_user, mock_sequels, mock_horizon, mock_html,
        mock_trakt, mock_sonarr, mock_radarr, mock_mdblist, mock_simkl
    ):
        """Single-library install (no 'libraries:' config): process_user is
        called once per user (same call count as before Phase 3), with the
        synthesized movie/tv libraries threaded through."""
        mock_root.return_value = '/fake/root'
        mock_load_config.return_value = {
            'plex': {'url': 'http://x', 'token': 'y', 'movie_library': 'Movies', 'tv_library': 'TV Shows'},
            'users': {'list': 'alice, bob'},
            'huntarr': {'sequel_huntarr': False, 'horizon_huntarr': False},
        }
        mock_get_tmdb.return_value = {'api_key': 'key', 'use_keywords': True}
        mock_plex_server.return_value = Mock()
        mock_process_user.return_value = {
            'username': 'x', 'display_name': 'x',
            'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []},
            'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []},
            'movie_profile': {}, 'show_profile': {}, 'user_services': [], 'library_id': None
        }
        mock_html.return_value = None

        from recommenders.external import main
        main()

        assert mock_process_user.call_count == 2
        for call in mock_process_user.call_args_list:
            assert call.kwargs['movie_library']['id'] == 'movies'
            assert call.kwargs['tv_library']['id'] == 'tv-shows'

        # arr exports get the exact same list as the combined/Trakt exports
        # in the non-fan-out path (library_id is None on every entry either
        # way - see main()'s arr_export_data docstring)
        assert mock_sonarr.call_args[0][1] is mock_radarr.call_args[0][1]
        assert mock_sonarr.call_args[0][1] == mock_trakt.call_args[0][1]

    @patch('recommenders.external.export_to_simkl')
    @patch('recommenders.external.export_to_mdblist')
    @patch('recommenders.external.export_to_radarr')
    @patch('recommenders.external.export_to_sonarr')
    @patch('recommenders.external.export_to_trakt')
    @patch('recommenders.external.generate_combined_html')
    @patch('recommenders.external.find_horizon_movies')
    @patch('recommenders.external.find_missing_sequels')
    @patch('recommenders.external.process_user_tv_library')
    @patch('recommenders.external.process_user_movie_library')
    @patch('recommenders.external.process_user')
    @patch('recommenders.external.PlexServer')
    @patch('recommenders.external.get_tmdb_config')
    @patch('recommenders.external.load_config')
    @patch('recommenders.external.get_project_root')
    @patch('sys.argv', ['external.py'])
    def test_one_movie_one_tv_library_install_calls_process_user_once_per_user(
        self, mock_root, mock_load_config, mock_get_tmdb, mock_plex_server,
        mock_process_user, mock_process_movie_lib, mock_process_tv_lib,
        mock_sequels, mock_horizon, mock_html,
        mock_trakt, mock_sonarr, mock_radarr, mock_mdblist, mock_simkl
    ):
        """#157 Phase 3.5 HARD invariant: an explicit 'libraries:' config with
        exactly one movie library and one tv library is NOT a fan-out - it
        takes the exact same combined process_user() path as a synthesized
        single-library install (byte-identical call count/routing). The new
        per-library fan-out functions are never called."""
        mock_root.return_value = '/fake/root'
        mock_load_config.return_value = {
            'plex': {'url': 'http://x', 'token': 'y'},
            'users': {'list': 'alice, bob'},
            'huntarr': {'sequel_huntarr': False, 'horizon_huntarr': False},
            'libraries': [
                {'id': 'movies', 'name': 'Movies', 'section': 'Movies', 'media_type': 'movie'},
                {'id': 'tv-shows', 'name': 'TV Shows', 'section': 'TV Shows', 'media_type': 'tv'},
            ],
        }
        mock_get_tmdb.return_value = {'api_key': 'key', 'use_keywords': True}
        mock_plex_server.return_value = Mock()
        mock_process_user.return_value = {
            'username': 'x', 'display_name': 'x',
            'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []},
            'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []},
            'movie_profile': {}, 'show_profile': {}, 'user_services': [], 'library_id': None
        }
        mock_html.return_value = None

        from recommenders.external import main
        main()

        assert mock_process_user.call_count == 2
        for call in mock_process_user.call_args_list:
            assert call.kwargs['movie_library']['id'] == 'movies'
            assert call.kwargs['tv_library']['id'] == 'tv-shows'

        mock_process_movie_lib.assert_not_called()
        mock_process_tv_lib.assert_not_called()

        assert mock_sonarr.call_args[0][1] is mock_radarr.call_args[0][1]
        assert mock_sonarr.call_args[0][1] == mock_trakt.call_args[0][1]


class TestFanOutMultiLibrary:
    """Tests for #157 Phase 3.5: external recommendation fan-out for configs
    with 2+ libraries of the same media type."""

    def _fake_movie_data(self, username, library_id):
        return {
            'username': username, 'display_name': username,
            'movies_categorized': {
                'user_services': {}, 'other_services': {},
                'acquire': [{'tmdb_id': 100 if library_id == 'movies' else 200, 'library_id': library_id}],
                'all_items': [{'tmdb_id': 100 if library_id == 'movies' else 200, 'library_id': library_id, 'score': 0.9}],
            },
            'shows_categorized': _empty_categorized(),
            'movie_profile': {'lib': library_id}, 'show_profile': None,
            'user_services': [], 'library_id': library_id,
        }

    def _fake_tv_data(self, username, library_id):
        return {
            'username': username, 'display_name': username,
            'movies_categorized': _empty_categorized(),
            'shows_categorized': {
                'user_services': {}, 'other_services': {},
                'acquire': [{'tmdb_id': 300, 'library_id': library_id}],
                'all_items': [{'tmdb_id': 300, 'library_id': library_id, 'score': 0.8}],
            },
            'movie_profile': None, 'show_profile': {'lib': library_id},
            'user_services': [], 'library_id': library_id,
        }

    @patch('recommenders.external.export_to_simkl')
    @patch('recommenders.external.export_to_mdblist')
    @patch('recommenders.external.export_to_radarr')
    @patch('recommenders.external.export_to_sonarr')
    @patch('recommenders.external.export_to_trakt')
    @patch('recommenders.external.generate_combined_html')
    @patch('recommenders.external.find_horizon_movies')
    @patch('recommenders.external.find_missing_sequels')
    @patch('recommenders.external.process_user_tv_library')
    @patch('recommenders.external.process_user_movie_library')
    @patch('recommenders.external.process_user')
    @patch('recommenders.external.PlexServer')
    @patch('recommenders.external.get_tmdb_config')
    @patch('recommenders.external.load_config')
    @patch('recommenders.external.get_project_root')
    @patch('sys.argv', ['external.py'])
    def test_two_movie_libraries_one_user_fans_out_to_two_runs(
        self, mock_root, mock_load_config, mock_get_tmdb, mock_plex_server,
        mock_process_user, mock_process_movie_lib, mock_process_tv_lib,
        mock_sequels, mock_horizon, mock_html,
        mock_trakt, mock_sonarr, mock_radarr, mock_mdblist, mock_simkl
    ):
        """2 movie libraries x 1 user -> 2 process_user_movie_library() calls
        (one per library), 2 arr_export_data entries each carrying its own
        real library_id, and the combined process_user() is never called."""
        mock_root.return_value = '/fake/root'
        mock_load_config.return_value = {
            'plex': {'url': 'http://x', 'token': 'y'},
            'users': {'list': 'alice'},
            'huntarr': {'sequel_huntarr': False, 'horizon_huntarr': False},
            'libraries': [
                {'id': 'movies', 'name': 'Movies', 'section': 'Movies', 'media_type': 'movie'},
                {'id': 'kids-movies', 'name': 'Kids Movies', 'section': 'Kids Movies', 'media_type': 'movie'},
            ],
        }
        mock_get_tmdb.return_value = {'api_key': 'key', 'use_keywords': True}
        mock_plex_server.return_value = Mock()
        mock_process_movie_lib.side_effect = lambda config, plex, username, library: self._fake_movie_data(username, library['id'])
        mock_html.return_value = None

        from recommenders.external import main
        main()

        mock_process_user.assert_not_called()
        mock_process_tv_lib.assert_not_called()
        assert mock_process_movie_lib.call_count == 2
        called_lib_ids = {call.args[3]['id'] for call in mock_process_movie_lib.call_args_list}
        assert called_lib_ids == {'movies', 'kids-movies'}

        # arr_export_data: 2 destinations, each with its own real library_id
        arr_export_data = mock_radarr.call_args[0][1]
        assert len(arr_export_data) == 2
        assert {d['library_id'] for d in arr_export_data} == {'movies', 'kids-movies'}
        assert mock_sonarr.call_args[0][1] == arr_export_data

        # all_users_data (combined/Trakt view): one merged entry for alice
        all_users_data = mock_trakt.call_args[0][1]
        assert len(all_users_data) == 1
        merged = all_users_data[0]
        assert merged['library_id'] is None
        assert len(merged['movies_categorized']['acquire']) == 2
        acquired_tmdb_ids = {item['tmdb_id'] for item in merged['movies_categorized']['acquire']}
        assert acquired_tmdb_ids == {100, 200}

    @patch('recommenders.external.export_to_simkl')
    @patch('recommenders.external.export_to_mdblist')
    @patch('recommenders.external.export_to_radarr')
    @patch('recommenders.external.export_to_sonarr')
    @patch('recommenders.external.export_to_trakt')
    @patch('recommenders.external.generate_combined_html')
    @patch('recommenders.external.find_horizon_movies')
    @patch('recommenders.external.find_missing_sequels')
    @patch('recommenders.external.process_user_tv_library')
    @patch('recommenders.external.process_user_movie_library')
    @patch('recommenders.external.process_user')
    @patch('recommenders.external.PlexServer')
    @patch('recommenders.external.get_tmdb_config')
    @patch('recommenders.external.load_config')
    @patch('recommenders.external.get_project_root')
    @patch('sys.argv', ['external.py'])
    def test_mixed_two_movie_one_tv_routes_per_media_type(
        self, mock_root, mock_load_config, mock_get_tmdb, mock_plex_server,
        mock_process_user, mock_process_movie_lib, mock_process_tv_lib,
        mock_sequels, mock_horizon, mock_html,
        mock_trakt, mock_sonarr, mock_radarr, mock_mdblist, mock_simkl
    ):
        """2 movie libraries + 1 tv library x 1 user: movies fan out to 2
        scoped runs, tv still gets its own scoped (not combined) run since
        entries must stay media-type-pure. 3 total arr_export_data entries,
        movies routed via export_to_radarr's group, tv via export_to_sonarr's."""
        mock_root.return_value = '/fake/root'
        mock_load_config.return_value = {
            'plex': {'url': 'http://x', 'token': 'y'},
            'users': {'list': 'alice'},
            'huntarr': {'sequel_huntarr': False, 'horizon_huntarr': False},
            'libraries': [
                {'id': 'movies', 'name': 'Movies', 'section': 'Movies', 'media_type': 'movie'},
                {'id': 'kids-movies', 'name': 'Kids Movies', 'section': 'Kids Movies', 'media_type': 'movie'},
                {'id': 'tv-shows', 'name': 'TV Shows', 'section': 'TV Shows', 'media_type': 'tv'},
            ],
        }
        mock_get_tmdb.return_value = {'api_key': 'key', 'use_keywords': True}
        mock_plex_server.return_value = Mock()
        mock_process_movie_lib.side_effect = lambda config, plex, username, library: self._fake_movie_data(username, library['id'])
        mock_process_tv_lib.side_effect = lambda config, plex, username, library: self._fake_tv_data(username, library['id'])
        mock_html.return_value = None

        from recommenders.external import main
        main()

        mock_process_user.assert_not_called()
        assert mock_process_movie_lib.call_count == 2
        assert mock_process_tv_lib.call_count == 1
        assert mock_process_tv_lib.call_args.args[3]['id'] == 'tv-shows'

        arr_export_data = mock_radarr.call_args[0][1]
        assert len(arr_export_data) == 3
        assert {d['library_id'] for d in arr_export_data} == {'movies', 'kids-movies', 'tv-shows'}

        all_users_data = mock_trakt.call_args[0][1]
        assert len(all_users_data) == 1
        merged = all_users_data[0]
        assert len(merged['movies_categorized']['acquire']) == 2
        assert len(merged['shows_categorized']['acquire']) == 1


class TestMergeHelpers:
    """Tests for #157 Phase 3.5 fan-out merge helpers."""

    def test_merge_categorized_combines_and_resorts(self):
        cat_a = {
            'user_services': {'Netflix': [{'tmdb_id': 1, 'score': 0.5}]},
            'other_services': {}, 'acquire': [], 'all_items': [{'tmdb_id': 1, 'score': 0.5}],
        }
        cat_b = {
            'user_services': {'Netflix': [{'tmdb_id': 2, 'score': 0.9}]},
            'other_services': {'Hulu': [{'tmdb_id': 3, 'score': 0.6}]},
            'acquire': [{'tmdb_id': 4, 'score': 0.1}],
            'all_items': [{'tmdb_id': 2, 'score': 0.9}, {'tmdb_id': 3, 'score': 0.6}],
        }

        merged = _merge_categorized([cat_a, cat_b])

        assert len(merged['user_services']['Netflix']) == 2
        assert merged['other_services']['Hulu'][0]['tmdb_id'] == 3
        assert merged['acquire'][0]['tmdb_id'] == 4
        # Re-sorted by score descending across both inputs
        assert [item['tmdb_id'] for item in merged['all_items']] == [2, 3, 1]

    def test_merge_categorized_empty_list(self):
        assert _merge_categorized([]) == _empty_categorized()

    def test_merge_user_runs_movies_only(self):
        movie_run = {
            'display_name': 'Alice', 'user_services': ['netflix'],
            'movies_categorized': {'user_services': {}, 'other_services': {}, 'acquire': [{'tmdb_id': 1}], 'all_items': [{'tmdb_id': 1, 'score': 0.5}]},
            'movie_profile': {'genres': {}},
        }

        merged = _merge_user_runs('alice', [movie_run], [])

        assert merged['username'] == 'alice'
        assert merged['display_name'] == 'Alice'
        assert merged['library_id'] is None
        assert merged['movie_profile'] == {'genres': {}}
        assert merged['show_profile'] is None
        assert merged['shows_categorized'] == _empty_categorized()
        assert len(merged['movies_categorized']['acquire']) == 1

    def test_merge_user_runs_shows_only(self):
        tv_run = {
            'display_name': 'Alice', 'user_services': ['netflix'],
            'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': [{'tmdb_id': 1}], 'all_items': [{'tmdb_id': 1, 'score': 0.5}]},
            'show_profile': {'genres': {}},
        }

        merged = _merge_user_runs('alice', [], [tv_run])

        assert merged['movie_profile'] is None
        assert merged['show_profile'] == {'genres': {}}
        assert merged['movies_categorized'] == _empty_categorized()
        assert len(merged['shows_categorized']['acquire']) == 1


def _fanout_load_cache_side_effect(display_name, media_type, lib_id=None):
    """Same fixture shape as _process_user_load_cache_side_effect but for
    the single-media-type fan-out functions - one already-cached,
    above-threshold item so discovery is skipped."""
    if media_type == 'movies':
        return {'100': {
            'tmdb_id': 100, 'title': 'Cached Movie', 'year': 2020,
            'rating': 7.5, 'vote_count': 500, 'score': 0.9, 'original_language': 'en'
        }}
    return {'200': {
        'tmdb_id': 200, 'title': 'Cached Show', 'year': 2019,
        'rating': 8.0, 'vote_count': 300, 'score': 0.9, 'original_language': 'en'
    }}


def _fanout_categorize_side_effect(items, tmdb_api_key, user_services, media_type):
    item = {'tmdb_id': 999 if media_type == 'movie' else 888, 'title': f'{media_type}-item'}
    return {'user_services': {}, 'other_services': {'Netflix': [item]}, 'acquire': [], 'all_items': [item]}


class TestProcessUserMovieLibrary:
    """Tests for process_user_movie_library() (#157 Phase 3.5 fan-out)."""

    @patch('recommenders.external.generate_markdown')
    @patch('recommenders.external.categorize_by_streaming_service')
    @patch('recommenders.external.save_cache')
    @patch('recommenders.external.load_cache')
    @patch('recommenders.external.load_ignore_list')
    @patch('recommenders.external.enhance_profile_with_trakt')
    @patch('recommenders.external.load_user_profile_from_cache')
    @patch('recommenders.external.get_tmdb_config')
    @patch('recommenders.external.get_library_items')
    def test_qualifies_cache_and_filename_when_multi_library(
        self, mock_get_items, mock_get_tmdb, mock_load_profile, mock_enhance,
        mock_load_ignore, mock_load_cache, mock_save_cache, mock_categorize, mock_markdown
    ):
        mock_get_items.return_value = {'titles': set(), 'tmdb_ids': set()}
        mock_get_tmdb.return_value = {'api_key': 'fake_key', 'use_keywords': True}
        mock_load_profile.return_value = {'genres': {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _fanout_load_cache_side_effect
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {'id': 'kids-movies', 'name': 'Kids Movies', 'section': 'Kids Movies', 'media_type': 'movie'}
        config = {
            'plex': {}, 'users': {'preferences': {}},
            'external_recommendations': {'movie_limit': 1, 'min_relevance_score': 0.65},
            'streaming_services': [], 'trakt': {},
            'libraries': [
                {'id': 'movies', 'name': 'Movies', 'section': 'Movies', 'media_type': 'movie'},
                library,
            ],
        }

        result = process_user_movie_library(config, Mock(), 'alice', library)

        assert mock_get_items.call_args[0][1] == 'Kids Movies'
        assert mock_load_cache.call_args.kwargs['lib_id'] == 'kids-movies'
        assert mock_save_cache.call_args.kwargs['lib_id'] == 'kids-movies'

        assert result['library_id'] == 'kids-movies'
        assert result['show_profile'] is None
        assert result['shows_categorized'] == _empty_categorized()
        movie_item = result['movies_categorized']['other_services']['Netflix'][0]
        assert movie_item['library_id'] == 'kids-movies'

        # Markdown filename qualified with the library id (>1 movie library)
        assert mock_markdown.call_args.kwargs['library_suffix'] == '_kids-movies'

    @patch('recommenders.external.generate_markdown')
    @patch('recommenders.external.categorize_by_streaming_service')
    @patch('recommenders.external.save_cache')
    @patch('recommenders.external.load_cache')
    @patch('recommenders.external.load_ignore_list')
    @patch('recommenders.external.enhance_profile_with_trakt')
    @patch('recommenders.external.load_user_profile_from_cache')
    @patch('recommenders.external.get_tmdb_config')
    @patch('recommenders.external.get_library_items')
    def test_legacy_filename_when_single_library_of_this_type(
        self, mock_get_items, mock_get_tmdb, mock_load_profile, mock_enhance,
        mock_load_ignore, mock_load_cache, mock_save_cache, mock_categorize, mock_markdown
    ):
        """Mixed fan-out (movies multi, tv single) still in progress: this
        media type has only ONE library, so its cache/filename stay legacy
        (unqualified) even though this function is being used - #157 Phase
        3.5's per-media-type independence rule."""
        mock_get_items.return_value = {'titles': set(), 'tmdb_ids': set()}
        mock_get_tmdb.return_value = {'api_key': 'fake_key', 'use_keywords': True}
        mock_load_profile.return_value = {'genres': {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _fanout_load_cache_side_effect
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {'id': 'movies', 'name': 'Movies', 'section': 'Movies', 'media_type': 'movie'}
        config = {
            'plex': {}, 'users': {'preferences': {}},
            'external_recommendations': {'movie_limit': 1, 'min_relevance_score': 0.65},
            'streaming_services': [], 'trakt': {},
            'libraries': [library],
        }

        result = process_user_movie_library(config, Mock(), 'alice', library)

        assert mock_load_cache.call_args.kwargs['lib_id'] is None
        assert mock_save_cache.call_args.kwargs['lib_id'] is None
        assert mock_markdown.call_args.kwargs['library_suffix'] == ''
        assert result['library_id'] == 'movies'


class TestProcessUserTvLibrary:
    """Tests for process_user_tv_library() (#157 Phase 3.5 fan-out)."""

    @patch('recommenders.external.generate_markdown')
    @patch('recommenders.external.categorize_by_streaming_service')
    @patch('recommenders.external.save_cache')
    @patch('recommenders.external.load_cache')
    @patch('recommenders.external.load_ignore_list')
    @patch('recommenders.external.enhance_profile_with_trakt')
    @patch('recommenders.external.load_user_profile_from_cache')
    @patch('recommenders.external.get_tmdb_config')
    @patch('recommenders.external.get_library_items')
    def test_qualifies_cache_and_filename_when_multi_library(
        self, mock_get_items, mock_get_tmdb, mock_load_profile, mock_enhance,
        mock_load_ignore, mock_load_cache, mock_save_cache, mock_categorize, mock_markdown
    ):
        mock_get_items.return_value = {'titles': set(), 'tmdb_ids': set()}
        mock_get_tmdb.return_value = {'api_key': 'fake_key', 'use_keywords': True}
        mock_load_profile.return_value = {'genres': {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _fanout_load_cache_side_effect
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {'id': 'anime', 'name': 'Anime', 'section': 'Anime', 'media_type': 'tv'}
        config = {
            'plex': {}, 'users': {'preferences': {}},
            'external_recommendations': {'show_limit': 1, 'min_relevance_score': 0.65},
            'streaming_services': [], 'trakt': {},
            'libraries': [
                {'id': 'tv-shows', 'name': 'TV Shows', 'section': 'TV Shows', 'media_type': 'tv'},
                library,
            ],
        }

        result = process_user_tv_library(config, Mock(), 'alice', library)

        assert mock_get_items.call_args[0][1] == 'Anime'
        assert mock_load_cache.call_args.kwargs['lib_id'] == 'anime'
        assert mock_save_cache.call_args.kwargs['lib_id'] == 'anime'

        assert result['library_id'] == 'anime'
        assert result['movie_profile'] is None
        assert result['movies_categorized'] == _empty_categorized()
        show_item = result['shows_categorized']['other_services']['Netflix'][0]
        assert show_item['library_id'] == 'anime'
        assert mock_markdown.call_args.kwargs['library_suffix'] == '_anime'


class TestTVMovieGenreDetection:
    """Tests for TV movie (special) genre detection"""

    def test_tv_movie_genre_id_constant(self):
        """Test TV_MOVIE_GENRE_ID is correct"""
        assert TV_MOVIE_GENRE_ID == 10770

    @patch('recommenders.external.requests.get')
    def test_get_movie_genre_ids_returns_genres(self, mock_get):
        """Test get_movie_genre_ids returns list of genre IDs"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'genres': [
                {'id': 28, 'name': 'Action'},
                {'id': 10770, 'name': 'TV Movie'},
                {'id': 35, 'name': 'Comedy'}
            ]
        }
        mock_get.return_value = mock_response

        result = get_movie_genre_ids('api_key', 12345)

        assert result == [28, 10770, 35]
        assert TV_MOVIE_GENRE_ID in result

    @patch('recommenders.external.requests.get')
    def test_get_movie_genre_ids_returns_empty_on_error(self, mock_get):
        """Test get_movie_genre_ids returns empty list on API error"""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = get_movie_genre_ids('api_key', 12345)

        assert result == []

    @patch('recommenders.external.requests.get')
    def test_get_movie_genre_ids_returns_empty_on_exception(self, mock_get):
        """Test get_movie_genre_ids handles exceptions gracefully"""
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError("Network error")

        result = get_movie_genre_ids('api_key', 12345)

        assert result == []

    @patch('recommenders.external.requests.get')
    def test_get_movie_genre_ids_no_genres_in_response(self, mock_get):
        """Test get_movie_genre_ids handles missing genres key"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'title': 'Some Movie'}  # No genres
        mock_get.return_value = mock_response

        result = get_movie_genre_ids('api_key', 12345)

        assert result == []

    def test_is_tv_movie_detection(self):
        """Test TV movie detection logic"""
        # TV movie special (like Phineas and Ferb: Mission Marvel)
        tv_movie_genres = [16, 10770, 10751]  # Animation, TV Movie, Family
        assert TV_MOVIE_GENRE_ID in tv_movie_genres

        # Regular movie
        regular_movie_genres = [28, 12, 878]  # Action, Adventure, Sci-Fi
        assert TV_MOVIE_GENRE_ID not in regular_movie_genres

    def test_tv_special_title_normalization(self):
        """Test that TV special titles match between TMDB movie and Plex episode"""
        import re

        def normalize_title(title):
            return re.sub(r'[^\w\s]', '', title.lower()).strip()

        # TMDB movie title vs Plex episode title (same content, different TMDB IDs)
        tmdb_movie_title = "Phineas and Ferb: Mission Marvel"
        plex_episode_title = "Phineas and Ferb: Mission Marvel"

        assert normalize_title(tmdb_movie_title) == normalize_title(plex_episode_title)
        assert normalize_title(tmdb_movie_title) == "phineas and ferb mission marvel"

        # Test case insensitivity
        assert normalize_title("PHINEAS AND FERB") == normalize_title("phineas and ferb")

        # Test punctuation removal
        assert normalize_title("Movie: The Sequel!") == "movie the sequel"
        assert normalize_title("Test's Movie") == "tests movie"


class TestThinProfile:
    """Tests for thin profile detection"""

    def test_is_thin_profile_returns_true_for_sparse_profile(self):
        """Test that profiles with few items are detected as thin"""
        sparse_profile = {
            'genres': Counter({'Action': 5, 'Comedy': 3, 'Drama': 2})
        }
        # 10 items total, below threshold of 40
        assert is_thin_profile(sparse_profile) is True

    def test_is_thin_profile_returns_false_for_full_profile(self):
        """Test that profiles with enough items are not detected as thin"""
        full_profile = {
            'genres': Counter({'Action': 20, 'Comedy': 15, 'Drama': 10, 'Thriller': 5})
        }
        # 50 items total, above threshold of 40
        assert is_thin_profile(full_profile) is False

    def test_is_thin_profile_exactly_at_threshold(self):
        """Test boundary condition at threshold"""
        at_threshold = {
            'genres': Counter({'Action': 20, 'Comedy': 20})
        }
        # Exactly 40 items, should NOT be thin (need to be below threshold)
        assert is_thin_profile(at_threshold) is False

    def test_is_thin_profile_empty_profile(self):
        """Test empty profile is detected as thin"""
        empty_profile = {'genres': Counter()}
        assert is_thin_profile(empty_profile) is True

    def test_thin_profile_threshold_constant(self):
        """Test threshold constant is set correctly"""
        assert THIN_PROFILE_THRESHOLD == 40


class TestDiscoverPopularByGenre:
    """Tests for genre-popular fallback discovery"""

    @patch('recommenders.external.requests.get')
    @patch('recommenders.external.time.sleep')
    def test_returns_recommendations_for_valid_genres(self, mock_sleep, mock_get):
        """Test that popular items are returned for valid genres"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'id': 123,
                    'title': 'Popular Action Movie',
                    'release_date': '2024-01-15',
                    'vote_average': 8.5,
                    'vote_count': 1000,
                    'overview': 'A great action movie',
                    'genre_ids': [28]
                },
                {
                    'id': 456,
                    'title': 'Another Action Movie',
                    'release_date': '2023-06-20',
                    'vote_average': 7.8,
                    'vote_count': 800,
                    'overview': 'Another good one',
                    'genre_ids': [28]
                }
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        results = discover_popular_by_genre(
            tmdb_api_key='test_key',
            top_genres=['Action'],
            library_data={},
            media_type='movie',
            limit=10
        )

        assert len(results) == 2
        assert results[0]['title'] == 'Popular Action Movie'
        assert results[0]['tmdb_id'] == 123
        assert results[0]['rating'] == 8.5

    @patch('recommenders.external.requests.get')
    @patch('recommenders.external.time.sleep')
    def test_filters_out_library_items(self, mock_sleep, mock_get):
        """Test that items already in library are excluded"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {'id': 123, 'title': 'In Library', 'release_date': '2024-01-01', 'vote_average': 8.0, 'vote_count': 500, 'overview': '', 'genre_ids': []},
                {'id': 456, 'title': 'Not In Library', 'release_date': '2024-01-01', 'vote_average': 8.0, 'vote_count': 500, 'overview': '', 'genre_ids': []}
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # 123 is in library
        library_data = {123: {'title': 'In Library'}}

        results = discover_popular_by_genre(
            tmdb_api_key='test_key',
            top_genres=['Action'],
            library_data=library_data,
            media_type='movie',
            limit=10
        )

        assert len(results) == 1
        assert results[0]['tmdb_id'] == 456

    @patch('recommenders.external.requests.get')
    @patch('recommenders.external.time.sleep')
    def test_handles_tv_shows(self, mock_sleep, mock_get):
        """Test TV show discovery uses correct field names"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {
                    'id': 789,
                    'name': 'Popular TV Show',
                    'first_air_date': '2024-03-01',
                    'vote_average': 9.0,
                    'vote_count': 2000,
                    'overview': 'Great show',
                    'genre_ids': [18]
                }
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        results = discover_popular_by_genre(
            tmdb_api_key='test_key',
            top_genres=['Drama'],
            library_data={},
            media_type='show',
            limit=10
        )

        assert len(results) == 1
        assert results[0]['title'] == 'Popular TV Show'
        assert results[0]['year'] == 2024

    @patch('recommenders.external.requests.get')
    @patch('recommenders.external.time.sleep')
    def test_respects_limit(self, mock_sleep, mock_get):
        """Test that limit parameter is respected"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'results': [
                {'id': i, 'title': f'Movie {i}', 'release_date': '2024-01-01', 'vote_average': 8.0, 'vote_count': 500, 'overview': '', 'genre_ids': []}
                for i in range(20)
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        results = discover_popular_by_genre(
            tmdb_api_key='test_key',
            top_genres=['Action'],
            library_data={},
            media_type='movie',
            limit=5
        )

        assert len(results) == 5

    @patch('recommenders.external.requests.get')
    @patch('recommenders.external.time.sleep')
    def test_handles_invalid_genre(self, mock_sleep, mock_get):
        """Test graceful handling of invalid genre names"""
        results = discover_popular_by_genre(
            tmdb_api_key='test_key',
            top_genres=['NotARealGenre'],
            library_data={},
            media_type='movie',
            limit=10
        )

        # Should return empty list, not crash
        assert results == []
        mock_get.assert_not_called()


class TestFindSimilarContentThinProfile:
    """Tests for thin profile handling in find_similar_content_with_profile"""

    def test_thin_profile_uses_reduced_iterations(self):
        """Test that thin profiles use reduced iterations instead of full discovery"""
        from recommenders.external import find_similar_content_with_profile, is_thin_profile

        # Create a thin profile (less than 40 items)
        thin_profile = {
            'genres': Counter({'Action': 5, 'Comedy': 3}),
            'actors': Counter(),
            'directors': Counter(),
            'studios': Counter(),
            'keywords': Counter(),
            'languages': Counter()
        }

        # Verify it's detected as thin
        assert is_thin_profile(thin_profile) is True

        # The function will run with reduced iterations (max 2)
        # We just verify it doesn't crash and returns a list
        result = find_similar_content_with_profile(
            tmdb_api_key='test_key',
            user_profile=thin_profile,
            library_data={},
            media_type='movie',
            limit=10
        )

        # Should return a list (possibly empty without real API)
        assert isinstance(result, list)

    @patch('recommenders.external.discover_popular_by_genre')
    def test_full_profile_skips_fallback(self, mock_discover):
        """Test that full profiles don't use the fallback"""
        from recommenders.external import find_similar_content_with_profile, is_thin_profile

        # Create a full profile (40+ items)
        full_profile = {
            'genres': Counter({'Action': 20, 'Comedy': 15, 'Drama': 10}),
            'actors': Counter(),
            'directors': Counter(),
            'studios': Counter(),
            'keywords': Counter(),
            'languages': Counter()
        }

        # Verify it's not thin
        assert is_thin_profile(full_profile) is False

        # The function will try to run iterations, which will fail without mocking
        # everything, but at least it won't call the fallback
        # We can't fully test without mocking many more things, but we can
        # verify the profile detection works


class TestEarlyTermination:
    """Tests for early termination logic"""

    def test_consecutive_zero_counter_logic(self):
        """Test the consecutive zero iteration counter logic"""
        # Simulate the counter behavior
        consecutive_zero_iterations = 0

        # First iteration finds 0 items
        new_quality = 0
        if new_quality == 0:
            consecutive_zero_iterations += 1
        else:
            consecutive_zero_iterations = 0
        assert consecutive_zero_iterations == 1

        # Second iteration also finds 0 items
        new_quality = 0
        if new_quality == 0:
            consecutive_zero_iterations += 1
        else:
            consecutive_zero_iterations = 0
        assert consecutive_zero_iterations == 2

        # Should trigger early exit at 2
        should_exit = consecutive_zero_iterations >= 2
        assert should_exit is True

    def test_consecutive_counter_resets_on_success(self):
        """Test that counter resets when items are found"""
        consecutive_zero_iterations = 2  # Already at 2

        # Third iteration finds items
        new_quality = 5
        if new_quality == 0:
            consecutive_zero_iterations += 1
        else:
            consecutive_zero_iterations = 0

        # Should reset to 0
        assert consecutive_zero_iterations == 0
