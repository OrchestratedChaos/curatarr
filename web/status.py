"""Read-only helpers for parsing curatarr's existing log files and
generated output. Nothing here writes logs or mutates recommender
state - it only globs/reads files that recommenders/*.py and
web/job_runner.py already produce.
"""

import glob
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

from .security import redact, safe_join

_TIMESTAMP_RE = re.compile(r'_(\d{8}_\d{6})\.log$')

# Lowercased substrings that indicate a recommender run hit an error.
# This is a heuristic (per the MVP spec), not a guarantee - a run can
# print warnings along the way and still succeed overall.
_FAILURE_MARKERS = (
    'traceback (most recent call last)',
    'fatal error',
    'an error occurred',
)

# Cap how much of a log file we read for status/display purposes.
TAIL_BYTES = 200_000


def _parse_timestamp(filename: str) -> Optional[datetime]:
    match = _TIMESTAMP_RE.search(filename)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), '%Y%m%d_%H%M%S')
    except ValueError:
        return None


def _read_tail(path: str, max_bytes: int = TAIL_BYTES) -> str:
    """Best-effort read of up to the last max_bytes of a file as text."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
        return data.decode('utf-8', errors='replace')
    except OSError:
        return ''


def latest_user_log(logs_dir: str, username: str) -> Optional[str]:
    """Return the path to the newest recommendations_<username>_*.log file.

    Note: movie.py and tv.py both write into this same naming pattern,
    so "latest" reflects whichever of the two most recently ran for
    this user, not necessarily a single combined run.
    """
    pattern = os.path.join(logs_dir, f'recommendations_{username}_*.log')
    candidates = glob.glob(pattern)
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def get_last_run_status(logs_dir: str, username: str) -> Dict:
    """Heuristic last-run status for one user, derived from their newest log.

    Returns a dict with keys:
      status: 'never_run' | 'success' | 'failed' | 'unknown'
      timestamp: datetime or None
      log_file: basename of the log, or None
    """
    log_path = latest_user_log(logs_dir, username)
    if not log_path:
        return {'status': 'never_run', 'timestamp': None, 'log_file': None}

    basename = os.path.basename(log_path)
    timestamp = _parse_timestamp(basename)
    if timestamp is None:
        timestamp = datetime.fromtimestamp(os.path.getmtime(log_path))

    content = _read_tail(log_path)
    if not content.strip():
        status = 'unknown'
    elif any(marker in content.lower() for marker in _FAILURE_MARKERS):
        status = 'failed'
    else:
        status = 'success'

    return {'status': status, 'timestamp': timestamp, 'log_file': basename}


def list_log_files(logs_dir: str) -> List[Dict]:
    """List every logs/*.log file, newest first, with size + mtime."""
    if not os.path.isdir(logs_dir):
        return []
    entries = []
    for name in os.listdir(logs_dir):
        if not name.endswith('.log'):
            continue
        path = os.path.join(logs_dir, name)
        if not os.path.isfile(path):
            continue
        entries.append({
            'name': name,
            'size': os.path.getsize(path),
            'mtime': datetime.fromtimestamp(os.path.getmtime(path)),
        })
    entries.sort(key=lambda e: e['mtime'], reverse=True)
    return entries


def display_name_safe_slug(config: Dict, username: str) -> str:
    """Mirror recommenders/external_output.py's filename derivation:
    display_name (falling back to the username itself), lowercased,
    spaces replaced with underscores.
    """
    prefs = (config or {}).get('users', {}).get('preferences', {}) or {}
    display_name = (prefs.get(username) or {}).get('display_name', username)
    return str(display_name).lower().replace(' ', '_')


def find_user_watchlist(external_dir: str, config: Dict, username: str) -> Optional[str]:
    """Return the basename of this user's generated watchlist, if any.

    Prefers the per-user "<display_name>_watchlist.html" file that
    recommenders/external_output.py writes; falls back to the combined
    (all-users, tabbed) "watchlist.html" if that's all that exists yet.
    Returns None if neither has been generated.
    """
    slug = display_name_safe_slug(config, username)
    per_user = f'{slug}_watchlist.html'
    if os.path.isfile(os.path.join(external_dir, per_user)):
        return per_user

    combined = 'watchlist.html'
    if os.path.isfile(os.path.join(external_dir, combined)):
        return combined

    return None


def read_log_tail(logs_dir: str, filename: str, max_lines: int = 500) -> str:
    """Read the last max_lines of logs_dir/filename, secrets redacted.

    Raises FileNotFoundError if filename escapes logs_dir or doesn't
    resolve to a real file.
    """
    path = safe_join(logs_dir, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(filename)
    content = _read_tail(path)
    lines = content.splitlines()[-max_lines:]
    return redact('\n'.join(lines))
