"""
Advisory update-check utilities for Curatarr.

This is the ONLY update-check path that reaches binary users (source
installs additionally get run.sh's/run.ps1's own git-tag check - see
select_verified_release() there). It hits the GitHub Releases API to
learn the latest published version number, purely to decide whether to
*display* a notification. It NEVER applies an update and NEVER verifies
anything cryptographically.

SECURITY: the version string returned here is advisory/unauthenticated
input - it comes from an unauthenticated HTTPS GET with no signature
check. Treat it as untrusted: fine to compare against __version__ and
print, never fine to use as a basis for fetching/running code. The only
trusted, signature-verified update path is select_verified_release() in
run.sh/run.ps1 (pinned signer fingerprint, verified BEFORE checkout).
Do not reimplement that here and do not let this module's output drive
a checkout/exec of any kind.

Design:
- get_latest_version() fails open: ANY error (offline, DNS, timeout,
  rate limit, malformed JSON, whatever) returns None instead of
  raising. A broken/unreachable update check must never block or
  crash the app.
- Result is cached to disk (per-user data dir - see
  utils.helpers.get_project_root, same dir frozen binaries already use
  for config/cache/logs) with a timestamp, and only refreshed at most
  every UPDATE_CHECK_INTERVAL_HOURS so normal use doesn't hammer the
  GitHub API or add latency to every run.
- update_available() does a real semver-tuple compare (current vs.
  latest), never a string compare - "v2.9.0" > "v2.10.0" as strings
  would be wrong, for instance.
"""

import json
import logging
import os
import re
import time
from typing import Optional, Tuple

import requests

from .config import __version__
from .helpers import get_project_root

logger = logging.getLogger('curatarr')

# api.github.com - JSON, used for the actual version lookup. Overridable
# via CURATARR_RELEASES_API_OVERRIDE for testing/staging (e.g. this
# repo's own real end-to-end self-update test - see
# tests/test_self_update.py and the v2.8.29 PR description for what
# exercises this) - never security-relevant since this whole module is
# already advisory/unauthenticated by design (see module docstring), so
# redirecting WHERE the version number comes from doesn't change what
# gets trusted with it.
GITHUB_RELEASES_API = (
    os.environ.get('CURATARR_RELEASES_API_OVERRIDE')
    or "https://api.github.com/repos/OrchestratedChaos/curatarr/releases/latest"
)
# github.com - human-facing, used for CLI/web "go download it" links.
GITHUB_RELEASES_PAGE = "https://github.com/OrchestratedChaos/curatarr/releases/latest"

REQUEST_TIMEOUT_SECONDS = 4
UPDATE_CHECK_INTERVAL_HOURS = 12

_CACHE_FILENAME = "update_check_cache.json"
_VALID_UPDATE_MODES = ('notify', 'force', 'off')


def _cache_path() -> str:
    # Same convention as every other cache file in this codebase (see
    # recommenders/external.py's huntarr/horizon caches, etc.): under
    # project_root/cache/, which is gitignored - never directly in
    # project_root itself, which for a source install IS the git
    # checkout and would otherwise get a stray untracked file dropped
    # into the working tree on every run.
    cache_dir = os.path.join(get_project_root(), 'cache')
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except Exception as e:
        # Never let a filesystem hiccup here escape as an exception -
        # _read_cache()/_write_cache() below handle a still-missing/
        # unwritable dir themselves (isfile() check / their own
        # try/except), same fail-open contract as the rest of this
        # module.
        logger.debug(f"Could not create update-check cache dir: {e}")
    return os.path.join(cache_dir, _CACHE_FILENAME)


