"""Tests for utils/simkl.py - Simkl API client."""

import pytest
from unittest.mock import Mock, patch
import requests

from utils.simkl import (
    SimklClient,
    SimklAuthError,
    SimklAPIError,
    create_simkl_client,
    get_authenticated_simkl_client,
    SIMKL_RATE_LIMIT_DELAY,
    SIMKL_REQUEST_TIMEOUT,
    SIMKL_API_URL,
)


class TestSimklClientInit:
    """Tests for SimklClient initialization."""

    def test_init_with_client_id(self):
        """Test initialization with client ID."""
        client = SimklClient(client_id="test_client_id")
        assert client.client_id == "test_client_id"
        assert client.access_token is None
        assert client._last_request_time == 0

    def test_init_with_access_token(self):
        """Test initialization with access token."""
        client = SimklClient(
            client_id="test_id",
            access_token="test_token"
        )
        assert client.access_token == "test_token"

    def test_is_authenticated_with_token(self):
        """Test is_authenticated returns True with token."""
        client = SimklClient("id", access_token="token")
        assert client.is_authenticated is True

    def test_is_authenticated_without_token(self):
        """Test is_authenticated returns False without token."""
        client = SimklClient("id")
        assert client.is_authenticated is False


class TestSimklClientHeaders:
    """Tests for header generation."""

    def test_headers_include_api_key(self):
        """Test headers include simkl-api-key."""
        client = SimklClient("my_client_id")
        headers = client._get_headers(authenticated=False)

        assert headers["Content-Type"] == "application/json"
        assert headers["simkl-api-key"] == "my_client_id"

    def test_headers_include_bearer_token(self):
        """Test headers include Authorization when authenticated."""
        client = SimklClient("id", access_token="my_token")
        headers = client._get_headers(authenticated=True)

        assert headers["Authorization"] == "Bearer my_token"

    def test_headers_exclude_bearer_when_not_authenticated(self):
        """Test headers exclude Authorization when not authenticated."""
        client = SimklClient("id", access_token="token")
        headers = client._get_headers(authenticated=False)

        assert "Authorization" not in headers


class TestSimklClientRateLimit:
    """Tests for rate limiting."""

    @patch('utils.simkl.time.sleep')
    @patch('utils.simkl.time.time')
    def test_rate_limit_sleeps_when_needed(self, mock_time, mock_sleep):
        """Test rate limiting enforces delay between requests."""
        client = SimklClient("id")
        client._last_request_time = 100.0

        mock_time.side_effect = [100.1, 100.2]  # 0.1s since last request

        client._rate_limit()

        mock_sleep.assert_called_once()
        sleep_time = mock_sleep.call_args[0][0]
        assert sleep_time == pytest.approx(SIMKL_RATE_LIMIT_DELAY - 0.1, abs=0.01)

    @patch('utils.simkl.time.sleep')
    @patch('utils.simkl.time.time')
    def test_rate_limit_no_sleep_when_enough_time_passed(self, mock_time, mock_sleep):
        """Test no sleep when enough time has passed."""
        client = SimklClient("id")
        client._last_request_time = 100.0

        mock_time.return_value = 100.5  # 0.5s since last request

        client._rate_limit()

        mock_sleep.assert_not_called()


