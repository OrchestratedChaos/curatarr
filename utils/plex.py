"""
Plex-specific utilities for Curatarr.
Handles Plex server connections, watch history, collections, and user management.
"""

import logging
import requests
import urllib3
import xml.etree.ElementTree as ET
import plexapi.server
import plexapi.exceptions

# Suppress InsecureRequestWarning when users explicitly set verify_ssl=False for local Plex servers
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

# Module-level logger
logger = logging.getLogger('curatarr')

from plexapi.myplex import MyPlexAccount

from .display import GREEN, YELLOW, RED, RESET, log_warning, log_error
from .helpers import normalize_title


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
        session.verify = config['plex'].get('verify_ssl', True)

        return plexapi.server.PlexServer(
            config['plex']['url'],
            config['plex']['token'],
            session=session
        )
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
        response = requests.get(
            f"{config['plex']['url']}/accounts",
            headers={'X-Plex-Token': config['plex']['token']},
            verify=config['plex'].get('verify_ssl', True),
            timeout=30
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)

        for username in users_to_match:
            account = None
            username_normalized = username.lower().replace(' ', '').replace('-', '').replace('_', '')

            # Try exact match first
            for acc in root.findall('.//Account'):
                plex_name = acc.get('name', '')
                if plex_name and plex_name.lower() == username.lower():
                    account = acc
                    break

            # Try normalized match
            if account is None:
                for acc in root.findall('.//Account'):
                    plex_name = acc.get('name', '')
                    if plex_name:
                        plex_normalized = plex_name.lower().replace(' ', '').replace('-', '').replace('_', '')
                        if username_normalized in plex_normalized or plex_normalized in username_normalized:
                            account = acc
                            break

            if account is not None:
                account_ids.append(str(account.get('id')))
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
    account = MyPlexAccount(token=config['plex']['token'])
    all_users = {u.title.lower(): u.id for u in account.users()}
    admin_username = account.username.lower()
    admin_account_id = account.id

    for username in users_to_check:
        username_lower = username.lower()
        if username_lower in ['admin', 'administrator', admin_username]:
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
            url = f"{config['plex']['url']}/status/sessions/history/all?X-Plex-Token={config['plex']['token']}&accountID={account_id}"
            response = requests.get(url, verify=config['plex'].get('verify_ssl', True), timeout=30)
            root = ET.fromstring(response.content)

            for video in root.findall('.//Video'):
                if video.get('type') == 'movie':
                    rating_key = video.get('ratingKey')
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
            url = f"{config['plex']['url']}/status/sessions/history/all?X-Plex-Token={config['plex']['token']}&accountID={account_id}"
            response = requests.get(url, verify=config['plex'].get('verify_ssl', True), timeout=30)
            root = ET.fromstring(response.content)

            for video in root.findall('.//Video'):
                if video.get('type') == 'episode':
                    show_key = video.get('grandparentRatingKey')
                    if show_key:
                        watched_shows.add(show_key)

        return len(watched_shows)
    except (requests.RequestException, ET.ParseError, plexapi.exceptions.PlexApiException) as e:
        log_warning(f"Error getting watched show count: {e}")
        return 0


