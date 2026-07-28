"""
Trakt API client for Curatarr.
Handles OAuth device authentication, token management, and API requests.
"""

import json
import logging
import os
import re
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional

import requests
import yaml

from .api_client import BaseAPIClient
from .display import log_error, log_warning
from .helpers import get_project_root, harden_file_permissions
from .metrics import record_api_call

logger = logging.getLogger("curatarr")

# Trakt API endpoints
TRAKT_API_URL = "https://api.trakt.tv"
TRAKT_AUTH_URL = "https://trakt.tv"

# Out-of-band redirect_uri for non-web (device-code/PIN) OAuth flows -
# the OAuth 2.0 standard placeholder value, and what PyTrakt (the
# reference third-party Trakt client) sends on both the authorization
# and refresh grant - see its trakt/core.py REDIRECT_URI constant.
# Trakt's own documented /oauth/token body includes redirect_uri on a
# refresh_token grant; curatarr's device-code flow never establishes
# one of its own (device auth doesn't use it), so this is the value to
# send instead of omitting the field entirely.
TRAKT_OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"

# How long before Trakt's own reported token expiry to proactively
# refresh (see TraktClient._access_token_expired) rather than waiting
# for a reactive 401 - covers clock skew and a request already in
# flight when the token crosses its exact expiry instant.
TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS = 300

# Rate limiting: 0.2s delay (5 req/sec, well under 1000/5min limit)
TRAKT_RATE_LIMIT_DELAY = 0.2

# HTTP request timeout in seconds
TRAKT_REQUEST_TIMEOUT = 30

# Bounds the 429 (rate limited) retry loop in _make_request - matching
# the pattern already used correctly in utils/tmdb.py's
# fetch_tmdb_with_retry. Without a cap, a 429 response recursed/looped
# forever, sleeping for however long the server's own Retry-After header
# said to (see TRAKT_MAX_RETRY_AFTER_SECONDS below for why that alone
# isn't trustworthy either).
TRAKT_MAX_429_RETRIES = 3

# Ceiling on how long a single 429 retry will ever sleep for, regardless
# of what the server's Retry-After header asks for - that header is
# server-controlled input, and a compromised/misbehaving Trakt endpoint
# (or a malicious response) could otherwise stall this process for an
# arbitrary/huge amount of time.
TRAKT_MAX_RETRY_AFTER_SECONDS = 60


class TraktAuthError(Exception):
    """Raised when Trakt authentication fails."""

    pass


class TraktAPIError(Exception):
    """Raised when Trakt API request fails."""

    pass


def derive_trakt_list_slug(name: str) -> str:
    """
    Best-effort LOCAL approximation of the slug Trakt itself assigns to
    a list name - a fallback for the rare case a real slug genuinely
    isn't available yet, never a substitute for it. The real slug
    Trakt actually assigned is always authoritative and must be
    preferred everywhere it's available (get_or_create_list/sync_list
    below hand it back from the API response's own `ids.slug` for
    exactly this reason) - this function exists only for:
      - get_or_create_list's speculative direct GET-by-slug lookup
        below, tried before falling back to a full by-name search
        (Trakt's API has no "look up by name" endpoint).
      - recommenders/external_sync.py's printed list-URL fallback, for
        the (should-never-happen) case sync_list()'s own return value
        doesn't include a slug.

    Any local reimplementation of Trakt's actual slugification rules
    will drift from theirs over time (apostrophes, ampersands,
    unicode, and whatever else Trakt's own rules handle aren't
    replicated here) - this only guarantees the one property that
    actually matters for both call sites above: whitespace/underscore
    runs collapse to a single hyphen, and the resulting hyphen runs
    ALSO collapse to one, so a name like "Curatarr - Jason - Movies"
    (word " - " word, not just word-space-word) produces
    "curatarr-jason-movies" - not "curatarr---jason---movies", which a
    naive `name.lower().replace(" ", "-")` produces instead (each of
    the three spaces around the two literal hyphens replaced
    independently) and which 404s against Trakt's real slug. Confirmed
    against a real Trakt list literally named "Curatarr - Jason -
    Movies", whose real `ids.slug` (GET /users/me/lists) came back as
    "curatarr-jason-movies".
    """
    slug = re.sub(r"[\s_]+", "-", (name or "").strip().lower())
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


