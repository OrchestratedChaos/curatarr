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

"""Explicit, structured last-run outcome per (engine, user) - the same
shape as utils/integration_status.py (see that module's own docstring
for the original rationale), applied to movie/tv/external recommender
runs themselves rather than a single downstream integration call.

#292: web/status.py's get_last_run_status() used to infer success/
failure entirely by grepping the tail of whichever log file
latest_user_log() happened to pick for English error-ish substrings
("traceback (most recent call last)", "fatal error", "an error
occurred"). Two independent, real, confirmed failure modes of that
approach:

  1. A failure phrased any other way reads as success. Concrete
     precedent: recommenders/external_sync.py logged
     "Failed to export <user> to Trakt: Cannot get lists: not
     authenticated" for months - matching NONE of those markers - and
     the dashboard reported success the entire time (the reason
     utils/integration_status.py itself exists, for that one specific
     integration).
  2. movie.py and tv.py both write to the identical
     "recommendations_<user>_<timestamp>.log" naming (see
     recommenders/movie.py's/tv.py's own process_recommendations() -
     both pass the literal string "recommendations" to
     setup_log_file(), not "movie"/"tv"), so latest_user_log()'s
     newest-by-mtime pick can silently show one engine's outcome while
     masking a DIFFERENT, older failure from the other engine for the
     same user.

The recommender itself now records its own real outcome here - one
JSON file per (engine, user), holding only the LAST attempt, not a
history - immediately after that (engine, user) pair finishes
processing (recommenders/movie.py's/tv.py's process_recommendations(),
recommenders/external.py's per-user loop), success/failure exactly as
that code itself observed it (did processing for this user raise at
all - independent of whether utils.cli's own fatal-keyword heuristic
additionally decided to sys.exit() over it), with no string-matching
involved on the reading side. get_last_run_status() reads these back
directly per user, comparing the movie/tv records' own timestamps
(never file mtime) to resolve which engine's outcome is actually
newest - solving failure mode 2 above as a side effect of no longer
needing to guess from a shared filename pattern at all.

Falls back to the pre-existing log-tail heuristic only when neither
engine has ever recorded a status for a user yet (an install predating
this feature, or a run from before it shipped) - never removed, since
every already-written log file on every existing install has no
recorded status to read.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .redact import redact

logger = logging.getLogger("curatarr")

# Recorded outcomes only ever exist for these - not "full" (that's the
# `full` engine's own bash-chain construction in web/job_runner.py
# invoking movie/tv/external as separate subprocesses, each of which
# records its own status here under its own engine name) and not
# per-library (a multi-library run still resolves to one "did this
# user's movie processing raise" outcome per engine, same granularity
# get_last_run_status()'s existing per-user contract already expects).
RUN_STATUS_ENGINES = ("movie", "tv", "external")


def _status_path(logs_dir: str, engine: str, username: str) -> str:
    # glob.escape-style safety isn't needed here (this is a fixed path
    # write/read, never a glob pattern) but the username is still
    # sanitized enough by os.path.join's own semantics - a username
    # containing a path separator would need to already be an accepted,
    # validated Plex username (see utils.config.get_users_from_config)
    # to reach this code path at all.
    return os.path.join(logs_dir, f"run_status_{engine}_{username}.json")


def record_run_status(logs_dir: str, engine: str, username: str, success: bool, detail: str = "") -> None:
    """Persist the outcome of the most recent *engine* run for
    *username* (e.g. engine="movie", username="alice").

    *detail* is redacted (see utils/redact.py) before being written -
    frequently str(exception), which could in principle echo a token.

    Atomic (temp file + rename), same as record_integration_status - a
    concurrent reader (the dashboard rendering while a run is
    mid-write) never sees a half-written file, and a crash mid-write
    never corrupts the previous, still-valid status.

    Never raises - recording status must never itself fail (or further
    complicate) a run that's already succeeding/failing; any error here
    is logged at debug level and swallowed.
    """
    try:
        os.makedirs(logs_dir, exist_ok=True)
        payload: Dict[str, Any] = {
            "engine": engine,
            "user": username,
            "success": success,
            "detail": redact(detail),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=logs_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_path, _status_path(logs_dir, engine, username))
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    except Exception as e:
        logger.debug(f"Could not record run status for {engine}/{username}: {e}")


def get_run_status(logs_dir: str, engine: str, username: str) -> Optional[Dict[str, Any]]:
    """Return the last recorded {"engine", "user", "success", "detail",
    "timestamp"} dict for (engine, username), or None if nothing's been
    recorded yet, or the file can't be read/parsed.

    Callers must treat None as "no explicit signal recorded" - never as
    "failed" - see get_last_run_status()'s fallback to the legacy
    log-tail heuristic for exactly that case.
    """
    path = _status_path(logs_dir, engine, username)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "success" not in data or "timestamp" not in data:
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None


def get_latest_run_status_for_user(logs_dir: str, username: str) -> Optional[Dict[str, Any]]:
    """The newest recorded status across every engine for *username*
    (movie/tv/external), by the record's OWN timestamp - never file
    mtime, which is what let movie.py's and tv.py's shared
    "recommendations_<user>_*.log" naming mask one engine's failure
    behind the other's later success (see this module's own docstring).

    Returns None if no engine has ever recorded a status for this user
    - the caller's cue to fall back to the legacy log-tail heuristic.
    """
    best: Optional[Dict[str, Any]] = None
    best_ts: Optional[datetime] = None
    for engine in RUN_STATUS_ENGINES:
        record = get_run_status(logs_dir, engine, username)
        if record is None:
            continue
        try:
            ts = datetime.fromisoformat(record["timestamp"])
        except (KeyError, ValueError):
            continue
        if best_ts is None or ts > best_ts:
            best, best_ts = record, ts
    return best
