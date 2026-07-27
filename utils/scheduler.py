"""In-app scheduler (#264): fires a "full" recommender run at a
configured daily wall-clock time, optionally restricted to specific
weekdays, from inside the long-running web UI process (both native
web/app.py and web/docker_server.py).

This is deliberately NOT a replacement for the existing host-cron /
docker-compose.yml `schedule` profile approach (see docs/DOCKER.md) -
both are documented as coexisting alternatives, and this stays
default-off. The two mechanisms share the exact same cross-container
run lock (utils.run_lock.PosixRunLock, taken internally by
web.job_runner.JobManager.start() for every run regardless of what
triggered it) so a run this scheduler fires can never overlap with one
docker-entrypoint.sh's `recommend` mode started in a sibling container -
but nothing here can stop a user from ALSO having host cron trigger a
run at a different time of day if they enable both; see docs/DOCKER.md
for why that's a docs problem, not something detectable/preventable
from in here (no Docker socket access, no host filesystem visibility
from inside this container - there is no reliable way to see a sibling
container's compose profile or a host crontab from here).

Config shape (config.yml, alongside general/logging - see
web/config_settings.py's Settings screen, "Scheduling" section):

    schedule:
      enabled: false
      time: "03:00"                       # 24-hour HH:MM, local to TZ below
      weekdays: [monday, wednesday, friday]  # optional; omit/empty = every day

Timezone: the container's own TZ environment variable, nothing else -
deliberately no separate schedule.timezone config key, so there is
exactly one timezone concept in play (the same one that already governs
every other system-clock-dependent thing in this process), not two that
could silently disagree. Falls back to UTC when TZ is unset (Docker's
own default when -e TZ=... isn't passed) or set to something the system
tzdata doesn't recognize.
"""

import logging
import os
import re
import zoneinfo
from datetime import datetime
from typing import Dict, Optional, Set, Tuple

from .display import log_warning

logger = logging.getLogger("curatarr")

_TIME_RE = re.compile(r"^([01]?[0-9]|2[0-3]):([0-5][0-9])$")

WEEKDAY_NAMES: Tuple[str, ...] = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_WEEKDAY_NAME_TO_INDEX: Dict[str, int] = {name: i for i, name in enumerate(WEEKDAY_NAMES)}


def resolve_scheduler_timezone() -> zoneinfo.ZoneInfo:
    """The container TZ env var, nothing else (#264) - see module
    docstring for why there's deliberately no separate config key for
    this. Falls back to UTC when TZ is unset or unrecognized, logging a
    warning in the latter case only (an unset TZ defaulting to UTC is
    completely normal and not worth a warning every time this is
    called; a TYPO'd TZ silently falling back to UTC is the actual
    "which clock is this?" ambiguity #264 is about, so that case does
    warn)."""
    tz_name = os.environ.get("TZ", "").strip()
    if not tz_name:
        return zoneinfo.ZoneInfo("UTC")
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception as e:
        log_warning(f"TZ={tz_name!r} is not a recognized timezone ({e}) - scheduler falling back to UTC")
        return zoneinfo.ZoneInfo("UTC")


def parse_schedule_config(schedule_cfg: Dict) -> Tuple[int, int, Optional[Set[int]]]:
    """
    Parse+validate a schedule: config section into (hour, minute,
    weekdays). weekdays is None for "every day" - schedule.weekdays
    omitted entirely, or present but empty, are treated identically
    (an admin unchecking every weekday checkbox in the web UI is far
    more likely to be an accidental "select none" than an intentional
    "never run" - "never run" is already spelled schedule.enabled:
    false).

    Raises ValueError (with a human-readable message) for a malformed
    schedule.time or an unrecognized schedule.weekdays entry - callers
    must catch this and fail safe (log + leave the scheduler idle this
    tick), never let it crash the scheduler thread or, worse, a run.
    """
    time_str = str(schedule_cfg.get("time", "")).strip()
    match = _TIME_RE.match(time_str)
    if not match:
        raise ValueError(f"schedule.time must be 24-hour HH:MM (e.g. '03:00') - got {time_str!r}")
    hour, minute = int(match.group(1)), int(match.group(2))

    raw_weekdays = schedule_cfg.get("weekdays")
    if not raw_weekdays:
        return hour, minute, None

    weekdays: Set[int] = set()
    for raw in raw_weekdays:
        key = str(raw).strip().lower()
        if key not in _WEEKDAY_NAME_TO_INDEX:
            raise ValueError(
                f"schedule.weekdays: unrecognized day {raw!r} - expected one of {', '.join(WEEKDAY_NAMES)}"
            )
        weekdays.add(_WEEKDAY_NAME_TO_INDEX[key])
    return hour, minute, weekdays


