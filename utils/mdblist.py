"""
MDBList API client for Curatarr.
Handles exporting recommendations to MDBList.
"""

import logging
import time
import requests
from typing import Dict, Optional, Any, List

logger = logging.getLogger('curatarr')

# Rate limiting: 0.1s delay between requests
MDBLIST_RATE_LIMIT_DELAY = 0.1

# HTTP request timeout in seconds
MDBLIST_REQUEST_TIMEOUT = 30

# API base URL
MDBLIST_API_BASE = "https://api.mdblist.com"


class MDBListAPIError(Exception):
    """Raised when MDBList API request fails."""
    pass


class MDBListClient:
    """
    MDBList API client for exporting recommendations.

    Uses API key authentication via query parameter.
    """

    def __init__(self, api_key: str):
        """
        Initialize MDBList client.

        Args:
            api_key: MDBList API key
        """
        self.api_key = api_key
        self._last_request_time = 0
        self._lists_cache: Optional[List[Dict]] = None

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < MDBLIST_RATE_LIMIT_DELAY:
            time.sleep(MDBLIST_RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _make_request(self, method: str, endpoint: str,
                      data: Optional[Dict] = None,
                      params: Optional[Dict] = None) -> Any:
        """
        Make an API request with rate limiting and error handling.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            endpoint: API endpoint (without base URL)
            data: Request body data
            params: Query parameters

        Returns:
            Response JSON data

        Raises:
            MDBListAPIError: If request fails
        """
        self._rate_limit()

        url = f"{MDBLIST_API_BASE}/{endpoint}"

        # Add API key to params
        if params is None:
            params = {}
        params['apikey'] = self.api_key

        try:
            response = requests.request(
                method=method,
                url=url,
                json=data,
                params=params,
                timeout=MDBLIST_REQUEST_TIMEOUT
            )

            if response.status_code == 401:
                raise MDBListAPIError("Invalid API key")
            elif response.status_code == 404:
                return None
            elif response.status_code >= 400:
                error_msg = response.text
                try:
                    error_data = response.json()
                    if isinstance(error_data, dict):
                        error_msg = error_data.get('error', error_msg)
                except Exception as e:
                    logger.debug(f"Failed to parse error response JSON: {e}")
                raise MDBListAPIError(f"API error {response.status_code}: {error_msg}")

            if response.status_code == 204:
                return None

            return response.json()

        except requests.exceptions.Timeout:
            raise MDBListAPIError(f"Request timeout after {MDBLIST_REQUEST_TIMEOUT}s")
        except requests.exceptions.ConnectionError:
            raise MDBListAPIError(f"Could not connect to MDBList API")
        except requests.exceptions.RequestException as e:
            raise MDBListAPIError(f"Request failed: {e}")

    def test_connection(self) -> bool:
        """
        Test connection to MDBList API.

        Returns:
            True if connection successful

        Raises:
            MDBListAPIError: If connection fails
        """
        # Get user's lists to verify API key works
        result = self._make_request("GET", "lists/user")
        if result is not None:
            logger.debug("Connected to MDBList API")
            return True
        return False

    def get_lists(self) -> List[Dict]:
        """
        Get all user's lists.

        Returns:
            List of list dictionaries with 'id', 'name', 'slug'
        """
        if self._lists_cache is None:
            self._lists_cache = self._make_request("GET", "lists/user") or []
        return self._lists_cache

    def get_list_by_name(self, name: str) -> Optional[Dict]:
        """
        Find a list by name.

        Args:
            name: List name to find

        Returns:
            List dict if found, None otherwise
        """
        lists = self.get_lists()
        for lst in lists:
            if lst.get('name', '').lower() == name.lower():
                return lst
        return None

    def create_list(self, name: str) -> Dict:
        """
        Create a new static list.

        Args:
            name: List name

        Returns:
            Created list data with 'id', 'name', 'slug', 'url'

        Raises:
            MDBListAPIError: If creation fails
        """
        result = self._make_request("POST", "lists/user/add", data={"name": name})
        # Invalidate cache
        self._lists_cache = None
        return result

    def get_or_create_list(self, name: str) -> Dict:
        """
        Get existing list or create new one.

        Args:
            name: List name

        Returns:
            List data with 'id', 'name', 'slug'
        """
        existing = self.get_list_by_name(name)
        if existing:
            return existing
        return self.create_list(name)

    def add_items(self, list_id: int, movies: Optional[List[int]] = None,
                  shows: Optional[List[int]] = None) -> Dict:
        """
        Add items to a list.

        Args:
            list_id: MDBList list ID
            movies: List of TMDB movie IDs
            shows: List of TMDB show IDs

        Returns:
            Result with 'added', 'existing', 'not_found' counts

        Raises:
            MDBListAPIError: If add fails
        """
        data = {}
        if movies:
            data['movies'] = [{"tmdb": tmdb_id} for tmdb_id in movies]
        if shows:
            data['shows'] = [{"tmdb": tmdb_id} for tmdb_id in shows]

        if not data:
            return {"added": 0, "existing": 0, "not_found": 0}

        return self._make_request("POST", f"lists/{list_id}/items/add", data=data)

    def clear_list(self, list_id: int) -> bool:
        """
        Remove all items from a list.

        Args:
            list_id: MDBList list ID

        Returns:
            True if successful
        """
        # Get current items first
        items = self._make_request("GET", f"lists/{list_id}/items")
        if not items:
            return True

        # Build removal data
        movies = []
        shows = []
        for item in items:
            if item.get('mediatype') == 'movie':
                if item.get('imdb_id'):
                    movies.append({"imdb": item['imdb_id']})
            else:
                if item.get('imdb_id'):
                    shows.append({"imdb": item['imdb_id']})

        if movies or shows:
            data = {}
            if movies:
                data['movies'] = movies
            if shows:
                data['shows'] = shows
            self._make_request("POST", f"lists/{list_id}/items/remove", data=data)

        return True


def create_mdblist_client(config: Dict) -> Optional[MDBListClient]:
    """
    Create an MDBList client from config.

    Args:
        config: Full config dict containing 'mdblist' section

    Returns:
        MDBListClient if configured and enabled, None otherwise
    """
    mdblist_config = config.get('mdblist', {})

    if not mdblist_config.get('enabled', False):
        return None

    api_key = mdblist_config.get('api_key')

    if not api_key or api_key == 'YOUR_MDBLIST_API_KEY':
        return None

    return MDBListClient(api_key)
