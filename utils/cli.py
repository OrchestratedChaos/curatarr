"""
Shared CLI utilities for Curatarr recommenders.
Provides common entry point logic for movie and TV recommenders.
"""

import argparse
import copy
import os
import sys
import traceback
from datetime import datetime
from typing import Callable, Dict, List, Optional, Type

import yaml
from plexapi.myplex import MyPlexAccount

from .config import __version__
from .display import (
    CYAN, GREEN, RED, RESET,
    TeeLogger,
    log_error, log_warning,
    setup_logging,
)
from .helpers import cleanup_old_logs, get_project_root


def get_users_from_config(config: Dict) -> List[str]:
    """
    Extract user list from config with fallback through legacy formats.

    Checks in order:
    1. users.list (new format)
    2. plex_users.users (legacy)
    3. plex.managed_users (oldest)

    Args:
        config: Adapted config dict

    Returns:
        List of usernames to process
    """
    all_users = []

    # Check users.list first (new config format)
    users_config = config.get('users', {})
    user_list = users_config.get('list', '')
    if user_list:
        if isinstance(user_list, str):
            all_users = [u.strip() for u in user_list.split(',') if u.strip()]
        elif isinstance(user_list, list):
            all_users = user_list

    # Fall back to plex_users.users (legacy format)
    if not all_users:
        plex_config = config.get('plex_users', {})
        plex_users = plex_config.get('users')
        if plex_users and str(plex_users).lower() != 'none':
            if isinstance(plex_users, str):
                all_users = [u.strip() for u in plex_users.split(',') if u.strip()]
            elif isinstance(plex_users, list):
                all_users = plex_users

    # Fall back to plex.managed_users (oldest format)
    if not all_users:
        managed_users = config.get('plex', {}).get('managed_users', '')
        if managed_users:
            all_users = [u.strip() for u in managed_users.split(',') if u.strip()]

    return all_users


def resolve_admin_username(user: str, plex_token: str) -> str:
    """
    Resolve 'Admin' or 'Administrator' to actual Plex account username.

    Args:
        user: Username to check
        plex_token: Plex authentication token

    Returns:
        Resolved username (original if not admin or resolution fails)
    """
    if user.lower() not in ['admin', 'administrator']:
        return user

    try:
        account = MyPlexAccount(token=plex_token)
        admin_username = account.username
        log_warning(f"Resolved Admin to: {admin_username}")
        return admin_username
    except Exception as e:
        log_warning(f"Could not resolve admin username: {e}")
        return user


def update_config_for_user(config: Dict, resolved_user: str) -> Dict:
    """
    Create a config copy with user set appropriately.

    Args:
        config: Base config dict
        resolved_user: Resolved username

    Returns:
        Deep copy of config with user settings updated
    """
    user_config = copy.deepcopy(config)

    if 'managed_users' in user_config.get('plex', {}):
        user_config['plex']['managed_users'] = resolved_user
    elif 'users' in user_config.get('plex_users', {}):
        user_config['plex_users']['users'] = [resolved_user]

    return user_config


def setup_log_file(log_dir: str, log_retention_days: int,
                   single_user: Optional[str] = None,
                   media_type: str = 'recommendations') -> bool:
    """
    Set up log file with TeeLogger for capturing output.

    Args:
        log_dir: Directory for log files
        log_retention_days: Days to retain logs (0 = don't log to file)
        single_user: Optional username suffix for log file
        media_type: Media type prefix for log file

    Returns:
        True if logging was set up, False otherwise
    """
    if log_retention_days <= 0:
        return False

    try:
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        user_suffix = f"_{single_user}" if single_user else ""
        log_file_path = os.path.join(log_dir, f"{media_type}{user_suffix}_{timestamp}.log")
        lf = open(log_file_path, "w", encoding="utf-8")
        sys.stdout = TeeLogger(lf)
        cleanup_old_logs(log_dir, log_retention_days)
        return True
    except Exception as e:
        log_error(f"Could not set up logging: {e}")
        return False


def teardown_log_file(original_stdout, log_retention_days: int):
    """
    Clean up log file and restore stdout.

    Args:
        original_stdout: Original sys.stdout to restore
        log_retention_days: If > 0 and stdout was redirected, close log
    """
    if log_retention_days > 0 and sys.stdout is not original_stdout:
        try:
            sys.stdout.logfile.close()
            sys.stdout = original_stdout
        except Exception as e:
            log_warning(f"Error closing log file: {e}")


def print_runtime(start_time: datetime):
    """Print formatted runtime duration."""
    runtime = datetime.now() - start_time
    hours = runtime.seconds // 3600
    minutes = (runtime.seconds % 3600) // 60
    seconds = runtime.seconds % 60
    print(f"\n{GREEN}All processing completed!{RESET}")
    print(f"Total runtime: {hours:02d}:{minutes:02d}:{seconds:02d}")


def run_recommender_main(
    media_type: str,
    description: str,
    adapt_config_func: Callable[[Dict], Dict],
    process_func: Callable[[Dict, str, int, Optional[str]], None]
):
    """
    Common main entry point for recommenders.

    Args:
        media_type: 'Movie' or 'TV Show' for display
        description: argparse description
        adapt_config_func: Function to adapt root config to media-specific format
        process_func: Function to process recommendations for a user
    """
    # Ensure UTF-8 output
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

    # Parse command line arguments
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('username', nargs='?', help='Process recommendations for only this user')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    start_time = datetime.now()
    print(f"{CYAN}{media_type} Recommendations for Plex v{__version__}{RESET}")
    print("-" * 50)

    # Load config from project root
    project_root = get_project_root()
    config_path = os.path.join(project_root, 'config/config.yml')

    try:
        with open(config_path, 'r') as f:
            root_config = yaml.safe_load(f)
        base_config = adapt_config_func(root_config)
    except Exception as e:
        log_error(f"Could not load config.yml from project root: {e}")
        log_warning(f"Looking for config at: {config_path}")
        sys.exit(1)

    # Setup logging
    logger = setup_logging(debug=args.debug, config=root_config)
    logger.debug("Debug logging enabled")

    general = base_config.get('general', {})
    log_retention_days = general.get('log_retention_days', 7)

    # Handle single user mode
    single_user = args.username
    if single_user:
        log_warning(f"Single user mode: {single_user}")

    # Get users to process
    all_users = get_users_from_config(base_config)
    if single_user:
        all_users = [single_user]

    if not all_users:
        log_error("No users configured. Please configure plex_users or managed_users in config.yml")
        sys.exit(1)

    # Process each user
    plex_token = base_config.get('plex', {}).get('token', '')
    for user in all_users:
        print(f"\n{GREEN}Processing recommendations for user: {user}{RESET}")
        print("-" * 50)

        resolved_user = resolve_admin_username(user, plex_token)
        user_config = update_config_for_user(base_config, resolved_user)

        process_func(user_config, config_path, log_retention_days, resolved_user)

        print(f"\n{GREEN}Completed processing for user: {resolved_user}{RESET}")
        print("-" * 50)

    print_runtime(start_time)
