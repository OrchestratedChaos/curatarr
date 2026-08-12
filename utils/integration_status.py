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

"""Small, dependency-free integration-health signal.

Lets the web UI show "this Trakt export attempt failed" without
grepping any log file for an English error string - fragile, and
exactly how a months-long silent Trakt token-refresh failure hid (every
run exited 0 and the dashboard reported "succeeded" the whole time; see
CHANGELOG's Trakt token-refresh-persistence entry). This module is the
explicit, structured alternative: the code that actually attempts an
integration call records its own real outcome here, and any reader
(the web dashboard, /status.json, a future CLI check) reads that
outcome back directly - no string matching involved.

Deliberately narrow: one JSON file per named integration under
cache/, holding only the LAST attempt's outcome - not a history, not a
queue. A run that succeeds after a prior failure overwrites the file,
so the signal always reflects the CURRENT state, never a stale one.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .redact import redact

logger = logging.getLogger("curatarr")


def _status_path(cache_dir: str, name: str) -> str:
    return os.path.join(cache_dir, f"integration_status_{name}.json")


def record_integration_status(cache_dir: str, name: str, success: bool, detail: str = "") -> None:
    """Persist the outcome of the most recent *name* integration attempt
    (e.g. "trakt_export").

    *detail* is redacted (see utils/redact.py) before being written -
    it's frequently str(exception), which could in principle echo a
    token (an API error body reflecting a bad request, for instance) -
    the same defense-in-depth already applied to log_warning/log_error.

    Atomic (temp file + rename) so a concurrent reader (the web
    dashboard rendering a page while a run is mid-write) never sees a
    half-written file, and a crash mid-write never corrupts the
    previous, still-valid status.

    Never raises - recording status must never itself fail (or further
    complicate) the run that's already succeeding/failing; any error
    here is logged at debug level and swallowed.
    """
    try:
        os.makedirs(cache_dir, exist_ok=True)
        payload: Dict[str, Any] = {
            "success": success,
            "detail": redact(detail),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=cache_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_path, _status_path(cache_dir, name))
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    except Exception as e:
        logger.debug(f"Could not record integration status for {name}: {e}")


def get_integration_status(cache_dir: str, name: str) -> Optional[Dict[str, Any]]:
    """Return the last recorded {"success", "detail", "timestamp"} dict
    for *name*, or None if nothing's been recorded yet, or the file
    can't be read/parsed.

    Callers must treat None as "unknown" (never attempted, or this
    install predates this feature) - never as "failed".
    """
    path = _status_path(cache_dir, name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "success" not in data:
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None
