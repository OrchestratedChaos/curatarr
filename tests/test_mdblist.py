"""Tests for utils/mdblist.py - MDBList API client."""

import pytest
from unittest.mock import Mock, patch
import requests

from utils.mdblist import (
    MDBListClient,
    MDBListAPIError,
    create_mdblist_client,
    MDBLIST_RATE_LIMIT_DELAY,
    MDBLIST_REQUEST_TIMEOUT,
    MDBLIST_API_BASE,
)


class TestMDBListClientInit:
    """Tests for MDBListClient initialization."""

    def test_init_with_api_key(self):
        """Test initialization with API key."""
        client = MDBListClient(api_key="test_api_key")
        assert client.api_key == "test_api_key"
        assert client._last_request_time == 0
        assert client._lists_cache is None

    def test_init_initializes_rate_limit_state(self):
        """Test rate limit state is initialized."""
        client = MDBListClient("key")
        assert client._last_request_time == 0


class TestMDBListClientRateLimit:
    """Tests for rate limiting."""

    @patch('utils.mdblist.time.sleep')
    @patch('utils.mdblist.time.time')
    def test_rate_limit_sleeps_when_needed(self, mock_time, mock_sleep):
        """Test rate limiting enforces delay between requests."""
        client = MDBListClient("key")
        client._last_request_time = 100.0

        # First call returns current time, second updates last_request_time
        mock_time.side_effect = [100.05, 100.1]  # 0.05s since last request

        client._rate_limit()

        # Should sleep for remaining time (0.1 - 0.05 = 0.05)
        mock_sleep.assert_called_once()
        sleep_time = mock_sleep.call_args[0][0]
        assert sleep_time == pytest.approx(MDBLIST_RATE_LIMIT_DELAY - 0.05, abs=0.01)

    @patch('utils.mdblist.time.sleep')
    @patch('utils.mdblist.time.time')
    def test_rate_limit_no_sleep_when_enough_time_passed(self, mock_time, mock_sleep):
        """Test no sleep when enough time has passed."""
        client = MDBListClient("key")
        client._last_request_time = 100.0

        mock_time.return_value = 100.2  # 0.2s since last request

        client._rate_limit()

        mock_sleep.assert_not_called()


class TestMDBListClientMakeRequest:
    """Tests for API request handling."""

    @patch('utils.mdblist.requests.request')
    def test_successful_request(self, mock_request):
        """Test successful API request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        mock_request.return_value = mock_response

        client = MDBListClient("key")
        result = client._make_request("GET", "lists/user")

        assert result == {"status": "ok"}
        mock_request.assert_called_once()

    @patch('utils.mdblist.requests.request')
    def test_api_key_in_params(self, mock_request):
        """Test API key is added to query params."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_request.return_value = mock_response

        client = MDBListClient("my_api_key")
        client._make_request("GET", "lists/user")

        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["params"]["apikey"] == "my_api_key"

    @patch('utils.mdblist.requests.request')
    def test_unauthorized_raises_error(self, mock_request):
        """Test 401 raises MDBListAPIError."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_request.return_value = mock_response

        client = MDBListClient("bad_key")

        with pytest.raises(MDBListAPIError, match="Invalid API key"):
            client._make_request("GET", "lists/user")

    @patch('utils.mdblist.requests.request')
    def test_404_returns_none(self, mock_request):
        """Test 404 returns None."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_request.return_value = mock_response

        client = MDBListClient("key")
        result = client._make_request("GET", "lists/99999")

        assert result is None

    @patch('utils.mdblist.requests.request')
    def test_204_returns_none(self, mock_request):
        """Test 204 No Content returns None."""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_request.return_value = mock_response

        client = MDBListClient("key")
        result = client._make_request("DELETE", "lists/1/items")

        assert result is None

    @patch('utils.mdblist.requests.request')
    def test_error_response_raises_api_error(self, mock_request):
        """Test error responses raise MDBListAPIError."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"
        mock_response.json.return_value = {"error": "Invalid list"}
        mock_request.return_value = mock_response

        client = MDBListClient("key")

        with pytest.raises(MDBListAPIError, match="Invalid list"):
            client._make_request("POST", "lists/user/add")

    @patch('utils.mdblist.requests.request')
    def test_timeout_raises_api_error(self, mock_request):
        """Test timeout raises MDBListAPIError."""
        mock_request.side_effect = requests.exceptions.Timeout()

        client = MDBListClient("key")

        with pytest.raises(MDBListAPIError, match="timeout"):
            client._make_request("GET", "lists/user")

    @patch('utils.mdblist.requests.request')
    def test_connection_error_raises_api_error(self, mock_request):
        """Test connection error raises MDBListAPIError."""
        mock_request.side_effect = requests.exceptions.ConnectionError()

        client = MDBListClient("key")

        with pytest.raises(MDBListAPIError, match="Could not connect"):
            client._make_request("GET", "lists/user")


class TestMDBListClientTestConnection:
    """Tests for connection testing."""

    @patch.object(MDBListClient, '_make_request')
    def test_connection_success(self, mock_request):
        """Test successful connection."""
        mock_request.return_value = [{"id": 1, "name": "Test"}]

        client = MDBListClient("key")
        result = client.test_connection()

        assert result is True
        mock_request.assert_called_once_with("GET", "lists/user")

    @patch.object(MDBListClient, '_make_request')
    def test_connection_returns_false_on_none(self, mock_request):
        """Test connection returns False when None returned."""
        mock_request.return_value = None

        client = MDBListClient("key")
        result = client.test_connection()

        assert result is False


class TestMDBListClientGetLists:
    """Tests for getting user's lists."""

    @patch.object(MDBListClient, '_make_request')
    def test_get_lists(self, mock_request):
        """Test fetching all user lists."""
        mock_request.return_value = [
            {"id": 1, "name": "Movies", "slug": "movies"},
            {"id": 2, "name": "TV Shows", "slug": "tv-shows"},
        ]

        client = MDBListClient("key")
        result = client.get_lists()

        assert len(result) == 2
        assert result[0]["name"] == "Movies"
        mock_request.assert_called_once_with("GET", "lists/user")

    @patch.object(MDBListClient, '_make_request')
    def test_get_lists_caches_result(self, mock_request):
        """Test that lists are cached."""
        mock_request.return_value = [{"id": 1, "name": "Test"}]

        client = MDBListClient("key")
        client.get_lists()
        client.get_lists()  # Second call

        # Should only call API once
        assert mock_request.call_count == 1

    @patch.object(MDBListClient, '_make_request')
    def test_get_lists_returns_empty_on_none(self, mock_request):
        """Test get_lists returns empty list if API returns None."""
        mock_request.return_value = None

        client = MDBListClient("key")
        result = client.get_lists()

        assert result == []


