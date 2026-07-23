"""
Tests for curatarr_app.py - the PyInstaller binary entry point.

With no `--run-recommender` argument this is deliberately thin (see the
module docstring): "running this module calls web.app.main()", since
all real UI logic already lives in - and is already tested via -
web/app.py.

With `--run-recommender <engine> [user]`, this module is what makes the
web UI's Run button work in a frozen PyInstaller binary (see
web/job_runner.py's _build_command) - it dispatches to the requested
recommender's own main() instead of shelling out to a
recommenders/<x>.py file that doesn't exist once packaged.

_attach_or_setup_console() (the AttachConsole/AllocConsole/CONOUT$
dance behind the windowed, console=False Windows build) is marked
`# pragma: no cover` in curatarr_app.py itself rather than unit-tested
here - it needs the real Windows ctypes console API, which doesn't
exist on the Linux CI runner (or this Mac dev machine). It's verified
against an actual Windows build as part of the release process instead
(see RELEASING.md). The tests below cover everything around it that
*is* safely testable cross-platform: debug detection, the log path,
and _configure_windowed_launch()'s not-frozen/not-Windows no-op guard.
"""

import os
import runpy
import sys
from unittest.mock import patch

import pytest

import curatarr_app


class TestCuratarrApp:
    def test_imports_main_from_web_app(self):
        """curatarr_app.main is the same function web.app.main() is."""
        from web.app import main as web_app_main
        assert curatarr_app.main is web_app_main

    @patch('web.app.main')
    def test_running_as_script_calls_main(self, mock_main):
        """PyInstaller runs this file as __main__ - confirm that path
        calls main() exactly once, matching run-ui.sh / run-ui.ps1."""
        runpy.run_module('curatarr_app', run_name='__main__')
        mock_main.assert_called_once_with()


class TestDebugRequested:
    """Tests for _debug_requested() - gates the AllocConsole fallback
    and file-logging level in _attach_or_setup_console()."""

    def test_true_when_debug_flag_present(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['curatarr', '--debug'])
        monkeypatch.delenv('CURATARR_DEBUG', raising=False)
        assert curatarr_app._debug_requested() is True

    def test_true_when_env_var_set(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['curatarr'])
        monkeypatch.setenv('CURATARR_DEBUG', '1')
        assert curatarr_app._debug_requested() is True

    def test_false_by_default(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['curatarr'])
        monkeypatch.delenv('CURATARR_DEBUG', raising=False)
        assert curatarr_app._debug_requested() is False


class TestBootLogPath:
    """Tests for _boot_log_path() - where the windowed build logs to
    when there's no console to print to."""

    def test_joins_project_root_logs_curatarr_log(self, monkeypatch, tmp_path):
        monkeypatch.setattr('utils.get_project_root', lambda: str(tmp_path))
        result = curatarr_app._boot_log_path()
        assert result == os.path.join(str(tmp_path), 'logs', 'curatarr.log')


class TestConfigureWindowedLaunch:
    """_configure_windowed_launch() is only meaningful for the frozen
    Windows build (curatarr.spec's console=False) - everywhere else it
    must be a no-op, since macOS/Linux builds and non-frozen dev runs
    already have a normal, working console."""

    def test_noop_when_not_frozen(self, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        monkeypatch.setattr(os, 'name', 'nt')
        with patch('curatarr_app._attach_or_setup_console') as mock_attach:
            curatarr_app._configure_windowed_launch()
        mock_attach.assert_not_called()

    def test_noop_when_not_windows(self, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        monkeypatch.setattr(os, 'name', 'posix')
        with patch('curatarr_app._attach_or_setup_console') as mock_attach:
            curatarr_app._configure_windowed_launch()
        mock_attach.assert_not_called()

    def test_dispatches_when_frozen_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        monkeypatch.setattr(os, 'name', 'nt')
        monkeypatch.setattr(sys, 'argv', ['curatarr'])
        monkeypatch.delenv('CURATARR_DEBUG', raising=False)
        with patch('curatarr_app._attach_or_setup_console') as mock_attach:
            curatarr_app._configure_windowed_launch()
        mock_attach.assert_called_once_with(False)


class TestRunOneRecommender:
    """Tests for _run_one_recommender() - the dispatch used when frozen
    (see web/job_runner.py._build_command's `--run-recommender` path)."""

    def test_dispatches_movie_engine_with_rewritten_argv(self, monkeypatch):
        called = {}

        def _fake_main():
            called['argv'] = list(sys.argv)

        monkeypatch.setattr('recommenders.movie.main', _fake_main)
        curatarr_app._run_one_recommender('movie', ['alice'])
        assert called['argv'][1:] == ['alice']

    def test_dispatches_tv_engine(self, monkeypatch):
        called = {}
        monkeypatch.setattr('recommenders.tv.main', lambda: called.setdefault('ran', True))
        curatarr_app._run_one_recommender('tv', [])
        assert called.get('ran') is True

    def test_dispatches_external_engine(self, monkeypatch):
        called = {}
        monkeypatch.setattr('recommenders.external.main', lambda: called.setdefault('ran', True))
        curatarr_app._run_one_recommender('external', [])
        assert called.get('ran') is True

    def test_unknown_engine_exits_with_error(self):
        with pytest.raises(SystemExit) as exc_info:
            curatarr_app._run_one_recommender('bogus', [])
        assert exc_info.value.code == 2


class TestDispatchRecommender:
    """Tests for _dispatch_recommender() - the --run-recommender argv
    parsing, including the 'full' engine's movie->tv->external chain."""

    def test_no_engine_argument_exits_with_error(self):
        with pytest.raises(SystemExit) as exc_info:
            curatarr_app._dispatch_recommender([])
        assert exc_info.value.code == 2

    def test_full_engine_runs_movie_tv_external_in_order(self, monkeypatch):
        order = []
        monkeypatch.setattr('recommenders.movie.main', lambda: order.append('movie'))
        monkeypatch.setattr('recommenders.tv.main', lambda: order.append('tv'))
        monkeypatch.setattr('recommenders.external.main', lambda: order.append('external'))

        curatarr_app._dispatch_recommender(['full'])

        assert order == ['movie', 'tv', 'external']

    def test_single_engine_with_user_passes_user_through(self, monkeypatch):
        called = {}

        def _fake_main():
            called['argv'] = list(sys.argv)

        monkeypatch.setattr('recommenders.movie.main', _fake_main)
        curatarr_app._dispatch_recommender(['movie', 'alice'])
        assert called['argv'][1:] == ['alice']
