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

"""Read-only helpers for parsing curatarr's existing log files and
generated output. Nothing here writes logs or mutates recommender
state - it only globs/reads files that recommenders/*.py and
web/job_runner.py already produce.
"""

import glob
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from utils import get_latest_run_status_for_user

from .security import redact, safe_join

_TIMESTAMP_RE = re.compile(r"_(\d{8}_\d{6})\.log$")

# Lowercased substrings that indicate a recommender run hit an error.
# #292: this heuristic is ONLY the fallback now, for a user with no
# utils.run_status record yet (an install predating that fix, or a run
# from before it shipped) - see get_last_run_status()'s own docstring.
# Confirmed real failure mode while this WAS the only signal:
# recommenders/external_sync.py logged "Failed to export <user> to
# Trakt: Cannot get lists: not authenticated" for months, matching none
# of these, and the dashboard reported success throughout.
_FAILURE_MARKERS = (
    "traceback (most recent call last)",
    "fatal error",
    "an error occurred",
)

# Cap how much of a log file we read for status/display (the legacy
# marker-matching fallback above) and for the log viewer's default
# "tail" view (read_log_tail()) - see read_log_full() for the
# unbounded (up to LOG_VIEW_MAX_BYTES) alternative the viewer also
# offers (#283), and cleanup_old_logs() (utils/helpers.py) for why a
# real log file can legitimately be up to ~20MB before this ever reads
# it - both deliberately independent of that retention size.
TAIL_BYTES = 200_000

# Hard ceiling for read_log_full()'s whole-file read (#283). Generous
# headroom above cleanup_old_logs()'s ~20MB retention target - this is
# a memory-safety backstop for a misconfigured/legacy-oversized log,
# not a normal ceiling any real log is expected to hit.
LOG_VIEW_MAX_BYTES = 50_000_000


def _parse_timestamp(filename: str) -> Optional[datetime]:
    match = _TIMESTAMP_RE.search(filename)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _read_tail(path: str, max_bytes: int = TAIL_BYTES) -> str:
    """Best-effort read of up to the last max_bytes of a file as text.

    Thin wrapper around _read_tail_with_reason() for callers that don't
    need to distinguish WHY an empty result came back - see that
    function's docstring (#263).
    """
    content, _reason = _read_tail_with_reason(path, max_bytes)
    return content


