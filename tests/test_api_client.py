"""Tests for utils/api_client.py - BaseAPIClient's shared request
handling: redirect safety (FIX 8), response-size capping (item 20), and
the 429-retry/response-error paths every subclass (Radarr/Sonarr/
Tautulli/MDBList) shares.
"""

from unittest.mock import Mock, patch

import pytest

from utils.api_client import _MAX_REDIRECT_HOPS, BaseAPIClient


class _FakeAPIError(Exception):
    pass


class _Client(BaseAPIClient):
    api_name = "FakeAPI"
    exception_class = _FakeAPIError

    def _get_headers(self):
        return {"X-Api-Key": "super-secret-key"}


def _make_response(status_code, url, headers=None, json_data=None, body=b"{}"):
    r = Mock()
    r.status_code = status_code
    r.url = url
    r.headers = headers or {}
    r.iter_content = Mock(return_value=[body])
    r.json.return_value = json_data if json_data is not None else {}
    r.text = ""
    return r


@pytest.fixture
def client():
    return _Client()


class TestRedirectSafety:
    """FIX 8: requests follows redirects by default and only auto-strips
    the Authorization header on a cross-host hop, not a custom header
    like X-Api-Key - a malicious/compromised configured Radarr/Sonarr
    host could otherwise redirect and harvest it."""

    def test_allow_redirects_is_disabled_on_the_initial_request(self, client):
        with patch(
            "utils.api_client.requests.request", return_value=_make_response(200, "http://radarr.local/api")
        ) as mock_req:
            client._make_request_to_url("GET", "http://radarr.local/api")
        assert mock_req.call_args.kwargs.get("allow_redirects") is False

    def test_cross_host_redirect_is_not_followed_and_never_resends_api_key(self, client):
        responses = [
            _make_response(302, "http://radarr.local/api", headers={"Location": "http://evil.example.com/steal"}),
        ]
        with patch("utils.api_client.requests.request", side_effect=responses) as mock_req:
            with pytest.raises(_FakeAPIError, match="unfollowed redirect"):
                client._make_request_to_url("GET", "http://radarr.local/api")
        # Only the ONE call to the original (trusted) host ever happened -
        # the API key headers were never sent anywhere else.
        assert mock_req.call_count == 1
        assert mock_req.call_args.kwargs["url"] == "http://radarr.local/api"

    def test_same_host_redirect_is_followed_with_the_same_headers(self, client):
        responses = [
            _make_response(302, "http://radarr.local/api", headers={"Location": "http://radarr.local/api/"}),
            _make_response(200, "http://radarr.local/api/", json_data={"ok": True}),
        ]
        with patch("utils.api_client.requests.request", side_effect=responses) as mock_req:
            result = client._make_request_to_url("GET", "http://radarr.local/api")
        assert result == {"ok": True}
        assert mock_req.call_count == 2
        # The redirected request still carried the real API key header -
        # same-host is the one case that's actually safe to follow.
        assert mock_req.call_args_list[1].kwargs["headers"]["X-Api-Key"] == "super-secret-key"

    def test_redirect_loop_is_capped_and_raises(self, client):
        def infinite():
            while True:
                yield _make_response(302, "http://radarr.local/api", headers={"Location": "http://radarr.local/api"})

        with patch("utils.api_client.requests.request", side_effect=infinite()) as mock_req:
            with pytest.raises(_FakeAPIError, match="unfollowed redirect"):
                client._make_request_to_url("GET", "http://radarr.local/api")
        # 1 initial request + _MAX_REDIRECT_HOPS follow-up attempts, never more.
        assert mock_req.call_count == 1 + _MAX_REDIRECT_HOPS


