"""
Sonarr API client for Curatarr.
Handles adding TV show recommendations to Sonarr.
"""

import logging
import time
import requests
from typing import Dict, Optional, Any, List

logger = logging.getLogger('curatarr')

# Rate limiting: 0.1s delay between requests
SONARR_RATE_LIMIT_DELAY = 0.1

# HTTP request timeout in seconds
SONARR_REQUEST_TIMEOUT = 30


class SonarrAPIError(Exception):
    """Raised when Sonarr API request fails."""
    pass


class SonarrClient:
    """
    Sonarr API client for adding TV shows.

    Uses API key authentication (no OAuth required).
    """

    def __init__(self, url: str, api_key: str):
        """
        Initialize Sonarr client.

        Args:
            url: Sonarr base URL (e.g., http://localhost:8989)
            api_key: Sonarr API key
        """
        self.url = url.rstrip('/')
        self.api_key = api_key
        self._last_request_time = 0
        self._existing_series: Optional[Dict[str, int]] = None

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        return {
            "Content-Type": "application/json",
            "X-Api-Key": self.api_key
        }

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < SONARR_RATE_LIMIT_DELAY:
            time.sleep(SONARR_RATE_LIMIT_DELAY - elapsed)
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
            SonarrAPIError: If request fails
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
                timeout=SONARR_REQUEST_TIMEOUT
            )

            if response.status_code == 401:
                raise SonarrAPIError("Invalid API key")
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
                except Exception:
                    pass
                raise SonarrAPIError(f"API error {response.status_code}: {error_msg}")

            if response.status_code == 204:
                return None

            return response.json()

        except requests.exceptions.Timeout:
            raise SonarrAPIError(f"Request timeout after {SONARR_REQUEST_TIMEOUT}s")
        except requests.exceptions.ConnectionError:
            raise SonarrAPIError(f"Could not connect to Sonarr at {self.url}")
        except requests.exceptions.RequestException as e:
            raise SonarrAPIError(f"Request failed: {e}")

    def test_connection(self) -> bool:
        """
        Test connection to Sonarr.

        Returns:
            True if connection successful

        Raises:
            SonarrAPIError: If connection fails
        """
        result = self._make_request("GET", "system/status")
        if result:
            logger.debug(f"Connected to Sonarr v{result.get('version', 'unknown')}")
            return True
        return False

    def get_series(self) -> List[Dict]:
        """
        Get all series in Sonarr.

        Returns:
            List of series dictionaries
        """
        return self._make_request("GET", "series") or []

    def get_existing_series_imdb_ids(self) -> Dict[str, int]:
        """
        Get a mapping of IMDB IDs to Sonarr series IDs.

        Returns:
            Dict mapping imdb_id -> sonarr_series_id
        """
        if self._existing_series is None:
            series = self.get_series()
            self._existing_series = {}
            for s in series:
                imdb_id = s.get('imdbId')
                if imdb_id:
                    self._existing_series[imdb_id] = s['id']
        return self._existing_series

    def series_exists(self, imdb_id: str) -> bool:
        """
        Check if a series already exists in Sonarr.

        Args:
            imdb_id: IMDB ID (e.g., "tt1234567")

        Returns:
            True if series exists
        """
        existing = self.get_existing_series_imdb_ids()
        return imdb_id in existing

    def lookup_series(self, imdb_id: str) -> Optional[Dict]:
        """
        Look up a series by IMDB ID.

        Args:
            imdb_id: IMDB ID (e.g., "tt1234567")

        Returns:
            Series data dict if found, None otherwise
        """
        results = self._make_request("GET", "series/lookup", params={"term": f"imdb:{imdb_id}"})
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

    def add_series(
        self,
        tvdb_id: int,
        title: str,
        root_folder_path: str,
        quality_profile_id: int,
        monitored: bool = False,
        monitor_option: str = "none",
        season_folder: bool = True,
        series_type: str = "standard",
        tag_ids: Optional[List[int]] = None,
        search_for_missing: bool = False
    ) -> Dict:
        """
        Add a series to Sonarr.

        Args:
            tvdb_id: TVDB ID
            title: Series title
            root_folder_path: Root folder path
            quality_profile_id: Quality profile ID
            monitored: Whether to monitor the series
            monitor_option: Monitor option (none, all, future, missing, etc.)
            season_folder: Use season folders
            series_type: Series type (standard, daily, anime)
            tag_ids: List of tag IDs to apply
            search_for_missing: Search for missing episodes immediately

        Returns:
            Created series data

        Raises:
            SonarrAPIError: If add fails
        """
        # Build add options based on monitor_option
        add_options = {
            "searchForMissingEpisodes": search_for_missing,
            "searchForCutoffUnmetEpisodes": False,
            "monitor": monitor_option
        }

        data = {
            "tvdbId": tvdb_id,
            "title": title,
            "rootFolderPath": root_folder_path,
            "qualityProfileId": quality_profile_id,
            "monitored": monitored,
            "seasonFolder": season_folder,
            "seriesType": series_type,
            "addOptions": add_options,
            "tags": tag_ids or []
        }

        return self._make_request("POST", "series", data=data)


def create_sonarr_client(config: Dict) -> Optional[SonarrClient]:
    """
    Create a Sonarr client from config.

    Args:
        config: Full config dict containing 'sonarr' section

    Returns:
        SonarrClient if configured and enabled, None otherwise
    """
    sonarr_config = config.get('sonarr', {})

    if not sonarr_config.get('enabled', False):
        return None

    url = sonarr_config.get('url')
    api_key = sonarr_config.get('api_key')

    if not url or not api_key or api_key == 'YOUR_SONARR_API_KEY':
        return None

    return SonarrClient(url, api_key)
