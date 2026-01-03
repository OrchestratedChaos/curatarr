"""
Shared utilities for Plex recommendation scripts (Movies and TV Shows)
Contains common functions for account management and watch history fetching
"""

import os
import sys
import re
import math
import json
import time
import logging
import requests
import yaml
import xml.etree.ElementTree as ET
import urllib3
import plexapi.server
from collections import Counter
from plexapi.myplex import MyPlexAccount
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple, List, Optional

# Suppress SSL warnings for self-signed Plex certificates (common for home servers)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Cache version - bump this when cache format changes to auto-invalidate old caches
CACHE_VERSION = 2  # v2: Added TMDB keywords to cache


def check_cache_version(cache_path: str, cache_type: str = "cache") -> bool:
    """
    Check if cache file is compatible with current version.

    Args:
        cache_path: Path to the cache file
        cache_type: Description for logging (e.g., "movie cache", "watched cache")

    Returns:
        True if cache is valid and compatible, False if it should be rebuilt
    """
    import json

    if not os.path.exists(cache_path):
        return False

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        cached_version = data.get('cache_version', 1)  # Default to v1 if not present

        if cached_version < CACHE_VERSION:
            print(f"\033[93m{cache_type} is outdated (v{cached_version} < v{CACHE_VERSION}), rebuilding...\033[0m")
            os.remove(cache_path)
            return False

        return True
    except Exception as e:
        print(f"\033[93mError reading {cache_type}, rebuilding: {e}\033[0m")
        return False


def get_config_section(config: Dict, key: str, default: Dict = None) -> Dict:
    """
    Get a config section case-insensitively.

    Args:
        config: The configuration dictionary
        key: The key to look for (will check lowercase and uppercase)
        default: Default value if key not found

    Returns:
        The config section or default value
    """
    if default is None:
        default = {}
    # Try lowercase first (preferred), then uppercase for backwards compatibility
    return config.get(key.lower(), config.get(key.upper(), default))


def get_tmdb_config(config: Dict) -> Dict:
    """
    Get TMDB configuration section, handling case variations.

    Args:
        config: The root configuration dictionary

    Returns:
        Dict with 'api_key' and 'use_keywords' keys
    """
    tmdb_config = get_config_section(config, 'tmdb')
    return {
        'api_key': tmdb_config.get('api_key'),
        'use_keywords': tmdb_config.get('use_tmdb_keywords', tmdb_config.get('use_TMDB_keywords', True))
    }


def setup_logging(debug: bool = False, config: dict = None) -> logging.Logger:
    """
    Configure logging for recommendation scripts.

    Args:
        debug: If True, set level to DEBUG. Otherwise use config or default to INFO.
        config: Optional config dict that may contain logging.level setting.

    Returns:
        Configured logger instance.
    """
    # Determine log level
    if debug:
        level = logging.DEBUG
    elif config and config.get('logging', {}).get('level'):
        level_str = config['logging']['level'].upper()
        level = getattr(logging, level_str, logging.INFO)
    else:
        level = logging.INFO

    # Create handler with colored formatter
    handler = logging.StreamHandler()
    handler.setLevel(level)

    # Import here to avoid circular import (ColoredFormatter defined below)
    formatter = logging.Formatter(
        fmt='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # Remove existing handlers to avoid duplicates
    root_logger.handlers = []
    root_logger.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

    logger = logging.getLogger('plex_recommender')
    logger.setLevel(level)

    return logger

# ANSI color codes
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to log levels"""

    LEVEL_COLORS = {
        logging.DEBUG: CYAN,
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: RED,
    }

    def format(self, record):
        # Add color to the level name
        color = self.LEVEL_COLORS.get(record.levelno, '')
        record.levelname = f"{color}{record.levelname}{RESET}"
        return super().format(record)


# Status output helpers - consistent patterns across all scripts
def print_user_header(username: str):
    """Print header when starting to process a user"""
    print(f"\n{GREEN}Processing recommendations for user: {username}{RESET}")
    print("-" * 50)


def print_user_footer(username: str):
    """Print footer when done processing a user"""
    print(f"\n{GREEN}Completed processing for user: {username}{RESET}")
    print("-" * 50)


def print_status(message: str, level: str = "info"):
    """Print a status message with appropriate color and log to file"""
    logger = logging.getLogger('plex_recommender')
    if level == "success":
        print(f"{GREEN}✓ {message}{RESET}")
        logger.info(message)
    elif level == "warning":
        log_warning(f"{message}")
        logger.warning(message)
    elif level == "error":
        log_error(f"{message}")
        logger.error(message)
    else:
        print(message)
        logger.info(message)


def log_warning(message: str):
    """Log warning and print with yellow color"""
    logger = logging.getLogger('plex_recommender')
    logger.warning(message)
    print(f"{YELLOW}{message}{RESET}")


def log_error(message: str):
    """Log error and print with red color"""
    logger = logging.getLogger('plex_recommender')
    logger.error(message)
    print(f"{RED}{message}{RESET}")

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

# Default rating multipliers for similarity scoring (Plex uses 0-10 scale)
# Higher ratings = stronger signal. 5-star (10) boosted to emphasize favorites.
DEFAULT_RATING_MULTIPLIERS = {
    0: 0.1,   # Strong dislike
    1: 0.2,   # Very poor
    2: 0.4,   # Poor
    3: 0.6,   # Below average
    4: 0.8,   # Slightly below average
    5: 1.0,   # Neutral/baseline
    6: 1.2,   # Slightly above average
    7: 1.4,   # Good
    8: 1.7,   # Very good
    9: 2.0,   # Excellent
    10: 2.5   # Outstanding (5 stars) - strong signal
}

# Backwards compatibility alias
RATING_MULTIPLIERS = DEFAULT_RATING_MULTIPLIERS


def get_rating_multipliers(config: dict = None) -> dict:
    """
    Get rating multipliers from config or use defaults.

    Config uses 5-star scale, Plex uses 10-point scale.
    Maps: star_5 → 9-10, star_4 → 7-8, star_3 → 5-6, star_2 → 3-4, star_1 → 1-2

    Args:
        config: Configuration dict with optional rating_multipliers section

    Returns:
        Dict mapping Plex ratings (0-10) to multiplier values
    """
    if not config or 'rating_multipliers' not in config:
        return DEFAULT_RATING_MULTIPLIERS.copy()

    rm = config['rating_multipliers']

    # Get values from config with defaults
    star_5 = rm.get('star_5', 2.5)
    star_4 = rm.get('star_4', 1.7)
    star_3 = rm.get('star_3', 1.0)
    star_2 = rm.get('star_2', 0.4)
    star_1 = rm.get('star_1', 0.2)

    # Map 5-star config to 10-point Plex scale
    return {
        0: 0.1,                              # Unrated/dislike
        1: star_1,                           # 1 star
        2: star_1 + (star_2 - star_1) * 0.5, # Between 1-2 stars
        3: star_2,                           # 2 stars
        4: star_2 + (star_3 - star_2) * 0.5, # Between 2-3 stars
        5: star_3,                           # 3 stars (baseline)
        6: star_3 + (star_4 - star_3) * 0.5, # Between 3-4 stars
        7: star_4,                           # 4 stars
        8: star_4 + (star_5 - star_4) * 0.5, # Between 4-5 stars
        9: star_5 - (star_5 - star_4) * 0.2, # High 4 stars
        10: star_5                           # 5 stars
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
                    log_warning(f"Removed old log: {filename} (age: {(datetime.now() - file_mtime).days} days)")
            except Exception as e:
                log_warning(f"Failed to remove old log {filename}: {e}")

    except Exception as e:
        log_warning(f"Error during log cleanup: {e}")


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
                log_error(f"User '{username}' not found in Plex accounts!")

    except Exception as e:
        log_error(f"Error getting Plex account IDs: {e}")

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
        log_warning(f"Error getting watched movie count: {e}")
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

        # Get unique watched show count (grandparent rating keys) using Plex history API
        watched_shows = set()
        for account_id in account_ids:
            url = f"{config['plex']['url']}/status/sessions/history/all?X-Plex-Token={config['plex']['token']}&accountID={account_id}"
            response = requests.get(url, verify=False)
            root = ET.fromstring(response.content)

            for video in root.findall('.//Video'):
                if video.get('type') == 'episode':
                    # For episodes, use grandparentRatingKey (the show's ID)
                    show_key = video.get('grandparentRatingKey')
                    if show_key:
                        watched_shows.add(show_key)

        return len(watched_shows)
    except Exception as e:
        log_warning(f"Error getting watched show count: {e}")
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
                        'sort': 'viewedAt:desc',
                        'X-Plex-Container-Size': 10000  # Fetch up to 10k history items (all)
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
        log_error(f"Error fetching watch history: {e}")
        return [], {}


def fetch_plex_watch_history_shows(config, account_ids, tv_section):
    """
    Fetch TV show watch history for specified account IDs using direct Plex API

    Args:
        config: Configuration dict with plex URL and token
        account_ids: List of account ID strings
        tv_section: PlexAPI TV library section

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
            'librarySectionID': tv_section.key,
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
            log_error(f"Error fetching Plex history: {e}")
            continue

    return watched_show_ids


def fetch_watch_history_with_tmdb(plex, config, account_ids, section, media_type='movie'):
    """
    Fetch watch history with TMDB IDs for external recommendations

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
            response = requests.get(url, params=params, verify=False)
            if response.status_code != 200:
                continue

            root = ET.fromstring(response.content)

            for video in root.findall('.//Video'):
                video_type = video.get('type')

                # Match video type to media type
                if (media_type == 'movie' and video_type == 'movie') or \
                   (media_type == 'show' and video_type == 'episode'):

                    # Get rating key
                    rating_key = video.get('ratingKey')
                    if media_type == 'show':
                        # For shows, get grandparent (show) key from path
                        grandparent_key_path = video.get('grandparentKey')
                        if grandparent_key_path:
                            rating_key = grandparent_key_path.split('/')[-1]
                        else:
                            rating_key = None

                    if rating_key and str(rating_key) not in seen_tmdb_ids:
                        try:
                            item = plex.fetchItem(int(rating_key))

                            # Extract TMDB ID
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
                        except:
                            pass

        except Exception as e:
            continue

    return watched_items


def update_plex_collection(section, collection_name: str, items: list, logger=None):
    """
    Create or update a Plex collection with items in the specified order.

    If collection exists: clears and refills (preserves pins/settings).
    If collection doesn't exist: creates new collection.

    Args:
        section: PlexAPI library section (movies or shows)
        collection_name: Name of the collection to create/update
        items: List of Plex media items in desired order (best first)
        logger: Optional logger instance

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

        if existing_collection:
            # Clear and refill to preserve settings/pins
            current_items = existing_collection.items()
            if current_items:
                existing_collection.removeItems(current_items)
            existing_collection.addItems(items)
            if logger:
                logger.info(f"Updated collection: {collection_name} ({len(items)} items)")
            else:
                print(f"Updated collection: {collection_name} ({len(items)} items)")
        else:
            # Create new collection
            section.createCollection(title=collection_name, items=items)
            if logger:
                logger.info(f"Created collection: {collection_name} ({len(items)} items)")
            else:
                print(f"Created collection: {collection_name} ({len(items)} items)")

        return True

    except Exception as e:
        error_msg = f"Error updating collection {collection_name}: {e}"
        if logger:
            logger.error(error_msg)
        else:
            print(f"ERROR: {error_msg}")
        return False


def cleanup_old_collections(section, current_collection_name: str, username: str, emoji: str, logger=None):
    """
    Delete old collection patterns for a user that don't match current naming.

    Cleans up collections from previous naming schemes (e.g., username-based
    when we now use display_name-based).

    Args:
        section: PlexAPI library section
        current_collection_name: The current/correct collection name
        username: The username to check for old patterns
        emoji: The emoji prefix (🎬 for movies, 📺 for TV)
        logger: Optional logger instance
    """
    # Patterns that might exist from old naming schemes
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
        # Smart collection patterns
        f"{emoji} {username} - Recommendation",
        f"{emoji} {username.capitalize()} - Recommendation",
    ]

    try:
        for collection in section.collections():
            # Skip the current collection
            if collection.title == current_collection_name:
                continue

            # Check if this matches an old pattern for this user
            # Also check if collection contains the username (broader match)
            matches_pattern = collection.title in old_patterns
            contains_username = username.lower() in collection.title.lower() and "Recommend" in collection.title

            if matches_pattern or contains_username:
                collection.delete()
                msg = f"Deleted old collection: {collection.title}"
                if logger:
                    logger.info(msg)
                else:
                    print(msg)

    except Exception as e:
        error_msg = f"Error cleaning up old collections: {e}"
        if logger:
            logger.warning(error_msg)
        else:
            print(f"WARNING: {error_msg}")