class TestResponseSizeCap:
    """A misconfigured/malicious/compromised CONFIGURED host serving an
    unbounded response body must not be able to exhaust this process's
    memory - see utils.helpers.read_response_capped."""

    def test_oversized_content_length_is_rejected(self, client):
        resp = _make_response(200, "http://radarr.local/api", headers={"Content-Length": str(50 * 1024 * 1024)})
        with patch("utils.api_client.requests.request", return_value=resp):
            with pytest.raises(_FakeAPIError, match="response rejected"):
                client._make_request_to_url("GET", "http://radarr.local/api")

    def test_streamed_body_exceeding_the_cap_is_rejected(self, client):
        resp = Mock()
        resp.status_code = 200
        resp.url = "http://radarr.local/api"
        resp.headers = {}
        resp.iter_content = Mock(return_value=(b"x" * 65536 for _ in range(200)))  # ~13MB > 10MB cap
        with patch("utils.api_client.requests.request", return_value=resp):
            with pytest.raises(_FakeAPIError, match="response rejected"):
                client._make_request_to_url("GET", "http://radarr.local/api")

    def test_normal_sized_response_is_unaffected(self, client):
        resp = _make_response(200, "http://radarr.local/api", json_data={"ok": True})
        with patch("utils.api_client.requests.request", return_value=resp):
            result = client._make_request_to_url("GET", "http://radarr.local/api")
        assert result == {"ok": True}


class TestExternalApiFailureLogging:
    """#284: a bad/expired API key, or a connection/timeout failure,
    must be visible (logged, not just raised and possibly swallowed by
    a caller) - this is the one shared choke point every Radarr/Sonarr/
    Tautulli/MDBList request goes through (Trakt/Simkl have their own
    equivalent coverage in tests/test_trakt.py / tests/test_simkl.py)."""

    def test_401_logs_authentication_failure(self, client, caplog):
        import logging

        resp = _make_response(401, "http://radarr.local/api")
        with (
            patch("utils.api_client.requests.request", return_value=resp),
            caplog.at_level(logging.ERROR, logger="curatarr"),
        ):
            with pytest.raises(_FakeAPIError):
                client._make_request_to_url("GET", "http://radarr.local/api")

        assert "FakeAPI" in caplog.text
        assert "authentication failed" in caplog.text.lower()

    def test_401_log_line_never_contains_the_api_key(self, client, caplog):
        import logging

        resp = _make_response(401, "http://radarr.local/api")
        with (
            patch("utils.api_client.requests.request", return_value=resp),
            caplog.at_level(logging.ERROR, logger="curatarr"),
        ):
            with pytest.raises(_FakeAPIError):
                client._make_request_to_url("GET", "http://radarr.local/api")

        assert "super-secret-key" not in caplog.text

    def test_5xx_logs_the_api_error(self, client, caplog):
        import logging

        resp = _make_response(500, "http://radarr.local/api")
        resp.text = "Internal Server Error"
        with (
            patch("utils.api_client.requests.request", return_value=resp),
            caplog.at_level(logging.ERROR, logger="curatarr"),
        ):
            with pytest.raises(_FakeAPIError):
                client._make_request_to_url("GET", "http://radarr.local/api")

        assert "500" in caplog.text

    def test_connection_error_is_logged(self, client, caplog):
        import logging

        import requests

        with (
            patch("utils.api_client.requests.request", side_effect=requests.exceptions.ConnectionError("refused")),
            caplog.at_level(logging.ERROR, logger="curatarr"),
        ):
            with pytest.raises(_FakeAPIError):
                client._make_request_to_url("GET", "http://radarr.local/api")

        assert "FakeAPI" in caplog.text
        assert "could not connect" in caplog.text.lower()

    def test_timeout_is_logged(self, client, caplog):
        import logging

        import requests

        with (
            patch("utils.api_client.requests.request", side_effect=requests.exceptions.Timeout("timed out")),
            caplog.at_level(logging.ERROR, logger="curatarr"),
        ):
            with pytest.raises(_FakeAPIError):
                client._make_request_to_url("GET", "http://radarr.local/api")

        assert "FakeAPI" in caplog.text
        assert "timed out" in caplog.text.lower() or "timeout" in caplog.text.lower()

    def test_404_is_not_logged_as_an_error(self, client, caplog):
        """A 404 is a normal, expected \"not found\" outcome for these
        clients (see _handle_response) - not a failure worth surfacing."""
        import logging

        resp = _make_response(404, "http://radarr.local/api")
        with (
            patch("utils.api_client.requests.request", return_value=resp),
            caplog.at_level(logging.ERROR, logger="curatarr"),
        ):
            result = client._make_request_to_url("GET", "http://radarr.local/api")

        assert result is None
        assert caplog.text == ""
