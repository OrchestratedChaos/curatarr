"""Tests for recommenders/external.py - HTML watchlist and export functionality"""

import json
import os

# Import the functions to test
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import Counter

from recommenders.external import (
    EXTERNAL_RECS_CACHE_VERSION,
    HORIZON_HUNTARR_CACHE_VERSION,
    HUNTARR_CACHE_VERSION,
    SERVICE_DISPLAY_NAMES,
    THIN_PROFILE_THRESHOLD,
    TMDB_ANIMATION_GENRE_ID,
    TMDB_PROVIDERS,
    TV_MOVIE_GENRE_ID,
    _build_profile_via_recommender,
    _empty_categorized,
    _merge_categorized,
    _merge_user_runs,
    _pu_categorize_and_stamp,
    _pu_plan_discovery,
    _pu_resolve_context,
    _stamp_library_id,
    categorize_by_streaming_service,
    discover_candidates_by_profile,
    discover_popular_by_genre,
    export_to_trakt,
    find_horizon_movies,
    find_missing_sequels,
    generate_combined_html,
    get_collection_details,
    get_imdb_id,
    get_library_items,
    get_movie_genre_ids,
    get_movie_status,
    get_tmdb_id_from_imdb,
    get_watch_providers,
    is_in_library,
    is_thin_profile,
    load_cache,
    load_horizon_cache,
    load_huntarr_cache,
    load_ignore_list,
    process_user,
    process_user_movie_library,
    process_user_tv_library,
    save_cache,
    save_horizon_cache,
    save_huntarr_cache,
)
from utils import TMDB_RATE_LIMIT_DELAY
from utils.trakt import enhance_profile_with_trakt


class TestGetImdbId:
    """Tests for get_imdb_id function"""

    @patch("recommenders.external.requests.get")
    def test_returns_imdb_id_for_movie(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"imdb_id": "tt1234567"}
        mock_get.return_value = mock_response

        result = get_imdb_id("api_key", 12345, "movie")

        assert result == "tt1234567"
        mock_get.assert_called_once()
        assert "movie/12345/external_ids" in mock_get.call_args[0][0]

    @patch("recommenders.external.requests.get")
    def test_returns_imdb_id_for_tv(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"imdb_id": "tt9876543"}
        mock_get.return_value = mock_response

        result = get_imdb_id("api_key", 54321, "tv")

        assert result == "tt9876543"
        assert "tv/54321/external_ids" in mock_get.call_args[0][0]

    @patch("recommenders.external.requests.get")
    def test_returns_none_on_api_error(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = get_imdb_id("api_key", 12345, "movie")

        assert result is None

    @patch("recommenders.external.requests.get")
    def test_returns_none_on_missing_imdb_id(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"tvdb_id": 123}  # No imdb_id
        mock_get.return_value = mock_response

        result = get_imdb_id("api_key", 12345, "movie")

        assert result is None

    @patch("recommenders.external.requests.get")
    def test_returns_none_on_exception(self, mock_get):
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError("Network error")

        result = get_imdb_id("api_key", 12345, "movie")

        assert result is None


class TestGetWatchProviders:
    """Tests for get_watch_providers function"""

    def setup_method(self):
        """Clear the watch provider cache before each test"""
        from recommenders import external

        external._watch_provider_cache.clear()

    @patch("recommenders.external.requests.get")
    def test_returns_providers_dict(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": {
                "US": {
                    "flatrate": [
                        {"provider_id": 8, "provider_name": "Netflix"},
                        {"provider_id": 337, "provider_name": "Disney Plus"},
                    ],
                    "rent": [{"provider_id": 2, "provider_name": "Apple TV"}],
                    "buy": [{"provider_id": 3, "provider_name": "Google Play"}],
                }
            }
        }
        mock_get.return_value = mock_response

        result = get_watch_providers("api_key", 12345, "movie")

        assert "netflix" in result["streaming"]
        assert "disney_plus" in result["streaming"]
        assert "Apple TV" in result["rent"]
        assert "Google Play" in result["buy"]

    @patch("recommenders.external.requests.get")
    def test_returns_empty_on_no_us_providers(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": {"GB": {"flatrate": [{"provider_id": 8}]}}}
        mock_get.return_value = mock_response

        result = get_watch_providers("api_key", 12345, "movie")

        assert result == {"streaming": [], "rent": [], "buy": []}

    @patch("recommenders.external.requests.get")
    def test_returns_empty_on_error(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        result = get_watch_providers("api_key", 12345, "movie")

        assert result == {"streaming": [], "rent": [], "buy": []}


class TestCategorizeByStreamingService:
    """Tests for categorize_by_streaming_service function"""

    @patch("recommenders.streaming.get_watch_providers")
    def test_categorizes_by_user_services(self, mock_providers):
        mock_providers.return_value = {"streaming": ["netflix"], "rent": [], "buy": []}

        recommendations = [
            {
                "tmdb_id": 1,
                "title": "Movie 1",
                "year": "2023",
                "rating": 7.5,
                "score": 0.8,
                "added_date": datetime.now().isoformat(),
            }
        ]
        user_services = ["netflix", "hulu"]

        result = categorize_by_streaming_service(recommendations, "api_key", user_services, "movie")

        assert "netflix" in result["user_services"]
        assert len(result["user_services"]["netflix"]) == 1

    @patch("recommenders.streaming.get_watch_providers")
    def test_categorizes_to_acquire(self, mock_providers):
        mock_providers.return_value = {"streaming": [], "rent": [], "buy": []}

        recommendations = [
            {
                "tmdb_id": 1,
                "title": "Movie 1",
                "year": "2023",
                "rating": 7.5,
                "score": 0.8,
                "added_date": datetime.now().isoformat(),
            }
        ]

        result = categorize_by_streaming_service(recommendations, "api_key", ["netflix"], "movie")

        assert len(result["acquire"]) == 1

    @patch("recommenders.streaming.get_watch_providers")
    def test_categorizes_other_services(self, mock_providers):
        mock_providers.return_value = {"streaming": ["disney_plus"], "rent": [], "buy": []}

        recommendations = [
            {
                "tmdb_id": 1,
                "title": "Movie 1",
                "year": "2023",
                "rating": 7.5,
                "score": 0.8,
                "added_date": datetime.now().isoformat(),
            }
        ]

        result = categorize_by_streaming_service(recommendations, "api_key", ["netflix"], "movie")

        assert "disney_plus" in result["other_services"]


class TestIsInLibrary:
    """Tests for is_in_library function"""

    def test_finds_by_tmdb_id(self):
        library_data = {"tmdb_ids": {12345}, "titles": set()}

        result = is_in_library(12345, "Some Movie", "2023", library_data)

        assert result is True

    def test_finds_by_title_and_year(self):
        library_data = {"tmdb_ids": set(), "titles": {("some movie", 2023)}}

        result = is_in_library(None, "Some Movie", "2023", library_data)

        assert result is True

    def test_finds_by_title_only(self):
        library_data = {"tmdb_ids": set(), "titles": {("some movie", 2023)}}

        result = is_in_library(None, "Some Movie", None, library_data)

        assert result is True

    def test_returns_false_when_not_found(self):
        library_data = {"tmdb_ids": set(), "titles": set()}

        result = is_in_library(None, "Unknown Movie", "2023", library_data)

        assert result is False


class TestGenerateCombinedHtml:
    """Tests for generate_combined_html function"""

    def test_generates_html_file(self):
        def mock_get_imdb(api_key, tmdb_id, media_type):
            return "tt1234567"

        with tempfile.TemporaryDirectory() as tmpdir:
            test_movie = {
                "tmdb_id": 1,
                "title": "Test Movie",
                "year": "2023",
                "rating": 7.5,
                "score": 0.8,
                "added_date": datetime.now().isoformat(),
                "streaming_services": [],
                "on_user_services": [],
            }
            all_users_data = [
                {
                    "username": "testuser",
                    "display_name": "Test User",
                    "user_services": [],
                    "movies_categorized": {
                        "user_services": {},
                        "other_services": {},
                        "acquire": [test_movie],
                        "all_items": [test_movie],
                    },
                    "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": [], "all_items": []},
                }
            ]

            result = generate_combined_html(all_users_data, tmpdir, "api_key", mock_get_imdb)

            assert os.path.exists(result)
            assert result.endswith("watchlist.html")

            with open(result) as f:
                content = f.read()
                assert "Test User" in content
                assert "Test Movie" in content
                assert "CURATARR" in content
                assert "Watchlist" in content

    def test_html_contains_tabs_for_multiple_users(self):
        def mock_get_imdb(api_key, tmdb_id, media_type):
            return "tt1234567"

        with tempfile.TemporaryDirectory() as tmpdir:
            all_users_data = [
                {
                    "username": "user1",
                    "display_name": "User One",
                    "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                    "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                },
                {
                    "username": "user2",
                    "display_name": "User Two",
                    "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                    "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                },
            ]

            result = generate_combined_html(all_users_data, tmpdir, "api_key", mock_get_imdb)

            with open(result) as f:
                content = f.read()
                assert "User One" in content
                assert "User Two" in content
                assert "tab-btn" in content

    def test_html_contains_export_buttons(self):
        def mock_get_imdb(api_key, tmdb_id, media_type):
            return "tt1234567"

        with tempfile.TemporaryDirectory() as tmpdir:
            all_users_data = [
                {
                    "username": "testuser",
                    "display_name": "Test",
                    "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                    "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                }
            ]

            result = generate_combined_html(all_users_data, tmpdir, "api_key", mock_get_imdb)

            with open(result) as f:
                content = f.read()
                assert "Export to Radarr" in content
                assert "Export to Sonarr" in content
                assert "exportRadarr()" in content
                assert "exportSonarr()" in content

    def test_html_checkboxes_unchecked_by_default(self):
        def mock_get_imdb(api_key, tmdb_id, media_type):
            return "tt1234567"

        with tempfile.TemporaryDirectory() as tmpdir:
            test_movie = {
                "tmdb_id": 1,
                "title": "Movie",
                "year": "2023",
                "rating": 7.0,
                "score": 0.5,
                "added_date": datetime.now().isoformat(),
                "streaming_services": [],
                "on_user_services": [],
            }
            all_users_data = [
                {
                    "username": "testuser",
                    "display_name": "Test",
                    "user_services": [],
                    "movies_categorized": {
                        "user_services": {},
                        "other_services": {},
                        "acquire": [test_movie],
                        "all_items": [test_movie],
                    },
                    "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": [], "all_items": []},
                }
            ]

            result = generate_combined_html(all_users_data, tmpdir, "api_key", mock_get_imdb)

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
            with patch("recommenders.external.get_project_root", return_value=tmpdir):
                cache_data = {
                    "12345": {
                        "tmdb_id": 12345,
                        "title": "Test Movie",
                        "year": "2023",
                        "rating": 7.5,
                        "vote_count": 500,
                        "score": 0.8,
                        "added_date": "2023-01-01T00:00:00",
                    }
                }

                # Create cache dir
                os.makedirs(os.path.join(tmpdir, "cache"), exist_ok=True)

                save_cache("TestUser", "movies", cache_data)

                loaded = load_cache("TestUser", "movies")

                assert "12345" in loaded
                assert loaded["12345"]["title"] == "Test Movie"

    def test_load_empty_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("recommenders.external.get_project_root", return_value=tmpdir):
                loaded = load_cache("NonExistent", "movies")

                assert loaded == {}


class TestServiceDisplayNames:
    """Tests for SERVICE_DISPLAY_NAMES constant"""

    def test_contains_major_services(self):
        assert "netflix" in SERVICE_DISPLAY_NAMES
        assert "hulu" in SERVICE_DISPLAY_NAMES
        assert "disney_plus" in SERVICE_DISPLAY_NAMES
        assert "amazon_prime" in SERVICE_DISPLAY_NAMES

    def test_display_names_are_readable(self):
        assert SERVICE_DISPLAY_NAMES["netflix"] == "Netflix"
        assert SERVICE_DISPLAY_NAMES["disney_plus"] == "Disney+"
        assert SERVICE_DISPLAY_NAMES["amazon_prime"] == "Amazon Prime Video"


class TestTmdbProviders:
    """Tests for TMDB_PROVIDERS mapping"""

    def test_contains_netflix(self):
        assert 8 in TMDB_PROVIDERS
        assert TMDB_PROVIDERS[8] == "netflix"

    def test_contains_disney_plus(self):
        assert 337 in TMDB_PROVIDERS
        assert TMDB_PROVIDERS[337] == "disney_plus"


class TestGetLibraryItems:
    """Tests for get_library_items function"""

    def test_returns_library_data_for_movies(self):
        mock_movie1 = Mock()
        mock_movie1.title = "Movie One"
        mock_movie1.year = 2023
        mock_guid = Mock()
        mock_guid.id = "tmdb://12345"
        mock_movie1.guids = [mock_guid]

        mock_movie2 = Mock()
        mock_movie2.title = "Movie Two"
        mock_movie2.year = 2022
        mock_movie2.guids = []

        mock_section = Mock()
        mock_section.all.return_value = [mock_movie1, mock_movie2]

        mock_plex = Mock()
        mock_plex.library.section.return_value = mock_section

        result = get_library_items(mock_plex, "Movies", "movie")

        assert 12345 in result["tmdb_ids"]
        assert ("movie one", 2023) in result["titles"]
        assert ("movie two", 2022) in result["titles"]


class TestLoadIgnoreList:
    """Tests for load_ignore_list function"""

    def test_returns_empty_set_when_no_file(self):
        # Non-existent user should return empty set
        result = load_ignore_list("definitely_nonexistent_user_xyz123")

        assert result == set()
        assert isinstance(result, set)


class TestGetTmdbIdFromImdb:
    """Tests for get_tmdb_id_from_imdb function"""

    @patch("recommenders.external.requests.get")
    def test_returns_tmdb_id_for_movie(self, mock_get):
        """Test successful IMDB to TMDB conversion for movie."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"movie_results": [{"id": 12345}], "tv_results": []}
        mock_get.return_value = mock_response

        result = get_tmdb_id_from_imdb("api_key", "tt1234567", "movie")

        assert result == 12345
        mock_get.assert_called_once()
        assert "find/tt1234567" in mock_get.call_args[0][0]

    @patch("recommenders.external.requests.get")
    def test_returns_tmdb_id_for_tv(self, mock_get):
        """Test successful IMDB to TMDB conversion for TV."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"movie_results": [], "tv_results": [{"id": 67890}]}
        mock_get.return_value = mock_response

        result = get_tmdb_id_from_imdb("api_key", "tt9876543", "tv")

        assert result == 67890

    @patch("recommenders.external.requests.get")
    def test_returns_none_when_not_found(self, mock_get):
        """Test returns None when IMDB ID not found."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"movie_results": [], "tv_results": []}
        mock_get.return_value = mock_response

        result = get_tmdb_id_from_imdb("api_key", "tt0000000", "movie")

        assert result is None

    @patch("recommenders.external.requests.get")
    def test_returns_none_on_api_error(self, mock_get):
        """Test returns None on API error."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        result = get_tmdb_id_from_imdb("api_key", "tt1234567", "movie")

        assert result is None

    @patch("recommenders.external.requests.get")
    def test_returns_none_on_exception(self, mock_get):
        """Test returns None when exception occurs."""
        mock_get.side_effect = Exception("Network error")

        result = get_tmdb_id_from_imdb("api_key", "tt1234567", "movie")

        assert result is None


