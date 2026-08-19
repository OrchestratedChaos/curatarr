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
Label management utilities for Curatarr.
Handles adding, removing, and categorizing Plex labels.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .display import GREEN, RESET, log_info, log_warning

logger = logging.getLogger("curatarr")

# #267: default collection-name templates - byte-for-byte what the
# hardcoded f"{emoji} {display_name} - Recommendation" format produced
# before this was configurable, so every install that never sets
# collections.movie_name_template/tv_name_template sees zero change.
# {user}/{media_type} are the only placeholders render_collection_name
# below supports - see that function's docstring.
DEFAULT_MOVIE_NAME_TEMPLATE = "🎬 {user} - Recommendation"
DEFAULT_TV_NAME_TEMPLATE = "📺 {user} - Recommendation"

_DEFAULT_NAME_TEMPLATES = {
    "movie": DEFAULT_MOVIE_NAME_TEMPLATE,
    "tv": DEFAULT_TV_NAME_TEMPLATE,
}


def render_collection_name(template: str, user: str, media_type: str) -> str:
    """
    Render a #267 custom collection-name template.

    Args:
        template: e.g. "Recommended movies - {user}" (config/tuning.yml's
            collections.movie_name_template/tv_name_template - see
            config/tuning.example.yml)
        user: the collection's resolved display name (user_preferences.
            <user>.display_name if configured, else the username
            capitalized) - the exact value the pre-#267 hardcoded format
            already used for this position
        media_type: "movie" or "tv" - rendered into {media_type} as
            "Movie"/"TV" (title case, singular) so one template can be
            shared across both collections.movie_name_template and
            collections.tv_name_template if desired, even though
            separate keys for each are the documented/recommended way
            to do it

    Returns:
        The rendered name, WITHOUT the multi-library disambiguation
        suffix (see recommenders/base.py's
        _library_suffix_for_collection_name) - that's applied
        unconditionally after this, regardless of any custom template,
        so a bad/creative template can never break that correctness
        guarantee (same-media-type libraries would otherwise collide on
        an identical collection name).

        Falls back to the matching DEFAULT_*_NAME_TEMPLATE constant
        above on any formatting error (an unknown placeholder like
        {typo}, or a bad positional {} - str.format raises KeyError/
        IndexError for those respectively) - a bad template in
        tuning.yml must never crash a run.
    """
    media_type_label = "Movie" if media_type == "movie" else "TV"
    try:
        return template.format(user=user, media_type=media_type_label)
    except (KeyError, IndexError, ValueError) as e:
        log_warning(
            f"Invalid collections.{media_type}_name_template {template!r} ({e}) - "
            f"falling back to the default template this run"
        )
        fallback = _DEFAULT_NAME_TEMPLATES.get(media_type, DEFAULT_MOVIE_NAME_TEMPLATE)
        return fallback.format(user=user, media_type=media_type_label)


def _sanitize_user_token(user: str, normalize_case: bool) -> str:
    """
    Turn a single username/display-name into the token build_label_name
    appends to a base label.

    normalize_case (#352) folds away case AND whitespace before the
    existing punctuation substitution - "Alex Pigot" and "alexpigot" are
    the same physical account observed via two different mutable Plex
    fields (title vs username), and without this they produce two
    different PrivateCollection_* label strings for one person. Only
    private-label callers opt into this (see build_all_private_labels
    and recommenders/base.py's private label call sites) - the
    general-purpose Recommended_* item label keeps today's exact
    behavior, since existing installs already have real items labeled
    under the un-normalized form and this isn't a matching-correctness
    problem for those (categorize_labeled_items reads labels already on
    the item, it doesn't need the string to survive a Plex title flap).
    """
    user = user.strip()
    if normalize_case:
        user = re.sub(r"\s+", "", user).lower()
    return re.sub(r"\W+", "_", user)


def build_label_name(
    base_label: str,
    users: List[str],
    single_user: Optional[str] = None,
    append_usernames: bool = True,
    normalize_case: bool = False,
) -> str:
    """
    Build a label name with optional username suffix.

    Args:
        base_label: Base label name (e.g., 'Recommended')
        users: List of usernames
        single_user: Optional single user override
        append_usernames: Whether to append usernames to label
        normalize_case: #352 - fold case/whitespace in the username
            token so the same account never produces two different
            label strings depending on which Plex field (title vs
            username) happened to be read this run. Private-label
            callers only - see _sanitize_user_token's docstring.

    Returns:
        Final label name
    """
    if not append_usernames:
        return base_label

    if single_user:
        user_suffix = _sanitize_user_token(single_user, normalize_case)
        return f"{base_label}_{user_suffix}"
    elif users:
        sanitized_users = [_sanitize_user_token(user, normalize_case) for user in users]
        user_suffix = "_".join(sanitized_users)
        return f"{base_label}_{user_suffix}"
    return base_label


def categorize_labeled_items(
    labeled_items: List,
    watched_ids: set,
    excluded_genres: Iterable[str],
    label_name: str,
    label_dates: Dict,
    stale_days: int = 7,
) -> Dict[str, List]:
    """
    Categorize labeled items into watched, excluded, and fresh.

    Note: Staleness is no longer used - items stay until replaced by higher-scoring
    recommendations via score-based eviction in _update_labels_by_rank.

    Args:
        labeled_items: List of Plex items with the label
        watched_ids: Set of watched item IDs
        excluded_genres: List of genres to exclude
        label_name: Name of the label
        label_dates: Dictionary tracking when labels were added
        stale_days: Deprecated, kept for API compatibility

    Returns:
        Dictionary with keys: 'fresh', 'watched', 'stale', 'excluded'
    """
    # Convert to list to allow multiple iterations (MediaContainer is single-use)
    labeled_items = list(labeled_items)

    result: Dict[str, List[Any]] = {
        "fresh": [],
        "watched": [],
        "stale": [],  # Always empty now - score-based eviction handles rotation
        "excluded": [],
    }

    for item in labeled_items:
        item.reload()
        item_id = int(item.ratingKey)
        label_key = f"{item_id}_{label_name}"

        # Check if item has excluded genres
        item_genres = [g.tag.lower() for g in item.genres] if hasattr(item, "genres") else []
        if any(g in excluded_genres for g in item_genres):
            result["excluded"].append(item)
            continue

        # watched_ids only. Reading item.isPlayed here was wrong in any
        # multi-user install: `item` comes from the ADMIN connection, so
        # the flag is the ADMIN's watched state, and every other user's
        # still-unwatched recommendations were being evicted as "watched".
        # Callers now pass a set that already unions the user's own
        # history with their own Plex played state (see
        # utils/plex.fetch_user_played_ids).
        if item_id in watched_ids:
            result["watched"].append(item)
            continue

        # Track label date if not already tracked (for reference, not staleness)
        label_date_str = label_dates.get(label_key)

        # Item is fresh - track date if not already tracked
        result["fresh"].append(item)
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
        item.removeLabel(label_name, locked=False)
        label_key = f"{int(item.ratingKey)}_{label_name}"
        if label_key in label_dates:
            del label_dates[label_key]
        if reason:
            log_info(f"Removed ({reason}): {item.title}")


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
    added_count = 0
    for item in items:
        current_labels = [label.tag for label in item.labels]
        if label_name not in current_labels:
            item.addLabel(label_name, locked=False)
            label_key = f"{int(item.ratingKey)}_{label_name}"
            label_dates[label_key] = datetime.now().isoformat()
            print(f"{GREEN}Added: {item.title}{RESET}")
            added_count += 1
    return added_count
