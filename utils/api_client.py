"""
Base API client for Curatarr external service integrations.
Provides common functionality for rate limiting, request handling, and error parsing.
"""

import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlsplit

import requests

from .helpers import read_response_capped
from .metrics import record_api_call

logger = logging.getLogger("curatarr")

# Redirect statuses requests would otherwise follow automatically.
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# Bounds _follow_same_host_redirect's loop - a same-host redirect chain
# should never legitimately be this long; this just guards against a
# misconfigured/malicious server redirecting forever.
_MAX_REDIRECT_HOPS = 5


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

    # Bounds a 429 (rate-limited) retry loop in _send_with_retries.
    # 0 (default) preserves every existing subclass's behavior exactly
    # (Radarr/Sonarr/Tautulli/MDBList never retried a 429, and don't
    # call _send_with_retries at all - they use _make_request_to_url,
    # unchanged here). Clients that DO need it (TraktClient/SimklClient
    # - both talk to a real rate-limited cloud API, unlike the others'
    # local network services) set this explicitly.
    max_429_retries: int = 0

    # Ceiling on how long a single 429 retry will ever sleep for,
    # regardless of what the server's Retry-After header asks for -
    # that header is server-controlled input, and a compromised/
    # misbehaving endpoint could otherwise stall this process for an
    # arbitrary/huge amount of time.
    max_retry_after_seconds: int = 60

    def __init__(self):
        """Initialize base client state."""
        self._last_request_time = 0

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    def _send_with_retries(
        self,
        method: str,
        url: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
    ) -> requests.Response:
        """Issue a rate-limited request with a bounded 429 (rate
        limited) retry loop, honoring the server's Retry-After header
        (capped at max_retry_after_seconds).

        Returns the raw response whatever its status code - unlike
        _make_request_to_url this never raises on a 4xx/5xx and never
        runs the redirect-following/response-size-cap pipeline, so a
        caller that needs to inspect the response before deciding how
        to handle an error (e.g. TraktClient retrying with a refreshed
        OAuth token on 401, which a raised exception here would
        prevent) can do so itself. Callers that just want the shared
        error-handling pipeline should use _make_request_to_url
        instead - this only owns the rate-limit/429-retry loop.
        """
        response = None
        for attempt in range(self.max_429_retries + 1):
            self._rate_limit()
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=data,
                params=params,
                timeout=self.request_timeout,
                allow_redirects=False,
            )

            if response.status_code != 429 or attempt == self.max_429_retries:
                break

            retry_after = min(
                int(response.headers.get("Retry-After", 1)),
                self.max_retry_after_seconds,
            )
            logger.warning(
                f"{self.api_name} rate limited, waiting {retry_after}s (retry {attempt + 1}/{self.max_429_retries})"
            )
            time.sleep(retry_after)
        return response

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
                error_msg = error_data[0].get("errorMessage", error_msg)
            elif isinstance(error_data, dict):
                error_msg = error_data.get("message", error_data.get("error", error_msg))
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
        if response.status_code in _REDIRECT_STATUSES:
            # Still a redirect after _follow_same_host_redirect - either
            # it pointed at a different host (refused, already logged
            # there) or exceeded _MAX_REDIRECT_HOPS. Either way, a raw
            # redirect response's body isn't the API response the caller
            # expects, so surface a clear error instead of trying (and
            # likely failing) to parse it as JSON.
            raise self.exception_class(
                f"{self.api_name} returned an unfollowed redirect "
                f"({response.status_code}) - refusing to send credentials "
                f"to a different host. See logs for the redirect target."
            )
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

    def _follow_same_host_redirect(
        self,
        method: str,
        response: requests.Response,
        headers: Dict[str, str],
        data: Optional[Dict],
        params: Optional[Dict],
    ) -> requests.Response:
        """Manually re-issue a redirected request, but ONLY when the
        redirect target is the same host the request was already
        trusted to go to.

        requests follows redirects by default and only auto-strips the
        Authorization header on a cross-host hop - not a custom auth
        header like the X-Api-Key every subclass here sends via
        _get_headers(). A malicious or compromised configured
        Sonarr/Radarr/etc. host could redirect this client to an
        attacker-controlled host and harvest the API key from those
        headers. _make_request_to_url disables requests' own redirect
        following entirely (allow_redirects=False) so that can never
        happen silently; this re-implements just the safe subset - a
        same-host redirect (e.g. a reverse proxy normalizing a trailing
        slash) still works exactly as before.
        """
        hops = 0
        while response.status_code in _REDIRECT_STATUSES and hops < _MAX_REDIRECT_HOPS:
            location = response.headers.get("Location")
            if not location:
                break
            original_host = urlsplit(response.url).netloc
            next_url = urljoin(response.url, location)
            if urlsplit(next_url).netloc != original_host:
                logger.warning(
                    f"{self.api_name} redirected to a different host "
                    f"({urlsplit(next_url).netloc}) - refusing to follow "
                    f"with credentials."
                )
                break
            response = requests.request(
                method=method,
                url=next_url,
                headers=headers,
                json=data,
                params=params,
                timeout=self.request_timeout,
                allow_redirects=False,
                stream=True,
            )
            hops += 1
        else:
            if response.status_code in _REDIRECT_STATUSES and response.headers.get("Location"):
                logger.warning(
                    f"{self.api_name} redirected more than {_MAX_REDIRECT_HOPS} times - refusing to follow further."
                )
        return response

    def _make_request_to_url(
        self,
        method: str,
        url: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
    ) -> Any:
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

        # curatarr_api_requests_total/curatarr_api_request_duration_seconds
        # (see utils/metrics.py), keyed by api_name (radarr/sonarr/
        # mdblist/tautulli - every BaseAPIClient subclass) - timed from
        # here (after rate-limiting, which is client-side pacing, not
        # part of the request itself) through the return/raise below.
        # outcome only ever flips to 'success' immediately before
        # returning, so ANY exception path here (a raised
        # self.exception_class from _handle_response's own status-code
        # handling included, not just the requests-level exceptions
        # this try/except itself translates) is recorded as 'error'.
        request_start = time.time()
        outcome = "error"
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=data,
                params=params,
                timeout=self.request_timeout,
                # Never auto-follow redirects: requests only strips the
                # Authorization header on a cross-host hop, not custom
                # auth headers like X-Api-Key - see
                # _follow_same_host_redirect for the safe, same-host-only
                # replacement.
                allow_redirects=False,
                # Deferred body read - see the capped read below. Every
                # subclass here (Radarr/Sonarr/Tautulli/MDBList) talks to
                # a host from config (all but MDBList are genuinely
                # user-configured, and could point anywhere), so the
                # response body is never assumed to be a bounded size
                # just because a legitimate one always would be.
                stream=True,
            )
            response = self._follow_same_host_redirect(method, response, headers, data, params)
            try:
                read_response_capped(response)
            except ValueError as e:
                raise self.exception_class(f"{self.api_name} response rejected: {e}") from e
            result = self._handle_response(response)
            outcome = "success"
            return result

        except requests.exceptions.Timeout as e:
            raise self.exception_class(f"Request timeout after {self.request_timeout}s") from e
        except requests.exceptions.ConnectionError as e:
            raise self.exception_class(f"Could not connect to {self.api_name}") from e
        except requests.exceptions.RequestException as e:
            raise self.exception_class(f"Request failed: {e}") from e
        finally:
            record_api_call(self.api_name.lower(), outcome, time.time() - request_start)
