"""
Tautulli API client for Curatarr.

Optional integration: supplements Plex-native watch history with history
pulled from a Tautulli instance, weighted the same way as Plex history.
Mainly useful for shared/external Plex users whose Plex-native history
retention is thin.

Never raises out of the high-level fetch_/merge_ helpers below - if
Tautulli is disabled, unreachable, or a user can't be mapped, callers
should transparently fall back to Plex-only behavior.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from .api_client import BaseAPIClient
from .display import log_warning

logger = logging.getLogger('curatarr')

TAUTULLI_RATE_LIMIT_DELAY = 0.1
TAUTULLI_REQUEST_TIMEOUT = 30

# Generous default page size for get_history - large enough to cover most
# users' full history in one call without paging.
TAUTULLI_DEFAULT_HISTORY_LENGTH = 5000

PLACEHOLDER_API_KEY = 'YOUR_TAUTULLI_API_KEY'


class TautulliAPIError(Exception):
    """Raised when a Tautulli API request fails."""
    pass


class TautulliClient(BaseAPIClient):
    """
    Tautulli API client (api/v2), authenticated via `apikey` query param.

    See: https://github.com/Tautulli/Tautulli/wiki/API-Reference
    """

    api_name = "Tautulli"
    exception_class = TautulliAPIError
    rate_limit_delay = TAUTULLI_RATE_LIMIT_DELAY
    request_timeout = TAUTULLI_REQUEST_TIMEOUT

    def __init__(self, url: str, api_key: str):
        """
        Initialize Tautulli client.

        Args:
            url: Base Tautulli URL, e.g. http://192.168.1.10:8181
            api_key: Tautulli API key (Settings -> Web Interface -> API Key)
        """
        super().__init__()
        self.base_url = url.rstrip('/')
        self.api_key = api_key

    def _get_headers(self) -> Dict[str, str]:
        """Tautulli doesn't require auth headers (uses query param)."""
        return {}

    def _call(self, cmd: str, params: Optional[Dict] = None) -> Any:
        """
        Call {base_url}/api/v2?apikey=<key>&cmd=<cmd> and unwrap response.data.

        Args:
            cmd: Tautulli API command (e.g. 'get_users', 'get_history')
            params: Additional query params for the command

        Returns:
            The `response.data` payload

        Raises:
            TautulliAPIError: On HTTP errors, timeouts, or an error result
        """
        query = {'apikey': self.api_key, 'cmd': cmd}
        if params:
            query.update(params)

        url = f"{self.base_url}/api/v2"
        result = self._make_request_to_url("GET", url, params=query)

        if not isinstance(result, dict) or 'response' not in result:
            raise TautulliAPIError(f"Unexpected response shape from Tautulli cmd={cmd}")

        envelope = result['response']
        if envelope.get('result') != 'success':
            raise TautulliAPIError(envelope.get('message') or f"Tautulli cmd={cmd} failed")

        return envelope.get('data')

    def get_users(self) -> List[Dict]:
        """
        Get all Tautulli-tracked users.

        Returns:
            List of user dicts (user_id, username, email, friendly_name, ...)
        """
        data = self._call('get_users')
        return data or []

    def get_history(self, user_id: Any, length: int = TAUTULLI_DEFAULT_HISTORY_LENGTH) -> List[Dict]:
        """
        Get watch history rows for a Tautulli user.

        Args:
            user_id: Tautulli user_id
            length: Max number of history rows to return

        Returns:
            List of history row dicts (rating_key, grandparent_rating_key,
            media_type, stopped, date, watched_status, ...)
        """
        data = self._call('get_history', params={'user_id': user_id, 'length': length})
        if isinstance(data, dict):
            # Tautulli wraps history in a DataTables-style envelope: {"data": [...], ...}
            return data.get('data') or []
        return data or []


def create_tautulli_client(config: Dict) -> Optional[TautulliClient]:
    """
    Create a TautulliClient from config, if configured and enabled.

    Args:
        config: Full config dict containing an optional 'tautulli' section

    Returns:
        TautulliClient if configured and enabled, None otherwise
    """
    tautulli_config = config.get('tautulli', {}) or {}

    if not tautulli_config.get('enabled', False):
        return None

    url = tautulli_config.get('url')
    api_key = tautulli_config.get('api_key')

    if not url or not api_key or api_key == PLACEHOLDER_API_KEY:
        log_warning("Tautulli enabled but 'url'/'api_key' not configured - skipping Tautulli history")
        return None

    return TautulliClient(url, api_key)


class TautulliHistoryItem:
    """
    Minimal duck-type of a plexapi history Video item.

    Exposes the same attributes (`ratingKey`, `viewedAt`, `userRating`) that
    utils.plex.fetch_plex_watch_history_movies' HistoryItem exposes, so it can
    be merged with real Plex history items and processed by the exact same
    downstream weighting code.
    """

    def __init__(self, rating_key: str, viewed_at: Optional[datetime] = None,
                 user_rating: Optional[float] = None):
        self.ratingKey = rating_key
        self.viewedAt = viewed_at
        self.userRating = user_rating


