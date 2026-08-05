"""Tests for utils/trakt.py - Trakt API client."""

import os
import time
from unittest.mock import Mock, patch

import pytest
import requests
import yaml

from utils.trakt import (
    TRAKT_RATE_LIMIT_DELAY,
    TraktAPIError,
    TraktAuthError,
    TraktClient,
    create_trakt_client,
    derive_trakt_list_slug,
)


class TestTraktClientInit:
    """Tests for TraktClient initialization."""

    def test_init_with_credentials(self):
        """Test initialization with client credentials."""
        client = TraktClient(client_id="test_id", client_secret="test_secret")
        assert client.client_id == "test_id"
        assert client.client_secret == "test_secret"
        assert client.access_token is None
        assert client.refresh_token is None

    def test_init_with_tokens(self):
        """Test initialization with existing tokens."""
        client = TraktClient(
            client_id="test_id", client_secret="test_secret", access_token="access123", refresh_token="refresh456"
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

    @patch("utils.trakt.requests.request")
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

    @patch("utils.trakt.requests.request")
    def test_204_no_content(self, mock_request):
        """Test 204 No Content response."""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_request.return_value = mock_response

        client = TraktClient("id", "secret", access_token="token")
        result = client._make_request("DELETE", "/test")

        assert result is None

    @patch("utils.trakt.requests.request")
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

    @patch("utils.trakt.time.sleep")
    @patch("utils.trakt.requests.request")
    def test_rate_limit_gives_up_after_max_retries(self, mock_request, mock_sleep):
        """FIX 9: this used to recurse unboundedly on a 429 - a server
        that never stops rate-limiting must not be able to hang/loop
        this process forever."""
        rate_limited = Mock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "1"}
        mock_request.return_value = rate_limited

        client = TraktClient("id", "secret", access_token="token")

        with pytest.raises(TraktAPIError):
            client._make_request("GET", "/test")

        # 1 initial attempt + TRAKT_MAX_429_RETRIES retries, never more.
        from utils.trakt import TRAKT_MAX_429_RETRIES

        assert mock_request.call_count == 1 + TRAKT_MAX_429_RETRIES

    @patch("utils.trakt.time.sleep")
    @patch("utils.trakt.requests.request")
    def test_retry_after_is_clamped_to_a_ceiling(self, mock_request, mock_sleep):
        """Retry-After is server-controlled input - a malicious/
        misbehaving Trakt endpoint must not be able to stall this
        process for an arbitrary amount of time by claiming an
        enormous Retry-After."""
        rate_limited = Mock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "99999"}

        success = Mock()
        success.status_code = 200
        success.json.return_value = {"data": "success"}

        mock_request.side_effect = [rate_limited, success]

        client = TraktClient("id", "secret", access_token="token")
        result = client._make_request("GET", "/test")

        assert result == {"data": "success"}
        # _rate_limit()'s own small pacing delay also calls time.sleep,
        # so check the CLAMPED 429 wait is among the calls rather than
        # asserting it's the only one.
        from utils.trakt import TRAKT_MAX_RETRY_AFTER_SECONDS

        sleep_durations = [call.args[0] for call in mock_sleep.call_args_list]
        assert TRAKT_MAX_RETRY_AFTER_SECONDS in sleep_durations
        assert 99999 not in sleep_durations

    @patch("utils.trakt.requests.request")
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

    @patch("utils.trakt.requests.request")
    def test_401_triggers_token_refresh(self, mock_request):
        """Test 401 triggers token refresh attempt."""
        unauthorized = Mock()
        unauthorized.status_code = 401
        unauthorized.text = "Unauthorized"

        mock_request.return_value = unauthorized

        client = TraktClient("id", "secret", access_token="token", refresh_token="refresh")

        with patch.object(client, "_refresh_access_token", return_value=False):
            with pytest.raises(TraktAuthError):
                client._make_request("GET", "/test")

    @patch("utils.trakt.requests.request")
    def test_api_error_is_logged_at_the_choke_point(self, mock_request, caplog):
        """#284: a Trakt API failure must be logged HERE, at the one
        shared choke point every Trakt request goes through - never
        relying on some caller further up to notice/log a caught
        TraktAPIError (the exact gap that let a real Trakt outage stay
        invisible for six months)."""
        import logging

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Server Error"
        mock_request.return_value = mock_response

        client = TraktClient("id", "secret", access_token="token")

        with caplog.at_level(logging.ERROR, logger="curatarr"), pytest.raises(TraktAPIError):
            client._make_request("GET", "/test")

        assert "Trakt" in caplog.text
        assert "500" in caplog.text

    @patch("utils.trakt.requests.request")
    def test_token_refresh_failure_is_logged(self, mock_request, caplog):
        """#284: a bad/expired Trakt token, unrecoverable via refresh,
        must be visible - not just an exception a caller might swallow."""
        import logging

        unauthorized = Mock()
        unauthorized.status_code = 401
        unauthorized.text = "Unauthorized"
        mock_request.return_value = unauthorized

        client = TraktClient("id", "secret", access_token="token", refresh_token="refresh")

        with (
            patch.object(client, "_refresh_access_token", return_value=False),
            caplog.at_level(logging.ERROR, logger="curatarr"),
            pytest.raises(TraktAuthError),
        ):
            client._make_request("GET", "/test")

        assert "authentication failed" in caplog.text.lower()

    @patch("utils.trakt.requests.request")
    def test_connection_failure_is_logged(self, mock_request, caplog):
        import logging

        mock_request.side_effect = requests.exceptions.ConnectionError("refused")
        client = TraktClient("id", "secret", access_token="token")

        with caplog.at_level(logging.ERROR, logger="curatarr"), pytest.raises(TraktAPIError):
            client._make_request("GET", "/test")

        assert "Trakt" in caplog.text


