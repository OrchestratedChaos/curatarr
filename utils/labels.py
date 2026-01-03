"""
Label management utilities for Plex Recommender.
Handles adding, removing, and categorizing Plex labels.
"""

import re
from datetime import datetime, timedelta
from typing import Dict, List

from .display import GREEN, RESET, log_warning


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
