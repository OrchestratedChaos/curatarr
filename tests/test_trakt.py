"""Tests for utils/trakt.py - Trakt API client."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import time

from utils.trakt import (
    TraktClient,
    TraktAuthError,
    TraktAPIError,
    create_trakt_client,
    TRAKT_RATE_LIMIT_DELAY,
)


class TestTraktClientInit:
    """Tests for TraktClient initialization."""

    def test_init_with_credentials(self):
        """Test initialization with client credentials."""
        client = TraktClient(
            client_id="test_id",
            client_secret="test_secret"
        )
        assert client.client_id == "test_id"
        assert client.client_secret == "test_secret"
        assert client.access_token is None
        assert client.refresh_token is None

    def test_init_with_tokens(self):
        """Test initialization with existing tokens."""
        client = TraktClient(
            client_id="test_id",
            client_secret="test_secret",
            access_token="access123",
            refresh_token="refresh456"
        )
        assert client.access_token == "access123"
        assert client.refresh_token == "refresh456"

    def test_is_authenticated_false(self):
        """Test is_authenticated when no token."""
        client = TraktClient("id", "secret")
        assert client.is_authenticated is False

    def test_is_authenticated_true(self):
        """Test is_authenticated when token exists."""
        client = TraktClient("id", "secret", access_token="token")
        assert client.is_authenticated is True


class TestTraktClientHeaders:
    """Tests for header generation."""

    def test_headers_unauthenticated(self):
        """Test headers without authentication."""
        client = TraktClient("test_id", "secret")
        headers = client._get_headers(authenticated=False)

        assert headers["Content-Type"] == "application/json"
        assert headers["trakt-api-version"] == "2"
        assert headers["trakt-api-key"] == "test_id"
        assert "Authorization" not in headers

    def test_headers_authenticated(self):
        """Test headers with authentication."""
        client = TraktClient("test_id", "secret", access_token="token123")
        headers = client._get_headers(authenticated=True)

        assert headers["Authorization"] == "Bearer token123"
        assert headers["trakt-api-key"] == "test_id"

    def test_headers_authenticated_no_token(self):
        """Test authenticated headers when no token available."""
        client = TraktClient("test_id", "secret")
        headers = client._get_headers(authenticated=True)

        assert "Authorization" not in headers


class TestTraktClientRateLimiting:
    """Tests for rate limiting."""

    def test_rate_limit_delay(self):
        """Test that rate limiting adds delay between requests."""
        client = TraktClient("id", "secret")

        # First call should not delay
        start = time.time()
        client._rate_limit()
        first_duration = time.time() - start
        assert first_duration < 0.1  # Should be nearly instant

        # Immediate second call should delay
        start = time.time()
        client._rate_limit()
        second_duration = time.time() - start
        assert second_duration >= TRAKT_RATE_LIMIT_DELAY * 0.9  # Allow some tolerance


class TestTraktClientMakeRequest:
    """Tests for API request handling."""

    @patch('utils.trakt.requests.request')
    def test_successful_request(self, mock_request):
        """Test successful API request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test"}
        mock_request.return_value = mock_response

        client = TraktClient("id", "secret", access_token="token")
        result = client._make_request("GET", "/test")

        assert result == {"data": "test"}
        mock_request.assert_called_once()

    @patch('utils.trakt.requests.request')
    def test_204_no_content(self, mock_request):
        """Test 204 No Content response."""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_request.return_value = mock_response

        client = TraktClient("id", "secret", access_token="token")
        result = client._make_request("DELETE", "/test")

        assert result is None

    @patch('utils.trakt.requests.request')
    def test_rate_limit_429_retry(self, mock_request):
        """Test 429 rate limit triggers retry."""
        rate_limited = Mock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "1"}

        success = Mock()
        success.status_code = 200
        success.json.return_value = {"data": "success"}

        mock_request.side_effect = [rate_limited, success]

        client = TraktClient("id", "secret", access_token="token")
        result = client._make_request("GET", "/test")

        assert result == {"data": "success"}
        assert mock_request.call_count == 2

    @patch('utils.trakt.requests.request')
    def test_api_error_raises_exception(self, mock_request):
        """Test API error raises TraktAPIError."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Server Error"
        mock_request.return_value = mock_response

        client = TraktClient("id", "secret", access_token="token")

        with pytest.raises(TraktAPIError) as exc_info:
            client._make_request("GET", "/test")

        assert "500" in str(exc_info.value)

    @patch('utils.trakt.requests.request')
    def test_401_triggers_token_refresh(self, mock_request):
        """Test 401 triggers token refresh attempt."""
        unauthorized = Mock()
        unauthorized.status_code = 401
        unauthorized.text = "Unauthorized"

        mock_request.return_value = unauthorized

        client = TraktClient("id", "secret", access_token="token", refresh_token="refresh")

        with patch.object(client, '_refresh_access_token', return_value=False):
            with pytest.raises(TraktAuthError):
                client._make_request("GET", "/test")


class TestTraktClientDeviceAuth:
    """Tests for device authentication flow."""

    @patch('utils.trakt.requests.post')
    def test_get_device_code_success(self, mock_post):
        """Test successful device code request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "device_code": "device123",
            "user_code": "USER123",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5
        }
        mock_post.return_value = mock_response

        client = TraktClient("id", "secret")
        result = client.get_device_code()

        assert result["device_code"] == "device123"
        assert result["user_code"] == "USER123"

    @patch('utils.trakt.requests.post')
    def test_get_device_code_failure(self, mock_post):
        """Test device code request failure."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_post.return_value = mock_response

        client = TraktClient("id", "secret")

        with pytest.raises(TraktAuthError):
            client.get_device_code()

    @patch('utils.trakt.requests.post')
    def test_poll_for_token_success(self, mock_post):
        """Test successful token poll."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "access123",
            "refresh_token": "refresh456"
        }
        mock_post.return_value = mock_response

        callback = Mock()
        client = TraktClient("id", "secret", token_callback=callback)
        result = client.poll_for_token("device_code", interval=0, expires_in=10)

        assert result is True
        assert client.access_token == "access123"
        assert client.refresh_token == "refresh456"
        callback.assert_called_once_with("access123", "refresh456")

    @patch('utils.trakt.requests.post')
    def test_poll_for_token_pending(self, mock_post):
        """Test poll returns pending then success."""
        pending = Mock()
        pending.status_code = 400  # Still waiting

        success = Mock()
        success.status_code = 200
        success.json.return_value = {
            "access_token": "access",
            "refresh_token": "refresh"
        }

        mock_post.side_effect = [pending, success]

        client = TraktClient("id", "secret")
        result = client.poll_for_token("device_code", interval=0, expires_in=10)

        assert result is True
        assert mock_post.call_count == 2

    @patch('utils.trakt.requests.post')
    def test_poll_for_token_denied(self, mock_post):
        """Test poll when user denies."""
        mock_response = Mock()
        mock_response.status_code = 418  # User denied
        mock_post.return_value = mock_response

        client = TraktClient("id", "secret")
        result = client.poll_for_token("device_code", interval=0, expires_in=10)

        assert result is False


