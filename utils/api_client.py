"""
Base API client for Curatarr external service integrations.
Provides common functionality for rate limiting, request handling, and error parsing.
"""

import logging
import time
import requests
from typing import Any, Dict, Optional

logger = logging.getLogger('curatarr')


class BaseAPIClient:
    """
    Base class for API clients with common rate limiting and request handling.

    Subclasses should:
    - Set `api_name` class attribute for error messages
    - Set `exception_class` class attribute for raising appropriate exceptions
    - Override `_get_headers()` to return auth headers
    - Override `_build_url()` if URL construction differs
    """

    api_name: str = "API"
    exception_class: type = Exception
    rate_limit_delay: float = 0.1
    request_timeout: int = 30

    def __init__(self):
        """Initialize base client state."""
        self._last_request_time = 0

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests. Override in subclass."""
        return {"Content-Type": "application/json"}

    def _build_url(self, base_url: str, endpoint: str) -> str:
        """Build full URL from base and endpoint. Override if needed."""
        return f"{base_url}/{endpoint}"

    def _parse_error_response(self, response: requests.Response) -> str:
        """
        Parse error message from response body.

        Handles common patterns:
        - List with 'errorMessage' key (Radarr/Sonarr style)
        - Dict with 'message' or 'error' key

        Args:
            response: Failed HTTP response

        Returns:
            Extracted error message or raw response text
        """
        error_msg = response.text
        try:
            error_data = response.json()
            if isinstance(error_data, list) and error_data:
                error_msg = error_data[0].get('errorMessage', error_msg)
            elif isinstance(error_data, dict):
                error_msg = error_data.get('message', error_data.get('error', error_msg))
        except Exception as e:
            logger.debug(f"Failed to parse error response JSON: {e}")
        return error_msg

    def _handle_response(self, response: requests.Response) -> Any:
        """
        Handle HTTP response, raising exceptions for errors.

        Args:
            response: HTTP response object

        Returns:
            Parsed JSON response or None for 204/404

        Raises:
            exception_class: For HTTP errors
        """
        if response.status_code == 401:
            raise self.exception_class("Invalid API key")
        elif response.status_code == 404:
            return None
        elif response.status_code >= 400:
            error_msg = self._parse_error_response(response)
            raise self.exception_class(f"API error {response.status_code}: {error_msg}")

        if response.status_code == 204:
            return None

        return response.json()

    def _make_request_to_url(self, method: str, url: str,
                              data: Optional[Dict] = None,
                              params: Optional[Dict] = None,
                              headers: Optional[Dict] = None) -> Any:
        """
        Make an HTTP request with rate limiting and error handling.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            url: Full URL to request
            data: Request body data (will be JSON encoded)
            params: Query parameters
            headers: Optional headers (uses _get_headers() if not provided)

        Returns:
            Response JSON data or None

        Raises:
            exception_class: If request fails
        """
        self._rate_limit()

        if headers is None:
            headers = self._get_headers()

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=data,
                params=params,
                timeout=self.request_timeout
            )
            return self._handle_response(response)

        except requests.exceptions.Timeout:
            raise self.exception_class(f"Request timeout after {self.request_timeout}s")
        except requests.exceptions.ConnectionError:
            raise self.exception_class(f"Could not connect to {self.api_name}")
        except requests.exceptions.RequestException as e:
            raise self.exception_class(f"Request failed: {e}")
