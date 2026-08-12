# curatarr
# Copyright (C) 2026 OrchestratedChaos
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Prunes cache/ files orphaned by no-longer-configured users.

cache/ has no equivalent of logs/'s cleanup_old_logs (see utils.helpers)
- nothing here ever removes a per-user cache file once that user is no
longer configured. Real examples found in a live install (#233 audit
remediation batch D / PR1(b)): watched_cache_plex_.json (an empty
username - an old bug), a joined-multi-user
watched_cache_plex_<many-usernames>.json (a legacy naming convention
from before every recommender run became strictly single-user - see
recommenders/base.py's _get_user_context - so the current code never
writes or reads that shape, only ever left it behind), and
external_recs_<removed-user>_*.json for a user no longer in users.list
at all.

Deliberately conservative:
  - Only ever considers the four exact per-user filename patterns
    utils.user_migration's rename-migration already uses
    (CACHE_FILENAME_PATTERNS) - a file this doesn't recognize is left
    alone entirely, not even logged. This never touches
    all_movies_cache.json/all_shows_cache.json (whole-library, not
    per-user), huntarr caches, user_id_map.json, or anything else this
    module has no positive evidence is a per-user cache.
  - prune_orphaned_cache_files() defaults to dry_run=True: logging
    candidates, removing nothing, until a caller explicitly opts into
    deletion. Deleting a live cache forces an expensive full Plex
    re-scan for that user on their next run, so this only ever acts on
    files it can positively identify as orphaned, and only when asked.
"""

import logging
import os
import re
from typing import Iterable, List, NamedTuple, Pattern

from .display import log_warning
from .user_migration import CACHE_FILENAME_PATTERNS

logger = logging.getLogger("curatarr")


class OrphanedCacheFile(NamedTuple):
    """One cache file whose embedded username doesn't match any
    currently-configured user."""

    path: str
    filename: str
    username: str


def _pattern_to_regex(pattern: str) -> Pattern:
    """Turn a "{username}"-templated filename pattern (see
    utils.user_migration.CACHE_FILENAME_PATTERNS) into a regex that
    captures the username portion back out of a real filename.

    Uses .* (not .+) for the captured group deliberately - an empty
    username portion (e.g. "watched_cache_plex_.json", a real example
    found in a live install) is exactly the kind of orphan this is
    meant to catch, not silently skip.
    """
    prefix, _, suffix = pattern.partition("{username}")
    return re.compile(f"^{re.escape(prefix)}(?P<username>.*){re.escape(suffix)}$")


_PATTERN_REGEXES = [_pattern_to_regex(p) for p in CACHE_FILENAME_PATTERNS]


def find_orphaned_cache_files(cache_dir: str, configured_usernames: Iterable[str]) -> List[OrphanedCacheFile]:
    """
    Find cache/ files matching one of the known per-user filename
    patterns (see utils.user_migration.CACHE_FILENAME_PATTERNS) whose
    embedded username isn't in configured_usernames.

    A file that doesn't match one of those four exact patterns is left
    alone entirely - not even logged - so this never flags/removes
    anything this codebase doesn't have positive evidence is a
    per-user cache file. A file whose embedded "username" is actually
    several usernames joined with '_' (the legacy pre-single-user-mode
    naming convention - see module docstring) can't exact-match any
    single configured username either, so it's correctly flagged here
    too without needing any special-case joined-name parsing.

    Args:
        cache_dir: Directory to scan (never the real cache/ during
            development/tests - see this module's own test suite,
            which only ever scans a tmp_path fixture)
        configured_usernames: Every username this run currently
            considers valid - already resolved (e.g. 'admin' already
            mapped to the real account username - see
            utils.cli.resolve_admin_username), not the raw config
            string, so a config that still says 'admin' doesn't
            false-flag that user's real cache file as orphaned.

    Returns:
        List of OrphanedCacheFile candidates, sorted by filename.
    """
    valid = set(configured_usernames)
    orphans = []

    try:
        entries = sorted(os.listdir(cache_dir))
    except OSError as e:
        logger.debug(f"Could not list cache dir {cache_dir} for orphan pruning: {e}")
        return []

    for filename in entries:
        for regex in _PATTERN_REGEXES:
            match = regex.match(filename)
            if not match:
                continue
            username = match.group("username")
            if username not in valid:
                orphans.append(
                    OrphanedCacheFile(path=os.path.join(cache_dir, filename), filename=filename, username=username)
                )
            break  # matched one pattern - don't also test the others against this filename

    return orphans


def prune_orphaned_cache_files(cache_dir: str, configured_usernames: Iterable[str], dry_run: bool = True) -> List[str]:
    """
    Find and (unless dry_run) remove orphaned per-user cache files - see
    find_orphaned_cache_files() for what counts as orphaned.

    Conservative by default: dry_run=True (the default) only logs what
    *would* be removed and deletes nothing.

    Args:
        cache_dir: Directory to scan
        configured_usernames: Every currently-valid (resolved) username
        dry_run: If True (default), only log candidates - remove
            nothing. If False, actually remove each file (best-effort;
            one file's removal failure is logged and does not stop the
            rest).

    Returns:
        Paths of files that were actually removed (always empty in
        dry_run mode).
    """
    orphans = find_orphaned_cache_files(cache_dir, configured_usernames)
    removed = []

    for orphan in orphans:
        if dry_run:
            logger.info(
                f"[dry-run] Would remove orphaned cache file: {orphan.filename} "
                f"(user '{orphan.username}' not in configured users)"
            )
            continue
        try:
            os.remove(orphan.path)
            logger.info(f"Removed orphaned cache file: {orphan.filename} (user '{orphan.username}' not configured)")
            removed.append(orphan.path)
        except OSError as e:
            log_warning(f"Could not remove orphaned cache file {orphan.filename}: {e}")

    return removed