def fetch_plex_watch_history_movies(config: Dict, account_ids: List[str], movies_section: Any) -> Tuple[List[Any], Dict]:
    """
    Fetch movie watch history for specified account IDs using direct Plex API.

    Args:
        config: Configuration dict with plex URL and token
        account_ids: List of account ID strings
        movies_section: PlexAPI movies library section

    Returns:
        Tuple of (all_history_items, watched_movie_dates dict)
    """
    print(f"")
    print(f"{GREEN}Fetching Plex watch history for {len(account_ids)} user(s)...{RESET}")

    try:
        myPlex = MyPlexAccount(token=config['plex']['token'])

        managed_users_map = {}
        for user in myPlex.users():
            user_id = str(user.id) if hasattr(user, 'id') else None
            if user_id:
                managed_users_map[user_id] = user

        owner_id = '1'
        all_history_items = []
        watched_movie_dates = {}

        for i, account_id in enumerate(account_ids, 1):
            print(f"  [{i}/{len(account_ids)}] Fetching history for account ID {account_id}...", end='')

            try:
                if account_id in managed_users_map or account_id == owner_id:
                    base_url = config['plex']['url']
                    token = config['plex']['token']
                    library_key = movies_section.key

                    history_url = f"{base_url}/status/sessions/history/all"
                    params = {
                        'X-Plex-Token': token,
                        'accountID': account_id,
                        'librarySectionID': library_key,
                        'sort': 'viewedAt:desc',
                        'X-Plex-Container-Size': 10000
                    }

                    response = requests.get(history_url, params=params, verify=config['plex'].get('verify_ssl', True), timeout=30)
                    response.raise_for_status()

                    root = ET.fromstring(response.content)

                    for video in root.findall('.//Video'):
                        class HistoryItem:
                            def __init__(self, rating_key, viewed_at, user_rating=None):
                                self.ratingKey = rating_key
                                self.viewedAt = viewed_at
                                self.userRating = user_rating

                        rating_key = video.get('ratingKey')
                        viewed_at_ts = int(video.get('viewedAt', 0))
                        user_rating = float(video.get('userRating', 0)) if video.get('userRating') else None

                        if rating_key and viewed_at_ts:
                            item = HistoryItem(rating_key, datetime.fromtimestamp(viewed_at_ts), user_rating)
                            all_history_items.append(item)

                    print(f" {GREEN}OK{RESET}")
                else:
                    print(f" {YELLOW}SKIP (account not found in managed users){RESET}")

            except (requests.RequestException, ET.ParseError) as e:
                print(f" {RED}ERROR: {e}{RESET}")

        return all_history_items, watched_movie_dates

    except (requests.RequestException, plexapi.exceptions.PlexApiException) as e:
        log_error(f"Error fetching watch history: {e}")
        return [], {}


def fetch_plex_watch_history_shows(
    config: Dict,
    account_ids: List[str],
    tv_section: Any = None,
    return_timestamps: bool = False
) -> Set[int]:
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
    print(f"")
    print(f"{GREEN}Fetching Plex watch history for {len(account_ids)} user(s)...{RESET}")

    watched_show_ids = set()
    show_timestamps = {}  # show_id -> latest viewedAt timestamp

    for account_id in account_ids:
        print(f"")
        print(f"{GREEN}Fetching Plex history for account ID: {account_id}{RESET}")

        url = f"{config['plex']['url']}/status/sessions/history/all"
        params = {
            'X-Plex-Token': config['plex']['token'],
            'accountID': account_id,
            'librarySectionID': tv_section.key,
            'sort': 'viewedAt:desc',
            'X-Plex-Container-Size': 5000
        }

        try:
            response = requests.get(url, params=params, verify=config['plex'].get('verify_ssl', True), timeout=30)
            response.raise_for_status()

            root = ET.fromstring(response.content)
            episode_count = 0

            for video in root.findall('.//Video'):
                if video.get('type') == 'episode':
                    grandparent_key_path = video.get('grandparentKey')
                    if grandparent_key_path:
                        grandparent_key = grandparent_key_path.split('/')[-1]
                        show_id = int(grandparent_key)
                        watched_show_ids.add(show_id)
                        episode_count += 1

                        # Track latest viewedAt per show for recency decay
                        if return_timestamps:
                            viewed_at_str = video.get('viewedAt')
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


def fetch_show_completion_data(
    config: Dict,
    account_ids: List[str],
    tv_section: Any
) -> Dict[int, Dict]:
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
    show_data = {}
    show_episodes = {}  # show_id -> set of episode rating keys
    show_last_watched = {}  # show_id -> most recent viewedAt

    # Fetch watched episode data from history
    for account_id in account_ids:
        url = f"{config['plex']['url']}/status/sessions/history/all"
        params = {
            'X-Plex-Token': config['plex']['token'],
            'accountID': account_id,
            'librarySectionID': tv_section.key,
            'sort': 'viewedAt:desc',
            'X-Plex-Container-Size': 10000
        }

        try:
            response = requests.get(
                url, params=params,
                verify=config['plex'].get('verify_ssl', True),
                timeout=60
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)

            for video in root.findall('.//Video'):
                if video.get('type') == 'episode':
                    grandparent_key_path = video.get('grandparentKey')
                    if grandparent_key_path:
                        show_id = int(grandparent_key_path.split('/')[-1])
                        episode_key = video.get('ratingKey')
                        viewed_at = int(video.get('viewedAt', 0))

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
                    'total_episodes': total_episodes,
                    'watched_episodes': watched_count,
                    'completion_percent': completion,
                    'last_watched': show_last_watched[show_id],
                    'title': show.title
                }
            except plexapi.exceptions.PlexApiException as e:
                logger.debug(f"Error processing show completion for {show.title}: {e}")
                continue

    return show_data


