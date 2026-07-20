"""
User identity migration utilities for Curatarr.

Plex usernames are mutable (a user can rename their account/display name),
but curatarr historically keyed all per-user artifacts - config preferences,
cache filenames, Plex labels/collections - on that mutable username string.
A rename therefore looked like a brand-new user: settings reset to defaults
and the old collection was orphaned in Plex.

This module detects renames by comparing the *stable* Plex account id
against a small persisted id -> username map, and migrates the affected
config/cache/collection artifacts from the old username to the new one.

Entirely best-effort: any failure here is logged and swallowed so a
migration problem never breaks a normal recommendation run. If a stable id
can't be resolved for a user, behavior is unchanged (falls back to today's
username-keyed behavior - no regression).
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import plexapi.exceptions
from plexapi.myplex import MyPlexAccount

from .display import log_warning
from .plex import cleanup_old_collections, init_plex

logger = logging.getLogger('curatarr')

USER_ID_MAP_FILENAME = 'user_id_map.json'

# Cache filename patterns keyed on username. Kept explicit (rather than a
# filesystem glob) so migration only ever touches files curatarr itself
# generates for a given user.
_CACHE_FILENAME_PATTERNS = [
    'watched_cache_plex_{username}.json',
    'tv_watched_cache_plex_{username}.json',
    'external_recs_{username}_movies.json',
    'external_recs_{username}_shows.json',
]


# ---------------------------------------------------------------------------
# Stable id <-> username map
# ---------------------------------------------------------------------------

def load_user_id_map(cache_dir: str) -> Dict[str, str]:
    """Load the persisted Plex account id -> last-known-username map."""
    path = os.path.join(cache_dir, USER_ID_MAP_FILENAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, IOError, OSError) as e:
        log_warning(f"Could not read user id map ({path}): {e}")
    return {}


def save_user_id_map(cache_dir: str, id_map: Dict[str, str]) -> None:
    """Persist the Plex account id -> username map."""
    path = os.path.join(cache_dir, USER_ID_MAP_FILENAME)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(id_map, f, indent=2, sort_keys=True)
    except (IOError, OSError) as e:
        log_warning(f"Could not save user id map ({path}): {e}")


def get_live_plex_user_map(config: Dict) -> Dict[str, str]:
    """
    Build a map of stable Plex account id -> current username for the
    server owner plus all managed/shared users.

    Uses the same "username" identity (title) that the rest of curatarr
    keys off of (see get_configured_users). Returns {} on any failure so
    callers can safely no-op.
    """
    try:
        account = MyPlexAccount(token=config['plex']['token'])
        live_map = {str(account.id): account.username}
        for user in account.users():
            if hasattr(user, 'id') and hasattr(user, 'title') and user.title:
                live_map[str(user.id)] = user.title
        return live_map
    except (plexapi.exceptions.PlexApiException, KeyError, TypeError) as e:
        log_warning(f"Could not resolve live Plex users for rename detection: {e}")
        return {}


def detect_renamed_users(previous_map: Dict[str, str], live_map: Dict[str, str]) -> Dict[str, str]:
    """
    Return {old_username: new_username} for accounts whose stable id is
    known from a prior run but whose username has since changed.
    """
    renames = {}
    for account_id, old_username in previous_map.items():
        new_username = live_map.get(account_id)
        if new_username and new_username != old_username:
            renames[old_username] = new_username
    return renames


# ---------------------------------------------------------------------------
# config.yml text surgery (targeted edits - preserves comments/formatting
# for everything outside the specific lines being changed)
# ---------------------------------------------------------------------------

def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def _find_bare_key_line(lines: List[str], key: str, start: int, end: int) -> Optional[int]:
    """Find a `key:` line (block mapping key, no inline scalar value) within lines[start:end]."""
    pattern = re.compile(rf'^[ \t]*{re.escape(key)}\s*:\s*(#.*)?$')
    for i in range(start, end):
        if pattern.match(lines[i]):
            return i
    return None


def _block_end(lines: List[str], key_line: int, key_indent: int) -> int:
    """
    Given the index of a `key:` line and its indentation, return the
    exclusive end index of its block (the first subsequent line indented
    at or below key_indent, ignoring blank lines and full-line comments).
    """
    for i in range(key_line + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith('#'):
            continue
        if _line_indent(lines[i]) <= key_indent:
            return i
    return len(lines)


def _first_child_indent(lines: List[str], key_line: int, block_end: int) -> Optional[int]:
    for i in range(key_line + 1, block_end):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith('#'):
            continue
        return _line_indent(lines[i])
    return None


def rename_user_preferences_key(config_text: str, old_username: str, new_username: str) -> Tuple[str, bool]:
    """
    Rename the `users.preferences.<old_username>` mapping key to
    `<new_username>` in raw config.yml text, leaving everything else
    (including comments/formatting) untouched.

    Returns (possibly modified text, changed).
    """
    lines = config_text.splitlines(keepends=True)

    users_line = _find_bare_key_line(lines, 'users', 0, len(lines))
    if users_line is None:
        return config_text, False
    users_indent = _line_indent(lines[users_line])
    users_end = _block_end(lines, users_line, users_indent)

    prefs_line = _find_bare_key_line(lines, 'preferences', users_line + 1, users_end)
    if prefs_line is None:
        return config_text, False
    prefs_indent = _line_indent(lines[prefs_line])
    prefs_end = _block_end(lines, prefs_line, prefs_indent)

    user_key_indent = _first_child_indent(lines, prefs_line, prefs_end)
    if user_key_indent is None:
        return config_text, False

    key_pattern = re.compile(
        rf'^(?P<indent>[ \t]{{{user_key_indent}}}){re.escape(old_username)}(?P<rest>\s*:\s*(#.*)?)$'
    )
    for i in range(prefs_line + 1, prefs_end):
        if _line_indent(lines[i]) != user_key_indent:
            continue
        stripped_line = lines[i].rstrip('\r\n')
        newline = lines[i][len(stripped_line):]
        m = key_pattern.match(stripped_line)
        if m:
            lines[i] = f"{m.group('indent')}{new_username}{m.group('rest')}{newline}"
            return ''.join(lines), True

    return config_text, False


def rename_user_in_users_list(config_text: str, old_username: str, new_username: str) -> Tuple[str, bool]:
    """
    Rename `old_username` to `new_username` wherever it appears as an entry
    in `users.list` (comma-separated string or YAML sequence form).
    """
    lines = config_text.splitlines(keepends=True)

    users_line = _find_bare_key_line(lines, 'users', 0, len(lines))
    if users_line is None:
        return config_text, False
    users_indent = _line_indent(lines[users_line])
    users_end = _block_end(lines, users_line, users_indent)

    token_pattern = re.compile(rf'(?<![\w.-]){re.escape(old_username)}(?![\w.-])')

    for i in range(users_line + 1, users_end):
        stripped = lines[i].strip()

        if re.match(r'^list\s*:', stripped):
            if token_pattern.search(lines[i]):
                lines[i] = token_pattern.sub(new_username, lines[i], count=1)
                return ''.join(lines), True
            continue

        seq_match = re.match(r'^-\s*(?P<val>.+?)\s*$', stripped)
        if seq_match and seq_match.group('val') == old_username:
            indent = ' ' * _line_indent(lines[i])
            newline = lines[i][len(lines[i].rstrip('\r\n')):]
            dash = re.match(r'^-\s*', stripped).group(0)
            lines[i] = f"{indent}{dash}{new_username}{newline}"
            return ''.join(lines), True

    return config_text, False


# ---------------------------------------------------------------------------
# Cache files + orphaned collections
# ---------------------------------------------------------------------------

def _capture_old_display_name(root_config: Dict, old_username: str) -> str:
    prefs = (root_config.get('users', {}) or {}).get('preferences', {}) or {}
    old_prefs = prefs.get(old_username, {}) or {}
    return old_prefs.get('display_name') or old_username.capitalize()


def migrate_cache_files(cache_dir: str, old_username: str, new_username: str) -> None:
    """Rename (or drop, if a file for the new name already exists) known
    per-user cache files so a rename doesn't leave stale/orphaned caches."""
    for pattern in _CACHE_FILENAME_PATTERNS:
        old_path = os.path.join(cache_dir, pattern.format(username=old_username))
        new_path = os.path.join(cache_dir, pattern.format(username=new_username))
        if not os.path.exists(old_path):
            continue
        try:
            if os.path.exists(new_path):
                # Don't clobber an existing cache for the new name - drop
                # the stale one so it regenerates cleanly on next run.
                os.remove(old_path)
                logger.info(f"Removed stale cache for renamed user: {os.path.basename(old_path)}")
            else:
                os.rename(old_path, new_path)
                logger.info(f"Migrated cache file: {os.path.basename(old_path)} -> {os.path.basename(new_path)}")
        except OSError as e:
            log_warning(f"Could not migrate cache file {old_path}: {e}")