def compute_next_run(now: datetime, hour: int, minute: int, weekdays: Optional[Set[int]] = None) -> datetime:
    """
    Next occurrence of *hour*:*minute* local time (in now.tzinfo)
    strictly after *now*, optionally restricted to *weekdays*
    (datetime.weekday() indices - 0=Monday, matching WEEKDAY_NAMES'
    order). Always returns a datetime AFTER now, never equal to or
    before it - so calling this fresh (e.g. right after startup, or
    right after a config change) can never "catch up" a missed
    occurrence (#264: the scheduler must never fire missed runs on
    restart - see web/scheduler_runner.py's own use of this, which
    always recomputes from the current moment, never from a stale
    prior target).

    DST correctness (verified by tests/test_scheduler.py):
    - Spring-forward (a wall-clock hour is skipped that day): zoneinfo
      resolves a nonexistent local time (PEP 495, default fold=0)
      using the pre-transition UTC offset, which lands at the first
      real instant after the gap closes - the run happens, shifted
      later by the gap size, never skipped entirely.
    - Fall-back (a wall-clock hour repeats that day): zoneinfo's
      default fold=0 always resolves to the FIRST of the two
      occurrences, and this function only ever evaluates one candidate
      per calendar day (advancing a full day per loop iteration, never
      re-evaluating one) - so the repeated hour is never independently
      considered a second time; it fires once.

    Raises ValueError if weekdays is an empty set (distinguish this
    from None="every day" at the call site - parse_schedule_config
    above already normalizes an empty/omitted config value to None,
    so an empty SET reaching here would be a caller bug, not a valid
    "never" state).
    """
    if weekdays is not None and not weekdays:
        raise ValueError("weekdays must be None (every day) or a non-empty set, not an empty set")

    candidate_date = now.date()
    for _ in range(8):  # a week out is always enough to hit a matching weekday
        candidate = datetime(
            candidate_date.year, candidate_date.month, candidate_date.day, hour, minute, tzinfo=now.tzinfo
        )
        if (weekdays is None or candidate.weekday() in weekdays) and candidate > now:
            return candidate
        candidate_date = candidate_date.fromordinal(candidate_date.toordinal() + 1)

    raise ValueError("No matching weekday found within 8 days - this should be unreachable")


def describe_next_run(config: Dict) -> Dict:
    """
    Read-only summary for the UI (Settings/Dashboard): whether the
    schedule is enabled, the resolved timezone name, and the next run
    time - or why there isn't one (disabled, or an invalid
    schedule.time/weekdays). Never raises; a bad config shows up as
    result["error"], not an exception - this is called from request
    handlers rendering a page, which must never 500 over a typo in
    tuning.yml/config.yml.

    Returns:
        {"enabled": bool, "timezone": str, "next_run": Optional[datetime], "error": Optional[str]}
    """
    schedule_cfg = (config or {}).get("schedule") or {}
    enabled = bool(schedule_cfg.get("enabled"))
    tz = resolve_scheduler_timezone()
    result: Dict = {"enabled": enabled, "timezone": str(tz), "next_run": None, "error": None}
    if not enabled:
        return result
    try:
        hour, minute, weekdays = parse_schedule_config(schedule_cfg)
        result["next_run"] = compute_next_run(datetime.now(tz), hour, minute, weekdays)
    except ValueError as e:
        result["error"] = str(e)
    return result
