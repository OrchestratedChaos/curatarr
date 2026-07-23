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

        library_client.add_movie.assert_called_once()
        _, kwargs = library_client.add_movie.call_args
        assert kwargs['root_folder_path'] == '/kids-movies'   # library override
        assert kwargs['quality_profile_id'] == 'qp-HD-1080p'  # falls back to global
        assert kwargs['tag_ids'] == ['tag-Curatarr']            # falls back to global


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