class TestTraktClientTokenRefresh:
    """Tests for token refresh."""

    @patch('utils.trakt.requests.post')
    def test_refresh_access_token_success(self, mock_post):
        """Test successful token refresh."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access",
            "refresh_token": "new_refresh"
        }
        mock_post.return_value = mock_response

        callback = Mock()
        client = TraktClient("id", "secret", refresh_token="old_refresh", token_callback=callback)
        result = client._refresh_access_token()

        assert result is True
        assert client.access_token == "new_access"
        assert client.refresh_token == "new_refresh"
        callback.assert_called_once_with("new_access", "new_refresh")

    @patch('utils.trakt.requests.post')
    def test_refresh_access_token_failure(self, mock_post):
        """Test failed token refresh."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        client = TraktClient("id", "secret", refresh_token="old_refresh")
        result = client._refresh_access_token()

        assert result is False

    def test_refresh_access_token_no_refresh_token(self):
        """Test refresh fails when no refresh token."""
        client = TraktClient("id", "secret")
        result = client._refresh_access_token()

        assert result is False


class TestCreateTraktClient:
    """Tests for create_trakt_client factory function."""

    def test_disabled_returns_none(self):
        """Test returns None when Trakt disabled."""
        config = {"trakt": {"enabled": False}}
        result = create_trakt_client(config)
        assert result is None

    def test_no_trakt_config_returns_none(self):
        """Test returns None when no Trakt config."""
        config = {}
        result = create_trakt_client(config)
        assert result is None

    def test_missing_credentials_returns_none(self):
        """Test returns None when credentials missing."""
        config = {"trakt": {"enabled": True, "client_id": None}}
        result = create_trakt_client(config)
        assert result is None

    def test_valid_config_returns_client(self):
        """Test returns client with valid config."""
        config = {
            "trakt": {
                "enabled": True,
                "client_id": "test_id",
                "client_secret": "test_secret",
                "access_token": "token",
                "refresh_token": "refresh"
            }
        }
        result = create_trakt_client(config)

        assert result is not None
        assert isinstance(result, TraktClient)
        assert result.client_id == "test_id"
        assert result.access_token == "token"


