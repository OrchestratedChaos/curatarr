"""
Shared utilities for Plex recommendation scripts (Movies and TV Shows)
Contains common functions for account management and watch history fetching
"""

import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# ANSI color codes
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'

# Language code mappings
LANGUAGE_CODES = {
    'en': 'English', 'es': 'Spanish', 'fr': 'French', 'de': 'German', 'it': 'Italian',
    'pt': 'Portuguese', 'ru': 'Russian', 'ja': 'Japanese', 'ko': 'Korean', 'zh': 'Chinese',
    'ar': 'Arabic', 'hi': 'Hindi', 'nl': 'Dutch', 'sv': 'Swedish', 'no': 'Norwegian',
    'da': 'Danish', 'fi': 'Finnish', 'pl': 'Polish', 'tr': 'Turkish', 'el': 'Greek',
    'he': 'Hebrew', 'th': 'Thai', 'vi': 'Vietnamese', 'id': 'Indonesian', 'ms': 'Malay',
    'cs': 'Czech', 'hu': 'Hungarian', 'ro': 'Romanian', 'uk': 'Ukrainian', 'fa': 'Persian',
    'bn': 'Bengali', 'ta': 'Tamil', 'te': 'Telugu', 'mr': 'Marathi', 'ur': 'Urdu'
}

# Rating multipliers for similarity scoring
RATING_MULTIPLIERS = {
    0: 0.1,   # Strong dislike
    1: 0.2,   # Very poor
    2: 0.4,   # Poor
    3: 0.6,   # Below average
    4: 0.8,   # Slightly below average
    5: 1.0,   # Neutral/baseline
    6: 1.2,   # Slightly above average
    7: 1.4,   # Good
    8: 1.6,   # Very good
    9: 1.8,   # Excellent
    10: 2.0   # Outstanding
}


def cleanup_old_logs(log_dir: str, retention_days: int):
    """
    Remove log files older than specified retention period

    Args:
        log_dir: Directory containing log files
        retention_days: Number of days to retain logs (0 = keep all)
    """
    if retention_days <= 0:
        return

    try:
        cutoff_time = datetime.now() - timedelta(days=retention_days)

        for filename in os.listdir(log_dir):
            if not filename.endswith('.log'):
                continue

            filepath = os.path.join(log_dir, filename)
            try:
                file_mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                if file_mtime < cutoff_time:
                    os.remove(filepath)
                    print(f"{YELLOW}Removed old log: {filename} (age: {(datetime.now() - file_mtime).days} days){RESET}")
            except Exception as e:
                print(f"{YELLOW}Failed to remove old log {filename}: {e}{RESET}")

    except Exception as e:
        print(f"{YELLOW}Error during log cleanup: {e}{RESET}")


def get_full_language_name(lang_code: str) -> str:
    """
    Convert language code to full language name

    Args:
        lang_code: ISO language code (e.g., 'en', 'es')

    Returns:
        Full language name (e.g., 'English', 'Spanish')
    """
    return LANGUAGE_CODES.get(lang_code.lower(), lang_code.capitalize())


def get_plex_account_ids(config, users_to_match):
    """
    Get Plex account IDs for configured users with flexible name matching

    Args:
        config: Configuration dict with plex URL and token
        users_to_match: List of usernames to find account IDs for

    Returns:
        List of account ID strings
    """
    account_ids = []
    try:
        # Get all Plex accounts
        response = requests.get(
            f"{config['plex']['url']}/accounts",
            headers={'X-Plex-Token': config['plex']['token']},
            verify=False
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)

        # Match configured usernames to account IDs with flexible matching
        for username in users_to_match:
            account = None
            username_normalized = username.lower().replace(' ', '').replace('-', '').replace('_', '')

            # Try exact match first
            for acc in root.findall('.//Account'):
                plex_name = acc.get('name', '')
                if plex_name and plex_name.lower() == username.lower():
                    account = acc
                    break

            # Try normalized match (remove spaces, dashes, underscores)
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
                print(f"{RED}User '{username}' not found in Plex accounts!{RESET}")

    except Exception as e:
        print(f"{RED}Error getting Plex account IDs: {e}{RESET}")

    return account_ids


def get_watched_movie_count(config, users_to_check):
    """
    Get count of unique watched movies from Plex (for cache invalidation)

    Args:
        config: Configuration dict with plex URL and token
        users_to_check: List of usernames to check watch history for

    Returns:
        Integer count of unique watched movies
    """
    try:
        from plexapi.myplex import MyPlexAccount

        if not users_to_check:
            return 0

        # Get account IDs for all users
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

        # Get unique watched movie count (rating keys) using Plex history API
        watched_movies = set()
        for account_id in account_ids:
            url = f"{config['plex']['url']}/status/sessions/history/all?X-Plex-Token={config['plex']['token']}&accountID={account_id}"
            response = requests.get(url, verify=False)
            root = ET.fromstring(response.content)

            for video in root.findall('.//Video'):
                if video.get('type') == 'movie':
                    rating_key = video.get('ratingKey')
                    if rating_key:
                        watched_movies.add(rating_key)

        return len(watched_movies)
    except Exception as e:
        print(f"{YELLOW}Error getting watched movie count: {e}{RESET}")
        return 0