def cleanup_orphaned_user_collections(config: Dict, old_username: str, old_display_name: str) -> None:
    """
    Best-effort deletion of the old username's "Recommendation" collection
    in both the movie and TV libraries so it doesn't linger orphaned after
    a rename. The correct collection under the new name is recreated by
    the normal recommendation run.
    """
    try:
        plex = init_plex(config)
    except Exception as e:
        log_warning(f"Could not connect to Plex for orphaned-collection cleanup: {e}")
        return

    plex_config = config.get('plex', {}) or {}
    library_targets = (
        (plex_config.get('movie_library', 'Movies'), '🎬'),
        (plex_config.get('tv_library', 'TV Shows'), '📺'),
    )

    for library_title, emoji in library_targets:
        try:
            section = plex.library.section(library_title)
        except plexapi.exceptions.PlexApiException as e:
            logger.debug(f"Library '{library_title}' not available for cleanup: {e}")
            continue

        # No collection exists yet under the new name at migration time,
        # so there's nothing that needs to be protected from deletion.
        try:
            cleanup_old_collections(section, current_collection_name="", username=old_display_name, emoji=emoji, logger=logger)
            if old_display_name.lower() != old_username.lower():
                cleanup_old_collections(section, current_collection_name="", username=old_username, emoji=emoji, logger=logger)
        except plexapi.exceptions.PlexApiException as e:
            log_warning(f"Error cleaning up orphaned collection in '{library_title}': {e}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def migrate_renamed_plex_users(root_config: Dict, config_path: str, cache_dir: str) -> Dict[str, str]:
    """
    Detect Plex account renames (by stable id) since the last run and
    migrate this user's config preferences, users.list entry, cache files,
    and orphaned Plex collection so a rename doesn't silently reset their
    settings.

    Args:
        root_config: Raw config.yml dict (as loaded, before per-media adapt)
        config_path: Path to config.yml (rewritten in place if a rename is
            detected and located)
        cache_dir: Directory containing curatarr's per-user cache files

    Returns:
        {old_username: new_username} for any renames that were detected
        (whether or not every part of the migration fully succeeded).
    """
    try:
        previous_map = load_user_id_map(cache_dir)
        live_map = get_live_plex_user_map(root_config)
        if not live_map:
            # Can't resolve stable ids this run - leave everything as-is.
            return {}

        renames = detect_renamed_users(previous_map, live_map)

        if renames:
            config_text = None
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config_text = f.read()
            except (IOError, OSError) as e:
                log_warning(f"Could not read {config_path} for user migration: {e}")

            for old_username, new_username in renames.items():
                log_warning(f"Detected Plex username change: '{old_username}' -> '{new_username}' (migrating settings)")
                old_display_name = _capture_old_display_name(root_config, old_username)

                if config_text is not None:
                    try:
                        config_text, prefs_changed = rename_user_preferences_key(config_text, old_username, new_username)
                        config_text, list_changed = rename_user_in_users_list(config_text, old_username, new_username)
                        if not (prefs_changed or list_changed):
                            log_warning(
                                f"Could not locate '{old_username}' in config.yml users section "
                                f"(unexpected format) - settings were not migrated automatically."
                            )
                    except Exception as e:
                        log_warning(f"Error migrating config.yml for renamed user '{old_username}': {e}")

                try:
                    migrate_cache_files(cache_dir, old_username, new_username)
                except Exception as e:
                    log_warning(f"Error migrating cache files for renamed user '{old_username}': {e}")

                try:
                    cleanup_orphaned_user_collections(root_config, old_username, old_display_name)
                except Exception as e:
                    log_warning(f"Error cleaning up orphaned collection for renamed user '{old_username}': {e}")

            if config_text is not None:
                try:
                    with open(config_path, 'w', encoding='utf-8') as f:
                        f.write(config_text)
                except (IOError, OSError) as e:
                    log_warning(f"Could not write migrated config.yml: {e}")

        save_user_id_map(cache_dir, live_map)
        return renames

    except Exception as e:
        log_warning(f"User rename migration failed, continuing with existing behavior: {e}")
        return {}