class TestSimklClientMakeRequest:
    """Tests for API request handling."""

    @patch('utils.simkl.requests.request')
    def test_successful_request(self, mock_request):
        """Test successful API request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        mock_request.return_value = mock_response

        client = SimklClient("id", access_token="token")
        result = client._make_request("GET", "/users/settings")

        assert result == {"status": "ok"}
        mock_request.assert_called_once()

    @patch('utils.simkl.requests.request')
    def test_unauthorized_raises_auth_error(self, mock_request):
        """Test 401 raises SimklAuthError."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_request.return_value = mock_response

        client = SimklClient("id", access_token="bad_token")

        with pytest.raises(SimklAuthError, match="Invalid or expired"):
            client._make_request("GET", "/users/settings")

    @patch('utils.simkl.requests.request')
    def test_404_returns_none(self, mock_request):
        """Test 404 returns None."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_request.return_value = mock_response

        client = SimklClient("id")
        result = client._make_request("GET", "/search/id", authenticated=False)

        assert result is None

    @patch('utils.simkl.requests.request')
    def test_204_returns_none(self, mock_request):
        """Test 204 No Content returns None."""
        mock_response = Mock()
        mock_response.status_code = 204
        mock_request.return_value = mock_response

        client = SimklClient("id", access_token="token")
        result = client._make_request("POST", "/sync/history")

        assert result is None

    @patch('utils.simkl.requests.request')
    def test_error_response_raises_api_error(self, mock_request):
        """Test error responses raise SimklAPIError."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"
        mock_request.return_value = mock_response

        client = SimklClient("id")

        with pytest.raises(SimklAPIError, match="400"):
            client._make_request("POST", "/sync/history")

    @patch('utils.simkl.requests.request')
    def test_timeout_raises_api_error(self, mock_request):
        """Test timeout raises SimklAPIError."""
        mock_request.side_effect = requests.exceptions.Timeout()

        client = SimklClient("id")

        with pytest.raises(SimklAPIError, match="timeout"):
            client._make_request("GET", "/users/settings")

    @patch('utils.simkl.requests.request')
    def test_connection_error_raises_api_error(self, mock_request):
        """Test connection error raises SimklAPIError."""
        mock_request.side_effect = requests.exceptions.ConnectionError()

        client = SimklClient("id")

        with pytest.raises(SimklAPIError, match="Could not connect"):
            client._make_request("GET", "/users/settings")

    @patch('utils.simkl.requests.request')
    def test_rate_limit_retries(self, mock_request):
        """Test 429 rate limit triggers retry."""
        rate_limit_response = Mock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {"Retry-After": "1"}

        success_response = Mock()
        success_response.status_code = 200
        success_response.json.return_value = {"ok": True}

        mock_request.side_effect = [rate_limit_response, success_response]

        client = SimklClient("id")
        result = client._make_request("GET", "/test", authenticated=False)

        assert result == {"ok": True}
        assert mock_request.call_count == 2

    @patch('utils.simkl.requests.request')
    def test_generic_request_exception(self, mock_request):
        """Test generic RequestException raises SimklAPIError."""
        mock_request.side_effect = requests.exceptions.RequestException("Something broke")

        client = SimklClient("id")

        with pytest.raises(SimklAPIError, match="request failed"):
            client._make_request("GET", "/test", authenticated=False)


class TestSimklClientPinAuth:
    """Tests for PIN authentication flow."""

    @patch('utils.simkl.requests.get')
    def test_get_pin_code_success(self, mock_get):
        """Test successful PIN code retrieval."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "user_code": "ABC123",
            "verification_url": "https://simkl.com/pin",
            "expires_in": 900,
            "interval": 5
        }
        mock_get.return_value = mock_response

        client = SimklClient("my_client_id")
        result = client.get_pin_code()

        assert result["user_code"] == "ABC123"
        assert result["verification_url"] == "https://simkl.com/pin"
        assert result["expires_in"] == 900

    @patch('utils.simkl.requests.get')
    def test_get_pin_code_error(self, mock_get):
        """Test PIN code error handling."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Invalid client"
        mock_get.return_value = mock_response

        client = SimklClient("bad_id")

        with pytest.raises(SimklAuthError, match="Failed to get PIN"):
            client.get_pin_code()

    @patch('utils.simkl.requests.get')
    def test_poll_for_token_success(self, mock_get):
        """Test successful token polling."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": "OK",
            "access_token": "my_access_token"
        }
        mock_get.return_value = mock_response

        client = SimklClient("id")
        result = client.poll_for_token("ABC123", interval=1, expires_in=5)

        assert result is True
        assert client.access_token == "my_access_token"

    @patch('utils.simkl.requests.get')
    @patch('utils.simkl.time.sleep')
    @patch('utils.simkl.time.time')
    def test_poll_for_token_waits(self, mock_time, mock_sleep, mock_get):
        """Test polling waits when user hasn't authorized."""
        pending_response = Mock()
        pending_response.status_code = 200
        pending_response.json.return_value = {"result": "KO"}

        success_response = Mock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "result": "OK",
            "access_token": "token"
        }

        mock_get.side_effect = [pending_response, success_response]
        mock_time.side_effect = [0, 1, 2]  # Simulate time passing

        client = SimklClient("id")
        result = client.poll_for_token("ABC", interval=1, expires_in=10)

        assert result is True
        mock_sleep.assert_called()

    @patch('utils.simkl.time.time')
    def test_poll_for_token_timeout(self, mock_time):
        """Test polling times out."""
        mock_time.side_effect = [0, 100]  # Immediately expired

        client = SimklClient("id")
        result = client.poll_for_token("ABC", expires_in=10)

        assert result is False


