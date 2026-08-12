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
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

import curatarr_app


class TestCuratarrApp:
    def test_launch_web_ui_imports_and_calls_web_app_main(self):
        """_launch_web_ui() is the only place in this module that
        imports web.app.main - deliberately deferred here (not at
        module level, see this module's docstring and
        _launch_web_ui's own) so CLI/cron-only paths (--version,
        --run-recommender, --self-update) never need flask installed."""
        with patch("web.app.main") as mock_main:
            curatarr_app._launch_web_ui()
        mock_main.assert_called_once_with()

    @patch("web.app.main")
    def test_running_as_script_calls_main(self, mock_main):
        """PyInstaller runs this file as __main__ - confirm that path
        calls main() exactly once, matching run-ui.sh / run-ui.ps1."""
        runpy.run_module("curatarr_app", run_name="__main__")
        mock_main.assert_called_once_with()


class TestCliPathsDontNeedFlask:
    """Regression coverage for the CLI-only-install bug fixed alongside
    _launch_web_ui() (v2.10.4): `curatarr_app.py` used to do
    `from web.app import main` at module level, unconditionally, before
    any argv dispatch - so a CLI/cron-only install (`pip install -r
    requirements.lock` per requirements.txt's own header, deliberately
    without requirements-ui.txt/.lock's flask) died with
    `ModuleNotFoundError: No module named 'flask'` on *every* invocation,
    including `--version` and `--run-recommender`, neither of which
    touch the web UI.

    Run in a real subprocess with a meta path finder that makes
    `import flask` raise ModuleNotFoundError, the same failure shape a
    genuine CLI-only install (flask never pip-installed at all) hits -
    proving these paths don't merely avoid *calling* flask code but
    never attempt to import it in the first place. An in-process
    `sys.modules` check can't do this reliably in a full suite run: other
    test modules (test_web_*.py) import flask/web.app first and leave
    them cached in sys.modules for the rest of the session regardless of
    import order.
    """

    _BLOCK_FLASK = (
        "import sys\n"
        "class _BlockFlask:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'flask' or name.startswith('flask.'):\n"
        "            raise ModuleNotFoundError(f'blocked for test: {name}', name=name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _BlockFlask())\n"
    )

    def _run(self, extra_argv):
        repo_root = os.path.dirname(os.path.abspath(curatarr_app.__file__))
        script = (
            self._BLOCK_FLASK
            + f"sys.argv = ['curatarr_app.py'] + {extra_argv!r}\n"
            + "import runpy\n"
            + "runpy.run_path('curatarr_app.py', run_name='__main__')\n"
        )
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )

    def test_version_flag_succeeds_with_flask_unimportable(self):
        result = self._run(["--version"])
        from utils.config import __version__

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stdout.strip() == __version__

    def test_help_flag_succeeds_with_flask_unimportable(self):
        result = self._run(["--help"])
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "usage: curatarr" in result.stdout

    def test_run_recommender_dispatch_does_not_need_flask(self):
        """Only checks dispatch reaches _run_one_recommender without
        ModuleNotFoundError on flask - the unknown-engine branch exits
        (2) before touching Plex/network, keeping this a fast,
        deterministic subprocess check."""
        result = self._run(["--run-recommender", "bogus"])
        assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "ModuleNotFoundError" not in result.stderr
        assert "flask" not in result.stderr.lower()

    def test_normal_launch_gives_actionable_error_not_a_traceback(self):
        """The no-flag (web UI) launch path DOES need flask - but a
        CLI-only install missing it must get one clear, actionable
        message pointing at requirements-ui.txt, never a raw
        ModuleNotFoundError traceback."""
        result = self._run([])
        assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "requirements-ui.txt" in result.stderr
        assert "Traceback" not in result.stderr