# ANSI pattern for stripping color codes from log files
ANSI_PATTERN = re.compile(r'\x1b\[[0-9;]*m')


class TeeLogger:
    """
    A simple 'tee' class that writes to both console and a file,
    stripping ANSI color codes for the file and handling Unicode characters.
    """
    def __init__(self, logfile):
        self.logfile = logfile
        # Force UTF-8 encoding for stdout
        if hasattr(sys.stdout, 'buffer'):
            self.stdout_buffer = sys.stdout.buffer
        else:
            self.stdout_buffer = sys.stdout

    def write(self, text):
        try:
            # Write to console
            if hasattr(sys.stdout, 'buffer'):
                self.stdout_buffer.write(text.encode('utf-8'))
            else:
                sys.__stdout__.write(text)

            # Write to file (strip ANSI codes)
            stripped = ANSI_PATTERN.sub('', text)
            self.logfile.write(stripped)
        except UnicodeEncodeError:
            # Fallback for problematic characters
            safe_text = text.encode('ascii', 'replace').decode('ascii')
            if hasattr(sys.stdout, 'buffer'):
                self.stdout_buffer.write(safe_text.encode('utf-8'))
            else:
                sys.__stdout__.write(safe_text)
            stripped = ANSI_PATTERN.sub('', safe_text)
            self.logfile.write(stripped)

    def flush(self):
        if hasattr(sys.stdout, 'buffer'):
            self.stdout_buffer.flush()
        else:
            sys.__stdout__.flush()
        self.logfile.flush()


def load_config(config_path: str) -> dict:
    """
    Load YAML configuration file.

    Args:
        config_path: Path to config.yml file

    Returns:
        Parsed config dictionary
    """
    try:
        with open(config_path, 'r') as file:
            config = yaml.safe_load(file)
            print(f"Successfully loaded configuration from {config_path}")
            return config
    except Exception as e:
        log_error(f"Error loading config from {config_path}: {e}")
        raise


def init_plex(config: dict) -> plexapi.server.PlexServer:
    """
    Initialize connection to Plex server.

    Args:
        config: Configuration dictionary with plex.url and plex.token

    Returns:
        PlexServer instance
    """
    try:
        return plexapi.server.PlexServer(
            config['plex']['url'],
            config['plex']['token']
        )
    except Exception as e:
        log_error(f"Error connecting to Plex server: {e}")
        raise


def get_configured_users(config: dict) -> dict:
    """
    Get and validate configured Plex users.

    Args:
        config: Configuration dictionary

    Returns:
        Dictionary with 'managed_users', 'plex_users', and 'admin_user'
    """
    # Get raw managed users list from config
    raw_managed = config['plex'].get('managed_users', '')
    managed_users = [u.strip() for u in raw_managed.split(',') if u.strip()]

    # Get Plex users
    plex_users = []

    # Check if Plex users is 'none' or empty
    plex_user_config = config.get('plex_users', {}).get('users')
    if plex_user_config and str(plex_user_config).lower() != 'none':
        if isinstance(plex_user_config, list):
            plex_users = plex_user_config
        elif isinstance(plex_user_config, str):
            plex_users = [u.strip() for u in plex_user_config.split(',') if u.strip()]

    # Resolve admin account
    account = MyPlexAccount(token=config['plex']['token'])
    admin_user = account.username

    # User validation logic
    all_users = account.users()
    all_usernames_lower = {u.title.lower(): u.title for u in all_users}

    processed_managed = []
    for user in managed_users:
        user_lower = user.lower()
        if user_lower in ['admin', 'administrator']:
            # Special case for admin keywords
            processed_managed.append(admin_user)
        elif user_lower == admin_user.lower():
            # Direct match with admin username (case-insensitive)
            processed_managed.append(admin_user)
        elif user_lower in all_usernames_lower:
            # Match with shared users
            processed_managed.append(all_usernames_lower[user_lower])
        else:
            log_error(f"Error: Managed user '{user}' not found")
            raise ValueError(f"User '{user}' not found in Plex account")

    # Remove duplicates while preserving order
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
    # Start with global excluded genres
    excluded = set(exclude_genres)

    # Add user-specific excluded genres if configured
    if username and username in user_preferences:
        user_prefs = user_preferences[username]
        user_excluded = user_prefs.get('exclude_genres', [])
        excluded.update([g.lower() for g in user_excluded])

    return excluded


