"""Tests for web/scheduler_runner.py - the #264 in-app scheduler's
background-thread integration (JobManager wiring, fire/skip/error
handling, state tracking). The scheduling MATH itself is tested
independently in tests/test_scheduler.py; these tests call
SchedulerThread._tick() directly rather than actually running the
thread, except for one end-to-end smoke test confirming start()/stop()
work at all.
"""

import os
import sys
import time
import zoneinfo
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.job_runner import JobAlreadyRunningError, JobError
from web.scheduler_runner import SchedulerState, SchedulerThread

UTC = zoneinfo.ZoneInfo("UTC")


def _thread(job_manager=None, config=None):
    job_manager = job_manager or Mock()
    return SchedulerThread(job_manager=job_manager, load_config_fn=lambda: config)


class TestSchedulerState:
    def test_starts_with_no_attempt_recorded(self):
        state = SchedulerState()
        snapshot = state.snapshot()
        assert snapshot["last_attempt_at"] is None
        assert snapshot["last_result"] is None

    def test_record_updates_snapshot(self):
        state = SchedulerState()
        state.record("started")
        snapshot = state.snapshot()
        assert snapshot["last_result"] == "started"
        assert snapshot["last_attempt_at"] is not None

    def test_record_overwrites_previous(self):
        state = SchedulerState()
        state.record("started")
        state.record("skipped - busy")
        assert state.snapshot()["last_result"] == "skipped - busy"


class TestSchedulerThreadTick:
    def test_disabled_schedule_does_nothing(self):
        job_manager = Mock()
        thread = _thread(job_manager, config={"schedule": {"enabled": False}})

        thread._tick()

        job_manager.start.assert_not_called()
        assert thread._next_fire_target is None

    def test_missing_schedule_section_does_nothing(self):
        job_manager = Mock()
        thread = _thread(job_manager, config={})

        thread._tick()

        job_manager.start.assert_not_called()

    def test_no_config_at_all_does_nothing(self):
        job_manager = Mock()
        thread = _thread(job_manager, config=None)

        thread._tick()

        job_manager.start.assert_not_called()

    def test_enabled_computes_next_target_without_firing_immediately(self):
        job_manager = Mock()
        thread = _thread(job_manager, config={"schedule": {"enabled": True, "time": "03:00"}})

        thread._tick()

        job_manager.start.assert_not_called()
        assert thread._next_fire_target is not None
        assert thread._next_fire_target > datetime.now(thread._next_fire_target.tzinfo)

    @patch("web.scheduler_runner.compute_next_run")
    def test_fires_when_target_has_passed(self, mock_compute_next_run):
        job_manager = Mock()
        thread = _thread(job_manager, config={"schedule": {"enabled": True, "time": "03:00"}})

        past_target = datetime.now(UTC) - timedelta(minutes=1)
        thread._next_fire_target = past_target
        thread._last_seen_schedule_key = (3, 0, None)
        mock_compute_next_run.return_value = past_target + timedelta(days=1)

        thread._tick()

        job_manager.start.assert_called_once_with("full", "all", [])
        assert thread.state.snapshot()["last_result"] == "started"
        assert thread._next_fire_target == past_target + timedelta(days=1)

    @patch("web.scheduler_runner.compute_next_run")
    def test_skips_and_logs_when_run_already_in_progress(self, mock_compute_next_run):
        job_manager = Mock()
        job_manager.start.side_effect = JobAlreadyRunningError("A run is already in progress")
        thread = _thread(job_manager, config={"schedule": {"enabled": True, "time": "03:00"}})

        past_target = datetime.now(UTC) - timedelta(minutes=1)
        thread._next_fire_target = past_target
        thread._last_seen_schedule_key = (3, 0, None)
        mock_compute_next_run.return_value = past_target + timedelta(days=1)

        with patch("web.scheduler_runner.log_warning") as mock_warn:
            thread._tick()

        job_manager.start.assert_called_once()
        assert "skipped" in thread.state.snapshot()["last_result"]
        mock_warn.assert_called_once()
        # Never queues/retries - the target still advances to the NEXT
        # occurrence, exactly as if the run had fired.
        assert thread._next_fire_target == past_target + timedelta(days=1)

    @patch("web.scheduler_runner.compute_next_run")
    def test_records_error_on_job_error_without_crashing(self, mock_compute_next_run):
        job_manager = Mock()
        job_manager.start.side_effect = JobError("Unknown engine: full")
        thread = _thread(job_manager, config={"schedule": {"enabled": True, "time": "03:00"}})

        past_target = datetime.now(UTC) - timedelta(minutes=1)
        thread._next_fire_target = past_target
        thread._last_seen_schedule_key = (3, 0, None)
        mock_compute_next_run.return_value = past_target + timedelta(days=1)

        thread._tick()  # must not raise

        assert "error" in thread.state.snapshot()["last_result"]

    def test_uses_configured_users(self):
        job_manager = Mock()
        config = {"schedule": {"enabled": True, "time": "03:00"}, "users": {"list": "alice, bob"}}
        thread = _thread(job_manager, config=config)

        past_target = datetime.now(UTC) - timedelta(minutes=1)
        thread._next_fire_target = past_target
        thread._last_seen_schedule_key = (3, 0, None)

        with patch("web.scheduler_runner.compute_next_run", return_value=past_target + timedelta(days=1)):
            thread._tick()

        args = job_manager.start.call_args[0]
        assert args[0] == "full"
        assert args[1] == "all"
        assert "alice" in args[2] and "bob" in args[2]

    def test_invalid_schedule_config_warns_once_not_every_tick(self):
        job_manager = Mock()
        thread = _thread(job_manager, config={"schedule": {"enabled": True, "time": "not-a-time"}})

        with patch("web.scheduler_runner.log_warning") as mock_warn:
            thread._tick()
            thread._tick()
            thread._tick()

        job_manager.start.assert_not_called()
        mock_warn.assert_called_once()

    def test_disabling_then_reenabling_recomputes_from_scratch(self):
        job_manager = Mock()
        config = {"schedule": {"enabled": True, "time": "03:00"}}
        thread = _thread(job_manager, config=config)

        thread._tick()
        first_target = thread._next_fire_target
        assert first_target is not None

        thread._load_config = lambda: {"schedule": {"enabled": False}}
        thread._tick()
        assert thread._next_fire_target is None

        thread._load_config = lambda: config
        thread._tick()
        assert thread._next_fire_target is not None

    def test_changing_schedule_time_recomputes_immediately(self):
        job_manager = Mock()
        thread = _thread(job_manager, config={"schedule": {"enabled": True, "time": "03:00"}})
        thread._tick()
        first_target = thread._next_fire_target

        thread._load_config = lambda: {"schedule": {"enabled": True, "time": "04:00"}}
        thread._tick()

        assert thread._next_fire_target != first_target
        job_manager.start.assert_not_called()


class TestSchedulerThreadRealRun:
    """One lightweight real-thread smoke test - everything else above
    calls _tick() directly for speed/determinism."""

    def test_start_and_stop(self):
        job_manager = Mock()
        thread = SchedulerThread(
            job_manager=job_manager,
            load_config_fn=lambda: {"schedule": {"enabled": False}},
            poll_interval_seconds=0.05,
        )
        thread.start()
        time.sleep(0.2)
        thread.stop()
        thread.join(timeout=2)
        assert not thread.is_alive()
