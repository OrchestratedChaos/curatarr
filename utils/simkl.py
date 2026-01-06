"""
Simkl API client for Curatarr.
Handles PIN authentication, token management, and API requests.
Provides watch history import, discovery, and list export for anime/TV/movies.
"""

import time
import logging
import requests
from typing import Dict, Optional, Any, List

logger = logging.getLogger('curatarr')

# Simkl API endpoints
SIMKL_API_URL = "https://api.simkl.com"
SIMKL_AUTH_URL = "https://simkl.com"

# Rate limiting: 0.2s delay between requests
SIMKL_RATE_LIMIT_DELAY = 0.2

# HTTP request timeout in seconds
SIMKL_REQUEST_TIMEOUT = 30


class SimklAuthError(Exception):
    """Raised when Simkl authentication fails."""
    pass


class SimklAPIError(Exception):
    """Raised when Simkl API request fails."""
    pass


class SimklClient:
    """
    Simkl API client with PIN authentication.

    PIN auth works in Docker/SSH environments without browser redirects.
    """

    def __init__(self, client_id: str,
                 access_token: Optional[str] = None,
                 token_callback: Optional[callable] = None):
        """
        Initialize Simkl client.

        Args:
            client_id: Simkl API application client ID
            access_token: Existing access token (optional)
            token_callback: Function to call when tokens are updated (for saving)
        """
        self.client_id = client_id
        self.access_token = access_token
        self.token_callback = token_callback
        self._last_request_time = 0

    @property
    def is_authenticated(self) -> bool:
        """Check if client has valid tokens."""
        return self.access_token is not None

    def _get_headers(self, authenticated: bool = True) -> Dict[str, str]:
        """Get headers for API requests."""
        headers = {
            "Content-Type": "application/json",
            "simkl-api-key": self.client_id
        }
        if authenticated and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < SIMKL_RATE_LIMIT_DELAY:
            time.sleep(SIMKL_RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _make_request(self, method: str, endpoint: str,
                      data: Optional[Dict] = None,
                      params: Optional[Dict] = None,
                      authenticated: bool = True) -> Any:
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
        """
        self._rate_limit()

        url = f"{SIMKL_API_URL}{endpoint}"
        headers = self._get_headers(authenticated)

        # Add client_id to params for unauthenticated requests
        if params is None:
            params = {}
        if not authenticated:
            params['client_id'] = self.client_id

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=data,
                params=params,
                timeout=SIMKL_REQUEST_TIMEOUT
            )

            # Handle rate limiting
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 1))
                logger.warning(f"Simkl rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
                return self._make_request(method, endpoint, data, params, authenticated)

            # Handle auth errors
            if response.status_code == 401:
                raise SimklAuthError("Invalid or expired Simkl token")

            # Handle not found
            if response.status_code == 404:
                return None

            # Handle other errors
            if response.status_code >= 400:
                raise SimklAPIError(f"Simkl API error {response.status_code}: {response.text}")

            # Return JSON or None for no-content responses
            if response.status_code == 204:
                return None

            return response.json()

        except requests.exceptions.Timeout:
            raise SimklAPIError(f"Request timeout after {SIMKL_REQUEST_TIMEOUT}s")
        except requests.exceptions.ConnectionError:
            raise SimklAPIError("Could not connect to Simkl API")
        except requests.exceptions.RequestException as e:
            raise SimklAPIError(f"Simkl request failed: {e}")

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
                timeout=SIMKL_REQUEST_TIMEOUT
            )

            if response.status_code != 200:
                raise SimklAuthError(f"Failed to get PIN code: {response.text}")

            data = response.json()
            return {
                "user_code": data.get("user_code"),
                "verification_url": data.get("verification_url", "https://simkl.com/pin"),
                "expires_in": data.get("expires_in", 900),
                "interval": data.get("interval", 5)
            }

        except requests.RequestException as e:
            raise SimklAuthError(f"Failed to get PIN code: {e}")

    def poll_for_token(self, user_code: str, interval: int = 5,
                       expires_in: int = 900) -> bool:
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
                    timeout=SIMKL_REQUEST_TIMEOUT
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

    def add_to_history(self, movies: Optional[List[Dict]] = None,
                       shows: Optional[List[Dict]] = None) -> Dict[str, Any]:
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

    def get_watch_history_ids(self, media_type: str = 'movies',
                              id_type: str = 'tmdb') -> set:
        """
        Get set of IDs from user's Simkl watch history.

        Args:
            media_type: 'movies', 'shows', or 'anime'
            id_type: 'tmdb', 'imdb', 'mal', etc.

        Returns:
            Set of IDs for watched items
        """
        ids = set()

        if media_type == 'movies':
            watched = self.get_watched_movies()
        elif media_type == 'anime':
            watched = self.get_watched_anime()
        else:
            watched = self.get_watched_shows()

        for item in watched:
            item_ids = item.get('ids', {})
            item_id = item_ids.get(id_type)
            if item_id:
                ids.add(item_id)

        return ids

    # =========================================================================
    # Watchlist / Plan to Watch
    # =========================================================================

    def add_to_watchlist(self, movies: Optional[List[Dict]] = None,
                         shows: Optional[List[Dict]] = None) -> Dict[str, Any]:
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

    def get_trending(self, media_type: str = 'tv',
                     interval: str = 'week') -> List[Dict[str, Any]]:
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

    def get_best(self, media_type: str = 'tv',
                 filter_type: str = 'all') -> List[Dict[str, Any]]:
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

    def get_anime_trending(self, interval: str = 'week') -> List[Dict[str, Any]]:
        """Get trending anime."""
        return self.get_trending('anime', interval)

    def get_anime_best(self) -> List[Dict[str, Any]]:
        """Get best rated anime."""
        return self.get_best('anime', 'all')

    # =========================================================================
    # Search / ID Lookup
    # =========================================================================

    def search_by_id(self, tmdb_id: Optional[int] = None,
                     imdb_id: Optional[str] = None,
                     media_type: str = 'movie') -> Optional[Dict[str, Any]]:
        """
        Look up content by external ID.

        Args:
            tmdb_id: TMDB ID
            imdb_id: IMDB ID (tt...)
            media_type: 'movie', 'show', or 'anime'

        Returns:
            Content object or None if not found
        """
        params = {}
        if tmdb_id:
            params['tmdb'] = tmdb_id
            params['type'] = media_type
        elif imdb_id:
            params['imdb'] = imdb_id

        if not params:
            return None

        result = self._make_request("GET", "/search/id", params=params, authenticated=False)

        if result and isinstance(result, list) and len(result) > 0:
            return result[0]
        return result

    def search(self, query: str, media_type: str = 'tv') -> List[Dict[str, Any]]:
        """
        Search for content by text query.

        Args:
            query: Search query
            media_type: 'movie', 'tv', or 'anime'

        Returns:
            List of matching items
        """
        params = {'q': query}
        return self._make_request("GET", f"/search/{media_type}",
                                  params=params, authenticated=False) or []


def create_simkl_client(config: Dict) -> Optional[SimklClient]:
    """
    Create a Simkl client from config.

    Args:
        config: Full config dict containing 'simkl' section

    Returns:
        SimklClient if configured and enabled, None otherwise
    """
    simkl_config = config.get('simkl', {})

    if not simkl_config.get('enabled', False):
        return None

    client_id = simkl_config.get('client_id')
    if not client_id or client_id == 'YOUR_SIMKL_CLIENT_ID':
        return None

    access_token = simkl_config.get('access_token')

    return SimklClient(
        client_id=client_id,
        access_token=access_token
    )


def get_authenticated_simkl_client(config: Dict,
                                   token_callback: Optional[callable] = None
                                   ) -> Optional[SimklClient]:
    """
    Get or create an authenticated Simkl client.

    Args:
        config: Full config dict containing 'simkl' section
        token_callback: Function to call when tokens are updated

    Returns:
        Authenticated SimklClient or None
    """
    simkl_config = config.get('simkl', {})

    if not simkl_config.get('enabled', False):
        return None

    client_id = simkl_config.get('client_id')
    if not client_id or client_id == 'YOUR_SIMKL_CLIENT_ID':
        return None

    access_token = simkl_config.get('access_token')

    client = SimklClient(
        client_id=client_id,
        access_token=access_token,
        token_callback=token_callback
    )

    # Verify token is still valid
    if access_token and client.test_connection():
        return client

    return None