class TestSimklClientWatchHistory:
    """Tests for watch history methods."""

    @patch.object(SimklClient, '_make_request')
    def test_get_all_items(self, mock_request):
        """Test fetching all items."""
        mock_request.return_value = {
            "movies": [{"title": "Movie 1"}],
            "shows": [{"title": "Show 1"}],
            "anime": [{"title": "Anime 1"}]
        }

        client = SimklClient("id", access_token="token")
        result = client.get_all_items()

        assert len(result["movies"]) == 1
        assert len(result["shows"]) == 1
        assert len(result["anime"]) == 1
        mock_request.assert_called_once_with("GET", "/sync/all-items")

    @patch.object(SimklClient, 'get_all_items')
    def test_get_watched_movies(self, mock_all_items):
        """Test getting watched movies."""
        mock_all_items.return_value = {
            "movies": [{"title": "Movie 1"}, {"title": "Movie 2"}],
            "shows": []
        }

        client = SimklClient("id", access_token="token")
        result = client.get_watched_movies()

        assert len(result) == 2

    @patch.object(SimklClient, 'get_all_items')
    def test_get_watched_anime(self, mock_all_items):
        """Test getting watched anime."""
        mock_all_items.return_value = {
            "anime": [{"title": "Anime 1"}],
            "movies": []
        }

        client = SimklClient("id", access_token="token")
        result = client.get_watched_anime()

        assert len(result) == 1
        assert result[0]["title"] == "Anime 1"

    @patch.object(SimklClient, 'get_all_items')
    def test_get_watched_shows(self, mock_all_items):
        """Test getting watched TV shows."""
        mock_all_items.return_value = {
            "shows": [{"title": "Show 1"}, {"title": "Show 2"}],
            "movies": []
        }

        client = SimklClient("id", access_token="token")
        result = client.get_watched_shows()

        assert len(result) == 2
        assert result[0]["title"] == "Show 1"

    @patch.object(SimklClient, '_make_request')
    def test_add_to_history(self, mock_request):
        """Test adding items to history."""
        mock_request.return_value = {"added": {"movies": 2, "shows": 1}}

        client = SimklClient("id", access_token="token")
        movies = [{"ids": {"tmdb": 123}}, {"ids": {"tmdb": 456}}]
        shows = [{"ids": {"tmdb": 789}}]
        result = client.add_to_history(movies=movies, shows=shows)

        assert result["added"]["movies"] == 2
        mock_request.assert_called_once()

    def test_add_to_history_empty(self):
        """Test adding empty items returns zeros."""
        client = SimklClient("id", access_token="token")
        result = client.add_to_history()

        assert result == {"added": {"movies": 0, "shows": 0}}

    @patch.object(SimklClient, 'get_watched_movies')
    def test_get_watch_history_ids(self, mock_watched):
        """Test extracting IDs from watch history."""
        mock_watched.return_value = [
            {"ids": {"tmdb": 123, "imdb": "tt123"}},
            {"ids": {"tmdb": 456, "imdb": "tt456"}},
            {"ids": {"imdb": "tt789"}}  # No TMDB
        ]

        client = SimklClient("id", access_token="token")
        result = client.get_watch_history_ids(media_type='movies', id_type='tmdb')

        assert 123 in result
        assert 456 in result
        assert len(result) == 2


