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

"""
Persisted registry of PrivateCollection_* label owners (#351).

utils.plex_policy.apply_user_label_restrictions excludes every OTHER
configured user's PrivateCollection_* label from a given user's
filterMovies/filterTelevision, but it only ever knew about the labels of
users CURRENTLY in config - a user removed from users.list (but not
necessarily removed from the Plex server itself) dropped out of that
computation entirely, silently un-hiding their old collection from
everyone else the very next run.

This module is the fix's memory: every run that successfully applies
restrictions records who it applied a PrivateCollection_* label for, so a
later run - even after that user is gone from config - still knows their
label needs to stay excluded. utils.plex_policy is the only caller that
mutates this file; it owns the load-merge-save lifecycle (mirrors
utils.user_migration.migrate_renamed_plex_users' own orchestration), this
module only provides the mechanism.

Keyed on the stable Plex account id, never username - same reasoning as
utils/user_migration.py's id<->username map (USER_ID_MAP_FILENAME):
usernames are mutable, so keying on one would make a simple rename look
exactly like a departure the next run.

Missing/corrupt cache file degrades to "no known owners" (never raises) -
same convention as utils.user_migration.load_user_id_map. Losing this
file only means a previously-departed owner's label stops being retained
starting from whatever run first sees the empty cache; it can never take
down a normal run.
"""

import json
import logging
import os
from typing import Any, Dict, List, Mapping

import plexapi.exceptions

from .config import MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV, get_libraries_for_media_type
from .display import log_warning

logger = logging.getLogger("curatarr")

PRIVATE_LABEL_OWNERS_FILENAME = "private_label_owners.json"


def load_private_label_owners(cache_dir: str) -> Dict[str, Dict[str, Any]]:
    """
    Load the persisted {account_id: {"username": str, "labels": {"movie":
    [...], "tv": [...]}}} map.

    A missing file, unreadable/corrupt JSON, or a value that isn't the
    expected shape all degrade to {} rather than raising or propagating a
    partially-parsed result - malformed entries are dropped individually,
    not treated as a reason to discard the whole file.
    """
    path = os.path.join(cache_dir, PRIVATE_LABEL_OWNERS_FILENAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        log_warning(f"Could not read private label owner cache ({path}): {e}")
        return {}

    if not isinstance(data, dict):
        log_warning(f"Private label owner cache ({path}) was not a JSON object - ignoring")
        return {}

    owners: Dict[str, Dict[str, Any]] = {}
    for account_id, entry in data.items():
        if not isinstance(entry, dict):
            continue
        username = entry.get("username")
        raw_labels = entry.get("labels")
        if not isinstance(username, str) or not username or not isinstance(raw_labels, dict):
            continue
        owners[str(account_id)] = {
            "username": username,
            "labels": {
                media_type: [str(x) for x in (raw_labels.get(media_type) or []) if x]
                for media_type in (MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV)
            },
        }
    return owners


def save_private_label_owners(cache_dir: str, owners: Mapping[str, Dict[str, Any]]) -> None:
    """Persist the account id -> {username, labels} map. Best-effort - a
    cache that cannot be written is a lost-memory problem for the NEXT
    run, never a reason to fail this one (mirrors
    utils.user_migration.save_user_id_map)."""
    path = os.path.join(cache_dir, PRIVATE_LABEL_OWNERS_FILENAME)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(owners, f, indent=2, sort_keys=True)
    except (IOError, OSError) as e:
        log_warning(f"Could not save private label owner cache ({path}): {e}")


def find_orphaned_owners(
    persisted_owners: Mapping[str, Dict[str, Any]], current_owner_ids: Any
) -> Dict[str, Dict[str, Any]]:
    """
    Persisted owners whose account id is not in current_owner_ids - i.e.
    curatarr created a PrivateCollection_* label for them at some point,
    but they are no longer a currently-configured user this run.

    current_owner_ids: any iterable of account id strings (typically
    utils.plex_policy.apply_user_label_restrictions' own id_to_configured
    .keys() - the ids it already resolved this run for users actually in
    config, admin excluded the same way it always is).
    """
    current = set(current_owner_ids)
    return {account_id: entry for account_id, entry in persisted_owners.items() if account_id not in current}


def prune_orphaned_private_collections(config: Dict, orphaned_owners: Mapping[str, Dict[str, Any]]) -> List[str]:
    """
    Best-effort teardown of orphaned PrivateCollection_* collections -
    #351's collections.prune_orphaned_private_labels opt-in.

    A currently-configured user can never reach this function's delete
    path: orphaned_owners only ever contains entries find_orphaned_owners
    identified as NOT among this run's currently-configured owner ids -
    this function has no other way to select what to delete.

    Ownership of each collection is confirmed the same way #291's
    no-watch-history removal path already does - solely via the
    PrivateCollection_* label already on it (utils.plex.
    remove_owned_collection), never by title/emoji/name-pattern
    guessing - so this can never delete a collection curatarr didn't
    create, even if a departed user's display name collides with
    something else in the library.

    Every library matching each media type (utils.config.
    get_libraries_for_media_type) is checked for each persisted label -
    the same multi-library enumeration utils.plex_policy.
    build_all_private_labels used to build those label names in the
    first place, so a multi-library install's per-library-suffixed
    labels (PrivateCollection_<library_id>_<user>) are found regardless
    of which library they were created in.

    Entirely best-effort, like utils.user_migration's rename migration:
    a Plex connection failure or a single collection's delete failure is
    logged and does not stop the rest.

    Does NOT touch cache/private_label_owners.json itself - the caller
    (utils.plex_policy.apply_user_label_restrictions) owns the single
    load-merge-save lifecycle for that file and decides what "pruned"
    means for its own already-loaded copy.

    Returns only the usernames whose collection was actually located and
    deleted. An owner is NEVER included just because a delete was
    attempted - if Plex couldn't be reached, a library section couldn't
    be resolved, or no matching collection was found in any library
    that could be checked, that owner is left out so the caller keeps
    tracking them rather than assuming (possibly wrongly) that nothing
    is left to hide. This is what lets the caller safely pop an owner
    out of cache/private_label_owners.json only once their collection
    is confirmed gone.
    """
    if not orphaned_owners:
        return []

    from .plex import init_plex, remove_owned_collection

    try:
        plex = init_plex(config)
    except Exception as e:
        log_warning(f"Could not connect to Plex to prune orphaned private collections: {e}")
        return []

    pruned: List[str] = []
    for account_id, entry in orphaned_owners.items():
        username = entry.get("username") or account_id
        labels = entry.get("labels") or {}
        reason = "owner no longer configured (collections.prune_orphaned_private_labels)"

        deleted = False
        for media_type in (MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV):
            for label in labels.get(media_type) or []:
                for library in get_libraries_for_media_type(config, media_type):
                    section_name = library.get("section")
                    if not section_name:
                        continue
                    try:
                        section = plex.library.section(section_name)
                    except plexapi.exceptions.PlexApiException as e:
                        logger.debug(f"Library '{section_name}' not available while pruning {username}: {e}")
                        continue
                    if remove_owned_collection(section, label, username, reason, logger):
                        deleted = True

        if deleted:
            pruned.append(username)

    return pruned