def get_user_specific_connection(plex, config: dict, users: dict):
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
    except Exception as e:
        log_warning(f"Could not switch to managed user context: {e}")
        return plex


def calculate_recency_multiplier(viewed_at, recency_config: dict) -> float:
    """
    Calculate recency decay multiplier based on when content was watched.

    Args:
        viewed_at: Timestamp of when content was viewed
        recency_config: Recency decay configuration from config

    Returns:
        Multiplier value (0.1 to 1.0)
    """
    # Check if recency decay is enabled
    if not recency_config.get('enabled', True):
        return 1.0

    # Calculate days since watched
    now = datetime.now(timezone.utc)
    viewed_date = datetime.fromtimestamp(int(viewed_at), tz=timezone.utc)
    days_ago = (now - viewed_date).days

    # Apply time-based decay weights
    if days_ago <= 30:
        multiplier = recency_config.get('days_0_30', 1.0)
    elif days_ago <= 90:
        multiplier = recency_config.get('days_31_90', 0.75)
    elif days_ago <= 180:
        multiplier = recency_config.get('days_91_180', 0.50)
    elif days_ago <= 365:
        multiplier = recency_config.get('days_181_365', 0.25)
    else:
        multiplier = recency_config.get('days_365_plus', 0.10)

    return multiplier


def calculate_rewatch_multiplier(view_count: int) -> float:
    """
    Calculate rewatch multiplier using logarithmic scaling.

    Rewatch scale (log2(views) + 1):
    - 1 view: 1.0x weight
    - 2 views: 2.0x weight
    - 4 views: 3.0x weight
    - 8 views: 4.0x weight
    - 16 views: 5.0x weight

    Args:
        view_count: Number of times content was viewed

    Returns:
        Multiplier value (1.0+)
    """
    if not view_count or view_count <= 1:
        return 1.0
    return math.log2(view_count) + 1


# Title suffixes to strip for fuzzy matching
TITLE_SUFFIXES_TO_STRIP = [
    ' 4K', ' 4k', ' HD', ' hd', ' UHD', ' uhd',
    ' Extended', ' extended', ' EXTENDED',
    ' Director\'s Cut', ' Directors Cut', ' Theatrical',
    ' Unrated', ' UNRATED', ' Remastered', ' REMASTERED',
    ' Special Edition', ' Collector\'s Edition',
    ' IMAX', ' 3D', ' 3d'
]


def normalize_title(title: str) -> str:
    """
    Normalize a movie/show title by removing common suffixes like 4K, HD, Extended, etc.

    Args:
        title: Original title

    Returns:
        Normalized title with suffixes stripped
    """
    if not title:
        return title

    normalized = title.strip()
    for suffix in TITLE_SUFFIXES_TO_STRIP:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)].strip()

    return normalized


def find_plex_movie(movies_section, title: str, year: int = None):
    """
    Find a movie in Plex library with fuzzy title matching.

    Handles cases like "Jason Bourne" matching "Jason Bourne 4K".

    Args:
        movies_section: Plex movies library section
        title: Movie title to search for
        year: Optional release year for additional filtering

    Returns:
        Plex movie object or None if not found
    """
    # First try exact title match
    results = movies_section.search(title=title)
    if results:
        if year:
            match = next((m for m in results if m.year == year), None)
            if match:
                return match
        else:
            return results[0]

    # Try normalized title search (strips 4K, HD, etc. from Plex titles)
    normalized_search = normalize_title(title)
    all_movies = movies_section.all()

    for movie in all_movies:
        plex_normalized = normalize_title(movie.title)
        if plex_normalized.lower() == normalized_search.lower():
            if year is None or movie.year == year:
                return movie

    # Try partial match (title contains or is contained)
    title_lower = title.lower()
    for movie in all_movies:
        movie_title_lower = movie.title.lower()
        if title_lower in movie_title_lower or movie_title_lower in title_lower:
            if year is None or movie.year == year:
                return movie

    return None


def map_path(path: str, path_mappings: dict) -> str:
    """
    Apply path mappings for cross-platform compatibility.

    Args:
        path: Original file path
        path_mappings: Dictionary of path prefix replacements

    Returns:
        Mapped path string
    """
    if not path_mappings:
        return path

    for from_path, to_path in path_mappings.items():
        if path.startswith(from_path):
            return path.replace(from_path, to_path, 1)

    return path


def show_progress(prefix: str, current: int, total: int):
    """
    Display progress indicator on console.

    Args:
        prefix: Text prefix for progress line
        current: Current item number
        total: Total number of items
    """
    pct = int((current / total) * 100) if total > 0 else 0
    msg = f"\r{CYAN}{prefix} {current}/{total} ({pct}%){RESET}"
    sys.stdout.write(msg)
    sys.stdout.flush()
    if current == total:
        sys.stdout.write("\n")


# Genre normalization: Map various genre names to standard TMDB names
GENRE_NORMALIZATION = {
    'sci-fi': 'Science Fiction',
    'scifi': 'Science Fiction',
    'science-fiction': 'Science Fiction',
    'sci-fi & fantasy': 'Science Fiction',
    'action & adventure': 'Action',
    'action/adventure': 'Action',
    'war & politics': 'War',
    'tv movie': 'Drama',
    'news': 'Documentary',
    'talk': 'Comedy',
    'reality': 'Documentary',
    'soap': 'Drama',
    'kids': 'Family',
}


def normalize_genre(genre_name: str) -> str:
    """
    Normalize genre name to lowercase standard.

    Args:
        genre_name: Raw genre name from Plex or other source

    Returns:
        Normalized lowercase genre name
    """
    if not genre_name:
        return genre_name
    lower = genre_name.lower().strip()
    # Return lowercase version, with optional mapping for special cases
    mapped = GENRE_NORMALIZATION.get(lower)
    return mapped.lower() if mapped else lower


def fuzzy_keyword_match(keyword: str, user_keywords: Dict[str, int]) -> Tuple[float, Optional[str]]:
    """
    Check if keyword fuzzy-matches any user keyword.

    Args:
        keyword: Keyword to match
        user_keywords: Dict of user's keyword preferences with counts

    Returns:
        Tuple of (match_score, matched_keyword) based on best partial match
    """
    if not keyword or not user_keywords:
        return 0, None

    keyword_lower = keyword.lower()

    # Check for exact match first
    if keyword_lower in user_keywords:
        return user_keywords[keyword_lower], keyword_lower

    # Check for partial matches (keyword contains or is contained by user keyword)
    best_score = 0
    best_match = None

    for user_kw, count in user_keywords.items():
        user_kw_lower = user_kw.lower()

        # Check if one contains the other
        if keyword_lower in user_kw_lower or user_kw_lower in keyword_lower:
            # Score based on overlap ratio
            overlap = len(set(keyword_lower.split()) & set(user_kw_lower.split()))
            total = len(set(keyword_lower.split()) | set(user_kw_lower.split()))
            similarity = overlap / total if total > 0 else 0

            # Weight by the user's preference count and similarity
            match_score = count * (0.5 + 0.5 * similarity)  # At least 50% credit for partial match

            if match_score > best_score:
                best_score = match_score
                best_match = user_kw

    return best_score, best_match


