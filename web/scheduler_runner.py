"""Background thread wiring for the #264 in-app scheduler - the part
that actually watches the clock and fires JobManager.start("full", "all",
...) at the configured time. The scheduling MATH itself (timezone
resolution, HH:MM/weekday parsing, DST-correct next-run computation)
lives in utils/scheduler.py, kept separate from and independent of
Flask/JobManager so it is trivially unit-testable without any of this
thread/lock machinery - see tests/test_scheduler.py for that, and
tests/test_scheduler_runner.py for this module's own (thread-free,
directly-call-_tick()) tests.

Started explicitly from web/app.py's and web/docker_server.py's own
main() - NOT from create_app() itself, so the hundreds of existing
tests that call create_app() directly never spawn a real background
thread (mirroring how atexit/signal registration for job-manager
shutdown already works the same way - see those main()s).
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from utils import get_users_from_config
from utils.display import log_error, log_info, log_warning
from utils.scheduler import compute_next_run, parse_schedule_config, resolve_scheduler_timezone

from .job_runner import JobAlreadyRunningError, JobError, JobManager

logger = logging.getLogger("curatarr")

DEFAULT_POLL_INTERVAL_SECONDS = 30


class SchedulerState:
    """Thread-safe holder for the scheduler's last-fire-attempt result,
    read by web routes (dashboard "last scheduled run" display), written
    by SchedulerThread. Deliberately in-memory only, not persisted - a
    fresh "nothing attempted yet this session" after a restart is
    honest and correct, matching #264's own "never catch up on restart"
    design: there is nothing from a previous process run worth showing
    as if it just happened.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_attempt_at: Optional[datetime] = None
        self._last_result: Optional[str] = None

    def record(self, result: str) -> None:
        with self._lock:
            self._last_attempt_at = datetime.now(timezone.utc)
            self._last_result = result

    def snapshot(self) -> Dict:
        with self._lock:
            return {"last_attempt_at": self._last_attempt_at, "last_result": self._last_result}


class SchedulerThread(threading.Thread):
    """Daemon thread: wakes up every poll_interval_seconds, reloads the
    live config (via *load_config_fn* - the SAME mtime-keyed cache
    web/app.py's create_app() already uses, so a schedule saved through
    the web UI takes effect on the very next tick, no restart needed -
    #264), and fires job_manager.start("full", "all", ...) at the
    computed next-run time.

    Never fires a missed occurrence: next_fire_target is always
    (re)computed strictly forward from the current moment, both at
    startup and immediately after firing - see utils.scheduler.
    compute_next_run's own docstring for why this structurally cannot
    "catch up".

    Relies entirely on job_manager.start() to enforce the cross-
    container PosixRunLock (utils/run_lock.py) and the in-process
    single-run lock - this thread does neither itself, it just calls
    .start() and reacts to JobAlreadyRunningError by skipping (logging
    why, at a visible level) rather than retrying/queuing.
    """

    def __init__(
        self,
        job_manager: JobManager,
        load_config_fn: Callable[[], Optional[Dict]],
        poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(name="curatarr-scheduler", daemon=True)
        self._job_manager = job_manager
        self._load_config = load_config_fn
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self.state = SchedulerState()
        self._next_fire_target: Optional[datetime] = None
        self._last_seen_schedule_key: Optional[tuple] = None

    def stop(self) -> None:
        """Signal the loop to exit at its next wait boundary - best-
        effort only (this is a daemon thread, so process exit does not
        actually depend on this ever being called; JobManager.
        terminate_running() already covers killing an in-flight
        scheduled run the same as any web-UI-triggered one)."""
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:  # never let a bug here kill the thread
                log_error(f"Scheduler tick failed unexpectedly: {exc}")
            self._stop_event.wait(self._poll_interval_seconds)

    def _tick(self) -> None:
        config = self._load_config()
        schedule_cfg = (config or {}).get("schedule") or {}
        if not schedule_cfg.get("enabled"):
            self._next_fire_target = None
            self._last_seen_schedule_key = None
            return

        try:
            hour, minute, weekdays = parse_schedule_config(schedule_cfg)
        except ValueError as e:
            # Warn once per distinct bad value, not once per 30s tick -
            # _last_seen_schedule_key doubles as "the last thing we
            # already logged about" so an admin staring at a broken
            # config doesn't get spammed, but a NEW/different mistake
            # (or the same one after a restart) still gets reported.
            invalid_marker = ("invalid", str(e))
            if self._last_seen_schedule_key != invalid_marker:
                log_warning(f"Scheduler: invalid schedule config ({e}) - idle until fixed")
                self._last_seen_schedule_key = invalid_marker
            self._next_fire_target = None
            return

        tz = resolve_scheduler_timezone()
        now = datetime.now(tz)
        schedule_key = (hour, minute, tuple(sorted(weekdays)) if weekdays else None)

        if self._next_fire_target is None or schedule_key != self._last_seen_schedule_key:
            # Freshly enabled, just (re)started, or the schedule's own
            # values changed since the last tick - always recompute
            # strictly forward from NOW, never from a stale prior
            # target (which might reflect config that no longer
            # applies). This is what makes a live config edit take
            # effect on the very next tick, and what guarantees this
            # thread never fires anything "missed" right after startup.
            self._next_fire_target = compute_next_run(now, hour, minute, weekdays)
            self._last_seen_schedule_key = schedule_key
            log_info(f"Scheduler: next run at {self._next_fire_target.isoformat()}")
            return

        if now >= self._next_fire_target:
            self._fire(config)
            self._next_fire_target = compute_next_run(now, hour, minute, weekdays)
            log_info(f"Scheduler: next run at {self._next_fire_target.isoformat()}")

    def _fire(self, config: Optional[Dict]) -> None:
        users: List[str] = get_users_from_config(config) if config else []
        try:
            self._job_manager.start("full", "all", users)
            self.state.record("started")
            log_info("Scheduler: started scheduled 'full' run")
        except JobAlreadyRunningError as exc:
            self.state.record(f"skipped - {exc}")
            log_warning(f"Scheduler: skipped this occurrence - {exc}")
        except JobError as exc:
            self.state.record(f"error - {exc}")
            log_error(f"Scheduler: could not start scheduled run - {exc}")
