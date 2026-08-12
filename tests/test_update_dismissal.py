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

"""Tests for utils/update_dismissal.py - the server-side 7-day snooze
shared by the web UI's dismissible update banner (web/app.py) and the
CLI's advisory update notice (utils/cli.py's print_update_notice).

The banner/CLI-level wiring into this module is covered separately
(tests/test_web_update_banner.py's TestDismiss/TestDismissSnooze,
tests/test_cli.py's TestPrintUpdateNotice) - these tests exercise
record_dismissal()/is_dismissed() directly, in isolation.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils.update_dismissal import (
    DISMISS_SNOOZE_DAYS,
    DISMISS_SNOOZE_SECONDS,
    is_dismissed,
    record_dismissal,
)


@pytest.fixture(autouse=True)
def _isolated_dismissal_dir(tmp_path, monkeypatch, _isolated_update_dismissal_dir):
    """Every test gets its own throwaway, STABLE (per-test) dir instead
    of touching the real one - overrides tests/conftest.py's suite-wide
    _isolated_update_dismissal_dir (which hands out a fresh dir on every
    call, fine for tests that don't care about persistence) the same
    way tests/test_update_check.py's own _isolated_cache_dir overrides
    _no_real_update_check_network for the same reason.
    """
    monkeypatch.setattr("utils.update_dismissal.get_project_root", lambda: str(tmp_path))
    return tmp_path


def _dismissal_file_path(tmp_path):
    return os.path.join(str(tmp_path), "cache", "dismissed_update.json")


def _seed_dismissal(tmp_path, data):
    path = _dismissal_file_path(tmp_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def _seed_dismissal_raw(tmp_path, text):
    path = _dismissal_file_path(tmp_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestConstants:
    def test_snooze_is_seven_days(self):
        assert DISMISS_SNOOZE_DAYS == 7
        assert DISMISS_SNOOZE_SECONDS == 7 * 24 * 60 * 60


class TestIsDismissedNoState:
    def test_never_dismissed_returns_false(self):
        assert is_dismissed("2.9.0") is False

    def test_empty_version_returns_false(self):
        assert is_dismissed("") is False


class TestRecordAndReadRoundTrip:
    def test_record_then_immediately_dismissed(self, tmp_path):
        record_dismissal("2.9.0")
        assert is_dismissed("2.9.0") is True

    def test_writes_expected_json_shape(self, tmp_path):
        record_dismissal("2.9.0", now=1000.0)
        path = _dismissal_file_path(tmp_path)
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data == {"version": "2.9.0", "dismissed_at": 1000.0}

    def test_record_empty_version_writes_nothing(self, tmp_path):
        record_dismissal("")
        assert not os.path.isfile(_dismissal_file_path(tmp_path))

    def test_record_overwrites_a_previous_dismissal(self, tmp_path):
        record_dismissal("2.9.0", now=1000.0)
        record_dismissal("2.10.0", now=2000.0)
        assert is_dismissed("2.9.0") is False  # overwritten, no longer the recorded version
        assert is_dismissed("2.10.0", now=2000.0) is True


class TestSnoozeWindow:
    """The exact 7-day boundary - see DISMISS_SNOOZE_SECONDS."""

    def test_dismissed_moments_ago_is_snoozed(self, tmp_path):
        record_dismissal("2.9.0", now=1000.0)
        assert is_dismissed("2.9.0", now=1000.0 + 1) is True

    def test_dismissed_just_under_seven_days_ago_is_still_snoozed(self, tmp_path):
        record_dismissal("2.9.0", now=1000.0)
        assert is_dismissed("2.9.0", now=1000.0 + DISMISS_SNOOZE_SECONDS - 1) is True

    def test_dismissed_exactly_seven_days_ago_is_no_longer_snoozed(self, tmp_path):
        """Boundary is a strict '<', not '<=' - see is_dismissed's
        (current_time - dismissed_at) < DISMISS_SNOOZE_SECONDS check."""
        record_dismissal("2.9.0", now=1000.0)
        assert is_dismissed("2.9.0", now=1000.0 + DISMISS_SNOOZE_SECONDS) is False

    def test_dismissed_well_over_seven_days_ago_is_not_snoozed(self, tmp_path):
        record_dismissal("2.9.0", now=1000.0)
        assert is_dismissed("2.9.0", now=1000.0 + DISMISS_SNOOZE_SECONDS + 3600) is False

    def test_default_now_uses_real_time(self, tmp_path):
        """Real-world usage: no `now` override, real time.time() on both
        sides - a dismissal recorded 'just now' must read back as
        dismissed 'just now', with no explicit time mocking needed."""
        record_dismissal("2.9.0")
        assert is_dismissed("2.9.0") is True


class TestNewerVersionOverridesSnooze:
    def test_newer_version_than_dismissed_is_not_suppressed(self, tmp_path):
        record_dismissal("2.9.0", now=1000.0)
        # Well within the snooze window time-wise, but a DIFFERENT
        # (newer) version is being asked about.
        assert is_dismissed("2.10.0", now=1000.0 + 60) is False

    def test_older_version_than_dismissed_is_also_not_suppressed(self, tmp_path):
        """Dismissal is scoped to the EXACT version dismissed, never
        "any version up to and including this one" - an older version
        string being re-checked (unusual, but not this module's job to
        assume impossible) must not be treated as still-dismissed
        either."""
        record_dismissal("2.10.0", now=1000.0)
        assert is_dismissed("2.9.0", now=1000.0 + 60) is False


class TestFailOpen:
    """Any unreadable/corrupt/unexpected state must resolve to "not
    dismissed" (the notice IS shown) - never silently swallow a pending
    update notice because of a filesystem hiccup."""

    def test_corrupt_json_file_is_not_dismissed(self, tmp_path):
        _seed_dismissal_raw(tmp_path, "{not valid json")
        assert is_dismissed("2.9.0") is False

    def test_missing_dismissed_at_field_is_not_dismissed(self, tmp_path):
        _seed_dismissal(tmp_path, {"version": "2.9.0"})
        assert is_dismissed("2.9.0") is False

    def test_non_numeric_dismissed_at_is_not_dismissed(self, tmp_path):
        _seed_dismissal(tmp_path, {"version": "2.9.0", "dismissed_at": "not-a-number"})
        assert is_dismissed("2.9.0") is False

    def test_missing_version_field_is_not_dismissed(self, tmp_path):
        _seed_dismissal(tmp_path, {"dismissed_at": time.time()})
        assert is_dismissed("2.9.0") is False

    def test_record_dismissal_write_failure_is_not_fatal(self, monkeypatch, tmp_path):
        """A disk error writing the dismissal state (permissions, full
        disk, etc.) must not raise - worst case, the notice just keeps
        reappearing every check instead of being snoozed. Points the
        cache dir at a path that can never be created - a plain file
        sits where the needed parent directory would go, so
        os.makedirs always fails, on every OS - so the real open()
        call fails naturally, same technique tests/test_update_check.py's
        own test_cache_write_failure_is_not_fatal uses (see its comment
        for why a hardcoded '/nonexistent/...' string isn't reliably
        uncreatable on Windows)."""
        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"not a directory")
        monkeypatch.setattr(
            "utils.update_dismissal.get_project_root",
            lambda: str(blocker / "unreachable"),
        )
        try:
            record_dismissal("2.9.0")
        except Exception as e:
            pytest.fail(f"record_dismissal() raised instead of failing open: {e}")

    def test_is_dismissed_read_failure_is_not_fatal(self, tmp_path):
        """A directory where the file should be (unreadable as JSON in
        any useful sense) must not raise - same fail-open contract,
        exercised via _read_dismissal()'s try/except around open()."""
        path = _dismissal_file_path(tmp_path)
        os.makedirs(path, exist_ok=True)  # a directory, not a file, at that exact path
        try:
            result = is_dismissed("2.9.0")
        except Exception as e:
            pytest.fail(f"is_dismissed() raised instead of failing open: {e}")
        assert result is False


class TestPersistsAcrossCalls:
    """Proves this is genuine on-disk state, not anything held in
    process/module-level memory - separate, independent calls (no
    shared object between them) must still see the same state, the same
    way a real web server restart or a separate CLI invocation would."""

    def test_state_survives_independent_read_after_write(self, tmp_path):
        record_dismissal("2.9.0", now=1000.0)
        # A second, wholly independent call - nothing carried over except
        # what's on disk at _dismissal_path().
        assert is_dismissed("2.9.0", now=1000.5) is True

    def test_state_visible_to_a_second_isolated_read(self, tmp_path):
        record_dismissal("2.9.0")
        with open(_dismissal_file_path(tmp_path), encoding="utf-8") as f:
            on_disk = json.load(f)
        assert on_disk["version"] == "2.9.0"
        assert isinstance(on_disk["dismissed_at"], (int, float))
