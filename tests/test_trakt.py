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
