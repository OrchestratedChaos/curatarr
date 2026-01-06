"""Tests for utils/sonarr.py - Sonarr API client."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests

from utils.sonarr import (
    SonarrClient,
    SonarrAPIError,
    create_sonarr_client,
    SONARR_RATE_LIMIT_DELAY,
)


class TestSonarrClientInit:
    """Tests for SonarrClient initialization."""

    def test_init_with_url_and_key(self):
        """Test initialization with URL and API key."""
        client = SonarrClient(
            url="http://localhost:8989",
            api_key="test_api_key"
        )
        assert client.url == "http://localhost:8989"
        assert client.api_key == "test_api_key"

    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is stripped from URL."""
        client = SonarrClient(
            url="http://localhost:8989/",
            api_key="key"
        )
        assert client.url == "http://localhost:8989"


class TestSonarrClientHeaders:
    """Tests for header generation."""

    def test_headers_include_api_key(self):
        """Test headers include X-Api-Key."""
        client = SonarrClient("http://localhost:8989", "my_api_key")
        headers = client._get_headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["X-Api-Key"] == "my_api_key"


class TestSonarrClientMakeRequest:
    """Tests for API request handling."""

    @patch('utils.sonarr.requests.request')
    def test_successful_request(self, mock_request):
        """Test successful API request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        mock_request.return_value = mock_response

        client = SonarrClient("http://localhost:8989", "key")
        result = client._make_request("GET", "system/status")

        assert result == {"status": "ok"}
        mock_request.assert_called_once()

    @patch('utils.sonarr.requests.request')
    def test_unauthorized_raises_error(self, mock_request):
        """Test 401 raises SonarrAPIError."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_request.return_value = mock_response

        client = SonarrClient("http://localhost:8989", "bad_key")

        with pytest.raises(SonarrAPIError, match="Invalid API key"):
            client._make_request("GET", "system/status")

    @patch('utils.sonarr.requests.request')
    def test_404_returns_none(self, mock_request):
        """Test 404 returns None."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_request.return_value = mock_response

        client = SonarrClient("http://localhost:8989", "key")
        result = client._make_request("GET", "series/99999")

        assert result is None

    @patch('utils.sonarr.requests.request')
    def test_204_returns_none(self, mock_request):
        """Test 204 No Content returns None."""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_request.return_value = mock_response

        client = SonarrClient("http://localhost:8989", "key")
        result = client._make_request("DELETE", "series/1")

        assert result is None

    @patch('utils.sonarr.requests.request')
    def test_timeout_raises_error(self, mock_request):
        """Test timeout raises SonarrAPIError."""
        mock_request.side_effect = requests.exceptions.Timeout()

        client = SonarrClient("http://localhost:8989", "key")

        with pytest.raises(SonarrAPIError, match="timeout"):
            client._make_request("GET", "system/status")

    @patch('utils.sonarr.requests.request')
    def test_connection_error_raises_error(self, mock_request):
        """Test connection error raises SonarrAPIError."""
        mock_request.side_effect = requests.exceptions.ConnectionError()

        client = SonarrClient("http://localhost:8989", "key")

        with pytest.raises(SonarrAPIError, match="Could not connect"):
            client._make_request("GET", "system/status")


class TestSonarrClientTestConnection:
    """Tests for test_connection method."""

    @patch.object(SonarrClient, '_make_request')
    def test_successful_connection(self, mock_request):
        """Test successful connection returns True."""
        mock_request.return_value = {"version": "4.0.0"}

        client = SonarrClient("http://localhost:8989", "key")
        result = client.test_connection()

        assert result is True
        mock_request.assert_called_with("GET", "system/status")


class TestSonarrClientSeriesExists:
    """Tests for series_exists method."""

    @patch.object(SonarrClient, 'get_series')
    def test_series_exists_true(self, mock_get_series):
        """Test series_exists returns True when found."""
        mock_get_series.return_value = [
            {"id": 1, "imdbId": "tt1234567"},
            {"id": 2, "imdbId": "tt7654321"}
        ]

        client = SonarrClient("http://localhost:8989", "key")
        result = client.series_exists("tt1234567")

        assert result is True

    @patch.object(SonarrClient, 'get_series')
    def test_series_exists_false(self, mock_get_series):
        """Test series_exists returns False when not found."""
        mock_get_series.return_value = [
            {"id": 1, "imdbId": "tt1234567"}
        ]

        client = SonarrClient("http://localhost:8989", "key")
        result = client.series_exists("tt9999999")

        assert result is False


class TestSonarrClientLookupSeries:
    """Tests for lookup_series method."""

    @patch.object(SonarrClient, '_make_request')
    def test_lookup_found(self, mock_request):
        """Test lookup returns series data when found."""
        mock_request.return_value = [
            {"title": "Breaking Bad", "tvdbId": 81189}
        ]

        client = SonarrClient("http://localhost:8989", "key")
        result = client.lookup_series("tt0903747")

        assert result["title"] == "Breaking Bad"
        assert result["tvdbId"] == 81189

    @patch.object(SonarrClient, '_make_request')
    def test_lookup_not_found(self, mock_request):
        """Test lookup returns None when not found."""
        mock_request.return_value = []

        client = SonarrClient("http://localhost:8989", "key")
        result = client.lookup_series("tt9999999")

        assert result is None


class TestSonarrClientQualityProfiles:
    """Tests for quality profile methods."""

    @patch.object(SonarrClient, '_make_request')
    def test_get_quality_profiles(self, mock_request):
        """Test getting quality profiles."""
        mock_request.return_value = [
            {"id": 1, "name": "HD-1080p"},
            {"id": 2, "name": "SD"}
        ]

        client = SonarrClient("http://localhost:8989", "key")
        result = client.get_quality_profiles()

        assert len(result) == 2
        assert result[0]["name"] == "HD-1080p"

    @patch.object(SonarrClient, 'get_quality_profiles')
    def test_get_quality_profile_id_found(self, mock_profiles):
        """Test getting profile ID by name."""
        mock_profiles.return_value = [
            {"id": 1, "name": "HD-1080p"},
            {"id": 2, "name": "SD"}
        ]

        client = SonarrClient("http://localhost:8989", "key")
        result = client.get_quality_profile_id("HD-1080p")

        assert result == 1

    @patch.object(SonarrClient, 'get_quality_profiles')
    def test_get_quality_profile_id_not_found(self, mock_profiles):
        """Test getting profile ID returns None when not found."""
        mock_profiles.return_value = [
            {"id": 1, "name": "HD-1080p"}
        ]

        client = SonarrClient("http://localhost:8989", "key")
        result = client.get_quality_profile_id("Ultra-HD")

        assert result is None

    @patch.object(SonarrClient, 'get_quality_profiles')
    def test_get_quality_profile_id_case_insensitive(self, mock_profiles):
        """Test profile lookup is case insensitive."""
        mock_profiles.return_value = [
            {"id": 1, "name": "HD-1080p"}
        ]

        client = SonarrClient("http://localhost:8989", "key")
        result = client.get_quality_profile_id("hd-1080p")

        assert result == 1


class TestSonarrClientTags:
    """Tests for tag methods."""

    @patch.object(SonarrClient, '_make_request')
    def test_get_tags(self, mock_request):
        """Test getting tags."""
        mock_request.return_value = [
            {"id": 1, "label": "Curatarr"}
        ]

        client = SonarrClient("http://localhost:8989", "key")
        result = client.get_tags()

        assert len(result) == 1
        assert result[0]["label"] == "Curatarr"

    @patch.object(SonarrClient, 'get_tags')
    @patch.object(SonarrClient, '_make_request')
    def test_get_or_create_tag_exists(self, mock_request, mock_tags):
        """Test get_or_create returns existing tag ID."""
        mock_tags.return_value = [
            {"id": 5, "label": "Curatarr"}
        ]

        client = SonarrClient("http://localhost:8989", "key")
        result = client.get_or_create_tag("Curatarr")

        assert result == 5
        mock_request.assert_not_called()

    @patch.object(SonarrClient, 'get_tags')
    @patch.object(SonarrClient, '_make_request')
    def test_get_or_create_tag_creates(self, mock_request, mock_tags):
        """Test get_or_create creates new tag."""
        mock_tags.return_value = []
        mock_request.return_value = {"id": 10, "label": "NewTag"}

        client = SonarrClient("http://localhost:8989", "key")
        result = client.get_or_create_tag("NewTag")

        assert result == 10
        mock_request.assert_called_with("POST", "tag", data={"label": "NewTag"})


class TestSonarrClientAddSeries:
    """Tests for add_series method."""

    @patch.object(SonarrClient, '_make_request')
    def test_add_series_basic(self, mock_request):
        """Test adding a series with basic options."""
        mock_request.return_value = {"id": 123, "title": "Test Show"}

        client = SonarrClient("http://localhost:8989", "key")
        result = client.add_series(
            tvdb_id=12345,
            title="Test Show",
            root_folder_path="/tv",
            quality_profile_id=1
        )

        assert result["title"] == "Test Show"
        mock_request.assert_called_once()

        call_args = mock_request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "series"
        assert call_args[1]["data"]["tvdbId"] == 12345
        assert call_args[1]["data"]["title"] == "Test Show"

    @patch.object(SonarrClient, '_make_request')
    def test_add_series_with_tags(self, mock_request):
        """Test adding a series with tags."""
        mock_request.return_value = {"id": 123}

        client = SonarrClient("http://localhost:8989", "key")
        client.add_series(
            tvdb_id=12345,
            title="Test Show",
            root_folder_path="/tv",
            quality_profile_id=1,
            tag_ids=[1, 2, 3]
        )

        call_args = mock_request.call_args
        assert call_args[1]["data"]["tags"] == [1, 2, 3]


class TestCreateSonarrClient:
    """Tests for create_sonarr_client factory function."""

    def test_returns_none_when_disabled(self):
        """Test returns None when Sonarr disabled."""
        config = {"sonarr": {"enabled": False}}
        result = create_sonarr_client(config)
        assert result is None

    def test_returns_none_when_no_config(self):
        """Test returns None when no Sonarr config."""
        config = {}
        result = create_sonarr_client(config)
        assert result is None

    def test_returns_none_when_placeholder_key(self):
        """Test returns None when API key is placeholder."""
        config = {
            "sonarr": {
                "enabled": True,
                "url": "http://localhost:8989",
                "api_key": "YOUR_SONARR_API_KEY"
            }
        }
        result = create_sonarr_client(config)
        assert result is None

    def test_returns_client_when_configured(self):
        """Test returns SonarrClient when properly configured."""
        config = {
            "sonarr": {
                "enabled": True,
                "url": "http://localhost:8989",
                "api_key": "real_api_key"
            }
        }
        result = create_sonarr_client(config)
        assert isinstance(result, SonarrClient)
        assert result.url == "http://localhost:8989"
        assert result.api_key == "real_api_key"
