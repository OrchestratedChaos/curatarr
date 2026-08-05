"""
Plex API adapter for Curatarr: connection setup, watch-history fetches,
collection CRUD, and user/genre/rating-field extraction helpers - i.e.
"how do we talk to Plex", not "what should Curatarr do with the answer".

The content-rating hierarchy and user label-visibility POLICY built on
top of this client (MOVIE_RATING_HIERARCHY/TV_RATING_HIERARCHY,
get_max_rating_for_user, is_rating_allowed, apply_user_label_restrictions)
live in utils/plex_policy.py instead (audit remediation batch F/I,
PR1(b)) - see that module's own docstring for why. This module has no
import of plex_policy (and must never gain one - plex_policy imports
FROM here, not the other way around, to avoid a cycle).
"""

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, TypedDict, Union

import plexapi.exceptions
import plexapi.server
import requests
import urllib3
from plexapi.myplex import MyPlexAccount

from .config import PLEX_LONG_REQUEST_TIMEOUT, PLEX_REQUEST_TIMEOUT
from .display import GREEN, RESET, YELLOW, log_error, log_warning
from .helpers import get_project_root, harden_file_permissions, normalize_title, read_response_capped
from .labels import remove_labels_from_items
from .metrics import record_api_call

# Module-level logger
logger = logging.getLogger("curatarr")


def _resolve_verify_ssl(config: dict) -> bool:
    """config['plex'].get('verify_ssl', True), with the side effect of
    suppressing urllib3's InsecureRequestWarning ONLY when THIS config
    actually opts out of certificate verification (an explicit user
    choice, e.g. for a local Plex server with a self-signed cert) -
    never unconditionally at import time (this module's previous
    behavior), which would also silence the warning for every other
    HTTPS request this process ever makes, including ones that never
    disabled verification at all."""
    verify_ssl = config["plex"].get("verify_ssl", True)
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return verify_ssl


def _capped_get(url, **kwargs):
    """requests.get() wrapper that streams the response and caps its
    body size (see utils.helpers.read_response_capped) - config['plex']
    ['url'] is user-configured and could point anywhere, so a response
    body is never assumed to be a bounded size just because a
    legitimate Plex server's always would be. Raises
    requests.RequestException (same as every other failure mode already
    handled at each call site) rather than the ValueError
    read_response_capped itself raises, so no call site needs its own
    except clause changed.

    Records curatarr_api_requests_total/curatarr_api_request_duration_seconds
    (see utils/metrics.py, service='plex') - this and _capped_put below
    are this module's own central choke point for raw Plex HTTP calls
    (every call site in this file goes through one or the other); calls
    plexapi itself makes internally via its own HTTP session (e.g.
    PlexServer()/MyPlexAccount() construction, library.search()) aren't
    separately wrapped, so this is a useful floor on Plex API traffic,
    not a complete count of every request plexapi issues."""
    start = time.time()
    outcome = "error"
    try:
        kwargs.setdefault("stream", True)
        response = requests.get(url, **kwargs)
        try:
            read_response_capped(response)
        except ValueError as e:
            raise requests.RequestException(f"Plex response rejected: {e}") from e
        outcome = "success"
        return response
    finally:
        record_api_call("plex", outcome, time.time() - start)


def _capped_put(url, **kwargs):
    """See _capped_get's docstring - identical reasoning, for PUT."""
    start = time.time()
    outcome = "error"
    try:
        kwargs.setdefault("stream", True)
        response = requests.put(url, **kwargs)
        try:
            read_response_capped(response)
        except ValueError as e:
            raise requests.RequestException(f"Plex response rejected: {e}") from e
        outcome = "success"
        return response
    finally:
        record_api_call("plex", outcome, time.time() - start)


def init_plex(config: dict) -> plexapi.server.PlexServer:
    """
    Initialize connection to Plex server.

    Args:
        config: Configuration dictionary with plex.url and plex.token

    Returns:
        PlexServer instance
    """
    try:
        # Create session with SSL verification settings
        session = requests.Session()
        session.verify = _resolve_verify_ssl(config)

        return plexapi.server.PlexServer(config["plex"]["url"], config["plex"]["token"], session=session)
    except (requests.RequestException, plexapi.exceptions.PlexApiException) as e:
        log_error(f"Error connecting to Plex server: {e}")
        raise


def get_plex_account_ids(config: Dict, users_to_match: List[str]) -> List[str]:
    """
    Get Plex account IDs for configured users with flexible name matching.

    Args:
        config: Configuration dict with plex URL and token
        users_to_match: List of usernames to find account IDs for

    Returns:
        List of account ID strings
    """
    account_ids = []
    try:
        response = _capped_get(
            f"{config['plex']['url']}/accounts",
            headers={"X-Plex-Token": config["plex"]["token"]},
            verify=_resolve_verify_ssl(config),
            timeout=PLEX_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)

        for username in users_to_match:
            account = None
            username_normalized = username.lower().replace(" ", "").replace("-", "").replace("_", "")

            # Try exact match first
            for acc in root.findall(".//Account"):
                plex_name = acc.get("name", "")
                if plex_name and plex_name.lower() == username.lower():
                    account = acc
                    break

            # Try normalized match
            if account is None:
                for acc in root.findall(".//Account"):
                    plex_name = acc.get("name", "")
                    if plex_name:
                        plex_normalized = plex_name.lower().replace(" ", "").replace("-", "").replace("_", "")
                        if username_normalized in plex_normalized or plex_normalized in username_normalized:
                            account = acc
                            break

            if account is not None:
                account_ids.append(str(account.get("id")))
            else:
                log_error(f"User '{username}' not found in Plex accounts!")

    except (requests.RequestException, ET.ParseError) as e:
        log_error(f"Error getting Plex account IDs: {e}")

    return account_ids


def _resolve_myplex_account_ids(config: Dict, users_to_check: List[str]) -> List[int]:
    """
    Resolve Plex usernames to MyPlex account IDs.

    Handles admin user aliases and case-insensitive matching.

    Args:
        config: Configuration dict with plex token
        users_to_check: List of usernames to resolve

    Returns:
        List of MyPlex account ID integers
    """
    account_ids = []
    account = MyPlexAccount(token=config["plex"]["token"])
    all_users = {u.title.lower(): u.id for u in account.users()}
    admin_username = account.username.lower()
    admin_account_id = account.id

    for username in users_to_check:
        username_lower = username.lower()
        if username_lower in ["admin", "administrator", admin_username]:
            account_ids.append(admin_account_id)
        elif username_lower in all_users:
            account_ids.append(all_users[username_lower])

    return account_ids


def get_watched_movie_count(config: Dict, users_to_check: List[str]) -> int:
    """
    Get count of unique watched movies from Plex (for cache invalidation).

    Args:
        config: Configuration dict with plex URL and token
        users_to_check: List of usernames to check watch history for

    Returns:
        Integer count of unique watched movies
    """
    try:
        if not users_to_check:
            return 0

        account_ids = _resolve_myplex_account_ids(config, users_to_check)

        watched_movies = set()
        for account_id in account_ids:
            url = f"{config['plex']['url']}/status/sessions/history/all?accountID={account_id}"
            response = _capped_get(
                url,
                headers={"X-Plex-Token": config["plex"]["token"]},
                verify=_resolve_verify_ssl(config),
                timeout=PLEX_REQUEST_TIMEOUT,
            )
            root = ET.fromstring(response.content)

            for video in root.findall(".//Video"):
                if video.get("type") == "movie":
                    rating_key = video.get("ratingKey")
                    if rating_key:
                        watched_movies.add(rating_key)

        return len(watched_movies)
    except (requests.RequestException, ET.ParseError, plexapi.exceptions.PlexApiException) as e:
        log_warning(f"Error getting watched movie count: {e}")
        return 0