def _redistribute_weights(weights: Dict, user_profile: Dict, media_type: str = 'movie') -> Dict:
    """
    Redistribute weights from empty profile components to components with data.

    If user has no keywords in their profile, that 45% would be wasted.
    This redistributes unused weight proportionally to components that have data.

    Args:
        weights: Original weight dict
        user_profile: User's profile data (counters for genres, actors, etc.)
        media_type: 'movie' or 'tv' - determines director vs studio

    Returns:
        Dict of effective weights with redistribution applied
    """
    # Check which components have data in user profile
    has_genres = bool(user_profile.get('genres', {}))
    has_directors = bool(user_profile.get('directors', {})) if media_type == 'movie' else False
    has_studios = bool(user_profile.get('studios', user_profile.get('studio', {}))) if media_type == 'tv' else False
    has_actors = bool(user_profile.get('actors', {}))
    has_languages = bool(user_profile.get('languages', {}))
    has_keywords = bool(user_profile.get('keywords', user_profile.get('tmdb_keywords', {})))

    # Get original weights
    genre_w = weights.get('genre', weights.get('genre_weight', 0.20))
    director_w = weights.get('director', weights.get('director_weight', 0.15)) if media_type == 'movie' else 0
    studio_w = weights.get('studio', weights.get('studio_weight', 0.15)) if media_type == 'tv' else 0
    actor_w = weights.get('actor', weights.get('actor_weight', 0.15))
    language_w = weights.get('language', weights.get('language_weight', 0.05))
    keyword_w = weights.get('keyword', weights.get('keyword_weight', 0.45))

    # Calculate used and unused weight
    used_weight = 0.0
    unused_weight = 0.0

    if has_genres:
        used_weight += genre_w
    else:
        unused_weight += genre_w

    if media_type == 'movie':
        if has_directors:
            used_weight += director_w
        else:
            unused_weight += director_w
    else:  # tv
        if has_studios:
            used_weight += studio_w
        else:
            unused_weight += studio_w

    if has_actors:
        used_weight += actor_w
    else:
        unused_weight += actor_w

    if has_languages:
        used_weight += language_w
    else:
        unused_weight += language_w

    if has_keywords:
        used_weight += keyword_w
    else:
        unused_weight += keyword_w

    # If no data at all, return original weights
    if used_weight == 0:
        return weights.copy()

    # Calculate redistribution multiplier
    multiplier = (used_weight + unused_weight) / used_weight

    # Build effective weights
    effective = {}
    effective['genre'] = (genre_w * multiplier) if has_genres else 0
    effective['director'] = (director_w * multiplier) if has_directors else 0
    effective['studio'] = (studio_w * multiplier) if has_studios else 0
    effective['actor'] = (actor_w * multiplier) if has_actors else 0
    effective['language'] = (language_w * multiplier) if has_languages else 0
    effective['keyword'] = (keyword_w * multiplier) if has_keywords else 0

    return effective