def parse_version(version: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """
    Parse a 'vX.Y.Z' or 'X.Y.Z' string into a comparable (X, Y, Z) tuple.

    Returns None (never raises) for anything that isn't EXACTLY a
    dotted-integer version (optionally 'v'-prefixed) - end-anchored, no
    trailing junk of any kind tolerated (previously only start-anchored,
    which accepted e.g. a trailing "-rc1" suffix). This is intentionally
    strict because the parsed value also gets re-serialized and used,
    downstream, to build a release download URL (see
    utils.self_update.release_asset_url, fed via _fetch_latest_version()
    below) - a merely-prefix match here would let a spoofed GitHub
    tag_name like "2.99.0/../v2.5.0" pass this check (real digits at the
    start, comparing as newer than the installed version) while still
    carrying a path-traversal sequence that a caller could put straight
    into that URL. Callers must treat an unparsable version as "can't
    compare", not crash.
    """
    if not version:
        return None
    match = re.match(r'^v?(\d+)\.(\d+)\.(\d+)$', version.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _read_cache() -> Optional[dict]:
    path = _cache_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(latest: Optional[str]) -> None:
    path = _cache_path()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'latest': latest, 'checked_at': time.time()}, f)
    except Exception as e:
        # Never fatal - worst case we just re-check next run instead of
        # respecting the cache interval.
        logger.debug(f"Could not write update-check cache: {e}")


def _fetch_latest_version() -> Optional[str]:
    """
    Hit the GitHub Releases API for the latest release's tag name.

    Fail-open: ANY exception (network error, timeout, DNS failure, rate
    limit, malformed/unexpected JSON, whatever) is caught here and
    returns None - this must never raise into a caller that isn't
    expecting an update check to be able to fail the whole run.

    SECURITY: never returns the raw `tag_name` (previously only a
    `.lstrip('vV')` was applied, which let anything GitHub's API
    returned - including path-traversal characters like "/../" - flow
    straight through to callers that build a download URL from it).
    Only the RE-SERIALIZED "%d.%d.%d" from parse_version()'s captured
    integer groups is ever returned, so the result is always exactly
    three dotted integers - no slashes, dots beyond the two separators,
    whitespace, or any other character can ever survive from here on.
    """
    try:
        response = requests.get(
            GITHUB_RELEASES_API,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={'Accept': 'application/vnd.github+json'},
        )
        response.raise_for_status()
        tag = (response.json() or {}).get('tag_name')
        parsed = parse_version(tag)
        if not parsed:
            return None
        return "%d.%d.%d" % parsed
    except Exception as e:
        logger.debug(f"Update check failed (non-fatal): {e}")
        return None


def get_latest_version(update_mode: str = 'notify', force_refresh: bool = False) -> Optional[str]:
    """
    Return the latest published release version (e.g. "2.9.0"), or None
    if unknown/unreachable/disabled/not-yet-due-for-a-recheck's-cache-
    was-also-unknown. Fail-open and disk-cached - see module docstring.
    Never raises.

    Args:
        update_mode: 'notify' | 'force' | 'off'. 'off' skips the network
            entirely and returns None without even reading the cache -
            a user who disabled update checks shouldn't have this
            module touch the network at all.
        force_refresh: bypass the on-disk cache/interval (used by tests
            and by anything that explicitly wants a fresh check).
    """
    if update_mode == 'off':
        return None

    if not force_refresh:
        cache = _read_cache()
        if cache and isinstance(cache.get('checked_at'), (int, float)):
            age_hours = (time.time() - cache['checked_at']) / 3600
            if age_hours < UPDATE_CHECK_INTERVAL_HOURS:
                return cache.get('latest')

    latest = _fetch_latest_version()
    _write_cache(latest)
    return latest


def update_available(
    update_mode: str = 'notify', force_refresh: bool = False
) -> Tuple[Optional[str], str, bool]:
    """
    Resolve whether a newer release is available.

    Returns:
        (latest, current, is_newer) - latest is None if unknown
        (network error, disabled, etc). is_newer is always False when
        latest is None or unparsable - "we don't know" must never be
        treated as "yes there's an update".
    """
    current = __version__
    latest = get_latest_version(update_mode=update_mode, force_refresh=force_refresh)
    latest_tuple = parse_version(latest)
    current_tuple = parse_version(current)
    is_newer = bool(latest_tuple and current_tuple and latest_tuple > current_tuple)
    return latest, current, is_newer