class TestTraktClientDeviceAuth:
    """Tests for device authentication flow."""

    @patch("utils.trakt.requests.post")
    def test_get_device_code_success(self, mock_post):
        """Test successful device code request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "device_code": "device123",
            "user_code": "USER123",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        mock_post.return_value = mock_response

        client = TraktClient("id", "secret")
        result = client.get_device_code()

        assert result["device_code"] == "device123"
        assert result["user_code"] == "USER123"

    @patch("utils.trakt.requests.post")
    @patch("utils.trakt.requests.get")
    def test_get_device_code_failure(self, mock_get, mock_post):
        """Test device code request failure."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_post.return_value = mock_response
        # The failure path now also probes whether the APPLICATION is
        # still registered, to say which of the two remedies applies -
        # see TestTraktApplicationDiagnostics.
        mock_get.return_value = Mock(status_code=200)

        client = TraktClient("id", "secret")

        with pytest.raises(TraktAuthError):
            client.get_device_code()

    @patch("utils.trakt.requests.post")
    def test_poll_for_token_success(self, mock_post):
        """Test successful token poll."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "access123", "refresh_token": "refresh456"}
        mock_post.return_value = mock_response

        callback = Mock()
        client = TraktClient("id", "secret", token_callback=callback)
        result = client.poll_for_token("device_code", interval=0, expires_in=10)

        assert result is True
        assert client.access_token == "access123"
        assert client.refresh_token == "refresh456"
        callback.assert_called_once_with("access123", "refresh456", None, None)

    @patch("utils.trakt.requests.post")
    def test_poll_for_token_captures_created_at_and_expires_in(self, mock_post):
        """The token grant's own created_at/expires_in fields (device-
        code polling's own `expires_in` parameter is a DIFFERENT thing -
        the polling window, not the access token's lifetime) are stored
        on the client and forwarded to token_callback."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "access123",
            "refresh_token": "refresh456",
            "created_at": 1700000000,
            "expires_in": 7776000,
        }
        mock_post.return_value = mock_response

        callback = Mock()
        client = TraktClient("id", "secret", token_callback=callback)
        result = client.poll_for_token("device_code", interval=0, expires_in=10)

        assert result is True
        assert client.created_at == 1700000000
        assert client.expires_in == 7776000
        callback.assert_called_once_with("access123", "refresh456", 1700000000, 7776000)

    @patch("utils.trakt.requests.post")
    def test_poll_for_token_pending(self, mock_post):
        """Test poll returns pending then success."""
        pending = Mock()
        pending.status_code = 400  # Still waiting

        success = Mock()
        success.status_code = 200
        success.json.return_value = {"access_token": "access", "refresh_token": "refresh"}

        mock_post.side_effect = [pending, success]

        client = TraktClient("id", "secret")
        result = client.poll_for_token("device_code", interval=0, expires_in=10)

        assert result is True
        assert mock_post.call_count == 2

    @patch("utils.trakt.requests.post")
    def test_poll_for_token_denied(self, mock_post):
        """Test poll when user denies."""
        mock_response = Mock()
        mock_response.status_code = 418  # User denied
        mock_post.return_value = mock_response

        client = TraktClient("id", "secret")
        result = client.poll_for_token("device_code", interval=0, expires_in=10)

        assert result is False

    @patch("utils.trakt.requests.post")
    def test_poll_for_token_calls_on_wait_once_per_pending_iteration(self, mock_post):
        """on_wait (see utils/trakt_auth.py's periodic progress printer)
        must fire once per still-waiting (400) iteration, and never on
        the final success iteration."""
        pending = Mock()
        pending.status_code = 400

        success = Mock()
        success.status_code = 200
        success.json.return_value = {"access_token": "access", "refresh_token": "refresh"}

        mock_post.side_effect = [pending, pending, success]

        on_wait = Mock()
        client = TraktClient("id", "secret")
        result = client.poll_for_token("device_code", interval=0, expires_in=10, on_wait=on_wait)

        assert result is True
        assert on_wait.call_count == 2

    @patch("utils.trakt.requests.post")
    def test_poll_for_token_without_on_wait_still_works(self, mock_post):
        """on_wait defaults to None - existing callers (and every
        existing test above) that never pass it must be unaffected."""
        pending = Mock()
        pending.status_code = 400

        success = Mock()
        success.status_code = 200
        success.json.return_value = {"access_token": "access", "refresh_token": "refresh"}

        mock_post.side_effect = [pending, success]

        client = TraktClient("id", "secret")
        result = client.poll_for_token("device_code", interval=0, expires_in=10)

        assert result is True

    @patch("utils.trakt.requests.post")
    def test_poll_for_token_on_wait_not_called_on_denied(self, mock_post):
        """on_wait is specifically for the still-waiting (400) branch -
        never for a terminal outcome like 418 (denied)."""
        mock_response = Mock()
        mock_response.status_code = 418
        mock_post.return_value = mock_response

        on_wait = Mock()
        client = TraktClient("id", "secret")
        result = client.poll_for_token("device_code", interval=0, expires_in=10, on_wait=on_wait)

        assert result is False
        on_wait.assert_not_called()