def identify_dropped_shows(
    show_data: Dict[int, Dict],
    config: Dict
) -> Set[int]:
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
    ns_config = config.get('negative_signals', {})
    dropped_config = ns_config.get('dropped_shows', {})

    if not ns_config.get('enabled', True) or not dropped_config.get('enabled', True):
        return set()

    min_episodes = dropped_config.get('min_episodes_watched', 2)
    max_completion = dropped_config.get('max_completion_percent', 25)

    dropped = set()

    for show_id, data in show_data.items():
        watched = data['watched_episodes']
        completion = data['completion_percent']
        total = data['total_episodes']

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


def fetch_watch_history_with_tmdb(plex: Any, config: Dict, account_ids: List[str], section: Any, media_type: str = 'movie') -> List[Dict]:
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
    seen_tmdb_ids = set()

    for account_id in account_ids:
        url = f"{config['plex']['url']}/status/sessions/history/all"
        params = {
            'X-Plex-Token': config['plex']['token'],
            'accountID': account_id,
            'librarySectionID': section.key,
            'sort': 'viewedAt:desc'
        }

        try:
            response = requests.get(url, params=params, verify=config['plex'].get('verify_ssl', True), timeout=30)
            if response.status_code != 200:
                continue

            root = ET.fromstring(response.content)

            for video in root.findall('.//Video'):
                video_type = video.get('type')

                if (media_type == 'movie' and video_type == 'movie') or \
                   (media_type == 'show' and video_type == 'episode'):

                    rating_key = video.get('ratingKey')
                    if media_type == 'show':
                        grandparent_key_path = video.get('grandparentKey')
                        if grandparent_key_path:
                            rating_key = grandparent_key_path.split('/')[-1]
                        else:
                            rating_key = None

                    if rating_key and str(rating_key) not in seen_tmdb_ids:
                        try:
                            item = plex.fetchItem(int(rating_key))

                            tmdb_id = None
                            for guid in item.guids:
                                if 'tmdb://' in guid.id:
                                    tmdb_id = int(guid.id.split('tmdb://')[1])
                                    break

                            if tmdb_id and tmdb_id not in seen_tmdb_ids:
                                watched_items.append({
                                    'tmdb_id': tmdb_id,
                                    'title': item.title,
                                    'year': item.year if hasattr(item, 'year') else None
                                })
                                seen_tmdb_ids.add(str(rating_key))
                                seen_tmdb_ids.add(tmdb_id)
                        except (ValueError, KeyError, AttributeError) as e:
                            logger.debug(f"Error extracting TMDB ID for rating key {rating_key}: {e}")

        except (requests.RequestException, ET.ParseError) as e:
            logger.debug(f"Error fetching watch history for account {account_id}: {e}")
            continue

    return watched_items