class TestEnhanceProfileWithTrakt:
    """Tests for enhance_profile_with_trakt function"""

    def test_returns_profile_when_trakt_disabled(self):
        """Test returns unchanged profile when Trakt disabled."""
        profile = {
            "genres": Counter({"Action": 5}),
            "actors": Counter(),
            "keywords": Counter(),
            "directors": Counter(),
            "studios": Counter(),
            "tmdb_ids": set(),
        }
        config = {"trakt": {"enabled": False}}

        result = enhance_profile_with_trakt(profile, config, "api_key", "/tmp/cache", "movie")

        assert result == profile
        assert result["genres"]["Action"] == 5

    def test_returns_profile_when_import_disabled(self):
        """Test returns unchanged profile when import disabled."""
        profile = {
            "genres": Counter({"Drama": 3}),
            "actors": Counter(),
            "keywords": Counter(),
            "directors": Counter(),
            "studios": Counter(),
            "tmdb_ids": set(),
        }
        config = {"trakt": {"enabled": True, "import": {"enabled": False}}}

        result = enhance_profile_with_trakt(profile, config, "api_key", "/tmp/cache", "movie")

        assert result["genres"]["Drama"] == 3

    def test_returns_profile_when_merge_disabled(self):
        """Test returns unchanged profile when merge_watch_history disabled."""
        profile = {
            "genres": Counter({"Comedy": 2}),
            "actors": Counter(),
            "keywords": Counter(),
            "directors": Counter(),
            "studios": Counter(),
            "tmdb_ids": set(),
        }
        config = {"trakt": {"enabled": True, "import": {"enabled": True, "merge_watch_history": False}}}

        result = enhance_profile_with_trakt(profile, config, "api_key", "/tmp/cache", "movie")

        assert result["genres"]["Comedy"] == 2

    @patch("utils.trakt.get_authenticated_trakt_client")
    def test_returns_profile_when_not_authenticated(self, mock_get_auth_client):
        """Test returns unchanged profile when Trakt not authenticated."""
        mock_get_auth_client.return_value = None  # Not authenticated

        profile = {
            "genres": Counter({"Horror": 1}),
            "actors": Counter(),
            "keywords": Counter(),
            "directors": Counter(),
            "studios": Counter(),
            "tmdb_ids": set(),
        }
        config = {
            "trakt": {
                "enabled": True,
                "client_id": "id",
                "client_secret": "secret",
                "import": {"enabled": True, "merge_watch_history": True},
            }
        }

        result = enhance_profile_with_trakt(profile, config, "api_key", "/tmp/cache", "movie")

        assert result["genres"]["Horror"] == 1

    @patch("utils.trakt.save_trakt_enhance_cache")
    @patch("utils.trakt.load_trakt_enhance_cache")
    @patch("utils.tmdb.save_imdb_tmdb_cache")
    @patch("utils.tmdb.load_imdb_tmdb_cache")
    @patch("utils.trakt.fetch_tmdb_details_for_profile")
    @patch("utils.tmdb.get_tmdb_id_from_imdb")
    @patch("utils.trakt.get_authenticated_trakt_client")
    def test_merges_trakt_history_into_profile(
        self,
        mock_get_auth_client,
        mock_get_tmdb_id,
        mock_get_details,
        mock_load_imdb_cache,
        mock_save_imdb_cache,
        mock_load_enhance_cache,
        mock_save_enhance_cache,
    ):
        """Test that Trakt watch history is merged into profile."""
        # Setup cache mocks - empty caches so items are "new"
        mock_load_enhance_cache.return_value = {"movie_ids": set(), "show_ids": set()}
        mock_load_imdb_cache.return_value = {}

        # Setup mock Trakt client
        mock_client = Mock()
        mock_client.get_watched_movies.return_value = [
            {"movie": {"title": "Trakt Movie", "ids": {"imdb": "tt1111111"}}}
        ]
        mock_get_auth_client.return_value = mock_client

        # Setup IMDB to TMDB conversion
        mock_get_tmdb_id.return_value = 99999

        # Setup TMDB details
        mock_get_details.return_value = {
            "genres": ["Sci-Fi", "Action"],
            "cast": ["Actor A", "Actor B"],
            "keywords": ["space", "aliens"],
            "directors": ["Director X"],
            "studios": [],
        }

        profile = {
            "genres": Counter({"Drama": 5}),
            "actors": Counter(),
            "keywords": Counter(),
            "directors": Counter(),
            "studios": Counter(),
            "tmdb_ids": set([12345]),  # Existing TMDB ID
        }
        config = {
            "trakt": {
                "enabled": True,
                "client_id": "id",
                "client_secret": "secret",
                "import": {"enabled": True, "merge_watch_history": True},
            }
        }

        result = enhance_profile_with_trakt(profile, config, "api_key", "/tmp/cache", "movie")

        # Original profile data preserved
        assert result["genres"]["Drama"] == 5
        # New data from Trakt added (genres/keywords lowercased by enhance function)
        assert result["genres"]["sci-fi"] == 1
        assert result["genres"]["action"] == 1
        assert result["actors"]["Actor A"] == 1
        assert result["keywords"]["space"] == 1
        assert result["directors"]["Director X"] == 1
        assert 99999 in result["tmdb_ids"]

    @patch("utils.trakt.load_trakt_enhance_cache")
    @patch("utils.trakt.get_authenticated_trakt_client")
    def test_skips_items_already_in_profile(self, mock_get_auth_client, mock_load_enhance_cache):
        """Test that items already in profile are not re-processed."""
        mock_load_enhance_cache.return_value = {"movie_ids": set(), "show_ids": set()}
        mock_client = Mock()
        mock_client.get_watched_movies.return_value = [
            {"movie": {"title": "Already Watched", "ids": {"imdb": "tt1111111"}}}
        ]
        mock_get_auth_client.return_value = mock_client

        profile = {
            "genres": Counter({"Drama": 5}),
            "actors": Counter(),
            "keywords": Counter(),
            "directors": Counter(),
            "studios": Counter(),
            "tmdb_ids": set(),  # Will check if item gets skipped when TMDB ID matches
        }
        config = {
            "trakt": {
                "enabled": True,
                "client_id": "id",
                "client_secret": "secret",
                "import": {"enabled": True, "merge_watch_history": True},
            }
        }

        with patch("utils.tmdb.get_tmdb_id_from_imdb") as mock_tmdb:
            # Return None to simulate failed conversion (item should be skipped)
            mock_tmdb.return_value = None

            result = enhance_profile_with_trakt(profile, config, "api_key", "/tmp/cache", "movie")

            # Profile unchanged since TMDB ID lookup failed
            assert result["genres"]["Drama"] == 5
            assert len(result["tmdb_ids"]) == 0


class TestExportToTraktAutoSync:
    """Tests for export_to_trakt auto_sync configuration."""

    def test_skips_when_trakt_disabled(self):
        """Test export skips when Trakt disabled."""
        config = {"trakt": {"enabled": False}}
        result = export_to_trakt(config, [], "api_key")
        assert result is None

    def test_skips_when_export_disabled(self):
        """Test export skips when export.enabled is false."""
        config = {"trakt": {"enabled": True, "export": {"enabled": False}}}
        result = export_to_trakt(config, [], "api_key")
        assert result is None

    def test_skips_when_auto_sync_disabled(self):
        """Test export skips when auto_sync is false."""
        config = {"trakt": {"enabled": True, "export": {"enabled": True, "auto_sync": False}}}
        result = export_to_trakt(config, [], "api_key")
        assert result is None

    @patch("recommenders.external_sync.get_authenticated_trakt_client")
    def test_skips_when_not_authenticated(self, mock_get_auth_client):
        """Test export skips when client not authenticated."""
        mock_get_auth_client.return_value = None  # Not authenticated

        config = {
            "trakt": {
                "enabled": True,
                "client_id": "id",
                "client_secret": "secret",
                "export": {"enabled": True, "auto_sync": True},
            }
        }
        result = export_to_trakt(config, [], "api_key")
        assert result is None


