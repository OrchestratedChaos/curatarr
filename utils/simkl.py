# curatarr
# Copyright (C) 2026 OrchestratedChaos
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Simkl API client for Curatarr.
Handles PIN authentication, token management, and API requests.
Provides watch history import, discovery, and list export for anime/TV/movies.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from .api_client import BaseAPIClient
from .display import log_error
from .metrics import record_api_call

logger = logging.getLogger("curatarr")

# Simkl API endpoints
SIMKL_API_URL = "https://api.simkl.com"
SIMKL_AUTH_URL = "https://simkl.com"

# Rate limiting: 0.2s delay between requests
SIMKL_RATE_LIMIT_DELAY = 0.2

# HTTP request timeout in seconds
SIMKL_REQUEST_TIMEOUT = 30

# Bounds the 429 (rate limited) retry loop in _make_request - matching
# the pattern already used correctly in utils/tmdb.py's
# fetch_tmdb_with_retry (and utils/trakt.py's own _make_request, fixed
# alongside this). Without a cap, a 429 response recursed forever,
# sleeping for however long the server's own Retry-After header said to
# (see SIMKL_MAX_RETRY_AFTER_SECONDS below for why that alone isn't
# trustworthy either).
SIMKL_MAX_429_RETRIES = 3

# Ceiling on how long a single 429 retry will ever sleep for, regardless
# of what the server's Retry-After header asks for - server-controlled
# input, and a compromised/misbehaving Simkl endpoint (or a malicious
# response) could otherwise stall this process for an arbitrary/huge
# amount of time.
SIMKL_MAX_RETRY_AFTER_SECONDS = 60


class SimklAuthError(Exception):
    """Raised when Simkl authentication fails."""

    pass


class SimklAPIError(Exception):
    """Raised when Simkl API request fails."""

    pass


