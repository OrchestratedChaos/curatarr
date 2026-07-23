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
    sync_watch_history_to_trakt,
    _resolve_library_groups,
    _sync_items_in_batches,
    TraktAPIError,
    TraktAuthError,
    RadarrAPIError,
    SonarrAPIError,
    MDBListAPIError,
    SimklAPIError,
    SimklAuthError,
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


def _mock_radarr_client():
    """MagicMock RadarrClient that echoes routing args back so tests can
    assert exactly what each per-library group resolved."""
    client = MagicMock()
    client.test_connection.return_value = None
    client.get_movies.return_value = []
    client.get_quality_profile_id.side_effect = lambda name: f"qp-{name}"
    client.get_quality_profiles.return_value = []
    client.get_root_folder_path.side_effect = lambda path: path
    client.get_root_folders.return_value = []
    client.get_or_create_tag.side_effect = lambda name: f"tag-{name}"
    client.movie_exists.return_value = False
    client.lookup_movie.side_effect = lambda tmdb_id: {'title': f'Movie {tmdb_id}'}
    client.add_movie.return_value = {}
    return client


class TestExportToRadarrPerLibraryRouting:
    """Tests for #157 Phase 2: per-library Radarr export routing"""

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_single_library_no_library_id_matches_legacy_routing(self, mock_create, mock_create_from):
        """No library_id on recs -> routes via the single synthesized/global library,
        producing identical routing to the pre-Phase-2 flat-config flow."""
        client = _mock_radarr_client()
        mock_create.return_value = client
        mock_create_from.return_value = client

        config = {
            'radarr': {
                'enabled': True,
                'auto_sync': True,
                'user_mode': 'combined',
                'url': 'http://radarr:7878',
                'api_key': 'global-key',
                'root_folder': '/movies',
                'quality_profile': 'HD-1080p',
                'tag': 'Curatarr',
                'monitor': True,
                'search_for_movie': True,
                'minimum_availability': 'released',
            }
        }
        all_users_data = [
            {
                'username': 'jason',
                'display_name': 'Jason',
                'movies_categorized': {'acquire': [{'tmdb_id': 100}], 'user_services': {}, 'other_services': {}},
            }
        ]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        # Preflight client still created once from the global block (unchanged safety gate)
        mock_create.assert_called_once_with(config)
        # Per-library client resolves to the same global block via the no-library_id fallback
        mock_create_from.assert_called_once_with('http://radarr:7878', 'global-key')

        client.add_movie.assert_called_once()
        _, kwargs = client.add_movie.call_args
        assert kwargs['root_folder_path'] == '/movies'
        assert kwargs['quality_profile_id'] == 'qp-HD-1080p'
        assert kwargs['tag_ids'] == ['tag-Curatarr']
        assert kwargs['monitored'] is True
        assert kwargs['minimum_availability'] == 'released'
        assert kwargs['search_for_movie'] is True

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_two_library_ids_build_two_clients_with_own_routing(self, mock_create, mock_create_from):
        """Recs tagged with two different library_ids route through two independently
        -resolved clients, each with its own root_folder/quality_profile/tag."""
        mock_create.return_value = _mock_radarr_client()

        movies_client = _mock_radarr_client()
        kids_client = _mock_radarr_client()
        mock_create_from.side_effect = [movies_client, kids_client]

        config = {
            'radarr': {
                'enabled': True,
                'auto_sync': True,
                'user_mode': 'combined',
                'url': 'http://radarr:7878',
                'api_key': 'global-key',
            },
            'libraries': [
                {
                    'id': 'movies', 'name': 'Movies', 'media_type': 'movie',
                    'arr': {'root_folder': '/movies', 'quality_profile': 'HD-1080p', 'tag': 'Curatarr'},
                },
                {
                    'id': 'kids-movies', 'name': 'Kids Movies', 'media_type': 'movie',
                    'arr': {'root_folder': '/kids-movies', 'quality_profile': 'SD', 'tag': 'Curatarr-Kids'},
                },
            ],
        }
        all_users_data = [
            {
                'username': 'jason', 'display_name': 'Jason', 'library_id': 'movies',
                'movies_categorized': {'acquire': [{'tmdb_id': 100}], 'user_services': {}, 'other_services': {}},
            },
            {
                'username': 'jason', 'display_name': 'Jason', 'library_id': 'kids-movies',
                'movies_categorized': {'acquire': [{'tmdb_id': 200}], 'user_services': {}, 'other_services': {}},
            },
        ]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        assert mock_create_from.call_count == 2

        movies_client.add_movie.assert_called_once()
        _, movies_kwargs = movies_client.add_movie.call_args
        assert movies_kwargs['root_folder_path'] == '/movies'
        assert movies_kwargs['quality_profile_id'] == 'qp-HD-1080p'
        assert movies_kwargs['tag_ids'] == ['tag-Curatarr']

        kids_client.add_movie.assert_called_once()
        _, kids_kwargs = kids_client.add_movie.call_args
        assert kids_kwargs['root_folder_path'] == '/kids-movies'
        assert kids_kwargs['quality_profile_id'] == 'qp-SD'
        assert kids_kwargs['tag_ids'] == ['tag-Curatarr-Kids']

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_library_instance_override_with_field_fallback(self, mock_create, mock_create_from):
        """Per-library arr.instance overrides url/api_key; omitted arr.* fields
        fall back to the global radarr block."""
        mock_create.return_value = _mock_radarr_client()
        library_client = _mock_radarr_client()
        mock_create_from.return_value = library_client

        config = {
            'radarr': {
                'enabled': True,
                'auto_sync': True,
                'user_mode': 'combined',
                'url': 'http://radarr:7878',
                'api_key': 'global-key',
                'root_folder': '/movies',
                'quality_profile': 'HD-1080p',
                'tag': 'Curatarr',
            },
            'libraries': [
                {
                    'id': 'kids-movies', 'name': 'Kids Movies', 'media_type': 'movie',
                    'arr': {
                        'root_folder': '/kids-movies',
                        'instance': {'url': 'http://kids-radarr:7878', 'api_key': 'kids-key'},
                    },
                },
            ],
        }
        all_users_data = [
            {
                'username': 'jason', 'display_name': 'Jason', 'library_id': 'kids-movies',
                'movies_categorized': {'acquire': [{'tmdb_id': 300}], 'user_services': {}, 'other_services': {}},
            },
        ]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        # Instance override used to build the per-library client
        mock_create_from.assert_called_once_with('http://kids-radarr:7878', 'kids-key')

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_combined_mode_add_skip_exists_and_fail_paths(self, mock_create, mock_create_from):
        """Combined mode's add/skip/exists/fail loop (distinct from the
        per-user loop's equivalent, tested separately above)."""
        mock_create.return_value = _mock_radarr_client()
        client = _mock_radarr_client()
        client.movie_exists.side_effect = lambda tmdb_id: tmdb_id == 1

        def lookup(tmdb_id):
            return None if tmdb_id == 2 else {'title': f'Movie {tmdb_id}'}
        client.lookup_movie.side_effect = lookup

        def add_movie(**kwargs):
            if kwargs['tmdb_id'] == 3:
                raise RadarrAPIError("add failed")
            return {}
        client.add_movie.side_effect = add_movie
        mock_create_from.return_value = client

        config = {'radarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'combined'}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {
                'acquire': [{'tmdb_id': 1}, {'tmdb_id': 2}, {'tmdb_id': 3}, {'tmdb_id': 4}],
                'user_services': {}, 'other_services': {},
            },
        }]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        assert client.add_movie.call_count == 2
        attempted = [c.kwargs['tmdb_id'] for c in client.add_movie.call_args_list]
        assert attempted == [3, 4]

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_combined_mode_no_movies_prints_info_and_skips(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_radarr_client()
        client = _mock_radarr_client()
        mock_create_from.return_value = client
        config = {'radarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'combined'}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        client.get_or_create_tag.assert_not_called()
        client.add_movie.assert_not_called()


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


def _mock_sonarr_client():
    """MagicMock SonarrClient that echoes routing args back so tests can
    assert exactly what each per-library group resolved."""
    client = MagicMock()
    client.test_connection.return_value = None
    client.get_series.return_value = []
    client.get_quality_profile_id.side_effect = lambda name: f"qp-{name}"
    client.get_quality_profiles.return_value = []
    client.get_root_folder_path.side_effect = lambda path: path
    client.get_root_folders.return_value = []
    client.get_or_create_tag.side_effect = lambda name: f"tag-{name}"
    client.series_exists.return_value = False
    client.lookup_series.side_effect = lambda tvdb_id: {'title': f'Show {tvdb_id}'}
    client.add_series.return_value = {}
    return client


class TestExportToSonarrPerLibraryRouting:
    """Tests for #157 Phase 2: per-library Sonarr export routing"""

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_single_library_no_library_id_matches_legacy_routing(self, mock_create, mock_create_from):
        """No library_id on recs -> routes via the single synthesized/global library,
        producing identical routing to the pre-Phase-2 flat-config flow."""
        client = _mock_sonarr_client()
        mock_create.return_value = client
        mock_create_from.return_value = client

        config = {
            'sonarr': {
                'enabled': True,
                'auto_sync': True,
                'user_mode': 'combined',
                'url': 'http://sonarr:8989',
                'api_key': 'global-key',
                'root_folder': '/tv',
                'quality_profile': 'HD-1080p',
                'tag': 'Curatarr',
                'monitor': True,
                'search_for_series': True,
                'series_type': 'standard',
                'season_folder': True,
            }
        }
        all_users_data = [
            {
                'username': 'jason',
                'display_name': 'Jason',
                'shows_categorized': {'acquire': [{'tvdb_id': 100}], 'user_services': {}, 'other_services': {}},
            }
        ]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        # Preflight client still created once from the global block (unchanged safety gate)
        mock_create.assert_called_once_with(config)
        # Per-library client resolves to the same global block via the no-library_id fallback
        mock_create_from.assert_called_once_with('http://sonarr:8989', 'global-key')

        client.add_series.assert_called_once()
        _, kwargs = client.add_series.call_args
        assert kwargs['root_folder_path'] == '/tv'
        assert kwargs['quality_profile_id'] == 'qp-HD-1080p'
        assert kwargs['tag_ids'] == ['tag-Curatarr']
        assert kwargs['monitored'] is True
        assert kwargs['search_for_missing_episodes'] is True
        assert kwargs['season_folder'] is True
        assert kwargs['series_type'] == 'standard'

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_two_library_ids_build_two_clients_with_own_routing(self, mock_create, mock_create_from):
        """Recs tagged with two different library_ids route through two independently
        -resolved clients, each with its own root_folder/quality_profile/tag."""
        mock_create.return_value = _mock_sonarr_client()

        tv_client = _mock_sonarr_client()
        anime_client = _mock_sonarr_client()
        mock_create_from.side_effect = [tv_client, anime_client]

        config = {
            'sonarr': {
                'enabled': True,
                'auto_sync': True,
                'user_mode': 'combined',
                'url': 'http://sonarr:8989',
                'api_key': 'global-key',
            },
            'libraries': [
                {
                    'id': 'tv-shows', 'name': 'TV Shows', 'media_type': 'tv',
                    'arr': {'root_folder': '/tv', 'quality_profile': 'HD-1080p', 'tag': 'Curatarr'},
                },
                {
                    'id': 'anime', 'name': 'Anime', 'media_type': 'tv',
                    'arr': {'root_folder': '/anime', 'quality_profile': '4K', 'tag': 'Curatarr-Anime'},
                },
            ],
        }
        all_users_data = [
            {
                'username': 'jason', 'display_name': 'Jason', 'library_id': 'tv-shows',
                'shows_categorized': {'acquire': [{'tvdb_id': 100}], 'user_services': {}, 'other_services': {}},
            },
            {
                'username': 'jason', 'display_name': 'Jason', 'library_id': 'anime',
                'shows_categorized': {'acquire': [{'tvdb_id': 200}], 'user_services': {}, 'other_services': {}},
            },
        ]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        assert mock_create_from.call_count == 2

        tv_client.add_series.assert_called_once()
        _, tv_kwargs = tv_client.add_series.call_args
        assert tv_kwargs['root_folder_path'] == '/tv'
        assert tv_kwargs['quality_profile_id'] == 'qp-HD-1080p'
        assert tv_kwargs['tag_ids'] == ['tag-Curatarr']

        anime_client.add_series.assert_called_once()
        _, anime_kwargs = anime_client.add_series.call_args
        assert anime_kwargs['root_folder_path'] == '/anime'
        assert anime_kwargs['quality_profile_id'] == 'qp-4K'
        assert anime_kwargs['tag_ids'] == ['tag-Curatarr-Anime']

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_library_instance_override_with_field_fallback(self, mock_create, mock_create_from):
        """Per-library arr.instance overrides url/api_key; omitted arr.* fields
        fall back to the global sonarr block."""
        mock_create.return_value = _mock_sonarr_client()
        library_client = _mock_sonarr_client()
        mock_create_from.return_value = library_client

        config = {
            'sonarr': {
                'enabled': True,
                'auto_sync': True,
                'user_mode': 'combined',
                'url': 'http://sonarr:8989',
                'api_key': 'global-key',
                'root_folder': '/tv',
                'quality_profile': 'HD-1080p',
                'tag': 'Curatarr',
            },
            'libraries': [
                {
                    'id': 'anime', 'name': 'Anime', 'media_type': 'tv',
                    'arr': {
                        'root_folder': '/anime',
                        'instance': {'url': 'http://anime-sonarr:8989', 'api_key': 'anime-key'},
                    },
                },
            ],
        }
        all_users_data = [
            {
                'username': 'jason', 'display_name': 'Jason', 'library_id': 'anime',
                'shows_categorized': {'acquire': [{'tvdb_id': 300}], 'user_services': {}, 'other_services': {}},
            },
        ]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        # Instance override used to build the per-library client
        mock_create_from.assert_called_once_with('http://anime-sonarr:8989', 'anime-key')

        library_client.add_series.assert_called_once()
        _, kwargs = library_client.add_series.call_args
        assert kwargs['root_folder_path'] == '/anime'          # library override
        assert kwargs['quality_profile_id'] == 'qp-HD-1080p'   # falls back to global
        assert kwargs['tag_ids'] == ['tag-Curatarr']            # falls back to global

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_combined_mode_add_skip_exists_and_fail_paths(self, mock_create, mock_create_from):
        """Combined mode's add/skip/exists/fail loop (distinct from the
        per-user loop's equivalent, tested separately above)."""
        mock_create.return_value = _mock_sonarr_client()
        client = _mock_sonarr_client()
        client.series_exists.side_effect = lambda tvdb_id: tvdb_id == 1

        def lookup(tvdb_id):
            return None if tvdb_id == 2 else {'title': f'Show {tvdb_id}'}
        client.lookup_series.side_effect = lookup

        def add_series(**kwargs):
            if kwargs['tvdb_id'] == 3:
                raise SonarrAPIError("add failed")
            return {}
        client.add_series.side_effect = add_series
        mock_create_from.return_value = client

        config = {'sonarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'combined'}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'shows_categorized': {
                'acquire': [{'tvdb_id': 1}, {'tvdb_id': 2}, {'tvdb_id': 3}, {'tvdb_id': 4}],
                'user_services': {}, 'other_services': {},
            },
        }]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        assert client.add_series.call_count == 2
        attempted = [c.kwargs['tvdb_id'] for c in client.add_series.call_args_list]
        assert attempted == [3, 4]

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_combined_mode_no_shows_prints_info_and_skips(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_sonarr_client()
        client = _mock_sonarr_client()
        mock_create_from.return_value = client
        config = {'sonarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'combined'}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        client.get_or_create_tag.assert_not_called()
        client.add_series.assert_not_called()


class TestResolveLibraryGroupsMediaTypeAware:
    """Tests for #157 Phase 3.5: _resolve_library_groups resolves an
    explicit library_id against the media-type-filtered library list, so a
    library_id belonging to the wrong media type can never leak into the
    wrong *arr's routing (e.g. a tv library id building a Radarr client)."""

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_tv_library_id_skipped_by_radarr_export(self, mock_create, mock_create_from):
        """A tv library_id never resolves in export_to_radarr's grouping -
        no Radarr client is built for it."""
        mock_create.return_value = _mock_radarr_client()

        config = {
            'radarr': {
                'enabled': True, 'auto_sync': True, 'user_mode': 'combined',
                'url': 'http://radarr:7878', 'api_key': 'global-key',
            },
            'libraries': [
                {'id': 'movies', 'name': 'Movies', 'media_type': 'movie', 'arr': {}},
                {'id': 'tv-shows', 'name': 'TV Shows', 'media_type': 'tv', 'arr': {}},
            ],
        }
        all_users_data = [
            {
                'username': 'jason', 'display_name': 'Jason', 'library_id': 'tv-shows',
                'movies_categorized': {'acquire': [{'tmdb_id': 100}], 'user_services': {}, 'other_services': {}},
            },
        ]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        mock_create_from.assert_not_called()

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_movie_library_id_skipped_by_sonarr_export(self, mock_create, mock_create_from):
        """A movie library_id never resolves in export_to_sonarr's grouping -
        no Sonarr client is built for it."""
        mock_create.return_value = _mock_sonarr_client()

        config = {
            'sonarr': {
                'enabled': True, 'auto_sync': True, 'user_mode': 'combined',
                'url': 'http://sonarr:8989', 'api_key': 'global-key',
            },
            'libraries': [
                {'id': 'movies', 'name': 'Movies', 'media_type': 'movie', 'arr': {}},
                {'id': 'tv-shows', 'name': 'TV Shows', 'media_type': 'tv', 'arr': {}},
            ],
        }
        all_users_data = [
            {
                'username': 'jason', 'display_name': 'Jason', 'library_id': 'movies',
                'shows_categorized': {'acquire': [{'tvdb_id': 100}], 'user_services': {}, 'other_services': {}},
            },
        ]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        mock_create_from.assert_not_called()

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_movie_library_id_still_routes_by_radarr_export(self, mock_create, mock_create_from):
        """Sanity check paired with the skip test above: a correctly-typed
        movie library_id still resolves and routes normally."""
        mock_create.return_value = _mock_radarr_client()
        client = _mock_radarr_client()
        mock_create_from.return_value = client

        config = {
            'radarr': {
                'enabled': True, 'auto_sync': True, 'user_mode': 'combined',
                'url': 'http://radarr:7878', 'api_key': 'global-key',
            },
            'libraries': [
                {'id': 'movies', 'name': 'Movies', 'media_type': 'movie', 'arr': {}},
                {'id': 'tv-shows', 'name': 'TV Shows', 'media_type': 'tv', 'arr': {}},
            ],
        }
        all_users_data = [
            {
                'username': 'jason', 'display_name': 'Jason', 'library_id': 'movies',
                'movies_categorized': {'acquire': [{'tmdb_id': 100}], 'user_services': {}, 'other_services': {}},
            },
        ]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        mock_create_from.assert_called_once_with('http://radarr:7878', 'global-key')
        client.add_movie.assert_called_once()


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


class TestResolveLibraryGroupsDirect:
    """Direct unit tests for _resolve_library_groups (#157 Phase 2/3.5
    per-library *arr export routing/grouping)."""

    @patch('recommenders.external_exports.get_libraries_for_media_type')
    def test_no_library_id_uses_first_candidate(self, mock_get_libs):
        movie_lib = {'id': 'movies', 'name': 'Movies', 'media_type': 'movie'}
        mock_get_libs.return_value = [movie_lib]
        users = [{'username': 'alice'}, {'username': 'bob'}]

        result = _resolve_library_groups({}, users, 'movie')

        assert result == [(movie_lib, users)]

    @patch('recommenders.external_exports.log_warning')
    @patch('recommenders.external_exports.get_libraries_for_media_type', return_value=[])
    def test_no_library_id_and_no_candidates_drops_group(self, mock_get_libs, mock_warn):
        users = [{'username': 'alice'}]

        result = _resolve_library_groups({}, users, 'movie')

        assert result == []
        mock_warn.assert_called_once()

    @patch('recommenders.external_exports.get_libraries_for_media_type')
    def test_explicit_library_id_resolves_matching_library(self, mock_get_libs):
        movies = {'id': 'movies', 'name': 'Movies', 'media_type': 'movie'}
        movies_4k = {'id': 'movies-4k', 'name': 'Movies 4K', 'media_type': 'movie'}
        mock_get_libs.return_value = [movies, movies_4k]
        users = [{'username': 'alice', 'library_id': 'movies-4k'}]

        result = _resolve_library_groups({}, users, 'movie')

        assert result == [(movies_4k, users)]

    @patch('recommenders.external_exports.log_warning')
    @patch('recommenders.external_exports.get_libraries_for_media_type')
    def test_unknown_library_id_drops_group(self, mock_get_libs, mock_warn):
        movies = {'id': 'movies', 'name': 'Movies', 'media_type': 'movie'}
        mock_get_libs.return_value = [movies]
        users = [{'username': 'alice', 'library_id': 'nonexistent'}]

        result = _resolve_library_groups({}, users, 'movie')

        assert result == []
        mock_warn.assert_called_once()

    @patch('recommenders.external_exports.get_libraries_for_media_type')
    def test_multiple_groups_resolved_independently(self, mock_get_libs):
        movies = {'id': 'movies', 'name': 'Movies', 'media_type': 'movie'}
        movies_4k = {'id': 'movies-4k', 'name': 'Movies 4K', 'media_type': 'movie'}
        mock_get_libs.return_value = [movies, movies_4k]
        users = [
            {'username': 'alice', 'library_id': 'movies'},
            {'username': 'bob', 'library_id': 'movies-4k'},
            {'username': 'carol', 'library_id': 'movies'},
        ]

        result = _resolve_library_groups({}, users, 'movie')

        result_by_lib = {lib['id']: group for lib, group in result}
        assert {u['username'] for u in result_by_lib['movies']} == {'alice', 'carol'}
        assert {u['username'] for u in result_by_lib['movies-4k']} == {'bob'}


class TestCollectImdbIdsAdditional:
    """Additional collect_imdb_ids branches not covered above:
    other_services in the inline fallback, and an explicit flatten_func."""

    @patch('recommenders.external_exports.get_imdb_id')
    def test_collects_ids_from_other_services_inline_fallback(self, mock_get_imdb):
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'
        categorized = {
            'user_services': {},
            'other_services': {'hulu': [{'tmdb_id': 444}]},
            'acquire': [],
        }

        result = collect_imdb_ids(categorized, 'api_key', 'movie')

        assert result == ['tt444']

    @patch('recommenders.external_exports.get_imdb_id')
    def test_uses_provided_flatten_func(self, mock_get_imdb):
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'
        categorized = {'anything': 'goes'}
        custom_flatten = Mock(return_value=[{'tmdb_id': 777}])

        result = collect_imdb_ids(categorized, 'api_key', 'movie', flatten_func=custom_flatten)

        custom_flatten.assert_called_once_with(categorized)
        assert result == ['tt777']


class TestSyncItemsInBatches:
    """Tests for _sync_items_in_batches helper"""

    def test_empty_items_returns_zero(self):
        client = MagicMock()

        result = _sync_items_in_batches([], client, 'movies', 'movies')

        assert result == 0
        client.add_to_history.assert_not_called()

    def test_single_batch_movies(self):
        client = MagicMock()
        client.add_to_history.return_value = {'added': {'movies': 3}}
        items = ['tt1', 'tt2', 'tt3']

        result = _sync_items_in_batches(items, client, 'movies', 'movies')

        assert result == 3
        client.add_to_history.assert_called_once_with(movies=items)

    def test_single_batch_shows(self):
        client = MagicMock()
        client.add_to_history.return_value = {'added': {'episodes': 5}}
        items = ['tt1']

        result = _sync_items_in_batches(items, client, 'shows', 'episodes')

        assert result == 5
        client.add_to_history.assert_called_once_with(shows=items)

    def test_multiple_batches_sums_totals(self):
        client = MagicMock()
        client.add_to_history.side_effect = [
            {'added': {'movies': 100}},
            {'added': {'movies': 50}},
        ]
        items = [f'tt{i}' for i in range(150)]

        result = _sync_items_in_batches(items, client, 'movies', 'movies')

        assert result == 150
        assert client.add_to_history.call_count == 2
        first_batch = client.add_to_history.call_args_list[0].kwargs['movies']
        second_batch = client.add_to_history.call_args_list[1].kwargs['movies']
        assert len(first_batch) == 100
        assert len(second_batch) == 50


def _mock_trakt_client(username='traktuser'):
    """MagicMock TraktClient for export_to_trakt / sync_watch_history_to_trakt tests."""
    client = MagicMock()
    client.get_username.return_value = username
    client.sync_list.return_value = {}
    client.get_watch_history_imdb_ids.return_value = set()
    return client


class TestExportToTraktLogic:
    """Tests for export_to_trakt's real logic (mapping/per_user/combined,
    payload building, and error handling) - previously only the
    disabled/skip-path branches were covered."""

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_no_client_warns_and_returns(self, mock_get_client):
        mock_get_client.return_value = None
        config = {'trakt': {'enabled': True}}

        export_to_trakt(config, [], 'tmdb-key')

        mock_get_client.assert_called_once()

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_mapping_mode_no_plex_users_warns_and_returns(self, mock_get_client):
        client = _mock_trakt_client()
        mock_get_client.return_value = client
        config = {'trakt': {'enabled': True, 'export': {'user_mode': 'mapping', 'plex_users': []}}}

        export_to_trakt(config, [{'username': 'jason'}], 'tmdb-key')

        client.sync_list.assert_not_called()

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_mapping_mode_placeholder_plex_users_warns(self, mock_get_client):
        client = _mock_trakt_client()
        mock_get_client.return_value = client
        config = {
            'trakt': {'enabled': True, 'export': {'user_mode': 'mapping', 'plex_users': ['YourPlexUsername']}}
        }

        export_to_trakt(config, [{'username': 'jason'}], 'tmdb-key')

        client.sync_list.assert_not_called()

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_mapping_mode_no_matching_users_warns(self, mock_get_client):
        client = _mock_trakt_client()
        mock_get_client.return_value = client
        config = {'trakt': {'enabled': True, 'export': {'user_mode': 'mapping', 'plex_users': ['jason']}}}
        all_users_data = [{'username': 'other', 'display_name': 'Other'}]

        export_to_trakt(config, all_users_data, 'tmdb-key')

        client.sync_list.assert_not_called()

    @patch('recommenders.external_exports.get_imdb_id')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_mapping_mode_syncs_matched_user_movies_and_shows(self, mock_get_client, mock_get_imdb):
        client = _mock_trakt_client(username='jasontv')
        mock_get_client.return_value = client
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'

        config = {
            'trakt': {
                'enabled': True,
                'export': {'user_mode': 'mapping', 'plex_users': ['Jason'], 'list_prefix': 'MyRecs'},
            }
        }
        all_users_data = [
            {
                'username': 'jason', 'display_name': 'Jason',
                'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
                'shows_categorized': {'acquire': [{'tmdb_id': 2}], 'user_services': {}, 'other_services': {}},
            },
            {
                'username': 'other', 'display_name': 'Other',
                'movies_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
                'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
            },
        ]

        export_to_trakt(config, all_users_data, 'tmdb-key')

        assert client.sync_list.call_count == 2
        movie_call = client.sync_list.call_args_list[0]
        assert movie_call.args[0] == 'MyRecs - Jason - Movies'
        assert movie_call.kwargs['movies'] == ['tt1']
        show_call = client.sync_list.call_args_list[1]
        assert show_call.args[0] == 'MyRecs - Jason - TV'
        assert show_call.kwargs['shows'] == ['tt2']

    @patch('recommenders.external_exports.get_imdb_id')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_skips_show_sync_when_no_show_ids(self, mock_get_client, mock_get_imdb):
        client = _mock_trakt_client()
        mock_get_client.return_value = client
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'
        config = {'trakt': {'enabled': True, 'export': {'user_mode': 'mapping', 'plex_users': ['jason']}}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
            'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_trakt(config, all_users_data, 'tmdb-key')

        client.sync_list.assert_called_once()
        assert client.sync_list.call_args.args[0] == 'Curatarr - Jason - Movies'

    @patch('recommenders.external_exports.get_imdb_id')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_combined_mode_merges_and_dedups_across_users(self, mock_get_client, mock_get_imdb):
        client = _mock_trakt_client(username='jasontv')
        mock_get_client.return_value = client
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'
        config = {'trakt': {'enabled': True, 'export': {'user_mode': 'combined', 'list_prefix': 'Fam'}}}
        all_users_data = [
            {'username': 'a', 'display_name': 'A',
             'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
             'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}}},
            {'username': 'b', 'display_name': 'B',
             'movies_categorized': {'acquire': [{'tmdb_id': 1}, {'tmdb_id': 2}], 'user_services': {}, 'other_services': {}},
             'shows_categorized': {'acquire': [{'tmdb_id': 3}], 'user_services': {}, 'other_services': {}}},
        ]

        export_to_trakt(config, all_users_data, 'tmdb-key')

        assert client.sync_list.call_count == 2
        movie_call = client.sync_list.call_args_list[0]
        assert movie_call.args[0] == 'Fam - Movies'
        assert movie_call.kwargs['movies'] == ['tt1', 'tt2']
        show_call = client.sync_list.call_args_list[1]
        assert show_call.args[0] == 'Fam - TV'
        assert show_call.kwargs['shows'] == ['tt3']

    @patch('recommenders.external_exports.get_imdb_id')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_per_user_sync_error_logged_and_next_user_continues(self, mock_get_client, mock_get_imdb):
        client = _mock_trakt_client()
        mock_get_client.return_value = client
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'
        client.sync_list.side_effect = [TraktAPIError("boom"), {}]

        config = {'trakt': {'enabled': True, 'export': {'user_mode': 'per_user'}}}
        all_users_data = [
            {'username': 'a', 'display_name': 'A',
             'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
             'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}}},
            {'username': 'b', 'display_name': 'B',
             'movies_categorized': {'acquire': [{'tmdb_id': 2}], 'user_services': {}, 'other_services': {}},
             'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}}},
        ]

        export_to_trakt(config, all_users_data, 'tmdb-key')  # should not raise

        assert client.sync_list.call_count == 2

    @patch('recommenders.external_exports.get_imdb_id')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_combined_mode_error_logged_not_raised(self, mock_get_client, mock_get_imdb):
        client = _mock_trakt_client()
        mock_get_client.return_value = client
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'
        client.sync_list.side_effect = TraktAuthError("expired")

        config = {'trakt': {'enabled': True, 'export': {'user_mode': 'combined'}}}
        all_users_data = [{
            'username': 'a', 'display_name': 'A',
            'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
            'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_trakt(config, all_users_data, 'tmdb-key')  # should not raise


class TestExportToRadarrNonCombinedMode:
    """Tests for export_to_radarr's per-user/mapping-mode loop (the
    non-combined branch) - previously untested beyond the disabled/skip
    paths and the combined-mode per-library routing tests."""

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_mapping_mode_no_plex_users_warns_and_returns(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_radarr_client()
        config = {'radarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'mapping', 'plex_users': []}}

        export_to_radarr(config, [{'username': 'jason'}], 'tmdb-key')

        mock_create_from.assert_not_called()

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_mapping_mode_no_matching_users_warns(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_radarr_client()
        config = {'radarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'mapping', 'plex_users': ['jason']}}
        all_users_data = [{'username': 'other', 'display_name': 'Other', 'movies_categorized': {}}]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        mock_create_from.assert_not_called()

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_connect_failure_logs_and_returns(self, mock_create, mock_create_from):
        client = _mock_radarr_client()
        client.test_connection.side_effect = RadarrAPIError("down")
        mock_create.return_value = client
        config = {'radarr': {'enabled': True, 'auto_sync': True}}

        export_to_radarr(config, [], 'tmdb-key')

        mock_create_from.assert_not_called()

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_per_user_mode_add_skip_exists_and_fail_paths(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_radarr_client()
        client = _mock_radarr_client()
        # movie 1 already exists (skipped), movie 2 lookup fails, movie 3 add raises, movie 4 succeeds
        client.movie_exists.side_effect = lambda tmdb_id: tmdb_id == 1

        def lookup(tmdb_id):
            return None if tmdb_id == 2 else {'title': f'Movie {tmdb_id}'}
        client.lookup_movie.side_effect = lookup

        def add_movie(**kwargs):
            if kwargs['tmdb_id'] == 3:
                raise RadarrAPIError("add failed")
            return {}
        client.add_movie.side_effect = add_movie
        mock_create_from.return_value = client

        config = {
            'radarr': {
                'enabled': True, 'auto_sync': True, 'user_mode': 'per_user',
                'url': 'http://radarr:7878', 'api_key': 'key',
            }
        }
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {
                'acquire': [{'tmdb_id': 1}, {'tmdb_id': 2}, {'tmdb_id': 3}, {'tmdb_id': 4}],
                'user_services': {}, 'other_services': {},
            },
        }]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        assert client.add_movie.call_count == 2
        attempted = [c.kwargs['tmdb_id'] for c in client.add_movie.call_args_list]
        assert attempted == [3, 4]

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_per_user_mode_skips_user_with_no_movies(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_radarr_client()
        client = _mock_radarr_client()
        mock_create_from.return_value = client
        config = {'radarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        client.get_or_create_tag.assert_not_called()
        client.add_movie.assert_not_called()

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_append_usernames_true_uses_per_user_tag(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_radarr_client()
        client = _mock_radarr_client()
        mock_create_from.return_value = client
        config = {
            'radarr': {
                'enabled': True, 'auto_sync': True, 'user_mode': 'per_user',
                'append_usernames': True, 'tag': 'Curatarr',
            }
        }
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {'acquire': [{'tmdb_id': 10}], 'user_services': {}, 'other_services': {}},
        }]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        client.get_or_create_tag.assert_called_once_with('Curatarr-Jason')

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_multiple_users_each_processed_independently(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_radarr_client()
        client = _mock_radarr_client()
        mock_create_from.return_value = client
        config = {'radarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}
        all_users_data = [
            {'username': 'a', 'display_name': 'A',
             'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}}},
            {'username': 'b', 'display_name': 'B',
             'movies_categorized': {'acquire': [{'tmdb_id': 2}], 'user_services': {}, 'other_services': {}}},
        ]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        assert client.add_movie.call_count == 2

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_quality_profile_not_found_skips_library(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_radarr_client()
        client = _mock_radarr_client()
        client.get_quality_profile_id.side_effect = None
        client.get_quality_profile_id.return_value = None
        client.get_quality_profiles.return_value = [{'name': 'SD'}]
        mock_create_from.return_value = client
        config = {
            'radarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user', 'quality_profile': 'HD-1080p'}
        }
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
        }]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        client.add_movie.assert_not_called()

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_root_folder_not_found_skips_library(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_radarr_client()
        client = _mock_radarr_client()
        client.get_root_folder_path.side_effect = None
        client.get_root_folder_path.return_value = None
        client.get_root_folders.return_value = [{'path': '/data'}]
        mock_create_from.return_value = client
        config = {
            'radarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user', 'root_folder': '/movies'}
        }
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
        }]

        export_to_radarr(config, all_users_data, 'tmdb-key')

        client.add_movie.assert_not_called()

    @patch('recommenders.external_exports.create_radarr_client_from')
    @patch('recommenders.external_exports.create_radarr_client')
    def test_no_arr_client_for_library_warns_and_skips(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_radarr_client()
        mock_create_from.return_value = None
        config = {'radarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
        }]

        export_to_radarr(config, all_users_data, 'tmdb-key')  # should not raise


class TestExportToSonarrNonCombinedMode:
    """Tests for export_to_sonarr's per-user/mapping-mode loop (the
    non-combined branch) - previously untested beyond the disabled/skip
    paths and the combined-mode per-library routing tests."""

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_mapping_mode_no_plex_users_warns_and_returns(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_sonarr_client()
        config = {'sonarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'mapping', 'plex_users': []}}

        export_to_sonarr(config, [{'username': 'jason'}], 'tmdb-key')

        mock_create_from.assert_not_called()

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_mapping_mode_no_matching_users_warns(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_sonarr_client()
        config = {'sonarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'mapping', 'plex_users': ['jason']}}
        all_users_data = [{'username': 'other', 'display_name': 'Other', 'shows_categorized': {}}]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        mock_create_from.assert_not_called()

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_connect_failure_logs_and_returns(self, mock_create, mock_create_from):
        client = _mock_sonarr_client()
        client.test_connection.side_effect = SonarrAPIError("down")
        mock_create.return_value = client
        config = {'sonarr': {'enabled': True, 'auto_sync': True}}

        export_to_sonarr(config, [], 'tmdb-key')

        mock_create_from.assert_not_called()

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_per_user_mode_add_skip_exists_and_fail_paths(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_sonarr_client()
        client = _mock_sonarr_client()
        # show 1 already exists (skipped), show 2 lookup fails, show 3 add raises, show 4 succeeds
        client.series_exists.side_effect = lambda tvdb_id: tvdb_id == 1

        def lookup(tvdb_id):
            return None if tvdb_id == 2 else {'title': f'Show {tvdb_id}'}
        client.lookup_series.side_effect = lookup

        def add_series(**kwargs):
            if kwargs['tvdb_id'] == 3:
                raise SonarrAPIError("add failed")
            return {}
        client.add_series.side_effect = add_series
        mock_create_from.return_value = client

        config = {
            'sonarr': {
                'enabled': True, 'auto_sync': True, 'user_mode': 'per_user',
                'url': 'http://sonarr:8989', 'api_key': 'key',
            }
        }
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'shows_categorized': {
                'acquire': [{'tvdb_id': 1}, {'tvdb_id': 2}, {'tvdb_id': 3}, {'tvdb_id': 4}],
                'user_services': {}, 'other_services': {},
            },
        }]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        assert client.add_series.call_count == 2
        attempted = [c.kwargs['tvdb_id'] for c in client.add_series.call_args_list]
        assert attempted == [3, 4]

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_per_user_mode_skips_user_with_no_shows(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_sonarr_client()
        client = _mock_sonarr_client()
        mock_create_from.return_value = client
        config = {'sonarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        client.get_or_create_tag.assert_not_called()
        client.add_series.assert_not_called()

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_append_usernames_true_uses_per_user_tag(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_sonarr_client()
        client = _mock_sonarr_client()
        mock_create_from.return_value = client
        config = {
            'sonarr': {
                'enabled': True, 'auto_sync': True, 'user_mode': 'per_user',
                'append_usernames': True, 'tag': 'Curatarr',
            }
        }
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'shows_categorized': {'acquire': [{'tvdb_id': 10}], 'user_services': {}, 'other_services': {}},
        }]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        client.get_or_create_tag.assert_called_once_with('Curatarr-Jason')

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_multiple_users_each_processed_independently(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_sonarr_client()
        client = _mock_sonarr_client()
        mock_create_from.return_value = client
        config = {'sonarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}
        all_users_data = [
            {'username': 'a', 'display_name': 'A',
             'shows_categorized': {'acquire': [{'tvdb_id': 1}], 'user_services': {}, 'other_services': {}}},
            {'username': 'b', 'display_name': 'B',
             'shows_categorized': {'acquire': [{'tvdb_id': 2}], 'user_services': {}, 'other_services': {}}},
        ]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        assert client.add_series.call_count == 2

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_quality_profile_not_found_skips_library(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_sonarr_client()
        client = _mock_sonarr_client()
        client.get_quality_profile_id.side_effect = None
        client.get_quality_profile_id.return_value = None
        client.get_quality_profiles.return_value = [{'name': 'SD'}]
        mock_create_from.return_value = client
        config = {
            'sonarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user', 'quality_profile': 'HD-1080p'}
        }
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'shows_categorized': {'acquire': [{'tvdb_id': 1}], 'user_services': {}, 'other_services': {}},
        }]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        client.add_series.assert_not_called()

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_root_folder_not_found_skips_library(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_sonarr_client()
        client = _mock_sonarr_client()
        client.get_root_folder_path.side_effect = None
        client.get_root_folder_path.return_value = None
        client.get_root_folders.return_value = [{'path': '/data'}]
        mock_create_from.return_value = client
        config = {
            'sonarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user', 'root_folder': '/tv'}
        }
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'shows_categorized': {'acquire': [{'tvdb_id': 1}], 'user_services': {}, 'other_services': {}},
        }]

        export_to_sonarr(config, all_users_data, 'tmdb-key')

        client.add_series.assert_not_called()

    @patch('recommenders.external_exports.create_sonarr_client_from')
    @patch('recommenders.external_exports.create_sonarr_client')
    def test_no_arr_client_for_library_warns_and_skips(self, mock_create, mock_create_from):
        mock_create.return_value = _mock_sonarr_client()
        mock_create_from.return_value = None
        config = {'sonarr': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'shows_categorized': {'acquire': [{'tvdb_id': 1}], 'user_services': {}, 'other_services': {}},
        }]

        export_to_sonarr(config, all_users_data, 'tmdb-key')  # should not raise


def _mock_mdblist_client():
    """MagicMock MDBListClient for export_to_mdblist tests."""
    client = MagicMock()
    client.get_user_info.return_value = {'name': 'jason'}
    client.get_or_create_list.side_effect = lambda name: {'id': abs(hash(name)) % 1000, 'name': name}
    client.clear_list.return_value = True
    client.add_items.return_value = {'added': 2}
    return client


class TestExportToMdblistLogic:
    """Tests for export_to_mdblist's real logic - previously only the
    disabled/skip-path branches were covered."""

    @patch('recommenders.external_exports.create_mdblist_client')
    def test_connection_error_logs_and_returns(self, mock_create):
        client = _mock_mdblist_client()
        client.get_user_info.side_effect = MDBListAPIError("bad token")
        mock_create.return_value = client
        config = {'mdblist': {'enabled': True, 'auto_sync': True}}

        export_to_mdblist(config, [], 'tmdb-key')

        client.get_or_create_list.assert_not_called()

    @patch('recommenders.external_exports.create_mdblist_client')
    def test_mapping_mode_no_plex_users_warns(self, mock_create):
        client = _mock_mdblist_client()
        mock_create.return_value = client
        config = {'mdblist': {'enabled': True, 'auto_sync': True, 'user_mode': 'mapping', 'plex_users': []}}

        export_to_mdblist(config, [{'username': 'jason'}], 'tmdb-key')

        client.get_or_create_list.assert_not_called()

    @patch('recommenders.external_exports.create_mdblist_client')
    def test_mapping_mode_no_matching_users_warns(self, mock_create):
        client = _mock_mdblist_client()
        mock_create.return_value = client
        config = {'mdblist': {'enabled': True, 'auto_sync': True, 'user_mode': 'mapping', 'plex_users': ['jason']}}
        all_users_data = [{'username': 'other', 'display_name': 'Other'}]

        export_to_mdblist(config, all_users_data, 'tmdb-key')

        client.get_or_create_list.assert_not_called()

    @patch('recommenders.external_exports.create_mdblist_client')
    def test_per_user_mode_creates_lists_clears_and_adds(self, mock_create):
        client = _mock_mdblist_client()
        client.get_or_create_list.side_effect = None
        client.get_or_create_list.return_value = {'id': 55, 'name': 'x'}
        client.add_items.return_value = {'added': 3}
        mock_create.return_value = client

        config = {'mdblist': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user', 'list_prefix': 'MyRecs'}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
            'shows_categorized': {'acquire': [{'tmdb_id': 2}], 'user_services': {}, 'other_services': {}},
        }]

        export_to_mdblist(config, all_users_data, 'tmdb-key')

        client.get_or_create_list.assert_any_call('MyRecs - Jason - Movies')
        client.get_or_create_list.assert_any_call('MyRecs - Jason - TV')
        assert client.clear_list.call_count == 2
        client.add_items.assert_any_call(55, movies=[1])
        client.add_items.assert_any_call(55, shows=[2])

    @patch('recommenders.external_exports.create_mdblist_client')
    def test_replace_existing_false_skips_clear(self, mock_create):
        client = _mock_mdblist_client()
        client.get_or_create_list.side_effect = None
        client.get_or_create_list.return_value = {'id': 1, 'name': 'x'}
        mock_create.return_value = client
        config = {
            'mdblist': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user', 'replace_existing': False}
        }
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
            'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_mdblist(config, all_users_data, 'tmdb-key')

        client.clear_list.assert_not_called()

    @patch('recommenders.external_exports.create_mdblist_client')
    def test_combined_mode_merges_and_dedups(self, mock_create):
        client = _mock_mdblist_client()
        client.get_or_create_list.side_effect = None
        client.get_or_create_list.return_value = {'id': 9, 'name': 'x'}
        mock_create.return_value = client
        config = {'mdblist': {'enabled': True, 'auto_sync': True, 'user_mode': 'combined', 'list_prefix': 'Fam'}}
        all_users_data = [
            {'username': 'a', 'display_name': 'A',
             'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
             'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}}},
            {'username': 'b', 'display_name': 'B',
             'movies_categorized': {'acquire': [{'tmdb_id': 1}, {'tmdb_id': 2}], 'user_services': {}, 'other_services': {}},
             'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}}},
        ]

        export_to_mdblist(config, all_users_data, 'tmdb-key')

        client.get_or_create_list.assert_called_once_with('Fam - Movies')
        client.add_items.assert_called_once_with(9, movies=[1, 2])

    @patch('recommenders.external_exports.create_mdblist_client')
    def test_combined_mode_shows_branch_and_error_handling(self, mock_create):
        """Combined mode's shows branch (movies branch covered above) and
        the combined-mode MDBListAPIError catch."""
        client = _mock_mdblist_client()
        client.get_or_create_list.side_effect = None
        client.get_or_create_list.return_value = {'id': 7, 'name': 'x'}
        client.add_items.side_effect = MDBListAPIError("quota exceeded")
        mock_create.return_value = client
        config = {'mdblist': {'enabled': True, 'auto_sync': True, 'user_mode': 'combined', 'list_prefix': 'Fam'}}
        all_users_data = [{
            'username': 'a', 'display_name': 'A',
            'movies_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
            'shows_categorized': {'acquire': [{'tmdb_id': 10}], 'user_services': {}, 'other_services': {}},
        }]

        export_to_mdblist(config, all_users_data, 'tmdb-key')  # should not raise

        client.get_or_create_list.assert_called_once_with('Fam - TV')

    @patch('recommenders.external_exports.create_mdblist_client')
    def test_skips_movies_and_shows_when_empty(self, mock_create):
        client = _mock_mdblist_client()
        mock_create.return_value = client
        config = {'mdblist': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
            'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_mdblist(config, all_users_data, 'tmdb-key')

        client.get_or_create_list.assert_not_called()

    @patch('recommenders.external_exports.create_mdblist_client')
    def test_per_user_error_logged_and_continues(self, mock_create):
        client = _mock_mdblist_client()
        client.get_or_create_list.side_effect = [MDBListAPIError("boom"), {'id': 2, 'name': 'x'}]
        mock_create.return_value = client
        config = {'mdblist': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}
        all_users_data = [
            {'username': 'a', 'display_name': 'A',
             'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
             'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}}},
            {'username': 'b', 'display_name': 'B',
             'movies_categorized': {'acquire': [{'tmdb_id': 2}], 'user_services': {}, 'other_services': {}},
             'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}}},
        ]

        export_to_mdblist(config, all_users_data, 'tmdb-key')  # should not raise

        assert client.get_or_create_list.call_count == 2

    @patch('recommenders.external_exports.create_mdblist_client')
    def test_collect_tmdb_ids_handles_nested_dict_and_flat_categories(self, mock_create):
        """The local collect_tmdb_ids helper walks both dict-of-lists and
        flat-list category shapes and dedups."""
        client = _mock_mdblist_client()
        client.get_or_create_list.side_effect = None
        client.get_or_create_list.return_value = {'id': 1, 'name': 'x'}
        mock_create.return_value = client
        config = {'mdblist': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}
        all_users_data = [{
            'username': 'jason', 'display_name': 'Jason',
            'movies_categorized': {
                'user_services': {'netflix': [{'tmdb_id': 1}, {'tmdb_id': 1}]},
                'other_services': {'hulu': [{'tmdb_id': 2}]},
                'acquire': [{'tmdb_id': 3}],
            },
            'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_mdblist(config, all_users_data, 'tmdb-key')

        client.add_items.assert_called_once_with(1, movies=[1, 2, 3])


def _mock_simkl_client():
    """MagicMock SimklClient for export_to_simkl tests."""
    client = MagicMock()
    client.test_connection.return_value = True
    client.add_to_watchlist.return_value = {'added': {'movies': 1, 'shows': 1}}
    return client


class TestExportToSimklLogic:
    """Tests for export_to_simkl's real logic - previously only the
    disabled/skip-path branches were covered."""

    @patch('recommenders.external_exports.create_simkl_client')
    def test_connection_returns_false_logs_error_and_returns(self, mock_create):
        client = _mock_simkl_client()
        client.test_connection.return_value = False
        mock_create.return_value = client
        config = {'simkl': {'enabled': True, 'export': {'enabled': True, 'auto_sync': True}}}

        export_to_simkl(config, [], 'tmdb-key')

        client.add_to_watchlist.assert_not_called()

    @patch('recommenders.external_exports.create_simkl_client')
    def test_connection_raises_error_logs_and_returns(self, mock_create):
        client = _mock_simkl_client()
        client.test_connection.side_effect = SimklAuthError("expired token")
        mock_create.return_value = client
        config = {'simkl': {'enabled': True, 'export': {'enabled': True, 'auto_sync': True}}}

        export_to_simkl(config, [], 'tmdb-key')

        client.add_to_watchlist.assert_not_called()

    @patch('recommenders.external_exports.create_simkl_client')
    def test_mapping_mode_no_plex_users_warns(self, mock_create):
        client = _mock_simkl_client()
        mock_create.return_value = client
        config = {
            'simkl': {
                'enabled': True,
                'export': {'enabled': True, 'auto_sync': True, 'user_mode': 'mapping', 'plex_users': []},
            }
        }

        export_to_simkl(config, [{'username': 'jason'}], 'tmdb-key')

        client.add_to_watchlist.assert_not_called()

    @patch('recommenders.external_exports.create_simkl_client')
    def test_mapping_mode_no_matching_users_warns(self, mock_create):
        client = _mock_simkl_client()
        mock_create.return_value = client
        config = {
            'simkl': {
                'enabled': True,
                'export': {'enabled': True, 'auto_sync': True, 'user_mode': 'mapping', 'plex_users': ['jason']},
            }
        }
        all_users_data = [{'username': 'other', 'display_name': 'Other'}]

        export_to_simkl(config, all_users_data, 'tmdb-key')

        client.add_to_watchlist.assert_not_called()

    @patch('recommenders.external_exports.create_simkl_client')
    def test_builds_payload_and_adds_to_watchlist(self, mock_create):
        client = _mock_simkl_client()
        client.add_to_watchlist.return_value = {'added': {'movies': 2, 'shows': 1}}
        mock_create.return_value = client
        config = {'simkl': {'enabled': True, 'export': {'enabled': True, 'auto_sync': True, 'user_mode': 'combined'}}}
        all_users_data = [
            {'username': 'a', 'display_name': 'A',
             'movies_categorized': {'acquire': [{'tmdb_id': 1}, {'tmdb_id': 2}], 'user_services': {}, 'other_services': {}},
             'shows_categorized': {'acquire': [{'tmdb_id': 10}], 'user_services': {}, 'other_services': {}}},
        ]

        export_to_simkl(config, all_users_data, 'tmdb-key')

        assert client.add_to_watchlist.call_count == 2
        movies_call = client.add_to_watchlist.call_args_list[0]
        assert movies_call.kwargs['movies'] == [{'ids': {'tmdb': 1}}, {'ids': {'tmdb': 2}}]
        shows_call = client.add_to_watchlist.call_args_list[1]
        assert shows_call.kwargs['shows'] == [{'ids': {'tmdb': 10}}]

    @patch('recommenders.external_exports.create_simkl_client')
    def test_skips_movies_call_when_no_movie_ids(self, mock_create):
        client = _mock_simkl_client()
        client.add_to_watchlist.return_value = {'added': {'shows': 1}}
        mock_create.return_value = client
        config = {'simkl': {'enabled': True, 'export': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}}
        all_users_data = [{
            'username': 'a', 'display_name': 'A',
            'movies_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
            'shows_categorized': {'acquire': [{'tmdb_id': 5}], 'user_services': {}, 'other_services': {}},
        }]

        export_to_simkl(config, all_users_data, 'tmdb-key')

        client.add_to_watchlist.assert_called_once()
        assert 'movies' not in client.add_to_watchlist.call_args.kwargs

    @patch('recommenders.external_exports.create_simkl_client')
    def test_skips_shows_call_when_no_show_ids(self, mock_create):
        client = _mock_simkl_client()
        client.add_to_watchlist.return_value = {'added': {'movies': 1}}
        mock_create.return_value = client
        config = {'simkl': {'enabled': True, 'export': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}}
        all_users_data = [{
            'username': 'a', 'display_name': 'A',
            'movies_categorized': {'acquire': [{'tmdb_id': 5}], 'user_services': {}, 'other_services': {}},
            'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_simkl(config, all_users_data, 'tmdb-key')

        client.add_to_watchlist.assert_called_once()
        assert 'shows' not in client.add_to_watchlist.call_args.kwargs

    @patch('recommenders.external_exports.create_simkl_client')
    def test_collect_tmdb_ids_handles_nested_dict_categories(self, mock_create):
        """Simkl's local collect_tmdb_ids helper walks the dict-of-lists
        category shape (user_services/other_services), not just flat lists."""
        client = _mock_simkl_client()
        client.add_to_watchlist.return_value = {'added': {'movies': 2}}
        mock_create.return_value = client
        config = {'simkl': {'enabled': True, 'export': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}}
        all_users_data = [{
            'username': 'a', 'display_name': 'A',
            'movies_categorized': {
                'user_services': {'netflix': [{'tmdb_id': 1}]},
                'other_services': {'hulu': [{'tmdb_id': 2}]},
                'acquire': [],
            },
            'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_simkl(config, all_users_data, 'tmdb-key')

        client.add_to_watchlist.assert_called_once_with(
            movies=[{'ids': {'tmdb': 1}}, {'ids': {'tmdb': 2}}]
        )

    @patch('recommenders.external_exports.create_simkl_client')
    def test_error_during_add_logs_error_not_raised(self, mock_create):
        client = _mock_simkl_client()
        client.add_to_watchlist.side_effect = SimklAPIError("rate limited")
        mock_create.return_value = client
        config = {'simkl': {'enabled': True, 'export': {'enabled': True, 'auto_sync': True, 'user_mode': 'per_user'}}}
        all_users_data = [{
            'username': 'a', 'display_name': 'A',
            'movies_categorized': {'acquire': [{'tmdb_id': 1}], 'user_services': {}, 'other_services': {}},
            'shows_categorized': {'acquire': [], 'user_services': {}, 'other_services': {}},
        }]

        export_to_simkl(config, all_users_data, 'tmdb-key')  # should not raise
        client.add_to_watchlist.assert_called_once()


class TestSyncWatchHistoryToTrakt:
    """Tests for sync_watch_history_to_trakt - previously entirely
    untested."""

    def test_disabled_returns(self):
        config = {'trakt': {'enabled': False}}

        sync_watch_history_to_trakt(config, 'tmdb-key')

    def test_auto_sync_disabled_returns(self):
        config = {'trakt': {'enabled': True, 'export': {'auto_sync': False}}}

        sync_watch_history_to_trakt(config, 'tmdb-key')

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_no_client_warns_and_returns(self, mock_get_client):
        mock_get_client.return_value = None
        config = {'trakt': {'enabled': True, 'export': {'auto_sync': True}}}

        sync_watch_history_to_trakt(config, 'tmdb-key')

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_mapping_mode_no_plex_users_warns_and_returns(self, mock_get_client):
        client = _mock_trakt_client()
        mock_get_client.return_value = client
        config = {
            'trakt': {'enabled': True, 'export': {'auto_sync': True, 'user_mode': 'mapping', 'plex_users': []}}
        }

        sync_watch_history_to_trakt(config, 'tmdb-key', users=['jason'])

        client.get_watch_history_imdb_ids.assert_not_called()

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_no_load_profile_func_prints_and_returns(self, mock_get_client):
        client = _mock_trakt_client()
        mock_get_client.return_value = client
        config = {'trakt': {'enabled': True, 'export': {'auto_sync': True, 'user_mode': 'per_user'}}}

        sync_watch_history_to_trakt(config, 'tmdb-key', users=['jason'], load_profile_func=None)

        client.add_to_history.assert_not_called()

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_no_matching_users_after_mapping_filter_warns(self, mock_get_client):
        client = _mock_trakt_client()
        mock_get_client.return_value = client
        config = {
            'trakt': {'enabled': True, 'export': {'auto_sync': True, 'user_mode': 'mapping', 'plex_users': ['jason']}}
        }

        sync_watch_history_to_trakt(config, 'tmdb-key', users=['someoneelse'], load_profile_func=Mock())

        client.add_to_history.assert_not_called()

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_no_watch_history_in_cache_prints_and_returns(self, mock_get_client):
        client = _mock_trakt_client()
        mock_get_client.return_value = client
        config = {'trakt': {'enabled': True, 'export': {'auto_sync': True, 'user_mode': 'per_user'}}}
        load_profile_func = Mock(return_value=None)

        sync_watch_history_to_trakt(config, 'tmdb-key', users=['jason'], load_profile_func=load_profile_func)

        client.add_to_history.assert_not_called()

    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_users_default_from_config_when_not_provided(self, mock_get_client):
        client = _mock_trakt_client()
        mock_get_client.return_value = client
        config = {
            'trakt': {'enabled': True, 'export': {'auto_sync': True, 'user_mode': 'per_user'}},
            'users': {'list': 'jason, alex'},
        }
        load_profile_func = Mock(return_value=None)

        sync_watch_history_to_trakt(config, 'tmdb-key', load_profile_func=load_profile_func)

        assert load_profile_func.call_count == 4
        called_users = {c.args[1] for c in load_profile_func.call_args_list}
        assert called_users == {'jason', 'alex'}

    @patch('recommenders.external_exports.get_imdb_id')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_syncs_new_items_and_writes_cache(self, mock_get_client, mock_get_imdb):
        client = _mock_trakt_client()
        client.add_to_history.side_effect = [{'added': {'movies': 1}}, {'added': {'episodes': 1}}]
        mock_get_client.return_value = client
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'

        tmp_cache = tempfile.mkdtemp()
        config = {'trakt': {'enabled': True, 'export': {'auto_sync': True, 'user_mode': 'per_user'}}, 'cache_dir': tmp_cache}

        def load_profile(cfg, username, media_type):
            return {'tmdb_ids': {100}} if media_type == 'movie' else {'tmdb_ids': {200}}

        sync_watch_history_to_trakt(config, 'tmdb-key', users=['jason'], load_profile_func=load_profile)

        client.add_to_history.assert_any_call(movies=['tt100'])
        client.add_to_history.assert_any_call(shows=['tt200'])

        cache_file = os.path.join(tmp_cache, 'trakt_synced_ids.json')
        assert os.path.exists(cache_file)
        with open(cache_file) as f:
            data = json.load(f)
        assert data['movies'] == [100]
        assert data['shows'] == [200]

    @patch('recommenders.external_exports.get_imdb_id')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_already_synced_ids_skipped_using_cache(self, mock_get_client, mock_get_imdb):
        client = _mock_trakt_client()
        mock_get_client.return_value = client

        tmp_cache = tempfile.mkdtemp()
        cache_file = os.path.join(tmp_cache, 'trakt_synced_ids.json')
        with open(cache_file, 'w') as f:
            json.dump({'version': 1, 'movies': [100], 'shows': []}, f)

        config = {'trakt': {'enabled': True, 'export': {'auto_sync': True, 'user_mode': 'per_user'}}, 'cache_dir': tmp_cache}
        load_profile_func = lambda cfg, u, mt: {'tmdb_ids': {100}} if mt == 'movie' else None

        sync_watch_history_to_trakt(config, 'tmdb-key', users=['jason'], load_profile_func=load_profile_func)

        mock_get_imdb.assert_not_called()
        client.add_to_history.assert_not_called()

    @patch('recommenders.external_exports.get_imdb_id')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_outdated_cache_version_ignored_reconverts_all(self, mock_get_client, mock_get_imdb):
        client = _mock_trakt_client()
        client.add_to_history.return_value = {'added': {'movies': 1}}
        mock_get_client.return_value = client
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'

        tmp_cache = tempfile.mkdtemp()
        cache_file = os.path.join(tmp_cache, 'trakt_synced_ids.json')
        with open(cache_file, 'w') as f:
            json.dump({'version': 0, 'movies': [100], 'shows': []}, f)  # stale, must be ignored

        config = {'trakt': {'enabled': True, 'export': {'auto_sync': True, 'user_mode': 'per_user'}}, 'cache_dir': tmp_cache}
        load_profile_func = lambda cfg, u, mt: {'tmdb_ids': {100}} if mt == 'movie' else None

        sync_watch_history_to_trakt(config, 'tmdb-key', users=['jason'], load_profile_func=load_profile_func)

        mock_get_imdb.assert_called_once_with('tmdb-key', 100, 'movie')

    @patch('recommenders.external_exports.get_imdb_id')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_items_already_on_trakt_not_resynced_but_cached(self, mock_get_client, mock_get_imdb):
        client = _mock_trakt_client()
        client.get_watch_history_imdb_ids.side_effect = lambda mt: {'tt100'} if mt == 'movies' else set()
        mock_get_client.return_value = client
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'

        tmp_cache = tempfile.mkdtemp()
        config = {'trakt': {'enabled': True, 'export': {'auto_sync': True, 'user_mode': 'per_user'}}, 'cache_dir': tmp_cache}
        load_profile_func = lambda cfg, u, mt: {'tmdb_ids': {100}} if mt == 'movie' else None

        sync_watch_history_to_trakt(config, 'tmdb-key', users=['jason'], load_profile_func=load_profile_func)

        client.add_to_history.assert_not_called()

    @patch('recommenders.external_exports.get_imdb_id')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_batch_sync_error_logged_not_raised(self, mock_get_client, mock_get_imdb):
        client = _mock_trakt_client()
        client.add_to_history.side_effect = TraktAPIError("rate limited")
        mock_get_client.return_value = client
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'

        tmp_cache = tempfile.mkdtemp()
        config = {'trakt': {'enabled': True, 'export': {'auto_sync': True, 'user_mode': 'per_user'}}, 'cache_dir': tmp_cache}
        load_profile_func = lambda cfg, u, mt: {'tmdb_ids': {100}} if mt == 'movie' else None

        sync_watch_history_to_trakt(config, 'tmdb-key', users=['jason'], load_profile_func=load_profile_func)

    @patch('recommenders.external_exports.get_imdb_id')
    @patch('recommenders.external_exports.get_authenticated_trakt_client')
    def test_corrupt_cache_file_ignored_rebuilds(self, mock_get_client, mock_get_imdb):
        """A cache file that isn't valid JSON is logged and ignored rather
        than crashing the sync."""
        client = _mock_trakt_client()
        client.add_to_history.return_value = {'added': {'movies': 1}}
        mock_get_client.return_value = client
        mock_get_imdb.side_effect = lambda api, tmdb, media: f'tt{tmdb}'

        tmp_cache = tempfile.mkdtemp()
        cache_file = os.path.join(tmp_cache, 'trakt_synced_ids.json')
        with open(cache_file, 'w') as f:
            f.write('{not valid json')

        config = {
            'trakt': {'enabled': True, 'export': {'auto_sync': True, 'user_mode': 'per_user'}},
            'cache_dir': tmp_cache,
        }
        load_profile_func = lambda cfg, u, mt: {'tmdb_ids': {100}} if mt == 'movie' else None

        sync_watch_history_to_trakt(config, 'tmdb-key', users=['jason'], load_profile_func=load_profile_func)

        mock_get_imdb.assert_called_once_with('tmdb-key', 100, 'movie')
        with open(cache_file) as f:
            data = json.load(f)
        assert data['movies'] == [100]
