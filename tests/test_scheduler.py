"""Tests for utils/scheduler.py - the #264 in-app scheduler's pure
timezone-resolution/config-parsing/next-run-computation logic (no
threads, no JobManager - see tests/test_scheduler_runner.py for the
background-thread integration this feeds)."""

import zoneinfo
from datetime import datetime
from unittest.mock import patch

import pytest

from utils.scheduler import (
    WEEKDAY_NAMES,
    compute_next_run,
    describe_next_run,
    parse_schedule_config,
    resolve_scheduler_timezone,
)

UTC = zoneinfo.ZoneInfo("UTC")
NY = zoneinfo.ZoneInfo("America/New_York")


class TestResolveSchedulerTimezone:
    def test_uses_tz_env_var(self, monkeypatch):
        monkeypatch.setenv("TZ", "Australia/Melbourne")
        result = resolve_scheduler_timezone()
        assert str(result) == "Australia/Melbourne"

    def test_falls_back_to_utc_when_unset(self, monkeypatch):
        monkeypatch.delenv("TZ", raising=False)
        result = resolve_scheduler_timezone()
        assert str(result) == "UTC"

    def test_falls_back_to_utc_when_blank(self, monkeypatch):
        monkeypatch.setenv("TZ", "")
        result = resolve_scheduler_timezone()
        assert str(result) == "UTC"

    @patch("utils.scheduler.log_warning")
    def test_invalid_tz_falls_back_to_utc_and_warns(self, mock_warn, monkeypatch):
        monkeypatch.setenv("TZ", "Not/A_Real_Zone")
        result = resolve_scheduler_timezone()
        assert str(result) == "UTC"
        mock_warn.assert_called_once()

    @patch("utils.scheduler.log_warning")
    def test_unset_tz_does_not_warn(self, mock_warn, monkeypatch):
        """An unset TZ defaulting to UTC is completely normal (most
        images don't set it) - only a TYPO'd value should warn."""
        monkeypatch.delenv("TZ", raising=False)
        resolve_scheduler_timezone()
        mock_warn.assert_not_called()


class TestParseScheduleConfig:
    def test_valid_time_no_weekdays(self):
        hour, minute, weekdays = parse_schedule_config({"time": "03:00"})
        assert (hour, minute, weekdays) == (3, 0, None)

    def test_valid_time_with_weekdays(self):
        hour, minute, weekdays = parse_schedule_config({"time": "23:45", "weekdays": ["monday", "Friday"]})
        assert (hour, minute) == (23, 45)
        assert weekdays == {0, 4}

    def test_empty_weekdays_list_means_every_day(self):
        _hour, _minute, weekdays = parse_schedule_config({"time": "03:00", "weekdays": []})
        assert weekdays is None

    def test_missing_weekdays_key_means_every_day(self):
        _hour, _minute, weekdays = parse_schedule_config({"time": "03:00"})
        assert weekdays is None

    def test_midnight_and_end_of_day_are_valid(self):
        assert parse_schedule_config({"time": "00:00"})[:2] == (0, 0)
        assert parse_schedule_config({"time": "23:59"})[:2] == (23, 59)

    def test_rejects_missing_time(self):
        with pytest.raises(ValueError):
            parse_schedule_config({})

    def test_rejects_hour_24(self):
        with pytest.raises(ValueError):
            parse_schedule_config({"time": "24:00"})

    def test_rejects_bad_format(self):
        with pytest.raises(ValueError):
            parse_schedule_config({"time": "3am"})

    def test_rejects_unknown_weekday_name(self):
        with pytest.raises(ValueError):
            parse_schedule_config({"time": "03:00", "weekdays": ["someday"]})

    def test_all_weekday_names_accepted(self):
        _hour, _minute, weekdays = parse_schedule_config({"time": "03:00", "weekdays": list(WEEKDAY_NAMES)})
        assert weekdays == set(range(7))


