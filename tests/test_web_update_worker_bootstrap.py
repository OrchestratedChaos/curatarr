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
Regression tests for the two defects that made the web UI's "Update now"
button fail permanently on a source install.

1. The detached worker is spawned as a PLAIN SCRIPT
   (`sys.executable os.path.abspath(web/update_apply.py)`), which puts
   web/ on sys.path rather than the project root, so its module-level
   `from utils import ...` raised ModuleNotFoundError and the worker died
   before doing anything. Nothing surfaced it: the route had already
   returned 202, and the traceback went to logs/update_apply.log.

2. Because subprocess.Popen() succeeds the moment the child is spawned,
   that instantly-dead worker left UpdateManager._in_progress True with
   nothing alive to clear it - so every subsequent click returned 409
   CONFLICT until the server process was restarted. One transient
   failure permanently disabled updates.

Observed together on a real install: an update attempt logged
ModuleNotFoundError, and every "Update now" for the next day returned
409.
"""

import pathlib
import subprocess
import sys

import pytest

from web.update_apply import UpdateManager

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKER = REPO_ROOT / "web" / "update_apply.py"


class TestWorkerIsImportableAsAScript:
    """The exact invocation UpdateManager._spawn_worker uses."""

    def test_running_worker_as_a_script_does_not_fail_to_import(self):
        # No args, so argparse rejects it and exits 2 - but only AFTER the
        # module body (and its `from utils import ...`) has executed. An
        # import error would instead surface as a traceback on stderr.
        proc = subprocess.run(
            [sys.executable, str(WORKER)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        assert "ModuleNotFoundError" not in proc.stderr, (
            f"worker cannot import its own dependencies when spawned as a script:\n{proc.stderr}"
        )
        assert "usage:" in proc.stderr, f"expected argparse usage, got:\n{proc.stderr}"

    def test_worker_imports_from_an_unrelated_cwd(self, tmp_path):
        """cwd is not what makes the import work - the sys.path bootstrap is.

        The spawner does pass cwd=project_root, which makes it tempting to
        assume that's sufficient; it isn't, because cwd is not on sys.path
        for a script invocation. Running from elsewhere proves the fix
        doesn't secretly depend on it.
        """
        proc = subprocess.run(
            [sys.executable, str(WORKER)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=60,
        )
        assert "ModuleNotFoundError" not in proc.stderr, proc.stderr
        assert "usage:" in proc.stderr

    def test_bootstrap_is_a_noop_when_imported_as_a_module(self):
        """`from web import update_apply` must not shove a duplicate entry
        onto sys.path - the guard is on __package__ for that reason."""
        code = (
            "import sys; before = list(sys.path);"
            "import web.update_apply;"
            "print('DUPLICATE' if len(sys.path) != len(before) else 'CLEAN')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        assert "CLEAN" in proc.stdout, f"sys.path mutated on normal import: {proc.stdout} {proc.stderr}"


class FakeProc:
    """Stands in for subprocess.Popen - poll() is all UpdateManager uses."""

    def __init__(self, exit_code=None, pid=4242):
        self._exit_code = exit_code
        self.pid = pid
        self.returncode = exit_code

    def poll(self):
        return self._exit_code


class TestDeadWorkerDoesNotWedgeTheButton:
    @pytest.fixture
    def manager(self, tmp_path):
        return UpdateManager(project_root=str(tmp_path), logs_dir=str(tmp_path / "logs"))

    def test_reports_in_progress_while_the_worker_is_alive(self, manager):
        manager._in_progress = True
        manager._worker = FakeProc(exit_code=None)  # still running
        assert manager.is_in_progress() is True

    def test_clears_in_progress_when_the_worker_has_exited(self, manager):
        """The actual bug: worker died, server lived, flag stuck forever."""
        manager._in_progress = True
        manager._worker = FakeProc(exit_code=1)  # crashed, e.g. ModuleNotFoundError
        assert manager.is_in_progress() is False
        assert manager._in_progress is False

    def test_clears_in_progress_even_on_a_zero_exit(self, manager):
        """A worker that exits 0 without restarting the server is equally
        stuck - success is the server going away, not a zero exit code."""
        manager._in_progress = True
        manager._worker = FakeProc(exit_code=0)
        assert manager.is_in_progress() is False

    def test_a_crashed_worker_does_not_block_the_next_attempt(self, manager, monkeypatch):
        """End to end: begin_update must not raise UpdateAlreadyInProgress
        just because a previous worker died."""
        manager._in_progress = True
        manager._worker = FakeProc(exit_code=1)

        monkeypatch.setattr("web.update_apply.check_verified_update", lambda root: "v9.9.9")
        spawned = {}
        monkeypatch.setattr(
            UpdateManager, "_spawn_worker", lambda self, host, port: spawned.update(host=host, port=port)
        )

        tag = manager.begin_update("127.0.0.1", 8787)
        assert tag == "v9.9.9"
        assert spawned == {"host": "127.0.0.1", "port": 8787}

    def test_still_rejects_a_genuinely_concurrent_update(self, manager, monkeypatch):
        """The guard must not be so permissive that two real updates overlap."""
        manager._in_progress = True
        manager._worker = FakeProc(exit_code=None)  # alive

        monkeypatch.setattr("web.update_apply.check_verified_update", lambda root: "v9.9.9")
        from web.update_apply import UpdateAlreadyInProgressError

        with pytest.raises(UpdateAlreadyInProgressError):
            manager.begin_update("127.0.0.1", 8787)

    def test_no_worker_handle_yet_is_still_in_progress(self, manager):
        """Between setting the flag and Popen returning there's no handle -
        that window must not read as 'not running'."""
        manager._in_progress = True
        manager._worker = None
        assert manager.is_in_progress() is True