def get_watched_show_count(config, users_to_check):
    """
    Get count of unique watched TV shows from Plex (for cache invalidation)

    Args:
        config: Configuration dict with plex URL and token
        users_to_check: List of usernames to check watch history for

    Returns:
        Integer count of unique watched TV shows
    """
    try:
        from plexapi.myplex import MyPlexAccount

        if not users_to_check:
            return 0

        # Get account IDs for all users
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

        # Get unique watched show count (grandparent keys) using Plex history API
        watched_shows = set()
        for account_id in account_ids:
            url = f"{config['plex']['url']}/status/sessions/history/all?X-Plex-Token={config['plex']['token']}&accountID={account_id}"
            response = requests.get(url, verify=False)
            root = ET.fromstring(response.content)

            for video in root.findall('.//Video'):
                if video.get('type') == 'episode':
                    # Get grandparent key path (e.g., '/library/metadata/1085')
                    grandparent_key_path = video.get('grandparentKey')
                    if grandparent_key_path:
                        # Extract rating key from path
                        grandparent_key = grandparent_key_path.split('/')[-1]
                        watched_shows.add(grandparent_key)

        return len(watched_shows)
    except Exception as e:
        print(f"{YELLOW}Error getting watched show count: {e}{RESET}")
        return 0


def fetch_plex_watch_history_movies(config, account_ids, movies_section):
    """
    Fetch movie watch history for specified account IDs using direct Plex API

    Args:
        config: Configuration dict with plex URL and token
        account_ids: List of account ID strings
        movies_section: PlexAPI movies library section

    Returns:
        Tuple of (all_history_items, watched_movie_dates dict)
    """
    from plexapi.myplex import MyPlexAccount

    print(f"")
    print(f"{GREEN}Fetching Plex watch history for {len(account_ids)} user(s)...{RESET}")

    try:
        # Get MyPlex account to access managed users (including cloud users)
        myPlex = MyPlexAccount(token=config['plex']['token'])

        # Create a mapping of account IDs to user objects
        managed_users_map = {}
        for user in myPlex.users():
            user_id = str(user.id) if hasattr(user, 'id') else None
            if user_id:
                managed_users_map[user_id] = user

        # Get owner account ID (usually '1')
        owner_id = '1'

        # Collect all history items from all users
        all_history_items = []
        watched_movie_dates = {}

        # Fetch history for each user
        for i, account_id in enumerate(account_ids, 1):
            print(f"  [{i}/{len(account_ids)}] Fetching history for account ID {account_id}...", end='')

            try:
                # Use Plex API directly for ALL users (more reliable than .history())
                if account_id in managed_users_map or account_id == owner_id:
                    # For managed/cloud users, use the Plex API directly
                    base_url = config['plex']['url']
                    token = config['plex']['token']
                    library_key = movies_section.key

                    # Fetch history for this specific account
                    history_url = f"{base_url}/status/sessions/history/all"
                    params = {
                        'X-Plex-Token': token,
                        'accountID': account_id,
                        'librarySectionID': library_key,
                        'sort': 'viewedAt:desc'
                    }

                    response = requests.get(history_url, params=params, verify=False)
                    response.raise_for_status()

                    # Parse the XML response
                    root = ET.fromstring(response.content)

                    # Convert XML history items to match PlexAPI format
                    for video in root.findall('.//Video'):
                        # Create a simple object to mimic PlexAPI history items
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

            except Exception as e:
                print(f" {RED}ERROR: {e}{RESET}")

        return all_history_items, watched_movie_dates

    except Exception as e:
        print(f"{RED}Error fetching watch history: {e}{RESET}")
        return [], {}


def fetch_plex_watch_history_shows(config, account_ids):
    """
    Fetch TV show watch history for specified account IDs using direct Plex API

    Args:
        config: Configuration dict with plex URL and token
        account_ids: List of account ID strings

    Returns:
        Set of watched show IDs (rating keys)
    """
    print(f"")
    print(f"{GREEN}Fetching Plex watch history for {len(account_ids)} user(s)...{RESET}")

    watched_show_ids = set()

    # Fetch history from Plex for each user
    for account_id in account_ids:
        print(f"")
        print(f"{GREEN}Fetching Plex history for account ID: {account_id}{RESET}")

        url = f"{config['plex']['url']}/status/sessions/history/all"
        params = {
            'X-Plex-Token': config['plex']['token'],
            'accountID': account_id,
            'sort': 'viewedAt:desc',
            'X-Plex-Container-Size': 5000
        }

        try:
            response = requests.get(url, params=params, verify=False)
            response.raise_for_status()

            # Parse XML response
            root = ET.fromstring(response.content)
            episode_count = 0

            for video in root.findall('.//Video'):
                # Only process TV episodes
                if video.get('type') == 'episode':
                    # For TV shows, we want the grandparent (show) rating key
                    grandparent_key_path = video.get('grandparentKey')
                    if grandparent_key_path:
                        # Extract rating key from path like '/library/metadata/1085'
                        grandparent_key = grandparent_key_path.split('/')[-1]
                        watched_show_ids.add(int(grandparent_key))
                        episode_count += 1

            print(f"Fetched {episode_count} watched episodes from {len(watched_show_ids)} shows")

        except Exception as e:
            print(f"{RED}Error fetching Plex history: {e}{RESET}")
            continue

    return watched_show_ids
