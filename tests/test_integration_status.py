"""Tests for utils/integration_status.py - explicit integration-health
signal (see module docstring: replaces log-string matching for
surfacing a Trakt auth/export failure to the web UI)."""

import json
import os

from utils.integration_status import get_integration_status, record_integration_status


class TestRecordAndGetIntegrationStatus:
    def test_roundtrip_success(self, tmp_path):
        record_integration_status(str(tmp_path), "trakt_export", True)

        result = get_integration_status(str(tmp_path), "trakt_export")

        assert result["success"] is True
        assert result["detail"] == ""
        assert "timestamp" in result

    def test_roundtrip_failure_with_detail(self, tmp_path):
        record_integration_status(str(tmp_path), "trakt_export", False, "boom")

        result = get_integration_status(str(tmp_path), "trakt_export")

        assert result["success"] is False
        assert result["detail"] == "boom"

    def test_overwrites_previous_status(self, tmp_path):
        """Only the LAST attempt's outcome is kept - a run that succeeds
        after a prior failure must clear the failed signal, not append
        to a history."""
        record_integration_status(str(tmp_path), "trakt_export", False, "first failure")
        record_integration_status(str(tmp_path), "trakt_export", True)

        result = get_integration_status(str(tmp_path), "trakt_export")

        assert result["success"] is True
        assert result["detail"] == ""

    def test_different_names_are_independent(self, tmp_path):
        record_integration_status(str(tmp_path), "trakt_export", False, "trakt broke")
        record_integration_status(str(tmp_path), "simkl_export", True)

        trakt_status = get_integration_status(str(tmp_path), "trakt_export")
        simkl_status = get_integration_status(str(tmp_path), "simkl_export")

        assert trakt_status["success"] is False
        assert simkl_status["success"] is True

    def test_detail_is_redacted(self, tmp_path):
        record_integration_status(str(tmp_path), "trakt_export", False, "access_token=supersecrettoken123")

        result = get_integration_status(str(tmp_path), "trakt_export")

        assert "supersecrettoken123" not in result["detail"]

    def test_creates_cache_dir_if_missing(self, tmp_path):
        cache_dir = tmp_path / "does_not_exist_yet"

        record_integration_status(str(cache_dir), "trakt_export", True)

        assert cache_dir.is_dir()

    def test_write_is_atomic_no_leftover_temp_file(self, tmp_path):
        record_integration_status(str(tmp_path), "trakt_export", True)

        leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
        assert leftover == []

    def test_get_returns_none_when_never_recorded(self, tmp_path):
        assert get_integration_status(str(tmp_path), "trakt_export") is None

    def test_get_returns_none_on_corrupt_file(self, tmp_path):
        path = tmp_path / "integration_status_trakt_export.json"
        path.write_text("{not valid json")

        assert get_integration_status(str(tmp_path), "trakt_export") is None

    def test_get_returns_none_on_unexpected_shape(self, tmp_path):
        path = tmp_path / "integration_status_trakt_export.json"
        path.write_text(json.dumps(["not", "a", "dict"]))

        assert get_integration_status(str(tmp_path), "trakt_export") is None

    def test_record_never_raises_on_unwritable_dir(self, tmp_path, monkeypatch):
        """Recording status must never itself crash the run that's
        already succeeding/failing."""

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "makedirs", _boom)

        record_integration_status(str(tmp_path / "sub"), "trakt_export", True)  # must not raise
