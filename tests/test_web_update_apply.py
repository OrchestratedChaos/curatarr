"""Tests for the web UI's "Update now" flow: web/update_apply.py
(UpdateManager + the detached worker's decision logic) and the
/update/apply, /healthz routes in web/app.py. Covers BOTH the source
install path (git-based, unchanged since v2.8.28) and the frozen-binary
path (utils.self_update-based - see that module's and this file's
module docstrings for the full trust chain and hand-off architecture).

What's covered: the precondition-check gate for both install types (no
verified/known release -> no restart, error returned), single-run
locking/concurrency, /healthz, the job-in-progress quiesce gate, and
the worker's own decision logic - source installs always relaunch
regardless of apply outcome (never a dead port); frozen binaries verify
then hand off to an external script and stop, never relaunching
in-process themselves (see _run_frozen_verify_and_handoff) - with every
subprocess/signal/sleep call mocked out. The frozen path's actual
download/verify logic (the real security boundary) is unit-tested
separately and thoroughly in tests/test_self_update.py, and the
hand-off script's own content/launch mechanics in
tests/test_self_update_handoff.py - here both are only ever mocked,
since this file's job is proving the worker calls into them correctly
and never lets an unhandled exception escape.

What's NOT covered (by design - matches this repo's existing precedent
for OS-process-boundary code, e.g. curatarr_app.py's
_attach_or_setup_console): actually spawning a detached process,
sending a real signal to a real process, or actually rebinding a real
socket after a real kill. That's exercised in practice via the
detached worker's own logging (logs/update_apply.log) - see
web/update_apply.py's module docstring - not something a unit test can
safely or meaningfully simulate (in particular, a test must NEVER pass
its own pid to _shut_down_old_server with a real signal - that would
kill the test process/runner).
"""

import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils import self_update
from web.app import create_app
from web.update_apply import (
    OLD_SERVER_SHUTDOWN_TIMEOUT_SECONDS,
    RELAUNCH_MAX_ATTEMPTS,
    RESPONSE_FLUSH_DELAY_SECONDS,
    UpdateAlreadyInProgressError,
    UpdateManager,
    UpdateNotAvailableError,
    _check_update_available_for_binary,
    _fresh_worker_temp_dir,
    _parse_binary_worker_args,
    _parse_worker_args,
    _pid_alive,
    _port_is_listening,
    _recommender_job_in_progress,
    _relaunch_and_verify,
    _relaunch_ui,
    _run_frozen_verify_and_handoff,
    _run_worker,
    _shut_down_old_server,
    check_verified_update,
    run_self_update_worker,
)


@pytest.fixture
def client(curatarr_web_root):
    app = create_app(project_root=curatarr_web_root)
    app.testing = True
    return app.test_client(), app, curatarr_web_root


class TestHealthz:
    def test_returns_running_version(self, client):
        c, app, root = client
        resp = c.get('/healthz')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'version' in data
        assert isinstance(data['version'], str) and data['version']