class TraktClient(BaseAPIClient):
    """
    Trakt API client with OAuth device authentication.

    Device auth flow works in Docker/SSH environments without browser redirects.
    """

    api_name = "Trakt"
    exception_class = TraktAPIError
    rate_limit_delay = TRAKT_RATE_LIMIT_DELAY
    request_timeout = TRAKT_REQUEST_TIMEOUT
    max_429_retries = TRAKT_MAX_429_RETRIES
    max_retry_after_seconds = TRAKT_MAX_RETRY_AFTER_SECONDS

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        created_at: Optional[int] = None,
        expires_in: Optional[int] = None,
        token_callback: Optional[Callable[[str, str, Optional[int], Optional[int]], None]] = None,
    ):
        """
        Initialize Trakt client.

        Args:
            client_id: Trakt API application client ID
            client_secret: Trakt API application client secret
            access_token: Existing access token (optional)
            refresh_token: Existing refresh token (optional)
            created_at: Unix timestamp the current access_token was issued
                at (Trakt's token-grant response field of the same name -
                previously discarded; see _access_token_expired below for
                why this is tracked now)
            expires_in: Seconds the current access_token is valid for from
                created_at (also a token-grant response field, previously
                discarded)
            token_callback: Called with (access_token, refresh_token,
                created_at, expires_in) whenever a token grant/refresh
                succeeds, so the caller can persist the rotated tokens
                (see create_trakt_client's token_callback - without this,
                a refreshed token lives only in this process's memory,
                and since Trakt rotates refresh tokens single-use, the
                NEXT run replays an already-consumed one and fails)
        """
        super().__init__()
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.created_at = created_at
        self.expires_in = expires_in
        self.token_callback = token_callback

    @property
    def is_authenticated(self) -> bool:
        """Check if client has valid tokens."""
        return self.access_token is not None

    def _access_token_expired(self) -> bool:
        """True if created_at/expires_in are both known and the access
        token is at or within TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS of
        Trakt's reported expiry - used to refresh proactively (see
        _make_request) instead of only reactively on an actual 401.

        False whenever either value is unknown (e.g. a trakt.yml
        written before this was tracked, or a client constructed without
        them) - never invents an expiry, and every existing install
        falls back to exactly today's reactive-only behavior until its
        next real token grant/refresh populates both.
        """
        if not self.created_at or not self.expires_in:
            return False
        return time.time() >= (self.created_at + self.expires_in - TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS)

    def _get_headers(self, authenticated: bool = True) -> Dict[str, str]:
        """Get headers for API requests."""
        headers = {"Content-Type": "application/json", "trakt-api-version": "2", "trakt-api-key": self.client_id}
        if authenticated and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    # _rate_limit() inherited from BaseAPIClient (rate_limit_delay set above)

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        authenticated: bool = True,
        retry_auth: bool = True,
    ) -> Any:
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

        Raises:
            TraktAPIError: If still rate limited (429) after
                TRAKT_MAX_429_RETRIES retries, or on any other API error.
        """
        # Proactive refresh (#trakt-token-refresh-persistence): if we
        # know this token is at/past its reported expiry, refresh BEFORE
        # sending the request rather than waiting for Trakt to reject it
        # with a 401 first. Best-effort only - if it fails, fall through
        # and let the existing reactive 401-retry below handle it exactly
        # as before (this is purely an optimization to avoid a guaranteed
        # wasted round-trip, never a behavior curatarr depends on for
        # correctness).
        if authenticated and retry_auth and self.access_token and self.refresh_token and self._access_token_expired():
            logger.info("Trakt token nearing/at expiry, refreshing proactively...")
            self._refresh_access_token()

        url = f"{TRAKT_API_URL}{endpoint}"
        headers = self._get_headers(authenticated)

        # curatarr_api_requests_total/curatarr_api_request_duration_seconds
        # (see utils/metrics.py) - one record per call to THIS method,
        # including a 401-triggered recursive retry (that recursive call
        # is a genuine separate request attempt and records its own
        # entry too - both are real). outcome only flips to 'success'
        # right before a normal return, so every raised path below
        # (TraktAuthError, TraktAPIError, or a re-raised
        # requests.RequestException) is recorded as 'error'.
        request_start = time.time()
        outcome = "error"
        try:
            # Rate-limited request with the shared bounded 429-retry
            # loop (see BaseAPIClient._send_with_retries) - this
            # returns the raw response instead of raising on an error
            # status, so the 401-refresh-and-retry handling below (this
            # client's own, security-sensitive OAuth logic) still gets
            # to inspect it first.
            response = self._send_with_retries(method, url, data=data, headers=headers)

            # Handle auth errors
            if response.status_code == 401 and retry_auth and self.refresh_token:
                logger.info("Trakt token expired, refreshing...")
                if self._refresh_access_token():
                    # This attempt itself still got a 401 (recorded as
                    # 'error' below via the unchanged `outcome`) - the
                    # retried call below is a separate request attempt
                    # and records its own outcome independently.
                    return self._make_request(method, endpoint, data, authenticated, retry_auth=False)
                # #284: logged HERE (the one choke point every Trakt
                # request goes through) rather than relying on every
                # individual caller to notice/log a caught
                # TraktAuthError/TraktAPIError - this is exactly the
                # "a bad token/expired auth shows up only as an
                # unexplained failure" gap: a six-month Trakt outage
                # previously stayed invisible because nothing here ever
                # logged anything at all.
                log_error(
                    "Trakt: authentication failed - token refresh unsuccessful (re-run utils.trakt_auth --reauth)"
                )
                raise TraktAuthError("Failed to refresh Trakt token")

            # Handle other errors
            if response.status_code >= 400:
                log_error(f"Trakt API error {response.status_code}: {response.text}")
                raise TraktAPIError(f"Trakt API error {response.status_code}: {response.text}")

            # Return JSON or None for no-content responses
            outcome = "success"
            if response.status_code == 204:
                return None
            return response.json()

        except requests.RequestException as e:
            log_error(f"Trakt: request failed - {e}")
            raise TraktAPIError(f"Trakt request failed: {e}") from e
        finally:
            record_api_call("trakt", outcome, time.time() - request_start)

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
            timeout=TRAKT_REQUEST_TIMEOUT,
            allow_redirects=False,
        )

        if response.status_code != 200:
            raise TraktAuthError(f"Failed to get device code: {response.text}")

        return response.json()

    def poll_for_token(
        self,
        device_code: str,
        interval: int = 5,
        expires_in: int = 600,
        on_wait: Optional[Callable[[], None]] = None,
    ) -> bool:
        """
        Poll for user authorization completion.

        Args:
            device_code: Device code from get_device_code()
            interval: Polling interval in seconds
            expires_in: Expiration time in seconds
            on_wait: optional zero-arg callback invoked once per
                still-waiting (HTTP 400) iteration, right before the
                interval sleep - lets a caller (see utils/trakt_auth.py)
                print its own periodic "still waiting" progress so a
                user watching this genuinely long (up to expires_in,
                normally 600s) blocking call can tell "working" from
                "hung" apart, without this method itself owning any
                console/display concerns. Never called on the
                success/denied/expired/error branches below, only while
                actually still polling.

        Returns:
            True if authorized, False if expired/denied
        """
        start_time = time.time()

        while time.time() - start_time < expires_in:
            response = requests.post(
                f"{TRAKT_API_URL}/oauth/device/token",
                json={"code": device_code, "client_id": self.client_id, "client_secret": self.client_secret},
                headers={"Content-Type": "application/json"},
                timeout=TRAKT_REQUEST_TIMEOUT,
                allow_redirects=False,
            )

            if response.status_code == 200:
                # Success - got tokens
                data = response.json()
                self.access_token = data["access_token"]
                self.refresh_token = data["refresh_token"]
                # Distinct names from this method's own `expires_in`
                # parameter (the device-code polling window) - this is
                # the ACCESS TOKEN's lifetime, a same-named but unrelated
                # field on the token-grant response.
                self.created_at = data.get("created_at")
                self.expires_in = data.get("expires_in")

                # Notify callback to save tokens
                if self.token_callback:
                    self.token_callback(self.access_token, self.refresh_token, self.created_at, self.expires_in)

                return True

            elif response.status_code == 400:
                # Still waiting for user
                if on_wait is not None:
                    on_wait()
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
                    "grant_type": "refresh_token",
                    # Trakt's documented /oauth/token body includes
                    # redirect_uri on a refresh_token grant (PyTrakt sends
                    # this too - see TRAKT_OOB_REDIRECT_URI's own comment).
                    # Previously omitted entirely; suspected (not yet
                    # confirmed against a real refresh at the time this
                    # was written) to be why every refresh attempt in the
                    # retained log window failed.
                    "redirect_uri": TRAKT_OOB_REDIRECT_URI,
                },
                headers={"Content-Type": "application/json"},
                timeout=TRAKT_REQUEST_TIMEOUT,
                allow_redirects=False,
            )

            if response.status_code == 200:
                data = response.json()
                self.access_token = data["access_token"]
                self.refresh_token = data["refresh_token"]
                self.created_at = data.get("created_at")
                self.expires_in = data.get("expires_in")

                # Notify callback to save tokens
                if self.token_callback:
                    self.token_callback(self.access_token, self.refresh_token, self.created_at, self.expires_in)

                logger.info("Trakt token refreshed successfully")
                return True

            # Log status + body (redacted by log_error - see
            # utils/display.py) so a refresh failure is actually
            # diagnosable instead of silently collapsing to a bare
            # False that every caller already treats identically to
            # "no refresh token configured" - see _make_request's
            # "Failed to refresh Trakt token" TraktAuthError, which
            # previously carried no detail about WHY.
            log_error(f"Trakt token refresh failed: HTTP {response.status_code}: {response.text}")
            return False

        except requests.RequestException as e:
            log_error(f"Trakt token refresh request failed: {e}")
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
                json={"token": self.access_token, "client_id": self.client_id, "client_secret": self.client_secret},
                headers={"Content-Type": "application/json"},
                timeout=TRAKT_REQUEST_TIMEOUT,
                allow_redirects=False,
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
        """Get authenticated user's username.

        Returns None on any failure (every caller below already treats
        None as "not authenticated" and reports its own generic message)
        - but the REAL cause is logged first rather than silently
        discarded, since a TraktAuthError here is very often "the token
        refresh this request triggered just failed" (see _make_request),
        which used to come out as the misleading "Cannot get lists: not
        authenticated" with zero indication that a refresh was even
        attempted, let alone why it failed.
        """
        try:
            settings = self.get_user_settings()
            return settings.get("user", {}).get("username")
        except TraktAuthError as e:
            log_warning(f"Trakt get_username failed (authentication): {e}")
            return None
        except TraktAPIError as e:
            log_warning(f"Trakt get_username failed (API error): {e}")
            return None

    # =========================================================================
    # List Management
    # =========================================================================

    def get_lists(self) -> List[Dict[str, Any]]:
        """
        Get all lists for the authenticated user.

        Returns:
            List of list objects
        """
        username = self.get_username()
        if not username:
            raise TraktAuthError("Cannot get lists: not authenticated")
        return self._make_request("GET", f"/users/{username}/lists")

    def get_list(self, list_slug: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific list by slug.

        Args:
            list_slug: The list's slug (URL-safe name)

        Returns:
            List object or None if not found
        """
        username = self.get_username()
        if not username:
            return None
        try:
            return self._make_request("GET", f"/users/{username}/lists/{list_slug}")
        except TraktAPIError:
            return None

    def create_list(self, name: str, description: str = "", privacy: str = "private") -> Dict[str, Any]:
        """
        Create a new list.

        Args:
            name: List name
            description: List description
            privacy: "private", "friends", or "public"

        Returns:
            Created list object
        """
        username = self.get_username()
        if not username:
            raise TraktAuthError("Cannot create list: not authenticated")

        data = {
            "name": name,
            "description": description,
            "privacy": privacy,
            "display_numbers": False,
            "allow_comments": False,
        }
        return self._make_request("POST", f"/users/{username}/lists", data)

    def get_or_create_list(self, name: str, description: str = "") -> Dict[str, Any]:
        """
        Get an existing list by name, or create it if it doesn't exist.

        Args:
            name: List name to find or create
            description: Description if creating new list

        Returns:
            List object
        """
        # Speculative direct-lookup slug - Trakt has no "look up by
        # name" endpoint, so this is a best-effort guess tried before
        # falling back to a full by-name search below (see
        # derive_trakt_list_slug's own docstring for why this is only
        # ever a fallback, never authoritative).
        expected_slug = derive_trakt_list_slug(name)

        # Check if list exists
        existing = self.get_list(expected_slug)
        if existing:
            return existing

        # Also search by name in case slug differs
        try:
            lists = self.get_lists()
            for lst in lists:
                if lst.get("name", "").lower() == name.lower():
                    return lst
        except TraktAPIError:
            pass

        # Create new list
        return self.create_list(name, description)

    def delete_list(self, list_slug: str) -> bool:
        """
        Delete a list.

        Args:
            list_slug: The list's slug

        Returns:
            True if deleted
        """
        username = self.get_username()
        if not username:
            return False
        try:
            self._make_request("DELETE", f"/users/{username}/lists/{list_slug}")
            return True
        except TraktAPIError:
            return False

    def get_list_items(self, list_slug: str) -> List[Dict[str, Any]]:
        """
        Get all items in a list.

        Args:
            list_slug: The list's slug

        Returns:
            List of items (movies/shows)
        """
        username = self.get_username()
        if not username:
            return []
        try:
            return self._make_request("GET", f"/users/{username}/lists/{list_slug}/items")
        except TraktAPIError:
            return []

    def add_to_list(
        self, list_slug: str, movies: Optional[List[Dict]] = None, shows: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        Add items to a list.

        Args:
            list_slug: The list's slug
            movies: List of movie objects with 'ids' containing 'imdb' or 'tmdb'
            shows: List of show objects with 'ids' containing 'imdb' or 'tmdb'

        Returns:
            Response with added/existing/not_found counts
        """
        # Check for empty data before making any API calls
        data = {}
        if movies:
            data["movies"] = movies
        if shows:
            data["shows"] = shows

        if not data:
            return {"added": {"movies": 0, "shows": 0}}

        username = self.get_username()
        if not username:
            raise TraktAuthError("Cannot add to list: not authenticated")

        return self._make_request("POST", f"/users/{username}/lists/{list_slug}/items", data)

    def remove_from_list(
        self, list_slug: str, movies: Optional[List[Dict]] = None, shows: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        Remove items from a list.

        Args:
            list_slug: The list's slug
            movies: List of movie objects with 'ids'
            shows: List of show objects with 'ids'

        Returns:
            Response with deleted/not_found counts
        """
        # Check for empty data before making any API calls
        data = {}
        if movies:
            data["movies"] = movies
        if shows:
            data["shows"] = shows

        if not data:
            return {"deleted": {"movies": 0, "shows": 0}}

        username = self.get_username()
        if not username:
            raise TraktAuthError("Cannot remove from list: not authenticated")

        return self._make_request("POST", f"/users/{username}/lists/{list_slug}/items/remove", data)

    def sync_list(
        self,
        list_name: str,
        movies: Optional[List[str]] = None,
        shows: Optional[List[str]] = None,
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Sync a list with the given IMDB IDs, replacing all existing content.

        Args:
            list_name: Name of the list to sync
            movies: List of IMDB IDs for movies
            shows: List of IMDB IDs for shows
            description: List description

        Returns:
            Dict with sync results, plus the list's REAL slug (as
            assigned by Trakt itself, from this same list object's own
            `ids.slug`) under the "list_slug" key - callers that need
            to print/build a `trakt.tv/users/<user>/lists/<slug>` URL
            (see recommenders/external_sync.py) should use THIS value
            rather than re-deriving one from list_name themselves: a
            locally re-derived slug can diverge from the one Trakt
            actually assigned (see derive_trakt_list_slug's docstring)
            and silently 404 even though the sync itself succeeded.
        """
        # Get or create the list
        lst = self.get_or_create_list(list_name, description)
        list_slug = lst.get("ids", {}).get("slug") or lst.get("slug")

        if not list_slug:
            raise TraktAPIError(f"Could not get slug for list: {list_name}")

        # Get current items to remove
        current_items = self.get_list_items(list_slug)

        # Build removal lists
        remove_movies = []
        remove_shows = []
        for item in current_items:
            if item.get("type") == "movie" and item.get("movie"):
                remove_movies.append({"ids": item["movie"].get("ids", {})})
            elif item.get("type") == "show" and item.get("show"):
                remove_shows.append({"ids": item["show"].get("ids", {})})

        # Remove old items
        if remove_movies or remove_shows:
            self.remove_from_list(list_slug, remove_movies or None, remove_shows or None)

        # Build add lists from IMDB IDs
        add_movies = [{"ids": {"imdb": imdb_id}} for imdb_id in (movies or [])]
        add_shows = [{"ids": {"imdb": imdb_id}} for imdb_id in (shows or [])]

        # Add new items
        result = {"added": {"movies": 0, "shows": 0}}
        if add_movies or add_shows:
            # _make_request (and therefore add_to_list) can return None
            # for a 204 response - not expected from this particular
            # endpoint (Trakt documents a body with added/existing/
            # not_found counts), but result["list_slug"] below must
            # never blow up on a NoneType if that ever happens.
            result = self.add_to_list(list_slug, add_movies or None, add_shows or None) or {}

        logger.info(f"Synced Trakt list '{list_name}': {len(movies or [])} movies, {len(shows or [])} shows")

        result["list_slug"] = list_slug
        return result

    # =========================================================================
    # Watch History and Ratings Import
    # =========================================================================

    def get_watched_movies(self) -> List[Dict[str, Any]]:
        """
        Get user's watched movies from Trakt.

        Returns:
            List of watched movie objects with 'movie' containing title, year, and ids
        """
        username = self.get_username()
        if not username:
            return []
        try:
            return self._make_request("GET", f"/users/{username}/watched/movies")
        except TraktAPIError:
            return []

    def get_watched_shows(self) -> List[Dict[str, Any]]:
        """
        Get user's watched shows from Trakt.

        Returns:
            List of watched show objects with 'show' containing title, year, and ids
        """
        username = self.get_username()
        if not username:
            return []
        try:
            return self._make_request("GET", f"/users/{username}/watched/shows")
        except TraktAPIError:
            return []

    def add_to_history(self, movies: Optional[List[str]] = None, shows: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Add items to watch history (mark as watched).

        Args:
            movies: List of IMDB IDs for movies
            shows: List of IMDB IDs for shows

        Returns:
            Response with added/not_found counts
        """
        data = {}
        if movies:
            data["movies"] = [{"ids": {"imdb": imdb_id}} for imdb_id in movies]
        if shows:
            data["shows"] = [{"ids": {"imdb": imdb_id}} for imdb_id in shows]

        if not data:
            return {"added": {"movies": 0, "episodes": 0}}

        return self._make_request("POST", "/sync/history", data)

    def get_ratings(self, media_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get user's ratings from Trakt.

        Args:
            media_type: Optional filter - 'movies', 'shows', or None for all

        Returns:
            List of rating objects with 'rating', 'rated_at', and media info
        """
        username = self.get_username()
        if not username:
            return []
        try:
            endpoint = f"/users/{username}/ratings"
            if media_type:
                endpoint += f"/{media_type}"
            return self._make_request("GET", endpoint)
        except TraktAPIError:
            return []

    def get_watchlist(self, media_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get user's watchlist from Trakt.

        Args:
            media_type: Optional filter - 'movies', 'shows', or None for all

        Returns:
            List of watchlist items with media info and ids
        """
        username = self.get_username()
        if not username:
            return []
        try:
            endpoint = f"/users/{username}/watchlist"
            if media_type:
                endpoint += f"/{media_type}"
            return self._make_request("GET", endpoint)
        except TraktAPIError:
            return []

    def get_watch_history_imdb_ids(self, media_type: str = "movies") -> set:
        """
        Get set of IMDB IDs from user's Trakt watch history.

        Args:
            media_type: 'movies' or 'shows'

        Returns:
            Set of IMDB IDs for watched items
        """
        imdb_ids = set()

        if media_type == "movies":
            watched = self.get_watched_movies()
            for item in watched:
                movie = item.get("movie", {})
                imdb_id = movie.get("ids", {}).get("imdb")
                if imdb_id:
                    imdb_ids.add(imdb_id)
        else:
            watched = self.get_watched_shows()
            for item in watched:
                show = item.get("show", {})
                imdb_id = show.get("ids", {}).get("imdb")
                if imdb_id:
                    imdb_ids.add(imdb_id)

        return imdb_ids

    def get_watchlist_imdb_ids(self, media_type: Optional[str] = None) -> set:
        """
        Get set of IMDB IDs from user's Trakt watchlist.

        Args:
            media_type: 'movies', 'shows', or None for all

        Returns:
            Set of IMDB IDs for watchlist items
        """
        imdb_ids = set()
        watchlist = self.get_watchlist(media_type)

        for item in watchlist:
            item_type = item.get("type")
            if item_type == "movie":
                media = item.get("movie", {})
            elif item_type == "show":
                media = item.get("show", {})
            else:
                continue

            imdb_id = media.get("ids", {}).get("imdb")
            if imdb_id:
                imdb_ids.add(imdb_id)

        return imdb_ids

    # =========================================================================
    # Discovery: Trending, Popular, Recommendations, Related
    # =========================================================================

    def get_trending(self, media_type: str = "movies", limit: int = 20, page: int = 1) -> List[Dict[str, Any]]:
        """
        Get trending movies or shows (most watched right now).

        Args:
            media_type: 'movies' or 'shows'
            limit: Number of results (1-100)
            page: Page number for pagination

        Returns:
            List of items with 'watchers' count and media info
        """
        endpoint = f"/{media_type}/trending"
        params = f"?limit={min(limit, 100)}&page={page}&extended=full"
        return self._make_request("GET", endpoint + params, authenticated=False)

    def get_popular(self, media_type: str = "movies", limit: int = 20, page: int = 1) -> List[Dict[str, Any]]:
        """
        Get popular movies or shows (most watched all time).

        Args:
            media_type: 'movies' or 'shows'
            limit: Number of results (1-100)
            page: Page number for pagination

        Returns:
            List of media objects with full metadata
        """
        endpoint = f"/{media_type}/popular"
        params = f"?limit={min(limit, 100)}&page={page}&extended=full"
        return self._make_request("GET", endpoint + params, authenticated=False)

    def get_recommendations(self, media_type: str = "movies", limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get personalized recommendations based on user's ratings.

        Requires authentication. Returns items Trakt thinks the user would like
        based on their watch history and ratings.

        Args:
            media_type: 'movies' or 'shows'
            limit: Number of results (1-100)

        Returns:
            List of recommended media objects
        """
        if not self.is_authenticated:
            logger.warning("Cannot get recommendations: not authenticated")
            return []
        endpoint = f"/recommendations/{media_type}"
        params = f"?limit={min(limit, 100)}&extended=full"
        try:
            return self._make_request("GET", endpoint + params)
        except TraktAPIError as e:
            logger.warning(f"Failed to get Trakt recommendations: {e}")
            return []

    def get_related(self, media_type: str, trakt_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get related/similar movies or shows.

        Args:
            media_type: 'movies' or 'shows'
            trakt_id: Trakt ID of the source item
            limit: Number of results (1-100)

        Returns:
            List of related media objects
        """
        endpoint = f"/{media_type}/{trakt_id}/related"
        params = f"?limit={min(limit, 100)}&extended=full"
        try:
            return self._make_request("GET", endpoint + params, authenticated=False)
        except TraktAPIError as e:
            logger.warning(f"Failed to get related items for {trakt_id}: {e}")
            return []

    def get_anticipated(self, media_type: str = "movies", limit: int = 20, page: int = 1) -> List[Dict[str, Any]]:
        """
        Get most anticipated upcoming movies or shows.

        Args:
            media_type: 'movies' or 'shows'
            limit: Number of results (1-100)
            page: Page number for pagination

        Returns:
            List of items with 'list_count' and media info
        """
        endpoint = f"/{media_type}/anticipated"
        params = f"?limit={min(limit, 100)}&page={page}&extended=full"
        return self._make_request("GET", endpoint + params, authenticated=False)


def save_trakt_tokens(
    trakt_path: str,
    access_token: str,
    refresh_token: str,
    created_at: Optional[int] = None,
    expires_in: Optional[int] = None,
) -> None:
    """
    Persist rotated Trakt tokens to trakt.yml.

    The single, shared implementation TraktClient's own runtime
    token_callback (see create_trakt_client below) AND utils/trakt_auth.py's
    manual `python3 utils/trakt_auth.py` device-auth flow both call -
    previously duplicated (trakt_auth.py had its own copy; create_trakt_client
    passed no callback at all, so a refreshed token during a normal
    recommender run was never saved anywhere - Trakt rotates refresh
    tokens single-use, so the NEXT run then replayed an already-consumed
    one and failed).

    Atomic (temp file in the same directory + os.replace) so a crash
    mid-write, or two recommenders (movie.py/tv.py, or a web-UI-triggered
    run racing a cron run) refreshing at close to the same moment, can
    never truncate/corrupt the file a third reader might load at the
    same time. Permissions are re-hardened after every write
    (harden_file_permissions) since this file holds live OAuth
    credentials.

    Every OTHER key/section in trakt.yml (export/import/discovery
    settings, comments) survives untouched, since the file is read into
    a dict first and only these specific keys are set on it - EXCEPT
    comments specifically: this uses plain pyyaml, not ruamel.yaml's
    round-trip mode (config_io.load_module/save_module's approach for
    the web UI's own config-save routes), because ruamel.yaml is a
    requirements-ui.txt-only dependency and this path runs on every
    plain CLI/cron install (requirements.txt only, no UI stack) - adding
    it here would mean every install pulls in the heavier UI dependency
    just to keep a token refresh's rewrite comment-preserving. Comments
    in trakt.yml are therefore NOT preserved across an automatic token
    refresh (they already weren't across a manual `trakt_auth.py`
    re-auth, before this change hoisted that script's own save_tokens
    into this one shared function).

    Args:
        trakt_path: Full path to config/trakt.yml
        access_token: New access token to save
        refresh_token: New (rotated) refresh token to save
        created_at: Unix timestamp the new access_token was issued at,
            if known (see TraktClient.created_at) - stored as
            token_created_at
        expires_in: Seconds the new access_token is valid for, if known
            (see TraktClient.expires_in) - stored as token_expires_in

    Raises:
        OSError: if trakt_path can't be read or the atomic write fails -
            callers (create_trakt_client's token_callback below) catch
            this so a persistence failure never crashes the run that
            triggered the refresh; the refreshed tokens still work for
            the REST of this process's lifetime either way, exactly as
            before this function existed.
    """
    directory = os.path.dirname(trakt_path) or "."
    with open(trakt_path, "r", encoding="utf-8") as f:
        trakt_config = yaml.safe_load(f) or {}

    trakt_config["access_token"] = access_token
    trakt_config["refresh_token"] = refresh_token
    if created_at is not None:
        trakt_config["token_created_at"] = created_at
    if expires_in is not None:
        trakt_config["token_expires_in"] = expires_in

    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".yml", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(trakt_config, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, trakt_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    harden_file_permissions(trakt_path)


def create_trakt_client(config: Dict) -> Optional[TraktClient]:
    """
    Create a TraktClient from config, if enabled.

    Args:
        config: Full application config dict

    Returns:
        TraktClient instance or None if Trakt is disabled
    """
    trakt_config = config.get("trakt", {})

    if not trakt_config.get("enabled", False):
        return None

    client_id = trakt_config.get("client_id")
    client_secret = trakt_config.get("client_secret")

    if not client_id or not client_secret:
        logger.warning("Trakt enabled but client_id/client_secret not configured")
        return None

    access_token = trakt_config.get("access_token")
    refresh_token = trakt_config.get("refresh_token")
    created_at = trakt_config.get("token_created_at")
    expires_in = trakt_config.get("token_expires_in")

    trakt_path = os.path.join(get_project_root(), "config", "trakt.yml")

    def _persist_refreshed_tokens(
        new_access_token: str,
        new_refresh_token: str,
        new_created_at: Optional[int] = None,
        new_expires_in: Optional[int] = None,
    ) -> None:
        try:
            save_trakt_tokens(trakt_path, new_access_token, new_refresh_token, new_created_at, new_expires_in)
        except OSError as e:
            # Persistence failing must never crash the run that
            # triggered the refresh - the refreshed token still works
            # in memory for the rest of THIS process either way. Logged
            # loud (error, not warning) since this is exactly the
            # failure mode that made refreshed tokens a one-way trapdoor
            # before this function existed - it must never go silent again.
            log_error(f"Could not persist refreshed Trakt tokens to {trakt_path}: {e}")

    return TraktClient(
        client_id=client_id,
        client_secret=client_secret,
        access_token=access_token,
        refresh_token=refresh_token,
        created_at=created_at,
        expires_in=expires_in,
        token_callback=_persist_refreshed_tokens,
    )


def get_authenticated_trakt_client(config: Dict) -> Optional[TraktClient]:
    """
    Get an authenticated TraktClient, or None if unavailable.

    Convenience wrapper that creates client and verifies authentication.
    Use this instead of create_trakt_client() when you need to make API calls.

    Args:
        config: Full application config dict

    Returns:
        Authenticated TraktClient or None if disabled/not authenticated
    """
    client = create_trakt_client(config)
    if not client:
        return None
    if not client.is_authenticated:
        logger.warning("Trakt client created but not authenticated")
        return None
    return client


# Cache version for Trakt enhancement tracking
TRAKT_ENHANCE_CACHE_VERSION = 1


def load_trakt_enhance_cache(cache_dir: str) -> Dict:
    """
    Load cache of Trakt IDs already processed for profile enhancement.

    Args:
        cache_dir: Directory where cache file is stored

    Returns:
        Dict with 'movie_ids' and 'show_ids' sets
    """
    cache_path = os.path.join(cache_dir, "trakt_enhance_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("version", 0) >= TRAKT_ENHANCE_CACHE_VERSION:
                    return {"movie_ids": set(data.get("movie_ids", [])), "show_ids": set(data.get("show_ids", []))}
        except Exception as e:
            logger.debug(f"Failed to load Trakt enhance cache: {e}")
    return {"movie_ids": set(), "show_ids": set()}


def save_trakt_enhance_cache(cache_dir: str, movie_ids: set, show_ids: set):
    """
    Save cache of Trakt IDs processed for profile enhancement.

    Args:
        cache_dir: Directory where cache file is stored
        movie_ids: Set of movie IMDB IDs seen from Trakt
        show_ids: Set of show IMDB IDs seen from Trakt
    """
    cache_path = os.path.join(cache_dir, "trakt_enhance_cache.json")
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(
                {"version": TRAKT_ENHANCE_CACHE_VERSION, "movie_ids": list(movie_ids), "show_ids": list(show_ids)}, f
            )
    except Exception as e:
        logger.debug(f"Failed to save Trakt enhance cache: {e}")


def fetch_tmdb_details_for_profile(tmdb_api_key: str, tmdb_id: int, media_type: str) -> Optional[Dict]:
    """
    Fetch TMDB details for a movie or TV show.

    Args:
        tmdb_api_key: TMDB API key
        tmdb_id: TMDB ID of the item
        media_type: 'movie' or 'tv'

    Returns:
        Dict with title, year, rating, vote_count, overview, genres, cast,
        keywords, directors/studios, or None on failure
    """
    try:
        endpoint = "movie" if media_type == "movie" else "tv"
        url = f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}"
        params = {"api_key": tmdb_api_key, "append_to_response": "keywords,credits"}
        response = requests.get(url, params=params, timeout=10, allow_redirects=False)

        if response.status_code != 200:
            return None

        data = response.json()

        # Extract year from release date
        if media_type == "movie":
            title = data.get("title", "")
            release_date = data.get("release_date", "")
        else:
            title = data.get("name", "")
            release_date = data.get("first_air_date", "")
        year = int(release_date[:4]) if release_date and len(release_date) >= 4 else None

        details = {
            "title": title,
            "year": year,
            "rating": data.get("vote_average", 0),
            "vote_count": data.get("vote_count", 0),
            "overview": data.get("overview", ""),
            "genres": [g["name"] for g in data.get("genres", [])],
            "original_language": data.get("original_language", ""),
            "cast": [],
            "keywords": [],
            "directors": [],
            "studios": [],
        }

        # Extract cast (top 5)
        credits = data.get("credits", {})
        for cast_member in credits.get("cast", [])[:5]:
            if cast_member.get("name"):
                details["cast"].append(cast_member["name"])

        # Extract keywords (top 10)
        keywords_data = data.get("keywords", {})
        keyword_list = keywords_data.get("keywords", keywords_data.get("results", []))
        for kw in keyword_list[:10]:
            if kw.get("name"):
                details["keywords"].append(kw["name"])

        # Extract directors (movies) or studios (TV)
        if media_type == "movie":
            for crew in credits.get("crew", []):
                if crew.get("job") == "Director" and crew.get("name"):
                    details["directors"].append(crew["name"])
        else:
            for network in data.get("networks", [])[:2]:
                if network.get("name"):
                    details["studios"].append(network["name"])

        return details

    except Exception:
        return None


def enhance_profile_with_trakt(
    profile: Dict,
    config: Dict,
    tmdb_api_key: str,
    cache_dir: str,
    media_type: str = "movie",
    single_user: Optional[str] = None,
) -> Dict:
    """
    Enhance user profile with Trakt watch history.

    Fetches Trakt watch history for items not already in the profile (from streaming
    services) and adds their genres, keywords, cast, etc. to build a more complete
    taste profile.

    Args:
        profile: Existing profile dict with counters (can be Counter or dict objects)
        config: Full config dict with Trakt settings
        tmdb_api_key: TMDB API key for fetching details
        cache_dir: Directory for cache files
        media_type: 'movie' or 'tv'
        single_user: Current user for user mapping checks (optional)

    Returns:
        Enhanced profile (same dict, modified in place)
    """
    # Import here to avoid circular imports
    from .tmdb import get_tmdb_id_from_imdb, load_imdb_tmdb_cache, save_imdb_tmdb_cache

    trakt_config = config.get("trakt", {})
    import_config = trakt_config.get("import", {})
    export_config = trakt_config.get("export", {})

    # Check if Trakt import is enabled
    if not all(
        [
            trakt_config.get("enabled", False),
            import_config.get("enabled", True),
            import_config.get("merge_watch_history", True),
        ]
    ):
        return profile

    # Check user mapping - only enhance for configured users
    if single_user:
        user_mode = export_config.get("user_mode", "mapping")
        plex_users = export_config.get("plex_users", [])
        if user_mode == "mapping" and plex_users:
            plex_users_lower = [u.lower() for u in plex_users]
            if single_user.lower() not in plex_users_lower:
                return profile  # Skip - user not in Trakt mapping

    # Get authenticated Trakt client
    trakt_client = get_authenticated_trakt_client(config)
    if not trakt_client:
        return profile

    print("  Enhancing profile with Trakt watch history...")

    # Get Trakt watch history
    sys.stdout.write(f"    Fetching Trakt {media_type} history...")
    sys.stdout.flush()
    if media_type == "movie":
        watched = trakt_client.get_watched_movies()
    else:
        watched = trakt_client.get_watched_shows()

    if not watched:
        print(f"\r    No Trakt {media_type} history found      ")
        return profile

    # Extract all IMDB IDs from Trakt response
    media_key = "movie" if media_type == "movie" else "show"
    current_imdb_ids = set()
    for item in watched:
        imdb_id = item.get(media_key, {}).get("ids", {}).get("imdb")
        if imdb_id:
            current_imdb_ids.add(imdb_id)

    # Load cached IDs to check for changes
    enhance_cache = load_trakt_enhance_cache(cache_dir)
    cache_key = "movie_ids" if media_type == "movie" else "show_ids"
    cached_ids = enhance_cache.get(cache_key, set())

    # Check if anything changed
    new_ids = current_imdb_ids - cached_ids
    if not new_ids:
        print(f"\r    Trakt {media_type}s unchanged ({len(current_imdb_ids)} items) - skipping")
        return profile

    print(f"\r    Found {len(new_ids)} new Trakt {media_type}s to process")

    # Get existing TMDB IDs from profile to avoid duplicates
    existing_tmdb_ids = set()
    if "tmdb_ids" in profile:
        if isinstance(profile["tmdb_ids"], set):
            existing_tmdb_ids = profile["tmdb_ids"]
        else:
            existing_tmdb_ids = set(profile["tmdb_ids"])

    # Load IMDB→TMDB cache for fast lookups
    imdb_cache = load_imdb_tmdb_cache(cache_dir)
    initial_cache_size = len(imdb_cache)

    # Process only new Trakt watched items
    added_count = 0
    total = len(new_ids)
    for i, imdb_id in enumerate(new_ids, 1):
        # Show progress
        pct = int((i / total) * 100)
        sys.stdout.write(f"\r    Processing new Trakt items {i}/{total} ({pct}%) - {added_count} added")
        sys.stdout.flush()

        # Convert IMDB to TMDB (uses cache)
        tmdb_id = get_tmdb_id_from_imdb(tmdb_api_key, imdb_id, media_type, imdb_cache)
        if not tmdb_id or tmdb_id in existing_tmdb_ids:
            continue

        # Fetch TMDB details
        details = fetch_tmdb_details_for_profile(tmdb_api_key, tmdb_id, media_type)
        if not details:
            continue

        # Add to profile with base weight
        weight = 1.0

        # Handle both Counter objects and regular dicts
        for genre in details.get("genres", []):
            genre_key = genre.lower()
            if hasattr(profile.get("genres"), "__iadd__"):
                # Counter-like object
                profile["genres"][genre_key] += weight
            else:
                # Regular dict
                profile["genres"][genre_key] = profile.get("genres", {}).get(genre_key, 0) + weight

        for actor in details.get("cast", [])[:3]:  # Top 3 actors
            if hasattr(profile.get("actors"), "__iadd__"):
                profile["actors"][actor] += weight
            else:
                profile["actors"][actor] = profile.get("actors", {}).get(actor, 0) + weight

        for keyword in details.get("keywords", []):
            keyword_key = keyword.lower()
            # Check for tmdb_keywords first (used by base recommender), then keywords
            keywords_field = "tmdb_keywords" if "tmdb_keywords" in profile else "keywords"
            if hasattr(profile.get(keywords_field), "__iadd__"):
                profile[keywords_field][keyword_key] += weight
            else:
                if keywords_field not in profile:
                    profile[keywords_field] = {}
                profile[keywords_field][keyword_key] = profile[keywords_field].get(keyword_key, 0) + weight

        if media_type == "movie":
            for director in details.get("directors", []):
                if hasattr(profile.get("directors"), "__iadd__"):
                    profile["directors"][director] += weight
                else:
                    profile["directors"][director] = profile.get("directors", {}).get(director, 0) + weight
        else:
            for studio in details.get("studios", []):
                studio_key = studio.lower()
                studio_field = "studio" if "studio" in profile else "studios"
                if hasattr(profile.get(studio_field), "__iadd__"):
                    profile[studio_field][studio_key] += weight
                else:
                    if studio_field not in profile:
                        profile[studio_field] = {}
                    profile[studio_field][studio_key] = profile[studio_field].get(studio_key, 0) + weight

        # Track that we've added this TMDB ID
        if "tmdb_ids" not in profile:
            profile["tmdb_ids"] = set()
        if isinstance(profile["tmdb_ids"], set):
            profile["tmdb_ids"].add(tmdb_id)
        else:
            profile["tmdb_ids"].append(tmdb_id)

        added_count += 1

    # Save caches
    if len(imdb_cache) > initial_cache_size:
        save_imdb_tmdb_cache(cache_dir, imdb_cache)

    # Update enhance cache with all current IDs
    if media_type == "movie":
        save_trakt_enhance_cache(cache_dir, current_imdb_ids, enhance_cache.get("show_ids", set()))
    else:
        save_trakt_enhance_cache(cache_dir, enhance_cache.get("movie_ids", set()), current_imdb_ids)

    # Final summary
    print(f"\r    Processing new Trakt items {total}/{total} (100%) - {added_count} added")

    return profile