class TestSimklClientWatchlist:
    """Tests for watchlist methods."""

    @patch.object(SimklClient, '_make_request')
    def test_add_to_watchlist(self, mock_request):
        """Test adding items to watchlist."""
        mock_request.return_value = {"added": {"movies": 1, "shows": 2}}

        client = SimklClient("id", access_token="token")
        movies = [{"ids": {"tmdb": 123}}]
        shows = [{"ids": {"tmdb": 456}}, {"ids": {"tmdb": 789}}]
        result = client.add_to_watchlist(movies=movies, shows=shows)

        assert result["added"]["movies"] == 1
        assert result["added"]["shows"] == 2

        # Verify "to": "plantowatch" was added - data is positional arg [2]
        call_args = mock_request.call_args[0]
        call_data = call_args[2]  # Third positional arg is data
        assert call_data["movies"][0]["to"] == "plantowatch"

    def test_add_to_watchlist_empty(self):
        """Test adding empty watchlist returns zeros."""
        client = SimklClient("id", access_token="token")
        result = client.add_to_watchlist()

        assert result == {"added": {"movies": 0, "shows": 0}}


class TestSimklClientDiscovery:
    """Tests for discovery methods."""

    @patch.object(SimklClient, '_make_request')
    def test_get_trending(self, mock_request):
        """Test getting trending content."""
        mock_request.return_value = [
            {"title": "Trending Show 1"},
            {"title": "Trending Show 2"}
        ]

        client = SimklClient("id")
        result = client.get_trending(media_type='tv', interval='week')

        assert len(result) == 2
        mock_request.assert_called_once_with(
            "GET", "/tv/trending/week", authenticated=False
        )

    @patch.object(SimklClient, '_make_request')
    def test_get_anime_trending(self, mock_request):
        """Test getting trending anime."""
        mock_request.return_value = [{"title": "Anime 1"}]

        client = SimklClient("id")
        result = client.get_anime_trending()

        assert len(result) == 1
        mock_request.assert_called_with(
            "GET", "/anime/trending/week", authenticated=False
        )

    @patch.object(SimklClient, '_make_request')
    def test_get_best(self, mock_request):
        """Test getting best rated content."""
        mock_request.return_value = [{"title": "Best Show"}]

        client = SimklClient("id")
        result = client.get_best(media_type='anime', filter_type='all')

        assert len(result) == 1
        mock_request.assert_called_once_with(
            "GET", "/anime/best/all", authenticated=False
        )


class TestSimklClientSearch:
    """Tests for search methods."""

    @patch.object(SimklClient, '_make_request')
    def test_search_by_tmdb_id(self, mock_request):
        """Test searching by TMDB ID."""
        mock_request.return_value = [{"title": "Found Movie"}]

        client = SimklClient("id")
        result = client.search_by_id(tmdb_id=12345, media_type='movie')

        assert result["title"] == "Found Movie"
        call_params = mock_request.call_args[1]["params"]
        assert call_params["tmdb"] == 12345
        assert call_params["type"] == "movie"

    @patch.object(SimklClient, '_make_request')
    def test_search_by_imdb_id(self, mock_request):
        """Test searching by IMDB ID."""
        mock_request.return_value = [{"title": "Found Show"}]

        client = SimklClient("id")
        result = client.search_by_id(imdb_id="tt1234567")

        assert result["title"] == "Found Show"
        call_params = mock_request.call_args[1]["params"]
        assert call_params["imdb"] == "tt1234567"

    def test_search_by_id_no_params(self):
        """Test search with no IDs returns None."""
        client = SimklClient("id")
        result = client.search_by_id()

        assert result is None

    @patch.object(SimklClient, '_make_request')
    def test_search_by_id_not_found(self, mock_request):
        """Test search returns None when not found."""
        mock_request.return_value = None

        client = SimklClient("id")
        result = client.search_by_id(tmdb_id=99999)

        assert result is None

    @patch.object(SimklClient, '_make_request')
    def test_text_search(self, mock_request):
        """Test text search."""
        mock_request.return_value = [
            {"title": "Result 1"},
            {"title": "Result 2"}
        ]

        client = SimklClient("id")
        result = client.search(query="naruto", media_type='anime')

        assert len(result) == 2
        call_params = mock_request.call_args[1]["params"]
        assert call_params["q"] == "naruto"