class TestRevokeToken:
    """Tests for revoke_token method."""

    def test_revoke_no_token(self):
        """Test revoke returns True when no token to revoke."""
        client = TraktClient("id", "secret")
        result = client.revoke_token()
        assert result is True

    @patch('utils.trakt.requests.post')
    def test_revoke_success(self, mock_post):
        """Test successful token revocation."""
        mock_post.return_value = Mock(status_code=200)

        client = TraktClient("id", "secret", access_token="token", refresh_token="refresh")
        result = client.revoke_token()

        assert result is True
        assert client.access_token is None
        assert client.refresh_token is None
        mock_post.assert_called_once()

    @patch('utils.trakt.requests.post')
    def test_revoke_failure(self, mock_post):
        """Test token revocation failure."""
        mock_post.return_value = Mock(status_code=500)

        client = TraktClient("id", "secret", access_token="token")
        result = client.revoke_token()

        assert result is False

    @patch('utils.trakt.requests.post')
    def test_revoke_request_exception(self, mock_post):
        """Test revoke handles request exception."""
        import requests
        mock_post.side_effect = requests.RequestException("Network error")

        client = TraktClient("id", "secret", access_token="token")
        result = client.revoke_token()

        assert result is False


class TestTraktClientUserInfo:
    """Tests for user info methods."""

    @patch('utils.trakt.requests.request')
    def test_get_user_settings(self, mock_request):
        """Test get_user_settings."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "user": {"username": "testuser"}
        }
        mock_request.return_value = mock_response

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_user_settings()

        assert result["user"]["username"] == "testuser"

    @patch('utils.trakt.requests.request')
    def test_get_username(self, mock_request):
        """Test get_username."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "user": {"username": "testuser"}
        }
        mock_request.return_value = mock_response

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_username()

        assert result == "testuser"

    @patch('utils.trakt.requests.request')
    def test_get_username_error(self, mock_request):
        """Test get_username returns None on error."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_request.return_value = mock_response

        client = TraktClient("id", "secret")
        result = client.get_username()

        assert result is None


class TestTraktClientListManagement:
    """Tests for list management methods."""

    @patch('utils.trakt.requests.request')
    def test_get_lists(self, mock_request):
        """Test getting user lists."""
        # First call returns user settings, second returns lists
        settings_response = Mock()
        settings_response.status_code = 200
        settings_response.json.return_value = {"user": {"username": "testuser"}}

        lists_response = Mock()
        lists_response.status_code = 200
        lists_response.json.return_value = [
            {"name": "List 1", "ids": {"slug": "list-1"}},
            {"name": "List 2", "ids": {"slug": "list-2"}}
        ]

        mock_request.side_effect = [settings_response, lists_response]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_lists()

        assert len(result) == 2
        assert result[0]["name"] == "List 1"

    @patch('utils.trakt.requests.request')
    def test_get_list_not_found(self, mock_request):
        """Test getting a list that doesn't exist."""
        settings_response = Mock()
        settings_response.status_code = 200
        settings_response.json.return_value = {"user": {"username": "testuser"}}

        not_found_response = Mock()
        not_found_response.status_code = 404
        not_found_response.text = "Not Found"

        mock_request.side_effect = [settings_response, not_found_response]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_list("nonexistent")

        assert result is None

    @patch('utils.trakt.requests.request')
    def test_create_list(self, mock_request):
        """Test creating a new list."""
        settings_response = Mock()
        settings_response.status_code = 200
        settings_response.json.return_value = {"user": {"username": "testuser"}}

        create_response = Mock()
        create_response.status_code = 200
        create_response.json.return_value = {
            "name": "New List",
            "ids": {"slug": "new-list"}
        }

        mock_request.side_effect = [settings_response, create_response]

        client = TraktClient("id", "secret", access_token="token")
        result = client.create_list("New List", description="Test")

        assert result["name"] == "New List"
        assert result["ids"]["slug"] == "new-list"

    @patch('utils.trakt.requests.request')
    def test_add_to_list(self, mock_request):
        """Test adding items to a list."""
        settings_response = Mock()
        settings_response.status_code = 200
        settings_response.json.return_value = {"user": {"username": "testuser"}}

        add_response = Mock()
        add_response.status_code = 200
        add_response.json.return_value = {
            "added": {"movies": 2, "shows": 1},
            "existing": {"movies": 0, "shows": 0},
            "not_found": {"movies": [], "shows": []}
        }

        mock_request.side_effect = [settings_response, add_response]

        client = TraktClient("id", "secret", access_token="token")
        result = client.add_to_list(
            "my-list",
            movies=[{"ids": {"imdb": "tt123"}}, {"ids": {"imdb": "tt456"}}],
            shows=[{"ids": {"imdb": "tt789"}}]
        )

        assert result["added"]["movies"] == 2
        assert result["added"]["shows"] == 1

    @patch('utils.trakt.requests.request')
    def test_remove_from_list(self, mock_request):
        """Test removing items from a list."""
        settings_response = Mock()
        settings_response.status_code = 200
        settings_response.json.return_value = {"user": {"username": "testuser"}}

        remove_response = Mock()
        remove_response.status_code = 200
        remove_response.json.return_value = {
            "deleted": {"movies": 1, "shows": 0},
            "not_found": {"movies": [], "shows": []}
        }

        mock_request.side_effect = [settings_response, remove_response]

        client = TraktClient("id", "secret", access_token="token")
        result = client.remove_from_list(
            "my-list",
            movies=[{"ids": {"imdb": "tt123"}}]
        )

        assert result["deleted"]["movies"] == 1

    def test_add_to_list_empty(self):
        """Test adding empty lists returns immediately."""
        client = TraktClient("id", "secret", access_token="token")
        result = client.add_to_list("my-list")

        assert result == {"added": {"movies": 0, "shows": 0}}

    @patch('utils.trakt.requests.request')
    def test_add_to_list_no_username(self, mock_request):
        """Test add_to_list raises error when no username."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {}}  # No username
        mock_request.return_value = settings

        client = TraktClient("id", "secret", access_token="token")
        with pytest.raises(TraktAuthError):
            client.add_to_list("my-list", movies=[{"ids": {"imdb": "tt123"}}])

    @patch('utils.trakt.requests.request')
    def test_remove_from_list_no_username(self, mock_request):
        """Test remove_from_list raises error when no username."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {}}  # No username
        mock_request.return_value = settings

        client = TraktClient("id", "secret", access_token="token")
        with pytest.raises(TraktAuthError):
            client.remove_from_list("my-list", movies=[{"ids": {"imdb": "tt123"}}])

    def test_remove_from_list_empty(self):
        """Test removing empty lists returns immediately."""
        client = TraktClient("id", "secret", access_token="token")
        result = client.remove_from_list("my-list")

        assert result == {"deleted": {"movies": 0, "shows": 0}}

    @patch('utils.trakt.requests.request')
    def test_delete_list_success(self, mock_request):
        """Test successful list deletion."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        delete_resp = Mock(status_code=204)
        delete_resp.json.return_value = None

        mock_request.side_effect = [settings, delete_resp]

        client = TraktClient("id", "secret", access_token="token")
        result = client.delete_list("my-list")

        assert result is True

    @patch('utils.trakt.requests.request')
    def test_delete_list_no_username(self, mock_request):
        """Test delete list fails when no username."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {}}  # No username

        mock_request.return_value = settings

        client = TraktClient("id", "secret", access_token="token")
        result = client.delete_list("my-list")

        assert result is False

    @patch('utils.trakt.requests.request')
    def test_delete_list_api_error(self, mock_request):
        """Test delete list handles API error."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        error_resp = Mock(status_code=404, text="Not found")

        mock_request.side_effect = [settings, error_resp]

        client = TraktClient("id", "secret", access_token="token")
        result = client.delete_list("nonexistent")

        assert result is False

    @patch('utils.trakt.requests.request')
    def test_get_or_create_finds_by_name(self, mock_request):
        """Test get_or_create_list finds list by name when slug lookup fails."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        # First lookup by slug fails
        not_found = Mock(status_code=404, text="Not found")

        # get_lists returns list with matching name
        lists_resp = Mock(status_code=200)
        lists_resp.json.return_value = [
            {"name": "My List", "ids": {"slug": "different-slug"}}
        ]

        mock_request.side_effect = [settings, not_found, settings, lists_resp]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_or_create_list("My List")

        assert result is not None
        assert result["name"] == "My List"


