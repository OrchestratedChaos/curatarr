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
"""

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
