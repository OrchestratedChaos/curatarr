"""
Miscellaneous helper utilities for Curatarr.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Dict

from .display import log_info, log_warning


@lru_cache(maxsize=1)
def get_project_root() -> str:
    """
    Get the project root directory path.

    For a normal source checkout / Docker image this is the parent of
    utils/ (repo root), same as always.

    For a PyInstaller --onefile binary (`sys.frozen` is set - see
    curatarr_app.py / curatarr.spec) there is no on-disk repo to anchor
    to: the running executable unpacks itself into a throwaway temp dir
    (sys._MEIPASS) that's deleted on exit, so config/cache/logs can't
    live there. Frozen binaries instead read/write a per-user data
    directory that persists across runs and across re-downloading a
    newer binary:
      - Windows: %APPDATA%\\curatarr
      - macOS/Linux: ~/.curatarr
    Created on first use if it doesn't already exist. See
    docs/BINARIES.md for the full rationale.

    Returns:
        Absolute path to the project root (or the per-user data dir
        when running as a frozen binary).
    """
    if getattr(sys, 'frozen', False):
        if os.name == 'nt':
            base = os.environ.get('APPDATA') or os.path.expanduser('~')
            root = os.path.join(base, 'curatarr')
        else:
            root = os.path.join(os.path.expanduser('~'), '.curatarr')
        os.makedirs(root, exist_ok=True)
        return root
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