def calculate_similarity_score(
    content_info: Dict,
    user_profile: Dict,
    media_type: str = 'movie',
    weights: Optional[Dict] = None,
    normalize_counters: bool = True,
    use_fuzzy_keywords: bool = True
) -> Tuple[float, Dict]:
    """
    Calculate similarity score between content and user profile.

    Unified scoring function for movies, TV shows, and external recommendations.
    Uses weighted scoring across genres, directors/studios, actors, keywords, and language.

    Args:
        content_info: Dict with content metadata (genres, directors/studio, cast, keywords, language)
        user_profile: Dict with user's weighted preferences (Counter objects or dicts)
        media_type: 'movie' or 'tv' - determines director vs studio scoring
        weights: Optional custom weights dict. Defaults to standard weights if None.
        normalize_counters: If True, use sqrt normalization for diminishing returns
        use_fuzzy_keywords: If True, use fuzzy matching for keywords

    Returns:
        Tuple of (score 0-1, breakdown dict with component scores)
    """
    # Default weights (specificity-first approach)
    # Keywords are most predictive, actors show taste, genre is baseline
    # Director/studio have low weight (most people don't pick by director)
    # Language removed (data is unreliable)
    default_weights = {
        'genre': 0.25,
        'director': 0.05,  # movies - low weight, most don't care
        'studio': 0.10,    # TV shows - networks matter slightly more
        'actor': 0.20,
        'keyword': 0.50,   # Primary driver - most specific signal
        'language': 0.0    # Disabled - data unreliable
    }
    weights = weights or default_weights

    # Weight redistribution: if user profile is missing data for a component,
    # redistribute that weight proportionally to components that have data
    effective_weights = _redistribute_weights(weights, user_profile, media_type)

    # Initialize score breakdown
    score_breakdown = {
        'genre_score': 0.0,
        'director_score': 0.0,
        'studio_score': 0.0,
        'actor_score': 0.0,
        'language_score': 0.0,
        'keyword_score': 0.0,
        'details': {
            'genres': [],
            'directors': [],
            'studio': None,
            'actors': [],
            'language': None,
            'keywords': []
        }
    }

    if not content_info or not user_profile:
        return 0.0, score_breakdown

    try:
        score = 0.0

        # Convert user profile to Counters if needed (handles both dict and Counter)
        user_prefs = {
            'genres': Counter(user_profile.get('genres', {})),
            'directors': Counter(user_profile.get('directors', {})),
            'studios': Counter(user_profile.get('studios', user_profile.get('studio', {}))),
            'actors': Counter(user_profile.get('actors', {})),
            'languages': Counter(user_profile.get('languages', {})),
            'keywords': Counter(user_profile.get('keywords', user_profile.get('tmdb_keywords', {})))
        }

        # Calculate max counts for normalization
        max_counts = {
            'genres': max(user_prefs['genres'].values()) if user_prefs['genres'] else 1,
            'directors': max(user_prefs['directors'].values()) if user_prefs['directors'] else 1,
            'studios': max(user_prefs['studios'].values()) if user_prefs['studios'] else 1,
            'actors': max(user_prefs['actors'].values()) if user_prefs['actors'] else 1,
            'languages': max(user_prefs['languages'].values()) if user_prefs['languages'] else 1,
            'keywords': max(user_prefs['keywords'].values()) if user_prefs['keywords'] else 1
        }

        # Build normalized genre lookup
        normalized_user_genres = {}
        for genre, count in user_prefs['genres'].items():
            norm_genre = normalize_genre(genre)
            if norm_genre in normalized_user_genres:
                normalized_user_genres[norm_genre] = max(normalized_user_genres[norm_genre], count)
            else:
                normalized_user_genres[norm_genre] = count
        max_genre_count = max(normalized_user_genres.values()) if normalized_user_genres else 1

        # --- Genre Score ---
        content_genres = set(content_info.get('genres', []))
        if content_genres:
            genre_scores = []
            for genre in content_genres:
                # Try normalized match first
                norm_genre = normalize_genre(genre)
                genre_count = normalized_user_genres.get(norm_genre, 0)

                # Fallback to original name
                if genre_count == 0:
                    genre_count = user_prefs['genres'].get(genre, 0)

                if genre_count > 0:
                    if normalize_counters:
                        normalized_score = math.sqrt(genre_count / max_genre_count)
                    else:
                        normalized_score = min(genre_count / max_genre_count, 1.0)
                    genre_scores.append(normalized_score)
                    score_breakdown['details']['genres'].append(
                        f"{genre} (count: {genre_count}, norm: {round(normalized_score, 2)})"
                    )
            if genre_scores:
                genre_weight = effective_weights.get('genre', 0.20)
                # Use sum with diminishing returns - genres are fewer so scale down
                # ~50% at sum=1, ~75% at sum=3, ~90% at sum=9
                genre_sum = sum(genre_scores)
                genre_ratio = 1 - (1 / (1 + genre_sum))
                genre_final = genre_ratio * genre_weight
                score += genre_final
                score_breakdown['genre_score'] = round(genre_final, 3)

        # --- Director Score (movies only) ---
        if media_type == 'movie':
            content_directors = content_info.get('directors', [])
            if content_directors:
                # Build lowercase lookup for case-insensitive matching
                user_directors_lower = {k.lower(): v for k, v in user_prefs['directors'].items()}
                director_scores = []
                for director in content_directors:
                    director_lower = director.lower() if isinstance(director, str) else director
                    director_count = user_prefs['directors'].get(director, 0)
                    if director_count == 0:
                        director_count = user_directors_lower.get(director_lower, 0)
                    if director_count > 0:
                        if normalize_counters:
                            normalized_score = math.sqrt(director_count / max_counts['directors'])
                        else:
                            normalized_score = min(director_count / max_counts['directors'], 1.0)
                        director_scores.append(normalized_score)
                        score_breakdown['details']['directors'].append(
                            f"{director} (count: {director_count}, norm: {round(normalized_score, 2)})"
                        )
                if director_scores:
                    director_weight = effective_weights.get('director', 0.15)
                    director_final = (sum(director_scores) / len(director_scores)) * director_weight
                    score += director_final
                    score_breakdown['director_score'] = round(director_final, 3)

        # --- Studio Score (TV only) ---
        if media_type == 'tv':
            content_studio = content_info.get('studio', content_info.get('studios', []))
            # Handle both single studio string and list of studios
            if isinstance(content_studio, str):
                studios_to_check = [content_studio] if content_studio and content_studio != 'N/A' else []
            else:
                studios_to_check = content_studio or []

            if studios_to_check:
                studio_scores = []
                for studio in studios_to_check:
                    studio_lower = studio.lower() if isinstance(studio, str) else studio
                    studio_count = user_prefs['studios'].get(studio_lower, 0)
                    if studio_count == 0:
                        studio_count = user_prefs['studios'].get(studio, 0)
                    if studio_count > 0:
                        if normalize_counters:
                            normalized_score = math.sqrt(studio_count / max_counts['studios'])
                        else:
                            normalized_score = min(studio_count / max_counts['studios'], 1.0)
                        studio_scores.append(normalized_score)
                        score_breakdown['details']['studio'] = f"{studio} (count: {studio_count}, norm: {round(normalized_score, 2)})"
                if studio_scores:
                    studio_weight = effective_weights.get('studio', 0.15)
                    studio_final = (sum(studio_scores) / len(studio_scores)) * studio_weight
                    score += studio_final
                    score_breakdown['studio_score'] = round(studio_final, 3)

        # --- Actor Score ---
        content_cast = content_info.get('cast', [])
        if content_cast:
            # Build lowercase lookup for case-insensitive matching
            user_actors_lower = {k.lower(): v for k, v in user_prefs['actors'].items()}
            actor_scores = []
            matched_actors = 0
            for actor in content_cast:
                actor_lower = actor.lower() if isinstance(actor, str) else actor
                actor_count = user_prefs['actors'].get(actor, 0)
                if actor_count == 0:
                    actor_count = user_actors_lower.get(actor_lower, 0)
                if actor_count > 0:
                    matched_actors += 1
                    if normalize_counters:
                        normalized_score = math.sqrt(actor_count / max_counts['actors'])
                    else:
                        normalized_score = min(actor_count / max_counts['actors'], 1.0)
                    actor_scores.append(normalized_score)
                    score_breakdown['details']['actors'].append(
                        f"{actor} (count: {actor_count}, norm: {round(normalized_score, 2)})"
                    )
            if matched_actors > 0:
                # Use sum with diminishing returns
                # ~50% at sum=1, ~75% at sum=3, ~90% at sum=9
                actor_sum = sum(actor_scores)
                actor_ratio = 1 - (1 / (1 + actor_sum))
                actor_weight = effective_weights.get('actor', 0.15)
                actor_final = actor_ratio * actor_weight
                score += actor_final
                score_breakdown['actor_score'] = round(actor_final, 3)

        # --- Language Score ---
        content_language = content_info.get('language', 'N/A')
        if content_language and content_language != 'N/A':
            lang_lower = content_language.lower()
            lang_count = user_prefs['languages'].get(lang_lower, 0)
            if lang_count > 0:
                if normalize_counters:
                    normalized_score = math.sqrt(lang_count / max_counts['languages'])
                else:
                    normalized_score = min(lang_count / max_counts['languages'], 1.0)
                language_weight = effective_weights.get('language', 0.05)
                lang_final = normalized_score * language_weight
                score += lang_final
                score_breakdown['language_score'] = round(lang_final, 3)
                score_breakdown['details']['language'] = f"{content_language} (count: {lang_count}, norm: {round(normalized_score, 2)})"

        # --- Keyword Score ---
        content_keywords = content_info.get('keywords', content_info.get('tmdb_keywords', []))
        if content_keywords:
            keyword_scores = []
            # Build lowercase keyword lookup for fuzzy matching
            user_keywords_lower = {k.lower(): v for k, v in user_prefs['keywords'].items()}

            for kw in content_keywords:
                kw_lower = kw.lower() if isinstance(kw, str) else kw
                count = user_prefs['keywords'].get(kw, 0)

                # Try lowercase match
                if count == 0:
                    count = user_keywords_lower.get(kw_lower, 0)

                # Try fuzzy matching if enabled
                if count == 0 and use_fuzzy_keywords:
                    fuzzy_count, matched_kw = fuzzy_keyword_match(kw, user_keywords_lower)
                    count = fuzzy_count

                if count > 0:
                    if normalize_counters:
                        normalized_score = math.sqrt(count / max_counts['keywords'])
                    else:
                        normalized_score = min(count / max_counts['keywords'], 1.0)
                    keyword_scores.append(normalized_score)
                    score_breakdown['details']['keywords'].append(
                        f"{kw} (count: {int(count)}, norm: {round(normalized_score, 2)})"
                    )
            if keyword_scores:
                keyword_weight = effective_weights.get('keyword', 0.45)
                # Use sum with diminishing returns instead of average
                # Scale factor of 1.0 gives: ~50% at sum=1, ~75% at sum=3, ~90% at sum=9
                keyword_sum = sum(keyword_scores)
                keyword_ratio = 1 - (1 / (1 + keyword_sum))
                keyword_final = keyword_ratio * keyword_weight
                score += keyword_final
                score_breakdown['keyword_score'] = round(keyword_final, 3)

        # Per-item weight redistribution: if a component scored 0 (no matches),
        # redistribute its weight proportionally to components that did score
        component_scores = {
            'genre': score_breakdown['genre_score'],
            'director': score_breakdown['director_score'],
            'studio': score_breakdown['studio_score'],
            'actor': score_breakdown['actor_score'],
            'language': score_breakdown['language_score'],
            'keyword': score_breakdown['keyword_score']
        }

        active_weights = {}
        lost_weight = 0.0

        for comp, comp_score in component_scores.items():
            weight = effective_weights.get(comp, 0)
            if weight > 0:
                if comp_score > 0:
                    # This component contributed - track its weight and ratio
                    active_weights[comp] = (weight, comp_score / weight if weight > 0 else 0)
                else:
                    # This component had weight but no matches - weight is "lost"
                    lost_weight += weight

        # Redistribute lost weight to active components proportionally
        if lost_weight > 0 and active_weights:
            total_active_weight = sum(w for w, _ in active_weights.values())
            if total_active_weight > 0:
                for comp, (weight, ratio) in active_weights.items():
                    extra_weight = lost_weight * (weight / total_active_weight)
                    extra_score = extra_weight * ratio
                    score += extra_score

        # Cap at 1.0 (100%)
        score = min(score, 1.0)

        return score, score_breakdown

    except Exception as e:
        logging.warning(f"Error calculating similarity score for {content_info.get('title', 'Unknown')}: {e}")
        return 0.0, score_breakdown


# ------------------------------------------------------------------------
# CONSOLIDATED UTILITY FUNCTIONS (moved from movie/tv recommenders)
# ------------------------------------------------------------------------

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
            # Handle Genre objects (plexapi.media.Genre)
            if hasattr(genre, 'tag'):
                genres.append(genre.tag.lower())
            elif isinstance(genre, str):
                genres.append(genre.lower())
    except Exception:
        pass
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


def fetch_tmdb_with_retry(url: str, params: Dict, max_retries: int = 3, timeout: int = 15) -> Optional[Dict]:
    """
    Fetch from TMDB API with retry logic and rate limit handling.

    Args:
        url: TMDB API endpoint URL
        params: Query parameters (must include api_key)
        max_retries: Maximum retry attempts (default 3)
        timeout: Request timeout in seconds (default 15)

    Returns:
        JSON response dict or None on failure
    """
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)

            if resp.status_code == 429:
                sleep_time = 2 * (attempt + 1)
                logging.warning(f"TMDB rate limit hit, waiting {sleep_time}s...")
                time.sleep(sleep_time)
                continue

            if resp.status_code == 200:
                return resp.json()

            logging.debug(f"TMDB request failed with status {resp.status_code}")
            return None

        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            logging.warning(f"TMDB connection error, retrying... ({attempt+1}/{max_retries})")
            time.sleep(1)
            if attempt == max_retries - 1:
                logging.warning(f"TMDB request failed after {max_retries} tries: {e}")
        except Exception as e:
            logging.warning(f"TMDB request error: {e}")
            return None

    return None