def build_user_map(client: TautulliClient, config: Dict) -> Dict[str, str]:
    """
    Map Plex account IDs to Tautulli user IDs.

    Matches on email first (most stable across username changes), falling
    back to username/friendly_name. Never raises - returns {} on any error
    so callers can fall back to Plex-only behavior.

    Args:
        client: TautulliClient instance
        config: Full config dict with plex.token

    Returns:
        Dict mapping Plex account_id (str) -> Tautulli user_id (str)
    """
    try:
        tautulli_users = client.get_users()
    except TautulliAPIError as e:
        log_warning(f"Tautulli: could not fetch users for mapping: {e}")
        return {}

    if not tautulli_users:
        return {}

    try:
        # Imported here to avoid a hard dependency for callers that only
        # need the API client (and to keep this module easy to unit test).
        from plexapi.myplex import MyPlexAccount

        account = MyPlexAccount(token=config['plex']['token'])
        plex_identities = [{
            # Plex convention: the server owner's account_id in the
            # `/accounts` endpoint (what get_plex_account_ids() /
            # fetch_plex_watch_history_movies/shows() actually use) is
            # always local id '1', which differs from the owner's global
            # plex.tv account.id used everywhere else in this account
            # object. Key on '1' here so owner history correctly merges.
            'id': '1',
            'username': account.username,
            'email': getattr(account, 'email', None),
        }]
        for u in account.users():
            plex_identities.append({
                'id': str(u.id),
                'username': u.title,
                'email': getattr(u, 'email', None),
            })
    except Exception as e:
        log_warning(f"Tautulli: could not fetch Plex users for mapping: {e}")
        return {}

    return map_users(plex_identities, tautulli_users)


def map_users(plex_identities: List[Dict], tautulli_users: List[Dict]) -> Dict[str, str]:
    """
    Match Plex users to Tautulli users by email, falling back to username.

    Args:
        plex_identities: List of dicts with 'id', 'username', 'email'
        tautulli_users: List of raw Tautulli user dicts (get_users() output)

    Returns:
        Dict mapping Plex account_id (str) -> Tautulli user_id (str)
    """
    by_email: Dict[str, str] = {}
    by_username: Dict[str, str] = {}

    for tu in tautulli_users:
        if tu.get('user_id') is None:
            continue
        tautulli_id = str(tu['user_id'])

        email = (tu.get('email') or '').strip().lower()
        if email and email not in by_email:
            by_email[email] = tautulli_id

        for name_field in ('username', 'friendly_name'):
            name = (tu.get(name_field) or '').strip().lower()
            if name and name not in by_username:
                by_username[name] = tautulli_id

    user_map: Dict[str, str] = {}
    for identity in plex_identities:
        plex_id = identity.get('id')
        if not plex_id:
            continue

        email = (identity.get('email') or '').strip().lower()
        username = (identity.get('username') or '').strip().lower()

        tautulli_id = None
        if email and email in by_email:
            tautulli_id = by_email[email]
        elif username and username in by_username:
            tautulli_id = by_username[username]

        if tautulli_id:
            user_map[str(plex_id)] = tautulli_id
        else:
            logger.debug(f"Tautulli: no match found for Plex user '{identity.get('username')}'")

    return user_map


def fetch_tautulli_movie_history(
    config: Dict,
    account_ids: List[str],
    client: Optional[TautulliClient] = None,
    user_map: Optional[Dict[str, str]] = None,
) -> List[TautulliHistoryItem]:
    """
    Fetch movie watch history from Tautulli for the given Plex account IDs.

    Safe no-op (returns []) if Tautulli is disabled, unreachable, or none of
    the account_ids can be mapped to a Tautulli user.

    Args:
        config: Full config dict
        account_ids: Plex account IDs to fetch Tautulli history for
        client: Optional pre-built TautulliClient (mainly for testing)
        user_map: Optional pre-built account_id -> tautulli_user_id map

    Returns:
        List of TautulliHistoryItem (duck-typed like plexapi history Video items)
    """
    client = client or create_tautulli_client(config)
    if not client:
        return []

    user_map = user_map if user_map is not None else build_user_map(client, config)
    if not user_map:
        return []

    items: List[TautulliHistoryItem] = []
    for account_id in account_ids:
        tautulli_user_id = user_map.get(str(account_id))
        if not tautulli_user_id:
            continue

        try:
            rows = client.get_history(tautulli_user_id)
        except TautulliAPIError as e:
            log_warning(f"Tautulli: history fetch failed for user {tautulli_user_id}: {e}")
            continue

        for row in rows:
            if row.get('media_type') != 'movie':
                continue
            if row.get('watched_status') != 1:
                continue

            rating_key = row.get('rating_key')
            if not rating_key:
                continue

            ts = row.get('stopped') or row.get('date')
            viewed_at = datetime.fromtimestamp(int(ts)) if ts else None

            items.append(TautulliHistoryItem(str(rating_key), viewed_at, None))

    return items