class TestDispatchViaRunpy:
    """Argv-based dispatch routing at the bottom of curatarr_app.py -
    exercised via runpy.run_module the same way
    test_running_as_script_calls_main above does, since functions
    defined at module top-level get freshly redefined on each re-exec
    (only imports from OTHER modules - web.app.main, web.update_apply.
    run_self_update_worker, utils.self_update.cleanup_stale_old_binary -
    can be usefully mocked here; curatarr_app's own
    _run_self_update_cli is instead exercised for real, via its actual
    (safe, deterministic) not-frozen early-exit path)."""

    def test_self_update_worker_flag_dispatches_with_argv(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["curatarr", "--self-update-worker", "--pid", "1", "--host", "x", "--port", "2"]
        )
        with patch("web.update_apply.run_self_update_worker") as mock_worker:
            runpy.run_module("curatarr_app", run_name="__main__")
        mock_worker.assert_called_once_with(["--pid", "1", "--host", "x", "--port", "2"])

    def test_self_update_flag_dispatches_to_cli_handler(self, monkeypatch, capsys):
        """Not frozen in the test environment - _run_self_update_cli's
        own real (safe, deterministic) not-frozen early-exit path
        proves dispatch reached it, without needing to mock a
        same-module function across a runpy re-exec."""
        monkeypatch.setattr(sys, "argv", ["curatarr", "--self-update"])
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("curatarr_app", run_name="__main__")
        assert exc_info.value.code == 2
        assert "--self-update only applies to a downloaded binary" in capsys.readouterr().err

    def test_help_flag_prints_usage_and_exits_0(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["curatarr", "--help"])
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("curatarr_app", run_name="__main__")
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "usage: curatarr" in out
        assert "--run-recommender" in out

    def test_short_h_flag_prints_usage_and_exits_0(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["curatarr", "-h"])
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("curatarr_app", run_name="__main__")
        assert exc_info.value.code == 0
        assert "usage: curatarr" in capsys.readouterr().out

    def test_frozen_normal_launch_cleans_up_stale_binary_before_main(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["curatarr"])
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        with (
            patch("utils.self_update.cleanup_stale_old_binary") as mock_cleanup,
            patch("web.app.main") as mock_main,
            patch("curatarr_app._configure_windowed_launch"),
        ):
            runpy.run_module("curatarr_app", run_name="__main__")
        mock_cleanup.assert_called_once_with()
        mock_main.assert_called_once_with()

    def test_not_frozen_normal_launch_skips_cleanup(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["curatarr"])
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        with patch("utils.self_update.cleanup_stale_old_binary") as mock_cleanup, patch("web.app.main"):
            runpy.run_module("curatarr_app", run_name="__main__")
        mock_cleanup.assert_not_called()


