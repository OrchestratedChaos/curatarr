"""
Server-side "dismiss the update notice" state for Curatarr.

Shared by the web UI's dismissible update banner (web/app.py's
_update_banner_context / update_dismiss route) AND the CLI's advisory
update notice (utils/cli.py's print_update_notice), so dismissing the
banner in the browser also quiets the CLI notice for the same window,
and vice versa - there's exactly one dismissal state, not one per
surface. Stored server-side (a small JSON file under project_root/
cache/ - same gitignored, per-install convention utils/update_check.py's
own update_check_cache.json uses, see that module's _cache_path())
rather than a browser cookie: this is a single-tenant, localhost-only
app with no other session/auth boundary to key a per-browser cookie
against (see web/app.py's module docstring), and a server-side file is
the only way the CLI notice - a separate process/invocation with no
HTTP request or cookie jar at all - can ever see what the web UI
dismissed.

Dismissal is a SNOOZE, not permanent (v2.8.31 - previously the web
banner's now-removed per-version cookie stayed dismissed for a full
year, effectively forever for that version). Semantics:
  - Dismissing the currently-offered version hides it for
    DISMISS_SNOOZE_DAYS; after that, the SAME version is offered again.
    This matters because, as of v2.8.31, update_mode: off/notify never
    auto-apply anything - without a re-notify, a user who dismissed
    once could otherwise miss a pending update indefinitely.
  - A version NEWER than the one that was dismissed always overrides
    an active snooze immediately - dismissal is scoped to the EXACT
    version string dismissed, never "any future update".

Fail-open by construction, matching utils.update_check's own contract:
any read/write error here means "not dismissed" (the notice is shown)
or "dismissal not recorded" (never "silently remember a dismissal that
didn't actually happen") - a broken dismissal state must never be the
reason an update goes unnoticed.
"""

import json
import logging
import os
import time
from typing import Optional

from .helpers import get_project_root

logger = logging.getLogger('curatarr')

# How long dismissing a specific version snoozes it for. Deliberately
# much shorter than UPDATE_CHECK_INTERVAL_HOURS-driven version
# *freshness* (utils.update_check) staying at ~12h - this constant only
# governs how long a dismissal is honored, never how often the network
# is polled for a possibly-newer version.
DISMISS_SNOOZE_DAYS = 7
DISMISS_SNOOZE_SECONDS = DISMISS_SNOOZE_DAYS * 24 * 60 * 60

_DISMISSAL_FILENAME = 'dismissed_update.json'


def _dismissal_path() -> str:
    # Same directory/convention as utils.update_check._cache_path() -
    # project_root/cache/, gitignored, never directly in project_root
    # itself (which for a source install IS the git checkout).
    cache_dir = os.path.join(get_project_root(), 'cache')
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except Exception as e:
        logger.debug(f"Could not create update-dismissal cache dir: {e}")
    return os.path.join(cache_dir, _DISMISSAL_FILENAME)


def record_dismissal(version: str, now: Optional[float] = None) -> None:
    """Persist that `version` (e.g. "2.9.0", no 'v' prefix - matches
    update_available()'s own `latest` shape) was just dismissed.

    Never raises: a failure to persist just means the notice keeps
    showing up on the next check, the same fail-toward-"still notify"
    direction as every other error path in this module.

    Args:
        version: the exact version string that was dismissed.
        now: override for the current time - tests only; real callers
            never pass this (defaults to time.time()).
    """
    if not version:
        return
    path = _dismissal_path()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(
                {'version': version, 'dismissed_at': now if now is not None else time.time()},
                f,
            )
    except Exception as e:
        logger.debug(f"Could not write update-dismissal state: {e}")


def _read_dismissal() -> Optional[dict]:
    path = _dismissal_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def is_dismissed(version: str, now: Optional[float] = None) -> bool:
    """True if `version` is currently snoozed - see module docstring for
    the exact-version-match + DISMISS_SNOOZE_DAYS-elapsed contract.

    Fails open (False - i.e. the notice IS shown) on: no dismissal ever
    recorded, an unreadable/corrupt state file, a dismissal for a
    DIFFERENT version (including an older one - a newer release always
    overrides an existing snooze), or a snooze whose window has already
    elapsed.

    Args:
        version: the version currently being offered (e.g. `latest`
            from update_available()).
        now: override for the current time - tests only; real callers
            never pass this (defaults to time.time()).
    """
    if not version:
        return False
    state = _read_dismissal()
    if not state or state.get('version') != version:
        return False
    dismissed_at = state.get('dismissed_at')
    if not isinstance(dismissed_at, (int, float)):
        return False
    current_time = now if now is not None else time.time()
    return (current_time - dismissed_at) < DISMISS_SNOOZE_SECONDS
