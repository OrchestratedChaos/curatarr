"""Tests for utils/radarr.py - Radarr API client."""

import pytest
from unittest.mock import Mock, patch
import requests

from utils.radarr import (
    RadarrClient,
    RadarrAPIError,
    create_radarr_client,
    RADARR_RATE_LIMIT_DELAY,
)


class TestRadarrClientInit:
    """Tests for RadarrClient initialization."""

    def test_init_with_url_and_key(self):
        """Test initialization with URL and API key."""
        client = RadarrClient(
            url="http://localhost:7878",
            api_key="test_api_key"
        )
        assert client.url == "http://localhost:7878"
        assert client.api_key == "test_api_key"

    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is stripped from URL."""
        client = RadarrClient(
            url="http://localhost:7878/",
            api_key="key"
        )
        assert client.url == "http://localhost:7878"


class TestRadarrClientHeaders:
    """Tests for header generation."""

    def test_headers_include_api_key(self):
        """Test headers include X-Api-Key."""
        client = RadarrClient("http://localhost:7878", "my_api_key")
        headers = client._get_headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["X-Api-Key"] == "my_api_key"


class TestRadarrClientMakeRequest:
    """Tests for API request handling."""

    @patch('utils.radarr.requests.request')
    def test_successful_request(self, mock_request):
        """Test successful API request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        mock_request.return_value = mock_response

        client = RadarrClient("http://localhost:7878", "key")
        result = client._make_request("GET", "system/status")

        assert result == {"status": "ok"}
        mock_request.assert_called_once()

    @patch('utils.radarr.requests.request')
    def test_unauthorized_raises_error(self, mock_request):
        """Test 401 raises RadarrAPIError."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_request.return_value = mock_response

        client = RadarrClient("http://localhost:7878", "bad_key")

        with pytest.raises(RadarrAPIError, match="Invalid API key"):
            client._make_request("GET", "system/status")

    @patch('utils.radarr.requests.request')
    def test_404_returns_none(self, mock_request):
        """Test 404 returns None."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_request.return_value = mock_response

        client = RadarrClient("http://localhost:7878", "key")
        result = client._make_request("GET", "movie/99999")

        assert result is None

    @patch('utils.radarr.requests.request')
    def test_204_returns_none(self, mock_request):
        """Test 204 No Content returns None."""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_request.return_value = mock_response

        client = RadarrClient("http://localhost:7878", "key")
        result = client._make_request("DELETE", "movie/1")

        assert result is None

    @patch('utils.radarr.requests.request')
    def test_timeout_raises_error(self, mock_request):
        """Test timeout raises RadarrAPIError."""
        mock_request.side_effect = requests.exceptions.Timeout()

        client = RadarrClient("http://localhost:7878", "key")

        with pytest.raises(RadarrAPIError, match="timeout"):
            client._make_request("GET", "system/status")

    @patch('utils.radarr.requests.request')
    def test_connection_error_raises_error(self, mock_request):
        """Test connection error raises RadarrAPIError."""
        mock_request.side_effect = requests.exceptions.ConnectionError()

        client = RadarrClient("http://localhost:7878", "key")

        with pytest.raises(RadarrAPIError, match="Could not connect"):
            client._make_request("GET", "system/status")


class TestRadarrClientTestConnection:
    """Tests for test_connection method."""

    @patch.object(RadarrClient, '_make_request')
    def test_successful_connection(self, mock_request):
        """Test successful connection returns True."""
        mock_request.return_value = {"version": "5.0.0"}

        client = RadarrClient("http://localhost:7878", "key")
        result = client.test_connection()

        assert result is True
        mock_request.assert_called_with("GET", "system/status")