class TestTraktClientSyncList:
    """Tests for list sync functionality."""

    @patch('utils.trakt.requests.request')
    def test_sync_list_creates_new(self, mock_request):
        """Test syncing to a new list."""
        # Mock responses in order: get_username, get_list (404), get_lists, create_list,
        # get_list_items, add_to_list
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        not_found = Mock(status_code=404, text="Not Found")

        empty_lists = Mock(status_code=200)
        empty_lists.json.return_value = []

        created = Mock(status_code=200)
        created.json.return_value = {"name": "Test", "ids": {"slug": "test"}}

        empty_items = Mock(status_code=200)
        empty_items.json.return_value = []

        added = Mock(status_code=200)
        added.json.return_value = {"added": {"movies": 2, "shows": 0}}

        mock_request.side_effect = [
            settings,  # get_username for get_or_create_list
            not_found,  # get_list (not found)
            settings,  # get_username for get_lists
            empty_lists,  # get_lists
            settings,  # get_username for create_list
            created,  # create_list
            settings,  # get_username for get_list_items
            empty_items,  # get_list_items
            settings,  # get_username for add_to_list
            added,  # add_to_list
        ]

        client = TraktClient("id", "secret", access_token="token")
        result = client.sync_list("Test", movies=["tt123", "tt456"])

        assert result["added"]["movies"] == 2

    @patch('utils.trakt.requests.request')
    def test_sync_list_clears_and_adds(self, mock_request):
        """Test syncing clears existing items before adding new ones."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        existing_list = Mock(status_code=200)
        existing_list.json.return_value = {"name": "Test", "ids": {"slug": "test"}}

        existing_items = Mock(status_code=200)
        existing_items.json.return_value = [
            {"type": "movie", "movie": {"ids": {"imdb": "tt000"}}}
        ]

        removed = Mock(status_code=200)
        removed.json.return_value = {"deleted": {"movies": 1, "shows": 0}}

        added = Mock(status_code=200)
        added.json.return_value = {"added": {"movies": 1, "shows": 0}}

        mock_request.side_effect = [
            settings,  # get_username for get_or_create_list
            existing_list,  # get_list
            settings,  # get_username for get_list_items
            existing_items,  # get_list_items (has old items)
            settings,  # get_username for remove_from_list
            removed,  # remove_from_list
            settings,  # get_username for add_to_list
            added,  # add_to_list
        ]

        client = TraktClient("id", "secret", access_token="token")
        result = client.sync_list("Test", movies=["tt123"])

        assert result["added"]["movies"] == 1


class TestTraktClientImport:
    """Tests for watch history and watchlist import methods."""

    @patch('utils.trakt.requests.request')
    def test_get_watched_movies(self, mock_request):
        """Test getting watched movies."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        watched = Mock(status_code=200)
        watched.json.return_value = [
            {"movie": {"title": "Movie 1", "ids": {"imdb": "tt123"}}},
            {"movie": {"title": "Movie 2", "ids": {"imdb": "tt456"}}}
        ]

        mock_request.side_effect = [settings, watched]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watched_movies()

        assert len(result) == 2
        assert result[0]["movie"]["title"] == "Movie 1"

    @patch('utils.trakt.requests.request')
    def test_get_watched_shows(self, mock_request):
        """Test getting watched shows."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        watched = Mock(status_code=200)
        watched.json.return_value = [
            {"show": {"title": "Show 1", "ids": {"imdb": "tt789"}}}
        ]

        mock_request.side_effect = [settings, watched]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watched_shows()

        assert len(result) == 1
        assert result[0]["show"]["title"] == "Show 1"

    @patch('utils.trakt.requests.request')
    def test_get_ratings(self, mock_request):
        """Test getting user ratings."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        ratings = Mock(status_code=200)
        ratings.json.return_value = [
            {"rating": 10, "movie": {"title": "Great Movie", "ids": {"imdb": "tt123"}}},
            {"rating": 5, "movie": {"title": "OK Movie", "ids": {"imdb": "tt456"}}}
        ]

        mock_request.side_effect = [settings, ratings]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_ratings("movies")

        assert len(result) == 2
        assert result[0]["rating"] == 10

    @patch('utils.trakt.requests.request')
    def test_get_watchlist(self, mock_request):
        """Test getting watchlist."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        watchlist = Mock(status_code=200)
        watchlist.json.return_value = [
            {"type": "movie", "movie": {"title": "Want to Watch", "ids": {"imdb": "tt111"}}},
            {"type": "show", "show": {"title": "Want to Watch Show", "ids": {"imdb": "tt222"}}}
        ]

        mock_request.side_effect = [settings, watchlist]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watchlist()

        assert len(result) == 2
        assert result[0]["type"] == "movie"

    @patch('utils.trakt.requests.request')
    def test_get_watch_history_imdb_ids(self, mock_request):
        """Test getting IMDB IDs from watch history."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        watched = Mock(status_code=200)
        watched.json.return_value = [
            {"movie": {"title": "Movie 1", "ids": {"imdb": "tt123"}}},
            {"movie": {"title": "Movie 2", "ids": {"imdb": "tt456"}}}
        ]

        mock_request.side_effect = [settings, watched]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watch_history_imdb_ids("movies")

        assert result == {"tt123", "tt456"}

    @patch('utils.trakt.requests.request')
    def test_get_watchlist_imdb_ids(self, mock_request):
        """Test getting IMDB IDs from watchlist."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        watchlist = Mock(status_code=200)
        watchlist.json.return_value = [
            {"type": "movie", "movie": {"ids": {"imdb": "tt111"}}},
            {"type": "show", "show": {"ids": {"imdb": "tt222"}}}
        ]

        mock_request.side_effect = [settings, watchlist]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watchlist_imdb_ids()

        assert result == {"tt111", "tt222"}

    def test_get_watched_movies_no_auth(self):
        """Test get_watched_movies returns empty when not authenticated."""
        client = TraktClient("id", "secret")
        result = client.get_watched_movies()
        assert result == []

    @patch('utils.trakt.requests.request')
    def test_get_watched_movies_api_error(self, mock_request):
        """Test get_watched_movies returns empty on API error."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        error = Mock(status_code=500, text="Server error")

        mock_request.side_effect = [settings, error]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watched_movies()
        assert result == []

    @patch('utils.trakt.requests.request')
    def test_get_watched_shows_api_error(self, mock_request):
        """Test get_watched_shows returns empty on API error."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        error = Mock(status_code=500, text="Server error")

        mock_request.side_effect = [settings, error]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watched_shows()
        assert result == []

    def test_get_watchlist_no_auth(self):
        """Test get_watchlist returns empty when not authenticated."""
        client = TraktClient("id", "secret")
        result = client.get_watchlist()
        assert result == []

    @patch('utils.trakt.requests.request')
    def test_get_watchlist_api_error(self, mock_request):
        """Test get_watchlist returns empty on API error."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}
        error = Mock(status_code=500, text="Server error")
        mock_request.side_effect = [settings, error]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watchlist()
        assert result == []

    @patch('utils.trakt.requests.request')
    def test_get_ratings_api_error(self, mock_request):
        """Test get_ratings returns empty on API error."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}
        error = Mock(status_code=500, text="Server error")
        mock_request.side_effect = [settings, error]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_ratings()
        assert result == []

    def test_get_ratings_no_auth(self):
        """Test get_ratings returns empty when not authenticated."""
        client = TraktClient("id", "secret")
        result = client.get_ratings()
        assert result == []

    def test_add_to_history_empty(self):
        """Test add_to_history returns early when no data."""
        client = TraktClient("id", "secret", access_token="token")
        result = client.add_to_history()
        assert result == {"added": {"movies": 0, "episodes": 0}}

    @patch('utils.trakt.requests.request')
    def test_get_watch_history_imdb_ids_shows(self, mock_request):
        """Test get_watch_history_imdb_ids for shows."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}
        shows = Mock(status_code=200)
        shows.json.return_value = [
            {"show": {"ids": {"imdb": "tt111"}}},
            {"show": {"ids": {"imdb": "tt222"}}},
            {"show": {"ids": {}}}  # Missing IMDB
        ]
        mock_request.side_effect = [settings, shows]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watch_history_imdb_ids(media_type='shows')
        assert result == {"tt111", "tt222"}