def _read_tail_with_reason(path: str, max_bytes: int = TAIL_BYTES) -> Tuple[str, Optional[str]]:
    """Same read as _read_tail, but also returns WHY the content is empty
    when it is - _read_tail alone collapsed "0-byte log, run was
    interrupted before writing anything" and "file exists but can't be
    read (permissions, race with log-retention cleanup, etc.)" into the
    same bare "" (#263), which is exactly what made get_last_run_status's
    "unknown" status unexplainable - a real interrupted-run report showed
    an "unknown" badge with a live "view log" link, and the log pane was
    just blank with no indication of why.

    Returns:
        (content, reason) - reason is None whenever content is genuinely
        non-empty; otherwise a short human-readable explanation.
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", f"log file unreadable ({e.strerror or e})"

    try:
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
    except OSError as e:
        return "", f"log file unreadable ({e.strerror or e})"

    content = data.decode("utf-8", errors="replace")
    if size == 0:
        return content, "log file is empty (0 bytes) - the run was likely interrupted before writing any output"
    return content, None


def latest_user_log(logs_dir: str, username: str) -> Optional[str]:
    """Return the path to the newest recommendations_<username>_*.log file.

    Note: movie.py and tv.py both write into this same naming pattern,
    so "latest" reflects whichever of the two most recently ran for
    this user, not necessarily a single combined run.
    """
    # glob.escape the username so a name containing glob special chars
    # (*, ?, [...]) can't turn this into an unintended wildcard match -
    # e.g. a Plex username of "*" would otherwise match every user's
    # log files instead of just this one, leaking other users' run
    # status onto this user's dashboard row.
    pattern = os.path.join(logs_dir, f"recommendations_{glob.escape(username)}_*.log")
    candidates = glob.glob(pattern)
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def get_last_run_status(logs_dir: str, username: str) -> Dict:
    """Last-run status for one user.

    #292: prefers the explicit, structured signal recommenders/movie.py's/
    tv.py's/external.py's own per-user processing records (see
    utils/run_status.py) - the actual outcome that code itself observed,
    read back directly with no string-matching involved. Falls back to
    the legacy log-tail marker-matching heuristic ONLY when neither
    engine has ever recorded a status for this user (an install
    predating this fix, or a run from before it shipped) - every
    already-written log file on every existing install has no recorded
    status to prefer, so this fallback is never removed.

    The explicit path also fixes a second, independent bug the legacy
    path had: movie.py and tv.py both write to the identical
    "recommendations_<user>_<timestamp>.log" naming, so
    latest_user_log()'s newest-by-mtime pick could show one engine's
    outcome while masking a different, OLDER failure from the other -
    the explicit signal instead compares each engine's OWN recorded
    timestamp (see get_latest_run_status_for_user()), never file mtime.

    latest_user_log() is still used for `log_file` (the "view log" link)
    in both paths - that's just "which log is relevant to look at", a
    much lower-stakes question than "did the run succeed", and the
    explicit signal doesn't currently record its own log filename (see
    that module's docstring for why: movie.py/tv.py/external.py all
    resolve it independently in the same process, milliseconds apart,
    so re-deriving it here is simpler than plumbing an exact filename
    through record_run_status just for this).

    Returns a dict with keys:
      status: 'never_run' | 'success' | 'failed' | 'unknown'
      timestamp: datetime or None
      log_file: basename of the log, or None
      reason: human-readable explanation - the failure detail itself
        for an explicit-signal 'failed' status, or (#263, legacy path
        only) why status is 'unknown' - None otherwise.
    """
    log_path = latest_user_log(logs_dir, username)
    log_file = os.path.basename(log_path) if log_path else None

    explicit = get_latest_run_status_for_user(logs_dir, username)
    if explicit is not None:
        timestamp: Optional[datetime] = None
        try:
            # Stored UTC-aware (see record_run_status) - every other
            # timestamp this module produces is a naive local datetime
            # (from _parse_timestamp()/os.path.getmtime() below), same
            # shape dashboard.html's Jinja formatting and /status.json's
            # last_run.timestamp comparison already expect.
            timestamp = datetime.fromisoformat(explicit["timestamp"]).astimezone().replace(tzinfo=None)
        except (KeyError, ValueError):
            pass
        success = bool(explicit.get("success"))
        return {
            "status": "success" if success else "failed",
            "timestamp": timestamp,
            "log_file": log_file,
            "reason": None if success else (explicit.get("detail") or None),
        }

    # Legacy fallback: no explicit signal recorded for this user on
    # either engine yet - infer from the log tail exactly as before.
    if not log_path:
        return {"status": "never_run", "timestamp": None, "log_file": None, "reason": None}

    timestamp = _parse_timestamp(log_file or "")
    if timestamp is None:
        try:
            timestamp = datetime.fromtimestamp(os.path.getmtime(log_path))
        except OSError:
            # Log was removed (e.g. log-retention cleanup) between the
            # glob() in latest_user_log() and here - fall back to no
            # timestamp rather than 500ing the dashboard.
            timestamp = None

    content, empty_reason = _read_tail_with_reason(log_path)
    reason: Optional[str] = None
    if not content.strip():
        status = "unknown"
        reason = empty_reason or "log file has no readable content"
    elif any(marker in content.lower() for marker in _FAILURE_MARKERS):
        status = "failed"
    else:
        status = "success"

    return {"status": status, "timestamp": timestamp, "log_file": log_file, "reason": reason}


def list_log_files(logs_dir: str) -> List[Dict]:
    """List every logs/*.log file, newest first, with size + mtime."""
    if not os.path.isdir(logs_dir):
        return []
    entries: List[Dict[str, Any]] = []
    for name in os.listdir(logs_dir):
        if not name.endswith(".log"):
            continue
        path = os.path.join(logs_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            # Deleted between listdir() and here (e.g. concurrent log
            # rotation/cleanup) - skip it rather than 500ing /results.
            continue
        entries.append({"name": name, "size": size, "mtime": mtime})
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


def find_user_watchlist(external_dir: str, config: Dict, username: str) -> Optional[str]:
    """Return the basename of this user's generated watchlist, if any.

    Returns the combined (all-users, tabbed) "watchlist.html" if it's
    been generated, else None.

    Used to prefer a per-user "<display_name>_watchlist.html"
    file first. recommenders/external_render.py has never written that
    file in this repo's tracked history (only the per-user markdown and
    the combined HTML - see generate_markdown()/generate_combined_html()
    there); the only files matching that pattern on disk were six-month-old
    stragglers from before this repo's history was truncated (a
    filter-repo at root commit dbd2054), never regenerated, and are now
    deleted. Every caller (web/app.py's dashboard()) was therefore
    already silently falling through to this same combined-file return
    on every real install - removing the dead branch changes nothing
    users see. config/username are still accepted (rather than
    narrowing this function's signature) so this stays a drop-in
    replacement for that one caller; neither is used internally
    anymore.
    """
    combined = "watchlist.html"
    if os.path.isfile(os.path.join(external_dir, combined)):
        return combined

    return None


def read_log_tail(logs_dir: str, filename: str, max_lines: int = 500) -> Tuple[str, Optional[str]]:
    """Read the last max_lines of logs_dir/filename, secrets redacted.

    Raises FileNotFoundError if filename escapes logs_dir, isn't a
    *.log file, or doesn't resolve to a real file.

    Returns (content, empty_reason) - empty_reason is None whenever
    content is non-empty, otherwise the same human-readable explanation
    get_last_run_status's "reason" surfaces (#263), so the log-view page
    can show why a log is blank instead of just rendering nothing.
    """
    if not filename.endswith(".log"):
        raise FileNotFoundError(filename)
    path = safe_join(logs_dir, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(filename)
    content, reason = _read_tail_with_reason(path)
    lines = content.splitlines()[-max_lines:]
    return redact("\n".join(lines)), (reason if not content.strip() else None)


def read_log_full(logs_dir: str, filename: str, max_bytes: int = LOG_VIEW_MAX_BYTES) -> Tuple[str, Optional[str], bool]:
    """Read up to max_bytes of logs_dir/filename in full, not just the
    last max_lines like read_log_tail() (#283: the log viewer only ever
    showed a fixed tail, with no way to reach the START of a long run's
    output). cleanup_old_logs() (utils/helpers.py) retains up to ~20MB
    per log file, so a real log can legitimately be large enough that
    reaching the true beginning requires more than the tail view shows.

    Raises FileNotFoundError under the same conditions as
    read_log_tail() (filename escapes logs_dir, isn't a *.log file, or
    doesn't resolve to a real file).

    Returns (content, empty_reason, truncated):
      empty_reason: same meaning as read_log_tail()'s (#263) - None
        unless content is empty.
      truncated: True if the file is larger than max_bytes and this is
        therefore still only its LAST max_bytes, not the genuine
        beginning - a memory-safety backstop for a misconfigured/
        unusually large log (LOG_VIEW_MAX_BYTES is well above
        cleanup_old_logs()'s own retention target), not something any
        normally-retained log is expected to hit.
    """
    if not filename.endswith(".log"):
        raise FileNotFoundError(filename)
    path = safe_join(logs_dir, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(filename)
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", f"log file unreadable ({e.strerror or e})", False
    truncated = size > max_bytes
    content, reason = _read_tail_with_reason(path, max_bytes=max_bytes)
    return redact(content), (reason if not content.strip() else None), truncated
