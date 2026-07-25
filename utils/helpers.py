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

    An explicit CURATARR_CONFIG_DIR environment variable overrides both
    of the above and takes top priority - added for Docker (see
    Dockerfile / docs/DOCKER.md), where the app's code lives at a fixed
    WORKDIR but config.yml/cache/logs/recommendations need to live on a
    separately mounted, persistent volume instead. Created on first use
    if it doesn't already exist, same as the frozen-binary case above.
    Unset for every existing source/frozen install, so this is a purely
    additive, opt-in override - nothing changes for them.

    Returns:
        Absolute path to the project root (the CURATARR_CONFIG_DIR
        override, the per-user data dir when running as a frozen
        binary, or the repo root - in that priority order).
    """
    override = os.environ.get('CURATARR_CONFIG_DIR')
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    if getattr(sys, 'frozen', False):
        if os.name == 'nt':
            base = os.environ.get('APPDATA') or os.path.expanduser('~')
            root = os.path.join(base, 'curatarr')
        else:
            root = os.path.join(os.path.expanduser('~'), '.curatarr')
        os.makedirs(root, exist_ok=True)
        return root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Response bodies from a USER-CONFIGURED host (Radarr/Sonarr/Tautulli/
# Plex - the URL comes from config, so it could point anywhere, unlike
# a fixed official third-party API like Trakt/Simkl/TMDB/MDBList) are
# capped at this many bytes before being read into memory at all - a
# misconfigured/malicious/compromised configured host serving an
# unbounded response body must not be able to exhaust this process's
# memory. Comfortably larger than any legitimate response these
# integrations actually return (even a large library/history listing).
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


def read_response_capped(response, max_bytes: int = MAX_RESPONSE_BYTES):
    """Read *response* (a `requests.Response` obtained with
    `stream=True`) into memory, capped at *max_bytes* - see
    MAX_RESPONSE_BYTES above for why. Raises ValueError if the body is
    (or claims, via a Content-Length header, to be) larger than the cap;
    callers translate that into whatever error type is appropriate for
    their own call site.

    Pre-fills response._content (requests' own internal cache for this)
    so every normal accessor downstream - .content, .text, .json() -
    just returns the already-capped bytes instead of re-reading (and
    potentially buffering an unbounded body) themselves. Returns
    *response* itself for convenient chaining.
    """
    content_length = response.headers.get('Content-Length')
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = None
        if declared is not None and declared > max_bytes:
            raise ValueError(
                f"Response declared {declared} bytes via Content-Length, exceeding the "
                f"{max_bytes}-byte limit"
            )

    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"Response exceeded the {max_bytes}-byte limit while streaming")
        chunks.append(chunk)
    response._content = b''.join(chunks)
    response._content_consumed = True
    return response


def resolve_system_executable(preferred_path: str, fallback_name: str) -> str:
    """Prefer a fully-qualified, well-known absolute path for a system
    interpreter/tool over a bare, PATH-resolved name (e.g. 'powershell',
    'sh', 'bash', 'tasklist', 'taskkill') when building a subprocess
    argv - a PATH-manipulation attacker (a malicious entry earlier on
    PATH, or a same-named binary placed/hijacked somewhere on it) can
    substitute a bare name far more easily than a specific, well-known
    absolute one. Falls back to *fallback_name* (today's behavior,
    unchanged) if *preferred_path* doesn't actually exist on this
    machine - never breaks an unusual/non-standard install by insisting
    on a path that isn't really there. Used by utils.self_update_handoff
    and web.update_apply for every subprocess invocation of a system
    shell/interpreter/process-management tool.
    """
    if os.path.isfile(preferred_path):
        return preferred_path
    return fallback_name


def harden_file_permissions(path: str) -> None:
    """Restrict *path* - a config/token file that was just written - to
    owner-only read/write (0o600).

    config.yml/trakt.yml/sonarr.yml/radarr.yml/etc. hold the Plex token
    and integration API keys/access/refresh tokens in plaintext. A plain
    ``open(path, 'w')`` lands at whatever the OS umask default is
    (typically 0o644 on Linux/Docker), which leaves those secrets
    world-readable on a multi-user host or container. Called after every
    such write (see utils/migrate_config.py, utils/trakt_auth.py,
    web/config_io.py's save_module).

    Best-effort and non-fatal: a permission-hardening step failing must
    never take down a config save/migration/auth flow that otherwise
    succeeded, so this warns rather than raising. Also a deliberate
    no-op on Windows - NTFS has no POSIX owner/group/other permission
    bits, so os.chmod() there only toggles the read-only attribute
    (which would apply to every user, not restrict others to begin
    with) - calling it would give a false sense of having hardened
    anything.
    """
    if os.name == 'nt':
        return
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        log_warning(f"Could not restrict permissions on {path}: {exc}")


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