def update_plex_collection(
    section: Any,
    collection_name: str,
    items: List[Any],
    logger: Any = None,
    label_name: str = None
) -> bool:
    """
    Create or update a Plex collection with items in the specified order.

    Args:
        section: PlexAPI library section (movies or shows)
        collection_name: Name of the collection to create/update
        items: List of Plex media items in desired order (best first)
        logger: Optional logger instance
        label_name: Optional label to add to the collection itself (for private collections)

    Returns:
        True if successful, False otherwise
    """
    if not items:
        if logger:
            logger.warning(f"No items provided for collection: {collection_name}")
        return False

    try:
        existing_collection = None
        for collection in section.collections():
            if collection.title == collection_name:
                existing_collection = collection
                break

        target_collection = None
        if existing_collection:
            current_items = existing_collection.items()
            if current_items:
                existing_collection.removeItems(current_items)
            existing_collection.addItems(items)
            target_collection = existing_collection
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

        # Add private label to collection itself for private collection filtering
        # Uses a DIFFERENT prefix than item labels so exclusions only affect collections
        # Items keep Recommended_* labels (visible to all), collections get PrivateCollection_*
        if target_collection and label_name:
            try:
                # Convert Recommended_username to PrivateCollection_username
                private_label = label_name.replace('Recommended_', 'PrivateCollection_')
                current_labels = [l.tag for l in target_collection.labels]
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


def cleanup_old_collections(section: Any, current_collection_name: str, username: str, emoji: str, logger: Any = None) -> None:
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


def get_configured_users(config: dict) -> dict:
    """
    Get and validate configured Plex users.

    Args:
        config: Configuration dictionary

    Returns:
        Dictionary with 'managed_users', 'plex_users', and 'admin_user'
    """
    raw_managed = config['plex'].get('managed_users', '')
    managed_users = [u.strip() for u in raw_managed.split(',') if u.strip()]

    plex_users = []
    # Check multiple possible config locations for user list
    plex_user_config = (
        config.get('plex_users', {}).get('users') or
        config.get('users', {}).get('list')  # New config format
    )
    if plex_user_config and str(plex_user_config).lower() != 'none':
        if isinstance(plex_user_config, list):
            plex_users = plex_user_config
        elif isinstance(plex_user_config, str):
            plex_users = [u.strip() for u in plex_user_config.split(',') if u.strip()]

    account = MyPlexAccount(token=config['plex']['token'])
    admin_user = account.username

    all_users = account.users()
    all_usernames_lower = {u.title.lower(): u.title for u in all_users}

    processed_managed = []
    for user in managed_users:
        user_lower = user.lower()
        if user_lower in ['admin', 'administrator']:
            processed_managed.append(admin_user)
        elif user_lower == admin_user.lower():
            processed_managed.append(admin_user)
        elif user_lower in all_usernames_lower:
            processed_managed.append(all_usernames_lower[user_lower])
        else:
            log_error(f"Error: Managed user '{user}' not found")
            raise ValueError(f"User '{user}' not found in Plex account")

    seen = set()
    managed_users = [u for u in processed_managed if not (u in seen or seen.add(u))]

    return {
        'managed_users': managed_users,
        'plex_users': plex_users,
        'admin_user': admin_user
    }


def get_current_users(users: dict) -> str:
    """
    Get formatted string of current users being processed.

    Args:
        users: Dictionary with 'plex_users' and 'managed_users'

    Returns:
        Formatted string describing current users
    """
    if users['plex_users']:
        return f"Plex users: {', '.join(users['plex_users'])}"
    return f"Managed users: {', '.join(users['managed_users'])}"


def get_excluded_genres_for_user(exclude_genres: set, user_preferences: dict, username: str = None) -> set:
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
        user_excluded = user_prefs.get('exclude_genres', [])
        excluded.update([g.lower() for g in user_excluded])

    return excluded


# Content rating hierarchy constants
# Movies: G < PG < PG-13 < R < NC-17
MOVIE_RATING_HIERARCHY = ['G', 'PG', 'PG-13', 'R', 'NC-17']

# TV: TV-Y < TV-Y7 < TV-G < TV-PG < TV-14 < TV-MA
TV_RATING_HIERARCHY = ['TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA']


def get_max_rating_for_user(user_preferences: dict, username: str = None) -> Optional[str]:
    """
    Get the maximum content rating allowed for a user.

    Args:
        user_preferences: User preferences dictionary
        username: Username to get max_rating for

    Returns:
        Content rating string (e.g., 'PG-13', 'TV-14') or None if no restriction
    """
    if not username or not user_preferences or username not in user_preferences:
        return None

    user_prefs = user_preferences[username]
    return user_prefs.get('max_rating')