def fetch_tautulli_show_watched_data(
    config: Dict,
    account_ids: List[str],
    client: Optional[TautulliClient] = None,
    user_map: Optional[Dict[str, str]] = None,
) -> Tuple[Set[int], Dict[int, int]]:
    """
    Fetch TV watch history from Tautulli for the given Plex account IDs.

    Safe no-op (returns (set(), {})) if Tautulli is disabled, unreachable, or
    none of the account_ids can be mapped to a Tautulli user.

    Args:
        config: Full config dict
        account_ids: Plex account IDs to fetch Tautulli history for
        client: Optional pre-built TautulliClient (mainly for testing)
        user_map: Optional pre-built account_id -> tautulli_user_id map

    Returns:
        Tuple of (watched_show_ids set, show_id -> latest viewed_at epoch dict)
    """
    client = client or create_tautulli_client(config)
    if not client:
        return set(), {}

    user_map = user_map if user_map is not None else build_user_map(client, config)
    if not user_map:
        return set(), {}

    watched_ids: Set[int] = set()
    timestamps: Dict[int, int] = {}

    for account_id in account_ids:
        tautulli_user_id = user_map.get(str(account_id))
        if not tautulli_user_id:
            continue

        try:
            rows = client.get_history(tautulli_user_id)
        except TautulliAPIError as e:
            log_warning(f"Tautulli: history fetch failed for user {tautulli_user_id}: {e}")
            continue

        for row in rows:
            if row.get('media_type') != 'episode':
                continue
            if row.get('watched_status') != 1:
                continue

            show_key = row.get('grandparent_rating_key')
            if not show_key:
                continue

            show_id = int(show_key)
            watched_ids.add(show_id)

            ts = row.get('stopped') or row.get('date')
            if ts:
                ts = int(ts)
                if show_id not in timestamps or ts > timestamps[show_id]:
                    timestamps[show_id] = ts

    return watched_ids, timestamps


def merge_movie_history(plex_items: List[Any], tautulli_items: List[Any]) -> List[Any]:
    """
    Merge Plex + Tautulli movie history, de-duplicated by rating_key.

    Weighted the same way as Plex-only history since the merged items are
    fed through the exact same downstream processing (recency decay from
    `.viewedAt`, rating multiplier from `.userRating`).

    A Plex-sourced rating always wins (Tautulli doesn't track star ratings).
    The most recent `viewedAt` across both sources is kept.

    Args:
        plex_items: History items from utils.plex.fetch_plex_watch_history_movies
        tautulli_items: History items from fetch_tautulli_movie_history

    Returns:
        List of merged, de-duplicated history items
    """
    merged: Dict[str, TautulliHistoryItem] = {}

    for item in list(plex_items) + list(tautulli_items):
        key = str(item.ratingKey)
        existing = merged.get(key)

        if existing is None:
            merged[key] = TautulliHistoryItem(key, item.viewedAt, item.userRating)
            continue

        best_rating = existing.userRating if existing.userRating is not None else item.userRating

        best_viewed_at = existing.viewedAt
        if item.viewedAt and (best_viewed_at is None or item.viewedAt > best_viewed_at):
            best_viewed_at = item.viewedAt

        merged[key] = TautulliHistoryItem(key, best_viewed_at, best_rating)

    return list(merged.values())


def merge_show_watched_data(
    plex_ids: Set[int],
    plex_timestamps: Dict[int, int],
    tautulli_ids: Set[int],
    tautulli_timestamps: Dict[int, int],
) -> Tuple[Set[int], Dict[int, int]]:
    """
    Merge Plex + Tautulli watched-show data, de-duplicated by show ID.

    Weighted the same way as Plex-only history since the merged result feeds
    the exact same downstream recency-decay processing.

    Args:
        plex_ids: Watched show IDs from Plex history
        plex_timestamps: show_id -> latest viewedAt epoch from Plex history
        tautulli_ids: Watched show IDs from Tautulli history
        tautulli_timestamps: show_id -> latest viewedAt epoch from Tautulli history

    Returns:
        Tuple of (merged watched_show_ids set, merged show_id -> latest viewedAt dict)
    """
    merged_ids = set(plex_ids) | set(tautulli_ids)

    merged_timestamps = dict(plex_timestamps)
    for show_id, ts in tautulli_timestamps.items():
        if show_id not in merged_timestamps or ts > merged_timestamps[show_id]:
            merged_timestamps[show_id] = ts

    return merged_ids, merged_timestamps
