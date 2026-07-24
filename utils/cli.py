"""
Shared CLI utilities for Curatarr recommenders.
Provides common entry point logic for movie and TV recommenders.
"""

import argparse
import copy
import os
import sys
from datetime import datetime
from typing import Callable, Dict, List, Optional

import yaml
from plexapi.myplex import MyPlexAccount

from .config import __version__, get_libraries_for_media_type, get_update_mode
from .display import (
    CYAN, GREEN, RESET, YELLOW,
    TeeLogger,
    log_error, log_warning,
    setup_logging,
)
from .helpers import cleanup_old_logs, get_project_root
from .update_check import GITHUB_RELEASES_PAGE, update_available
from .user_migration import migrate_renamed_plex_users


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


def print_update_notice(update_mode: str) -> None:
    """
    Print a one-line advisory notice if a newer release is available.

    This is the only update signal that reaches BINARY users via the
    CLI - a packaged/frozen build never runs run.sh/run.ps1's own
    git-based update check (see curatarr_app.py), so this notice plus
    the web UI banner (web/app.py) are the only places binary installs
    learn an update exists at all. As of v2.8.29, a binary install can
    also self-update in place (see utils/self_update.py and
    curatarr_app.py's `--self-update` dispatch) - this notice's
    frozen-branch message points at that flag first.

    Fails open by construction: utils.update_check.update_available()
    never raises, so a broken/offline check just means no notice gets
    printed - it can never block or break a run. update_mode == 'off'
    is handled by update_available() itself (skips the network call
    entirely), checked again here for clarity at the call site.

    Args:
        update_mode: 'notify' | 'force' | 'off' (see
            utils.config.get_update_mode)
    """
    if update_mode == 'off':
        return

    latest, current, is_newer = update_available(update_mode=update_mode)
    if not is_newer:
        return

    if os.environ.get('RUNNING_IN_DOCKER') == 'true':
        # Container image - there's no on-disk .git to check out
        # against and no frozen binary to swap; the run.sh/run.ps1 and
        # curatarr --self-update paths below are both gated off in
        # Docker (see run.sh's --check-verified-update/
        # --apply-verified-update and web/update_apply.py's
        # UpdateManager.begin_update). Updating means pulling a new
        # image tag instead - see docs/DOCKER.md.
        print(
            f"{YELLOW}Update available: v{latest} (you have v{current}) - "
            f"pull the new image: docker pull ghcr.io/orchestratedchaos/curatarr:v{latest}{RESET}"
        )
    elif getattr(sys, 'frozen', False):
        # Binary install - self-update in place (verified download/
        # swap - see utils/self_update.py), or download manually.
        print(
            f"{YELLOW}Update available: v{latest} (you have v{current}) - "
            f"run 'curatarr --self-update' or download: {GITHUB_RELEASES_PAGE}{RESET}"
        )
    else:
        # Source install - the real (signature-verified) update path
        # lives in run.sh/run.ps1, not here.
        print(
            f"{YELLOW}Update available: v{latest} (you have v{current}) - "
            f"restart via run.sh/run.ps1 to update"
            f"{' (or set general.update_mode: force to auto-update)' if update_mode == 'notify' else ''}"
            f"{RESET}"
        )


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
    process_func: Callable[[Dict, str, int, Optional[str], Optional[Dict]], None],
    media_type_key: str = 'movie'
):
    """
    Common main entry point for recommenders.

    Loops library-outer, user-inner (#157 Phase 3): for each configured
    library matching media_type_key, process every user against that
    library. Single-library installs (no 'libraries:' config, or exactly
    one library of this media type) synthesize a single library entry, so
    this collapses back to the original one-loop-per-user behavior.

    Args:
        media_type: 'Movie' or 'TV Show' for display
        description: argparse description
        adapt_config_func: Function to adapt root config to media-specific format
        process_func: Function to process recommendations for a user. Receives
            (user_config, config_path, log_retention_days, resolved_user, library)
        media_type_key: 'movie' or 'tv' - selects which libraries to loop over
            (see utils.config.get_libraries_for_media_type)
    """
    # Ensure UTF-8 output
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

    # Parse command line arguments
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('username', nargs='?', help='Process recommendations for only this user')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--library', dest='library_id', default=None,
                         help='Process recommendations for only this library id')
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

        # Detect Plex account renames (keyed by stable id) and migrate any
        # affected preferences/cache files/collections before this run
        # processes users. Best-effort - never blocks a normal run.
        cache_dir = os.path.join(project_root, 'cache')
        renamed_users = migrate_renamed_plex_users(root_config, config_path, cache_dir)
        if renamed_users:
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

    # Advisory update notice - printed right after the version banner
    # above in the overall output; see print_update_notice() docstring
    # for why this (not run.sh/run.ps1) is what binary users see.
    print_update_notice(get_update_mode(root_config))

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

    # Resolve libraries for this media type (#157 Phase 3). A single-library
    # install (no 'libraries:' config, or exactly one explicit library of
    # this media type) always resolves to exactly one entry here, so the
    # loop below collapses to the original one-pass-per-user behavior.
    libraries = get_libraries_for_media_type(base_config, media_type_key)

    if args.library_id:
        libraries = [lib for lib in libraries if lib.get('id') == args.library_id]
        if not libraries:
            log_error(f"Library '{args.library_id}' not found for media type '{media_type_key}'")
            sys.exit(1)

    multi_library = len(libraries) > 1

    # Process each library x user
    plex_token = base_config.get('plex', {}).get('token', '')
    for library in libraries:
        if multi_library:
            print(f"\n{CYAN}=== Library: {library['name']} ==={RESET}")
            print("-" * 50)

        for user in all_users:
            print(f"\n{GREEN}Processing recommendations for user: {user}{RESET}")
            print("-" * 50)

            resolved_user = resolve_admin_username(user, plex_token)
            user_config = update_config_for_user(base_config, resolved_user)

            process_func(user_config, config_path, log_retention_days, resolved_user, library)

            print(f"\n{GREEN}Completed processing for user: {resolved_user}{RESET}")
            print("-" * 50)

    print_runtime(start_time)