class TestLoadTraktEnhanceCache:
    """Tests for load_trakt_enhance_cache function."""

    def test_returns_empty_when_file_not_exists(self, tmp_path):
        """Test returns empty sets when cache file doesn't exist."""
        from utils.trakt import load_trakt_enhance_cache
        result = load_trakt_enhance_cache(str(tmp_path))
        assert result == {'movie_ids': set(), 'show_ids': set()}

    def test_loads_valid_cache(self, tmp_path):
        """Test loads valid cache file."""
        from utils.trakt import load_trakt_enhance_cache, TRAKT_ENHANCE_CACHE_VERSION
        import json

        cache_path = tmp_path / 'trakt_enhance_cache.json'
        cache_data = {
            'version': TRAKT_ENHANCE_CACHE_VERSION,
            'movie_ids': ['tt1234567', 'tt7654321'],
            'show_ids': ['tt1111111']
        }
        with open(cache_path, 'w') as f:
            json.dump(cache_data, f)

        result = load_trakt_enhance_cache(str(tmp_path))
        assert result['movie_ids'] == {'tt1234567', 'tt7654321'}
        assert result['show_ids'] == {'tt1111111'}

    def test_returns_empty_on_old_version(self, tmp_path):
        """Test returns empty sets for old cache version."""
        from utils.trakt import load_trakt_enhance_cache
        import json

        cache_path = tmp_path / 'trakt_enhance_cache.json'
        cache_data = {'version': 0, 'movie_ids': ['tt123'], 'show_ids': []}
        with open(cache_path, 'w') as f:
            json.dump(cache_data, f)

        result = load_trakt_enhance_cache(str(tmp_path))
        assert result == {'movie_ids': set(), 'show_ids': set()}

    def test_returns_empty_on_invalid_json(self, tmp_path):
        """Test returns empty sets for corrupted cache."""
        from utils.trakt import load_trakt_enhance_cache

        cache_path = tmp_path / 'trakt_enhance_cache.json'
        with open(cache_path, 'w') as f:
            f.write('not valid json')

        result = load_trakt_enhance_cache(str(tmp_path))
        assert result == {'movie_ids': set(), 'show_ids': set()}


class TestSaveTraktEnhanceCache:
    """Tests for save_trakt_enhance_cache function."""

    def test_saves_cache_file(self, tmp_path):
        """Test saves cache to file."""
        from utils.trakt import save_trakt_enhance_cache, TRAKT_ENHANCE_CACHE_VERSION
        import json

        movie_ids = {'tt1234567', 'tt7654321'}
        show_ids = {'tt1111111'}
        save_trakt_enhance_cache(str(tmp_path), movie_ids, show_ids)

        cache_path = tmp_path / 'trakt_enhance_cache.json'
        assert cache_path.exists()

        with open(cache_path) as f:
            data = json.load(f)

        assert data['version'] == TRAKT_ENHANCE_CACHE_VERSION
        assert set(data['movie_ids']) == movie_ids
        assert set(data['show_ids']) == show_ids

    def test_handles_invalid_path(self):
        """Test handles write error gracefully."""
        from utils.trakt import save_trakt_enhance_cache
        # Should not raise exception
        save_trakt_enhance_cache('/nonexistent/path', set(), set())