class TestRunSelfUpdateCli:
    """Direct unit tests for _run_self_update_cli() - the `--self-update`
    CLI flag's handler (see curatarr_app.py's module docstring). The
    actual download/verify/swap it delegates to is
    utils.self_update.perform_self_update(), mocked here entirely - its
    own logic is tests/test_self_update.py's job."""

    def test_not_frozen_prints_clear_message_and_exits_2(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        with pytest.raises(SystemExit) as exc_info:
            curatarr_app._run_self_update_cli()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "--self-update only applies to a downloaded binary" in err
        assert "run.sh" in err or "run.ps1" in err

    def test_success_prints_new_version_and_exits_cleanly(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        with patch("utils.self_update.perform_self_update", return_value="2.9.0"):
            curatarr_app._run_self_update_cli()  # must not raise/exit non-zero
        out = capsys.readouterr().out
        assert "v2.9.0" in out

    def test_no_update_available_prints_message_and_exits_0(self, monkeypatch, capsys):
        from utils.self_update import NoUpdateAvailableError

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        with patch("utils.self_update.perform_self_update", side_effect=NoUpdateAvailableError("nothing newer")):
            with pytest.raises(SystemExit) as exc_info:
                curatarr_app._run_self_update_cli()
        assert exc_info.value.code == 0
        assert "nothing newer" in capsys.readouterr().out

    def test_verification_failure_prints_error_and_exits_1(self, monkeypatch, capsys):
        from utils.self_update import HashMismatchError

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        with patch("utils.self_update.perform_self_update", side_effect=HashMismatchError("bad hash")):
            with pytest.raises(SystemExit) as exc_info:
                curatarr_app._run_self_update_cli()
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "bad hash" in err
        assert "left unchanged" in err


class TestSuppressWindowsCrashDialogs:
    """_suppress_windows_crash_dialogs() - SetErrorMode(SEM_FAILCRITICALERRORS
    | SEM_NOGPFAULTERRORBOX), called first thing on every frozen Windows
    launch (worker, relaunch, normal UI, CLI alike) so a native-level
    fault can never pop a modal Windows Error Reporting dialog on the
    user's desktop - see that function's docstring. Marked
    `# pragma: no cover` in curatarr_app.py itself (real Windows ctypes
    API, same category as _attach_or_setup_console - see this file's
    module docstring), but still exercised here with a faked
    ctypes.windll so its guard/call/never-raises behavior is proven
    cross-platform."""

    def test_noop_when_not_frozen(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(os, "name", "nt")
        curatarr_app._suppress_windows_crash_dialogs()  # must not raise

    def test_noop_when_not_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(os, "name", "posix")
        curatarr_app._suppress_windows_crash_dialogs()  # must not raise

    def test_calls_set_error_mode_when_frozen_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(os, "name", "nt")
        mock_windll = Mock()
        monkeypatch.setattr(curatarr_app.ctypes, "windll", mock_windll, raising=False)

        curatarr_app._suppress_windows_crash_dialogs()

        SEM_FAILCRITICALERRORS = 0x0001
        SEM_NOGPFAULTERRORBOX = 0x0002
        mock_windll.kernel32.SetErrorMode.assert_called_once_with(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX)

    def test_never_raises_even_if_the_api_call_itself_fails(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(os, "name", "nt")
        mock_windll = Mock()
        mock_windll.kernel32.SetErrorMode.side_effect = OSError("no such API")
        monkeypatch.setattr(curatarr_app.ctypes, "windll", mock_windll, raising=False)

        curatarr_app._suppress_windows_crash_dialogs()  # must not raise


class TestDebugRequested:
    """Tests for _debug_requested() - gates the AllocConsole fallback
    and file-logging level in _attach_or_setup_console()."""

    def test_true_when_debug_flag_present(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["curatarr", "--debug"])
        monkeypatch.delenv("CURATARR_DEBUG", raising=False)
        assert curatarr_app._debug_requested() is True

    def test_true_when_env_var_set(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["curatarr"])
        monkeypatch.setenv("CURATARR_DEBUG", "1")
        assert curatarr_app._debug_requested() is True

    def test_false_by_default(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["curatarr"])
        monkeypatch.delenv("CURATARR_DEBUG", raising=False)
        assert curatarr_app._debug_requested() is False


class TestBootLogPath:
    """Tests for _boot_log_path() - where the windowed build logs to
    when there's no console to print to."""

    def test_joins_project_root_logs_curatarr_log(self, monkeypatch, tmp_path):
        monkeypatch.setattr("utils.get_project_root", lambda: str(tmp_path))
        result = curatarr_app._boot_log_path()
        assert result == os.path.join(str(tmp_path), "logs", "curatarr.log")


class TestConfigureWindowedLaunch:
    """_configure_windowed_launch() is only meaningful for the frozen
    Windows build (curatarr.spec's console=False) - everywhere else it
    must be a no-op, since macOS/Linux builds and non-frozen dev runs
    already have a normal, working console."""

    def test_noop_when_not_frozen(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(os, "name", "nt")
        with patch("curatarr_app._attach_or_setup_console") as mock_attach:
            curatarr_app._configure_windowed_launch()
        mock_attach.assert_not_called()

    def test_noop_when_not_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(os, "name", "posix")
        with patch("curatarr_app._attach_or_setup_console") as mock_attach:
            curatarr_app._configure_windowed_launch()
        mock_attach.assert_not_called()

    def test_dispatches_when_frozen_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(sys, "argv", ["curatarr"])
        monkeypatch.delenv("CURATARR_DEBUG", raising=False)
        with patch("curatarr_app._attach_or_setup_console") as mock_attach:
            curatarr_app._configure_windowed_launch()
        mock_attach.assert_called_once_with(False)


class TestRunOneRecommender:
    """Tests for _run_one_recommender() - the dispatch used when frozen
    (see web/job_runner.py._build_command's `--run-recommender` path)."""

    def test_dispatches_movie_engine_with_rewritten_argv(self, monkeypatch):
        called = {}

        def _fake_main():
            called["argv"] = list(sys.argv)

        monkeypatch.setattr("recommenders.movie.main", _fake_main)
        curatarr_app._run_one_recommender("movie", ["alice"])
        assert called["argv"][1:] == ["alice"]

    def test_dispatches_tv_engine(self, monkeypatch):
        called = {}
        monkeypatch.setattr("recommenders.tv.main", lambda: called.setdefault("ran", True))
        curatarr_app._run_one_recommender("tv", [])
        assert called.get("ran") is True

    def test_dispatches_external_engine(self, monkeypatch):
        called = {}
        monkeypatch.setattr("recommenders.external.main", lambda: called.setdefault("ran", True))
        curatarr_app._run_one_recommender("external", [])
        assert called.get("ran") is True

    def test_unknown_engine_exits_with_error(self):
        with pytest.raises(SystemExit) as exc_info:
            curatarr_app._run_one_recommender("bogus", [])
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
        monkeypatch.setattr("recommenders.movie.main", lambda: order.append("movie"))
        monkeypatch.setattr("recommenders.tv.main", lambda: order.append("tv"))
        monkeypatch.setattr("recommenders.external.main", lambda: order.append("external"))

        curatarr_app._dispatch_recommender(["full"])

        assert order == ["movie", "tv", "external"]

    def test_single_engine_with_user_passes_user_through(self, monkeypatch):
        called = {}

        def _fake_main():
            called["argv"] = list(sys.argv)

        monkeypatch.setattr("recommenders.movie.main", _fake_main)
        curatarr_app._dispatch_recommender(["movie", "alice"])
        assert called["argv"][1:] == ["alice"]