def get_tmdb_id_for_item(item, tmdb_api_key: str, media_type: str = 'movie', cache: Dict = None) -> Optional[int]:
    """
    Get TMDB ID for a Plex item using multiple fallback methods.

    Args:
        item: Plex media item
        tmdb_api_key: TMDB API key
        media_type: 'movie' or 'tv'
        cache: Optional dict to check/store cached lookups

    Returns:
        TMDB ID as integer or None
    """
    # Check cache first
    cache_key = str(getattr(item, 'ratingKey', None))
    if cache and cache_key in cache:
        return cache[cache_key]

    # Method 1: Extract from Plex GUIDs
    ids = extract_ids_from_guids(item)
    if ids['tmdb_id']:
        if cache is not None:
            cache[cache_key] = ids['tmdb_id']
        return ids['tmdb_id']

    # Method 2: Search TMDB API
    if tmdb_api_key:
        title = getattr(item, 'title', '')
        year = getattr(item, 'year', None)

        search_url = f"https://api.themoviedb.org/3/search/{media_type}"
        params = {
            'api_key': tmdb_api_key,
            'query': title,
            'include_adult': False
        }

        # Add year parameter (different field name for TV)
        if year:
            if media_type == 'movie':
                params['year'] = year
            else:
                params['first_air_date_year'] = year

        data = fetch_tmdb_with_retry(search_url, params)
        if data and data.get('results'):
            tmdb_id = data['results'][0]['id']
            if cache is not None:
                cache[cache_key] = tmdb_id
            return tmdb_id

    # Method 3: Try via IMDb ID if available
    if ids['imdb_id'] and tmdb_api_key:
        find_url = f"https://api.themoviedb.org/3/find/{ids['imdb_id']}"
        params = {'api_key': tmdb_api_key, 'external_source': 'imdb_id'}
        data = fetch_tmdb_with_retry(find_url, params)
        if data:
            results_key = 'movie_results' if media_type == 'movie' else 'tv_results'
            if data.get(results_key):
                tmdb_id = data[results_key][0]['id']
                if cache is not None:
                    cache[cache_key] = tmdb_id
                return tmdb_id

    return None


def get_tmdb_keywords(tmdb_api_key: str, tmdb_id: int, media_type: str = 'movie', cache: Dict = None) -> List[str]:
    """
    Get keywords for a TMDB item.

    Args:
        tmdb_api_key: TMDB API key
        tmdb_id: TMDB ID
        media_type: 'movie' or 'tv'
        cache: Optional dict to check/store cached keywords

    Returns:
        List of lowercase keyword strings
    """
    if not tmdb_id or not tmdb_api_key:
        return []

    # Check cache
    cache_key = str(tmdb_id)
    if cache and cache_key in cache:
        return list(cache[cache_key])

    media = 'movie' if media_type == 'movie' else 'tv'
    url = f"https://api.themoviedb.org/3/{media}/{tmdb_id}/keywords"
    params = {'api_key': tmdb_api_key}

    data = fetch_tmdb_with_retry(url, params)
    if data:
        # Movies use 'keywords', TV uses 'results'
        keywords_list = data.get('keywords', data.get('results', []))
        keywords = [k['name'].lower() for k in keywords_list if 'name' in k]

        if cache is not None and keywords:
            cache[cache_key] = keywords

        return keywords

    return []


def adapt_config_for_media_type(root_config: Dict, media_type: str = 'movies') -> Dict:
    """
    Convert root config.yml format to legacy format for a specific media type.

    Args:
        root_config: The root configuration dictionary
        media_type: 'movies' or 'tv'

    Returns:
        Adapted configuration dictionary
    """
    is_movie = media_type == 'movies'

    # Defaults differ by media type
    default_limit = 50 if is_movie else 20
    default_normalize = False if is_movie else True
    library_key = 'movie_library_title' if is_movie else 'TV_library_title'
    library_source = 'movie_library' if is_movie else 'tv_library'
    library_default = 'Movies' if is_movie else 'TV Shows'

    adapted = {
        'general': {
            'confirm_operations': root_config.get('general', {}).get('confirm_operations', False),
            'plex_only': root_config.get('general', {}).get('plex_only', True),
            'combine_watch_history': root_config.get('general', {}).get('combine_watch_history', False),
            'log_retention_days': root_config.get('general', {}).get('log_retention_days', 7),
            'limit_plex_results': root_config.get(media_type, {}).get('limit_results', default_limit),
            'exclude_genre': root_config.get('general', {}).get('exclude_genre', None),
            'randomize_recommendations': root_config.get(media_type, {}).get('randomize_recommendations', False),
            'normalize_counters': root_config.get(media_type, {}).get('normalize_counters', default_normalize),
            'show_summary': root_config.get(media_type, {}).get('show_summary', True),
            'show_cast': root_config.get(media_type, {}).get('show_cast', True),
            'show_language': root_config.get(media_type, {}).get('show_language', True),
            'show_rating': root_config.get(media_type, {}).get('show_rating', True),
            'show_imdb_link': root_config.get(media_type, {}).get('show_imdb_link', True),
        },
        'plex': {
            'url': root_config.get('plex', {}).get('url', ''),
            'token': root_config.get('plex', {}).get('token', ''),
            library_key: root_config.get('plex', {}).get(library_source, library_default),
            'managed_users': root_config.get('plex', {}).get('managed_users', 'Admin'),
        },
        'collections': {
            'add_label': root_config.get('collections', {}).get('add_label', True),
            'label_name': root_config.get('collections', {}).get('label_name', 'Recommended'),
            'append_usernames': root_config.get('collections', {}).get('append_usernames', True),
            'remove_previous_recommendations': root_config.get('collections', {}).get('remove_previous_recommendations', False),
            'stale_removal_days': root_config.get('collections', {}).get('stale_removal_days', 7),
        },
        'TMDB': get_tmdb_config(root_config),
        'plex_users': {
            'users': root_config.get('users', {}).get('list', ''),
        },
        'user_preferences': root_config.get('users', {}).get('preferences', {}),
        'weights': root_config.get(media_type, {}).get('weights', {}),
        'quality_filters': root_config.get(media_type, {}).get('quality_filters', {}),
        'recency_decay': root_config.get('recency_decay', {}),
        'paths': root_config.get('platform', {}),
    }

    # Movie-specific options
    if is_movie:
        adapted['general']['show_director'] = root_config.get(media_type, {}).get('show_director', True)
        adapted['general']['show_genres'] = root_config.get(media_type, {}).get('show_genres', True)

    return adapted


def save_json_cache(cache_path: str, data: Dict, cache_version: int = None) -> bool:
    """
    Save data to a JSON cache file.

    Args:
        cache_path: Path to the cache file
        data: Dictionary to save
        cache_version: Optional version number to include

    Returns:
        True on success, False on failure
    """
    try:
        if cache_version is not None:
            data['cache_version'] = cache_version
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        logging.error(f"Error saving cache to {cache_path}: {e}")
        return False


def load_json_cache(cache_path: str) -> Optional[Dict]:
    """
    Load data from a JSON cache file.

    Args:
        cache_path: Path to the cache file

    Returns:
        Dictionary from cache or None on failure
    """
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"Error loading cache from {cache_path}: {e}")
        return None