class TestTraktClientTokenRefresh:
    """Tests for token refresh."""

    @patch("utils.trakt.requests.post")
    def test_refresh_access_token_success(self, mock_post):
        """Test successful token refresh."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "new_access", "refresh_token": "new_refresh"}
        mock_post.return_value = mock_response

        callback = Mock()
        client = TraktClient("id", "secret", refresh_token="old_refresh", token_callback=callback)
        result = client._refresh_access_token()

        assert result is True
        assert client.access_token == "new_access"
        assert client.refresh_token == "new_refresh"
        callback.assert_called_once_with("new_access", "new_refresh", None, None)

    @patch("utils.trakt.requests.post")
    def test_refresh_access_token_sends_redirect_uri(self, mock_post):
        """Trakt's documented /oauth/token refresh body includes
        redirect_uri (PyTrakt sends this too) - suspected-required per
        the diagnosis that motivated this whole fix."""
        from utils.trakt import TRAKT_OOB_REDIRECT_URI

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "new_access", "refresh_token": "new_refresh"}
        mock_post.return_value = mock_response

        client = TraktClient("id", "secret", refresh_token="old_refresh")
        client._refresh_access_token()

        body = mock_post.call_args.kwargs["json"]
        assert body["redirect_uri"] == TRAKT_OOB_REDIRECT_URI
        assert body["grant_type"] == "refresh_token"

    @patch("utils.trakt.requests.post")
    def test_refresh_access_token_captures_created_at_and_expires_in(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "created_at": 1700000000,
            "expires_in": 7776000,
        }
        mock_post.return_value = mock_response

        client = TraktClient("id", "secret", refresh_token="old_refresh")
        client._refresh_access_token()

        assert client.created_at == 1700000000
        assert client.expires_in == 7776000

    @patch("utils.trakt.log_error")
    @patch("utils.trakt.requests.post")
    @patch("utils.trakt.requests.get")
    def test_refresh_access_token_failure(self, mock_get, mock_post, mock_log_error):
        """Test failed token refresh - status + body are logged (#trakt-
        token-refresh-persistence: this used to fail completely silently)."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = '{"error": "invalid_grant"}'
        mock_post.return_value = mock_response
        mock_get.return_value = Mock(status_code=200)  # application still registered

        client = TraktClient("id", "secret", refresh_token="old_refresh")
        result = client._refresh_access_token()

        assert result is False
        # Two lines now: the raw failure, then which remedy applies.
        assert mock_log_error.call_count == 2
        logged_message = mock_log_error.call_args_list[0][0][0]
        assert "401" in logged_message
        assert "invalid_grant" in logged_message
        assert "--reauth" in mock_log_error.call_args_list[1][0][0]

    @patch("utils.trakt.log_error")
    @patch("utils.trakt.requests.post")
    def test_refresh_access_token_request_exception_is_logged(self, mock_post, mock_log_error):
        """A network-level failure (not just a bad HTTP status) must
        also be logged, not silently swallowed."""
        mock_post.side_effect = requests.RequestException("connection reset")

        client = TraktClient("id", "secret", refresh_token="old_refresh")
        result = client._refresh_access_token()

        assert result is False
        mock_log_error.assert_called_once()
        assert "connection reset" in mock_log_error.call_args[0][0]

    def test_refresh_access_token_no_refresh_token(self):
        """Test refresh fails when no refresh token."""
        client = TraktClient("id", "secret")
        result = client._refresh_access_token()

        assert result is False


class TestTraktClientProactiveExpiry:
    """Tests for TraktClient._access_token_expired / proactive refresh."""

    def test_no_expiry_data_never_expired(self):
        """Unknown created_at/expires_in (e.g. a trakt.yml predating this
        feature) must never be treated as expired - falls back to
        exactly today's reactive-only (401-triggered) refresh."""
        client = TraktClient("id", "secret", access_token="tok")
        assert client._access_token_expired() is False

    def test_well_within_lifetime_not_expired(self):
        client = TraktClient("id", "secret", access_token="tok", created_at=int(time.time()), expires_in=7776000)
        assert client._access_token_expired() is False

    def test_past_expiry_is_expired(self):
        client = TraktClient(
            "id", "secret", access_token="tok", created_at=int(time.time()) - 7776001, expires_in=7776000
        )
        assert client._access_token_expired() is True

    def test_within_safety_margin_is_expired(self):
        from utils.trakt import TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS

        created_at = int(time.time()) - 7776000 + (TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS - 10)
        client = TraktClient("id", "secret", access_token="tok", created_at=created_at, expires_in=7776000)
        assert client._access_token_expired() is True

    @patch("utils.trakt.requests.post")
    def test_make_request_refreshes_proactively_before_sending(self, mock_post):
        """An expired-per-our-tracking token triggers a refresh BEFORE
        the real request is sent, not just reactively after a 401."""
        refresh_response = Mock()
        refresh_response.status_code = 200
        refresh_response.json.return_value = {"access_token": "new_access", "refresh_token": "new_refresh"}
        mock_post.return_value = refresh_response

        client = TraktClient(
            "id",
            "secret",
            access_token="stale_access",
            refresh_token="old_refresh",
            created_at=int(time.time()) - 7776001,
            expires_in=7776000,
        )
        with patch.object(client, "_send_with_retries") as mock_send:
            api_response = Mock()
            api_response.status_code = 200
            api_response.json.return_value = {"ok": True}
            mock_send.return_value = api_response

            client._make_request("GET", "/some/endpoint")

            # The real request went out with the REFRESHED token, not
            # the stale one.
            sent_headers = mock_send.call_args.kwargs["headers"]
            assert sent_headers["Authorization"] == "Bearer new_access"