def get_watched_show_count(config: Dict, users_to_check: List[str]) -> int:
    """
    Get count of unique watched TV shows from Plex (for cache invalidation).

    Args:
        config: Configuration dict with plex URL and token
        users_to_check: List of usernames to check watch history for

    Returns:
        Integer count of unique watched TV shows
    """
    try:
        if not users_to_check:
            return 0

        account_ids = _resolve_myplex_account_ids(config, users_to_check)

        watched_shows = set()
        for account_id in account_ids:
            url = f"{config['plex']['url']}/status/sessions/history/all?accountID={account_id}"
            response = _capped_get(
                url,
                headers={"X-Plex-Token": config["plex"]["token"]},
                verify=_resolve_verify_ssl(config),
                timeout=PLEX_REQUEST_TIMEOUT,
            )
            root = ET.fromstring(response.content)

            for video in root.findall(".//Video"):
                if video.get("type") == "episode":
                    show_key = video.get("grandparentRatingKey")
                    if show_key:
                        watched_shows.add(show_key)

        return len(watched_shows)
    except (requests.RequestException, ET.ParseError, plexapi.exceptions.PlexApiException) as e:
        log_warning(f"Error getting watched show count: {e}")
        return 0


def fetch_plex_watch_history_movies(
    config: Dict, account_ids: List[str], movies_section: Any
) -> Tuple[List[Any], Dict]:
    """
    Fetch movie watch history for specified account IDs using direct Plex API.

    Args:
        config: Configuration dict with plex URL and token
        account_ids: List of account ID strings
        movies_section: PlexAPI movies library section

    Returns:
        Tuple of (all_history_items, watched_movie_dates dict)
    """
    print("")
    print(f"{GREEN}Fetching Plex watch history for {len(account_ids)} user(s)...{RESET}")

    try:
        myPlex = MyPlexAccount(token=config["plex"]["token"])

        managed_users_map = {}
        for user in myPlex.users():
            user_id = str(user.id) if hasattr(user, "id") else None
            if user_id:
                managed_users_map[user_id] = user

        owner_id = "1"
        all_history_items = []
        watched_movie_dates: Dict[str, Any] = {}

        for i, account_id in enumerate(account_ids, 1):
            print(f"  [{i}/{len(account_ids)}] Fetching history for account ID {account_id}...", end="")

            try:
                if account_id in managed_users_map or account_id == owner_id:
                    base_url = config["plex"]["url"]
                    token = config["plex"]["token"]
                    library_key = movies_section.key

                    history_url = f"{base_url}/status/sessions/history/all"
                    params = {
                        "accountID": account_id,
                        "librarySectionID": library_key,
                        "sort": "viewedAt:desc",
                        "X-Plex-Container-Size": 10000,
                    }

                    response = _capped_get(
                        history_url,
                        params=params,
                        headers={"X-Plex-Token": token},
                        verify=_resolve_verify_ssl(config),
                        timeout=PLEX_REQUEST_TIMEOUT,
                    )
                    response.raise_for_status()

                    root = ET.fromstring(response.content)

                    for video in root.findall(".//Video"):

                        class HistoryItem:
                            def __init__(self, rating_key, viewed_at, user_rating=None):
                                self.ratingKey = rating_key
                                self.viewedAt = viewed_at
                                self.userRating = user_rating

                        rating_key = video.get("ratingKey")
                        viewed_at_ts = int(video.get("viewedAt", 0))
                        user_rating = float(video.get("userRating", 0)) if video.get("userRating") else None

                        if rating_key and viewed_at_ts:
                            item = HistoryItem(rating_key, datetime.fromtimestamp(viewed_at_ts), user_rating)
                            all_history_items.append(item)

                    print(f" {GREEN}OK{RESET}")
                else:
                    print(f" {YELLOW}SKIP (account not found in managed users){RESET}")

            except (requests.RequestException, ET.ParseError) as e:
                # Routed through the level-gated logging module (#306) -
                # this is the one choke point that was still a bare
                # print(), so logging.verbosity's off/quiet/verbose
                # setting didn't govern it like every other integration
                # (Plex connection init, TMDB, Trakt, Simkl, the shared
                # Sonarr/Radarr/Tautulli/MDBList client, and this same
                # module's fetch_plex_watch_history_shows/
                # fetch_show_completion_data siblings just below, which
                # already did this correctly). A failure to fetch one
                # user's watch history is exactly the kind of thing an
                # operator must see - the same class of silent failure
                # that hid a real six-month Trakt outage - so this stays
                # visible at the default 'quiet' level (log_error maps
                # to logging.ERROR, at or above every non-off tier).
                log_error(f"Error fetching watch history for account {account_id}: {e}")

        return all_history_items, watched_movie_dates

    except (requests.RequestException, plexapi.exceptions.PlexApiException) as e:
        log_error(f"Error fetching watch history: {e}")
        return [], {}