class TestComputeNextRun:
    def test_returns_today_when_time_has_not_passed(self):
        now = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)  # Monday
        result = compute_next_run(now, 14, 0, None)
        assert result == datetime(2026, 6, 15, 14, 0, tzinfo=UTC)

    def test_returns_tomorrow_when_time_has_already_passed_today(self):
        now = datetime(2026, 6, 15, 15, 0, tzinfo=UTC)  # Monday, already past 14:00
        result = compute_next_run(now, 14, 0, None)
        assert result == datetime(2026, 6, 16, 14, 0, tzinfo=UTC)

    def test_never_returns_now_or_earlier(self):
        """The core #264 restart-safety guarantee: always strictly future."""
        now = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
        result = compute_next_run(now, 14, 0, None)
        assert result > now
        assert result == datetime(2026, 6, 16, 14, 0, tzinfo=UTC)

    def test_restart_hours_after_missed_time_does_not_catch_up(self):
        """#264: starting at 5pm with a 3am schedule must compute
        tomorrow's 3am, never "immediately" / "today, late"."""
        now = datetime(2026, 6, 15, 17, 0, tzinfo=UTC)
        result = compute_next_run(now, 3, 0, None)
        assert result == datetime(2026, 6, 16, 3, 0, tzinfo=UTC)
        assert result > now

    def test_weekday_filter_skips_non_matching_days(self):
        # 2026-06-15 is a Monday. Schedule for Wednesday/Friday only.
        now = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
        result = compute_next_run(now, 9, 0, {2, 4})  # Wed, Fri
        assert result == datetime(2026, 6, 17, 9, 0, tzinfo=UTC)  # Wednesday

    def test_weekday_filter_wraps_to_next_week(self):
        # Monday 2026-06-15, only Monday allowed, already past today's time.
        now = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
        result = compute_next_run(now, 9, 0, {0})
        assert result == datetime(2026, 6, 22, 9, 0, tzinfo=UTC)

    def test_empty_weekdays_set_raises(self):
        now = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
        with pytest.raises(ValueError):
            compute_next_run(now, 9, 0, set())

    def test_spring_forward_skipped_hour_runs_at_next_valid_time(self):
        """US spring-forward 2026: America/New_York jumps 2:00 -> 3:00 on
        March 8. A 02:30 schedule (inside the skipped hour) must not be
        silently dropped for the day - it resolves to a real instant
        shortly after the gap closes (see compute_next_run's own
        docstring for the zoneinfo/PEP 495 mechanics)."""
        now = datetime(2026, 3, 7, 12, 0, tzinfo=NY)
        result = compute_next_run(now, 2, 30, None)
        assert result.date() == datetime(2026, 3, 8, 0, 0).date()
        # The nonexistent 2:30 resolves (fold=0, pre-transition offset)
        # to a real UTC instant that - converted back to the ACTUAL
        # local offset in effect at that instant - is after the 2am
        # transition point, not before it or on a different day.
        real_utc_offset_after_transition = -4 * 3600  # EDT, UTC-4, in seconds
        assert result.utcoffset().total_seconds() in (-5 * 3600, real_utc_offset_after_transition)
        # Convert to a fixed-offset UTC instant and confirm it's after
        # the transition instant itself (2026-03-08 07:00 UTC).
        transition_instant = datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
        assert result.astimezone(UTC) >= transition_instant

    def test_fall_back_duplicated_hour_fires_only_once(self):
        """US fall-back 2026: America/New_York repeats 1:00-2:00 on
        Nov 1 (falls back from 2:00 EDT to 1:00 EST). A 01:30 schedule
        must fire once for that day, not twice - simulates the
        scheduler thread's own "recompute from the instant we just
        fired at" pattern (web/scheduler_runner.py) and confirms the
        second call skips straight to Nov 2, never re-triggering on
        the repeated hour."""
        before = datetime(2026, 10, 31, 12, 0, tzinfo=NY)
        first_fire = compute_next_run(before, 1, 30, None)
        assert first_fire.date() == datetime(2026, 11, 1, 0, 0).date()

        second_fire = compute_next_run(first_fire, 1, 30, None)
        assert second_fire.date() == datetime(2026, 11, 2, 0, 0).date()
        assert second_fire > first_fire


class TestDescribeNextRun:
    def test_disabled_reports_no_next_run(self):
        result = describe_next_run({"schedule": {"enabled": False, "time": "03:00"}})
        assert result["enabled"] is False
        assert result["next_run"] is None
        assert result["error"] is None

    def test_missing_schedule_section_reports_disabled(self):
        result = describe_next_run({})
        assert result["enabled"] is False
        assert result["next_run"] is None

    @patch("utils.scheduler.resolve_scheduler_timezone", return_value=UTC)
    def test_enabled_reports_next_run_and_timezone(self, _mock_tz):
        result = describe_next_run({"schedule": {"enabled": True, "time": "03:00"}})
        assert result["enabled"] is True
        assert result["timezone"] == "UTC"
        assert result["next_run"] is not None
        assert result["error"] is None

    def test_enabled_with_invalid_time_reports_error_not_exception(self):
        result = describe_next_run({"schedule": {"enabled": True, "time": "not-a-time"}})
        assert result["enabled"] is True
        assert result["next_run"] is None
        assert result["error"] is not None

    def test_enabled_with_invalid_weekday_reports_error_not_exception(self):
        result = describe_next_run({"schedule": {"enabled": True, "time": "03:00", "weekdays": ["someday"]}})
        assert result["error"] is not None