class TestSaveTraktTokens:
    """Tests for save_trakt_tokens() - the shared, atomic token-persistence
    function hoisted out of utils/trakt_auth.py's own save_tokens (see
    that module) so TraktClient's runtime token_callback and the manual
    `python3 utils/trakt_auth.py` re-auth flow persist identically."""

    def test_updates_tokens_preserves_other_keys(self, tmp_path):
        from utils.trakt import save_trakt_tokens

        trakt_path = tmp_path / "trakt.yml"
        trakt_path.write_text(
            "enabled: true\nclient_id: abc\nclient_secret: def\naccess_token: old_access\n"
            "refresh_token: old_refresh\nexport:\n  auto_sync: false\n  user_mode: mapping\n"
        )

        save_trakt_tokens(str(trakt_path), "new_access", "new_refresh", 1700000000, 7776000)

        result = yaml.safe_load(trakt_path.read_text())
        assert result["access_token"] == "new_access"
        assert result["refresh_token"] == "new_refresh"
        assert result["token_created_at"] == 1700000000
        assert result["token_expires_in"] == 7776000
        # Every other key untouched.
        assert result["enabled"] is True
        assert result["client_id"] == "abc"
        assert result["client_secret"] == "def"
        assert result["export"] == {"auto_sync": False, "user_mode": "mapping"}

    def test_omits_created_at_expires_in_when_not_given(self, tmp_path):
        from utils.trakt import save_trakt_tokens

        trakt_path = tmp_path / "trakt.yml"
        trakt_path.write_text("access_token: old\nrefresh_token: old\n")

        save_trakt_tokens(str(trakt_path), "new_access", "new_refresh")

        result = yaml.safe_load(trakt_path.read_text())
        assert "token_created_at" not in result
        assert "token_expires_in" not in result

    def test_write_is_atomic_no_leftover_temp_file(self, tmp_path):
        from utils.trakt import save_trakt_tokens

        trakt_path = tmp_path / "trakt.yml"
        trakt_path.write_text("access_token: old\nrefresh_token: old\n")

        save_trakt_tokens(str(trakt_path), "new_access", "new_refresh")

        leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
        assert leftover == []
        assert trakt_path.exists()

    def test_hardens_permissions(self, tmp_path):
        from utils.trakt import save_trakt_tokens

        trakt_path = tmp_path / "trakt.yml"
        trakt_path.write_text("access_token: old\nrefresh_token: old\n")

        save_trakt_tokens(str(trakt_path), "new_access", "new_refresh")

        if os.name != "nt":
            mode = trakt_path.stat().st_mode & 0o777
            assert mode == 0o600

    def test_raises_and_leaves_original_file_untouched_on_bad_target_dir(self, tmp_path):
        """A write failure (e.g. the temp file can't be created) must
        raise - not silently succeed - so create_trakt_client's
        token_callback can log it instead of pretending persistence
        worked."""
        from utils.trakt import save_trakt_tokens

        trakt_path = tmp_path / "missing_dir" / "trakt.yml"
        with pytest.raises(OSError):
            save_trakt_tokens(str(trakt_path), "new_access", "new_refresh")


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
                "refresh_token": "refresh",
            }
        }
        result = create_trakt_client(config)

        assert result is not None
        assert isinstance(result, TraktClient)
        assert result.client_id == "test_id"
        assert result.access_token == "token"

    def test_reads_created_at_and_expires_in_from_config(self):
        config = {
            "trakt": {
                "enabled": True,
                "client_id": "test_id",
                "client_secret": "test_secret",
                "access_token": "token",
                "refresh_token": "refresh",
                "token_created_at": 1700000000,
                "token_expires_in": 7776000,
            }
        }
        result = create_trakt_client(config)

        assert result.created_at == 1700000000
        assert result.expires_in == 7776000

    @patch("utils.trakt.save_trakt_tokens")
    @patch("utils.trakt.get_project_root")
    def test_token_callback_persists_via_save_trakt_tokens(self, mock_project_root, mock_save):
        """#trakt-token-refresh-persistence: create_trakt_client's
        token_callback is now wired to actually persist a refreshed
        token (previously no callback was passed at all, so a refresh
        during a normal run was never saved anywhere - and since Trakt
        rotates refresh tokens single-use, the NEXT run then replayed an
        already-consumed one)."""
        mock_project_root.return_value = os.path.join("fake", "project", "root")
        config = {
            "trakt": {
                "enabled": True,
                "client_id": "test_id",
                "client_secret": "test_secret",
                "access_token": "token",
                "refresh_token": "refresh",
            }
        }
        client = create_trakt_client(config)
        assert client.token_callback is not None

        client.token_callback("new_access", "new_refresh", 1700000000, 7776000)

        mock_save.assert_called_once_with(
            os.path.join("fake", "project", "root", "config", "trakt.yml"),
            "new_access",
            "new_refresh",
            1700000000,
            7776000,
        )

    @patch("utils.trakt.log_error")
    @patch("utils.trakt.save_trakt_tokens")
    @patch("utils.trakt.get_project_root")
    def test_token_callback_persistence_failure_is_logged_not_raised(
        self, mock_project_root, mock_save, mock_log_error
    ):
        """A disk-level failure while persisting a refreshed token must
        never crash the run that triggered the refresh - the refreshed
        token still works in memory for the rest of this process."""
        mock_project_root.return_value = os.path.join("fake", "project", "root")
        mock_save.side_effect = OSError("disk full")
        config = {
            "trakt": {
                "enabled": True,
                "client_id": "test_id",
                "client_secret": "test_secret",
                "access_token": "token",
                "refresh_token": "refresh",
            }
        }
        client = create_trakt_client(config)

        client.token_callback("new_access", "new_refresh", None, None)  # must not raise

        mock_log_error.assert_called_once()
        assert "disk full" in mock_log_error.call_args[0][0]


class TestRevokeToken:
    """Tests for revoke_token method."""

    def test_revoke_no_token(self):
        """Test revoke returns True when no token to revoke."""
        client = TraktClient("id", "secret")
        result = client.revoke_token()
        assert result is True

    @patch("utils.trakt.requests.post")
    def test_revoke_success(self, mock_post):
        """Test successful token revocation."""
        mock_post.return_value = Mock(status_code=200)

        client = TraktClient("id", "secret", access_token="token", refresh_token="refresh")
        result = client.revoke_token()

        assert result is True
        assert client.access_token is None
        assert client.refresh_token is None
        mock_post.assert_called_once()

    @patch("utils.trakt.requests.post")
    def test_revoke_failure(self, mock_post):
        """Test token revocation failure."""
        mock_post.return_value = Mock(status_code=500)

        client = TraktClient("id", "secret", access_token="token")
        result = client.revoke_token()

        assert result is False

    @patch("utils.trakt.requests.post")
    def test_revoke_request_exception(self, mock_post):
        """Test revoke handles request exception."""
        import requests

        mock_post.side_effect = requests.RequestException("Network error")

        client = TraktClient("id", "secret", access_token="token")
        result = client.revoke_token()

        assert result is False