def user_select_recommendations(recommendations: List[Dict], operation_label: str) -> List[Dict]:
    """
    Prompt user to select which recommendations to process.

    Args:
        recommendations: List of recommendation dictionaries
        operation_label: Description of the operation (e.g., "label in Plex")

    Returns:
        List of selected recommendations (may be empty, subset, or all)
    """
    import re
    prompt = (
        f"\nWhich recommendations would you like to {operation_label}?\n"
        "Enter 'all' or 'y' to select ALL,\n"
        "Enter 'none' or 'n' to skip them,\n"
        "Or enter a comma-separated list of numbers (e.g. 1,3,5). "
        "\nYour choice: "
    )
    choice = input(prompt).strip().lower()

    if choice in ("n", "no", "none", ""):
        log_warning(f"Skipping {operation_label} as per user choice.")
        return []
    if choice in ("y", "yes", "all"):
        return recommendations

    indices_str = re.split(r'[,\s]+', choice)
    chosen = []
    for idx_str in indices_str:
        idx_str = idx_str.strip()
        if not idx_str.isdigit():
            log_warning(f"Skipping invalid index: {idx_str}")
            continue
        idx = int(idx_str)
        if 1 <= idx <= len(recommendations):
            chosen.append(idx)
        else:
            log_warning(f"Skipping out-of-range index: {idx}")

    if not chosen:
        log_warning(f"No valid indices selected, skipping {operation_label}.")
        return []

    return [recommendations[c - 1] for c in chosen]


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
            # Try userRating first (personal rating)
            if hasattr(item, 'userRating') and item.userRating:
                return float(item.userRating)
            # Then try audienceRating (community rating)
            if hasattr(item, 'audienceRating') and item.audienceRating:
                return float(item.audienceRating)
        else:
            # Prefer audience rating
            if hasattr(item, 'audienceRating') and item.audienceRating:
                return float(item.audienceRating)
            if hasattr(item, 'userRating') and item.userRating:
                return float(item.userRating)

        # Finally check ratings collection
        if hasattr(item, 'ratings'):
            for rating in item.ratings:
                if hasattr(rating, 'value') and rating.value:
                    if (getattr(rating, 'image', '') == 'imdb://image.rating' or
                        getattr(rating, 'type', '') == 'audience'):
                        try:
                            return float(rating.value)
                        except (ValueError, AttributeError):
                            pass
    except Exception:
        pass
    return 0.0


def format_media_output(
    media: Dict,
    media_type: str = 'movie',
    show_summary: bool = False,
    index: Optional[int] = None,
    show_cast: bool = False,
    show_director: bool = False,
    show_language: bool = False,
    show_rating: bool = False,
    show_genres: bool = True,
    show_imdb_link: bool = False
) -> str:
    """
    Format media item for display output.

    Args:
        media: Dictionary containing media info
        media_type: 'movie' or 'tv'
        show_summary: Include summary in output
        index: Optional index number for list display
        show_cast: Include cast in output
        show_director: Include director in output (movies only)
        show_language: Include language in output
        show_rating: Include rating in output
        show_genres: Include genres in output
        show_imdb_link: Include IMDb link in output

    Returns:
        Formatted string for display
    """
    bullet = f"{index}. " if index is not None else "- "
    output = f"{bullet}{CYAN}{media['title']}{RESET} ({media.get('year', 'N/A')})"

    if 'similarity_score' in media:
        score_percentage = round(media['similarity_score'] * 100, 1)
        output += f" - Similarity: {YELLOW}{score_percentage}%{RESET}"

    if show_genres and media.get('genres'):
        output += f"\n  {YELLOW}Genres:{RESET} {', '.join(media['genres'])}"

    if show_summary and media.get('summary'):
        output += f"\n  {YELLOW}Summary:{RESET} {media['summary']}"

    if show_cast and media.get('cast'):
        output += f"\n  {YELLOW}Cast:{RESET} {', '.join(media['cast'])}"

    # Director is typically for movies
    if show_director and media.get('directors'):
        if isinstance(media['directors'], list):
            output += f"\n  {YELLOW}Director:{RESET} {', '.join(media['directors'])}"
        else:
            output += f"\n  {YELLOW}Director:{RESET} {media['directors']}"

    if show_language and media.get('language') != "N/A":
        output += f"\n  {YELLOW}Language:{RESET} {media['language']}"

    if show_rating and media.get('ratings', {}).get('audience_rating', 0) > 0:
        rating = media['ratings']['audience_rating']
        output += f"\n  {YELLOW}Rating:{RESET} {rating}/10"

    if show_imdb_link and media.get('imdb_id'):
        imdb_link = f"https://www.imdb.com/title/{media['imdb_id']}/"
        output += f"\n  {YELLOW}IMDb Link:{RESET} {imdb_link}"

    return output


def build_label_name(base_label: str, users: List[str], single_user: str = None, append_usernames: bool = True) -> str:
    """
    Build a label name with optional username suffix.

    Args:
        base_label: Base label name (e.g., 'Recommended')
        users: List of usernames
        single_user: Optional single user override
        append_usernames: Whether to append usernames to label

    Returns:
        Final label name
    """
    import re
    if not append_usernames:
        return base_label

    if single_user:
        user_suffix = re.sub(r'\W+', '_', single_user.strip())
        return f"{base_label}_{user_suffix}"
    elif users:
        sanitized_users = [re.sub(r'\W+', '_', user.strip()) for user in users]
        user_suffix = '_'.join(sanitized_users)
        return f"{base_label}_{user_suffix}"
    return base_label


def categorize_labeled_items(
    labeled_items: List,
    watched_ids: set,
    excluded_genres: List[str],
    label_name: str,
    label_dates: Dict,
    stale_days: int = 7
) -> Dict[str, List]:
    """
    Categorize labeled items into watched, stale, excluded, and fresh.

    Args:
        labeled_items: List of Plex items with the label
        watched_ids: Set of watched item IDs
        excluded_genres: List of genres to exclude
        label_name: Name of the label
        label_dates: Dictionary tracking when labels were added
        stale_days: Number of days before an unwatched item is considered stale

    Returns:
        Dictionary with keys: 'fresh', 'watched', 'stale', 'excluded'
    """
    from datetime import datetime, timedelta

    stale_threshold = datetime.now() - timedelta(days=stale_days)

    result = {
        'fresh': [],
        'watched': [],
        'stale': [],
        'excluded': []
    }

    for item in labeled_items:
        item.reload()
        item_id = int(item.ratingKey)
        label_key = f"{item_id}_{label_name}"

        # Check if item has excluded genres
        item_genres = [g.tag.lower() for g in item.genres] if hasattr(item, 'genres') else []
        if any(g in excluded_genres for g in item_genres):
            result['excluded'].append(item)
            continue

        # Check if watched
        if item_id in watched_ids:
            result['watched'].append(item)
            continue

        # Check if stale
        label_date_str = label_dates.get(label_key)
        if label_date_str:
            try:
                label_date = datetime.fromisoformat(label_date_str)
                if label_date < stale_threshold:
                    result['stale'].append(item)
                    continue
            except (ValueError, TypeError):
                pass

        # Item is fresh - track date if not already tracked
        result['fresh'].append(item)
        if not label_date_str:
            label_dates[label_key] = datetime.now().isoformat()

    return result


def remove_labels_from_items(items: List, label_name: str, label_dates: Dict, reason: str = "") -> None:
    """
    Remove labels from a list of Plex items.

    Args:
        items: List of Plex items
        label_name: Name of the label to remove
        label_dates: Dictionary tracking label dates (will be updated)
        reason: Reason for removal (for logging)
    """
    for item in items:
        item.removeLabel(label_name)
        label_key = f"{int(item.ratingKey)}_{label_name}"
        if label_key in label_dates:
            del label_dates[label_key]
        if reason:
            log_warning(f"Removed ({reason}): {item.title}")


def add_labels_to_items(items: List, label_name: str, label_dates: Dict) -> int:
    """
    Add labels to a list of Plex items.

    Args:
        items: List of Plex items
        label_name: Name of the label to add
        label_dates: Dictionary tracking label dates (will be updated)

    Returns:
        Number of items that had labels added
    """
    from datetime import datetime

    added_count = 0
    for item in items:
        current_labels = [label.tag for label in item.labels]
        if label_name not in current_labels:
            item.addLabel(label_name)
            label_key = f"{int(item.ratingKey)}_{label_name}"
            label_dates[label_key] = datetime.now().isoformat()
            print(f"{GREEN}Added: {item.title}{RESET}")
            added_count += 1
    return added_count


def get_library_imdb_ids(plex_section) -> set:
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
    except Exception as e:
        log_warning(f"Error retrieving IMDb IDs from library: {e}")
    return imdb_ids