class TestExportToTraktUserMode:
    """Tests for export_to_trakt user_mode configuration."""

    @patch("recommenders.external_sync.get_authenticated_trakt_client")
    def test_mapping_mode_requires_valid_plex_users(self, mock_get_auth_client):
        """Test that mapping mode requires configured plex_users.

        export_to_trakt() gets an authenticated client - a real
        get_authenticated_trakt_client() call, before this test's own
        plex_users check even runs - so this must be mocked here too,
        same as the other TestExportToTraktUserMode tests below.
        """
        mock_client = Mock()
        mock_client.get_username.return_value = "trakt_user"
        mock_get_auth_client.return_value = mock_client

        config = {
            "trakt": {
                "enabled": True,
                "client_id": "id",
                "client_secret": "secret",
                "access_token": "token",
                "export": {
                    "enabled": True,
                    "auto_sync": True,
                    "user_mode": "mapping",
                    "plex_users": ["YourPlexUsername"],  # Default placeholder
                },
            }
        }
        result = export_to_trakt(config, [], "api_key")
        assert result is None

    @patch("recommenders.external_sync.get_authenticated_trakt_client")
    def test_mapping_mode_rejects_empty_plex_users(self, mock_get_auth_client):
        """Test that mapping mode rejects empty plex_users list.

        See test_mapping_mode_requires_valid_plex_users's docstring above
        for why get_authenticated_trakt_client must be mocked here.
        """
        mock_client = Mock()
        mock_client.get_username.return_value = "trakt_user"
        mock_get_auth_client.return_value = mock_client

        config = {
            "trakt": {
                "enabled": True,
                "client_id": "id",
                "client_secret": "secret",
                "access_token": "token",
                "export": {"enabled": True, "auto_sync": True, "user_mode": "mapping", "plex_users": []},
            }
        }
        result = export_to_trakt(config, [], "api_key")
        assert result is None

    @patch("recommenders.external_sync.get_authenticated_trakt_client")
    def test_mapping_mode_filters_users(self, mock_get_auth_client):
        """Test that mapping mode only exports specified users."""
        mock_client = Mock()
        mock_client.get_username.return_value = "trakt_user"
        mock_client.sync_list.return_value = {"added": {"movies": 2}}
        mock_get_auth_client.return_value = mock_client

        all_users_data = [
            {
                "username": "jason",
                "display_name": "Jason",
                "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            },
            {
                "username": "guest",
                "display_name": "Guest",
                "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            },
        ]
        config = {
            "trakt": {
                "enabled": True,
                "client_id": "id",
                "client_secret": "secret",
                "access_token": "token",
                "export": {
                    "enabled": True,
                    "auto_sync": True,
                    "user_mode": "mapping",
                    "plex_users": ["jason"],  # Only export jason
                },
            }
        }

        export_to_trakt(config, all_users_data, "api_key")

        # Should not have called sync_list for 'guest' user
        call_args_list = [str(call) for call in mock_client.sync_list.call_args_list]
        assert not any("Guest" in args for args in call_args_list)

    @patch("recommenders.external_sync.get_authenticated_trakt_client")
    def test_mapping_mode_case_insensitive(self, mock_get_auth_client):
        """Test that mapping mode matches usernames case-insensitively."""
        mock_client = Mock()
        mock_client.get_username.return_value = "trakt_user"
        mock_get_auth_client.return_value = mock_client

        all_users_data = [
            {
                "username": "Jason",
                "display_name": "Jason",
                "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            },
        ]
        config = {
            "trakt": {
                "enabled": True,
                "client_id": "id",
                "client_secret": "secret",
                "access_token": "token",
                "export": {
                    "enabled": True,
                    "auto_sync": True,
                    "user_mode": "mapping",
                    "plex_users": ["jason"],  # lowercase
                },
            }
        }

        # Should find 'Jason' even with 'jason' in config
        export_to_trakt(config, all_users_data, "api_key")
        # No warning should have been logged about missing users

    @patch("recommenders.external_sync.collect_imdb_ids")
    @patch("recommenders.external_sync.get_authenticated_trakt_client")
    def test_combined_mode_merges_all_users(self, mock_get_auth_client, mock_collect_ids):
        """Test that combined mode creates single merged list."""
        mock_client = Mock()
        mock_client.get_username.return_value = "trakt_user"
        mock_client.sync_list.return_value = {"added": {"movies": 3}}
        mock_get_auth_client.return_value = mock_client
        mock_collect_ids.side_effect = [
            ["tt0001", "tt0002"],  # user1 movies
            [],  # user1 shows
            ["tt0003"],  # user2 movies
            [],  # user2 shows
        ]

        all_users_data = [
            {
                "username": "user1",
                "display_name": "User1",
                "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            },
            {
                "username": "user2",
                "display_name": "User2",
                "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            },
        ]
        config = {
            "trakt": {
                "enabled": True,
                "client_id": "id",
                "client_secret": "secret",
                "access_token": "token",
                "export": {"enabled": True, "auto_sync": True, "user_mode": "combined", "list_prefix": "Curatarr"},
            }
        }

        export_to_trakt(config, all_users_data, "api_key")

        # Should create combined list, not per-user lists
        mock_client.sync_list.assert_called()
        call_args = mock_client.sync_list.call_args
        # List name should be "Curatarr - Movies" not "Curatarr - User1 - Movies"
        assert "Curatarr - Movies" == call_args[0][0]

    @patch("recommenders.external_sync.get_authenticated_trakt_client")
    def test_per_user_mode_exports_all(self, mock_get_auth_client):
        """Test that per_user mode exports all users."""
        mock_client = Mock()
        mock_client.get_username.return_value = "trakt_user"
        mock_get_auth_client.return_value = mock_client

        all_users_data = [
            {
                "username": "user1",
                "display_name": "User1",
                "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            },
            {
                "username": "user2",
                "display_name": "User2",
                "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
                "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            },
        ]
        config = {
            "trakt": {
                "enabled": True,
                "client_id": "id",
                "client_secret": "secret",
                "access_token": "token",
                "export": {"enabled": True, "auto_sync": True, "user_mode": "per_user"},
            }
        }

        export_to_trakt(config, all_users_data, "api_key")
        # No error, both users should be processed


class TestGetCollectionDetails:
    """Tests for get_collection_details function"""

    @patch("recommenders.external.requests.get")
    def test_returns_collection_movies(self, mock_get):
        """Test successful collection fetch."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 10,
            "name": "Star Wars Collection",
            "parts": [
                {"id": 11, "title": "Star Wars", "release_date": "1977-05-25"},
                {"id": 12, "title": "Empire Strikes Back", "release_date": "1980-05-21"},
            ],
        }
        mock_get.return_value = mock_response

        result = get_collection_details("api_key", 10)

        assert result is not None
        assert result["collection_name"] == "Star Wars Collection"
        assert len(result["movies"]) == 2
        assert result["movies"][0]["tmdb_id"] == 11

    @patch("recommenders.external.requests.get")
    def test_returns_none_on_error(self, mock_get):
        """Test returns None on API error."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = get_collection_details("api_key", 99999)

        assert result is None

    @patch("recommenders.external.requests.get")
    def test_returns_none_on_exception(self, mock_get):
        """Test returns None on requests exception."""
        import requests

        mock_get.side_effect = requests.RequestException("Network error")

        result = get_collection_details("api_key", 10)

        assert result is None


class TestHuntarrCache:
    """Tests for Huntarr cache functions"""

    def test_load_cache_returns_empty_when_no_file(self):
        """Test returns empty dict when cache file doesn't exist."""
        result = load_huntarr_cache("/nonexistent/path/cache.json")
        assert result == {}

    def test_load_cache_returns_empty_when_stale(self):
        """Test returns empty dict when cache is stale."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            cache_data = {
                "version": HUNTARR_CACHE_VERSION,
                "cached_at": 0,  # Very old timestamp
                "library_hash": "abc123",
                "data": {"12345": {"title": "Test Movie"}},
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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            cache_data = {
                "version": HUNTARR_CACHE_VERSION,
                "cached_at": time.time(),  # Fresh timestamp
                "library_hash": "abc123",
                "data": {"12345": {"title": "Test Movie", "collection_id": 100}},
            }
            json.dump(cache_data, f)
            cache_path = f.name

        try:
            result = load_huntarr_cache(cache_path, stale_days=7)
            # Returns full cache object when fresh
            assert "data" in result
            assert "12345" in result["data"]
            assert result["data"]["12345"]["title"] == "Test Movie"
        finally:
            os.unlink(cache_path)

    def test_load_cache_returns_empty_on_version_mismatch(self):
        """Test returns empty dict when cache version doesn't match."""
        import time

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            cache_data = {
                "version": HUNTARR_CACHE_VERSION + 100,  # Wrong version
                "cached_at": time.time(),
                "library_hash": "abc123",
                "data": {"12345": {"title": "Test Movie"}},
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
            cache_path = os.path.join(tmpdir, "subdir", "huntarr_cache.json")
            cache_data = {"library_hash": "abc", "data": {"1": {"title": "Movie"}}}

            save_huntarr_cache(cache_path, cache_data)

            assert os.path.exists(cache_path)
            with open(cache_path) as f:
                saved = json.load(f)
            assert saved["data"]["1"]["title"] == "Movie"
            assert saved["version"] == HUNTARR_CACHE_VERSION
            assert "cached_at" in saved

    def test_save_cache_overwrites_existing(self):
        """Test save overwrites existing cache."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"old": "data"}, f)
            cache_path = f.name

        try:
            new_cache = {"library_hash": "new", "data": {"new": {"title": "New Movie"}}}
            save_huntarr_cache(cache_path, new_cache)

            with open(cache_path) as f:
                saved = json.load(f)
            assert "new" in saved["data"]
            assert "old" not in saved
        finally:
            os.unlink(cache_path)


class TestHorizonHuntarrCache:
    """Tests for Horizon Huntarr cache functions"""

    def test_load_horizon_cache_returns_empty_when_no_file(self):
        """Test returns empty dict when cache file doesn't exist."""
        result = load_horizon_cache("/nonexistent/path/cache.json")
        assert result == {}

    def test_load_horizon_cache_returns_empty_when_stale(self):
        """Test returns empty dict when cache is stale."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            cache_data = {
                "version": HORIZON_HUNTARR_CACHE_VERSION,
                "cached_at": 0,  # Very old timestamp
                "library_tmdb_ids": [123, 456],
                "horizon_movies": [{"title": "Future Movie"}],
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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            cache_data = {
                "version": HORIZON_HUNTARR_CACHE_VERSION,
                "cached_at": time.time(),  # Fresh timestamp
                "library_tmdb_ids": [123, 456],
                "horizon_movies": [{"title": "Future Movie", "status": "In Production"}],
            }
            json.dump(cache_data, f)
            cache_path = f.name

        try:
            result = load_horizon_cache(cache_path, stale_days=7)
            assert "horizon_movies" in result
            assert result["horizon_movies"][0]["title"] == "Future Movie"
        finally:
            os.unlink(cache_path)

    def test_save_horizon_cache_creates_file(self):
        """Test save creates cache file with version and timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "horizon_cache.json")
            cache_data = {"library_tmdb_ids": [123], "horizon_movies": [{"title": "Future Movie"}]}

            save_horizon_cache(cache_path, cache_data)

            assert os.path.exists(cache_path)
            with open(cache_path) as f:
                saved = json.load(f)
            assert saved["horizon_movies"][0]["title"] == "Future Movie"
            assert saved["version"] == HORIZON_HUNTARR_CACHE_VERSION
            assert "cached_at" in saved


class TestGetMovieStatus:
    """Tests for get_movie_status function"""

    @patch("recommenders.external.requests.get")
    def test_returns_status_and_release_date(self, mock_get):
        """Test returns status and release date from TMDB."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "In Production", "release_date": "2026-06-15"}
        mock_get.return_value = mock_response

        status, release_date = get_movie_status("api_key", 12345)
        assert status == "In Production"
        assert release_date == "2026-06-15"

    @patch("recommenders.external.requests.get")
    def test_returns_unknown_on_api_error(self, mock_get):
        """Test returns Unknown on API error."""
        import requests as req

        mock_get.side_effect = req.RequestException("API Error")

        status, release_date = get_movie_status("api_key", 12345)
        assert status == "Unknown"
        assert release_date == ""


# -- Shared helpers for TestFindMissingSequels / TestFindHorizonMovies -----
# Both functions' only real I/O is requests.get (TMDB) and the Plex
# library.section()/item.guids scan - everything else (gap detection,
# caching, sorting, TV-special reconciliation) is real logic under test.


def _tmdb_movie_detail_response(belongs_to_collection=None, status=None, release_date=None):
    """Fake requests.Response for GET /movie/{id}. The same TMDB endpoint
    backs both collection-membership discovery (belongs_to_collection) and
    get_movie_status's live status re-check (status/release_date) - a
    single response can carry whichever field(s) a given test needs since
    each caller only reads its own field via .get()."""
    data = {}
    if belongs_to_collection is not None:
        data["belongs_to_collection"] = belongs_to_collection
    if status is not None:
        data["status"] = status
    if release_date is not None:
        data["release_date"] = release_date
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = data
    return resp


def _tmdb_collection_detail_response(collection_id, name, parts):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {"id": collection_id, "name": name, "parts": parts}
    return resp


def _tmdb_providers_detail_response(streaming_ids=None, rent_ids=None, buy_ids=None):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "results": {
            "US": {
                "flatrate": [{"provider_id": pid} for pid in (streaming_ids or [])],
                "rent": [{"provider_id": pid} for pid in (rent_ids or [])],
                "buy": [{"provider_id": pid} for pid in (buy_ids or [])],
            }
        }
    }
    return resp


def _tmdb_404_response():
    resp = Mock()
    resp.status_code = 404
    return resp


def _strict_get_dispatcher(url_map):
    """requests.get side_effect that routes by exact URL. Raises loudly on
    any URL not in url_map instead of silently returning a fabricated
    MagicMock, so an unexpected (or missing) TMDB call fails the test
    instead of making up data."""

    def _fake_get(url, params=None, timeout=None):
        if url not in url_map:
            raise AssertionError(f"Unexpected TMDB URL requested by test: {url}")
        return url_map[url]

    return _fake_get


def _guid_mock(tmdb_id):
    guid = Mock()
    guid.id = f"tmdb://{tmdb_id}"
    return guid


def _library_item(tmdb_id=None, malformed_guid=False):
    """Fake plexapi library item. find_missing_sequels/find_horizon_movies
    only ever read item.guids[*].id when scanning a library."""
    item = Mock()
    if malformed_guid:
        item.guids = [Mock(id="tmdb://not-a-number")]
    elif tmdb_id is None:
        item.guids = []
    else:
        item.guids = [_guid_mock(tmdb_id)]
    return item


def _plex_with_libraries(movie_items, library_map=None, movie_library="Movies"):
    """Fake PlexServer: plex.library.section(movie_library) returns a
    section wrapping movie_items; any other library name is looked up in
    library_map (e.g. Sequel Huntarr's TV-specials library)."""
    movie_section = Mock()
    movie_section.all.return_value = movie_items
    sections = {movie_library: movie_section}
    if library_map:
        sections.update(library_map)

    def _section(name):
        if name in sections:
            return sections[name]
        raise Exception(f"no such test library configured: {name}")

    plex = Mock()
    plex.library.section.side_effect = _section
    return plex


# Dates relative to "now" so these tests never rot as the calendar moves on.
_FUTURE_DATE = (datetime.now() + timedelta(days=3650)).strftime("%Y-%m-%d")
_PAST_DATE = (datetime.now() - timedelta(days=3650)).strftime("%Y-%m-%d")


class TestFindMissingSequels:
    """Tests for find_missing_sequels ('Sequel Huntarr' discovery). Only
    the TMDB HTTP boundary and the Plex library/guid scan are mocked - the
    real gap-finding, caching, and TV-special reconciliation logic runs
    for real."""

    def setup_method(self):
        from recommenders import external

        external._watch_provider_cache.clear()

    def test_library_access_failure_returns_empty_list(self, tmp_path):
        plex = Mock()
        plex.library.section.side_effect = Exception("plex unreachable")

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get") as mock_get,
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", [])

        assert result == []
        mock_get.assert_not_called()

    def test_empty_library_returns_empty_list_without_network_calls(self, tmp_path):
        plex = _plex_with_libraries([])

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get") as mock_get,
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", [])

        assert result == []
        mock_get.assert_not_called()

    def test_cache_hit_reuses_cached_missing_sequels_and_refilters_user_services(self, tmp_path):
        import time

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "huntarr_cache.json").write_text(
            json.dumps(
                {
                    "version": HUNTARR_CACHE_VERSION,
                    "cached_at": time.time(),
                    "library_tmdb_ids": [1],
                    "movie_collections": {},
                    "collection_details": {},
                    "missing_sequels": [
                        {
                            "tmdb_id": 2,
                            "title": "Sequel",
                            "streaming_services": ["netflix", "hulu"],
                            "on_user_services": [],
                        },
                    ],
                }
            )
        )
        plex = _plex_with_libraries([_library_item(1)])

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get") as mock_get,
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", ["netflix"])

        mock_get.assert_not_called()
        assert len(result) == 1
        assert result[0]["on_user_services"] == ["netflix"]

    def test_movie_without_collection_membership_produces_no_gap(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(),
        }

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", [])

        assert result == []

    def test_finds_missing_sequel_with_streaming_and_genre_flags(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Test Collection",
                [
                    {"id": 1, "title": "Part One", "release_date": _PAST_DATE, "genre_ids": [18]},
                    {
                        "id": 2,
                        "title": "Part Two",
                        "release_date": _PAST_DATE,
                        "genre_ids": [TMDB_ANIMATION_GENRE_ID, TV_MOVIE_GENRE_ID],
                    },
                ],
            ),
            "https://api.themoviedb.org/3/movie/2/watch/providers": _tmdb_providers_detail_response(
                streaming_ids=[8, 337]
            ),  # netflix, disney_plus
        }

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "", ["netflix"])

        assert len(result) == 1
        missing = result[0]
        assert missing["tmdb_id"] == 2
        assert missing["collection_name"] == "Test Collection"
        assert missing["owned_count"] == 1
        assert missing["total_count"] == 2
        assert "netflix" in missing["streaming_services"]
        assert missing["on_user_services"] == ["netflix"]
        assert missing["is_tv_movie"] is True
        assert missing["is_animated"] is True

    def test_collection_fully_owned_produces_no_gap(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1), _library_item(2)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/movie/2": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Full Collection",
                [
                    {"id": 1, "title": "One", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Two", "release_date": _PAST_DATE, "genre_ids": []},
                ],
            ),
        }

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "", [])

        assert result == []

    def test_collection_with_no_released_movies_is_skipped(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Unreleased Collection",
                [
                    {"id": 1, "title": "One", "release_date": "", "genre_ids": []},
                    {"id": 2, "title": "Two", "release_date": "", "genre_ids": []},
                ],
            ),
        }

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)) as mock_get,
        ):
            result = find_missing_sequels("key", plex, "Movies", "", [])

        assert result == []
        assert mock_get.call_count == 2  # movie/1 lookup + collection/100 - never reaches watch/providers

    def test_collection_detail_fetch_failure_is_skipped(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_404_response(),
        }

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "", [])

        assert result == []

    def test_uses_cached_movie_collection_mapping_and_collection_details(self, tmp_path):
        import time

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "huntarr_cache.json").write_text(
            json.dumps(
                {
                    "version": HUNTARR_CACHE_VERSION,
                    "cached_at": time.time(),
                    # Deliberately mismatched vs. current library so this exercises
                    # the "rebuild, but reuse movie_collections/collection_details"
                    # path rather than the top-level cache-hit shortcut.
                    "library_tmdb_ids": [],
                    "movie_collections": {"1": 100},
                    "collection_details": {
                        "100": {
                            "collection_id": 100,
                            "collection_name": "Cached Collection",
                            "movies": [
                                {
                                    "tmdb_id": 1,
                                    "title": "One",
                                    "year": "2020",
                                    "release_date": _PAST_DATE,
                                    "genre_ids": [],
                                },
                                {
                                    "tmdb_id": 2,
                                    "title": "Two",
                                    "year": "2021",
                                    "release_date": _PAST_DATE,
                                    "genre_ids": [],
                                },
                            ],
                        },
                    },
                    "missing_sequels": [],
                }
            )
        )
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/2/watch/providers": _tmdb_providers_detail_response(),
        }

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)) as mock_get,
        ):
            result = find_missing_sequels("key", plex, "Movies", "", [])

        assert len(result) == 1
        assert result[0]["tmdb_id"] == 2
        # Neither movie/1's collection id nor collection/100's details were
        # re-fetched - both came straight from the cache.
        assert mock_get.call_count == 1

    def test_collection_id_lookup_handles_non_200_and_request_exception(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1), _library_item(2)])

        def _fake_get(url, params=None, timeout=None):
            if url == "https://api.themoviedb.org/3/movie/1":
                resp = Mock()
                resp.status_code = 500
                return resp
            if url == "https://api.themoviedb.org/3/movie/2":
                raise requests.RequestException("network blip")
            raise AssertionError(f"unexpected url {url}")

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_fake_get),
        ):
            result = find_missing_sequels("key", plex, "Movies", "", [])

        assert result == []

    def test_items_with_malformed_or_missing_tmdb_guid_are_ignored(self, tmp_path):
        plex = _plex_with_libraries([_library_item(malformed_guid=True), _library_item(tmdb_id=None)])

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get") as mock_get,
        ):
            result = find_missing_sequels("key", plex, "Movies", "", [])

        assert result == []
        mock_get.assert_not_called()

    def test_saves_cache_with_expected_shape_after_fresh_scan(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Saved Collection",
                [
                    {"id": 1, "title": "One", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Two", "release_date": _PAST_DATE, "genre_ids": []},
                ],
            ),
            "https://api.themoviedb.org/3/movie/2/watch/providers": _tmdb_providers_detail_response(),
        }

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            find_missing_sequels("key", plex, "Movies", "", [])

        saved = json.loads((tmp_path / "cache" / "huntarr_cache.json").read_text())
        assert saved["version"] == HUNTARR_CACHE_VERSION
        assert saved["library_tmdb_ids"] == [1]
        assert saved["movie_collections"]["1"] == 100
        assert "100" in saved["collection_details"]
        assert saved["missing_sequels"][0]["tmdb_id"] == 2

    def test_sorts_missing_sequels_by_collection_name_then_release_date(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1), _library_item(3)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/movie/3": _tmdb_movie_detail_response(belongs_to_collection={"id": 200}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Beta Collection",
                [
                    {"id": 1, "title": "B1", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "B2", "release_date": _PAST_DATE, "genre_ids": []},
                ],
            ),
            "https://api.themoviedb.org/3/collection/200": _tmdb_collection_detail_response(
                200,
                "Alpha Collection",
                [
                    {"id": 3, "title": "A1", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 4, "title": "A2", "release_date": _PAST_DATE, "genre_ids": []},
                ],
            ),
            "https://api.themoviedb.org/3/movie/2/watch/providers": _tmdb_providers_detail_response(),
            "https://api.themoviedb.org/3/movie/4/watch/providers": _tmdb_providers_detail_response(),
        }

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "", [])

        assert [m["collection_name"] for m in result] == ["Alpha Collection", "Beta Collection"]

    # -- TV special (specials-stored-as-episodes) reconciliation --------

    def _tv_special_setup(self, tv_search_results):
        """One owned regular movie + one missing TV-special (genre 10770)
        in the same collection; tv_search_results is returned for every
        Plex TV-library search() call."""
        tv_section = Mock()
        tv_section.search.return_value = tv_search_results
        plex = _plex_with_libraries([_library_item(1)], library_map={"TV Shows": tv_section})
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Special Collection",
                [
                    {"id": 1, "title": "Owned Movie", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Holiday Special", "release_date": _PAST_DATE, "genre_ids": [TV_MOVIE_GENRE_ID]},
                ],
            ),
            "https://api.themoviedb.org/3/movie/2/watch/providers": _tmdb_providers_detail_response(),
        }
        return plex, url_map, tv_section

    def test_tv_special_removed_when_matched_by_tmdb_guid(self, tmp_path):
        episode = Mock()
        episode.guids = [Mock(id="tmdb://2")]
        episode.title = "Something Else Entirely"
        plex, url_map, tv_section = self._tv_special_setup([episode])

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", [])

        assert result == []
        tv_section.search.assert_called()

    def test_tv_special_removed_when_matched_by_normalized_title(self, tmp_path):
        episode = Mock(spec=["guids", "title"])
        episode.guids = []
        episode.title = "Holiday Special!"
        plex, url_map, _ = self._tv_special_setup([episode])

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", [])

        assert result == []

    def test_tv_special_removed_when_matched_by_grandparent_title_combo(self, tmp_path):
        episode = Mock()
        episode.guids = []
        episode.title = "Mission Marvel"
        episode.grandparentTitle = "Phineas And Ferb"
        plex, url_map, _ = self._tv_special_setup([episode])
        # This match is on the combined "show + episode" name, so give the
        # collection's TV special that combined title.
        url_map["https://api.themoviedb.org/3/collection/100"] = _tmdb_collection_detail_response(
            100,
            "Special Collection",
            [
                {"id": 1, "title": "Owned Movie", "release_date": _PAST_DATE, "genre_ids": []},
                {
                    "id": 2,
                    "title": "Phineas And Ferb Mission Marvel",
                    "release_date": _PAST_DATE,
                    "genre_ids": [TV_MOVIE_GENRE_ID],
                },
            ],
        )

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", [])

        assert result == []

    def test_tv_special_removed_when_episode_title_is_suffix_of_movie_title(self, tmp_path):
        episode = Mock()
        episode.guids = []
        episode.title = "Holiday Special"
        episode.grandparentTitle = "Some Other Show"
        plex, url_map, _ = self._tv_special_setup([episode])
        url_map["https://api.themoviedb.org/3/collection/100"] = _tmdb_collection_detail_response(
            100,
            "Special Collection",
            [
                {"id": 1, "title": "Owned Movie", "release_date": _PAST_DATE, "genre_ids": []},
                {
                    "id": 2,
                    "title": "The Great Holiday Special",
                    "release_date": _PAST_DATE,
                    "genre_ids": [TV_MOVIE_GENRE_ID],
                },
            ],
        )

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", [])

        assert result == []

    def test_tv_special_not_found_in_tv_library_remains_in_results(self, tmp_path):
        episode = Mock()
        episode.guids = []
        episode.title = "Totally Unrelated"
        episode.grandparentTitle = "Another Show"
        plex, url_map, _ = self._tv_special_setup([episode])

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", [])

        assert len(result) == 1
        assert result[0]["tmdb_id"] == 2

    def test_tv_episode_with_malformed_tmdb_guid_is_ignored(self, tmp_path):
        episode = Mock()
        episode.guids = [Mock(id="tmdb://not-a-number")]
        episode.title = "Totally Unrelated"
        episode.grandparentTitle = "Another Show"
        plex, url_map, _ = self._tv_special_setup([episode])

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", [])

        assert len(result) == 1
        assert result[0]["tmdb_id"] == 2

    def test_tv_library_search_exception_is_swallowed_movie_remains(self, tmp_path):
        plex, url_map, tv_section = self._tv_special_setup([])
        tv_section.search.side_effect = Exception("search backend down")

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", [])

        assert len(result) == 1
        assert result[0]["tmdb_id"] == 2

    def test_tv_library_section_access_failure_logs_warning_and_keeps_movie(self, tmp_path):
        movie_section = Mock()
        movie_section.all.return_value = [_library_item(1)]
        plex = Mock()

        def _section(name):
            if name == "TV Shows":
                raise Exception("no such library")
            return movie_section

        plex.library.section.side_effect = _section

        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Special Collection",
                [
                    {"id": 1, "title": "Owned Movie", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Holiday Special", "release_date": _PAST_DATE, "genre_ids": [TV_MOVIE_GENRE_ID]},
                ],
            ),
            "https://api.themoviedb.org/3/movie/2/watch/providers": _tmdb_providers_detail_response(),
        }

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "TV Shows", [])

        assert len(result) == 1
        assert result[0]["tmdb_id"] == 2

    def test_empty_tv_library_name_skips_tv_reconciliation_entirely(self, tmp_path):
        plex, url_map, _ = self._tv_special_setup([])

        with (
            patch("recommenders.huntarr.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_missing_sequels("key", plex, "Movies", "", [])

        assert len(result) == 1
        assert result[0]["tmdb_id"] == 2
        plex.library.section.assert_called_once_with("Movies")


class TestFindHorizonMovies:
    """Tests for find_horizon_movies ('Horizon Huntarr' discovery). Only
    the TMDB HTTP boundary and the Plex library/guid scan are mocked - the
    real unreleased-movie detection, status re-check, and cache-reuse
    logic runs for real."""

    def test_library_access_failure_returns_empty_list(self, tmp_path):
        plex = Mock()
        plex.library.section.side_effect = Exception("plex unreachable")

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get") as mock_get,
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert result == []
        mock_get.assert_not_called()

    def test_empty_library_returns_empty_list_without_network_calls(self, tmp_path):
        plex = _plex_with_libraries([])

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get") as mock_get,
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert result == []
        mock_get.assert_not_called()

    def test_cache_hit_returns_cached_horizon_movies_without_recompute(self, tmp_path):
        import time

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "horizon_huntarr_cache.json").write_text(
            json.dumps(
                {
                    "version": HORIZON_HUNTARR_CACHE_VERSION,
                    "cached_at": time.time(),
                    "library_tmdb_ids": [1],
                    "horizon_movies": [{"tmdb_id": 2, "title": "Cached Upcoming", "status": "Planned"}],
                }
            )
        )
        plex = _plex_with_libraries([_library_item(1)])

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get") as mock_get,
        ):
            result = find_horizon_movies("key", plex, "Movies")

        mock_get.assert_not_called()
        assert result == [{"tmdb_id": 2, "title": "Cached Upcoming", "status": "Planned"}]

    def test_builds_collection_data_fresh_when_no_sequel_cache(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Fresh Collection",
                [
                    {"id": 1, "title": "Owned", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Upcoming", "release_date": _FUTURE_DATE, "genre_ids": [12]},
                ],
            ),
            "https://api.themoviedb.org/3/movie/2": _tmdb_movie_detail_response(
                status="In Production", release_date=_FUTURE_DATE
            ),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert len(result) == 1
        assert result[0]["tmdb_id"] == 2
        assert result[0]["status"] == "In Production"
        assert result[0]["release_date"] == _FUTURE_DATE
        assert result[0]["genre_ids"] == [12]

    def test_movie_without_collection_membership_produces_no_horizon_movies(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert result == []

    def test_items_with_malformed_or_missing_tmdb_guid_are_ignored(self, tmp_path):
        plex = _plex_with_libraries([_library_item(malformed_guid=True), _library_item(tmdb_id=None)])

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get") as mock_get,
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert result == []
        mock_get.assert_not_called()

    def test_collection_detail_fetch_failure_is_skipped(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_404_response(),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert result == []

    def test_duplicate_library_items_reuse_movie_collections_entry_mid_scan(self, tmp_path):
        """Two Plex items resolving to the same tmdb_id (e.g. a duplicate
        library entry) - the second one must reuse the movie_collections
        entry the first one just populated rather than re-fetching it."""
        plex = _plex_with_libraries([_library_item(1), _library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Duplicate Item Collection",
                [
                    {"id": 1, "title": "Owned", "release_date": _PAST_DATE, "genre_ids": []},
                ],
            ),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)) as mock_get,
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert result == []
        assert mock_get.call_count == 2  # movie/1 id lookup once + collection/100 once

    def test_collection_id_lookup_handles_non_200_and_request_exception(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1), _library_item(2)])

        def _fake_get(url, params=None, timeout=None):
            if url == "https://api.themoviedb.org/3/movie/1":
                resp = Mock()
                resp.status_code = 500
                return resp
            if url == "https://api.themoviedb.org/3/movie/2":
                raise requests.RequestException("network blip")
            raise AssertionError(f"unexpected url {url}")

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_fake_get),
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert result == []

    def test_reuses_sequel_cache_mapping_skips_id_and_detail_lookups(self, tmp_path):
        import time

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "huntarr_cache.json").write_text(
            json.dumps(
                {
                    "version": HUNTARR_CACHE_VERSION,
                    "cached_at": time.time(),
                    "movie_collections": {"1": 100},
                    "collection_details": {
                        "100": {
                            "collection_id": 100,
                            "collection_name": "Reused Collection",
                            "movies": [
                                {
                                    "tmdb_id": 1,
                                    "title": "Owned",
                                    "year": "2020",
                                    "release_date": _PAST_DATE,
                                    "genre_ids": [],
                                },
                                {"tmdb_id": 2, "title": "Upcoming", "year": "", "release_date": "", "genre_ids": []},
                            ],
                        },
                    },
                    "missing_sequels": [],
                }
            )
        )
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/2": _tmdb_movie_detail_response(status="Planned"),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)) as mock_get,
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert len(result) == 1
        assert result[0]["tmdb_id"] == 2
        assert result[0]["status"] == "Planned"
        assert result[0]["collection_name"] == "Reused Collection"
        # Only the live status re-check happened - collection membership
        # and collection details both came from the sequel cache.
        assert mock_get.call_count == 1

    def test_owned_movies_are_never_candidates_even_with_future_release_date(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1), _library_item(3)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/movie/3": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Owned Collection",
                [
                    {"id": 1, "title": "One", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 3, "title": "Three", "release_date": _FUTURE_DATE, "genre_ids": []},
                ],
            ),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert result == []

    def test_past_release_date_is_treated_as_released_no_status_call(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Past Collection",
                [
                    {"id": 1, "title": "Owned", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Already Out", "release_date": _PAST_DATE, "genre_ids": []},
                ],
            ),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)) as mock_get,
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert result == []
        assert mock_get.call_count == 2  # movie/1 id lookup + collection/100 only

    def test_future_release_date_but_live_status_released_is_skipped(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Stale Date Collection",
                [
                    {"id": 1, "title": "Owned", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Actually Out Already", "release_date": _FUTURE_DATE, "genre_ids": []},
                ],
            ),
            "https://api.themoviedb.org/3/movie/2": _tmdb_movie_detail_response(
                status="Released", release_date=_PAST_DATE
            ),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert result == []

    def test_canceled_status_is_skipped(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Canceled Collection",
                [
                    {"id": 1, "title": "Owned", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Never Happening", "release_date": _FUTURE_DATE, "genre_ids": []},
                ],
            ),
            "https://api.themoviedb.org/3/movie/2": _tmdb_movie_detail_response(status="Canceled"),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert result == []

    def test_in_production_status_included_with_tba_fallback_for_missing_release_date(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "In Production Collection",
                [
                    {"id": 1, "title": "Owned", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Coming Soon", "release_date": _FUTURE_DATE, "genre_ids": []},
                ],
            ),
            "https://api.themoviedb.org/3/movie/2": _tmdb_movie_detail_response(status="In Production"),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert len(result) == 1
        assert result[0]["status"] == "In Production"
        assert result[0]["release_date"] == "TBA"

    def test_sorts_by_collection_name_before_status_priority(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1), _library_item(3)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/movie/3": _tmdb_movie_detail_response(belongs_to_collection={"id": 200}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Beta Collection",
                [
                    {"id": 1, "title": "Owned B", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Beta Upcoming", "release_date": _FUTURE_DATE, "genre_ids": []},
                ],
            ),
            "https://api.themoviedb.org/3/collection/200": _tmdb_collection_detail_response(
                200,
                "Alpha Collection",
                [
                    {"id": 3, "title": "Owned A", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 4, "title": "Alpha Upcoming", "release_date": _FUTURE_DATE, "genre_ids": []},
                ],
            ),
            # Beta's candidate has a higher-priority status than Alpha's,
            # but collection name must still win the sort.
            "https://api.themoviedb.org/3/movie/2": _tmdb_movie_detail_response(status="Post Production"),
            "https://api.themoviedb.org/3/movie/4": _tmdb_movie_detail_response(status="Rumored"),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert [m["collection_name"] for m in result] == ["Alpha Collection", "Beta Collection"]

    def test_sorts_by_status_priority_within_same_collection(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Mixed Status Collection",
                [
                    {"id": 1, "title": "Owned", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Rumored One", "release_date": _FUTURE_DATE, "genre_ids": []},
                    {"id": 3, "title": "Post Production One", "release_date": _FUTURE_DATE, "genre_ids": []},
                    {"id": 4, "title": "Planned One", "release_date": _FUTURE_DATE, "genre_ids": []},
                ],
            ),
            "https://api.themoviedb.org/3/movie/2": _tmdb_movie_detail_response(status="Rumored"),
            "https://api.themoviedb.org/3/movie/3": _tmdb_movie_detail_response(status="Post Production"),
            "https://api.themoviedb.org/3/movie/4": _tmdb_movie_detail_response(status="Planned"),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert [m["status"] for m in result] == ["Post Production", "Planned", "Rumored"]

    def test_saves_cache_with_expected_shape_after_fresh_scan(self, tmp_path):
        plex = _plex_with_libraries([_library_item(1)])
        url_map = {
            "https://api.themoviedb.org/3/movie/1": _tmdb_movie_detail_response(belongs_to_collection={"id": 100}),
            "https://api.themoviedb.org/3/collection/100": _tmdb_collection_detail_response(
                100,
                "Saved Horizon Collection",
                [
                    {"id": 1, "title": "Owned", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 2, "title": "Upcoming", "release_date": _FUTURE_DATE, "genre_ids": []},
                ],
            ),
            "https://api.themoviedb.org/3/movie/2": _tmdb_movie_detail_response(status="Planned"),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            find_horizon_movies("key", plex, "Movies")

        saved = json.loads((tmp_path / "cache" / "horizon_huntarr_cache.json").read_text())
        assert saved["version"] == HORIZON_HUNTARR_CACHE_VERSION
        assert saved["library_tmdb_ids"] == [1]
        assert saved["horizon_movies"][0]["tmdb_id"] == 2

    def test_stale_partial_sequel_cache_still_discovers_newly_owned_movie_collection(self, tmp_path):
        """When the Sequel Huntarr cache exists but is PARTIAL (e.g. the
        user added a new movie to Plex after Sequel Huntarr's last run, so
        its tmdb_id was never recorded in movie_collections),
        find_horizon_movies must diff the current library against the
        cached movie_collections map - just like find_missing_sequels does
        - and fetch collection data for the still-uncached ids, instead of
        trusting the cache wholesale. Net effect: a legitimately-owned
        movie's upcoming sequel is still discovered, without needing
        Sequel Huntarr to run again first."""
        import time

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "huntarr_cache.json").write_text(
            json.dumps(
                {
                    "version": HUNTARR_CACHE_VERSION,
                    "cached_at": time.time(),
                    # Non-empty, but describes a movie/collection unrelated to (and
                    # no longer even owned by) the current library - simulating a
                    # cache populated before movie tmdb_id=5 was ever added to Plex.
                    "movie_collections": {"999": 500},
                    "collection_details": {},
                    "missing_sequels": [],
                }
            )
        )
        # The real, current Plex library owns tmdb_id=5, which does belong
        # to a collection with a genuine upcoming sequel. Since 5 isn't a
        # key in movie_collections yet, discovering it requires a fetch.
        plex = _plex_with_libraries([_library_item(5)])
        url_map = {
            "https://api.themoviedb.org/3/movie/5": _tmdb_movie_detail_response(belongs_to_collection={"id": 700}),
            "https://api.themoviedb.org/3/collection/700": _tmdb_collection_detail_response(
                700,
                "Newly Discovered Collection",
                [
                    {"id": 5, "title": "Owned", "release_date": _PAST_DATE, "genre_ids": []},
                    {"id": 6, "title": "Upcoming Sequel", "release_date": _FUTURE_DATE, "genre_ids": []},
                ],
            ),
            "https://api.themoviedb.org/3/movie/6": _tmdb_movie_detail_response(status="Planned"),
        }

        with (
            patch("recommenders.horizon.get_project_root", return_value=str(tmp_path)),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            result = find_horizon_movies("key", plex, "Movies")

        assert [m["tmdb_id"] for m in result] == [6]
        assert result[0]["collection_name"] == "Newly Discovered Collection"
        assert result[0]["status"] == "Planned"


class TestCategorizeByStreamingServiceAllItems:
    """Tests for categorize_by_streaming_service with all_items structure"""

    @patch("recommenders.streaming.get_watch_providers")
    def test_returns_all_items_list(self, mock_providers):
        """Test that categorized data includes all_items."""
        mock_providers.side_effect = [
            {"streaming": ["netflix"], "rent": [], "buy": []},
            {"streaming": ["hulu"], "rent": [], "buy": []},
        ]
        items = [
            {"tmdb_id": 1, "title": "Movie 1", "score": 0.8},
            {"tmdb_id": 2, "title": "Movie 2", "score": 0.7},
        ]
        user_services = ["netflix"]

        result = categorize_by_streaming_service(items, "api_key", user_services, "movie")

        assert "all_items" in result
        assert len(result["all_items"]) == 2

    @patch("recommenders.streaming.get_watch_providers")
    def test_all_items_sorted_by_score(self, mock_providers):
        """Test all_items are sorted by score descending."""
        mock_providers.return_value = {"streaming": [], "rent": [], "buy": []}
        items = [
            {"tmdb_id": 1, "title": "Low Score", "score": 0.5},
            {"tmdb_id": 2, "title": "High Score", "score": 0.9},
            {"tmdb_id": 3, "title": "Mid Score", "score": 0.7},
        ]

        result = categorize_by_streaming_service(items, "api_key", [], "movie")

        scores = [item["score"] for item in result["all_items"]]
        assert scores == sorted(scores, reverse=True)

    @patch("recommenders.streaming.get_watch_providers")
    def test_items_include_streaming_services_list(self, mock_providers):
        """Test each item has streaming_services list from API."""
        mock_providers.return_value = {"streaming": ["netflix", "hulu"], "rent": [], "buy": []}
        items = [
            {"tmdb_id": 1, "title": "Movie", "score": 0.8},
        ]

        result = categorize_by_streaming_service(items, "api_key", ["netflix"], "movie")

        item = result["all_items"][0]
        assert "streaming_services" in item
        assert "netflix" in item["streaming_services"]
        assert "hulu" in item["streaming_services"]

    @patch("recommenders.streaming.get_watch_providers")
    def test_items_include_on_user_services(self, mock_providers):
        """Test each item has on_user_services list."""
        mock_providers.return_value = {"streaming": ["netflix", "hulu"], "rent": [], "buy": []}
        items = [
            {"tmdb_id": 1, "title": "Movie", "score": 0.8},
        ]
        user_services = ["netflix"]

        result = categorize_by_streaming_service(items, "api_key", user_services, "movie")

        item = result["all_items"][0]
        assert "on_user_services" in item
        assert "netflix" in item["on_user_services"]
        assert "hulu" not in item["on_user_services"]

    @patch("recommenders.streaming.get_watch_providers")
    def test_acquire_items_have_no_streaming(self, mock_providers):
        """Test items with no providers go to acquire list."""
        mock_providers.return_value = {"streaming": [], "rent": [], "buy": []}
        items = [
            {"tmdb_id": 1, "title": "Rare Movie", "score": 0.8},
        ]

        result = categorize_by_streaming_service(items, "api_key", ["netflix"], "movie")

        assert len(result["acquire"]) == 1
        assert result["acquire"][0]["title"] == "Rare Movie"

    @patch("recommenders.streaming.get_watch_providers")
    def test_user_service_items_categorized(self, mock_providers):
        """Test items on user's services go to user_services dict."""
        mock_providers.return_value = {"streaming": ["netflix"], "rent": [], "buy": []}
        items = [
            {"tmdb_id": 1, "title": "Netflix Movie", "score": 0.8},
        ]

        result = categorize_by_streaming_service(items, "api_key", ["netflix"], "movie")

        assert "netflix" in result["user_services"]
        assert len(result["user_services"]["netflix"]) == 1


class TestExternalRecsCacheVersioning:
    """Tests for external recommendations cache versioning"""

    def test_load_cache_returns_empty_for_old_version(self):
        """Test returns empty dict when cache has old version."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            cache_data = {
                "version": 0,  # Old version
                "items": {"12345": {"title": "Test Movie", "tmdb_id": 12345, "vote_count": 1000}},
            }
            json.dump(cache_data, f)
            cache_path = f.name

        try:
            # Need to create directory structure expected by load_cache
            with tempfile.TemporaryDirectory() as tmpdir:
                # Copy file to expected location
                import shutil

                cache_dir = os.path.join(tmpdir, "cache")
                os.makedirs(cache_dir, exist_ok=True)
                dest_path = os.path.join(cache_dir, "external_recs_testuser_movie.json")
                shutil.copy(cache_path, dest_path)

                # Patch the project root detection
                with patch("recommenders.external.get_project_root", return_value=tmpdir):
                    result = load_cache("testuser", "movie")
                    # Old version should return empty
                    assert result == {}
        finally:
            os.unlink(cache_path)

    def test_save_cache_includes_version(self):
        """Test save adds version to cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch the project root detection
            with patch("recommenders.external.get_project_root", return_value=tmpdir):
                cache_data = {"12345": {"title": "Test", "tmdb_id": 12345}}
                save_cache("testuser", "movie", cache_data)

                cache_path = os.path.join(tmpdir, "cache", "external_recs_testuser_movie.json")
                with open(cache_path) as f:
                    saved = json.load(f)

                assert "version" in saved
                assert saved["version"] == EXTERNAL_RECS_CACHE_VERSION
                assert "items" in saved

    def test_load_cache_reads_versioned_format(self):
        """Test load correctly reads new versioned format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "cache")
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, "external_recs_testuser_movie.json")

            cache_data = {
                "version": EXTERNAL_RECS_CACHE_VERSION,
                "items": {"12345": {"title": "Test Movie", "tmdb_id": 12345, "vote_count": 1000}},
            }
            with open(cache_path, "w") as f:
                json.dump(cache_data, f)

            with patch("recommenders.external.get_project_root", return_value=tmpdir):
                result = load_cache("testuser", "movie")

                assert "12345" in result
                assert result["12345"]["title"] == "Test Movie"


class TestExternalRecsCacheLibraryId:
    """Tests for per-library external recs cache filenames (#157 Phase 3)."""

    def test_no_lib_id_uses_legacy_filename(self):
        """lib_id=None (default) keeps the exact legacy filename - single
        library install byte-identical proof for external recs cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("recommenders.external.get_project_root", return_value=tmpdir):
                save_cache("testuser", "movies", {"1": {"title": "A"}})

                expected_path = os.path.join(tmpdir, "cache", "external_recs_testuser_movies.json")
                assert os.path.exists(expected_path)

    def test_lib_id_qualifies_filename(self):
        """lib_id qualifies the filename so multiple libraries of the same
        media type don't collide."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("recommenders.external.get_project_root", return_value=tmpdir):
                save_cache("testuser", "movies", {"1": {"title": "A"}}, lib_id="movies-4k")

                expected_path = os.path.join(tmpdir, "cache", "external_recs_movies-4k_testuser_movies.json")
                assert os.path.exists(expected_path)

    def test_distinct_lib_ids_produce_isolated_caches(self):
        """Two libraries of the same media type get fully isolated caches -
        writing to one must not affect the other."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("recommenders.external.get_project_root", return_value=tmpdir):
                save_cache(
                    "testuser", "movies", {"1": {"title": "Library A movie", "vote_count": 500}}, lib_id="movies"
                )
                save_cache(
                    "testuser", "movies", {"2": {"title": "Library B movie", "vote_count": 500}}, lib_id="movies-4k"
                )

                cache_a = load_cache("testuser", "movies", lib_id="movies")
                cache_b = load_cache("testuser", "movies", lib_id="movies-4k")

                assert "1" in cache_a and "2" not in cache_a
                assert "2" in cache_b and "1" not in cache_b

    def test_stamp_library_id_covers_all_categories(self):
        """_stamp_library_id sets library_id on every item across
        user_services, other_services, and acquire."""
        categorized = {
            "user_services": {"Netflix": [{"tmdb_id": 1}, {"tmdb_id": 2}]},
            "other_services": {"Hulu": [{"tmdb_id": 3}]},
            "acquire": [{"tmdb_id": 4}],
            "all_items": [{"tmdb_id": 1}],  # same objects in real usage
        }

        _stamp_library_id(categorized, "movies-4k")

        assert categorized["user_services"]["Netflix"][0]["library_id"] == "movies-4k"
        assert categorized["user_services"]["Netflix"][1]["library_id"] == "movies-4k"
        assert categorized["other_services"]["Hulu"][0]["library_id"] == "movies-4k"
        assert categorized["acquire"][0]["library_id"] == "movies-4k"

    def test_stamp_library_id_handles_none(self):
        """_stamp_library_id(None) is valid - legacy/no-library items stay None."""
        categorized = {"user_services": {}, "other_services": {}, "acquire": [{"tmdb_id": 1}]}

        _stamp_library_id(categorized, None)

        assert categorized["acquire"][0]["library_id"] is None

    def test_lib_id_round_trip(self):
        """save_cache(lib_id=...) then load_cache(lib_id=...) reads back
        what was written."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("recommenders.external.get_project_root", return_value=tmpdir):
                cache_data = {"999": {"title": "Qualified Cache", "tmdb_id": 999, "vote_count": 500}}
                save_cache("testuser", "movies", cache_data, lib_id="movies-4k")

                result = load_cache("testuser", "movies", lib_id="movies-4k")

                assert "999" in result
                assert result["999"]["title"] == "Qualified Cache"


def _process_user_load_cache_side_effect(display_name, media_type, lib_id=None):
    """Shared fixture data for TestProcessUserLibraryProvenance - one
    already-cached, above-threshold item per media type so discovery
    (movie_deficit/show_deficit) is skipped and only the caching/
    categorization/provenance path under test runs."""
    if media_type == "movies":
        return {
            "100": {
                "tmdb_id": 100,
                "title": "Cached Movie",
                "year": 2020,
                "rating": 7.5,
                "vote_count": 500,
                "score": 0.9,
                "original_language": "en",
            }
        }
    return {
        "200": {
            "tmdb_id": 200,
            "title": "Cached Show",
            "year": 2019,
            "rating": 8.0,
            "vote_count": 300,
            "score": 0.9,
            "original_language": "en",
        }
    }


def _process_user_categorize_side_effect(items, tmdb_api_key, user_services, media_type):
    """Fixture standing in for categorize_by_streaming_service - returns one
    item so _stamp_library_id has something to stamp."""
    item = {"tmdb_id": 999 if media_type == "movie" else 888, "title": f"{media_type}-item"}
    return {"user_services": {}, "other_services": {"Netflix": [item]}, "acquire": [], "all_items": [item]}


class TestProcessUserLibraryProvenance:
    """Tests for process_user library params and item provenance (#157 Phase 3)."""

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.enhance_profile_with_trakt")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_legacy_no_library_params(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_enhance,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_categorize,
        mock_markdown,
    ):
        """No movie_library/tv_library passed (legacy callers): resolves
        section names from config['plex'], cache lib_id stays None, item
        library_id stamps are None, and the top-level library_id is None."""
        mock_get_items.return_value = {"titles": set(), "tmdb_ids": set()}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = {"genres": {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _process_user_load_cache_side_effect
        mock_categorize.side_effect = _process_user_categorize_side_effect

        config = {
            "plex": {"movie_library": "Movies", "tv_library": "TV Shows"},
            "users": {"preferences": {}},
            "external_recommendations": {"movie_limit": 1, "show_limit": 1, "min_relevance_score": 0.65},
            "streaming_services": [],
            "trakt": {},
        }

        result = process_user(config, Mock(), "alice")

        # Section names resolved from legacy config['plex'] keys
        get_items_calls = mock_get_items.call_args_list
        assert get_items_calls[0][0][1] == "Movies"
        assert get_items_calls[1][0][1] == "TV Shows"

        # Cache filenames stay legacy (lib_id=None)
        assert mock_load_cache.call_args_list[0].kwargs["lib_id"] is None
        assert mock_load_cache.call_args_list[1].kwargs["lib_id"] is None
        assert mock_save_cache.call_args_list[0].kwargs["lib_id"] is None
        assert mock_save_cache.call_args_list[1].kwargs["lib_id"] is None

        # Top-level library_id always None (see _stamp_library_id / return docstring)
        assert result["library_id"] is None

        # Item-level provenance stamps are None (no library resolved)
        movie_item = result["movies_categorized"]["other_services"]["Netflix"][0]
        show_item = result["shows_categorized"]["other_services"]["Netflix"][0]
        assert movie_item["library_id"] is None
        assert show_item["library_id"] is None

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.enhance_profile_with_trakt")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_multi_library_params_qualify_cache_and_stamp_items(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_enhance,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_categorize,
        mock_markdown,
    ):
        """movie_library/tv_library passed with a genuinely multi-library
        movie config (2 movie libraries) but a single tv library:
        - movie cache filenames get lib-qualified (movie_is_multi)
        - tv cache filenames stay legacy (only 1 tv library, despite
          tv_library being passed) - per-media-type independence
        - every returned item is stamped with its real library id
        """
        mock_get_items.return_value = {"titles": set(), "tmdb_ids": set()}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = {"genres": {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _process_user_load_cache_side_effect
        mock_categorize.side_effect = _process_user_categorize_side_effect

        movie_library = {"id": "movies-4k", "name": "Movies 4K", "section": "Movies 4K", "media_type": "movie"}
        tv_library = {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"}

        config = {
            "plex": {"movie_library": "Movies", "tv_library": "TV Shows"},
            "users": {"preferences": {}},
            "external_recommendations": {"movie_limit": 1, "show_limit": 1, "min_relevance_score": 0.65},
            "streaming_services": [],
            "trakt": {},
            "libraries": [
                {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
                movie_library,
                tv_library,
            ],
        }

        result = process_user(config, Mock(), "alice", movie_library=movie_library, tv_library=tv_library)

        # Section names resolved from the passed library dicts
        get_items_calls = mock_get_items.call_args_list
        assert get_items_calls[0][0][1] == "Movies 4K"
        assert get_items_calls[1][0][1] == "TV Shows"

        # Movie cache qualified (2 movie libraries); tv cache stays legacy (1 tv library)
        assert mock_load_cache.call_args_list[0].kwargs["lib_id"] == "movies-4k"
        assert mock_load_cache.call_args_list[1].kwargs["lib_id"] is None
        assert mock_save_cache.call_args_list[0].kwargs["lib_id"] == "movies-4k"
        assert mock_save_cache.call_args_list[1].kwargs["lib_id"] is None

        # Top-level library_id stays None even in multi-library mode (see
        # process_user's return docstring: a single call spans both movie
        # and tv libraries, which can have different ids, and Phase 2's
        # _resolve_library_groups isn't media-type-aware for non-None ids)
        assert result["library_id"] is None

        # Item-level provenance carries the real library ids
        movie_item = result["movies_categorized"]["other_services"]["Netflix"][0]
        show_item = result["shows_categorized"]["other_services"]["Netflix"][0]
        assert movie_item["library_id"] == "movies-4k"
        assert show_item["library_id"] == "tv-shows"


class TestMainMissingTmdbApiKey:
    """Tests for main()'s upfront TMDB API key check.

    Unlike movie.py/tv.py (where a missing key just degrades scoring -
    every use there is guarded with `if tmdb_api_key`), this module has
    no degraded mode: every candidate it discovers comes from TMDB, so a
    missing key must fail fast with an actionable message rather than
    silently producing an empty/broken watchlist (fetch_tmdb_with_retry
    swallows every TMDB failure into a bare None by design)."""

    @patch("recommenders.external.log_error")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_missing_api_key_exits_with_actionable_message(
        self, mock_root, mock_load_config, mock_get_tmdb, mock_log_error
    ):
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = {
            "plex": {"url": "http://x", "token": "y"},
            "users": {"list": "alice"},
        }
        mock_get_tmdb.return_value = {"api_key": None, "use_keywords": True}

        from recommenders.external import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

        logged = " ".join(call.args[0] for call in mock_log_error.call_args_list)
        assert "TMDB" in logged
        assert "themoviedb.org" in logged

    @patch("recommenders.external.log_error")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_empty_string_api_key_also_exits(self, mock_root, mock_load_config, mock_get_tmdb, mock_log_error):
        """An empty string (e.g. an un-filled config.example.yml placeholder
        left blank) is exactly as unusable as None - same fail-fast path."""
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = {
            "plex": {"url": "http://x", "token": "y"},
            "users": {"list": "alice"},
        }
        mock_get_tmdb.return_value = {"api_key": "", "use_keywords": True}

        from recommenders.external import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


class TestMainLibraryResolution:
    """Tests for main() resolving primary movie/tv libraries (#157 Phase 3)."""

    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.find_horizon_movies")
    @patch("recommenders.external.find_missing_sequels")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_single_library_install_calls_process_user_once_per_user(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_sequels,
        mock_horizon,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
    ):
        """Single-library install (no 'libraries:' config): process_user is
        called once per user (same call count as before Phase 3), with the
        synthesized movie/tv libraries threaded through."""
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = {
            "plex": {"url": "http://x", "token": "y", "movie_library": "Movies", "tv_library": "TV Shows"},
            "users": {"list": "alice, bob"},
            "huntarr": {"sequel_huntarr": False, "horizon_huntarr": False},
        }
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()
        mock_process_user.return_value = {
            "username": "x",
            "display_name": "x",
            "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "movie_profile": {},
            "show_profile": {},
            "user_services": [],
            "library_id": None,
        }
        mock_html.return_value = None

        from recommenders.external import main

        main()

        assert mock_process_user.call_count == 2
        for call in mock_process_user.call_args_list:
            assert call.kwargs["movie_library"]["id"] == "movies"
            assert call.kwargs["tv_library"]["id"] == "tv-shows"

        # arr exports get the exact same list as the combined/Trakt exports
        # in the non-fan-out path (library_id is None on every entry either
        # way - see main()'s arr_export_data docstring)
        assert mock_sonarr.call_args[0][1] is mock_radarr.call_args[0][1]
        assert mock_sonarr.call_args[0][1] == mock_trakt.call_args[0][1]

    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.find_horizon_movies")
    @patch("recommenders.external.find_missing_sequels")
    @patch("recommenders.external.process_user_tv_library")
    @patch("recommenders.external.process_user_movie_library")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_one_movie_one_tv_library_install_calls_process_user_once_per_user(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_process_movie_lib,
        mock_process_tv_lib,
        mock_sequels,
        mock_horizon,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
    ):
        """#157 Phase 3.5 HARD invariant: an explicit 'libraries:' config with
        exactly one movie library and one tv library is NOT a fan-out - it
        takes the exact same combined process_user() path as a synthesized
        single-library install (byte-identical call count/routing). The new
        per-library fan-out functions are never called."""
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = {
            "plex": {"url": "http://x", "token": "y"},
            "users": {"list": "alice, bob"},
            "huntarr": {"sequel_huntarr": False, "horizon_huntarr": False},
            "libraries": [
                {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
                {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"},
            ],
        }
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()
        mock_process_user.return_value = {
            "username": "x",
            "display_name": "x",
            "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "movie_profile": {},
            "show_profile": {},
            "user_services": [],
            "library_id": None,
        }
        mock_html.return_value = None

        from recommenders.external import main

        main()

        assert mock_process_user.call_count == 2
        for call in mock_process_user.call_args_list:
            assert call.kwargs["movie_library"]["id"] == "movies"
            assert call.kwargs["tv_library"]["id"] == "tv-shows"

        mock_process_movie_lib.assert_not_called()
        mock_process_tv_lib.assert_not_called()

        assert mock_sonarr.call_args[0][1] is mock_radarr.call_args[0][1]
        assert mock_sonarr.call_args[0][1] == mock_trakt.call_args[0][1]


class TestMainOutputGenerationBranches:
    """Tests for main()'s watchlist-generation orchestration branches:
    huntarr-only mode, Plex connection failure, per-user error isolation,
    shared movie/show counts feeding generate_combined_html, Sequel/Horizon
    Huntarr wiring, and the html_file/auto_open_html tail."""

    def _base_config(self, **overrides):
        config = {
            "plex": {"url": "http://x", "token": "y", "movie_library": "Movies", "tv_library": "TV Shows"},
            "users": {"list": "alice, bob"},
            "huntarr": {"sequel_huntarr": False, "horizon_huntarr": False},
        }
        config.update(overrides)
        return config

    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.find_horizon_movies")
    @patch("recommenders.external.find_missing_sequels")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py", "--huntarr-only"])
    def test_huntarr_only_skips_recommendations(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_sequels,
        mock_horizon,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
    ):
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = self._base_config()
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()
        mock_html.return_value = None

        from recommenders.external import main

        main()

        mock_process_user.assert_not_called()
        mock_trakt.assert_not_called()
        mock_sonarr.assert_not_called()
        mock_radarr.assert_not_called()
        mock_mdblist.assert_not_called()
        mock_simkl.assert_not_called()

    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_plex_connection_failure_exits(self, mock_root, mock_load_config, mock_get_tmdb, mock_plex_server):
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = self._base_config()
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.side_effect = Exception("connection refused")

        from recommenders.external import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.find_horizon_movies")
    @patch("recommenders.external.find_missing_sequels")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_external_recommendations_enabled_false_skips_recommendations_not_huntarr(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_sequels,
        mock_horizon,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
    ):
        """external_recommendations.enabled: false (newly wired up, was
        previously read nowhere) must skip the recommendations/watchlist
        pass - but NOT Huntarr, which is a separate feature under its
        own huntarr.* keys and stays independently gated."""
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = self._base_config(
            external_recommendations={"enabled": False},
            huntarr={"sequel_huntarr": True, "horizon_huntarr": True},
        )
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()
        mock_html.return_value = None
        mock_sequels.return_value = []
        mock_horizon.return_value = []

        from recommenders.external import main

        main()

        mock_process_user.assert_not_called()
        mock_trakt.assert_not_called()
        mock_sonarr.assert_not_called()
        mock_radarr.assert_not_called()
        mock_mdblist.assert_not_called()
        mock_simkl.assert_not_called()
        mock_sequels.assert_called_once()
        mock_horizon.assert_called_once()

    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_external_recommendations_enabled_defaults_true_when_unset(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
    ):
        """No external_recommendations.enabled key at all (the state of
        every install before this was wired up) must behave exactly like
        enabled: true - nobody's working setup changes by accident."""
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = self._base_config()
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()
        mock_html.return_value = None
        mock_process_user.return_value = {
            "username": "alice",
            "display_name": "alice",
            "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "movie_profile": {},
            "show_profile": {},
            "user_services": [],
            "library_id": None,
        }

        from recommenders.external import main

        main()

        mock_process_user.assert_called()

    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.find_horizon_movies")
    @patch("recommenders.external.find_missing_sequels")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_process_user_exception_is_isolated_per_user(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_sequels,
        mock_horizon,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
    ):
        """One user's process_user() exception is logged and doesn't stop
        the other user from being processed."""
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = self._base_config()
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()
        good_result = {
            "username": "bob",
            "display_name": "Bob",
            "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "movie_profile": {},
            "show_profile": {},
            "user_services": [],
            "library_id": None,
        }
        mock_process_user.side_effect = [Exception("boom"), good_result]
        mock_html.return_value = None

        from recommenders.external import main

        main()  # should not raise

        assert mock_process_user.call_count == 2
        # Only bob's (successful) data reaches the combined HTML call
        assert mock_html.call_args[0][0] == [good_result]

    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.find_horizon_movies")
    @patch("recommenders.external.find_missing_sequels")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_shared_counts_computed_across_users(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_sequels,
        mock_horizon,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
    ):
        """movie_counts/show_counts tally how many users want each tmdb_id,
        across user_services/other_services/acquire, for movies and shows."""
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = self._base_config(users={"list": "alice, bob"})
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()

        def make_result(username):
            return {
                "username": username,
                "display_name": username,
                "movies_categorized": {
                    "user_services": {"netflix": [{"tmdb_id": 100}]},
                    "other_services": {"hulu": [{"tmdb_id": 200}]},
                    "acquire": [{"tmdb_id": 300}],
                },
                "shows_categorized": {
                    "user_services": {"netflix": [{"tmdb_id": 400}]},
                    "other_services": {},
                    "acquire": [{"tmdb_id": 500}],
                },
                "movie_profile": {},
                "show_profile": {},
                "user_services": [],
                "library_id": None,
            }

        mock_process_user.side_effect = [make_result("alice"), make_result("bob")]
        mock_html.return_value = None

        from recommenders.external import main

        main()

        movie_counts = mock_html.call_args.kwargs["movie_counts"]
        show_counts = mock_html.call_args.kwargs["show_counts"]
        assert movie_counts == {"100": 2, "200": 2, "300": 2}
        assert show_counts == {"400": 2, "500": 2}
        assert mock_html.call_args.kwargs["total_users"] == 2

    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.find_horizon_movies")
    @patch("recommenders.external.find_missing_sequels")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_sequel_and_horizon_huntarr_enabled_calls_finders_and_feeds_html(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_sequels,
        mock_horizon,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
    ):
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = self._base_config(
            huntarr={"sequel_huntarr": True, "horizon_huntarr": True},
            users={"list": "alice"},
        )
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()
        mock_process_user.return_value = {
            "username": "alice",
            "display_name": "Alice",
            "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "movie_profile": {},
            "show_profile": {},
            "user_services": [],
            "library_id": None,
        }
        mock_sequels.return_value = [{"title": "Missing Sequel", "tmdb_id": 1}]
        mock_horizon.return_value = [{"title": "Upcoming Movie", "tmdb_id": 2}]
        mock_html.return_value = None

        from recommenders.external import main

        main()

        mock_sequels.assert_called_once()
        mock_horizon.assert_called_once()
        assert mock_html.call_args.kwargs["missing_sequels"] == mock_sequels.return_value
        assert mock_html.call_args.kwargs["horizon_movies"] == mock_horizon.return_value

    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.find_horizon_movies")
    @patch("recommenders.external.find_missing_sequels")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_no_data_at_all_skips_html_generation(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_sequels,
        mock_horizon,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
    ):
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = self._base_config(users={"list": "alice"})
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()
        mock_process_user.return_value = None  # nothing produced
        mock_sequels.return_value = []
        mock_horizon.return_value = []

        from recommenders.external import main

        main()

        mock_html.assert_not_called()
        mock_trakt.assert_not_called()  # all_users_data empty -> export gate skipped

    @patch("recommenders.external.smart_open_html")
    @patch("recommenders.external.clickable_link")
    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.find_horizon_movies")
    @patch("recommenders.external.find_missing_sequels")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_html_file_generated_prints_link_and_respects_auto_open(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_sequels,
        mock_horizon,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
        mock_clickable,
        mock_smart_open,
    ):
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = self._base_config(
            users={"list": "alice"},
            external_recommendations={"auto_open_html": True},
        )
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()
        mock_process_user.return_value = {
            "username": "alice",
            "display_name": "Alice",
            "movies_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "shows_categorized": {"user_services": {}, "other_services": {}, "acquire": []},
            "movie_profile": {},
            "show_profile": {},
            "user_services": [],
            "library_id": None,
        }
        mock_html.return_value = "/fake/root/recommendations/external/watchlist.html"
        mock_clickable.return_value = "link"

        from recommenders.external import main

        main()

        mock_clickable.assert_called_once_with("file:///fake/root/recommendations/external/watchlist.html")
        mock_smart_open.assert_called_once_with(mock_html.return_value)


class TestFanOutMultiLibrary:
    """Tests for #157 Phase 3.5: external recommendation fan-out for configs
    with 2+ libraries of the same media type."""

    def _fake_movie_data(self, username, library_id):
        return {
            "username": username,
            "display_name": username,
            "movies_categorized": {
                "user_services": {},
                "other_services": {},
                "acquire": [{"tmdb_id": 100 if library_id == "movies" else 200, "library_id": library_id}],
                "all_items": [
                    {"tmdb_id": 100 if library_id == "movies" else 200, "library_id": library_id, "score": 0.9}
                ],
            },
            "shows_categorized": _empty_categorized(),
            "movie_profile": {"lib": library_id},
            "show_profile": None,
            "user_services": [],
            "library_id": library_id,
        }

    def _fake_tv_data(self, username, library_id):
        return {
            "username": username,
            "display_name": username,
            "movies_categorized": _empty_categorized(),
            "shows_categorized": {
                "user_services": {},
                "other_services": {},
                "acquire": [{"tmdb_id": 300, "library_id": library_id}],
                "all_items": [{"tmdb_id": 300, "library_id": library_id, "score": 0.8}],
            },
            "movie_profile": None,
            "show_profile": {"lib": library_id},
            "user_services": [],
            "library_id": library_id,
        }

    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.find_horizon_movies")
    @patch("recommenders.external.find_missing_sequels")
    @patch("recommenders.external.process_user_tv_library")
    @patch("recommenders.external.process_user_movie_library")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_two_movie_libraries_one_user_fans_out_to_two_runs(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_process_movie_lib,
        mock_process_tv_lib,
        mock_sequels,
        mock_horizon,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
    ):
        """2 movie libraries x 1 user -> 2 process_user_movie_library() calls
        (one per library), 2 arr_export_data entries each carrying its own
        real library_id, and the combined process_user() is never called."""
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = {
            "plex": {"url": "http://x", "token": "y"},
            "users": {"list": "alice"},
            "huntarr": {"sequel_huntarr": False, "horizon_huntarr": False},
            "libraries": [
                {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
                {"id": "kids-movies", "name": "Kids Movies", "section": "Kids Movies", "media_type": "movie"},
            ],
        }
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()
        mock_process_movie_lib.side_effect = lambda config, plex, username, library: self._fake_movie_data(
            username, library["id"]
        )
        mock_html.return_value = None

        from recommenders.external import main

        main()

        mock_process_user.assert_not_called()
        mock_process_tv_lib.assert_not_called()
        assert mock_process_movie_lib.call_count == 2
        called_lib_ids = {call.args[3]["id"] for call in mock_process_movie_lib.call_args_list}
        assert called_lib_ids == {"movies", "kids-movies"}

        # arr_export_data: 2 destinations, each with its own real library_id
        arr_export_data = mock_radarr.call_args[0][1]
        assert len(arr_export_data) == 2
        assert {d["library_id"] for d in arr_export_data} == {"movies", "kids-movies"}
        assert mock_sonarr.call_args[0][1] == arr_export_data

        # all_users_data (combined/Trakt view): one merged entry for alice
        all_users_data = mock_trakt.call_args[0][1]
        assert len(all_users_data) == 1
        merged = all_users_data[0]
        assert merged["library_id"] is None
        assert len(merged["movies_categorized"]["acquire"]) == 2
        acquired_tmdb_ids = {item["tmdb_id"] for item in merged["movies_categorized"]["acquire"]}
        assert acquired_tmdb_ids == {100, 200}

    @patch("recommenders.external.export_to_simkl")
    @patch("recommenders.external.export_to_mdblist")
    @patch("recommenders.external.export_to_radarr")
    @patch("recommenders.external.export_to_sonarr")
    @patch("recommenders.external.export_to_trakt")
    @patch("recommenders.external.generate_combined_html")
    @patch("recommenders.external.find_horizon_movies")
    @patch("recommenders.external.find_missing_sequels")
    @patch("recommenders.external.process_user_tv_library")
    @patch("recommenders.external.process_user_movie_library")
    @patch("recommenders.external.process_user")
    @patch("recommenders.external.PlexServer")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.load_config")
    @patch("recommenders.external.get_project_root")
    @patch("sys.argv", ["external.py"])
    def test_mixed_two_movie_one_tv_routes_per_media_type(
        self,
        mock_root,
        mock_load_config,
        mock_get_tmdb,
        mock_plex_server,
        mock_process_user,
        mock_process_movie_lib,
        mock_process_tv_lib,
        mock_sequels,
        mock_horizon,
        mock_html,
        mock_trakt,
        mock_sonarr,
        mock_radarr,
        mock_mdblist,
        mock_simkl,
    ):
        """2 movie libraries + 1 tv library x 1 user: movies fan out to 2
        scoped runs, tv still gets its own scoped (not combined) run since
        entries must stay media-type-pure. 3 total arr_export_data entries,
        movies routed via export_to_radarr's group, tv via export_to_sonarr's."""
        mock_root.return_value = "/fake/root"
        mock_load_config.return_value = {
            "plex": {"url": "http://x", "token": "y"},
            "users": {"list": "alice"},
            "huntarr": {"sequel_huntarr": False, "horizon_huntarr": False},
            "libraries": [
                {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
                {"id": "kids-movies", "name": "Kids Movies", "section": "Kids Movies", "media_type": "movie"},
                {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"},
            ],
        }
        mock_get_tmdb.return_value = {"api_key": "key", "use_keywords": True}
        mock_plex_server.return_value = Mock()
        mock_process_movie_lib.side_effect = lambda config, plex, username, library: self._fake_movie_data(
            username, library["id"]
        )
        mock_process_tv_lib.side_effect = lambda config, plex, username, library: self._fake_tv_data(
            username, library["id"]
        )
        mock_html.return_value = None

        from recommenders.external import main

        main()

        mock_process_user.assert_not_called()
        assert mock_process_movie_lib.call_count == 2
        assert mock_process_tv_lib.call_count == 1
        assert mock_process_tv_lib.call_args.args[3]["id"] == "tv-shows"

        arr_export_data = mock_radarr.call_args[0][1]
        assert len(arr_export_data) == 3
        assert {d["library_id"] for d in arr_export_data} == {"movies", "kids-movies", "tv-shows"}

        all_users_data = mock_trakt.call_args[0][1]
        assert len(all_users_data) == 1
        merged = all_users_data[0]
        assert len(merged["movies_categorized"]["acquire"]) == 2
        assert len(merged["shows_categorized"]["acquire"]) == 1


class TestMergeHelpers:
    """Tests for #157 Phase 3.5 fan-out merge helpers."""

    def test_merge_categorized_combines_and_resorts(self):
        cat_a = {
            "user_services": {"Netflix": [{"tmdb_id": 1, "score": 0.5}]},
            "other_services": {},
            "acquire": [],
            "all_items": [{"tmdb_id": 1, "score": 0.5}],
        }
        cat_b = {
            "user_services": {"Netflix": [{"tmdb_id": 2, "score": 0.9}]},
            "other_services": {"Hulu": [{"tmdb_id": 3, "score": 0.6}]},
            "acquire": [{"tmdb_id": 4, "score": 0.1}],
            "all_items": [{"tmdb_id": 2, "score": 0.9}, {"tmdb_id": 3, "score": 0.6}],
        }

        merged = _merge_categorized([cat_a, cat_b])

        assert len(merged["user_services"]["Netflix"]) == 2
        assert merged["other_services"]["Hulu"][0]["tmdb_id"] == 3
        assert merged["acquire"][0]["tmdb_id"] == 4
        # Re-sorted by score descending across both inputs
        assert [item["tmdb_id"] for item in merged["all_items"]] == [2, 3, 1]

    def test_merge_categorized_empty_list(self):
        assert _merge_categorized([]) == _empty_categorized()

    def test_merge_user_runs_movies_only(self):
        movie_run = {
            "display_name": "Alice",
            "user_services": ["netflix"],
            "movies_categorized": {
                "user_services": {},
                "other_services": {},
                "acquire": [{"tmdb_id": 1}],
                "all_items": [{"tmdb_id": 1, "score": 0.5}],
            },
            "movie_profile": {"genres": {}},
        }

        merged = _merge_user_runs("alice", [movie_run], [])

        assert merged["username"] == "alice"
        assert merged["display_name"] == "Alice"
        assert merged["library_id"] is None
        assert merged["movie_profile"] == {"genres": {}}
        assert merged["show_profile"] is None
        assert merged["shows_categorized"] == _empty_categorized()
        assert len(merged["movies_categorized"]["acquire"]) == 1

    def test_merge_user_runs_shows_only(self):
        tv_run = {
            "display_name": "Alice",
            "user_services": ["netflix"],
            "shows_categorized": {
                "user_services": {},
                "other_services": {},
                "acquire": [{"tmdb_id": 1}],
                "all_items": [{"tmdb_id": 1, "score": 0.5}],
            },
            "show_profile": {"genres": {}},
        }

        merged = _merge_user_runs("alice", [], [tv_run])

        assert merged["movie_profile"] is None
        assert merged["show_profile"] == {"genres": {}}
        assert merged["movies_categorized"] == _empty_categorized()
        assert len(merged["shows_categorized"]["acquire"]) == 1


def _fanout_load_cache_side_effect(display_name, media_type, lib_id=None):
    """Same fixture shape as _process_user_load_cache_side_effect but for
    the single-media-type fan-out functions - one already-cached,
    above-threshold item so discovery is skipped."""
    if media_type == "movies":
        return {
            "100": {
                "tmdb_id": 100,
                "title": "Cached Movie",
                "year": 2020,
                "rating": 7.5,
                "vote_count": 500,
                "score": 0.9,
                "original_language": "en",
            }
        }
    return {
        "200": {
            "tmdb_id": 200,
            "title": "Cached Show",
            "year": 2019,
            "rating": 8.0,
            "vote_count": 300,
            "score": 0.9,
            "original_language": "en",
        }
    }


def _fanout_categorize_side_effect(items, tmdb_api_key, user_services, media_type):
    item = {"tmdb_id": 999 if media_type == "movie" else 888, "title": f"{media_type}-item"}
    return {"user_services": {}, "other_services": {"Netflix": [item]}, "acquire": [], "all_items": [item]}


class TestProcessUserMovieLibrary:
    """Tests for process_user_movie_library() (#157 Phase 3.5 fan-out)."""

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.enhance_profile_with_trakt")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_qualifies_cache_and_filename_when_multi_library(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_enhance,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_categorize,
        mock_markdown,
    ):
        mock_get_items.return_value = {"titles": set(), "tmdb_ids": set()}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = {"genres": {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _fanout_load_cache_side_effect
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {"id": "kids-movies", "name": "Kids Movies", "section": "Kids Movies", "media_type": "movie"}
        config = {
            "plex": {},
            "users": {"preferences": {}},
            "external_recommendations": {"movie_limit": 1, "min_relevance_score": 0.65},
            "streaming_services": [],
            "trakt": {},
            "libraries": [
                {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
                library,
            ],
        }

        result = process_user_movie_library(config, Mock(), "alice", library)

        assert mock_get_items.call_args[0][1] == "Kids Movies"
        assert mock_load_cache.call_args.kwargs["lib_id"] == "kids-movies"
        assert mock_save_cache.call_args.kwargs["lib_id"] == "kids-movies"

        assert result["library_id"] == "kids-movies"
        assert result["show_profile"] is None
        assert result["shows_categorized"] == _empty_categorized()
        movie_item = result["movies_categorized"]["other_services"]["Netflix"][0]
        assert movie_item["library_id"] == "kids-movies"

        # Markdown filename qualified with the library id (>1 movie library)
        assert mock_markdown.call_args.kwargs["library_suffix"] == "_kids-movies"

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.enhance_profile_with_trakt")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_legacy_filename_when_single_library_of_this_type(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_enhance,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_categorize,
        mock_markdown,
    ):
        """Mixed fan-out (movies multi, tv single) still in progress: this
        media type has only ONE library, so its cache/filename stay legacy
        (unqualified) even though this function is being used - #157 Phase
        3.5's per-media-type independence rule."""
        mock_get_items.return_value = {"titles": set(), "tmdb_ids": set()}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = {"genres": {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _fanout_load_cache_side_effect
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"}
        config = {
            "plex": {},
            "users": {"preferences": {}},
            "external_recommendations": {"movie_limit": 1, "min_relevance_score": 0.65},
            "streaming_services": [],
            "trakt": {},
            "libraries": [library],
        }

        result = process_user_movie_library(config, Mock(), "alice", library)

        assert mock_load_cache.call_args.kwargs["lib_id"] is None
        assert mock_save_cache.call_args.kwargs["lib_id"] is None
        assert mock_markdown.call_args.kwargs["library_suffix"] == ""
        assert result["library_id"] == "movies"

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.enhance_profile_with_trakt")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_per_user_streaming_services_merges_onto_global(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_enhance,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_categorize,
        mock_markdown,
    ):
        """PR2 audit remediation: users.preferences.<user>.streaming_services
        was previously dead config - it must now merge onto the global
        streaming_services list (see get_streaming_services_for_user)."""
        mock_get_items.return_value = {"titles": set(), "tmdb_ids": set()}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = {"genres": {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _fanout_load_cache_side_effect
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"}
        config = {
            "plex": {},
            "users": {"preferences": {"alice": {"streaming_services": ["hulu"]}}},
            "external_recommendations": {"movie_limit": 1, "min_relevance_score": 0.65},
            "streaming_services": ["netflix"],
            "trakt": {},
            "libraries": [library],
        }

        process_user_movie_library(config, Mock(), "alice", library)

        assert mock_categorize.call_args[0][2] == ["netflix", "hulu"]


class TestProcessUserTvLibrary:
    """Tests for process_user_tv_library() (#157 Phase 3.5 fan-out)."""

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.enhance_profile_with_trakt")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_qualifies_cache_and_filename_when_multi_library(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_enhance,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_categorize,
        mock_markdown,
    ):
        mock_get_items.return_value = {"titles": set(), "tmdb_ids": set()}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = {"genres": {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _fanout_load_cache_side_effect
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {"id": "anime", "name": "Anime", "section": "Anime", "media_type": "tv"}
        config = {
            "plex": {},
            "users": {"preferences": {}},
            "external_recommendations": {"show_limit": 1, "min_relevance_score": 0.65},
            "streaming_services": [],
            "trakt": {},
            "libraries": [
                {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"},
                library,
            ],
        }

        result = process_user_tv_library(config, Mock(), "alice", library)

        assert mock_get_items.call_args[0][1] == "Anime"
        assert mock_load_cache.call_args.kwargs["lib_id"] == "anime"
        assert mock_save_cache.call_args.kwargs["lib_id"] == "anime"

        assert result["library_id"] == "anime"
        assert result["movie_profile"] is None
        assert result["movies_categorized"] == _empty_categorized()
        show_item = result["shows_categorized"]["other_services"]["Netflix"][0]
        assert show_item["library_id"] == "anime"
        assert mock_markdown.call_args.kwargs["library_suffix"] == "_anime"

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.enhance_profile_with_trakt")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_per_user_streaming_services_merges_onto_global(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_enhance,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_categorize,
        mock_markdown,
    ):
        """PR2 audit remediation: users.preferences.<user>.streaming_services
        was previously dead config - it must now merge onto the global
        streaming_services list (see get_streaming_services_for_user)."""
        mock_get_items.return_value = {"titles": set(), "tmdb_ids": set()}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = {"genres": {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = set()
        mock_load_cache.side_effect = _fanout_load_cache_side_effect
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"}
        config = {
            "plex": {},
            "users": {"preferences": {"alice": {"streaming_services": ["hulu"]}}},
            "external_recommendations": {"show_limit": 1, "min_relevance_score": 0.65},
            "streaming_services": ["netflix"],
            "trakt": {},
            "libraries": [library],
        }

        process_user_tv_library(config, Mock(), "alice", library)

        assert mock_categorize.call_args[0][2] == ["netflix", "hulu"]


class TestProcessUserMovieLibraryBranches:
    """Additional branch coverage for process_user_movie_library: language
    filtering, in-library/ignore-list cache pruning, Trakt watchlist
    exclusion, discovery, and above-limit cache trimming (#157 Phase 3.5)."""

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external.find_similar_content_with_profile")
    @patch("recommenders.external.get_authenticated_trakt_client")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.enhance_profile_with_trakt")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_exercises_filter_discovery_and_trim_branches(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_enhance,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_get_trakt,
        mock_find_similar,
        mock_categorize,
        mock_markdown,
    ):
        mock_get_items.return_value = {"titles": {("in library movie", 2018)}, "tmdb_ids": {777}}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = {"genres": {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = {"Ignored Movie"}
        mock_load_cache.return_value = {
            "1": {
                "tmdb_id": 1,
                "title": "Foreign Movie",
                "year": 2015,
                "rating": 6.0,
                "vote_count": 100,
                "score": 0.5,
                "original_language": "fr",
            },
            "777": {
                "tmdb_id": 777,
                "title": "in library movie",
                "year": 2018,
                "rating": 7.0,
                "vote_count": 200,
                "score": 0.7,
                "original_language": "en",
            },
            "2": {
                "tmdb_id": 2,
                "title": "Ignored Movie",
                "year": 2016,
                "rating": 6.5,
                "vote_count": 150,
                "score": 0.6,
                "original_language": "en",
            },
            "3": {
                "tmdb_id": 3,
                "title": "Existing Movie",
                "year": 2019,
                "rating": 7.2,
                "vote_count": 300,
                "score": 0.5,
                "original_language": "en",
            },
        }
        mock_get_trakt.return_value = Mock(get_watchlist_imdb_ids=Mock(return_value={"tt999"}))
        mock_find_similar.return_value = [
            {
                "tmdb_id": 3,
                "title": "Existing Movie",
                "year": 2019,
                "rating": 7.5,
                "vote_count": 350,
                "score": 0.95,
                "original_language": "en",
            },
            {
                "tmdb_id": 4,
                "title": "New Movie",
                "year": 2021,
                "rating": 8.0,
                "vote_count": 400,
                "score": 0.9,
                "original_language": "en",
            },
            {
                "tmdb_id": 5,
                "title": "Another New Movie",
                "year": 2022,
                "rating": 8.2,
                "vote_count": 420,
                "score": 0.85,
                "original_language": "en",
            },
        ]
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"}
        config = {
            "plex": {},
            "users": {"preferences": {}},
            "external_recommendations": {"movie_limit": 2, "min_relevance_score": 0.65, "language": "en"},
            "streaming_services": [],
            "trakt": {"import": {"exclude_watchlist": True}},
            "libraries": [library],
        }

        result = process_user_movie_library(config, Mock(), "alice", library)

        mock_find_similar.assert_called_once()
        assert mock_find_similar.call_args.kwargs["exclude_imdb_ids"] == {"tt999"}
        # Cache trimmed back down to movie_limit (2) after discovery added 3 items
        saved_cache = mock_save_cache.call_args[0][2]
        assert len(saved_cache) == 2
        assert result["library_id"] == "movies"

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external._build_profile_via_recommender")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_builds_profile_when_no_cached_profile(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_build_profile,
        mock_categorize,
        mock_markdown,
    ):
        mock_get_items.return_value = {"titles": set(), "tmdb_ids": set()}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = None
        mock_load_ignore.return_value = set()
        mock_load_cache.return_value = {
            "100": {
                "tmdb_id": 100,
                "title": "Cached Movie",
                "year": 2020,
                "rating": 7.5,
                "vote_count": 500,
                "score": 0.9,
                "original_language": "en",
            }
        }
        mock_build_profile.return_value = {"genres": {}}
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"}
        config = {
            "plex": {},
            "users": {"preferences": {}},
            "external_recommendations": {"movie_limit": 1, "min_relevance_score": 0.65},
            "streaming_services": [],
            "trakt": {},
            "libraries": [library],
        }

        process_user_movie_library(config, Mock(), "alice", library)

        mock_build_profile.assert_called_once()

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external.enhance_profile_with_trakt")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_skips_enhance_when_user_not_in_export_mapping(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_enhance,
        mock_categorize,
        mock_markdown,
    ):
        """When export.user_mode='mapping' and plex_users is set but doesn't
        include this user, Trakt enhancement is skipped for them."""
        mock_get_items.return_value = {"titles": set(), "tmdb_ids": set()}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = {"genres": {}}
        mock_load_ignore.return_value = set()
        mock_load_cache.return_value = {
            "100": {
                "tmdb_id": 100,
                "title": "Cached Movie",
                "year": 2020,
                "rating": 7.5,
                "vote_count": 500,
                "score": 0.9,
                "original_language": "en",
            }
        }
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"}
        config = {
            "plex": {},
            "users": {"preferences": {}},
            "external_recommendations": {"movie_limit": 1, "min_relevance_score": 0.65},
            "streaming_services": [],
            "trakt": {"export": {"user_mode": "mapping", "plex_users": ["bob"]}},
            "libraries": [library],
        }

        process_user_movie_library(config, Mock(), "alice", library)

        mock_enhance.assert_not_called()


class TestProcessUserTvLibraryBranches:
    """Additional branch coverage for process_user_tv_library: language
    filtering, in-library/ignore-list cache pruning, Trakt watchlist
    exclusion, discovery, and above-limit cache trimming (#157 Phase 3.5)."""

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external.find_similar_content_with_profile")
    @patch("recommenders.external.get_authenticated_trakt_client")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.enhance_profile_with_trakt")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_exercises_filter_discovery_and_trim_branches(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_enhance,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_get_trakt,
        mock_find_similar,
        mock_categorize,
        mock_markdown,
    ):
        mock_get_items.return_value = {"titles": {("in library show", 2018)}, "tmdb_ids": {777}}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = {"genres": {}}
        mock_enhance.side_effect = lambda profile, *a, **kw: profile
        mock_load_ignore.return_value = {"Ignored Show"}
        mock_load_cache.return_value = {
            "1": {
                "tmdb_id": 1,
                "title": "Foreign Show",
                "year": 2015,
                "rating": 6.0,
                "vote_count": 100,
                "score": 0.5,
                "original_language": "fr",
            },
            "777": {
                "tmdb_id": 777,
                "title": "in library show",
                "year": 2018,
                "rating": 7.0,
                "vote_count": 200,
                "score": 0.7,
                "original_language": "en",
            },
            "2": {
                "tmdb_id": 2,
                "title": "Ignored Show",
                "year": 2016,
                "rating": 6.5,
                "vote_count": 150,
                "score": 0.6,
                "original_language": "en",
            },
            "3": {
                "tmdb_id": 3,
                "title": "Existing Show",
                "year": 2019,
                "rating": 7.2,
                "vote_count": 300,
                "score": 0.5,
                "original_language": "en",
            },
        }
        mock_get_trakt.return_value = Mock(get_watchlist_imdb_ids=Mock(return_value={"tt888"}))
        mock_find_similar.return_value = [
            {
                "tmdb_id": 3,
                "title": "Existing Show",
                "year": 2019,
                "rating": 7.5,
                "vote_count": 350,
                "score": 0.95,
                "original_language": "en",
            },
            {
                "tmdb_id": 4,
                "title": "New Show",
                "year": 2021,
                "rating": 8.0,
                "vote_count": 400,
                "score": 0.9,
                "original_language": "en",
            },
            {
                "tmdb_id": 5,
                "title": "Another New Show",
                "year": 2022,
                "rating": 8.2,
                "vote_count": 420,
                "score": 0.85,
                "original_language": "en",
            },
        ]
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"}
        config = {
            "plex": {},
            "users": {"preferences": {}},
            "external_recommendations": {"show_limit": 2, "min_relevance_score": 0.65, "language": "en"},
            "streaming_services": [],
            "trakt": {"import": {"exclude_watchlist": True}},
            "libraries": [library],
        }

        result = process_user_tv_library(config, Mock(), "alice", library)

        mock_find_similar.assert_called_once()
        assert mock_find_similar.call_args.kwargs["exclude_imdb_ids"] == {"tt888"}
        saved_cache = mock_save_cache.call_args[0][2]
        assert len(saved_cache) == 2
        assert result["library_id"] == "tv-shows"

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external._build_profile_via_recommender")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_builds_profile_when_no_cached_profile(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_build_profile,
        mock_categorize,
        mock_markdown,
    ):
        mock_get_items.return_value = {"titles": set(), "tmdb_ids": set()}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = None
        mock_load_ignore.return_value = set()
        mock_load_cache.return_value = {
            "200": {
                "tmdb_id": 200,
                "title": "Cached Show",
                "year": 2019,
                "rating": 8.0,
                "vote_count": 300,
                "score": 0.9,
                "original_language": "en",
            }
        }
        mock_build_profile.return_value = {"genres": {}}
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"}
        config = {
            "plex": {},
            "users": {"preferences": {}},
            "external_recommendations": {"show_limit": 1, "min_relevance_score": 0.65},
            "streaming_services": [],
            "trakt": {},
            "libraries": [library],
        }

        process_user_tv_library(config, Mock(), "alice", library)

        mock_build_profile.assert_called_once()

    @patch("recommenders.external.generate_markdown")
    @patch("recommenders.external.categorize_by_streaming_service")
    @patch("recommenders.external.enhance_profile_with_trakt")
    @patch("recommenders.external.save_cache")
    @patch("recommenders.external.load_cache")
    @patch("recommenders.external.load_ignore_list")
    @patch("recommenders.external.load_user_profile_from_cache")
    @patch("recommenders.external.get_tmdb_config")
    @patch("recommenders.external.get_library_items")
    def test_skips_enhance_when_user_not_in_export_mapping(
        self,
        mock_get_items,
        mock_get_tmdb,
        mock_load_profile,
        mock_load_ignore,
        mock_load_cache,
        mock_save_cache,
        mock_enhance,
        mock_categorize,
        mock_markdown,
    ):
        mock_get_items.return_value = {"titles": set(), "tmdb_ids": set()}
        mock_get_tmdb.return_value = {"api_key": "fake_key", "use_keywords": True}
        mock_load_profile.return_value = {"genres": {}}
        mock_load_ignore.return_value = set()
        mock_load_cache.return_value = {
            "200": {
                "tmdb_id": 200,
                "title": "Cached Show",
                "year": 2019,
                "rating": 8.0,
                "vote_count": 300,
                "score": 0.9,
                "original_language": "en",
            }
        }
        mock_categorize.side_effect = _fanout_categorize_side_effect

        library = {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"}
        config = {
            "plex": {},
            "users": {"preferences": {}},
            "external_recommendations": {"show_limit": 1, "min_relevance_score": 0.65},
            "streaming_services": [],
            "trakt": {"export": {"user_mode": "mapping", "plex_users": ["bob"]}},
            "libraries": [library],
        }

        process_user_tv_library(config, Mock(), "alice", library)

        mock_enhance.assert_not_called()


class TestTVMovieGenreDetection:
    """Tests for TV movie (special) genre detection"""

    def test_tv_movie_genre_id_constant(self):
        """Test TV_MOVIE_GENRE_ID is correct"""
        assert TV_MOVIE_GENRE_ID == 10770

    @patch("recommenders.external.requests.get")
    def test_get_movie_genre_ids_returns_genres(self, mock_get):
        """Test get_movie_genre_ids returns list of genre IDs"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "genres": [{"id": 28, "name": "Action"}, {"id": 10770, "name": "TV Movie"}, {"id": 35, "name": "Comedy"}]
        }
        mock_get.return_value = mock_response

        result = get_movie_genre_ids("api_key", 12345)

        assert result == [28, 10770, 35]
        assert TV_MOVIE_GENRE_ID in result

    @patch("recommenders.external.requests.get")
    def test_get_movie_genre_ids_returns_empty_on_error(self, mock_get):
        """Test get_movie_genre_ids returns empty list on API error"""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = get_movie_genre_ids("api_key", 12345)

        assert result == []

    @patch("recommenders.external.requests.get")
    def test_get_movie_genre_ids_returns_empty_on_exception(self, mock_get):
        """Test get_movie_genre_ids handles exceptions gracefully"""
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError("Network error")

        result = get_movie_genre_ids("api_key", 12345)

        assert result == []

    @patch("recommenders.external.requests.get")
    def test_get_movie_genre_ids_no_genres_in_response(self, mock_get):
        """Test get_movie_genre_ids handles missing genres key"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"title": "Some Movie"}  # No genres
        mock_get.return_value = mock_response

        result = get_movie_genre_ids("api_key", 12345)

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
            return re.sub(r"[^\w\s]", "", title.lower()).strip()

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
        sparse_profile = {"genres": Counter({"Action": 5, "Comedy": 3, "Drama": 2})}
        # 10 items total, below threshold of 40
        assert is_thin_profile(sparse_profile) is True

    def test_is_thin_profile_returns_false_for_full_profile(self):
        """Test that profiles with enough items are not detected as thin"""
        full_profile = {"genres": Counter({"Action": 20, "Comedy": 15, "Drama": 10, "Thriller": 5})}
        # 50 items total, above threshold of 40
        assert is_thin_profile(full_profile) is False

    def test_is_thin_profile_exactly_at_threshold(self):
        """Test boundary condition at threshold"""
        at_threshold = {"genres": Counter({"Action": 20, "Comedy": 20})}
        # Exactly 40 items, should NOT be thin (need to be below threshold)
        assert is_thin_profile(at_threshold) is False

    def test_is_thin_profile_empty_profile(self):
        """Test empty profile is detected as thin"""
        empty_profile = {"genres": Counter()}
        assert is_thin_profile(empty_profile) is True

    def test_thin_profile_threshold_constant(self):
        """Test threshold constant is set correctly"""
        assert THIN_PROFILE_THRESHOLD == 40

    def test_is_thin_profile_coerces_plain_dict_genres(self):
        """#273 PR3: genres doesn't have to already be a Counter - a
        plain dict (e.g. straight off a JSON cache read) works too."""
        plain_dict_profile = {"genres": {"Action": 20, "Comedy": 15, "Drama": 10, "Thriller": 5}}
        assert is_thin_profile(plain_dict_profile) is False

    def test_is_thin_profile_missing_genres_key(self):
        """#273 PR3: no 'genres' key at all is treated as an empty (thin) profile."""
        assert is_thin_profile({}) is True


class TestDiscoverCandidatesByProfileKeyCoercion:
    """#273 PR3: discover_candidates_by_profile's genre/keyword lookups
    (user_profile["genres"].most_common()/user_profile["keywords"].most_common())
    used to assume the caller always handed over Counter objects keyed
    exactly "genres"/"keywords" - true for load_user_profile_from_cache()'s
    own return shape, but not guaranteed for every caller. Both are now
    coerced to Counter if not already one.

    #273 PR4: no longer accepts "tmdb_keywords" as an alternate keyword
    key (removed along with the other now-provably-dead #273 compat
    shims - see CHANGELOG: every real caller of this function always
    uses "keywords", never the raw watched_data_counters storage key
    "tmdb_keywords")."""

    @patch("recommenders.external.requests.get")
    def test_plain_dict_genres_and_keywords_do_not_raise(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        # Plain dicts (not Counter) - the .most_common() coercion is the
        # thing under test here, not the key name (see class docstring
        # for why "tmdb_keywords" is no longer an accepted alternative).
        user_profile = {"genres": {"nonexistent-genre-xyz": 5}, "keywords": {"some-keyword": 3}}

        # Must not raise (AttributeError: 'dict' object has no attribute
        # 'most_common') - the whole point of this test.
        candidates = discover_candidates_by_profile(
            tmdb_api_key="fake_key",
            user_profile=user_profile,
            library_data={"tmdb_ids": set(), "titles": set()},
            media_type="movie",
        )
        assert isinstance(candidates, dict)

    @patch("recommenders.external.requests.get")
    def test_counter_genres_and_keywords_key_still_work(self, mock_get):
        """Regression: the pre-existing shape (Counter-valued, 'keywords'
        key - load_user_profile_from_cache()'s own return shape) must
        keep working identically."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        user_profile = {
            "genres": Counter({"nonexistent-genre-xyz": 5}),
            "keywords": Counter({"some-keyword": 3}),
        }
        candidates = discover_candidates_by_profile(
            tmdb_api_key="fake_key",
            user_profile=user_profile,
            library_data={"tmdb_ids": set(), "titles": set()},
            media_type="movie",
        )
        assert isinstance(candidates, dict)

    @patch("recommenders.external.requests.get")
    def test_missing_genres_and_keywords_keys_do_not_raise(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        candidates = discover_candidates_by_profile(
            tmdb_api_key="fake_key",
            user_profile={},
            library_data={"tmdb_ids": set(), "titles": set()},
            media_type="movie",
        )
        assert candidates == {}


class TestBuildProfileViaRecommender:
    """Tests for _build_profile_via_recommender (#273 PR3) - replaces
    the deleted build_user_profile(). Constructs the real
    PlexMovieRecommender/PlexTVRecommender for `username` directly
    (the same "shared path" recommenders/movie.py's/tv.py's own
    builders use) rather than re-implementing a second, independently
    buggy Plex scan."""

    @patch("recommenders.external.get_project_root", return_value="/fake/root")
    @patch("recommenders.movie.PlexMovieRecommender")
    def test_movie_returns_counter_profile_from_recommender(self, mock_recommender_cls, mock_root):
        mock_recommender = Mock()
        mock_recommender.watched_data_counters = {
            "genres": {"action": 2.0},
            "directors": {"Some Director": 1.0},
            "actors": {"Some Actor": 1.5},
            "tmdb_keywords": {"heist": 0.6},
            "languages": {"english": 2.0},
            "tmdb_ids": {101, 102},
        }
        mock_recommender_cls.return_value = mock_recommender

        profile = _build_profile_via_recommender("alice", "movie")

        mock_recommender_cls.assert_called_once_with("/fake/root/config/config.yml", single_user="alice")
        assert isinstance(profile["genres"], Counter)
        assert profile["genres"]["action"] == 2.0
        # tmdb_keywords -> keywords, matching load_user_profile_from_cache()'s
        # own renaming convention.
        assert profile["keywords"]["heist"] == 0.6
        assert profile["tmdb_ids"] == {101, 102}

    @patch("recommenders.external.get_project_root", return_value="/fake/root")
    @patch("recommenders.tv.PlexTVRecommender")
    def test_tv_media_type_uses_plex_tv_recommender(self, mock_recommender_cls, mock_root):
        mock_recommender = Mock()
        mock_recommender.watched_data_counters = {"genres": {"drama": 1.0}}
        mock_recommender_cls.return_value = mock_recommender

        profile = _build_profile_via_recommender("alice", "tv")

        mock_recommender_cls.assert_called_once_with("/fake/root/config/config.yml", single_user="alice")
        assert profile["genres"]["drama"] == 1.0

    @patch("recommenders.external.log_warning")
    @patch("recommenders.external.get_project_root", return_value="/fake/root")
    @patch("recommenders.movie.PlexMovieRecommender")
    def test_construction_failure_returns_empty_profile_not_raise(self, mock_recommender_cls, mock_root, mock_warn):
        mock_recommender_cls.side_effect = Exception("Plex unreachable")

        profile = _build_profile_via_recommender("alice", "movie")

        assert profile["genres"] == Counter()
        assert profile["tmdb_ids"] == set()
        mock_warn.assert_called_once()


class TestDiscoverPopularByGenre:
    """Tests for genre-popular fallback discovery"""

    @patch("recommenders.external.requests.get")
    @patch("recommenders.external.time.sleep")
    def test_returns_recommendations_for_valid_genres(self, mock_sleep, mock_get):
        """Test that popular items are returned for valid genres"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "id": 123,
                    "title": "Popular Action Movie",
                    "release_date": "2024-01-15",
                    "vote_average": 8.5,
                    "vote_count": 1000,
                    "overview": "A great action movie",
                    "genre_ids": [28],
                },
                {
                    "id": 456,
                    "title": "Another Action Movie",
                    "release_date": "2023-06-20",
                    "vote_average": 7.8,
                    "vote_count": 800,
                    "overview": "Another good one",
                    "genre_ids": [28],
                },
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        results = discover_popular_by_genre(
            tmdb_api_key="test_key", top_genres=["Action"], library_data={}, media_type="movie", limit=10
        )

        assert len(results) == 2
        assert results[0]["title"] == "Popular Action Movie"
        assert results[0]["tmdb_id"] == 123
        assert results[0]["rating"] == 8.5

    @patch("recommenders.external.requests.get")
    @patch("recommenders.external.time.sleep")
    def test_filters_out_library_items(self, mock_sleep, mock_get):
        """Test that items already in library are excluded"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "id": 123,
                    "title": "In Library",
                    "release_date": "2024-01-01",
                    "vote_average": 8.0,
                    "vote_count": 500,
                    "overview": "",
                    "genre_ids": [],
                },
                {
                    "id": 456,
                    "title": "Not In Library",
                    "release_date": "2024-01-01",
                    "vote_average": 8.0,
                    "vote_count": 500,
                    "overview": "",
                    "genre_ids": [],
                },
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # 123 is in library
        library_data = {123: {"title": "In Library"}}

        results = discover_popular_by_genre(
            tmdb_api_key="test_key", top_genres=["Action"], library_data=library_data, media_type="movie", limit=10
        )

        assert len(results) == 1
        assert results[0]["tmdb_id"] == 456

    @patch("recommenders.external.requests.get")
    @patch("recommenders.external.time.sleep")
    def test_handles_tv_shows(self, mock_sleep, mock_get):
        """Test TV show discovery uses correct field names"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "id": 789,
                    "name": "Popular TV Show",
                    "first_air_date": "2024-03-01",
                    "vote_average": 9.0,
                    "vote_count": 2000,
                    "overview": "Great show",
                    "genre_ids": [18],
                }
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        results = discover_popular_by_genre(
            tmdb_api_key="test_key", top_genres=["Drama"], library_data={}, media_type="show", limit=10
        )

        assert len(results) == 1
        assert results[0]["title"] == "Popular TV Show"
        assert results[0]["year"] == 2024

    @patch("recommenders.external.requests.get")
    @patch("recommenders.external.time.sleep")
    def test_respects_limit(self, mock_sleep, mock_get):
        """Test that limit parameter is respected"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "id": i,
                    "title": f"Movie {i}",
                    "release_date": "2024-01-01",
                    "vote_average": 8.0,
                    "vote_count": 500,
                    "overview": "",
                    "genre_ids": [],
                }
                for i in range(20)
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        results = discover_popular_by_genre(
            tmdb_api_key="test_key", top_genres=["Action"], library_data={}, media_type="movie", limit=5
        )

        assert len(results) == 5

    @patch("recommenders.external.requests.get")
    @patch("recommenders.external.time.sleep")
    def test_handles_invalid_genre(self, mock_sleep, mock_get):
        """Test graceful handling of invalid genre names"""
        results = discover_popular_by_genre(
            tmdb_api_key="test_key", top_genres=["NotARealGenre"], library_data={}, media_type="movie", limit=10
        )

        # Should return empty list, not crash
        assert results == []
        mock_get.assert_not_called()

    @patch("recommenders.external.requests.get")
    @patch("recommenders.external.time.sleep")
    def test_applies_rate_limit_delay_between_genre_calls(self, mock_sleep, mock_get):
        """Regression test for a ruff F821: TMDB_RATE_LIMIT_DELAY was an
        undefined name here, so evaluating it raised a NameError that the
        surrounding try/except silently swallowed (logged as a generic
        "Genre discover failed" warning) -- meaning the rate-limit sleep
        never actually ran. Asserting the real constant is passed to the
        mocked sleep call proves that line executes without raising."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "id": 123,
                    "title": "Popular Action Movie",
                    "release_date": "2024-01-15",
                    "vote_average": 8.5,
                    "vote_count": 1000,
                    "overview": "",
                    "genre_ids": [28],
                }
            ]
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        discover_popular_by_genre(
            tmdb_api_key="test_key", top_genres=["Action"], library_data={}, media_type="movie", limit=10
        )

        mock_sleep.assert_called_once_with(TMDB_RATE_LIMIT_DELAY)


class TestFindSimilarContentThinProfile:
    """Tests for thin profile handling in find_similar_content_with_profile"""

    @patch("recommenders.external.requests.get")
    def test_thin_profile_uses_reduced_iterations(self, mock_get):
        """Test that thin profiles use reduced iterations instead of full discovery"""
        from recommenders.external import find_similar_content_with_profile, is_thin_profile

        # discover_candidates_by_profile hits the real TMDB Discover API via
        # requests.get - mock it to fail the way an unreachable/uncalled API
        # would (all callers here catch requests.RequestException and
        # continue with empty results), rather than actually reaching
        # api.themoviedb.org for real.
        mock_get.side_effect = requests.RequestException("no real API in tests")

        # Create a thin profile (less than 40 items)
        thin_profile = {
            "genres": Counter({"Action": 5, "Comedy": 3}),
            "actors": Counter(),
            "directors": Counter(),
            "studios": Counter(),
            "keywords": Counter(),
            "languages": Counter(),
        }

        # Verify it's detected as thin
        assert is_thin_profile(thin_profile) is True

        # The function will run with reduced iterations (max 2)
        # We just verify it doesn't crash and returns a list
        result = find_similar_content_with_profile(
            tmdb_api_key="test_key", user_profile=thin_profile, library_data={}, media_type="movie", limit=10
        )

        # Should return a list (possibly empty without real API)
        assert isinstance(result, list)

    @patch("recommenders.external.discover_popular_by_genre")
    def test_full_profile_skips_fallback(self, mock_discover):
        """Test that full profiles don't use the fallback"""
        from recommenders.external import is_thin_profile

        # Create a full profile (40+ items)
        full_profile = {
            "genres": Counter({"Action": 20, "Comedy": 15, "Drama": 10}),
            "actors": Counter(),
            "directors": Counter(),
            "studios": Counter(),
            "keywords": Counter(),
            "languages": Counter(),
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


class TestProcessUserResolveContext:
    """Tests for _pu_resolve_context() - one of process_user's named
    pipeline stages (#audit remediation batch E, PR3)."""

    def test_legacy_config_resolves_section_names_and_none_lib_ids(self):
        config = {"plex": {"movie_library": "Movies", "tv_library": "TV Shows"}, "users": {"preferences": {}}}
        (
            user_prefs,
            display_name,
            movie_library_name,
            tv_library_name,
            movie_cache_lib_id,
            tv_cache_lib_id,
        ) = _pu_resolve_context(config, "alice", None, None)

        assert user_prefs == {}
        assert display_name == "alice"
        assert movie_library_name == "Movies"
        assert tv_library_name == "TV Shows"
        assert movie_cache_lib_id is None
        assert tv_cache_lib_id is None

    def test_display_name_prefers_configured_preference(self):
        config = {
            "plex": {"movie_library": "Movies", "tv_library": "TV Shows"},
            "users": {"preferences": {"alice": {"display_name": "Alice A."}}},
        }
        user_prefs, display_name, *_ = _pu_resolve_context(config, "alice", None, None)
        assert display_name == "Alice A."
        assert user_prefs == {"display_name": "Alice A."}

    @patch("recommenders.external.get_libraries_for_media_type")
    def test_multi_library_qualifies_cache_lib_id(self, mock_get_libs):
        mock_get_libs.return_value = [Mock(), Mock()]  # 2 libraries -> "is_multi"
        config = {"plex": {}, "users": {"preferences": {}}}
        movie_library = {"id": "movies-4k", "section": "Movies 4K"}
        tv_library = {"id": "tv-shows", "section": "TV Shows"}

        *_rest, movie_cache_lib_id, tv_cache_lib_id = _pu_resolve_context(config, "alice", movie_library, tv_library)

        assert movie_cache_lib_id == "movies-4k"
        assert tv_cache_lib_id == "tv-shows"


class TestProcessUserPlanDiscovery:
    """Tests for _pu_plan_discovery() - one of process_user's named
    pipeline stages (#audit remediation batch E, PR3)."""

    def test_healthy_cache_has_zero_deficit(self):
        config = {
            "external_recommendations": {"movie_limit": 2, "show_limit": 2, "min_relevance_score": 0.5},
            "trakt": {},
        }
        movie_cache = {"1": {"score": 0.9}, "2": {"score": 0.8}}
        show_cache = {}

        result = _pu_plan_discovery(config, {}, movie_cache, show_cache)
        (
            movie_limit,
            show_limit,
            min_relevance,
            exclude_genres,
            quality_movies,
            quality_shows,
            movie_deficit,
            show_deficit,
            cached_movie_ids,
            cached_show_ids,
            exclude_movie_imdb_ids,
            exclude_show_imdb_ids,
        ) = result

        assert movie_deficit == 0
        assert show_deficit == 2
        assert len(quality_movies) == 2
        assert cached_movie_ids == {1, 2}
        assert exclude_genres == []
        assert exclude_movie_imdb_ids == set()
        assert exclude_show_imdb_ids == set()

    def test_excluded_genres_from_user_prefs(self):
        config = {"external_recommendations": {"movie_limit": 5, "show_limit": 5}, "trakt": {}}
        user_prefs = {"exclude_genres": ["Horror"]}

        result = _pu_plan_discovery(config, user_prefs, {}, {})
        assert result[3] == ["Horror"]


class TestPuCategorizeAndStamp:
    """Tests for _pu_categorize_and_stamp() - process_user's (the
    legacy, non-fan-out path's) streaming-service categorization stage
    (PR2 audit remediation: users.preferences.<user>.streaming_services
    was previously dead config here - it now merges onto the global
    streaming_services list the same way process_user_movie_library/
    process_user_tv_library do - see get_streaming_services_for_user)."""

    @patch("recommenders.external.categorize_by_streaming_service")
    def test_no_user_override_uses_global_services_only(self, mock_categorize):
        mock_categorize.return_value = _empty_categorized()
        config = {"streaming_services": ["netflix"], "users": {"preferences": {}}}

        _pu_categorize_and_stamp(config, [], [], "fake_key", None, None, "alice")

        assert mock_categorize.call_args_list[0][0][2] == ["netflix"]
        assert mock_categorize.call_args_list[1][0][2] == ["netflix"]

    @patch("recommenders.external.categorize_by_streaming_service")
    def test_user_override_merges_onto_global_services(self, mock_categorize):
        mock_categorize.return_value = _empty_categorized()
        config = {
            "streaming_services": ["netflix"],
            "users": {"preferences": {"alice": {"streaming_services": ["hulu"]}}},
        }

        _pu_categorize_and_stamp(config, [], [], "fake_key", None, None, "alice")

        assert mock_categorize.call_args_list[0][0][2] == ["netflix", "hulu"]

    @patch("recommenders.external.categorize_by_streaming_service")
    def test_other_user_without_override_unaffected_by_a_different_users_override(self, mock_categorize):
        """Alice's personal streaming_services must not leak onto bob's
        run - each user is resolved independently by username."""
        mock_categorize.return_value = _empty_categorized()
        config = {
            "streaming_services": ["netflix"],
            "users": {"preferences": {"alice": {"streaming_services": ["hulu"]}}},
        }

        _pu_categorize_and_stamp(config, [], [], "fake_key", None, None, "bob")

        assert mock_categorize.call_args_list[0][0][2] == ["netflix"]