class TestRadarrClientMovieExists:
    """Tests for movie_exists method."""

    @patch.object(RadarrClient, 'get_movies')
    def test_movie_exists_true(self, mock_get_movies):
        """Test movie_exists returns True when found."""
        mock_get_movies.return_value = [
            {"id": 1, "tmdbId": 550},
            {"id": 2, "tmdbId": 278}
        ]

        client = RadarrClient("http://localhost:7878", "key")
        result = client.movie_exists(550)

        assert result is True

    @patch.object(RadarrClient, 'get_movies')
    def test_movie_exists_false(self, mock_get_movies):
        """Test movie_exists returns False when not found."""
        mock_get_movies.return_value = [
            {"id": 1, "tmdbId": 550}
        ]

        client = RadarrClient("http://localhost:7878", "key")
        result = client.movie_exists(99999)

        assert result is False


class TestRadarrClientLookupMovie:
    """Tests for lookup_movie method."""

    @patch.object(RadarrClient, '_make_request')
    def test_lookup_found(self, mock_request):
        """Test lookup returns movie data when found."""
        mock_request.return_value = [
            {"title": "Fight Club", "tmdbId": 550}
        ]

        client = RadarrClient("http://localhost:7878", "key")
        result = client.lookup_movie(550)

        assert result["title"] == "Fight Club"
        assert result["tmdbId"] == 550

    @patch.object(RadarrClient, '_make_request')
    def test_lookup_not_found(self, mock_request):
        """Test lookup returns None when not found."""
        mock_request.return_value = []

        client = RadarrClient("http://localhost:7878", "key")
        result = client.lookup_movie(99999)

        assert result is None


class TestRadarrClientQualityProfiles:
    """Tests for quality profile methods."""

    @patch.object(RadarrClient, '_make_request')
    def test_get_quality_profiles(self, mock_request):
        """Test getting quality profiles."""
        mock_request.return_value = [
            {"id": 1, "name": "HD-1080p"},
            {"id": 2, "name": "SD"}
        ]

        client = RadarrClient("http://localhost:7878", "key")
        result = client.get_quality_profiles()

        assert len(result) == 2
        assert result[0]["name"] == "HD-1080p"

    @patch.object(RadarrClient, 'get_quality_profiles')
    def test_get_quality_profile_id_found(self, mock_profiles):
        """Test getting profile ID by name."""
        mock_profiles.return_value = [
            {"id": 1, "name": "HD-1080p"},
            {"id": 2, "name": "SD"}
        ]

        client = RadarrClient("http://localhost:7878", "key")
        result = client.get_quality_profile_id("HD-1080p")

        assert result == 1

    @patch.object(RadarrClient, 'get_quality_profiles')
    def test_get_quality_profile_id_not_found(self, mock_profiles):
        """Test getting profile ID returns None when not found."""
        mock_profiles.return_value = [
            {"id": 1, "name": "HD-1080p"}
        ]

        client = RadarrClient("http://localhost:7878", "key")
        result = client.get_quality_profile_id("Ultra-HD")

        assert result is None

    @patch.object(RadarrClient, 'get_quality_profiles')
    def test_get_quality_profile_id_case_insensitive(self, mock_profiles):
        """Test profile lookup is case insensitive."""
        mock_profiles.return_value = [
            {"id": 1, "name": "HD-1080p"}
        ]

        client = RadarrClient("http://localhost:7878", "key")
        result = client.get_quality_profile_id("hd-1080p")

        assert result == 1