class TestTraktClientUserInfo:
    """Tests for user info methods."""

    @patch("utils.trakt.requests.request")
    def test_get_user_settings(self, mock_request):
        """Test get_user_settings."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"user": {"username": "testuser"}}
        mock_request.return_value = mock_response

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_user_settings()

        assert result["user"]["username"] == "testuser"

    @patch("utils.trakt.requests.request")
    def test_get_username(self, mock_request):
        """Test get_username."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"user": {"username": "testuser"}}
        mock_request.return_value = mock_response

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_username()

        assert result == "testuser"

    @patch("utils.trakt.requests.request")
    def test_get_username_error(self, mock_request):
        """Test get_username returns None on error."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_request.return_value = mock_response

        client = TraktClient("id", "secret")
        result = client.get_username()

        assert result is None

    @patch("utils.trakt.log_warning")
    def test_get_username_logs_real_auth_failure_cause(self, mock_log_warning):
        """#trakt-token-refresh-persistence: a TraktAuthError (e.g. "Failed
        to refresh Trakt token" from a rejected refresh) must be logged,
        not silently discarded - previously this collapsed straight to
        None with zero indication a refresh was even attempted, turning
        into the misleading "Cannot get lists: not authenticated" at
        every calling site with no way to tell a real refresh failure
        apart from simply never having authenticated at all."""
        client = TraktClient("id", "secret", access_token="token")
        with patch.object(client, "get_user_settings", side_effect=TraktAuthError("Failed to refresh Trakt token")):
            result = client.get_username()

        assert result is None
        mock_log_warning.assert_called_once()
        assert "Failed to refresh Trakt token" in mock_log_warning.call_args[0][0]

    @patch("utils.trakt.log_warning")
    def test_get_username_logs_real_api_error_cause(self, mock_log_warning):
        client = TraktClient("id", "secret", access_token="token")
        with patch.object(client, "get_user_settings", side_effect=TraktAPIError("Trakt API error 500: boom")):
            result = client.get_username()

        assert result is None
        mock_log_warning.assert_called_once()
        assert "500" in mock_log_warning.call_args[0][0]


class TestTraktClientListManagement:
    """Tests for list management methods."""

    @patch("utils.trakt.requests.request")
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
            {"name": "List 2", "ids": {"slug": "list-2"}},
        ]

        mock_request.side_effect = [settings_response, lists_response]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_lists()

        assert len(result) == 2
        assert result[0]["name"] == "List 1"

    @patch("utils.trakt.requests.request")
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

    @patch("utils.trakt.requests.request")
    def test_create_list(self, mock_request):
        """Test creating a new list."""
        settings_response = Mock()
        settings_response.status_code = 200
        settings_response.json.return_value = {"user": {"username": "testuser"}}

        create_response = Mock()
        create_response.status_code = 200
        create_response.json.return_value = {"name": "New List", "ids": {"slug": "new-list"}}

        mock_request.side_effect = [settings_response, create_response]

        client = TraktClient("id", "secret", access_token="token")
        result = client.create_list("New List", description="Test")

        assert result["name"] == "New List"
        assert result["ids"]["slug"] == "new-list"

    @patch("utils.trakt.requests.request")
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
            "not_found": {"movies": [], "shows": []},
        }

        mock_request.side_effect = [settings_response, add_response]

        client = TraktClient("id", "secret", access_token="token")
        result = client.add_to_list(
            "my-list",
            movies=[{"ids": {"imdb": "tt123"}}, {"ids": {"imdb": "tt456"}}],
            shows=[{"ids": {"imdb": "tt789"}}],
        )

        assert result["added"]["movies"] == 2
        assert result["added"]["shows"] == 1

    @patch("utils.trakt.requests.request")
    def test_remove_from_list(self, mock_request):
        """Test removing items from a list."""
        settings_response = Mock()
        settings_response.status_code = 200
        settings_response.json.return_value = {"user": {"username": "testuser"}}

        remove_response = Mock()
        remove_response.status_code = 200
        remove_response.json.return_value = {
            "deleted": {"movies": 1, "shows": 0},
            "not_found": {"movies": [], "shows": []},
        }

        mock_request.side_effect = [settings_response, remove_response]

        client = TraktClient("id", "secret", access_token="token")
        result = client.remove_from_list("my-list", movies=[{"ids": {"imdb": "tt123"}}])

        assert result["deleted"]["movies"] == 1

    def test_add_to_list_empty(self):
        """Test adding empty lists returns immediately."""
        client = TraktClient("id", "secret", access_token="token")
        result = client.add_to_list("my-list")

        assert result == {"added": {"movies": 0, "shows": 0}}

    @patch("utils.trakt.requests.request")
    def test_add_to_list_no_username(self, mock_request):
        """Test add_to_list raises error when no username."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {}}  # No username
        mock_request.return_value = settings

        client = TraktClient("id", "secret", access_token="token")
        with pytest.raises(TraktAuthError):
            client.add_to_list("my-list", movies=[{"ids": {"imdb": "tt123"}}])

    @patch("utils.trakt.requests.request")
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

    @patch("utils.trakt.requests.request")
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

    @patch("utils.trakt.requests.request")
    def test_delete_list_no_username(self, mock_request):
        """Test delete list fails when no username."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {}}  # No username

        mock_request.return_value = settings

        client = TraktClient("id", "secret", access_token="token")
        result = client.delete_list("my-list")

        assert result is False

    @patch("utils.trakt.requests.request")
    def test_delete_list_api_error(self, mock_request):
        """Test delete list handles API error."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        error_resp = Mock(status_code=404, text="Not found")

        mock_request.side_effect = [settings, error_resp]

        client = TraktClient("id", "secret", access_token="token")
        result = client.delete_list("nonexistent")

        assert result is False

    @patch("utils.trakt.requests.request")
    def test_get_or_create_finds_by_name(self, mock_request):
        """Test get_or_create_list finds list by name when slug lookup fails."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        # First lookup by slug fails
        not_found = Mock(status_code=404, text="Not found")

        # get_lists returns list with matching name
        lists_resp = Mock(status_code=200)
        lists_resp.json.return_value = [{"name": "My List", "ids": {"slug": "different-slug"}}]

        mock_request.side_effect = [settings, not_found, settings, lists_resp]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_or_create_list("My List")

        assert result is not None
        assert result["name"] == "My List"

    @patch("utils.trakt.requests.request")
    def test_get_or_create_direct_lookup_slug_hits_for_hyphen_separated_name(self, mock_request):
        """Regression test: get_or_create_list's speculative direct-
        lookup slug used to be a naive `name.lower().replace(" ",
        "-").replace("_", "-")`, which for "Curatarr - Jason - Movies"
        guesses "curatarr---jason---movies" (wrong - 404s) instead of
        Trakt's real "curatarr-jason-movies", forcing every such name
        through the slower get_lists()-and-search-by-name fallback on
        EVERY sync. With the corrected derivation the direct lookup
        should hit on the first try - only two requests total (username
        + the direct GET), no get_lists() call at all."""
        list_name = "Curatarr - Jason - Movies"

        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        existing_list = Mock(status_code=200)
        existing_list.json.return_value = {"name": list_name, "ids": {"slug": "curatarr-jason-movies"}}

        mock_request.side_effect = [settings, existing_list]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_or_create_list(list_name)

        assert result["ids"]["slug"] == "curatarr-jason-movies"
        # The direct-lookup GET's own URL is the real proof the guessed
        # slug was correct - the second call's endpoint must contain
        # the real slug, not the broken triple-hyphen guess.
        # requests.request is called with keyword args (method=, url=,
        # ...) - see utils/api_client.py's _send_with_retries.
        direct_lookup_call = mock_request.call_args_list[1]
        assert "curatarr-jason-movies" in direct_lookup_call.kwargs["url"]
        assert mock_request.call_count == 2


class TestDeriveTraktListSlug:
    """Unit tests for derive_trakt_list_slug - see its own docstring
    for why this exists and what it's a fallback for."""

    def test_collapses_space_hyphen_space_runs_to_a_single_hyphen(self):
        """The exact reported bug: 'Curatarr - Jason - Movies' must
        produce Trakt's real, confirmed-live slug
        'curatarr-jason-movies', not 'curatarr---jason---movies'."""
        assert derive_trakt_list_slug("Curatarr - Jason - Movies") == "curatarr-jason-movies"

    def test_plain_spaces_become_single_hyphens(self):
        assert derive_trakt_list_slug("TV Shows") == "tv-shows"

    def test_underscores_become_hyphens(self):
        assert derive_trakt_list_slug("my_list_name") == "my-list-name"

    def test_mixed_separators_collapse_together(self):
        assert derive_trakt_list_slug("Fam - TV") == "fam-tv"

    def test_leading_and_trailing_separators_are_stripped(self):
        assert derive_trakt_list_slug("  - Movies -  ") == "movies"

    def test_consecutive_literal_hyphens_collapse(self):
        assert derive_trakt_list_slug("a--b") == "a-b"

    def test_empty_and_none_are_safe(self):
        assert derive_trakt_list_slug("") == ""
        assert derive_trakt_list_slug(None) == ""


