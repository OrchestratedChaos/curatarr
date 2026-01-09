"""
Miscellaneous helper utilities for Curatarr.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Dict

from .display import log_info, log_warning


@lru_cache(maxsize=1)
def get_project_root() -> str:
    """
    Get the project root directory path.

    Returns:
        Absolute path to the project root (parent of utils/).
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def compute_profile_hash(profile_data: Dict) -> str:
    """
    Compute a hash of user profile data for cache invalidation.

    Used to detect when the user's watch history has changed,
    which would require recalculating similarity scores.

    Args:
        profile_data: Dict containing user preferences (genres, actors, etc.)

    Returns:
        SHA256 hash string (first 16 chars for compactness)
    """
    if not profile_data:
        return ""
    # Sort keys for consistent hashing
    serialized = json.dumps(profile_data, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]

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


def cleanup_old_logs(log_dir: str, retention_days: int) -> None:
    """
    Remove log files older than specified retention period.

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
                    log_info(f"Removed old log: {filename} (age: {(datetime.now() - file_mtime).days} days)")
            except Exception as e:
                log_warning(f"Failed to remove old log {filename}: {e}")

    except Exception as e:
        log_warning(f"Error during log cleanup: {e}")