def is_rating_allowed(content_rating: str, max_rating: str, media_type: str = 'movie') -> bool:
    """
    Check if a content rating is allowed given the user's max_rating.

    Args:
        content_rating: The content rating of the item (e.g., 'R', 'TV-MA')
        max_rating: The user's maximum allowed rating (e.g., 'PG-13', 'TV-14')
        media_type: 'movie' or 'tv' to select the appropriate rating hierarchy

    Returns:
        True if the content rating is at or below max_rating, False otherwise
    """
    if not max_rating or not content_rating:
        return True  # No restriction or no rating = allow

    # Normalize the rating strings (uppercase, strip whitespace)
    content_rating = content_rating.upper().strip()
    max_rating = max_rating.upper().strip()

    # Select the appropriate hierarchy
    hierarchy = TV_RATING_HIERARCHY if media_type == 'tv' else MOVIE_RATING_HIERARCHY

    # Get the indices of both ratings
    try:
        content_idx = hierarchy.index(content_rating)
        max_idx = hierarchy.index(max_rating)
        return content_idx <= max_idx
    except ValueError:
        # Rating not in hierarchy - allow by default (e.g., 'NR', 'Unrated')
        # Log this case for debugging but don't block the content
        logger.debug(f"Rating '{content_rating}' not in hierarchy, allowing by default")
        return True


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
    if users['plex_users']:
        return plex
    try:
        account = MyPlexAccount(token=config['plex']['token'])
        user = account.user(users['managed_users'][0])
        return plex.switchUser(user)
    except plexapi.exceptions.PlexApiException as e:
        log_warning(f"Could not switch to managed user context: {e}")
        return plex


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
    genres = []
    try:
        if not hasattr(item, 'genres') or not item.genres:
            return genres

        for genre in item.genres:
            if hasattr(genre, 'tag'):
                genres.append(genre.tag.lower())
            elif isinstance(genre, str):
                genres.append(genre.lower())
    except (AttributeError, TypeError) as e:
        logger.debug(f"Failed to extract genres: {e}")
    return genres


def extract_ids_from_guids(item) -> Dict[str, Optional[str]]:
    """
    Extract IMDB and TMDB IDs from a Plex item's guids.

    Args:
        item: Plex media item with optional 'guids' attribute

    Returns:
        Dict with 'imdb_id' and 'tmdb_id' keys (values may be None)
    """
    result = {'imdb_id': None, 'tmdb_id': None}

    if not hasattr(item, 'guids'):
        return result

    for guid in item.guids:
        guid_id = guid.id if hasattr(guid, 'id') else str(guid)
        if 'imdb://' in guid_id:
            result['imdb_id'] = guid_id.replace('imdb://', '').split('?')[0]
        elif 'themoviedb://' in guid_id or 'tmdb://' in guid_id:
            try:
                tmdb_str = guid_id.split('themoviedb://')[-1].split('tmdb://')[-1].split('?')[0]
                result['tmdb_id'] = int(tmdb_str)
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
            if hasattr(item, 'userRating') and item.userRating:
                return float(item.userRating)
            if hasattr(item, 'audienceRating') and item.audienceRating:
                return float(item.audienceRating)
        else:
            if hasattr(item, 'audienceRating') and item.audienceRating:
                return float(item.audienceRating)
            if hasattr(item, 'userRating') and item.userRating:
                return float(item.userRating)

        if hasattr(item, 'ratings'):
            for rating in item.ratings:
                if hasattr(rating, 'value') and rating.value:
                    if (getattr(rating, 'image', '') == 'imdb://image.rating' or
                        getattr(rating, 'type', '') == 'audience'):
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
    imdb_ids = set()
    try:
        for item in plex_section.all():
            if hasattr(item, 'guids'):
                for guid in item.guids:
                    if guid.id.startswith('imdb://'):
                        imdb_ids.add(guid.id.replace('imdb://', ''))
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


