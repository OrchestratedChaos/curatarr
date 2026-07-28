"""Tests for web/status.py - log parsing for the dashboard and results page."""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import web.status as status_mod
from utils.run_status import record_run_status
from web.status import (
    LOG_VIEW_MAX_BYTES,
    TAIL_BYTES,
    find_user_watchlist,
    get_last_run_status,
    list_log_files,
    read_log_full,
    read_log_tail,
)


def _write_log(logs_dir, name, content):
    path = os.path.join(str(logs_dir), name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestGetLastRunStatus:
    """Tests for get_last_run_status()"""

    def test_never_run_when_no_logs(self, tmp_path):
        result = get_last_run_status(str(tmp_path), "alice")
        assert result == {"status": "never_run", "timestamp": None, "log_file": None, "reason": None}

    def test_success_when_no_failure_markers(self, tmp_path):
        _write_log(tmp_path, "recommendations_alice_20260101_030000.log", "Processing alice\nDone\n")
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["status"] == "success"
        assert result["log_file"] == "recommendations_alice_20260101_030000.log"
        assert result["timestamp"] == datetime(2026, 1, 1, 3, 0, 0)

    def test_failed_when_traceback_present(self, tmp_path):
        _write_log(
            tmp_path,
            "recommendations_alice_20260101_030000.log",
            "Processing alice\nTraceback (most recent call last):\nValueError\n",
        )
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["status"] == "failed"

    def test_failed_when_fatal_error_present(self, tmp_path):
        _write_log(tmp_path, "recommendations_alice_20260101_030000.log", "Fatal error detected\n")
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["status"] == "failed"

    def test_unknown_when_log_empty(self, tmp_path):
        _write_log(tmp_path, "recommendations_alice_20260101_030000.log", "")
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["status"] == "unknown"

    def test_unknown_reason_distinguishes_empty_from_unreadable(self, tmp_path):
        """#263: 'unknown' used to collapse two very different causes -
        a 0-byte log (run interrupted before writing anything) and a
        log that exists but can't be read at all (permissions, or a
        race with log-retention cleanup) - into the identical bare
        'unknown' with no explanation. Both must now say why."""
        log_path = _write_log(tmp_path, "recommendations_alice_20260101_030000.log", "")
        empty_result = get_last_run_status(str(tmp_path), "alice")
        assert empty_result["status"] == "unknown"
        assert empty_result["reason"] is not None
        assert "empty" in empty_result["reason"].lower()

        try:
            os.chmod(log_path, 0o000)
            if os.access(log_path, os.R_OK):
                pytest.skip("running as a user that bypasses file permissions (e.g. root)")
            unreadable_result = get_last_run_status(str(tmp_path), "alice")
            assert unreadable_result["status"] == "unknown"
            assert unreadable_result["reason"] is not None
            assert "unreadable" in unreadable_result["reason"].lower()
            assert unreadable_result["reason"] != empty_result["reason"]
        finally:
            os.chmod(log_path, 0o644)

    def test_picks_newest_log_by_mtime(self, tmp_path):
        older = _write_log(tmp_path, "recommendations_alice_20260101_030000.log", "ok\n")
        newer = _write_log(
            tmp_path,
            "recommendations_alice_20260102_030000.log",
            "Traceback (most recent call last):\n",
        )
        os.utime(older, (1, 1))
        os.utime(newer, (100, 100))
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["log_file"] == "recommendations_alice_20260102_030000.log"
        assert result["status"] == "failed"

    def test_only_matches_this_users_logs(self, tmp_path):
        _write_log(tmp_path, "recommendations_bob_20260101_030000.log", "ok\n")
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["status"] == "never_run"

    def test_falls_back_to_mtime_for_unparseable_timestamp(self, tmp_path):
        # Month 13 doesn't parse as a real date - status.py should fall
        # back to the file's mtime instead of raising.
        path = _write_log(tmp_path, "recommendations_alice_20261301_030000.log", "ok\n")
        result = get_last_run_status(str(tmp_path), "alice")
        assert isinstance(result["timestamp"], datetime)
        assert result["timestamp"] == datetime.fromtimestamp(os.path.getmtime(path))

    def test_username_with_glob_special_chars_does_not_match_other_users(self, tmp_path):
        # A Plex username of "*" (or containing "?"/"[...]") must not
        # turn the glob pattern into a wildcard that leaks other users'
        # last-run status onto this one's dashboard row.
        _write_log(tmp_path, "recommendations_bob_20260101_030000.log", "ok\n")
        result = get_last_run_status(str(tmp_path), "*")
        assert result == {"status": "never_run", "timestamp": None, "log_file": None, "reason": None}

    def test_username_with_bracket_glob_chars_does_not_match(self, tmp_path):
        _write_log(tmp_path, "recommendations_bob_20260101_030000.log", "ok\n")
        result = get_last_run_status(str(tmp_path), "[ab]*")
        assert result["status"] == "never_run"

    def test_traceback_marker_within_tail_window_is_detected(self, tmp_path):
        filler = "x" * (TAIL_BYTES + 1000)
        content = filler + "\nTraceback (most recent call last):\nValueError\n"
        _write_log(tmp_path, "recommendations_alice_20260101_030000.log", content)
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["status"] == "failed"

    def test_traceback_marker_beyond_tail_window_is_not_detected(self, tmp_path):
        # Documents the heuristic's known limitation (per TAIL_BYTES):
        # only the last TAIL_BYTES of a log are inspected, so a
        # traceback appearing only earlier than that in a very large log
        # won't flip status to 'failed'. The important thing this
        # asserts is that it doesn't crash on a large file either way.
        content = "Traceback (most recent call last):\nValueError\n" + ("x" * (TAIL_BYTES + 1000))
        _write_log(tmp_path, "recommendations_alice_20260101_030000.log", content)
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["status"] == "success"

    def test_getmtime_oserror_falls_back_to_none_timestamp(self, tmp_path, monkeypatch):
        # Unparseable-timestamp filename forces the getmtime() fallback;
        # simulate the file vanishing (log-retention cleanup racing this
        # request) right before that specific call. The first getmtime()
        # call (inside latest_user_log()'s max(key=...) over glob
        # results) must still succeed, so this only fails the second.
        _write_log(tmp_path, "recommendations_alice_20261301_030000.log", "ok\n")
        real_getmtime = os.path.getmtime
        calls = {"n": 0}

        def _flaky(path):
            calls["n"] += 1
            if calls["n"] == 1:
                return real_getmtime(path)
            raise OSError("gone")

        monkeypatch.setattr(status_mod.os.path, "getmtime", _flaky)
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["timestamp"] is None


class TestGetLastRunStatusExplicitSignal:
    """#292: get_last_run_status() prefers utils.run_status's explicit,
    structured signal over the legacy log-tail marker-matching
    heuristic - see that module's own docstring for the two confirmed
    failure modes this replaces (an error phrased outside the fixed
    marker list; movie.py/tv.py sharing one log-filename pattern)."""

    def test_prefers_explicit_success_over_log_content_saying_otherwise(self, tmp_path):
        """The log content itself would say 'failed' under the legacy
        heuristic - the explicit signal (what the code itself actually
        observed) must win regardless."""
        _write_log(
            tmp_path,
            "recommendations_alice_20260101_030000.log",
            "Traceback (most recent call last):\nValueError\n",
        )
        record_run_status(str(tmp_path), "movie", "alice", True)
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["status"] == "success"
        assert result["reason"] is None

    def test_prefers_explicit_failure_over_log_content_saying_otherwise(self, tmp_path):
        """The concrete #292 precedent: a failure phrased in a way the
        marker list never catches (e.g. "Cannot get lists: not
        authenticated") must still surface as failed via the explicit
        signal, and the failure detail itself is now available as
        `reason` - previously only set for 'unknown', never 'failed'."""
        _write_log(tmp_path, "recommendations_alice_20260101_030000.log", "Everything looks fine\n")
        record_run_status(str(tmp_path), "movie", "alice", False, "Cannot get lists: not authenticated")
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["status"] == "failed"
        assert result["reason"] == "Cannot get lists: not authenticated"

    def test_compares_movie_and_tv_by_their_own_recorded_timestamp_not_log_mtime(self, tmp_path, monkeypatch):
        """movie.py and tv.py both write to the identical
        "recommendations_<user>_*.log" naming - get_last_run_status()
        must resolve "which engine ran last" from each explicit
        record's OWN timestamp, never from comparing log file mtimes
        (which is exactly the ambiguity that let one engine's failure
        hide behind the other's later, unrelated success)."""
        import utils.run_status as run_status_mod

        real_now = run_status_mod.datetime

        class _FixedNow(real_now):
            @classmethod
            def now(cls, tz=None):
                return real_now(2026, 1, 1, 3, 0, 0, tzinfo=tz)

        monkeypatch.setattr(run_status_mod, "datetime", _FixedNow)
        record_run_status(str(tmp_path), "movie", "alice", False, "movie blew up")

        class _LaterNow(real_now):
            @classmethod
            def now(cls, tz=None):
                return real_now(2026, 1, 1, 4, 0, 0, tzinfo=tz)

        monkeypatch.setattr(run_status_mod, "datetime", _LaterNow)
        record_run_status(str(tmp_path), "tv", "alice", True)

        result = get_last_run_status(str(tmp_path), "alice")
        assert result["status"] == "success"  # tv's record is newer

    def test_falls_back_to_heuristic_when_no_explicit_signal_recorded(self, tmp_path):
        """An install predating #292 (or a run from before it shipped)
        has no run_status_*.json at all - must fall back to the legacy
        log-tail heuristic exactly as before, never regress to
        'unknown'/'never_run' just because the new signal is absent."""
        _write_log(
            tmp_path,
            "recommendations_alice_20260101_030000.log",
            "Traceback (most recent call last):\nValueError\n",
        )
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["status"] == "failed"

    def test_explicit_signal_still_resolves_log_file_for_view_log_link(self, tmp_path):
        _write_log(tmp_path, "recommendations_alice_20260101_030000.log", "ok\n")
        record_run_status(str(tmp_path), "movie", "alice", True)
        result = get_last_run_status(str(tmp_path), "alice")
        assert result["log_file"] == "recommendations_alice_20260101_030000.log"


class TestListLogFiles:
    """Tests for list_log_files()"""

    def test_empty_when_dir_missing(self, tmp_path):
        assert list_log_files(str(tmp_path / "missing")) == []

    def test_lists_only_log_files_newest_first(self, tmp_path):
        a = _write_log(tmp_path, "a.log", "a")
        b = _write_log(tmp_path, "b.log", "b")
        (tmp_path / "notes.txt").write_text("not a log")
        os.utime(a, (1, 1))
        os.utime(b, (100, 100))
        result = list_log_files(str(tmp_path))
        assert [e["name"] for e in result] == ["b.log", "a.log"]

    def test_skips_file_deleted_mid_scan_instead_of_raising(self, tmp_path, monkeypatch):
        _write_log(tmp_path, "gone.log", "a")
        _write_log(tmp_path, "still-here.log", "b")
        real_getsize = os.path.getsize

        def _flaky(path):
            if path.endswith("gone.log"):
                raise OSError("deleted mid-scan")
            return real_getsize(path)

        monkeypatch.setattr(status_mod.os.path, "getsize", _flaky)
        result = list_log_files(str(tmp_path))
        assert [e["name"] for e in result] == ["still-here.log"]


class TestReadLogTail:
    """Tests for read_log_tail()"""

    def test_reads_content(self, tmp_path):
        _write_log(tmp_path, "a.log", "line1\nline2\n")
        content, reason = read_log_tail(str(tmp_path), "a.log")
        assert content == "line1\nline2"
        assert reason is None

    def test_raises_for_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_log_tail(str(tmp_path), "missing.log")

    def test_raises_for_path_traversal(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_log_tail(str(tmp_path), "../secret.log")

    def test_redacts_secrets(self, tmp_path):
        _write_log(tmp_path, "a.log", "token=abcdef123456\n")
        content, _reason = read_log_tail(str(tmp_path), "a.log")
        assert "abcdef123456" not in content

    def test_truncates_to_max_lines(self, tmp_path):
        content = "\n".join(f"line{i}" for i in range(10))
        _write_log(tmp_path, "a.log", content)
        result, _reason = read_log_tail(str(tmp_path), "a.log", max_lines=3)
        assert result.splitlines() == ["line7", "line8", "line9"]

    def test_rejects_non_log_extension(self, tmp_path):
        (tmp_path / "config.yml").write_text("plex:\n  token: secret\n", encoding="utf-8")
        with pytest.raises(FileNotFoundError):
            read_log_tail(str(tmp_path), "config.yml")

    def test_rejects_symlink_escape(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.log").write_text("TOP SECRET", encoding="utf-8")
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        try:
            os.symlink(str(outside / "secret.log"), str(logs_dir / "escape.log"))
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported in this environment")
        with pytest.raises(FileNotFoundError):
            read_log_tail(str(logs_dir), "escape.log")

    def test_empty_log_returns_reason(self, tmp_path):
        """#263: a 0-byte log (interrupted run) gets an explanatory
        reason instead of just an empty string with no context."""
        _write_log(tmp_path, "a.log", "")
        content, reason = read_log_tail(str(tmp_path), "a.log")
        assert content == ""
        assert reason is not None
        assert "empty" in reason.lower()


class TestReadLogFull:
    """#283: the log viewer only ever showed a fixed-size tail with no
    way to reach the START of a long run - read_log_full() is the
    unbounded (up to LOG_VIEW_MAX_BYTES) alternative."""

    def test_reads_entire_file_not_just_last_max_lines(self, tmp_path):
        content = "\n".join(f"line{i}" for i in range(1000))
        _write_log(tmp_path, "a.log", content)
        result, reason, truncated = read_log_full(str(tmp_path), "a.log")
        assert result.splitlines()[0] == "line0"
        assert result.splitlines()[-1] == "line999"
        assert reason is None
        assert truncated is False

    def test_truncated_true_when_file_exceeds_max_bytes(self, tmp_path):
        _write_log(tmp_path, "a.log", "x" * 1000)
        result, _reason, truncated = read_log_full(str(tmp_path), "a.log", max_bytes=100)
        assert truncated is True
        assert len(result) <= 100

    def test_not_truncated_when_file_is_under_max_bytes(self, tmp_path):
        _write_log(tmp_path, "a.log", "small\n")
        _result, _reason, truncated = read_log_full(str(tmp_path), "a.log", max_bytes=LOG_VIEW_MAX_BYTES)
        assert truncated is False

    def test_raises_for_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_log_full(str(tmp_path), "missing.log")

    def test_raises_for_path_traversal(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_log_full(str(tmp_path), "../secret.log")

    def test_rejects_non_log_extension(self, tmp_path):
        (tmp_path / "config.yml").write_text("plex:\n  token: secret\n", encoding="utf-8")
        with pytest.raises(FileNotFoundError):
            read_log_full(str(tmp_path), "config.yml")

    def test_redacts_secrets(self, tmp_path):
        _write_log(tmp_path, "a.log", "token=abcdef123456\n")
        content, _reason, _truncated = read_log_full(str(tmp_path), "a.log")
        assert "abcdef123456" not in content

    def test_empty_log_returns_reason(self, tmp_path):
        _write_log(tmp_path, "a.log", "")
        content, reason, truncated = read_log_full(str(tmp_path), "a.log")
        assert content == ""
        assert reason is not None
        assert "empty" in reason.lower()
        assert truncated is False


class TestFindUserWatchlist:
    """Tests for find_user_watchlist()"""

    def test_ignores_stray_per_user_html_file(self, tmp_path):
        """The per-user "<slug>_watchlist.html" preference was removed
        (recommenders/external_render.py has never written that file in
        this repo's tracked history - only six-month-old stragglers
        predating it ever existed on disk, since deleted). Even if one
        is somehow present, this must still resolve to the combined
        file, not the per-user one.
        """
        config = {"users": {"preferences": {"alice": {"display_name": "Alice A"}}}}
        (tmp_path / "alice_a_watchlist.html").write_text("<html></html>")
        (tmp_path / "watchlist.html").write_text("<html></html>")
        result = find_user_watchlist(str(tmp_path), config, "alice")
        assert result == "watchlist.html"

    def test_falls_back_to_combined_file_with_user_markdown_present(self, tmp_path):
        """The realistic case: this user's own per-user markdown
        (which does regenerate every run - generate_markdown()) exists
        alongside the combined HTML. Resolves to the combined file.
        """
        config = {"users": {"preferences": {"alice": {"display_name": "Alice A"}}}}
        (tmp_path / "alice_a_watchlist.md").write_text("# Alice A")
        (tmp_path / "watchlist.html").write_text("<html></html>")
        result = find_user_watchlist(str(tmp_path), config, "alice")
        assert result == "watchlist.html"

    def test_returns_none_when_nothing_generated(self, tmp_path):
        config = {"users": {"preferences": {}}}
        result = find_user_watchlist(str(tmp_path), config, "bob")
        assert result is None
