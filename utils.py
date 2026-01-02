"""
Shared utilities for Plex recommendation scripts (Movies and TV Shows)
Contains common functions for account management and watch history fetching
"""

import os
import sys
import re
import math
import logging
import requests
import yaml
import xml.etree.ElementTree as ET
import urllib3
import plexapi.server
from plexapi.myplex import MyPlexAccount
from datetime import datetime, timedelta, timezone

# Suppress SSL warnings for self-signed Plex certificates (common for home servers)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
