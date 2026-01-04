"""
Trakt API client for Curatarr.
Handles OAuth device authentication, token management, and API requests.
"""

import time
import logging
import requests
from typing import Dict, Optional, Any, List

logger = logging.getLogger('curatarr')

# Trakt API endpoints
TRAKT_API_URL = "https://api.trakt.tv"
TRAKT_AUTH_URL = "https://trakt.tv"

# Rate limiting: 0.2s delay (5 req/sec, well under 1000/5min limit)
TRAKT_RATE_LIMIT_DELAY = 0.2


class TraktAuthError(Exception):
    """Raised when Trakt authentication fails."""
    pass


class TraktAPIError(Exception):
    """Raised when Trakt API request fails."""
    pass


class TraktClient:
    """
    Trakt API client with OAuth device authentication.

    Device auth flow works in Docker/SSH environments without browser redirects.
    """

    def __init__(self, client_id: str, client_secret: str,
                 access_token: Optional[str] = None,
                 refresh_token: Optional[str] = None,
                 token_callback: Optional[callable] = None):
        """
        Initialize Trakt client.

        Args:
            client_id: Trakt API application client ID
            client_secret: Trakt API application client secret
            access_token: Existing access token (optional)
            refresh_token: Existing refresh token (optional)
            token_callback: Function to call when tokens are updated (for saving)
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
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
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id
        }
        if authenticated and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < TRAKT_RATE_LIMIT_DELAY:
            time.sleep(TRAKT_RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _make_request(self, method: str, endpoint: str,
                      data: Optional[Dict] = None,
                      authenticated: bool = True,
                      retry_auth: bool = True) -> Any:
        """
        Make an API request with rate limiting and error handling.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            endpoint: API endpoint (without base URL)
            data: Request body data
            authenticated: Whether to include auth header
            retry_auth: Whether to retry with refreshed token on 401

        Returns:
            Response JSON or None for 204 responses
        """
        self._rate_limit()

        url = f"{TRAKT_API_URL}{endpoint}"
        headers = self._get_headers(authenticated)

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=data,
                timeout=30
            )

            # Handle rate limiting
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 1))
                logger.warning(f"Trakt rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
                return self._make_request(method, endpoint, data, authenticated, retry_auth)

            # Handle auth errors
            if response.status_code == 401 and retry_auth and self.refresh_token:
                logger.info("Trakt token expired, refreshing...")
                if self._refresh_access_token():
                    return self._make_request(method, endpoint, data, authenticated, retry_auth=False)
                raise TraktAuthError("Failed to refresh Trakt token")

            # Handle other errors
            if response.status_code >= 400:
                raise TraktAPIError(f"Trakt API error {response.status_code}: {response.text}")

            # Return JSON or None for no-content responses
            if response.status_code == 204:
                return None
            return response.json()

        except requests.RequestException as e:
            raise TraktAPIError(f"Trakt request failed: {e}")

    # =========================================================================
    # Device Authentication Flow
    # =========================================================================

    def get_device_code(self) -> Dict[str, Any]:
        """
        Start device authentication flow.

        Returns:
            Dict with device_code, user_code, verification_url, expires_in, interval
        """
        response = requests.post(
            f"{TRAKT_API_URL}/oauth/device/code",
            json={"client_id": self.client_id},
            headers={"Content-Type": "application/json"},
            timeout=30
        )

        if response.status_code != 200:
            raise TraktAuthError(f"Failed to get device code: {response.text}")

        return response.json()

    def poll_for_token(self, device_code: str, interval: int = 5,
                       expires_in: int = 600) -> bool:
        """
        Poll for user authorization completion.

        Args:
            device_code: Device code from get_device_code()
            interval: Polling interval in seconds
            expires_in: Expiration time in seconds

        Returns:
            True if authorized, False if expired/denied
        """
        start_time = time.time()

        while time.time() - start_time < expires_in:
            response = requests.post(
                f"{TRAKT_API_URL}/oauth/device/token",
                json={
                    "code": device_code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret
                },
                headers={"Content-Type": "application/json"},
                timeout=30
            )

            if response.status_code == 200:
                # Success - got tokens
                data = response.json()
                self.access_token = data["access_token"]
                self.refresh_token = data["refresh_token"]

                # Notify callback to save tokens
                if self.token_callback:
                    self.token_callback(self.access_token, self.refresh_token)

                return True

            elif response.status_code == 400:
                # Still waiting for user
                time.sleep(interval)
                continue

            elif response.status_code == 404:
                # Invalid device code
                raise TraktAuthError("Invalid device code")

            elif response.status_code == 409:
                # Code already used
                raise TraktAuthError("Device code already used")

            elif response.status_code == 410:
                # Code expired
                return False

            elif response.status_code == 418:
                # User denied
                return False

            else:
                raise TraktAuthError(f"Unexpected response: {response.status_code}")

        return False  # Timed out

    def _refresh_access_token(self) -> bool:
        """
        Refresh the access token using refresh token.

        Returns:
            True if successful, False otherwise
        """
        if not self.refresh_token:
            return False

        try:
            response = requests.post(
                f"{TRAKT_API_URL}/oauth/token",
                json={
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token"
                },
                headers={"Content-Type": "application/json"},
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                self.access_token = data["access_token"]
                self.refresh_token = data["refresh_token"]

                # Notify callback to save tokens
                if self.token_callback:
                    self.token_callback(self.access_token, self.refresh_token)

                logger.info("Trakt token refreshed successfully")
                return True

            return False

        except requests.RequestException:
            return False

    def revoke_token(self) -> bool:
        """
        Revoke the current access token.

        Returns:
            True if successful
        """
        if not self.access_token:
            return True

        try:
            response = requests.post(
                f"{TRAKT_API_URL}/oauth/revoke",
                json={
                    "token": self.access_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret
                },
                headers={"Content-Type": "application/json"},
                timeout=30
            )

            if response.status_code == 200:
                self.access_token = None
                self.refresh_token = None
                return True

            return False

        except requests.RequestException:
            return False

    # =========================================================================
    # User Info
    # =========================================================================

    def get_user_settings(self) -> Dict[str, Any]:
        """Get authenticated user's settings."""
        return self._make_request("GET", "/users/settings")

    def get_username(self) -> Optional[str]:
        """Get authenticated user's username."""
        try:
            settings = self.get_user_settings()
            return settings.get("user", {}).get("username")
        except (TraktAPIError, TraktAuthError):
            return None


def create_trakt_client(config: Dict) -> Optional[TraktClient]:
    """
    Create a TraktClient from config, if enabled.

    Args:
        config: Full application config dict

    Returns:
        TraktClient instance or None if Trakt is disabled
    """
    trakt_config = config.get('trakt', {})

    if not trakt_config.get('enabled', False):
        return None

    client_id = trakt_config.get('client_id')
    client_secret = trakt_config.get('client_secret')

    if not client_id or not client_secret:
        logger.warning("Trakt enabled but client_id/client_secret not configured")
        return None

    access_token = trakt_config.get('access_token')
    refresh_token = trakt_config.get('refresh_token')

    return TraktClient(
        client_id=client_id,
        client_secret=client_secret,
        access_token=access_token,
        refresh_token=refresh_token
    )
