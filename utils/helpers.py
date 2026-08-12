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
Miscellaneous helper utilities for Curatarr.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Dict

from .config import MAX_LOG_FILE_BYTES
from .display import RESET, YELLOW, log_info, log_warning


def get_code_root() -> str:
    """
    Get the directory the curatarr *code* (recommenders/, utils/, web/,
    run.sh/run.ps1) actually lives in on disk - deliberately NOT the
    same thing get_project_root() below returns.

    get_project_root() answers "where does config/cache/logs/
    recommendations live" - for a source checkout or a Docker image
    those two questions happen to have the same answer (the repo root),
    which is exactly why this distinction was missing for so long and
    why #260's second half (JobManager launching movie/tv/external
    subprocesses) went unnoticed until Docker: CURATARR_CONFIG_DIR
    deliberately makes get_project_root() point at a separate, mounted
    *data* volume (/data) while the code stays at the image's fixed
    WORKDIR (/app) - anything that needs to find recommenders/<x>.py or
    run.sh/run.ps1 on disk (see web/job_runner.py's JobManager, the only
    current caller) must resolve against THIS, never against
    get_project_root().

    Not meaningful for a frozen (PyInstaller --onefile) binary - there
    is no on-disk recommenders/<x>.py to point at once packaged (they're
    bundled into the executable, not shipped as loose .py files - see
    curatarr_app.py's dispatcher) - callers must branch on
    `sys.frozen` themselves and never call this in that branch (see
    JobManager._build_command's `if frozen: ... else:` split, which
    already does exactly this for an unrelated reason: re-invoking the
    packaged exe with --run-recommender instead of a script path).

    Returns:
        Absolute path to the directory containing utils/ (this file's
        own parent's parent) - always the on-disk code location,
        regardless of CURATARR_CONFIG_DIR or sys.frozen.
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@lru_cache(maxsize=1)
def get_project_root() -> str:
    """
    Get the project root directory path - i.e. where config/cache/logs/
    recommendations live, NOT necessarily where the code itself lives
    (see get_code_root() above for that, and for why the two can
    genuinely differ).

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
    override = os.environ.get("CURATARR_CONFIG_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    if getattr(sys, "frozen", False):
        if os.name == "nt":
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
            root = os.path.join(base, "curatarr")
        else:
            root = os.path.join(os.path.expanduser("~"), ".curatarr")
        os.makedirs(root, exist_ok=True)
        return root
    return get_code_root()


def migrate_legacy_cache_dir(legacy_dir: str, new_dir: str) -> None:
    """
    One-time best-effort migration of curatarr's cache directory from
    the pre-2.10.3 location (computed relative to recommenders/base.py's
    own __file__, bypassing get_project_root() entirely - see
    recommenders/base.py's cache_dir setup) to the get_project_root()-
    resolved location.

    legacy_dir is ALWAYS the real, on-disk source checkout's own cache/
    directory - it's derived from __file__, never from config or
    CURATARR_CONFIG_DIR. This function runs unconditionally on every
    BaseRecommender construction (every recommender run), so ANY process
    that sets CURATARR_CONFIG_DIR to a directory that doesn't already
    have these cache files reaches here - not just a genuine one-time
    upgrade. Confirmed: this previously used shutil.move(), and a
    process pointed at a temporary/later-removed destination (a scratch
    dir, a test harness, a misconfigured CURATARR_CONFIG_DIR) silently
    relocated - not copied - a real install's cache files there with no
    warning; deleting that destination afterward destroyed real user
    data with nothing left to recover. COPIES instead now, specifically
    to make that no longer possible: the source repo's cache/ is never
    touched, regardless of what happens to the destination.

    In practice only reachable for a source install run with
    CURATARR_CONFIG_DIR set, where the two locations genuinely differ
    and the old one may still have files on disk:
      - Docker: the old /app/cache is destroyed by the container
        recreate that delivers this fix, so there's nothing to migrate.
      - Frozen binary: the old location was inside a PyInstaller
        --onefile temp unpack dir already deleted when that earlier run
        exited, so there's nothing to migrate.
      - Source install with no CURATARR_CONFIG_DIR: both __file__-relative
        computations land on the same repo root - old and new paths are
        identical, so this is a same-directory no-op.
    Callers don't need to special-case any of the above; legacy_dir
    equal to (or empty/missing at) new_dir all just no-op below.

    Only copies files that don't already exist at the new location -
    never overwrites/clobbers a cache the new location already has.
    This existing per-file check is also what keeps a copy-based
    migration from repeating itself forever: once a file has been
    copied once, it exists at new_path, so every subsequent run's
    os.path.exists(new_path) check skips it - no separate completion
    marker needed, and (since the source is never deleted) nothing
    about that check can ever regress into a delete.

    Never raises: any failure is logged as a warning and the run
    continues with whatever ended up on disk - worst case, an
    unmigrated cache simply regenerates rather than blocking a run.

    Logs loudly (both print() and log_warning(), not the log_info() this
    used before) whenever it actually copies anything - silent
    relocation of a user's data was the core defect this fixes, so
    finding out it happened must not depend on log level configuration
    or on scrolling back through a log file.
    """
    try:
        if not os.path.isdir(legacy_dir):
            return
        if os.path.realpath(legacy_dir) == os.path.realpath(new_dir):
            return

        copied = []
        for filename in os.listdir(legacy_dir):
            legacy_path = os.path.join(legacy_dir, filename)
            if not os.path.isfile(legacy_path):
                continue
            new_path = os.path.join(new_dir, filename)
            if os.path.exists(new_path):
                # New location already has this cache - don't clobber it,
                # just leave the (also untouched) legacy copy where it is.
                continue
            os.makedirs(new_dir, exist_ok=True)
            # copy2 (not move/copy): preserves mtime/permissions, and -
            # the entire point of this fix - leaves legacy_path in place.
            # The real repo's cache/ is never modified by this function.
            shutil.copy2(legacy_path, new_path)
            copied.append(filename)

        if copied:
            message = (
                f"Copied {len(copied)} cache file(s) from the legacy location "
                f"{legacy_dir} to {new_dir} (originals left in place, unmodified): "
                f"{', '.join(sorted(copied))}"
            )
            print(f"{YELLOW}{message}{RESET}")
            log_warning(message)
    except Exception as e:
        log_warning(f"Cache directory migration from {legacy_dir} skipped due to error: {e}")


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
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = None
        if declared is not None and declared > max_bytes:
            raise ValueError(
                f"Response declared {declared} bytes via Content-Length, exceeding the {max_bytes}-byte limit"
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
    response._content = b"".join(chunks)
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


def no_window_kwargs() -> Dict[str, int]:
    """subprocess.run()/Popen() kwargs to suppress a console window on
    Windows for a short-lived helper child (tasklist/taskkill/powershell
    precondition-check invocations) whose output is already captured via
    capture_output=True/stdout=PIPE - never anything a visible window
    would show the user that a log/capture doesn't already have. Returns
    {} on non-Windows: subprocess.CREATE_NO_WINDOW doesn't exist there,
    so a bare reference would raise AttributeError (same reasoning as
    web/job_runner.py's/web/update_apply.py's own getattr(...) guard
    around this constant, which this centralizes for every call site
    that only needs the plain "no window" flag - not the
    CREATE_NEW_PROCESS_GROUP/DETACHED_PROCESS combinations
    web/update_apply.py's _relaunch_ui and utils/self_update_handoff.py
    use for their own detached, longer-lived children, which stay
    exactly as they are).

    Deliberately NOT gated on debug mode: every call site this is used
    from already pipes its child's stdout/stderr, so hiding the window
    never hides any output - see curatarr_app.py's _debug_requested()/
    AllocConsole() path for the separate, existing mechanism that gives
    the main process itself a console on request.
    """
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


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
    if os.name == "nt":
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
    " 4K",
    " 4k",
    " HD",
    " hd",
    " UHD",
    " uhd",
    " Extended",
    " extended",
    " EXTENDED",
    " Director's Cut",
    " Directors Cut",
    " Theatrical",
    " Unrated",
    " UNRATED",
    " Remastered",
    " REMASTERED",
    " Special Edition",
    " Collector's Edition",
    " IMAX",
    " 3D",
    " 3d",
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
            normalized = normalized[: -len(suffix)].strip()

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

    Also caps any single .log file that has grown past MAX_LOG_FILE_BYTES,
    regardless of its mtime, to its last MAX_LOG_FILE_BYTES - keeping a
    tail instead of truncating to zero (#263 - see below for why this
    changed). This exists for append-only logs (e.g. a cron job's
    `>> logs/daily-run.log` redirect) - every write refreshes the mtime,
    so the age-based removal below can never trigger for them and they
    would otherwise grow forever. Rewriting in place (rather than
    removing/renaming) is deliberate: a process that already has the
    file open in append mode keeps writing from the new end-of-file
    without needing to reopen it - the same "copytruncate" strategy
    logrotate uses for this exact situation.

    #263: this used to truncate to exactly 0 bytes (`open(path, "w")`),
    which is silent, permanent data loss the moment ANY .log file in
    log_dir - not just genuinely append-only ones - happens to exceed
    the cap: this function runs at the START of every setup_log_file()
    call (see utils/cli.py), so a user's own completed, still-wanted
    run log got zeroed out by the very NEXT user's run in the same
    batch, the moment it crossed 20MB - producing a permanent "unknown"
    status (web/status.py) with no way to recover the content. Keeping
    a tail preserves the same size-bounding property (and the same
    copytruncate-safety for an actively-appending writer) without
    destroying everything that was there.

    Args:
        log_dir: Directory containing log files
        retention_days: Number of days to retain logs (0 = keep all)
    """
    if retention_days <= 0:
        return

    try:
        cutoff_time = datetime.now() - timedelta(days=retention_days)

        for filename in os.listdir(log_dir):
            if not filename.endswith(".log"):
                continue

            filepath = os.path.join(log_dir, filename)

            try:
                file_size = os.path.getsize(filepath)
                if file_size > MAX_LOG_FILE_BYTES:
                    with open(filepath, "rb") as f:
                        f.seek(file_size - MAX_LOG_FILE_BYTES)
                        tail = f.read()
                    with open(filepath, "wb") as f:
                        f.write(tail)
                    log_info(
                        f"Capped oversized log to its last {MAX_LOG_FILE_BYTES} bytes: "
                        f"{filename} (was {file_size} bytes)"
                    )
                    continue
            except Exception as e:
                log_warning(f"Failed to check/cap oversized log {filename}: {e}")
                continue

            try:
                file_mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                if file_mtime < cutoff_time:
                    os.remove(filepath)
                    log_info(f"Removed old log: {filename} (age: {(datetime.now() - file_mtime).days} days)")
            except Exception as e:
                log_warning(f"Failed to remove old log {filename}: {e}")

    except Exception as e:
        log_warning(f"Error during log cleanup: {e}")