class TestSimklClientTestConnection:
    """Tests for connection testing."""

    @patch.object(SimklClient, 'get_user_settings')
    def test_connection_success(self, mock_settings):
        """Test successful connection."""
        mock_settings.return_value = {"user": {"name": "test"}}

        client = SimklClient("id", access_token="token")
        result = client.test_connection()

        assert result is True

    @patch.object(SimklClient, 'get_user_settings')
    def test_connection_failure(self, mock_settings):
        """Test connection failure."""
        mock_settings.side_effect = SimklAPIError("Failed")

        client = SimklClient("id", access_token="token")
        result = client.test_connection()

        assert result is False


class TestCreateSimklClient:
    """Tests for factory function."""

    def test_returns_none_when_disabled(self):
        """Test returns None when Simkl disabled."""
        config = {"simkl": {"enabled": False}}
        result = create_simkl_client(config)
        assert result is None

    def test_returns_none_when_no_config(self):
        """Test returns None when no simkl config."""
        config = {}
        result = create_simkl_client(config)
        assert result is None

    def test_returns_none_when_no_client_id(self):
        """Test returns None when no client ID."""
        config = {"simkl": {"enabled": True}}
        result = create_simkl_client(config)
        assert result is None

    def test_returns_none_for_placeholder_id(self):
        """Test returns None for placeholder client ID."""
        config = {"simkl": {"enabled": True, "client_id": "YOUR_SIMKL_CLIENT_ID"}}
        result = create_simkl_client(config)
        assert result is None

    def test_creates_client_with_valid_config(self):
        """Test creates client with valid configuration."""
        config = {
            "simkl": {
                "enabled": True,
                "client_id": "real_client_id",
                "access_token": "real_token"
            }
        }
        result = create_simkl_client(config)

        assert result is not None
        assert isinstance(result, SimklClient)
        assert result.client_id == "real_client_id"
        assert result.access_token == "real_token"


class TestGetAuthenticatedSimklClient:
    """Tests for authenticated client factory."""

    def test_returns_none_when_disabled(self):
        """Test returns None when disabled."""
        config = {"simkl": {"enabled": False}}
        result = get_authenticated_simkl_client(config)
        assert result is None

    @patch.object(SimklClient, 'test_connection')
    def test_returns_client_when_token_valid(self, mock_test):
        """Test returns client when token is valid."""
        mock_test.return_value = True

        config = {
            "simkl": {
                "enabled": True,
                "client_id": "id",
                "access_token": "valid_token"
            }
        }
        result = get_authenticated_simkl_client(config)

        assert result is not None
        assert result.access_token == "valid_token"

    @patch.object(SimklClient, 'test_connection')
    def test_returns_none_when_token_invalid(self, mock_test):
        """Test returns None when token is invalid."""
        mock_test.return_value = False

        config = {
            "simkl": {
                "enabled": True,
                "client_id": "id",
                "access_token": "invalid_token"
            }
        }
        result = get_authenticated_simkl_client(config)

        assert result is None


class TestSimklConstants:
    """Tests for module constants."""

    def test_rate_limit_delay_is_positive(self):
        """Test rate limit delay is positive."""
        assert SIMKL_RATE_LIMIT_DELAY > 0

    def test_request_timeout_is_reasonable(self):
        """Test request timeout is reasonable."""
        assert 10 <= SIMKL_REQUEST_TIMEOUT <= 60

    def test_api_url_is_https(self):
        """Test API URL uses HTTPS."""
        assert SIMKL_API_URL.startswith("https://")