class TestFrozenAndSourceBothGetTheButton:
    """As of v2.8.29, frozen binaries get a real "Update now" button too
    (utils.self_update-based) - no longer a 400/"not supported" rejection.
    See tests/test_web_update_banner.py for the banner-content assertions
    themselves; these are the /update/apply route-level checks."""

    def test_frozen_binary_precondition_check_uses_binary_helper(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        with patch('web.update_apply._check_update_available_for_binary', return_value='v2.9.0'), \
                patch.object(UpdateManager, '_spawn_worker') as mock_spawn:
            resp = c.post('/update/apply')
        assert resp.status_code == 202
        assert resp.get_json()['tag'] == 'v2.9.0'
        mock_spawn.assert_called_once()

    def test_frozen_binary_never_reaches_source_check_verified_update(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        with patch('web.update_apply.check_verified_update') as mock_check, \
                patch('web.update_apply._check_update_available_for_binary', return_value=None):
            c.post('/update/apply')
            mock_check.assert_not_called()

    def test_source_install_never_reaches_binary_check(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with patch('web.update_apply._check_update_available_for_binary') as mock_binary_check, \
                patch('web.update_apply.check_verified_update', return_value=None):
            c.post('/update/apply')
            mock_binary_check.assert_not_called()

    def test_frozen_banner_has_update_now_button(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        config_path = os.path.join(root, 'config', 'config.yml')
        with open(config_path, 'a', encoding='utf-8') as f:
            f.write('general:\n  update_mode: notify\n')
        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/')
        assert b'update-now-btn' in resp.data

    def test_source_banner_has_update_now_button(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        config_path = os.path.join(root, 'config', 'config.yml')
        with open(config_path, 'a', encoding='utf-8') as f:
            f.write('general:\n  update_mode: notify\n')
        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/')
        assert b'update-now-btn' in resp.data


class TestPreconditionCheckGate:
    def test_no_verified_release_returns_404_and_does_not_spawn(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with patch('web.update_apply.check_verified_update', return_value=None), \
                patch.object(UpdateManager, '_spawn_worker') as mock_spawn:
            resp = c.post('/update/apply')
        assert resp.status_code == 404
        assert 'error' in resp.get_json()
        mock_spawn.assert_not_called()

    def test_verified_release_returns_202_and_spawns(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with patch('web.update_apply.check_verified_update', return_value='v2.9.0'), \
                patch.object(UpdateManager, '_spawn_worker') as mock_spawn:
            resp = c.post('/update/apply')
        assert resp.status_code == 202
        data = resp.get_json()
        assert data['status'] == 'started'
        assert data['tag'] == 'v2.9.0'
        mock_spawn.assert_called_once()

    def test_precondition_failure_leaves_lock_released(self, client, monkeypatch):
        """A failed precondition check must not leave the lock stuck -
        a later click (e.g. once a release actually does land) has to
        be able to try again."""
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with patch('web.update_apply.check_verified_update', return_value=None):
            c.post('/update/apply')
        assert app.update_manager.is_in_progress() is False


class TestRefusesToUpdateWhileJobRunning:
    """A recommender run's subprocess is itself another instance of
    this same binary (frozen), sharing PyInstaller onefile extraction
    state with the server that spawned it - killing/swapping that
    server out from under a running job could crash it (see
    utils.self_update.fresh_extraction_temp_dir's docstring for the
    real end-to-end crash that motivated this). The route-level check
    is the immediate, synchronous gate; web/update_apply.py's own
    _recommender_job_in_progress is the race-safe, cross-process second
    gate the detached worker itself re-checks - see
    TestRunWorkerAbortsWhenJobInProgress below."""

    def test_job_running_returns_409_and_does_not_begin_update(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with patch.object(app.job_manager, 'is_running', return_value=True), \
                patch.object(UpdateManager, 'begin_update') as mock_begin:
            resp = c.post('/update/apply')
        assert resp.status_code == 409
        assert 'in progress' in resp.get_json()['error']
        mock_begin.assert_not_called()

    def test_job_not_running_proceeds_normally(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with patch.object(app.job_manager, 'is_running', return_value=False), \
                patch('web.update_apply.check_verified_update', return_value='v2.9.0'), \
                patch.object(UpdateManager, '_spawn_worker'):
            resp = c.post('/update/apply')
        assert resp.status_code == 202


class TestLockConcurrency:
    def test_second_request_rejected_while_first_in_progress(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with patch('web.update_apply.check_verified_update', return_value='v2.9.0'), \
                patch.object(UpdateManager, '_spawn_worker'):
            resp1 = c.post('/update/apply')
            resp2 = c.post('/update/apply')
        assert resp1.status_code == 202
        assert resp2.status_code == 409
        assert 'error' in resp2.get_json()

    def test_second_request_does_not_spawn_a_second_worker(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with patch('web.update_apply.check_verified_update', return_value='v2.9.0'), \
                patch.object(UpdateManager, '_spawn_worker') as mock_spawn:
            c.post('/update/apply')
            c.post('/update/apply')
        assert mock_spawn.call_count == 1

    def test_begin_update_raises_directly(self):
        """Unit-level (no HTTP) check of the gate itself."""
        manager = UpdateManager('/fake/root', '/fake/root/logs')
        with patch('web.update_apply.check_verified_update', return_value='v2.9.0'), \
                patch.object(UpdateManager, '_spawn_worker'):
            tag = manager.begin_update('127.0.0.1', 8787)
            assert tag == 'v2.9.0'
            assert manager.is_in_progress() is True
            with pytest.raises(UpdateAlreadyInProgressError):
                manager.begin_update('127.0.0.1', 8787)

    def test_frozen_uses_binary_precondition_check(self, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        manager = UpdateManager('/fake/root', '/fake/root/logs')
        with patch('web.update_apply._check_update_available_for_binary', return_value='v2.9.0'), \
                patch.object(UpdateManager, '_spawn_worker'):
            tag = manager.begin_update('127.0.0.1', 8787)
        assert tag == 'v2.9.0'

    def test_frozen_no_update_raises_not_available(self, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        manager = UpdateManager('/fake/root', '/fake/root/logs')
        with patch('web.update_apply._check_update_available_for_binary', return_value=None):
            with pytest.raises(UpdateNotAvailableError):
                manager.begin_update('127.0.0.1', 8787)

    def test_no_update_raises_not_available(self):
        manager = UpdateManager('/fake/root', '/fake/root/logs')
        with patch('web.update_apply.check_verified_update', return_value=None):
            with pytest.raises(UpdateNotAvailableError):
                manager.begin_update('127.0.0.1', 8787)


class TestCheckVerifiedUpdate:
    """Unit tests for the precondition-check helper itself (not through
    the route) - mirrors run.sh's/run.ps1's own --check-verified-update
    mode being shelled out to, never reimplemented."""

    def test_missing_script_returns_none(self, tmp_path):
        assert check_verified_update(str(tmp_path)) is None

    @patch('web.update_apply.subprocess.run')
    def test_nonzero_exit_returns_none(self, mock_run, tmp_path):
        (tmp_path / 'run.sh').write_text('#!/bin/bash\n', encoding='utf-8')
        mock_run.return_value = Mock(returncode=1, stdout='')
        assert check_verified_update(str(tmp_path)) is None

    @patch('web.update_apply.subprocess.run')
    def test_verified_tag_returned(self, mock_run, tmp_path):
        (tmp_path / 'run.sh').write_text('#!/bin/bash\n', encoding='utf-8')
        mock_run.return_value = Mock(returncode=0, stdout='v2.9.0\n')
        assert check_verified_update(str(tmp_path)) == 'v2.9.0'

    @patch('web.update_apply.subprocess.run')
    def test_exception_is_not_fatal(self, mock_run, tmp_path):
        (tmp_path / 'run.sh').write_text('#!/bin/bash\n', encoding='utf-8')
        mock_run.side_effect = Exception('boom')
        assert check_verified_update(str(tmp_path)) is None

    @patch('web.update_apply.subprocess.run')
    def test_timeout_is_not_fatal(self, mock_run, tmp_path):
        import subprocess as sp
        (tmp_path / 'run.sh').write_text('#!/bin/bash\n', encoding='utf-8')
        mock_run.side_effect = sp.TimeoutExpired(cmd='run.sh', timeout=20)
        assert check_verified_update(str(tmp_path)) is None


class TestPidAlive:
    """Safe to test with real PIDs - os.kill(pid, 0) only probes, never
    signals. Never test this with a real terminating signal (see
    TestShutDownOldServer, which mocks everything instead)."""

    def test_own_pid_is_alive(self):
        assert _pid_alive(os.getpid()) is True

    def test_implausible_pid_is_not_alive(self):
        assert _pid_alive(999999) is False

    def test_zero_or_negative_pid_is_not_alive(self):
        assert _pid_alive(0) is False
        assert _pid_alive(-1) is False

    def test_permission_error_means_alive_but_owned_by_someone_else(self):
        with patch('web.update_apply.os.kill', side_effect=PermissionError()):
            assert _pid_alive(1234) is True

    def test_generic_os_error_means_not_alive(self):
        with patch('web.update_apply.os.kill', side_effect=OSError('weird')):
            assert _pid_alive(1234) is False


class TestRecommenderJobInProgress:
    """_recommender_job_in_progress - the worker's own cross-process,
    race-safe re-check of web/job_runner.py's lockfile (same filename,
    deliberately not imported - see that function's docstring)."""

    def test_no_lockfile_means_not_running(self, tmp_path):
        (tmp_path / 'logs').mkdir()
        assert _recommender_job_in_progress(str(tmp_path)) is False

    def test_no_logs_dir_at_all_means_not_running(self, tmp_path):
        assert _recommender_job_in_progress(str(tmp_path)) is False

    def test_live_pid_in_lockfile_means_running(self, tmp_path):
        logs_dir = tmp_path / 'logs'
        logs_dir.mkdir()
        (logs_dir / 'webui_job.lock').write_text(str(os.getpid()), encoding='utf-8')
        assert _recommender_job_in_progress(str(tmp_path)) is True

    def test_dead_pid_in_lockfile_means_not_running(self, tmp_path):
        logs_dir = tmp_path / 'logs'
        logs_dir.mkdir()
        (logs_dir / 'webui_job.lock').write_text('999999', encoding='utf-8')
        with patch('web.update_apply._pid_alive', return_value=False):
            assert _recommender_job_in_progress(str(tmp_path)) is False

    def test_corrupt_lockfile_fails_safe_toward_running(self, tmp_path):
        logs_dir = tmp_path / 'logs'
        logs_dir.mkdir()
        (logs_dir / 'webui_job.lock').write_text('not-a-pid', encoding='utf-8')
        assert _recommender_job_in_progress(str(tmp_path)) is True


class TestRunWorkerAbortsWhenJobInProgress:
    """The worker's own top-level re-check (see
    _recommender_job_in_progress) - applies BEFORE the frozen/source
    branch decision, so if a job is in flight, the old server is left
    COMPLETELY untouched regardless of install type: no shutdown, no
    apply/verify/hand-off, no relaunch."""

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply._run_frozen_verify_and_handoff')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply._recommender_job_in_progress', return_value=True)
    @patch('web.update_apply.time.sleep')
    def test_job_in_progress_skips_everything_frozen(
        self, mock_sleep, mock_job_check, mock_shutdown, mock_frozen_apply, mock_relaunch, monkeypatch
    ):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_shutdown.assert_not_called()
        mock_frozen_apply.assert_not_called()
        mock_relaunch.assert_not_called()

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply._recommender_job_in_progress', return_value=True)
    @patch('web.update_apply.time.sleep')
    def test_job_in_progress_skips_everything_source(
        self, mock_sleep, mock_job_check, mock_shutdown, mock_relaunch, monkeypatch
    ):
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with patch('web.update_apply.subprocess.run') as mock_run:
            _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
            mock_run.assert_not_called()
        mock_shutdown.assert_not_called()
        mock_relaunch.assert_not_called()

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply._recommender_job_in_progress', return_value=True)
    @patch('web.update_apply.time.sleep')
    def test_job_in_progress_logs_why(self, mock_sleep, mock_job_check, mock_shutdown, mock_relaunch, capsys):
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        out = capsys.readouterr().out
        assert 'in progress' in out
        assert 'aborting' in out

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply._run_frozen_verify_and_handoff')
    @patch('web.update_apply._recommender_job_in_progress', return_value=False)
    @patch('web.update_apply.time.sleep')
    def test_no_job_in_progress_proceeds_normally_frozen(
        self, mock_sleep, mock_job_check, mock_frozen_apply, mock_relaunch, monkeypatch
    ):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_frozen_apply.assert_called_once_with('/fake/root', 12345, 8787)
        # The external hand-off script owns the relaunch for a frozen
        # binary - _run_worker itself must not ALSO relaunch.
        mock_relaunch.assert_not_called()

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply._recommender_job_in_progress', return_value=False)
    @patch('web.update_apply.time.sleep')
    def test_no_job_in_progress_proceeds_normally_source(
        self, mock_sleep, mock_job_check, mock_shutdown, mock_relaunch, monkeypatch
    ):
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with patch('web.update_apply.subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout='UPDATED:v2.9.0\n', stderr='')
            _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
            mock_run.assert_called_once()
        mock_shutdown.assert_called_once()
        mock_relaunch.assert_called_once()


class TestShutDownOldServer:
    """Every path here mocks _pid_alive/os.kill/subprocess.run - this
    must NEVER send a real signal to a real pid (that would kill
    whatever process happens to own that pid, including the test
    runner itself if a real pid were used carelessly)."""

    def test_already_dead_pid_returns_immediately_without_signaling(self):
        with patch('web.update_apply._pid_alive', return_value=False), \
                patch('web.update_apply.os.kill') as mock_kill:
            _shut_down_old_server(12345, timeout=5)
        mock_kill.assert_not_called()

    @patch('web.update_apply.time.sleep')
    @patch('web.update_apply.os.kill')
    def test_signals_then_waits_for_exit(self, mock_kill, mock_sleep):
        calls = {'n': 0}

        def _fake_alive(pid):
            calls['n'] += 1
            return calls['n'] < 3  # alive for the first couple checks, then gone

        with patch('web.update_apply._pid_alive', side_effect=_fake_alive):
            _shut_down_old_server(12345, timeout=OLD_SERVER_SHUTDOWN_TIMEOUT_SECONDS)
        mock_kill.assert_called_once_with(12345, 15)  # signal.SIGTERM == 15 on POSIX

    @patch('web.update_apply.time.sleep')
    @patch('web.update_apply.os.kill', side_effect=ProcessLookupError())
    def test_process_vanishing_mid_signal_does_not_raise(self, mock_kill, mock_sleep):
        with patch('web.update_apply._pid_alive', return_value=True):
            _shut_down_old_server(12345, timeout=5)  # must not raise


class TestRelaunchUi:
    """_relaunch_ui - source installs ONLY (see this module's docstring
    and _relaunch_ui's own): the frozen path never calls this function
    at all - _run_worker's frozen branch hands the entire swap+relaunch
    off to an external script instead (see
    utils/self_update_handoff.py)."""

    @patch('web.update_apply.subprocess.Popen')
    def test_spawns_run_ui_with_expected_env(self, mock_popen):
        _relaunch_ui('/fake/root', 9999)
        assert mock_popen.call_count == 1
        args, kwargs = mock_popen.call_args
        assert kwargs['env']['CURATARR_UI_PORT'] == '9999'
        assert kwargs['env']['CURATARR_SKIP_BROWSER_OPEN'] == '1'
        assert kwargs['cwd'] == '/fake/root'

    @patch('web.update_apply.subprocess.Popen')
    def test_behavior_is_unaffected_by_sys_frozen(self, mock_popen, monkeypatch):
        """Regression guard: _relaunch_ui must not branch on
        sys.frozen at all anymore - it's only ever called for source
        installs in the first place."""
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        _relaunch_ui('/fake/root', 9999)
        assert mock_popen.call_count == 1
        cmd = mock_popen.call_args[0][0]
        assert 'run-ui.ps1' in cmd[-1] or 'run-ui.sh' in cmd[-1]


class TestRunWorkerNeverPropagatesUnhandledException:
    """Crash-hardening: _run_worker must NEVER let an exception escape
    unhandled, no matter where it originates - not just the apply
    step's own try/excepts, but ANY unexpected failure between the
    shutdown-wait and the apply step (see _run_worker's docstring for
    why: this process is windowed/console=False, so an unhandled
    exception has no console to even print a traceback to, and
    curatarr_app.py's _suppress_windows_crash_dialogs() only prevents a
    *modal OS dialog* for a lower-level native fault - a plain Python
    exception escaping this function entirely would still mean a
    crashed worker and a dead port)."""

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply._shut_down_old_server', side_effect=RuntimeError('totally unexpected'))
    @patch('web.update_apply.time.sleep')
    def test_unexpected_error_before_apply_step_still_relaunches(self, mock_sleep, mock_shutdown, mock_relaunch):
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)  # must not raise
        mock_relaunch.assert_called_once_with('/fake/root', 8787)

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply.time.sleep', side_effect=RuntimeError('even the sleep itself blew up'))
    def test_unexpected_error_at_the_very_start_still_relaunches(self, mock_sleep, mock_relaunch):
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)  # must not raise
        mock_relaunch.assert_called_once_with('/fake/root', 8787)

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply._shut_down_old_server', side_effect=RuntimeError('totally unexpected'))
    @patch('web.update_apply.time.sleep')
    def test_unexpected_error_is_logged(self, mock_sleep, mock_shutdown, mock_relaunch, capsys):
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        out = capsys.readouterr().out
        assert 'UNEXPECTED ERROR' in out
        assert 'totally unexpected' in out


class TestPortIsListening:
    def test_false_when_nothing_listening(self):
        assert _port_is_listening(1) is False  # port 1 requires privileges - never bound in CI/dev


class TestRelaunchAndVerify:
    """_relaunch_and_verify - the reliability net around _relaunch_ui's
    fire-and-forget spawn (see that function's docstring for why a
    frozen relaunch's startup time isn't perfectly deterministic).
    Every test shrinks RELAUNCH_VERIFY_TIMEOUT_SECONDS via monkeypatch
    (rather than mocking time.time()'s exact sequence) so the real
    per-attempt poll loop runs for real, just fast - deterministic
    without being fragile to exactly how many times the loop body
    happens to iterate."""

    def test_succeeds_on_first_attempt_without_retrying(self, monkeypatch):
        monkeypatch.setattr('web.update_apply.RELAUNCH_VERIFY_TIMEOUT_SECONDS', 2.0)
        with patch('web.update_apply._relaunch_ui') as mock_relaunch_ui, \
                patch('web.update_apply._port_is_listening', return_value=True) as mock_listening:
            _relaunch_and_verify('/fake/root', 8787)
        mock_relaunch_ui.assert_called_once_with('/fake/root', 8787)
        mock_listening.assert_called_once_with(8787)

    def test_retries_a_fresh_spawn_when_first_attempt_never_comes_up(self, monkeypatch):
        monkeypatch.setattr('web.update_apply.RELAUNCH_VERIFY_TIMEOUT_SECONDS', 0.05)
        state = {'spawns': 0}

        def fake_relaunch(project_root, port):
            state['spawns'] += 1

        def fake_listening(port):
            # Only "comes up" once a SECOND fresh spawn has happened -
            # deterministic regardless of how many times the poll loop
            # itself iterates within the first attempt's tiny timeout.
            return state['spawns'] >= 2

        with patch('web.update_apply._relaunch_ui', side_effect=fake_relaunch) as mock_relaunch_ui, \
                patch('web.update_apply._port_is_listening', side_effect=fake_listening), \
                patch('web.update_apply.time.sleep'):
            _relaunch_and_verify('/fake/root', 8787)
        assert mock_relaunch_ui.call_count == 2

    def test_gives_up_after_max_attempts(self, monkeypatch):
        monkeypatch.setattr('web.update_apply.RELAUNCH_VERIFY_TIMEOUT_SECONDS', 0.02)
        with patch('web.update_apply._relaunch_ui') as mock_relaunch_ui, \
                patch('web.update_apply._port_is_listening', return_value=False), \
                patch('web.update_apply.time.sleep'):
            _relaunch_and_verify('/fake/root', 8787)  # must not raise
        assert mock_relaunch_ui.call_count == RELAUNCH_MAX_ATTEMPTS

    def test_logs_fatal_when_all_attempts_exhausted(self, monkeypatch, capsys):
        monkeypatch.setattr('web.update_apply.RELAUNCH_VERIFY_TIMEOUT_SECONDS', 0.02)
        with patch('web.update_apply._relaunch_ui'), \
                patch('web.update_apply._port_is_listening', return_value=False), \
                patch('web.update_apply.time.sleep'):
            _relaunch_and_verify('/fake/root', 8787)
        out = capsys.readouterr().out
        assert 'FATAL' in out
        assert 'manual restart required' in out

    def test_relaunch_ui_raising_is_caught_and_retried(self, monkeypatch):
        monkeypatch.setattr('web.update_apply.RELAUNCH_VERIFY_TIMEOUT_SECONDS', 2.0)
        with patch(
                'web.update_apply._relaunch_ui',
                side_effect=[Exception('spawn boom'), None],
        ) as mock_relaunch_ui, \
                patch('web.update_apply._port_is_listening', return_value=True), \
                patch('web.update_apply.time.sleep'):
            _relaunch_and_verify('/fake/root', 8787)  # must not raise
        assert mock_relaunch_ui.call_count == 2

    def test_relaunch_ui_raising_on_every_attempt_never_raises_out(self, monkeypatch):
        monkeypatch.setattr('web.update_apply.RELAUNCH_VERIFY_TIMEOUT_SECONDS', 0.02)
        with patch('web.update_apply._relaunch_ui', side_effect=Exception('always fails')) as mock_relaunch_ui, \
                patch('web.update_apply._port_is_listening', return_value=False), \
                patch('web.update_apply.time.sleep'):
            _relaunch_and_verify('/fake/root', 8787)  # must not raise
        assert mock_relaunch_ui.call_count == RELAUNCH_MAX_ATTEMPTS


class TestRunWorkerAlwaysRelaunches:
    """The core "never leave a dead port" guarantee: regardless of
    whether the apply step reports success, no update available, or an
    outright failure, the worker must always relaunch the UI."""

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply.subprocess.run')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_relaunches_after_successful_apply(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch):
        mock_run.return_value = Mock(returncode=0, stdout='UPDATED:v2.9.0\n', stderr='')
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_shutdown.assert_called_once()
        mock_relaunch.assert_called_once_with('/fake/root', 8787)

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply.subprocess.run')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_relaunches_when_apply_finds_nothing(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch):
        mock_run.return_value = Mock(returncode=1, stdout='NO_UPDATE\n', stderr='')
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_relaunch.assert_called_once_with('/fake/root', 8787)

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply.subprocess.run')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_relaunches_when_apply_fails(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch):
        mock_run.return_value = Mock(returncode=1, stdout='FAILED:git checkout failed\n', stderr='')
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_relaunch.assert_called_once_with('/fake/root', 8787)

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply.subprocess.run', side_effect=Exception('subprocess plumbing exploded'))
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_relaunches_even_if_apply_step_raises(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch):
        """Belt-and-suspenders: even an unexpected exception from the
        apply subprocess call itself must not skip the relaunch."""
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_relaunch.assert_called_once_with('/fake/root', 8787)

    @patch('web.update_apply._relaunch_and_verify', side_effect=Exception('could not spawn'))
    @patch('web.update_apply.subprocess.run')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_relaunch_failure_itself_does_not_raise_out_of_run_worker(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch):
        """Even in the worst case (relaunch itself fails), _run_worker
        must return normally (it logs a FATAL line - see the module -
        rather than raising, since nothing downstream could act on an
        exception anyway; this is the true last-resort path)."""
        mock_run.return_value = Mock(returncode=0, stdout='UPDATED:v2.9.0\n', stderr='')
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)  # must not raise

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply.subprocess.run')
    @patch('web.update_apply._shut_down_old_server')
    def test_sleeps_for_response_flush_delay(self, mock_shutdown, mock_run, mock_relaunch):
        mock_run.return_value = Mock(returncode=0, stdout='UPDATED:v2.9.0\n', stderr='')
        with patch('web.update_apply.time.sleep') as mock_sleep:
            _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_sleep.assert_any_call(RESPONSE_FLUSH_DELAY_SECONDS)


class TestSpawnWorkerBuildsCorrectCommand:
    @patch('web.update_apply.subprocess.Popen')
    def test_spawn_worker_command_and_flags(self, mock_popen, tmp_path):
        logs_dir = str(tmp_path / 'logs')
        manager = UpdateManager(str(tmp_path), logs_dir)
        manager._spawn_worker('127.0.0.1', 8787)

        assert mock_popen.call_count == 1
        cmd, kwargs = mock_popen.call_args[0][0], mock_popen.call_args[1]
        assert cmd[0] == sys.executable
        assert '--project-root' in cmd and str(tmp_path) in cmd
        assert '--pid' in cmd and str(os.getpid()) in cmd
        assert '--host' in cmd and '127.0.0.1' in cmd
        assert '--port' in cmd and '8787' in cmd
        if os.name != 'nt':
            assert kwargs.get('start_new_session') is True
        assert os.path.isfile(os.path.join(logs_dir, 'update_apply.log'))


class TestParseWorkerArgs:
    """_parse_worker_args is pure argv-parsing (no subprocess/OS side
    effects) - directly testable, unlike the `if __name__ ==
    '__main__':` block that calls it (see that block's pragma)."""

    def test_parses_all_required_args(self):
        args = _parse_worker_args([
            '--project-root', '/fake/root',
            '--pid', '4242',
            '--host', '127.0.0.1',
            '--port', '8787',
        ])
        assert args.project_root == '/fake/root'
        assert args.pid == 4242
        assert args.host == '127.0.0.1'
        assert args.port == 8787

    def test_missing_required_arg_exits(self):
        with pytest.raises(SystemExit):
            _parse_worker_args(['--project-root', '/fake/root'])


class TestWindowsBranches:
    """Windows-only argv/flag construction, exercised on any platform
    by monkeypatching os.name - safe because every subprocess call in
    these paths is mocked (see web/job_runner.py's own CREATE_NO_WINDOW
    precedent for why the getattr(subprocess, 'X', default) pattern in
    web/update_apply.py matters here: a bare subprocess.CREATE_NEW_
    PROCESS_GROUP reference would raise AttributeError on a non-Windows
    Python the moment this branch actually ran, which is exactly what
    these tests do)."""

    @patch('web.update_apply.subprocess.run')
    def test_check_verified_update_uses_powershell(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setattr('web.update_apply.os.name', 'nt')
        (tmp_path / 'run.ps1').write_text('# stub', encoding='utf-8')
        mock_run.return_value = Mock(returncode=0, stdout='v2.9.0\n')

        result = check_verified_update(str(tmp_path))

        assert result == 'v2.9.0'
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == 'powershell'
        assert '-CheckVerifiedUpdate' in cmd

    def test_pid_alive_uses_tasklist(self, monkeypatch):
        monkeypatch.setattr('web.update_apply.os.name', 'nt')
        with patch('web.update_apply.subprocess.run') as mock_run:
            mock_run.return_value = Mock(stdout='1234 python.exe')
            assert _pid_alive(1234) is True
            assert mock_run.call_args[0][0][0] == 'tasklist'

    def test_pid_alive_tasklist_exception_fails_toward_alive(self, monkeypatch):
        monkeypatch.setattr('web.update_apply.os.name', 'nt')
        with patch('web.update_apply.subprocess.run', side_effect=Exception('no tasklist')):
            assert _pid_alive(1234) is True

    def test_shut_down_old_server_uses_forceful_taskkill(self, monkeypatch):
        """/F is required - see _shut_down_old_server's docstring for
        why plain (non-forceful) taskkill doesn't actually work against
        a console-less Windows process (confirmed via a real end-to-end
        test against a built binary)."""
        monkeypatch.setattr('web.update_apply.os.name', 'nt')
        with patch('web.update_apply._pid_alive', side_effect=[True, False]), \
                patch('web.update_apply.subprocess.run') as mock_run, \
                patch('web.update_apply.time.sleep'):
            _shut_down_old_server(1234, timeout=5)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == 'taskkill'
        assert '/F' in cmd
        assert cmd == ['taskkill', '/F', '/PID', '1234']

    @patch('web.update_apply.subprocess.Popen')
    def test_relaunch_ui_uses_powershell_and_creationflags(self, mock_popen, monkeypatch):
        monkeypatch.setattr('web.update_apply.os.name', 'nt')
        _relaunch_ui('/fake/root', 8787)
        args, kwargs = mock_popen.call_args
        assert args[0][0] == 'powershell'
        assert 'creationflags' in kwargs

    @patch('web.update_apply.subprocess.Popen')
    def test_spawn_worker_sets_creationflags_on_windows(self, mock_popen, tmp_path, monkeypatch):
        monkeypatch.setattr('web.update_apply.os.name', 'nt')
        manager = UpdateManager(str(tmp_path), str(tmp_path / 'logs'))
        manager._spawn_worker('127.0.0.1', 8787)
        _, kwargs = mock_popen.call_args
        assert 'creationflags' in kwargs
        assert 'start_new_session' not in kwargs

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply.subprocess.run')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_run_worker_apply_cmd_uses_powershell_on_windows(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch, monkeypatch):
        monkeypatch.setattr('web.update_apply.os.name', 'nt')
        mock_run.return_value = Mock(returncode=0, stdout='UPDATED:v2.9.0\n', stderr='')
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        apply_cmd = mock_run.call_args[0][0]
        assert apply_cmd[0] == 'powershell'
        assert '-ApplyVerifiedUpdate' in apply_cmd


class TestRunWorkerLogsApplyStderr:
    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply.subprocess.run')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_apply_stderr_is_logged(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch, capsys):
        mock_run.return_value = Mock(returncode=1, stdout='FAILED:git checkout failed\n', stderr='fatal: something')
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        out = capsys.readouterr().out
        assert 'fatal: something' in out


class TestCheckUpdateAvailableForBinary:
    """Unit tests for the frozen-binary precondition check - the ADVISORY
    half of the frozen trust chain (see web/update_apply.py's module
    docstring); the real verification is entirely inside
    utils.self_update.perform_self_update(), tested in
    tests/test_self_update.py."""

    @patch('web.update_apply.update_available')
    def test_returns_v_prefixed_tag_when_newer(self, mock_update_available):
        mock_update_available.return_value = ('2.9.0', '2.8.29', True)
        assert _check_update_available_for_binary() == 'v2.9.0'

    @patch('web.update_apply.update_available')
    def test_returns_none_when_not_newer(self, mock_update_available):
        mock_update_available.return_value = ('2.8.29', '2.8.29', False)
        assert _check_update_available_for_binary() is None

    @patch('web.update_apply.update_available', side_effect=RuntimeError('unexpected'))
    def test_exception_is_not_fatal(self, mock_update_available):
        assert _check_update_available_for_binary() is None

    @patch('web.update_apply.update_available')
    def test_forces_a_fresh_check_not_the_cache(self, mock_update_available):
        mock_update_available.return_value = ('2.9.0', '2.8.29', True)
        _check_update_available_for_binary()
        assert mock_update_available.call_args.kwargs['force_refresh'] is True


class TestRunFrozenVerifyAndHandoff:
    """Unit tests for _run_frozen_verify_and_handoff - the ENTIRE frozen
    apply step (verify, quiesce re-check, shutdown, hand off to the
    external script - see this module's docstring for why it never
    swaps or relaunches anything itself). Mocks
    utils.self_update.download_and_verify_update() and
    utils.self_update_handoff.write_and_launch_handoff_script() entirely
    (their own real logic is tests/test_self_update.py's and
    tests/test_self_update_handoff.py's job respectively); this just
    proves the wiring: what gets called, in what order, and that
    nothing here EVER raises out (see _run_worker's crash-hardening,
    tested below)."""

    def _verified(self, version='2.9.0', asset_path='/fake/root/.curatarr-update-x.tmp'):
        return self_update.VerifiedUpdate(version=version, asset_path=asset_path, asset_name='curatarr-linux-x86_64')

    @patch('web.update_apply.self_update_handoff.write_and_launch_handoff_script')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply._recommender_job_in_progress', return_value=False)
    @patch('web.update_apply.self_update.download_and_verify_update')
    @patch('web.update_apply.self_update.current_binary_path', return_value='/fake/root/curatarr')
    def test_success_shuts_down_old_server_then_hands_off(
        self, mock_current_path, mock_download, mock_job_check, mock_shutdown, mock_handoff,
    ):
        mock_download.return_value = self._verified()
        _run_frozen_verify_and_handoff('/fake/root', 12345, 8787)

        mock_shutdown.assert_called_once_with(12345, OLD_SERVER_SHUTDOWN_TIMEOUT_SECONDS)
        mock_handoff.assert_called_once_with(
            old_pid=12345,
            current_exe_path='/fake/root/curatarr',
            verified_asset_path='/fake/root/.curatarr-update-x.tmp',
            port=8787,
            target_version='2.9.0',
        )

    @patch('web.update_apply.self_update_handoff.write_and_launch_handoff_script')
    @patch('web.update_apply._shut_down_old_server')
    @patch(
        'web.update_apply.self_update.download_and_verify_update',
        side_effect=self_update.HashMismatchError('bad hash'),
    )
    def test_verify_failure_never_shuts_down_or_hands_off(self, mock_download, mock_shutdown, mock_handoff, capsys):
        _run_frozen_verify_and_handoff('/fake/root', 12345, 8787)  # must not raise

        mock_shutdown.assert_not_called()
        mock_handoff.assert_not_called()
        assert 'old server left running' in capsys.readouterr().out

    @patch('web.update_apply.self_update_handoff.write_and_launch_handoff_script')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply._recommender_job_in_progress', return_value=True)
    @patch('web.update_apply.self_update.download_and_verify_update')
    def test_job_started_during_download_aborts_without_touching_old_server(
        self, mock_download, mock_job_check, mock_shutdown, mock_handoff, tmp_path, capsys,
    ):
        """Race-safe re-check: a job could start DURING the download -
        even though none was running when the worker began (that
        earlier check is _run_worker's job, tested separately)."""
        asset_path = tmp_path / 'verified-asset.tmp'
        asset_path.write_bytes(b'x')
        mock_download.return_value = self._verified(asset_path=str(asset_path))

        _run_frozen_verify_and_handoff('/fake/root', 12345, 8787)  # must not raise

        mock_shutdown.assert_not_called()
        mock_handoff.assert_not_called()
        assert not asset_path.exists()  # unused verified download cleaned up
        assert 'aborting' in capsys.readouterr().out

    @patch('web.update_apply.self_update_handoff.write_and_launch_handoff_script')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply._recommender_job_in_progress', return_value=True)
    @patch('web.update_apply.self_update.download_and_verify_update')
    def test_job_started_during_download_cleanup_failure_does_not_raise(
        self, mock_download, mock_job_check, mock_shutdown, mock_handoff, tmp_path,
    ):
        asset_path = tmp_path / 'verified-asset.tmp'
        asset_path.write_bytes(b'x')
        mock_download.return_value = self._verified(asset_path=str(asset_path))

        with patch('web.update_apply.os.remove', side_effect=OSError('locked')):
            _run_frozen_verify_and_handoff('/fake/root', 12345, 8787)  # must not raise

    @patch('web.update_apply.self_update_handoff.write_and_launch_handoff_script', side_effect=Exception('boom'))
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply._recommender_job_in_progress', return_value=False)
    @patch('web.update_apply.self_update.download_and_verify_update')
    @patch('web.update_apply.self_update.current_binary_path', return_value='/fake/root/curatarr')
    def test_handoff_failure_does_not_raise_out(
        self, mock_current_path, mock_download, mock_job_check, mock_shutdown, mock_handoff,
    ):
        mock_download.return_value = self._verified()
        _run_frozen_verify_and_handoff('/fake/root', 12345, 8787)  # must not raise


class TestRunWorkerFrozenBranch:
    """_run_worker's frozen branch: calls _run_frozen_verify_and_handoff
    and then returns - unlike the source path, it never falls through
    to _relaunch_and_verify (the external hand-off script owns the
    relaunch entirely for frozen binaries - see this module's
    docstring)."""

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply._run_frozen_verify_and_handoff')
    @patch('web.update_apply.time.sleep')
    def test_frozen_calls_verify_and_handoff_not_source_apply(
        self, mock_sleep, mock_verify_and_handoff, mock_relaunch, monkeypatch
    ):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        with patch('web.update_apply.subprocess.run') as mock_subprocess_run, \
                patch('web.update_apply._shut_down_old_server') as mock_shutdown:
            _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
            mock_subprocess_run.assert_not_called()
            # _shut_down_old_server is called FROM WITHIN
            # _run_frozen_verify_and_handoff (mocked out here), never
            # directly by _run_worker for the frozen path.
            mock_shutdown.assert_not_called()
        mock_verify_and_handoff.assert_called_once_with('/fake/root', 12345, 8787)

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply._run_frozen_verify_and_handoff')
    @patch('web.update_apply.time.sleep')
    def test_frozen_never_falls_through_to_relaunch(self, mock_sleep, mock_verify_and_handoff, mock_relaunch, monkeypatch):
        """The external hand-off script owns the relaunch entirely for
        a frozen binary - _run_worker itself must never ALSO try to
        relaunch (that would be wrong: there's no run-ui.sh/ps1 next to
        a binary install)."""
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_relaunch.assert_not_called()

    @patch('web.update_apply._relaunch_and_verify')
    @patch('web.update_apply._run_frozen_verify_and_handoff', side_effect=Exception('should never escape anyway'))
    @patch('web.update_apply.time.sleep')
    def test_unexpected_exception_in_frozen_path_does_not_raise_and_does_not_relaunch(
        self, mock_sleep, mock_verify_and_handoff, mock_relaunch, monkeypatch, capsys
    ):
        """Belt-and-suspenders on top of _run_frozen_verify_and_handoff's
        own internal error handling: even if something outside that
        somehow raised, _run_worker must still not crash - and, unlike
        the source path, must NOT fall back to _relaunch_and_verify
        (wrong for a binary install)."""
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)  # must not raise
        mock_relaunch.assert_not_called()
        assert 'UNEXPECTED ERROR in frozen apply/hand-off' in capsys.readouterr().out


class TestSpawnWorkerFrozenCommand:
    @patch('web.update_apply.subprocess.Popen')
    def test_frozen_spawns_self_update_worker_flag_not_a_script_path(self, mock_popen, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        fake_exe = str(tmp_path / 'curatarr.exe')
        monkeypatch.setattr(sys, 'executable', fake_exe, raising=False)
        logs_dir = str(tmp_path / 'logs')
        manager = UpdateManager(str(tmp_path), logs_dir)
        manager._spawn_worker('127.0.0.1', 8787)

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == fake_exe
        assert cmd[1] == '--self-update-worker'
        assert '--project-root' not in cmd
        assert '--pid' in cmd and str(os.getpid()) in cmd
        assert '--host' in cmd and '127.0.0.1' in cmd
        assert '--port' in cmd and '8787' in cmd

    @patch('web.update_apply.subprocess.Popen')
    def test_frozen_worker_env_strips_meipass2(self, mock_popen, tmp_path, monkeypatch):
        """Regression test for the real end-to-end failure this fixes -
        see utils.self_update.sanitize_frozen_relaunch_env's docstring:
        this server process is itself a frozen curatarr.exe instance
        and may carry PyInstaller's internal _MEIPASS2 in its own
        environment - the spawned worker (another independent instance)
        must never inherit it."""
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        monkeypatch.setenv('_MEIPASS2', r'C:\Temp\_MEIstale')
        manager = UpdateManager(str(tmp_path), str(tmp_path / 'logs'))
        manager._spawn_worker('127.0.0.1', 8787)
        _, kwargs = mock_popen.call_args
        assert '_MEIPASS2' not in kwargs['env']

    @patch('web.update_apply.subprocess.Popen')
    def test_frozen_worker_gets_its_own_fresh_extraction_temp_dir(self, mock_popen, tmp_path, monkeypatch):
        """Regression test for the exact real-world crash this fixes:
        the worker sharing the OLD server's PyInstaller onefile
        extraction directory, which then gets torn apart when
        _shut_down_old_server force-kills that old server moments
        later - crashing the still-running worker with a hard
        bootloader error dialog (`pyi_rth_multiprocessing` failing to
        find files under a now-gone `_MEI*` directory). See
        _fresh_worker_temp_dir's docstring and this module's own for
        the full chain and why the actual swap+relaunch was moved out
        of any frozen process entirely as the more thorough fix."""
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        manager = UpdateManager(str(tmp_path), str(tmp_path / 'logs'))
        manager._spawn_worker('127.0.0.1', 8787)
        _, kwargs = mock_popen.call_args
        assert kwargs['env']['TEMP'] == kwargs['env']['TMP']
        assert os.path.isdir(kwargs['env']['TEMP'])
        # Never the plain inherited TEMP - must be a freshly-created,
        # dedicated directory the old server never touched.
        assert kwargs['env']['TEMP'] != os.environ.get('TEMP')

    @patch('web.update_apply.subprocess.Popen')
    def test_source_worker_does_not_set_env_kwarg(self, mock_popen, tmp_path, monkeypatch):
        """Source installs never spawn another curatarr.exe instance -
        no PyInstaller-internal state to worry about, and no reason to
        override the default (full) inheritance the existing behavior
        already relied on."""
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        manager = UpdateManager(str(tmp_path), str(tmp_path / 'logs'))
        manager._spawn_worker('127.0.0.1', 8787)
        _, kwargs = mock_popen.call_args
        assert 'env' not in kwargs


class TestParseBinaryWorkerArgs:
    def test_parses_pid_host_port_without_project_root(self):
        args = _parse_binary_worker_args(['--pid', '4242', '--host', '127.0.0.1', '--port', '8787'])
        assert args.pid == 4242
        assert args.host == '127.0.0.1'
        assert args.port == 8787

    def test_missing_required_arg_exits(self):
        with pytest.raises(SystemExit):
            _parse_binary_worker_args(['--pid', '4242'])


class TestRunSelfUpdateWorker:
    """run_self_update_worker() is curatarr_app.py's `--self-update-worker`
    entry point - a thin argv-parse + get_project_root() + _run_worker()
    wrapper, pragma'd like the module's own `if __name__ ==
    '__main__':` block (see that pragma's comment) since it's exercised
    end-to-end here with _run_worker itself mocked out, same as every
    other _spawn_worker-adjacent test in this file."""

    @patch('web.update_apply._run_worker')
    def test_resolves_project_root_and_calls_run_worker(self, mock_run_worker, tmp_path):
        with patch('utils.helpers.get_project_root', return_value=str(tmp_path)):
            run_self_update_worker(['--pid', '4242', '--host', '127.0.0.1', '--port', '8787'])
        mock_run_worker.assert_called_once_with(str(tmp_path), 4242, '127.0.0.1', 8787)