class TestRadarrClientTags:
    """Tests for tag methods."""

    @patch.object(RadarrClient, '_make_request')
    def test_get_tags(self, mock_request):
        """Test getting tags."""
        mock_request.return_value = [
            {"id": 1, "label": "Curatarr"}
        ]

        client = RadarrClient("http://localhost:7878", "key")
        result = client.get_tags()

        assert len(result) == 1
        assert result[0]["label"] == "Curatarr"

    @patch.object(RadarrClient, 'get_tags')
    @patch.object(RadarrClient, '_make_request')
    def test_get_or_create_tag_exists(self, mock_request, mock_tags):
        """Test get_or_create returns existing tag ID."""
        mock_tags.return_value = [
            {"id": 5, "label": "Curatarr"}
        ]

        client = RadarrClient("http://localhost:7878", "key")
        result = client.get_or_create_tag("Curatarr")

        assert result == 5
        mock_request.assert_not_called()

    @patch.object(RadarrClient, 'get_tags')
    @patch.object(RadarrClient, '_make_request')
    def test_get_or_create_tag_creates(self, mock_request, mock_tags):
        """Test get_or_create creates new tag."""
        mock_tags.return_value = []
        mock_request.return_value = {"id": 10, "label": "NewTag"}

        client = RadarrClient("http://localhost:7878", "key")
        result = client.get_or_create_tag("NewTag")

        assert result == 10
        mock_request.assert_called_with("POST", "tag", data={"label": "NewTag"})


class TestRadarrClientAddMovie:
    """Tests for add_movie method."""

    @patch.object(RadarrClient, '_make_request')
    def test_add_movie_basic(self, mock_request):
        """Test adding a movie with basic options."""
        mock_request.return_value = {"id": 123, "title": "Test Movie"}

        client = RadarrClient("http://localhost:7878", "key")
        result = client.add_movie(
            tmdb_id=550,
            title="Test Movie",
            root_folder_path="/movies",
            quality_profile_id=1
        )

        assert result["title"] == "Test Movie"
        mock_request.assert_called_once()

        call_args = mock_request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "movie"
        assert call_args[1]["data"]["tmdbId"] == 550
        assert call_args[1]["data"]["title"] == "Test Movie"

    @patch.object(RadarrClient, '_make_request')
    def test_add_movie_with_tags(self, mock_request):
        """Test adding a movie with tags."""
        mock_request.return_value = {"id": 123}

        client = RadarrClient("http://localhost:7878", "key")
        client.add_movie(
            tmdb_id=550,
            title="Test Movie",
            root_folder_path="/movies",
            quality_profile_id=1,
            tag_ids=[1, 2, 3]
        )

        call_args = mock_request.call_args
        assert call_args[1]["data"]["tags"] == [1, 2, 3]

    @patch.object(RadarrClient, '_make_request')
    def test_add_movie_with_minimum_availability(self, mock_request):
        """Test adding a movie with minimum availability."""
        mock_request.return_value = {"id": 123}

        client = RadarrClient("http://localhost:7878", "key")
        client.add_movie(
            tmdb_id=550,
            title="Test Movie",
            root_folder_path="/movies",
            quality_profile_id=1,
            minimum_availability="inCinemas"
        )

        call_args = mock_request.call_args
        assert call_args[1]["data"]["minimumAvailability"] == "inCinemas"


class TestCreateRadarrClient:
    """Tests for create_radarr_client factory function."""

    def test_returns_none_when_disabled(self):
        """Test returns None when Radarr disabled."""
        config = {"radarr": {"enabled": False}}
        result = create_radarr_client(config)
        assert result is None

    def test_returns_none_when_no_config(self):
        """Test returns None when no Radarr config."""
        config = {}
        result = create_radarr_client(config)
        assert result is None

    def test_returns_none_when_placeholder_key(self):
        """Test returns None when API key is placeholder."""
        config = {
            "radarr": {
                "enabled": True,
                "url": "http://localhost:7878",
                "api_key": "YOUR_RADARR_API_KEY"
            }
        }
        result = create_radarr_client(config)
        assert result is None

    def test_returns_client_when_configured(self):
        """Test returns RadarrClient when properly configured."""
        config = {
            "radarr": {
                "enabled": True,
                "url": "http://localhost:7878",
                "api_key": "real_api_key"
            }
        }
        result = create_radarr_client(config)
        assert isinstance(result, RadarrClient)
        assert result.url == "http://localhost:7878"
        assert result.api_key == "real_api_key"