def print_similarity_breakdown(media_info: Dict, score: float, breakdown: Dict, media_type: str = 'movie') -> None:
    """
    Print detailed breakdown of similarity score calculation.

    Args:
        media_info: Dictionary containing media info (must have 'title')
        score: Overall similarity score
        breakdown: Dictionary with score components and details
        media_type: 'movie' or 'tv' (affects which scores are shown)
    """
    print(f"\n{CYAN}Similarity Score Breakdown for '{media_info['title']}'{RESET}")
    print(f"Total Score: {round(score * 100, 1)}%")
    print(f"├─ Genre Score: {round(breakdown['genre_score'] * 100, 1)}%")
    if breakdown['details']['genres']:
        print(f"│  └─ Matching genres: {', '.join(breakdown['details']['genres'])}")

    # Movie uses director, TV uses studio
    if media_type == 'movie':
        print(f"├─ Director Score: {round(breakdown.get('director_score', 0) * 100, 1)}%")
        if breakdown['details'].get('directors'):
            print(f"│  └─ Director match: {', '.join(breakdown['details']['directors'])}")
    else:
        print(f"├─ Studio Score: {round(breakdown.get('studio_score', 0) * 100, 1)}%")
        if breakdown['details'].get('studio'):
            print(f"│  └─ Studio match: {breakdown['details']['studio']}")

    print(f"├─ Actor Score: {round(breakdown['actor_score'] * 100, 1)}%")
    if breakdown['details']['actors']:
        print(f"│  └─ Matching actors: {', '.join(breakdown['details']['actors'])}")
    print(f"├─ Language Score: {round(breakdown['language_score'] * 100, 1)}%")
    if breakdown['details']['language']:
        print(f"│  └─ Language match: {breakdown['details']['language']}")
    print(f"└─ Keyword Score: {round(breakdown['keyword_score'] * 100, 1)}%")
    if breakdown['details']['keywords']:
        print(f"   └─ Matching keywords: {', '.join(breakdown['details']['keywords'])}")
    print("")


def process_counters_from_cache(
    media_info: Dict,
    counters: Dict,
    multiplier: float = 1.0,
    media_type: str = 'movie',
    plex_tmdb_cache: Dict = None,
    tmdb_keywords_cache: Dict = None,
    cache_lookup: Dict = None
) -> None:
    """
    Process media info from cache and update counters.

    Args:
        media_info: Dictionary with media metadata
        counters: Counter dictionary to update
        multiplier: Rating/rewatch multiplier
        media_type: 'movie' or 'tv'
        plex_tmdb_cache: Optional cache for Plex->TMDB mappings
        tmdb_keywords_cache: Optional cache for TMDB keywords
        cache_lookup: Optional dict to find show_id from title/year
    """
    try:
        # Get rating-based multiplier
        rating = float(media_info.get('user_rating', 0))
        if not rating:
            rating = float(media_info.get('audience_rating', 5.0))
        rating = max(0, min(10, int(round(rating))))
        final_multiplier = RATING_MULTIPLIERS.get(rating, 1.0) * multiplier

        # Process genres
        for genre in media_info.get('genres', []):
            counters['genres'][genre] += final_multiplier

        # Process studio/director based on media type
        if media_type == 'movie':
            for director in media_info.get('directors', []):
                counters['directors'][director] += final_multiplier
        else:
            if studio := media_info.get('studio'):
                counters['studio'][studio.lower()] += final_multiplier

        # Process actors
        for actor in media_info.get('cast', [])[:3]:
            counters['actors'][actor] += final_multiplier

        # Process language
        if language := media_info.get('language'):
            counters['languages'][language.lower()] += final_multiplier

        # Process TMDB keywords and update caches
        if tmdb_id := media_info.get('tmdb_id'):
            if plex_tmdb_cache is not None and cache_lookup is not None:
                # Find the item_id from cache
                item_id = next((k for k, v in cache_lookup.items()
                              if v.get('title') == media_info['title'] and
                              v.get('year') == media_info.get('year')), None)
                if item_id:
                    plex_tmdb_cache[str(item_id)] = tmdb_id

            if keywords := media_info.get('tmdb_keywords', []):
                if tmdb_keywords_cache is not None:
                    tmdb_keywords_cache[str(tmdb_id)] = keywords
                counters['tmdb_keywords'].update({k: final_multiplier for k in keywords})

    except Exception as e:
        log_warning(f"Error processing counters for {media_info.get('title')}: {e}")


def load_media_cache(cache_path: str, media_key: str = 'movies') -> Dict:
    """
    Load media cache from file with version checking.

    Args:
        cache_path: Path to the cache file
        media_key: Key for media items ('movies' or 'shows')

    Returns:
        Cache dictionary with media items, or empty structure if invalid/missing
    """
    empty_cache = {media_key: {}, 'last_updated': None, 'library_count': 0, 'cache_version': CACHE_VERSION}

    if not check_cache_version(cache_path, f"{media_key.title()} cache"):
        return empty_cache

    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_warning(f"Error loading {media_key} cache: {e}")
            return empty_cache
    return empty_cache


def save_media_cache(cache_path: str, cache_data: Dict, media_key: str = 'movies') -> bool:
    """
    Save media cache to file.

    Args:
        cache_path: Path to the cache file
        cache_data: Cache dictionary to save
        media_key: Key for media items (for logging)

    Returns:
        True on success, False on failure
    """
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        log_warning(f"Error saving {media_key} cache: {e}")
        return False


def create_empty_counters(media_type: str = 'movie') -> Dict:
    """
    Create empty counter structure for tracking watched media preferences.

    Args:
        media_type: 'movie' or 'tv'

    Returns:
        Dictionary with Counter objects for each category
    """
    from collections import Counter
    counters = {
        'genres': Counter(),
        'actors': Counter(),
        'languages': Counter(),
        'tmdb_keywords': Counter(),
        'tmdb_ids': set()
    }
    # Movies use directors, TV uses studio
    if media_type == 'movie':
        counters['directors'] = Counter()
    else:
        counters['studio'] = Counter()
    return counters


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
    except Exception as e:
        log_warning(f"Error getting Plex user IDs: {e}")
    return user_ids


def save_watched_cache(
    cache_path: str,
    watched_data_counters: Dict,
    plex_tmdb_cache: Dict,
    tmdb_keywords_cache: Dict,
    watched_ids: set,
    label_dates: Dict,
    watched_count: int,
    media_type: str = 'movie'
) -> bool:
    """
    Save watched data cache to file.

    Args:
        cache_path: Path to save cache
        watched_data_counters: Counter data for preferences
        plex_tmdb_cache: Plex to TMDB ID mappings
        tmdb_keywords_cache: TMDB keywords cache
        watched_ids: Set of watched item IDs
        label_dates: Label date tracking dict
        watched_count: Count of watched items
        media_type: 'movie' or 'tv'

    Returns:
        True on success, False on failure
    """
    import copy
    from datetime import datetime

    try:
        # Create a copy for serialization
        watched_data_for_cache = copy.deepcopy(watched_data_counters)

        # Convert any set objects to lists for JSON serialization
        if 'tmdb_ids' in watched_data_for_cache and isinstance(watched_data_for_cache['tmdb_ids'], set):
            watched_data_for_cache['tmdb_ids'] = list(watched_data_for_cache['tmdb_ids'])

        # Build cache data structure
        id_key = 'watched_movie_ids' if media_type == 'movie' else 'watched_show_ids'
        cache_data = {
            'cache_version': CACHE_VERSION,
            'watched_count': watched_count,
            'watched_data_counters': watched_data_for_cache,
            'plex_tmdb_cache': {str(k): v for k, v in plex_tmdb_cache.items()},
            'tmdb_keywords_cache': {str(k): v for k, v in tmdb_keywords_cache.items()},
            id_key: list(watched_ids),
            'label_dates': label_dates,
            'last_updated': datetime.now().isoformat()
        }

        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=4, ensure_ascii=False)

        logging.debug(f"Saved watched cache: {watched_count} {media_type}s, {len(watched_ids)} IDs")
        return True

    except Exception as e:
        logging.error(f"Error saving watched cache: {e}")
        return False
