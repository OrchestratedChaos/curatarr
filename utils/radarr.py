"""
Radarr API client for Curatarr.
Handles adding movie recommendations to Radarr.
"""

import logging
import time
import requests
from typing import Dict, Optional, Any, List

logger = logging.getLogger('curatarr')

# Rate limiting: 0.1s delay between requests
RADARR_RATE_LIMIT_DELAY = 0.1

# HTTP request timeout in seconds
RADARR_REQUEST_TIMEOUT = 30


class RadarrAPIError(Exception):
    """Raised when Radarr API request fails."""
    pass


class RadarrClient:
    """
    Radarr API client for adding movies.

    Uses API key authentication (no OAuth required).
    """

    def __init__(self, url: str, api_key: str):
        """
        Initialize Radarr client.

        Args:
            url: Radarr base URL (e.g., http://localhost:7878)
            api_key: Radarr API key
        """
        self.url = url.rstrip('/')
        self.api_key = api_key
        self._last_request_time = 0
        self._existing_movies: Optional[Dict[str, int]] = None

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        return {
            "Content-Type": "application/json",
            "X-Api-Key": self.api_key
        }

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < RADARR_RATE_LIMIT_DELAY:
            time.sleep(RADARR_RATE_LIMIT_DELAY - elapsed)
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
            RadarrAPIError: If request fails
        """
        self._rate_limit()

        url = f"{self.url}/api/v3/{endpoint}"

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self._get_headers(),
                json=data,
                params=params,
                timeout=RADARR_REQUEST_TIMEOUT
            )

            if response.status_code == 401:
                raise RadarrAPIError("Invalid API key")
            elif response.status_code == 404:
                return None
            elif response.status_code >= 400:
                error_msg = response.text
                try:
                    error_data = response.json()
                    if isinstance(error_data, list) and error_data:
                        error_msg = error_data[0].get('errorMessage', error_msg)
                    elif isinstance(error_data, dict):
                        error_msg = error_data.get('message', error_msg)
                except Exception as e:
                    logger.debug(f"Failed to parse error response JSON: {e}")
                raise RadarrAPIError(f"API error {response.status_code}: {error_msg}")

            if response.status_code == 204:
                return None

            return response.json()

        except requests.exceptions.Timeout:
            raise RadarrAPIError(f"Request timeout after {RADARR_REQUEST_TIMEOUT}s")
        except requests.exceptions.ConnectionError:
            raise RadarrAPIError(f"Could not connect to Radarr at {self.url}")
        except requests.exceptions.RequestException as e:
            raise RadarrAPIError(f"Request failed: {e}")

    def test_connection(self) -> bool:
        """
        Test connection to Radarr.

        Returns:
            True if connection successful

        Raises:
            RadarrAPIError: If connection fails
        """
        result = self._make_request("GET", "system/status")
        if result:
            logger.debug(f"Connected to Radarr v{result.get('version', 'unknown')}")
            return True
        return False

    def get_movies(self) -> List[Dict]:
        """
        Get all movies in Radarr.

        Returns:
            List of movie dictionaries
        """
        return self._make_request("GET", "movie") or []

    def get_existing_movies_tmdb_ids(self) -> Dict[int, int]:
        """
        Get a mapping of TMDB IDs to Radarr movie IDs.

        Returns:
            Dict mapping tmdb_id -> radarr_movie_id
        """
        if self._existing_movies is None:
            movies = self.get_movies()
            self._existing_movies = {}
            for m in movies:
                tmdb_id = m.get('tmdbId')
                if tmdb_id:
                    self._existing_movies[tmdb_id] = m['id']
        return self._existing_movies

    def movie_exists(self, tmdb_id: int) -> bool:
        """
        Check if a movie already exists in Radarr.

        Args:
            tmdb_id: TMDB ID

        Returns:
            True if movie exists
        """
        existing = self.get_existing_movies_tmdb_ids()
        return tmdb_id in existing

    def lookup_movie(self, tmdb_id: int) -> Optional[Dict]:
        """
        Look up a movie by TMDB ID.

        Args:
            tmdb_id: TMDB ID

        Returns:
            Movie data dict if found, None otherwise
        """
        results = self._make_request("GET", "movie/lookup", params={"term": f"tmdb:{tmdb_id}"})
        if results and len(results) > 0:
            return results[0]
        return None

    def get_quality_profiles(self) -> List[Dict]:
        """
        Get available quality profiles.

        Returns:
            List of quality profile dictionaries with 'id' and 'name'
        """
        return self._make_request("GET", "qualityprofile") or []

    def get_quality_profile_id(self, profile_name: str) -> Optional[int]:
        """
        Get quality profile ID by name.

        Args:
            profile_name: Quality profile name

        Returns:
            Profile ID if found, None otherwise
        """
        profiles = self.get_quality_profiles()
        for profile in profiles:
            if profile['name'].lower() == profile_name.lower():
                return profile['id']
        return None

    def get_root_folders(self) -> List[Dict]:
        """
        Get available root folders.

        Returns:
            List of root folder dictionaries with 'id' and 'path'
        """
        return self._make_request("GET", "rootfolder") or []

    def get_root_folder_path(self, folder_path: str) -> Optional[str]:
        """
        Validate and get the exact root folder path.

        Args:
            folder_path: Configured root folder path

        Returns:
            Exact path if found, None otherwise
        """
        folders = self.get_root_folders()
        for folder in folders:
            if folder['path'] == folder_path:
                return folder['path']
        return None

    def get_tags(self) -> List[Dict]:
        """
        Get all tags.

        Returns:
            List of tag dictionaries with 'id' and 'label'
        """
        return self._make_request("GET", "tag") or []

    def get_or_create_tag(self, tag_label: str) -> int:
        """
        Get existing tag ID or create new tag.

        Args:
            tag_label: Tag label (e.g., "Curatarr")

        Returns:
            Tag ID
        """
        tags = self.get_tags()
        for tag in tags:
            if tag['label'].lower() == tag_label.lower():
                return tag['id']

        # Create new tag
        result = self._make_request("POST", "tag", data={"label": tag_label})
        return result['id']

    def add_movie(
        self,
        tmdb_id: int,
        title: str,
        root_folder_path: str,
        quality_profile_id: int,
        monitored: bool = False,
        minimum_availability: str = "released",
        tag_ids: Optional[List[int]] = None,
        search_for_movie: bool = False
    ) -> Dict:
        """
        Add a movie to Radarr.

        Args:
            tmdb_id: TMDB ID
            title: Movie title
            root_folder_path: Root folder path
            quality_profile_id: Quality profile ID
            monitored: Whether to monitor the movie
            minimum_availability: When movie is considered available
                (announced, inCinemas, released, preDB)
            tag_ids: List of tag IDs to apply
            search_for_movie: Search for movie immediately

        Returns:
            Created movie data

        Raises:
            RadarrAPIError: If add fails
        """
        # Build add options
        add_options = {
            "searchForMovie": search_for_movie
        }

        data = {
            "tmdbId": tmdb_id,
            "title": title,
            "rootFolderPath": root_folder_path,
            "qualityProfileId": quality_profile_id,
            "monitored": monitored,
            "minimumAvailability": minimum_availability,
            "addOptions": add_options,
            "tags": tag_ids or []
        }

        return self._make_request("POST", "movie", data=data)


def create_radarr_client(config: Dict) -> Optional[RadarrClient]:
    """
    Create a Radarr client from config.

    Args:
        config: Full config dict containing 'radarr' section

    Returns:
        RadarrClient if configured and enabled, None otherwise
    """
    radarr_config = config.get('radarr', {})

    if not radarr_config.get('enabled', False):
        return None

    url = radarr_config.get('url')
    api_key = radarr_config.get('api_key')

    if not url or not api_key or api_key == 'YOUR_RADARR_API_KEY':
        return None

    return RadarrClient(url, api_key)