class TestMDBListClientGetListByName:
    """Tests for finding list by name."""

    @patch.object(MDBListClient, 'get_lists')
    def test_find_list_case_insensitive(self, mock_get_lists):
        """Test list lookup is case-insensitive."""
        mock_get_lists.return_value = [
            {"id": 1, "name": "Curatarr Movies", "slug": "curatarr-movies"},
        ]

        client = MDBListClient("key")
        result = client.get_list_by_name("CURATARR MOVIES")

        assert result is not None
        assert result["id"] == 1

    @patch.object(MDBListClient, 'get_lists')
    def test_find_list_not_found(self, mock_get_lists):
        """Test list lookup returns None when not found."""
        mock_get_lists.return_value = [
            {"id": 1, "name": "Other List"},
        ]

        client = MDBListClient("key")
        result = client.get_list_by_name("Nonexistent")

        assert result is None


class TestMDBListClientCreateList:
    """Tests for creating lists."""

    @patch.object(MDBListClient, '_make_request')
    def test_create_list(self, mock_request):
        """Test creating a new list."""
        mock_request.return_value = {
            "id": 123,
            "name": "New List",
            "slug": "new-list",
            "url": "https://mdblist.com/lists/user/new-list"
        }

        client = MDBListClient("key")
        client._lists_cache = [{"id": 1, "name": "Old"}]  # Pre-existing cache
        result = client.create_list("New List")

        assert result["id"] == 123
        mock_request.assert_called_once_with("POST", "lists/user/add", data={"name": "New List"})
        # Cache should be invalidated
        assert client._lists_cache is None


class TestMDBListClientGetOrCreateList:
    """Tests for get_or_create_list."""

    @patch.object(MDBListClient, 'get_list_by_name')
    @patch.object(MDBListClient, 'create_list')
    def test_returns_existing_list(self, mock_create, mock_get):
        """Test returns existing list without creating."""
        mock_get.return_value = {"id": 1, "name": "Existing"}

        client = MDBListClient("key")
        result = client.get_or_create_list("Existing")

        assert result["id"] == 1
        mock_create.assert_not_called()

    @patch.object(MDBListClient, 'get_list_by_name')
    @patch.object(MDBListClient, 'create_list')
    def test_creates_new_list(self, mock_create, mock_get):
        """Test creates new list when not found."""
        mock_get.return_value = None
        mock_create.return_value = {"id": 2, "name": "New"}

        client = MDBListClient("key")
        result = client.get_or_create_list("New")

        assert result["id"] == 2
        mock_create.assert_called_once_with("New")