class TestTraktClientSyncList:
    """Tests for list sync functionality."""

    @patch("utils.trakt.requests.request")
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
        assert result["list_slug"] == "test"

    @patch("utils.trakt.requests.request")
    def test_sync_list_clears_and_adds(self, mock_request):
        """Test syncing clears existing items before adding new ones."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        existing_list = Mock(status_code=200)
        existing_list.json.return_value = {"name": "Test", "ids": {"slug": "test"}}

        existing_items = Mock(status_code=200)
        existing_items.json.return_value = [{"type": "movie", "movie": {"ids": {"imdb": "tt000"}}}]

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
        assert result["list_slug"] == "test"

    @patch("utils.trakt.requests.request")
    def test_sync_list_returns_real_slug_not_a_naive_derivation(self, mock_request):
        """Regression test for the real bug: a list literally named
        'Curatarr - Jason - Movies' has a REAL Trakt slug of
        'curatarr-jason-movies' (confirmed against the live API) - not
        'curatarr---jason---movies', which naively replacing each space
        independently produces. sync_list()'s returned "list_slug" must
        be the slug Trakt's own API response carried (here, the
        existing-list lookup's `ids.slug`), never a locally re-derived
        one - callers (recommenders/external_sync.py) build a clickable
        URL directly from this value."""
        list_name = "Curatarr - Jason - Movies"

        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        # get_or_create_list's own speculative direct-lookup slug guess
        # ("curatarr-jason-movies", now correctly derived - see
        # derive_trakt_list_slug) happens to hit on the first try here,
        # simulating the list already existing.
        existing_list = Mock(status_code=200)
        existing_list.json.return_value = {"name": list_name, "ids": {"slug": "curatarr-jason-movies"}}

        empty_items = Mock(status_code=200)
        empty_items.json.return_value = []

        added = Mock(status_code=200)
        added.json.return_value = {"added": {"movies": 1, "shows": 0}}

        mock_request.side_effect = [
            settings,  # get_username for get_or_create_list
            existing_list,  # get_list (direct-lookup slug guess hits)
            settings,  # get_username for get_list_items
            empty_items,  # get_list_items
            settings,  # get_username for add_to_list
            added,  # add_to_list
        ]

        client = TraktClient("id", "secret", access_token="token")
        result = client.sync_list(list_name, movies=["tt123"])

        assert result["list_slug"] == "curatarr-jason-movies"
        assert result["list_slug"] != "curatarr---jason---movies"


class TestTraktClientImport:
    """Tests for watch history and watchlist import methods."""

    @patch("utils.trakt.requests.request")
    def test_get_watched_movies(self, mock_request):
        """Test getting watched movies."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        watched = Mock(status_code=200)
        watched.json.return_value = [
            {"movie": {"title": "Movie 1", "ids": {"imdb": "tt123"}}},
            {"movie": {"title": "Movie 2", "ids": {"imdb": "tt456"}}},
        ]

        mock_request.side_effect = [settings, watched]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watched_movies()

        assert len(result) == 2
        assert result[0]["movie"]["title"] == "Movie 1"

    @patch("utils.trakt.requests.request")
    def test_get_watched_shows(self, mock_request):
        """Test getting watched shows."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        watched = Mock(status_code=200)
        watched.json.return_value = [{"show": {"title": "Show 1", "ids": {"imdb": "tt789"}}}]

        mock_request.side_effect = [settings, watched]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watched_shows()

        assert len(result) == 1
        assert result[0]["show"]["title"] == "Show 1"

    @patch("utils.trakt.requests.request")
    def test_get_ratings(self, mock_request):
        """Test getting user ratings."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        ratings = Mock(status_code=200)
        ratings.json.return_value = [
            {"rating": 10, "movie": {"title": "Great Movie", "ids": {"imdb": "tt123"}}},
            {"rating": 5, "movie": {"title": "OK Movie", "ids": {"imdb": "tt456"}}},
        ]

        mock_request.side_effect = [settings, ratings]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_ratings("movies")

        assert len(result) == 2
        assert result[0]["rating"] == 10

    @patch("utils.trakt.requests.request")
    def test_get_watchlist(self, mock_request):
        """Test getting watchlist."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        watchlist = Mock(status_code=200)
        watchlist.json.return_value = [
            {"type": "movie", "movie": {"title": "Want to Watch", "ids": {"imdb": "tt111"}}},
            {"type": "show", "show": {"title": "Want to Watch Show", "ids": {"imdb": "tt222"}}},
        ]

        mock_request.side_effect = [settings, watchlist]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watchlist()

        assert len(result) == 2
        assert result[0]["type"] == "movie"

    @patch("utils.trakt.requests.request")
    def test_get_watch_history_imdb_ids(self, mock_request):
        """Test getting IMDB IDs from watch history."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        watched = Mock(status_code=200)
        watched.json.return_value = [
            {"movie": {"title": "Movie 1", "ids": {"imdb": "tt123"}}},
            {"movie": {"title": "Movie 2", "ids": {"imdb": "tt456"}}},
        ]

        mock_request.side_effect = [settings, watched]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watch_history_imdb_ids("movies")

        assert result == {"tt123", "tt456"}

    @patch("utils.trakt.requests.request")
    def test_get_watchlist_imdb_ids(self, mock_request):
        """Test getting IMDB IDs from watchlist."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        watchlist = Mock(status_code=200)
        watchlist.json.return_value = [
            {"type": "movie", "movie": {"ids": {"imdb": "tt111"}}},
            {"type": "show", "show": {"ids": {"imdb": "tt222"}}},
        ]

        mock_request.side_effect = [settings, watchlist]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watchlist_imdb_ids()

        assert result == {"tt111", "tt222"}

    @patch("utils.trakt.requests.request")
    def test_get_watched_movies_no_auth(self, mock_request):
        """Test get_watched_movies returns empty when not authenticated.

        No access_token means _get_headers() omits the Authorization
        header, but get_username() still makes a real (unauthenticated)
        /users/settings request - the client has no local "skip the call
        if unauthenticated" short-circuit, so this mocks the real 401 a
        real Trakt server would return, rather than relying on an actual
        unmocked network round-trip to produce one.
        """
        mock_request.return_value = Mock(status_code=401, text="Unauthorized")
        client = TraktClient("id", "secret")
        result = client.get_watched_movies()
        assert result == []

    @patch("utils.trakt.requests.request")
    def test_get_watched_movies_api_error(self, mock_request):
        """Test get_watched_movies returns empty on API error."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        error = Mock(status_code=500, text="Server error")

        mock_request.side_effect = [settings, error]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watched_movies()
        assert result == []

    @patch("utils.trakt.requests.request")
    def test_get_watched_shows_api_error(self, mock_request):
        """Test get_watched_shows returns empty on API error."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}

        error = Mock(status_code=500, text="Server error")

        mock_request.side_effect = [settings, error]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watched_shows()
        assert result == []

    @patch("utils.trakt.requests.request")
    def test_get_watchlist_no_auth(self, mock_request):
        """Test get_watchlist returns empty when not authenticated.

        See test_get_watched_movies_no_auth's docstring above - same
        reasoning (get_username() always makes a real request).
        """
        mock_request.return_value = Mock(status_code=401, text="Unauthorized")
        client = TraktClient("id", "secret")
        result = client.get_watchlist()
        assert result == []

    @patch("utils.trakt.requests.request")
    def test_get_watchlist_api_error(self, mock_request):
        """Test get_watchlist returns empty on API error."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}
        error = Mock(status_code=500, text="Server error")
        mock_request.side_effect = [settings, error]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watchlist()
        assert result == []

    @patch("utils.trakt.requests.request")
    def test_get_ratings_api_error(self, mock_request):
        """Test get_ratings returns empty on API error."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}
        error = Mock(status_code=500, text="Server error")
        mock_request.side_effect = [settings, error]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_ratings()
        assert result == []

    @patch("utils.trakt.requests.request")
    def test_get_ratings_no_auth(self, mock_request):
        """Test get_ratings returns empty when not authenticated.

        See test_get_watched_movies_no_auth's docstring above - same
        reasoning (get_username() always makes a real request).
        """
        mock_request.return_value = Mock(status_code=401, text="Unauthorized")
        client = TraktClient("id", "secret")
        result = client.get_ratings()
        assert result == []

    def test_add_to_history_empty(self):
        """Test add_to_history returns early when no data."""
        client = TraktClient("id", "secret", access_token="token")
        result = client.add_to_history()
        assert result == {"added": {"movies": 0, "episodes": 0}}

    @patch("utils.trakt.requests.request")
    def test_get_watch_history_imdb_ids_shows(self, mock_request):
        """Test get_watch_history_imdb_ids for shows."""
        settings = Mock(status_code=200)
        settings.json.return_value = {"user": {"username": "testuser"}}
        shows = Mock(status_code=200)
        shows.json.return_value = [
            {"show": {"ids": {"imdb": "tt111"}}},
            {"show": {"ids": {"imdb": "tt222"}}},
            {"show": {"ids": {}}},  # Missing IMDB
        ]
        mock_request.side_effect = [settings, shows]

        client = TraktClient("id", "secret", access_token="token")
        result = client.get_watch_history_imdb_ids(media_type="shows")
        assert result == {"tt111", "tt222"}


class TestLoadTraktEnhanceCache:
    """Tests for load_trakt_enhance_cache function."""

    def test_returns_empty_when_file_not_exists(self, tmp_path):
        """Test returns empty sets when cache file doesn't exist."""
        from utils.trakt import load_trakt_enhance_cache

        result = load_trakt_enhance_cache(str(tmp_path))
        assert result == {"movie_ids": set(), "show_ids": set()}

    def test_loads_valid_cache(self, tmp_path):
        """Test loads valid cache file."""
        import json

        from utils.trakt import TRAKT_ENHANCE_CACHE_VERSION, load_trakt_enhance_cache

        cache_path = tmp_path / "trakt_enhance_cache.json"
        cache_data = {
            "version": TRAKT_ENHANCE_CACHE_VERSION,
            "movie_ids": ["tt1234567", "tt7654321"],
            "show_ids": ["tt1111111"],
        }
        with open(cache_path, "w") as f:
            json.dump(cache_data, f)

        result = load_trakt_enhance_cache(str(tmp_path))
        assert result["movie_ids"] == {"tt1234567", "tt7654321"}
        assert result["show_ids"] == {"tt1111111"}

    def test_returns_empty_on_old_version(self, tmp_path):
        """Test returns empty sets for old cache version."""
        import json

        from utils.trakt import load_trakt_enhance_cache

        cache_path = tmp_path / "trakt_enhance_cache.json"
        cache_data = {"version": 0, "movie_ids": ["tt123"], "show_ids": []}
        with open(cache_path, "w") as f:
            json.dump(cache_data, f)

        result = load_trakt_enhance_cache(str(tmp_path))
        assert result == {"movie_ids": set(), "show_ids": set()}

    def test_returns_empty_on_invalid_json(self, tmp_path):
        """Test returns empty sets for corrupted cache."""
        from utils.trakt import load_trakt_enhance_cache

        cache_path = tmp_path / "trakt_enhance_cache.json"
        with open(cache_path, "w") as f:
            f.write("not valid json")

        result = load_trakt_enhance_cache(str(tmp_path))
        assert result == {"movie_ids": set(), "show_ids": set()}