class SimklClient(BaseAPIClient):
    """
    Simkl API client with PIN authentication.

    PIN auth works in Docker/SSH environments without browser redirects.
    """

    api_name = "Simkl"
    exception_class = SimklAPIError
    rate_limit_delay = SIMKL_RATE_LIMIT_DELAY
    request_timeout = SIMKL_REQUEST_TIMEOUT
    max_429_retries = SIMKL_MAX_429_RETRIES
    max_retry_after_seconds = SIMKL_MAX_RETRY_AFTER_SECONDS

    def __init__(
        self,
        client_id: str,
        access_token: Optional[str] = None,
        token_callback: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize Simkl client.

        Args:
            client_id: Simkl API application client ID
            access_token: Existing access token (optional)
            token_callback: Function to call when tokens are updated (for saving)
        """
        super().__init__()
        self.client_id = client_id
        self.access_token = access_token
        self.token_callback = token_callback

    @property
    def is_authenticated(self) -> bool:
        """Check if client has valid tokens."""
        return self.access_token is not None

    def _get_headers(self, authenticated: bool = True) -> Dict[str, str]:
        """Get headers for API requests."""
        headers = {"Content-Type": "application/json", "simkl-api-key": self.client_id}
        if authenticated and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    # _rate_limit() inherited from BaseAPIClient (rate_limit_delay set above)

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        authenticated: bool = True,
    ) -> Any:
        """
        Make an API request with rate limiting and error handling.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            endpoint: API endpoint (without base URL)
            data: Request body data
            params: Query parameters
            authenticated: Whether to include auth header

        Returns:
            Response JSON or None for 204 responses

        Raises:
            SimklAPIError: If still rate limited (429) after
                SIMKL_MAX_429_RETRIES retries, or on any other API error.
        """
        url = f"{SIMKL_API_URL}{endpoint}"
        headers = self._get_headers(authenticated)

        # Add client_id to params for unauthenticated requests
        if params is None:
            params = {}
        if not authenticated:
            params["client_id"] = self.client_id

        # curatarr_api_requests_total/curatarr_api_request_duration_seconds
        # (see utils/metrics.py) - see utils/trakt.py's _make_request for
        # the identical reasoning (same pattern intentionally mirrored
        # here): outcome only flips to 'success' on an actually-handled
        # response (2xx/204, and 404 which this client treats as a valid
        # "not found" None return, not an error), so every raised path
        # is recorded as 'error'.
        request_start = time.time()
        outcome = "error"
        try:
            # Rate-limited request with the shared bounded 429-retry
            # loop (see BaseAPIClient._send_with_retries) - returns the
            # raw response instead of raising, so the status handling
            # below (401/404/error mapping specific to Simkl) stays
            # unchanged.
            response = self._send_with_retries(method, url, data=data, params=params, headers=headers)

            # Handle auth errors
            if response.status_code == 401:
                # #284: logged HERE (the one choke point every Simkl
                # request goes through) - a bad/expired token must be
                # visible rather than surfacing only as a silently
                # empty import/export.
                log_error("Simkl: authentication failed (invalid or expired token)")
                raise SimklAuthError("Invalid or expired Simkl token")

            # Handle not found
            if response.status_code == 404:
                outcome = "success"
                return None

            # Handle other errors
            if response.status_code >= 400:
                log_error(f"Simkl API error {response.status_code}: {response.text}")
                raise SimklAPIError(f"Simkl API error {response.status_code}: {response.text}")

            # Return JSON or None for no-content responses
            outcome = "success"
            if response.status_code == 204:
                return None

            return response.json()

        except requests.exceptions.Timeout as e:
            log_error(f"Simkl: request timed out after {SIMKL_REQUEST_TIMEOUT}s")
            raise SimklAPIError(f"Request timeout after {SIMKL_REQUEST_TIMEOUT}s") from e
        except requests.exceptions.ConnectionError as e:
            log_error(f"Simkl: could not connect - {e}")
            raise SimklAPIError("Could not connect to Simkl API") from e
        except requests.exceptions.RequestException as e:
            log_error(f"Simkl: request failed - {e}")
            raise SimklAPIError(f"Simkl request failed: {e}") from e
        finally:
            record_api_call("simkl", outcome, time.time() - request_start)

    # =========================================================================
    # PIN Authentication Flow
    # =========================================================================

    def get_pin_code(self) -> Dict[str, Any]:
        """
        Start PIN authentication flow.

        Returns:
            Dict with user_code, verification_url, expires_in, interval
        """
        url = f"{SIMKL_API_URL}/oauth/pin"
        params = {"client_id": self.client_id}

        try:
            response = requests.get(
                url,
                params=params,
                timeout=SIMKL_REQUEST_TIMEOUT,
                allow_redirects=False,
            )

            if response.status_code != 200:
                raise SimklAuthError(f"Failed to get PIN code: {response.text}")

            data = response.json()
            return {
                "user_code": data.get("user_code"),
                "verification_url": data.get("verification_url", "https://simkl.com/pin"),
                "expires_in": data.get("expires_in", 900),
                "interval": data.get("interval", 5),
            }

        except requests.RequestException as e:
            raise SimklAuthError(f"Failed to get PIN code: {e}") from e

    def poll_for_token(self, user_code: str, interval: int = 5, expires_in: int = 900) -> bool:
        """
        Poll for user authorization completion.

        Args:
            user_code: User code from get_pin_code()
            interval: Polling interval in seconds
            expires_in: Expiration time in seconds

        Returns:
            True if authorized, False if expired/denied
        """
        start_time = time.time()
        url = f"{SIMKL_API_URL}/oauth/pin/{user_code}"
        params = {"client_id": self.client_id}

        while time.time() - start_time < expires_in:
            try:
                response = requests.get(
                    url,
                    params=params,
                    timeout=SIMKL_REQUEST_TIMEOUT,
                    allow_redirects=False,
                )

                if response.status_code == 200:
                    data = response.json()
                    result = data.get("result")

                    if result == "OK":
                        # Success - got token
                        self.access_token = data.get("access_token")

                        # Notify callback to save token
                        if self.token_callback:
                            self.token_callback(self.access_token)

                        return True

                    elif result == "KO":
                        # Still waiting for user
                        time.sleep(interval)
                        continue

                else:
                    # Error response
                    time.sleep(interval)
                    continue

            except requests.RequestException:
                time.sleep(interval)
                continue

        return False  # Timed out

    # =========================================================================
    # User Info
    # =========================================================================

    def get_user_settings(self) -> Dict[str, Any]:
        """Get authenticated user's settings."""
        return self._make_request("GET", "/users/settings")

    def test_connection(self) -> bool:
        """
        Test connection to Simkl API.

        Returns:
            True if connection successful
        """
        try:
            result = self.get_user_settings()
            return result is not None
        except (SimklAPIError, SimklAuthError):
            return False

    # =========================================================================
    # Watch History
    # =========================================================================

    def get_all_items(self) -> Dict[str, Any]:
        """
        Get all items in user's Simkl library.

        Returns:
            Dict with 'movies', 'shows', 'anime' lists
        """
        return self._make_request("GET", "/sync/all-items") or {}

    def get_watched_movies(self) -> List[Dict[str, Any]]:
        """
        Get user's watched movies.

        Returns:
            List of movie objects with ids and metadata
        """
        all_items = self.get_all_items()
        return all_items.get("movies", [])

    def get_watched_shows(self) -> List[Dict[str, Any]]:
        """
        Get user's watched TV shows.

        Returns:
            List of show objects with ids and metadata
        """
        all_items = self.get_all_items()
        return all_items.get("shows", [])

    def get_watched_anime(self) -> List[Dict[str, Any]]:
        """
        Get user's watched anime.

        Returns:
            List of anime objects with ids and metadata
        """
        all_items = self.get_all_items()
        return all_items.get("anime", [])

    def add_to_history(self, movies: Optional[List[Dict]] = None, shows: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Add items to watch history.

        Args:
            movies: List of movie objects with 'ids' (tmdb, imdb, etc.)
            shows: List of show objects with 'ids'

        Returns:
            Response with added counts
        """
        data = {}
        if movies:
            data["movies"] = movies
        if shows:
            data["shows"] = shows

        if not data:
            return {"added": {"movies": 0, "shows": 0}}

        return self._make_request("POST", "/sync/history", data)

    def get_watch_history_ids(self, media_type: str = "movies", id_type: str = "tmdb") -> set:
        """
        Get set of IDs from user's Simkl watch history.

        Args:
            media_type: 'movies', 'shows', or 'anime'
            id_type: 'tmdb', 'imdb', 'mal', etc.

        Returns:
            Set of IDs for watched items
        """
        ids = set()

        if media_type == "movies":
            watched = self.get_watched_movies()
        elif media_type == "anime":
            watched = self.get_watched_anime()
        else:
            watched = self.get_watched_shows()

        for item in watched:
            item_ids = item.get("ids", {})
            item_id = item_ids.get(id_type)
            if item_id:
                ids.add(item_id)

        return ids

    # =========================================================================
    # Watchlist / Plan to Watch
    # =========================================================================

    def add_to_watchlist(
        self, movies: Optional[List[Dict]] = None, shows: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        Add items to watchlist (plan to watch).

        Args:
            movies: List of movie objects with 'ids' (tmdb, imdb, etc.)
            shows: List of show objects with 'ids'

        Returns:
            Response with added counts
        """
        data = {}
        if movies:
            data["movies"] = [{"to": "plantowatch", **m} for m in movies]
        if shows:
            data["shows"] = [{"to": "plantowatch", **s} for s in shows]

        if not data:
            return {"added": {"movies": 0, "shows": 0}}

        return self._make_request("POST", "/sync/add-to-list", data)

    # =========================================================================
    # Discovery - Trending/Popular
    # =========================================================================

    def get_trending(self, media_type: str = "tv", interval: str = "week") -> List[Dict[str, Any]]:
        """
        Get trending content.

        Args:
            media_type: 'tv', 'anime', or 'movie'
            interval: 'day', 'week', 'month', 'year', 'all'

        Returns:
            List of trending items with ids and metadata
        """
        endpoint = f"/{media_type}/trending/{interval}"
        return self._make_request("GET", endpoint, authenticated=False) or []

    def get_best(self, media_type: str = "tv", filter_type: str = "all") -> List[Dict[str, Any]]:
        """
        Get best rated content.

        Args:
            media_type: 'tv', 'anime', or 'movie'
            filter_type: 'all', 'watched', 'new', etc.

        Returns:
            List of best items with ids and metadata
        """
        endpoint = f"/{media_type}/best/{filter_type}"
        return self._make_request("GET", endpoint, authenticated=False) or []

    def get_anime_trending(self, interval: str = "week") -> List[Dict[str, Any]]:
        """Get trending anime."""
        return self.get_trending("anime", interval)

    def get_anime_best(self) -> List[Dict[str, Any]]:
        """Get best rated anime."""
        return self.get_best("anime", "all")

    # =========================================================================
    # Search / ID Lookup
    # =========================================================================

    def search_by_id(
        self, tmdb_id: Optional[int] = None, imdb_id: Optional[str] = None, media_type: str = "movie"
    ) -> Optional[Dict[str, Any]]:
        """
        Look up content by external ID.

        Args:
            tmdb_id: TMDB ID
            imdb_id: IMDB ID (tt...)
            media_type: 'movie', 'show', or 'anime'

        Returns:
            Content object or None if not found
        """
        params: Dict[str, Any] = {}
        if tmdb_id:
            params["tmdb"] = tmdb_id
            params["type"] = media_type
        elif imdb_id:
            params["imdb"] = imdb_id

        if not params:
            return None

        result = self._make_request("GET", "/search/id", params=params, authenticated=False)

        if result and isinstance(result, list) and len(result) > 0:
            return result[0]
        return result

    def search(self, query: str, media_type: str = "tv") -> List[Dict[str, Any]]:
        """
        Search for content by text query.

        Args:
            query: Search query
            media_type: 'movie', 'tv', or 'anime'

        Returns:
            List of matching items
        """
        params = {"q": query}
        return self._make_request("GET", f"/search/{media_type}", params=params, authenticated=False) or []


def create_simkl_client(config: Dict) -> Optional[SimklClient]:
    """
    Create a Simkl client from config.

    Args:
        config: Full config dict containing 'simkl' section

    Returns:
        SimklClient if configured and enabled, None otherwise
    """
    simkl_config = config.get("simkl", {})

    if not simkl_config.get("enabled", False):
        return None

    client_id = simkl_config.get("client_id")
    if not client_id or client_id == "YOUR_SIMKL_CLIENT_ID":
        return None

    access_token = simkl_config.get("access_token")

    return SimklClient(client_id=client_id, access_token=access_token)


def get_authenticated_simkl_client(
    config: Dict, token_callback: Optional[Callable[[str], None]] = None
) -> Optional[SimklClient]:
    """
    Get or create an authenticated Simkl client.

    Args:
        config: Full config dict containing 'simkl' section
        token_callback: Function to call when tokens are updated

    Returns:
        Authenticated SimklClient or None
    """
    simkl_config = config.get("simkl", {})

    if not simkl_config.get("enabled", False):
        return None

    client_id = simkl_config.get("client_id")
    if not client_id or client_id == "YOUR_SIMKL_CLIENT_ID":
        return None

    access_token = simkl_config.get("access_token")

    client = SimklClient(client_id=client_id, access_token=access_token, token_callback=token_callback)

    # Verify token is still valid
    if access_token and client.test_connection():
        return client

    return None