def apply_user_label_restrictions(
    config: Dict,
    all_user_labels: Dict[str, str],
) -> bool:
    """
    Apply exclude restrictions so each user can't see other users' collections.

    Collections get PrivateCollection_* labels (hidden via exclusions).
    Items get Recommended_* labels (visible to everyone, not excluded).

    Each user gets an EXCLUDE filter for other users' PrivateCollection_* labels:
    - They can see their full library (all items, including others' recommendations)
    - They can see their own collection (their PrivateCollection label not excluded)
    - They cannot see other users' collections (those PrivateCollection labels excluded)

    Uses direct Plex API calls (not plexapi's updateFriend which doesn't work for Home users).
    Note: Server admin cannot have restrictions applied (Plex limitation).

    Args:
        config: Configuration dict with plex token
        all_user_labels: Dict mapping username to their Recommended_* label name
                         e.g., {'Jason': 'Recommended_Jason', 'Sarah': 'Recommended_Sarah'}
                         (converted to PrivateCollection_* internally for exclusions)

    Returns:
        True if all restrictions applied successfully, False if any failed
    """
    if not all_user_labels:
        return True

    # Only one user - nothing to hide from anyone
    if len(all_user_labels) <= 1:
        return True

    plex_token = config['plex']['token']

    try:
        # Get admin username to skip
        account = MyPlexAccount(token=plex_token)
        admin_username = account.username.lower()

        # Fetch all users via direct API (works for both shared and managed users)
        users_url = f"https://plex.tv/api/users?X-Plex-Token={plex_token}"
        response = requests.get(users_url)
        response.raise_for_status()

        # Parse XML response to get user IDs and names
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.content)

        # Build user lookup: username -> user_id
        plex_users = {}
        for user_elem in root.findall('.//User'):
            user_id = user_elem.get('id')
            title = user_elem.get('title', '')
            username_attr = user_elem.get('username', '')
            email = user_elem.get('email', '')

            if title:
                plex_users[title.lower()] = user_id
            if username_attr:
                plex_users[username_attr.lower()] = user_id
            if email:
                plex_users[email.lower()] = user_id

        logger.debug(f"Plex users available for restrictions: {list(plex_users.keys())}")

        all_success = True
        for username, user_label in all_user_labels.items():
            # Admin can't have restrictions
            if username.lower() == admin_username:
                logger.debug(f"Skipping restrictions for admin user: {username}")
                continue

            # Find the user ID
            user_id = plex_users.get(username.lower())

            # Try normalized match if exact match fails
            if not user_id:
                username_normalized = username.lower().replace(' ', '').replace('-', '').replace('_', '')
                for key, uid in plex_users.items():
                    key_normalized = key.replace(' ', '').replace('-', '').replace('_', '')
                    if username_normalized == key_normalized:
                        user_id = uid
                        logger.debug(f"Matched '{username}' to user ID {uid} via normalized match")
                        break

            if not user_id:
                log_warning(f"User '{username}' not found for label restrictions. Available: {list(plex_users.keys())}")
                all_success = False
                continue

            # Get labels to EXCLUDE (all other users' PrivateCollection labels)
            # We exclude PrivateCollection_* (on collections) NOT Recommended_* (on items)
            # This hides other users' collections but keeps items visible to everyone
            exclude_labels = [
                label.replace('Recommended_', 'PrivateCollection_')
                for u, label in all_user_labels.items()
                if u.lower() != username.lower()
            ]

            if not exclude_labels:
                continue  # Nothing to exclude

            # Build filter string: label!=Label1,Label2,Label3
            labels_str = ','.join(exclude_labels)
            filter_value = f"label!={labels_str}"

            # Apply restrictions via direct PUT to Plex API
            update_url = f"https://plex.tv/api/users/{user_id}"
            params = {
                'X-Plex-Token': plex_token,
                'filterMovies': filter_value,
                'filterTelevision': filter_value
            }

            try:
                put_response = requests.put(update_url, params=params)
                put_response.raise_for_status()
                print(f"{GREEN}Applied exclusions for {username}: hiding labels {exclude_labels}{RESET}")
            except requests.RequestException as e:
                log_warning(f"Failed to apply restrictions for {username}: {e}")
                all_success = False

        return all_success

    except requests.RequestException as e:
        log_warning(f"Error fetching Plex users: {e}")
        return False
    except Exception as e:
        log_warning(f"Unexpected error applying restrictions: {e}")
        return False