class TestSaveTraktEnhanceCache:
    """Tests for save_trakt_enhance_cache function."""

    def test_saves_cache_file(self, tmp_path):
        """Test saves cache to file."""
        import json

        from utils.trakt import TRAKT_ENHANCE_CACHE_VERSION, save_trakt_enhance_cache

        movie_ids = {"tt1234567", "tt7654321"}
        show_ids = {"tt1111111"}
        save_trakt_enhance_cache(str(tmp_path), movie_ids, show_ids)

        cache_path = tmp_path / "trakt_enhance_cache.json"
        assert cache_path.exists()

        with open(cache_path) as f:
            data = json.load(f)

        assert data["version"] == TRAKT_ENHANCE_CACHE_VERSION
        assert set(data["movie_ids"]) == movie_ids
        assert set(data["show_ids"]) == show_ids

    def test_handles_invalid_path(self):
        """Test handles write error gracefully."""
        from utils.trakt import save_trakt_enhance_cache

        # Should not raise exception
        save_trakt_enhance_cache("/nonexistent/path", set(), set())


class TestTraktApplicationDiagnostics:
    """
    application_is_registered() / describe_auth_failure().

    A deleted Trakt application and an expired token both surface as an
    auth failure, but only one is fixable by re-authorizing - against a
    deleted application there is nothing to authorize against. The raw
    API body for the former is {"error":"invalid_client",
    "error_description":"client not found"}, which says neither that the
    application must be recreated nor where. This was a real incident:
    the cause took an hour to establish from that message.
    """

    @staticmethod
    def _client():
        return TraktClient(client_id="dead-id", client_secret="secret")

    @patch("utils.trakt.requests.get")
    def test_registered_application_reports_true(self, mock_get):
        mock_get.return_value = Mock(status_code=200)
        assert self._client().application_is_registered() is True

    @patch("utils.trakt.requests.get")
    def test_rejected_key_reports_false(self, mock_get):
        """403 is what Trakt actually returned for the deleted app."""
        mock_get.return_value = Mock(status_code=403, text="Forbidden")
        assert self._client().application_is_registered() is False

    @patch("utils.trakt.requests.get")
    def test_unauthorized_also_reports_false(self, mock_get):
        mock_get.return_value = Mock(status_code=401, text="Unauthorized")
        assert self._client().application_is_registered() is False

    @patch("utils.trakt.requests.get")
    def test_network_failure_is_unknown_not_a_verdict(self, mock_get):
        """Never claim the app is dead because we could not ask."""
        mock_get.side_effect = requests.RequestException("no route to host")
        assert self._client().application_is_registered() is None

    @patch("utils.trakt.requests.get")
    def test_unexpected_status_is_unknown(self, mock_get):
        mock_get.return_value = Mock(status_code=500, text="boom")
        assert self._client().application_is_registered() is None

    @patch("utils.trakt.requests.get")
    def test_message_for_a_dead_application_names_the_remedy(self, mock_get):
        mock_get.return_value = Mock(status_code=403, text="Forbidden")
        msg = self._client().describe_auth_failure()
        assert "no longer exists" in msg
        assert "https://trakt.tv/oauth/applications" in msg
        assert "urn:ietf:wg:oauth:2.0:oob" in msg
        assert "Re-authorizing cannot fix this" in msg

    @patch("utils.trakt.requests.get")
    def test_message_for_a_live_application_says_reauthorize_instead(self, mock_get):
        """The opposite remedy - must not tell the user to recreate a
        perfectly good application."""
        mock_get.return_value = Mock(status_code=200)
        msg = self._client().describe_auth_failure()
        assert "--reauth" in msg
        assert "no longer exists" not in msg

    @patch("utils.trakt.requests.get")
    def test_message_when_unreachable_claims_neither(self, mock_get):
        mock_get.side_effect = requests.RequestException("down")
        msg = self._client().describe_auth_failure()
        assert "Could not reach Trakt" in msg
        assert "no longer exists" not in msg
        assert "--reauth" not in msg

    @patch("utils.trakt.requests.get")
    @patch("utils.trakt.requests.post")
    def test_device_code_failure_carries_the_diagnosis(self, mock_post, mock_get):
        """The path the user actually hit: python -m utils.trakt_auth --reauth."""
        mock_post.return_value = Mock(
            status_code=401, text='{"error":"invalid_client","error_description":"client not found"}'
        )
        mock_get.return_value = Mock(status_code=403, text="Forbidden")

        with pytest.raises(TraktAuthError) as excinfo:
            self._client().get_device_code()

        assert "https://trakt.tv/oauth/applications" in str(excinfo.value)
