"""Tests for utils/metrics.py - the local-first, dependency-free
Prometheus text-format metrics registry.

Follows the same per-test-stable-tmp_path override pattern as
tests/test_update_dismissal.py: tests/conftest.py's suite-wide
_isolated_metrics_dir fixture hands out a FRESH throwaway dir on every
call (fine for tests elsewhere in the suite that don't care about
persistence across calls), but these tests need the SAME dir across
multiple record_*()/render_prometheus_text() calls within one test.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils import metrics


@pytest.fixture(autouse=True)
def _isolated_metrics_state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.metrics.get_project_root", lambda: str(tmp_path))
    return tmp_path


def _state_file_path(tmp_path):
    return os.path.join(str(tmp_path), "cache", "metrics_state.json")


class TestRecordRecommenderRun:
    def test_increments_counter_and_histogram(self, tmp_path):
        metrics.record_recommender_run("movie", "success", 12.5)
        text = metrics.render_prometheus_text()
        assert 'curatarr_recommender_runs_total{engine="movie",outcome="success"} 1.0' in text
        assert 'curatarr_recommender_run_duration_seconds_count{engine="movie",outcome="success"} 1' in text
        assert 'curatarr_recommender_run_duration_seconds_sum{engine="movie",outcome="success"} 12.5' in text

    def test_accumulates_across_multiple_runs(self, tmp_path):
        metrics.record_recommender_run("tv", "success", 5.0)
        metrics.record_recommender_run("tv", "success", 7.0)
        metrics.record_recommender_run("tv", "failure", 1.0)
        text = metrics.render_prometheus_text()
        assert 'curatarr_recommender_runs_total{engine="tv",outcome="success"} 2.0' in text
        assert 'curatarr_recommender_runs_total{engine="tv",outcome="failure"} 1.0' in text

    def test_persists_to_disk_state_file(self, tmp_path):
        metrics.record_recommender_run("external", "success", 3.0)
        assert os.path.isfile(_state_file_path(tmp_path))

    def test_survives_across_a_fresh_load_simulating_another_process(self, tmp_path):
        """A recommender subprocess (see web/job_runner.py) records into
        the same on-disk file a separately-running web server process
        later reads - simulated here by never relying on any in-memory
        state between the record call and the render call, both of
        which independently call _load_state()/_state_path()."""
        metrics.record_recommender_run("movie", "success", 1.0)
        # A fresh render (as a real /metrics scrape would do, from a
        # different process than whatever recorded the run) still sees it.
        text = metrics.render_prometheus_text()
        assert 'curatarr_recommender_runs_total{engine="movie",outcome="success"} 1.0' in text


class TestRecordApiCall:
    def test_success_and_error_tracked_separately(self):
        metrics.record_api_call("radarr", "success", 0.2)
        metrics.record_api_call("radarr", "error", 1.5)
        text = metrics.render_prometheus_text()
        assert 'curatarr_api_requests_total{service="radarr",outcome="error"} 1.0' in text
        assert 'curatarr_api_requests_total{service="radarr",outcome="success"} 1.0' in text

    def test_track_api_call_context_manager_records_success(self):
        with metrics.track_api_call("tmdb"):
            pass
        text = metrics.render_prometheus_text()
        assert 'curatarr_api_requests_total{service="tmdb",outcome="success"} 1.0' in text

    def test_track_api_call_context_manager_records_error_and_reraises(self):
        with pytest.raises(ValueError):
            with metrics.track_api_call("sonarr"):
                raise ValueError("boom")
        text = metrics.render_prometheus_text()
        assert 'curatarr_api_requests_total{service="sonarr",outcome="error"} 1.0' in text


class TestRecordCacheLookup:
    def test_hit_and_miss_tracked_separately(self):
        metrics.record_cache_lookup("hit")
        metrics.record_cache_lookup("hit")
        metrics.record_cache_lookup("miss")
        text = metrics.render_prometheus_text()
        assert 'curatarr_cache_lookups_total{result="hit"} 2.0' in text
        assert 'curatarr_cache_lookups_total{result="miss"} 1.0' in text


class TestRecordSelfUpdateAttempt:
    def test_success_and_failure_tracked_separately(self):
        metrics.record_self_update_attempt("success")
        metrics.record_self_update_attempt("failure")
        metrics.record_self_update_attempt("failure")
        text = metrics.render_prometheus_text()
        assert 'curatarr_self_update_attempts_total{outcome="success"} 1.0' in text
        assert 'curatarr_self_update_attempts_total{outcome="failure"} 2.0' in text


class TestRecordUnhandledError:
    def test_defaults_to_unknown_component(self):
        metrics.record_unhandled_error()
        text = metrics.render_prometheus_text()
        assert 'curatarr_unhandled_errors_total{component="unknown"} 1.0' in text

    def test_records_named_component(self):
        metrics.record_unhandled_error(component="web")
        text = metrics.render_prometheus_text()
        assert 'curatarr_unhandled_errors_total{component="web"} 1.0' in text


class TestRenderPrometheusText:
    def test_includes_build_info_with_version(self):
        from utils.config import __version__

        text = metrics.render_prometheus_text()
        assert f'curatarr_build_info{{version="{__version__}"}} 1' in text

    def test_valid_before_anything_recorded(self):
        """Rendering with a completely empty state must not raise, and
        must still expose curatarr_build_info."""
        text = metrics.render_prometheus_text()
        assert "curatarr_build_info" in text
        assert text.endswith("\n")

    def test_declares_help_and_type_for_every_metric(self):
        text = metrics.render_prometheus_text()
        for name in metrics._COUNTERS:
            assert f"# HELP {name}" in text
            assert f"# TYPE {name} counter" in text
        for name in metrics._HISTOGRAMS:
            assert f"# HELP {name}" in text
            assert f"# TYPE {name} histogram" in text

    def test_histogram_buckets_are_cumulative(self):
        metrics.record_api_call("plex", "success", 0.05)
        text = metrics.render_prometheus_text()
        lines = {
            line.split(" ")[0]: float(line.split(" ")[1])
            for line in text.splitlines()
            if line.startswith('curatarr_api_request_duration_seconds_bucket{service="plex"')
        }
        # A 0.05s observation falls into every bucket (le >= 0.05),
        # including +Inf - cumulative histogram semantics.
        for key, count in lines.items():
            assert count == 1.0, f"{key} should count the single 0.05s observation"

    def test_never_makes_a_network_call(self, monkeypatch):
        """Scraping must never trigger a Plex/TMDB/etc. request - see
        module docstring. Any accidental network call would trip this
        suite's own autouse _block_non_loopback_sockets guard anyway,
        but assert directly that nothing under `requests`/`socket` is
        even touched by patching them to raise if called."""
        import socket as socket_module

        def _explode(*args, **kwargs):
            raise AssertionError("render_prometheus_text() must not touch the network")

        monkeypatch.setattr(socket_module.socket, "connect", _explode)
        metrics.record_recommender_run("movie", "success", 1.0)
        metrics.render_prometheus_text()  # must not raise


class TestLoadStateFailureModes:
    def test_missing_state_file_renders_cleanly(self, tmp_path):
        assert not os.path.isfile(_state_file_path(tmp_path))
        text = metrics.render_prometheus_text()
        assert "curatarr_build_info" in text

    def test_corrupt_state_file_fails_open(self, tmp_path):
        path = _state_file_path(tmp_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        # Must not raise - corrupt state degrades to "no data recorded
        # yet", never a broken /metrics scrape.
        text = metrics.render_prometheus_text()
        assert "curatarr_build_info" in text