def fetch_plex_watch_history_shows(
    config: Dict, account_ids: List[str], tv_section: Any = None, return_timestamps: bool = False
) -> Union[Set[int], Tuple[Set[int], Dict[int, Any]]]:
    """
    Fetch TV show watch history for specified account IDs using direct Plex API.

    Args:
        config: Configuration dict with plex URL and token
        account_ids: List of account ID strings
        tv_section: PlexAPI TV library section
        return_timestamps: If True, returns (set, dict) where dict maps show_id -> latest viewedAt

    Returns:
        Set of watched show IDs (rating keys), or tuple (set, dict) if return_timestamps=True
    """
    print("")
    print(f"{GREEN}Fetching Plex watch history for {len(account_ids)} user(s)...{RESET}")

    watched_show_ids: Set[int] = set()
    show_timestamps: Dict[int, Any] = {}  # show_id -> latest viewedAt timestamp

    for account_id in account_ids:
        print("")
        print(f"{GREEN}Fetching Plex history for account ID: {account_id}{RESET}")

        url = f"{config['plex']['url']}/status/sessions/history/all"
        params = {
            "accountID": account_id,
            "librarySectionID": tv_section.key,
            "sort": "viewedAt:desc",
            "X-Plex-Container-Size": 5000,
        }

        try:
            response = _capped_get(
                url,
                params=params,
                headers={"X-Plex-Token": config["plex"]["token"]},
                verify=_resolve_verify_ssl(config),
                timeout=PLEX_REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            root = ET.fromstring(response.content)
            episode_count = 0

            for video in root.findall(".//Video"):
                if video.get("type") == "episode":
                    grandparent_key_path = video.get("grandparentKey")
                    if grandparent_key_path:
                        grandparent_key = grandparent_key_path.split("/")[-1]
                        show_id = int(grandparent_key)
                        watched_show_ids.add(show_id)
                        episode_count += 1

                        # Track latest viewedAt per show for recency decay
                        if return_timestamps:
                            viewed_at_str = video.get("viewedAt")
                            if viewed_at_str:
                                viewed_at = int(viewed_at_str)
                                if show_id not in show_timestamps or viewed_at > show_timestamps[show_id]:
                                    show_timestamps[show_id] = viewed_at

            print(f"Fetched {episode_count} watched episodes from {len(watched_show_ids)} shows")

        except (requests.RequestException, ET.ParseError) as e:
            log_error(f"Error fetching Plex history: {e}")
            continue

    if return_timestamps:
        return watched_show_ids, show_timestamps
    return watched_show_ids


def fetch_show_completion_data(config: Dict, account_ids: List[str], tv_section: Any) -> Dict[int, Dict]:
    """
    Fetch detailed watch completion data for TV shows.

    Used to detect dropped shows - shows that were started but abandoned.

    Args:
        config: Configuration dict with plex URL and token
        account_ids: List of account ID strings
        tv_section: PlexAPI TV library section

    Returns:
        Dict mapping show_id to completion data:
        {
            'total_episodes': int,
            'watched_episodes': int,
            'completion_percent': float,
            'last_watched': int (timestamp),
        }
    """
    show_data: Dict[int, Any] = {}
    show_episodes: Dict[int, Set[Any]] = {}  # show_id -> set of episode rating keys
    show_last_watched: Dict[int, Any] = {}  # show_id -> most recent viewedAt

    # Fetch watched episode data from history
    for account_id in account_ids:
        url = f"{config['plex']['url']}/status/sessions/history/all"
        params = {
            "accountID": account_id,
            "librarySectionID": tv_section.key,
            "sort": "viewedAt:desc",
            "X-Plex-Container-Size": 10000,
        }

        try:
            # This history/all page can return up to 10000 items
            # (X-Plex-Container-Size above) - larger than a typical Plex
            # call, so it gets the longer of the two timeouts.
            response = _capped_get(
                url,
                params=params,
                headers={"X-Plex-Token": config["plex"]["token"]},
                verify=_resolve_verify_ssl(config),
                timeout=PLEX_LONG_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)

            for video in root.findall(".//Video"):
                if video.get("type") == "episode":
                    grandparent_key_path = video.get("grandparentKey")
                    if grandparent_key_path:
                        show_id = int(grandparent_key_path.split("/")[-1])
                        episode_key = video.get("ratingKey")
                        viewed_at = int(video.get("viewedAt", 0))

                        if show_id not in show_episodes:
                            show_episodes[show_id] = set()
                            show_last_watched[show_id] = 0

                        show_episodes[show_id].add(episode_key)
                        show_last_watched[show_id] = max(show_last_watched[show_id], viewed_at)

        except (requests.RequestException, ET.ParseError) as e:
            log_warning(f"Error fetching show completion data for account {account_id}: {e}")
            continue

    # Get total episode counts from library
    for show in tv_section.all():
        show_id = int(show.ratingKey)
        if show_id in show_episodes:
            try:
                total_episodes = len(show.episodes())
                watched_count = len(show_episodes[show_id])
                completion = (watched_count / total_episodes * 100) if total_episodes > 0 else 0

                show_data[show_id] = {
                    "total_episodes": total_episodes,
                    "watched_episodes": watched_count,
                    "completion_percent": completion,
                    "last_watched": show_last_watched[show_id],
                    "title": show.title,
                }
            except plexapi.exceptions.PlexApiException as e:
                logger.debug(f"Error processing show completion for {show.title}: {e}")
                continue

    return show_data


def identify_dropped_shows(show_data: Dict[int, Dict], config: Dict) -> Set[int]:
    """
    Identify shows that were started but dropped.

    A show is considered "dropped" if:
    - User watched at least min_episodes_watched episodes (gave it a chance)
    - Completion is below max_completion_percent
    - Show has more episodes than min threshold

    Args:
        show_data: Output from fetch_show_completion_data()
        config: Configuration with negative_signals.dropped_shows settings

    Returns:
        Set of show IDs that are considered "dropped"
    """
    ns_config = config.get("negative_signals", {})
    dropped_config = ns_config.get("dropped_shows", {})

    if not ns_config.get("enabled", True) or not dropped_config.get("enabled", True):
        return set()

    min_episodes = dropped_config.get("min_episodes_watched", 2)
    max_completion = dropped_config.get("max_completion_percent", 25)

    dropped = set()

    for show_id, data in show_data.items():
        watched = data["watched_episodes"]
        completion = data["completion_percent"]
        total = data["total_episodes"]

        # Must have watched enough to "give it a chance"
        if watched < min_episodes:
            continue

        # Only consider shows with enough episodes to meaningfully drop
        if total <= min_episodes:
            continue

        # Consider dropped if low completion
        if completion < max_completion:
            dropped.add(show_id)

    return dropped


def fetch_watch_history_with_tmdb(
    plex: Any, config: Dict, account_ids: List[str], section: Any, media_type: str = "movie"
) -> List[Dict]:
    """
    Fetch watch history with TMDB IDs for external recommendations.

    Args:
        plex: PlexServer instance
        config: Configuration dict
        account_ids: List of account ID strings
        section: PlexAPI library section
        media_type: 'movie' or 'show'

    Returns:
        List of dicts: [{'tmdb_id': int, 'title': str, 'year': int}, ...]
    """
    watched_items = []
    # Holds both str(rating_key) forms and int tmdb_id forms - two
    # independent dedup checks (below) intentionally share one set.
    seen_tmdb_ids: Set[Union[str, int]] = set()

    for account_id in account_ids:
        url = f"{config['plex']['url']}/status/sessions/history/all"
        params = {"accountID": account_id, "librarySectionID": section.key, "sort": "viewedAt:desc"}

        try:
            response = _capped_get(
                url,
                params=params,
                headers={"X-Plex-Token": config["plex"]["token"]},
                verify=_resolve_verify_ssl(config),
                timeout=PLEX_REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                continue

            root = ET.fromstring(response.content)

            for video in root.findall(".//Video"):
                video_type = video.get("type")

                if (media_type == "movie" and video_type == "movie") or (
                    media_type == "show" and video_type == "episode"
                ):
                    rating_key = video.get("ratingKey")
                    if media_type == "show":
                        grandparent_key_path = video.get("grandparentKey")
                        if grandparent_key_path:
                            rating_key = grandparent_key_path.split("/")[-1]
                        else:
                            rating_key = None

                    if rating_key and str(rating_key) not in seen_tmdb_ids:
                        try:
                            item = plex.fetchItem(int(rating_key))

                            tmdb_id = None
                            for guid in item.guids:
                                if "tmdb://" in guid.id:
                                    tmdb_id = int(guid.id.split("tmdb://")[1])
                                    break

                            if tmdb_id and tmdb_id not in seen_tmdb_ids:
                                watched_items.append(
                                    {
                                        "tmdb_id": tmdb_id,
                                        "title": item.title,
                                        "year": item.year if hasattr(item, "year") else None,
                                    }
                                )
                                seen_tmdb_ids.add(str(rating_key))
                                seen_tmdb_ids.add(tmdb_id)
                        except (ValueError, KeyError, AttributeError) as e:
                            logger.debug(f"Error extracting TMDB ID for rating key {rating_key}: {e}")

        except (requests.RequestException, ET.ParseError) as e:
            logger.debug(f"Error fetching watch history for account {account_id}: {e}")
            continue

    return watched_items


def _find_stale_owned_collections(all_collections: List[Any], collection_name: str, private_label: str) -> List[Any]:
    """Every OTHER collection in this library carrying private_label whose
    title isn't already collection_name - i.e. every rename_on_template_
    change candidate this run's freshly-rendered collection_name could
    apply to (see update_plex_collection's own docstring).

    Trusts ONLY the PrivateCollection_<user> label already applied to the
    collection object by this same function on every prior run (never
    title pattern-matching) - that label is applied unconditionally
    whenever private_label is passed in, independent of
    collections.private_collections (that config key only gates the
    cross-user Plex exclude-filter push in
    utils.plex_policy.apply_user_label_restrictions, never whether the
    label itself is set on the collection).

    Steady state is exactly zero (nothing stale) or exactly one (last
    run's rendered name, about to be renamed). More than one means
    ownership is ambiguous - see the caller for how that's handled.
    """
    return [
        c for c in all_collections if c.title != collection_name and private_label in [label.tag for label in c.labels]
    ]


def remove_owned_collection(section: Any, private_label: str, username: str, reason: str, logger: Any = None) -> bool:
    """Remove a user's own curatarr-created collection (#291
    recommend_for_no_history: false path) - fires ONLY when a user has
    zero watch history AND that config is explicitly disabled, never on
    the default (create) path. See BaseRecommender._remove_collection_
    for_no_history, the only caller.

    Ownership is confirmed ONLY via the PrivateCollection_<user> label
    already on the collection - the exact same marker
    _find_stale_owned_collections/update_plex_collection's own
    rename-on-template-change path trusts (#274) - never inferred from
    title, emoji, or name pattern. A false positive here would delete a
    real, hand-curated collection instead of one curatarr created, so
    every branch below either removes a single unambiguously-owned
    collection or leaves everything alone and logs why.

    That label is applied by manage_plex_labels()/update_plex_collection
    unconditionally whenever collections.add_label is enabled,
    independent of whether collections.private_collections is itself
    enabled (private_collections only gates the cross-user Plex
    exclude-filter push in utils.plex_policy.apply_user_label_
    restrictions - see update_plex_collection's own docstring) - so this
    removal path can confirm ownership the same way regardless of that
    setting.

    Args:
        section: PlexAPI library section (movies or shows)
        private_label: This user's PrivateCollection_<user> label
        username: Real username, for the log message only
        reason: Human-readable reason this run wants the collection gone
            (e.g. "no watch history and movies.recommend_for_no_history
            is disabled"), for the log message only
        logger: Optional logger instance (falls back to print, matching
            every other function in this module)

    Returns:
        True if a collection was found and removed, False otherwise -
        nothing found, ownership ambiguous, or the delete call itself
        failed are all logged, never silent.
    """
    try:
        all_collections = list(section.collections())
    except plexapi.exceptions.PlexApiException as e:
        msg = f"Could not list Plex collections to check for one to remove for {username}: {e}"
        if logger:
            logger.warning(msg)
        else:
            print(f"WARNING: {msg}")
        return False

    owned = [c for c in all_collections if private_label in [label.tag for label in c.labels]]

    if not owned:
        # Nothing carries this user's label - either they never had a
        # collection, or add_label/private-label application was off
        # when it was created. Nothing safe to remove either way.
        return False

    if len(owned) > 1:
        titles = [c.title for c in owned]
        msg = (
            f"{username} has no watch history ({reason}), but {len(owned)} collections carry "
            f"label {private_label!r} ({titles}) - ownership is ambiguous, leaving all of them "
            "alone rather than guessing which one to remove"
        )
        if logger:
            logger.warning(msg)
        else:
            print(f"WARNING: {msg}")
        return False

    collection = owned[0]
    title = collection.title
    try:
        collection.delete()
    except plexapi.exceptions.PlexApiException as e:
        msg = f"Could not remove collection {title!r} for {username} ({reason}): {e}"
        if logger:
            logger.warning(msg)
        else:
            print(f"WARNING: {msg}")
        return False

    msg = f"Removed collection {title!r} for {username}: {reason} (ownership confirmed via label {private_label!r})"
    if logger:
        logger.warning(msg)
    else:
        print(f"WARNING: {msg}")
    return True


def update_plex_collection(
    section: Any,
    collection_name: str,
    items: List[Any],
    logger: Any = None,
    label_name: Optional[str] = None,
    private_label: Optional[str] = None,
    rename_on_template_change: bool = True,
) -> bool:
    """
    Create or update a Plex collection with items in the specified order.

    Args:
        section: PlexAPI library section (movies or shows)
        collection_name: Name of the collection to create/update
        items: List of Plex media items in desired order (best first)
        logger: Optional logger instance
        label_name: Currently unused here beyond being an on/off switch for
            adding a collection-level label at all - kept as a separate
            parameter (rather than folded into private_label) so a caller
            that wants item labeling without collection labeling still has
            a way to say so.
        private_label: The COLLECTION-level label to add (e.g.
            "PrivateCollection_alice"), fully computed by the caller (see
            recommenders/base.py's manage_plex_labels) via the same
            build_label_name() call it uses for the item-level label -
            not derived here by string-rewriting label_name. Prior to
            #261 this was computed as
            label_name.replace("Recommended_", "PrivateCollection_"),
            which silently produced the wrong (unprefixed) label whenever
            label_name wasn't literally "Recommended_<user>" - e.g. every
            install running with collections.append_usernames: false.
        rename_on_template_change: When True (default -
            collections.rename_on_template_change in config/tuning.yml)
            and private_label is given, a collections.movie_name_template/
            tv_name_template (#267) edit that changes this run's rendered
            collection_name RENAMES the previous run's collection (found
            via private_label) instead of leaving it orphaned while a
            second, identically-managed collection gets created under the
            new name - renaming preserves the collection's poster, sort
            title, added-at date and any manual curation, all of which a
            delete-and-recreate would lose. Only ever fires when EXACTLY
            one other collection carries private_label; see
            _find_stale_owned_collections and the "both old- and
            new-named collections already exist" branch below for the
            conservative, no-guessing edge-case handling.

    Returns:
        True if successful, False otherwise
    """
    if not items:
        if logger:
            logger.warning(f"No items provided for collection: {collection_name}")
        return False

    try:
        existing_collection = None
        all_collections = list(section.collections())
        for collection in all_collections:
            if collection.title == collection_name:
                existing_collection = collection
                break

        stale_owned_collections: List[Any] = []
        if rename_on_template_change and private_label:
            stale_owned_collections = _find_stale_owned_collections(all_collections, collection_name, private_label)

        target_collection = None

        if existing_collection and stale_owned_collections:
            # Both the (already correct) new-named collection and at least
            # one stale, still-labeled old-named one exist - renaming would
            # create a second collection with the identical title. Leave
            # both alone; this run just updates existing_collection as
            # normal below. (Can happen if a prior run created the
            # new-named collection before this feature existed, or a rename
            # attempt failed previously.)
            stale_titles = [c.title for c in stale_owned_collections]
            msg = (
                f"Collection {collection_name!r} already exists alongside "
                f"{len(stale_owned_collections)} other collection(s) carrying "
                f"label {private_label!r} ({stale_titles}) - not renaming (would "
                f"produce a duplicate title); leaving all as-is"
            )
            if logger:
                logger.warning(msg)
            else:
                print(f"WARNING: {msg}")
        elif not existing_collection and len(stale_owned_collections) == 1:
            stale_collection = stale_owned_collections[0]
            old_title = stale_collection.title
            try:
                stale_collection.editTitle(collection_name)
                target_collection = stale_collection
                msg = f"Renamed collection {old_title!r} -> {collection_name!r} (template change)"
                if logger:
                    logger.info(msg)
                else:
                    print(msg)
            except plexapi.exceptions.PlexApiException as e:
                # Rename failed - fall through to the normal create path
                # below rather than losing this run's sync entirely.
                if logger:
                    logger.warning(f"Could not rename collection {old_title!r} to {collection_name!r}: {e}")
                else:
                    print(f"WARNING: Could not rename collection {old_title!r} to {collection_name!r}: {e}")
                target_collection = None
        elif not existing_collection and len(stale_owned_collections) > 1:
            # Ambiguous ownership (e.g. a leftover duplicate) - never guess
            # which one to rename. Leave every candidate alone; a new
            # collection gets created below like before this feature existed.
            stale_titles = [c.title for c in stale_owned_collections]
            msg = (
                f"Found {len(stale_owned_collections)} collections carrying label "
                f"{private_label!r} with a title other than {collection_name!r} "
                f"({stale_titles}) - ownership is ambiguous, skipping rename"
            )
            if logger:
                logger.warning(msg)
            else:
                print(f"WARNING: {msg}")

        # A successful rename (if any) takes precedence; otherwise fall
        # back to the exact-title match found above.
        target_collection = target_collection or existing_collection

        if target_collection:
            current_items = target_collection.items()
            if current_items:
                target_collection.removeItems(current_items)
            target_collection.addItems(items)
            if logger:
                logger.info(f"Updated collection: {collection_name} ({len(items)} items)")
            else:
                print(f"Updated collection: {collection_name} ({len(items)} items)")
        else:
            target_collection = section.createCollection(title=collection_name, items=items)
            if logger:
                logger.info(f"Created collection: {collection_name} ({len(items)} items)")
            else:
                print(f"Created collection: {collection_name} ({len(items)} items)")

        # Set custom sort order and reorder items to match our ranking
        if target_collection and len(items) > 1:
            try:
                target_collection.sortUpdate(sort="custom")
                # Move items in REVERSE order, each to the beginning
                # This results in first item ending up at position 1
                for item in reversed(items):
                    target_collection.moveItem(item, after=None)
            except plexapi.exceptions.PlexApiException as e:
                # Log but don't fail if reordering doesn't work
                if logger:
                    logger.warning(f"Could not set custom order: {e}")

        # Add private label to collection itself for per-user label
        # restrictions (utils.plex_policy.apply_user_label_restrictions).
        # Uses a DIFFERENT namespace than item labels so exclusions only ever
        # affect collections, never the items shared in everyone's normal
        # library view - items keep Recommended_* labels (visible to all),
        # collections get PrivateCollection_* (see private_label's docstring
        # above for why this is passed in fully-built, not derived here).
        # NOT an access-control boundary - see apply_user_label_restrictions's
        # own docstring for the enumeration caveat (Plex enforces this
        # exclusion on the collection object, not on the items inside it).
        if target_collection and label_name and private_label:
            try:
                current_labels = [label.tag for label in target_collection.labels]
                if private_label not in current_labels:
                    target_collection.addLabel(private_label)
            except plexapi.exceptions.PlexApiException as e:
                if logger:
                    logger.warning(f"Could not add label to collection: {e}")

        return True

    except plexapi.exceptions.PlexApiException as e:
        error_msg = f"Error updating collection {collection_name}: {e}"
        if logger:
            logger.error(error_msg)
        else:
            print(f"ERROR: {error_msg}")
        return False


def cleanup_old_collections(
    section: Any, current_collection_name: str, username: str, emoji: str, logger: Any = None
) -> None:
    """
    Delete old collection patterns for a user that don't match current naming.

    Args:
        section: PlexAPI library section
        current_collection_name: The current/correct collection name
        username: The username to check for old patterns
        emoji: The emoji prefix
        logger: Optional logger instance
    """
    old_patterns = [
        f"{emoji} {username} - Recommendation",
        f"{emoji} {username.capitalize()} - Recommendation",
        f"{emoji} {username.title()} - Recommendation",
        f"# {username}'s - Recommended",
        f"# {username.capitalize()}'s - Recommended",
        f"{username}'s - Recommended",
        f"{username.capitalize()}'s - Recommended",
        f"{username} - Recommendation",
        f"{username.capitalize()} - Recommendation",
    ]

    try:
        for collection in section.collections():
            if collection.title == current_collection_name:
                continue

            matches_pattern = collection.title in old_patterns
            contains_username = username.lower() in collection.title.lower() and "Recommend" in collection.title

            if matches_pattern or contains_username:
                collection.delete()
                msg = f"Deleted old collection: {collection.title}"
                if logger:
                    logger.info(msg)
                else:
                    print(msg)

    except plexapi.exceptions.PlexApiException as e:
        error_msg = f"Error cleaning up old collections: {e}"
        if logger:
            logger.warning(error_msg)
        else:
            print(f"WARNING: {error_msg}")


# Exact collection title every install produced while running under the
# collections.append_usernames: false code default (fixed in #261 - the
# shipped config/tuning.example.yml always documented true, but nothing
# in any install path ever wrote a real tuning.yml, so every fresh
# install got the code default instead). build_label_name() never got a
# username to append, so every user's collection was created under this
# one literal, identical name - and update_plex_collection's old
# label_name.replace("Recommended_", "PrivateCollection_") was a no-op
# on the bare "Recommended" label that produced, so the collection's own
# filter label and every item's label were both also just "Recommended".
_LEGACY_SHARED_COLLECTION_LABEL = "Recommended"


def cleanup_legacy_unnamed_collection(
    section: Any, current_collection_name: str, emoji: str, logger: Any = None
) -> None:
    """
    One-time migration cleanup for the #261 append_usernames default bug.

    cleanup_old_collections() above can't find this collection - it only
    ever matches patterns built from a REAL username, and this legacy
    collection's title contains none (see _LEGACY_SHARED_COLLECTION_LABEL's
    comment). Left alone, it stays in Plex forever as an orphaned,
    never-updated collection, and its items keep a stale "Recommended"
    label that no current run ever looks at.

    Idempotent and safe to call every run: does nothing once the legacy
    collection is gone. Skips deleting *current_collection_name* itself
    (mirroring cleanup_old_collections' own guard) so a real Plex user
    literally named "Recommended" - who would legitimately produce this
    exact title today - never has their live collection treated as
    legacy junk.

    Args:
        section: PlexAPI library section
        current_collection_name: This run's correct collection name - never deleted
        emoji: The emoji prefix (differs between movies/TV)
        logger: Optional logger instance
    """
    legacy_title = f"{emoji} Recommended - Recommendation"
    if legacy_title == current_collection_name:
        return

    try:
        for collection in section.collections():
            if collection.title != legacy_title:
                continue

            try:
                legacy_items = collection.items()
            except plexapi.exceptions.PlexApiException:
                legacy_items = []

            if legacy_items:
                remove_labels_from_items(
                    legacy_items,
                    _LEGACY_SHARED_COLLECTION_LABEL,
                    {},
                    "legacy collections.append_usernames=false cleanup (#261)",
                )

            collection.delete()
            msg = f"Deleted legacy shared collection from #261 migration: {legacy_title}"
            if logger:
                logger.info(msg)
            else:
                print(msg)

    except plexapi.exceptions.PlexApiException as e:
        error_msg = f"Error cleaning up legacy shared collection: {e}"
        if logger:
            logger.warning(error_msg)
        else:
            print(f"WARNING: {error_msg}")


def get_configured_users(config: dict) -> dict:
    """
    Get and validate configured Plex users.

    Args:
        config: Configuration dictionary

    Returns:
        Dictionary with 'managed_users', 'plex_users', and 'admin_user'
    """
    raw_managed = config["plex"].get("managed_users", "")
    managed_users = [u.strip() for u in raw_managed.split(",") if u.strip()]

    plex_users = []
    # Check multiple possible config locations for user list
    plex_user_config = (
        config.get("plex_users", {}).get("users") or config.get("users", {}).get("list")  # New config format
    )
    if plex_user_config and str(plex_user_config).lower() != "none":
        if isinstance(plex_user_config, list):
            plex_users = plex_user_config
        elif isinstance(plex_user_config, str):
            plex_users = [u.strip() for u in plex_user_config.split(",") if u.strip()]

    account = MyPlexAccount(token=config["plex"]["token"])
    admin_user = account.username

    all_users = account.users()
    all_usernames_lower = {u.title.lower(): u.title for u in all_users}

    processed_managed = []
    for user in managed_users:
        user_lower = user.lower()
        if user_lower in ["admin", "administrator"]:
            processed_managed.append(admin_user)
        elif user_lower == admin_user.lower():
            processed_managed.append(admin_user)
        elif user_lower in all_usernames_lower:
            processed_managed.append(all_usernames_lower[user_lower])
        else:
            log_error(f"Error: Managed user '{user}' not found")
            raise ValueError(f"User '{user}' not found in Plex account")

    # Dedup while preserving first-occurrence order. Written as an
    # explicit loop (rather than the `not (u in seen or seen.add(u))`
    # one-liner) so the set.add() call isn't used for its return value -
    # same result, just without relying on set.add() always being None.
    seen: set = set()
    managed_users = []
    for u in processed_managed:
        if u not in seen:
            seen.add(u)
            managed_users.append(u)

    return {"managed_users": managed_users, "plex_users": plex_users, "admin_user": admin_user}


def fetch_plex_users(config: Dict) -> List[Dict[str, Any]]:
    """
    #266: fetch real Plex account users (server owner + every Home/
    managed user and shared friend) for the web UI's "Fetch from Plex"
    convenience on the Users screen (web/config_users.py) - lets an
    admin pick real usernames instead of typing them by hand and
    risking a mismatch (curatarr silently not matching a misspelled
    name is exactly what get_configured_users above already has to
    guard against for managed_users).

    NOT used by the recommender run path itself, which still resolves
    users from config (users.list/managed_users) exactly as before -
    this only helps discover what to put there.

    Returns a list of {"username", "title", "is_admin"} dicts, admin
    first, then every other Plex account user by title. "username" is
    what get_configured_users/managed_users matching above actually
    compares against - falls back to title for a Home user with no
    linked Plex account/email, where plexapi's own username is blank.

    Raises requests.RequestException/plexapi.exceptions.PlexApiException
    on any connection/auth failure - callers (the web route) are
    responsible for catching and turning that into a UI-friendly
    message, same convention as init_plex/get_configured_users above.
    """
    account = MyPlexAccount(token=config["plex"]["token"])
    admin_username = account.username
    users = [{"username": admin_username, "title": admin_username, "is_admin": True}]
    for u in account.users():
        username = u.username or u.title
        users.append({"username": username, "title": u.title, "is_admin": False})
    return users


def fetch_plex_libraries(config: Dict) -> List[Dict[str, str]]:
    """
    #266: fetch real Plex library sections for the web UI's "Fetch from
    Plex" convenience on the Libraries screen (web/config_libraries.py) -
    same rationale as fetch_plex_users above, and likewise never used by
    the recommender run path itself.

    Returns a list of {"section", "media_type"} dicts - section is the
    Plex library's own display title (what utils.config.get_libraries's
    'section:' field and Plex's own library.section() lookup both match
    against), media_type is curatarr's "movie"/"tv" vocabulary (Plex's
    own section.type of "movie"/"show" respectively). Library types
    curatarr doesn't manage (music, photo, etc.) are silently skipped -
    there's nothing meaningful to configure for them here.

    Raises requests.RequestException/plexapi.exceptions.PlexApiException
    on any connection failure - same convention as init_plex above.
    """
    server = init_plex(config)
    libraries = []
    for section in server.library.sections():
        if section.type == "movie":
            media_type = "movie"
        elif section.type == "show":
            media_type = "tv"
        else:
            continue
        libraries.append({"section": section.title, "media_type": media_type})
    return libraries


def get_current_users(users: dict) -> str:
    """
    Get formatted string of current users being processed.

    Args:
        users: Dictionary with 'plex_users' and 'managed_users'

    Returns:
        Formatted string describing current users
    """
    if users["plex_users"]:
        return f"Plex users: {', '.join(users['plex_users'])}"
    return f"Managed users: {', '.join(users['managed_users'])}"


def get_excluded_genres_for_user(
    exclude_genres: Iterable[str], user_preferences: dict, username: Optional[str] = None
) -> set:
    """
    Get excluded genres including user-specific preferences.

    Args:
        exclude_genres: Global set of excluded genres
        user_preferences: User preferences dictionary
        username: Username to get excluded genres for

    Returns:
        Set of excluded genre names (lowercase)
    """
    excluded = set(exclude_genres)

    if username and user_preferences and username in user_preferences:
        user_prefs = user_preferences[username]
        user_excluded = user_prefs.get("exclude_genres", [])
        excluded.update([g.lower() for g in user_excluded])

    return excluded


def get_streaming_services_for_user(
    streaming_services: List[str], user_preferences: dict, username: Optional[str] = None
) -> List[str]:
    """
    Get streaming services including user-specific preferences.

    Mirrors get_excluded_genres_for_user() above: a per-user
    users.preferences.<user>.streaming_services list is UNIONed onto
    (never replaces) the global top-level streaming_services list, not
    overridden outright - both are list-type preferences with a global
    counterpart (unlike max_rating, which has no global equivalent to
    override), and this codebase's one other example of that shape
    (exclude_genres) merges. A user with a personal streaming_services
    override still benefits from services the whole household/global
    config lists, rather than losing them.

    Args:
        streaming_services: Global list of streaming service ids
        user_preferences: User preferences dictionary
        username: Username to get streaming services for

    Returns:
        List of streaming service ids (global + user-specific, de-duplicated,
        original order preserved)
    """
    services = list(streaming_services)

    if username and user_preferences and username in user_preferences:
        user_prefs = user_preferences[username]
        user_services = user_prefs.get("streaming_services", [])
        for service in user_services:
            if service not in services:
                services.append(service)

    return services


def get_user_specific_connection(plex: Any, config: Dict, users: Dict) -> Any:
    """
    Get Plex connection for specific user context.

    Args:
        plex: PlexServer instance
        config: Configuration dictionary
        users: Users dictionary from get_configured_users()

    Returns:
        PlexServer instance (possibly switched to managed user)
    """
    if users["plex_users"]:
        return plex
    try:
        account = MyPlexAccount(token=config["plex"]["token"])
        user = account.user(users["managed_users"][0])
        return plex.switchUser(user)
    except plexapi.exceptions.PlexApiException as e:
        log_warning(f"Could not switch to managed user context: {e}")
        return plex


def resolve_plex_user(account: Any, username: str) -> Optional[Any]:
    """
    Find the MyPlexUser matching a configured username.

    config/config.yml lists users by Plex USERNAME (e.g. "homehouse165"),
    while account.users() surfaces them by friendly TITLE (e.g. "home
    house") - matching on title alone silently misses every user whose
    display name differs from their login, which on a real Home is all of
    them. Username is tried first as the identifier the config actually
    holds, then email, then title.

    Returns None for the server owner (who is not in account.users() at
    all) and for anyone unmatched; callers treat that as "use the admin
    connection", which for the owner is exactly right.
    """
    if not username:
        return None
    wanted = username.strip().lower()
    try:
        users = list(account.users())
    except (plexapi.exceptions.PlexApiException, requests.RequestException, TypeError, AttributeError) as e:
        # Deliberately broad: this helper exists to answer an optional
        # question, and every caller's fallback is "use the admin
        # connection". Letting an unexpected shape from the Plex API
        # propagate would fail an entire recommendation run over it.
        log_warning(f"Could not list Plex users: {e}")
        return None

    for attr in ("username", "email", "title"):
        for user in users:
            value = getattr(user, attr, None)
            if value and str(value).strip().lower() == wanted:
                return user
    return None


# Per-user server tokens, cached across runs.
#
# switchUser() resolves a user's server token from plex.tv on every call.
# Reading per-user watched state for six users across the movie and TV
# recommenders therefore issued a dozen plex.tv token requests per run
# where there had previously been none. These tokens are stable for a
# given (server, user) pair, so re-fetching them every run is pure waste
# against a third-party API.
#
# Stored 0600 in the cache directory (already gitignored, and the same
# treatment the watched caches get) - they are lower-privilege than the
# admin token that already sits in config.yml in plaintext, but they are
# still credentials and are written no more loosely than it is.
# Deliberately file-only, with no in-process memo. A module-level dict
# would be keyed by (server, user) but NOT by cache location, so two
# configs pointing at different cache directories would share entries -
# which is exactly how it leaked between tests when first written. The
# file read it replaces is a few hundred bytes of local JSON against a
# plex.tv round trip, so there is nothing to win by holding it in memory.
_USER_TOKEN_CACHE_FILE = "plex_user_tokens.json"


def _user_token_cache_path(config: Dict) -> str:
    """
    Mirrors BaseRecommender.__init__'s resolution exactly:
    get_project_root() joined with config['cache_dir'] (a NAME, not an
    absolute path, defaulting to "cache").

    get_project_root is bound at module level on purpose. tests/conftest.py
    isolates the cache directory by patching each CONSUMING module's own
    binding rather than utils.helpers' - a lazily imported name would
    resolve past that and write into the real repo's cache/, which the
    suite has a hard session-level gate against for exactly this reason.
    """
    return os.path.join(get_project_root(), config.get("cache_dir", "cache"), _USER_TOKEN_CACHE_FILE)


def _load_user_token(config: Dict, key: str) -> Optional[str]:
    try:
        with open(_user_token_cache_path(config), "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(stored, dict):
        return None
    value = stored.get(key)
    return value if isinstance(value, str) else None


def _store_user_token(config: Dict, key: str, token: str) -> None:
    path = _user_token_cache_path(config)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        existing = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, dict):
                    existing = loaded
        except (OSError, ValueError):
            pass
        existing[key] = token
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(existing, fh)
        harden_file_permissions(path)
    except (OSError, ValueError, TypeError) as e:
        # A cache that cannot be written is a performance problem, never
        # a correctness one - the token was still obtained. ValueError
        # covers a malformed cache_dir (an embedded null byte makes
        # os.makedirs raise it, not OSError), which must not take down a
        # run over an optional optimization.
        logger.debug(f"Could not cache Plex user token: {e}")


def forget_user_token(config: Dict, key: str) -> None:
    """Drop a cached token that the server has stopped accepting."""
    path = _user_token_cache_path(config)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict) and stored.pop(key, None) is not None:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(stored, fh)
            harden_file_permissions(path)
    except (OSError, ValueError, TypeError):
        pass


def get_user_connection(plex: Any, config: Dict, username: Optional[str]) -> Any:
    """
    A PlexServer connection that sees the library as `username` does.

    Needed because per-user state - specifically `isPlayed` - is a
    property of the CONNECTION, not the item. The admin token reports the
    admin's watched state for every item, so a recommender that reuses one
    admin connection across a multi-user loop treats everything the ADMIN
    has watched as watched by everyone. Measured on a real server: of 143
    titles the admin had seen and a Home user had not, 141 reported
    isPlayed=True through the admin connection and 0 through that user's
    own - and that user consequently lost 45% of their candidate pool.

    READ-ONLY. A managed user's connection cannot write labels or
    collections, so callers must keep every write on the admin
    connection; this exists to answer "has THIS user played this", nothing
    more.

    Falls back to the admin connection (with a warning) if the user can't
    be resolved or switched - degrading to the previous behavior rather
    than failing a run.
    """
    if not username:
        return plex
    try:
        account = plex.myPlexAccount()
    except (plexapi.exceptions.PlexApiException, requests.RequestException, AttributeError) as e:
        log_warning(f"Could not reach Plex account to switch user context: {e}")
        return plex

    # The owner is not listed in account.users(); the admin connection
    # already IS their connection, so this is a correct no-op for them.
    if str(getattr(account, "username", "")).strip().lower() == username.strip().lower():
        return plex

    machine_id = getattr(plex, "machineIdentifier", "") or ""
    cache_key = f"{machine_id}:{username.strip().lower()}"

    # Reuse a previously resolved token rather than asking plex.tv again.
    cached = _load_user_token(config, cache_key)
    if cached:
        try:
            return plexapi.server.PlexServer(plex._baseurl, token=cached, session=plex._session)
        except (plexapi.exceptions.Unauthorized, plexapi.exceptions.PlexApiException) as e:
            # A cached token the server no longer accepts (revoked, user
            # removed and re-added) must not wedge this permanently -
            # drop it and resolve a fresh one below.
            logger.debug(f"Cached Plex token for '{username}' rejected ({e}) - refetching")
            forget_user_token(config, cache_key)
        except requests.RequestException as e:
            log_warning(f"Could not reach Plex with the cached token for '{username}': {e}")
            return plex

    user = resolve_plex_user(account, username)
    if user is None:
        log_warning(
            f"Could not resolve Plex user '{username}' - falling back to the admin "
            f"connection, whose watched state is the ADMIN's, not theirs."
        )
        return plex

    try:
        switched = plex.switchUser(user)
    except (plexapi.exceptions.PlexApiException, requests.RequestException) as e:
        log_warning(f"Could not switch to Plex user '{username}': {e} - using admin connection")
        return plex

    # Only a real string is cacheable - plexapi's private attribute is
    # not part of its public API, so treat anything else as "no token to
    # cache" rather than writing a value that cannot be serialized (or,
    # worse, round-tripped into a later connection attempt).
    token = getattr(switched, "_token", None)
    if machine_id and isinstance(token, str) and token:
        _store_user_token(config, cache_key, token)
    return switched


def fetch_user_played_ids(plex: Any, config: Dict, username: Optional[str], section_title: str) -> Set[int]:
    """
    Rating keys `username` has played, as THEY see the library.

    Returned as a set so callers can test membership instead of reading
    `item.isPlayed` off an admin-connection item - see
    get_user_connection() for why that read is wrong in a multi-user loop.

    An empty set is returned on any failure. That is the safe direction:
    an item wrongly believed unwatched is merely a redundant
    recommendation, whereas one wrongly believed watched is silently
    removed from consideration, which is the defect being fixed.
    """
    try:
        user_plex = get_user_connection(plex, config, username)
        section = user_plex.library.section(section_title)
        try:
            # Ask the server for watched items only - far cheaper than
            # pulling the section and filtering client-side.
            watched = section.search(unwatched=False)
        except (TypeError, plexapi.exceptions.PlexApiException):
            watched = [m for m in section.all() if getattr(m, "isPlayed", False)]
        return {int(m.ratingKey) for m in watched}
    except (
        plexapi.exceptions.PlexApiException,
        requests.RequestException,
        KeyError,
        TypeError,
        AttributeError,
        ValueError,
    ) as e:
        # See the docstring: an empty set degrades to "recommend it
        # again", which is recoverable. Raising would abort the run.
        log_warning(f"Could not read watched state for '{username}': {e} - proceeding without it")
        return set()


def find_plex_movie(movies_section: Any, title: str, year: Optional[int] = None) -> Optional[Any]:
    """
    Find a movie in Plex library with fuzzy title matching.

    Args:
        movies_section: Plex movies library section
        title: Movie title to search for
        year: Optional release year for additional filtering

    Returns:
        Plex movie object or None if not found
    """
    results = movies_section.search(title=title)
    if results:
        if year:
            match = next((m for m in results if m.year == year), None)
            if match:
                return match
        else:
            return results[0]

    normalized_search = normalize_title(title)
    all_movies = movies_section.all()

    for movie in all_movies:
        plex_normalized = normalize_title(movie.title)
        if plex_normalized.lower() == normalized_search.lower():
            if year is None or movie.year == year:
                return movie

    title_lower = title.lower()
    for movie in all_movies:
        movie_title_lower = movie.title.lower()
        if title_lower in movie_title_lower or movie_title_lower in title_lower:
            if year is None or movie.year == year:
                return movie

    return None


def extract_genres(item) -> List[str]:
    """
    Extract genres from a Plex media item (movie or show).

    Args:
        item: Plex media item with optional 'genres' attribute

    Returns:
        List of lowercase genre strings
    """
    genres: List[str] = []
    try:
        if not hasattr(item, "genres") or not item.genres:
            return genres

        for genre in item.genres:
            if hasattr(genre, "tag"):
                genres.append(genre.tag.lower())
            elif isinstance(genre, str):
                genres.append(genre.lower())
    except (AttributeError, TypeError) as e:
        logger.debug(f"Failed to extract genres: {e}")
    return genres


class GuidIds(TypedDict):
    """Return shape of extract_ids_from_guids() - a TypedDict (rather than
    a homogeneous Dict[str, ...]) so imdb_id and tmdb_id each keep their
    own real type at every call site, instead of both collapsing to a
    shared Optional[Union[str, int]] that callers would need to re-narrow."""

    imdb_id: Optional[str]
    tmdb_id: Optional[int]


def extract_ids_from_guids(item) -> GuidIds:
    """
    Extract IMDB and TMDB IDs from a Plex item's guids.

    Args:
        item: Plex media item with optional 'guids' attribute

    Returns:
        Dict with 'imdb_id' (str or None) and 'tmdb_id' (int or None) keys
    """
    result: GuidIds = {"imdb_id": None, "tmdb_id": None}

    if not hasattr(item, "guids"):
        return result

    for guid in item.guids:
        guid_id = guid.id if hasattr(guid, "id") else str(guid)
        if "imdb://" in guid_id:
            result["imdb_id"] = guid_id.replace("imdb://", "").split("?")[0]
        elif "themoviedb://" in guid_id or "tmdb://" in guid_id:
            try:
                tmdb_str = guid_id.split("themoviedb://")[-1].split("tmdb://")[-1].split("?")[0]
                result["tmdb_id"] = int(tmdb_str)
            except (ValueError, IndexError):
                pass

    return result


def extract_rating(item, prefer_user_rating: bool = True) -> float:
    """
    Extract rating from a Plex media item.

    Args:
        item: Plex media item (movie or show)
        prefer_user_rating: If True, prefer userRating over audienceRating

    Returns:
        Rating value (0-10 scale) or 0 if not found
    """
    try:
        if prefer_user_rating:
            if hasattr(item, "userRating") and item.userRating:
                return float(item.userRating)
            if hasattr(item, "audienceRating") and item.audienceRating:
                return float(item.audienceRating)
        else:
            if hasattr(item, "audienceRating") and item.audienceRating:
                return float(item.audienceRating)
            if hasattr(item, "userRating") and item.userRating:
                return float(item.userRating)

        if hasattr(item, "ratings"):
            for rating in item.ratings:
                if hasattr(rating, "value") and rating.value:
                    if (
                        getattr(rating, "image", "") == "imdb://image.rating"
                        or getattr(rating, "type", "") == "audience"
                    ):
                        try:
                            return float(rating.value)
                        except (ValueError, AttributeError):
                            pass
    except (AttributeError, TypeError) as e:
        logger.debug(f"Failed to extract rating: {e}")
    return 0.0


def get_library_imdb_ids(plex_section: Any) -> Set[str]:
    """
    Get set of all IMDb IDs in a Plex library section.

    Args:
        plex_section: Plex library section object

    Returns:
        Set of IMDb ID strings
    """
    try:
        items = plex_section.all()
    except (plexapi.exceptions.PlexApiException, TypeError) as e:
        log_warning(f"Error retrieving IMDb IDs from library: {e}")
        return set()
    return get_library_imdb_ids_from_items(items)


def get_library_imdb_ids_from_items(items: Any) -> Set[str]:
    """
    Extract IMDb IDs from an already-fetched list/iterable of Plex
    library items (e.g. a section.all() result a caller already holds).

    Shares the guid-parsing logic with get_library_imdb_ids() so a
    caller that's already fetched the full library once (see
    recommenders/base.py's _get_all_library_items(), #233 audit
    remediation batch D / PR1(a)) doesn't have to re-query Plex just to
    derive this set too.

    Args:
        items: Iterable of Plex media items (already fetched)

    Returns:
        Set of IMDb ID strings
    """
    imdb_ids = set()
    try:
        for item in items:
            if hasattr(item, "guids"):
                for guid in item.guids:
                    if guid.id.startswith("imdb://"):
                        imdb_ids.add(guid.id.replace("imdb://", ""))
                        break
    except (plexapi.exceptions.PlexApiException, TypeError) as e:
        log_warning(f"Error retrieving IMDb IDs from library: {e}")
    return imdb_ids


def get_plex_user_ids(plex, managed_users: List[str]) -> Dict[str, int]:
    """
    Get account IDs for managed Plex users.

    Args:
        plex: PlexServer instance
        managed_users: List of managed user names

    Returns:
        Dictionary mapping usernames to account IDs
    """
    user_ids = {}
    try:
        account = plex.myPlexAccount()
        for user in account.users():
            if user.title in managed_users:
                user_ids[user.title] = user.id
    except plexapi.exceptions.PlexApiException as e:
        log_warning(f"Error getting Plex user IDs: {e}")
    return user_ids