class TestMDBListClientAddItems:
    """Tests for adding items to lists."""

    @patch.object(MDBListClient, '_make_request')
    def test_add_movies(self, mock_request):
        """Test adding movies to list."""
        mock_request.return_value = {"added": 5, "existing": 2, "not_found": 0}

        client = MDBListClient("key")
        result = client.add_items(123, movies=[1, 2, 3, 4, 5])

        assert result["added"] == 5
        call_data = mock_request.call_args[1]["data"]
        assert len(call_data["movies"]) == 5
        assert call_data["movies"][0] == {"tmdb": 1}

    @patch.object(MDBListClient, '_make_request')
    def test_add_shows(self, mock_request):
        """Test adding shows to list."""
        mock_request.return_value = {"added": 3, "existing": 0, "not_found": 0}

        client = MDBListClient("key")
        result = client.add_items(123, shows=[10, 20, 30])

        assert result["added"] == 3
        call_data = mock_request.call_args[1]["data"]
        assert len(call_data["shows"]) == 3
        assert call_data["shows"][0] == {"tmdb": 10}

    @patch.object(MDBListClient, '_make_request')
    def test_add_movies_and_shows(self, mock_request):
        """Test adding both movies and shows."""
        mock_request.return_value = {"added": 4, "existing": 0, "not_found": 0}

        client = MDBListClient("key")
        result = client.add_items(123, movies=[1, 2], shows=[10, 20])

        call_data = mock_request.call_args[1]["data"]
        assert len(call_data["movies"]) == 2
        assert len(call_data["shows"]) == 2

    def test_add_empty_items_returns_zero_counts(self):
        """Test adding no items returns zeros."""
        client = MDBListClient("key")
        result = client.add_items(123)

        assert result == {"added": 0, "existing": 0, "not_found": 0}


class TestMDBListClientClearList:
    """Tests for clearing lists."""

    @patch.object(MDBListClient, '_make_request')
    def test_clear_empty_list(self, mock_request):
        """Test clearing an empty list."""
        mock_request.return_value = None  # No items

        client = MDBListClient("key")
        result = client.clear_list(123)

        assert result is True
        # Should only call GET, not POST remove
        assert mock_request.call_count == 1

    @patch.object(MDBListClient, '_make_request')
    def test_clear_list_with_items(self, mock_request):
        """Test clearing a list with items."""
        # First call returns items, second call removes them
        mock_request.side_effect = [
            [
                {"mediatype": "movie", "imdb_id": "tt1234567"},
                {"mediatype": "show", "imdb_id": "tt7654321"},
            ],
            None  # Remove response
        ]

        client = MDBListClient("key")
        result = client.clear_list(123)

        assert result is True
        assert mock_request.call_count == 2

        # Check remove request
        remove_call = mock_request.call_args_list[1]
        assert remove_call[0][0] == "POST"
        assert "items/remove" in remove_call[0][1]


class TestCreateMDBListClient:
    """Tests for factory function."""

    def test_returns_none_when_disabled(self):
        """Test returns None when MDBList disabled."""
        config = {"mdblist": {"enabled": False}}
        result = create_mdblist_client(config)
        assert result is None

    def test_returns_none_when_no_config(self):
        """Test returns None when no mdblist config."""
        config = {}
        result = create_mdblist_client(config)
        assert result is None

    def test_returns_none_when_no_api_key(self):
        """Test returns None when no API key."""
        config = {"mdblist": {"enabled": True}}
        result = create_mdblist_client(config)
        assert result is None

    def test_returns_none_for_placeholder_key(self):
        """Test returns None for placeholder API key."""
        config = {"mdblist": {"enabled": True, "api_key": "YOUR_MDBLIST_API_KEY"}}
        result = create_mdblist_client(config)
        assert result is None

    def test_creates_client_with_valid_config(self):
        """Test creates client with valid configuration."""
        config = {
            "mdblist": {
                "enabled": True,
                "api_key": "real_api_key"
            }
        }
        result = create_mdblist_client(config)

        assert result is not None
        assert isinstance(result, MDBListClient)
        assert result.api_key == "real_api_key"


class TestMDBListConstants:
    """Tests for module constants."""

    def test_rate_limit_delay_is_positive(self):
        """Test rate limit delay is positive."""
        assert MDBLIST_RATE_LIMIT_DELAY > 0

    def test_request_timeout_is_reasonable(self):
        """Test request timeout is reasonable."""
        assert 10 <= MDBLIST_REQUEST_TIMEOUT <= 60

    def test_api_base_is_https(self):
        """Test API base URL uses HTTPS."""
        assert MDBLIST_API_BASE.startswith("https://")
