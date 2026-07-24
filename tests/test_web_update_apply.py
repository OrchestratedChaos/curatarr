"""Tests for the web UI's "Update now" flow: web/update_apply.py
(UpdateManager + the detached worker's decision logic) and the
/update/apply, /healthz routes in web/app.py.

What's covered: source-only gating, the precondition-check gate (no
verified release -> no restart, error returned), single-run locking/
concurrency, /healthz, and the worker's own decision logic (always
relaunches regardless of apply outcome - never a dead port) with every
subprocess/signal/sleep call mocked out.

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

from web.app import create_app
from web.update_apply import (
    OLD_SERVER_SHUTDOWN_TIMEOUT_SECONDS,
    RESPONSE_FLUSH_DELAY_SECONDS,
    UpdateAlreadyInProgressError,
    UpdateManager,
    UpdateNotAvailableError,
    UpdateNotSupportedError,
    _parse_worker_args,
    _pid_alive,
    _relaunch_ui,
    _run_worker,
    _shut_down_old_server,
    check_verified_update,
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


class TestSourceOnlyGating:
    def test_frozen_binary_rejected_with_400(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        resp = c.post('/update/apply')
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_frozen_binary_never_reaches_precondition_check(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        with patch('web.update_apply.check_verified_update') as mock_check:
            c.post('/update/apply')
            mock_check.assert_not_called()

    def test_frozen_banner_has_no_update_now_button(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        config_path = os.path.join(root, 'config', 'config.yml')
        with open(config_path, 'a', encoding='utf-8') as f:
            f.write('general:\n  update_mode: notify\n')
        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/')
        assert b'update-now-btn' not in resp.data
        assert b'Download v2.9.0' in resp.data

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

    def test_frozen_raises_not_supported(self, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        manager = UpdateManager('/fake/root', '/fake/root/logs')
        with pytest.raises(UpdateNotSupportedError):
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
    @patch('web.update_apply.subprocess.Popen')
    def test_spawns_run_ui_with_expected_env(self, mock_popen):
        _relaunch_ui('/fake/root', 9999)
        assert mock_popen.call_count == 1
        args, kwargs = mock_popen.call_args
        assert kwargs['env']['CURATARR_UI_PORT'] == '9999'
        assert kwargs['env']['CURATARR_SKIP_BROWSER_OPEN'] == '1'
        assert kwargs['cwd'] == '/fake/root'


class TestRunWorkerAlwaysRelaunches:
    """The core "never leave a dead port" guarantee: regardless of
    whether the apply step reports success, no update available, or an
    outright failure, the worker must always relaunch the UI."""

    @patch('web.update_apply._relaunch_ui')
    @patch('web.update_apply.subprocess.run')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_relaunches_after_successful_apply(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch):
        mock_run.return_value = Mock(returncode=0, stdout='UPDATED:v2.9.0\n', stderr='')
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_shutdown.assert_called_once()
        mock_relaunch.assert_called_once_with('/fake/root', 8787)

    @patch('web.update_apply._relaunch_ui')
    @patch('web.update_apply.subprocess.run')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_relaunches_when_apply_finds_nothing(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch):
        mock_run.return_value = Mock(returncode=1, stdout='NO_UPDATE\n', stderr='')
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_relaunch.assert_called_once_with('/fake/root', 8787)

    @patch('web.update_apply._relaunch_ui')
    @patch('web.update_apply.subprocess.run')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_relaunches_when_apply_fails(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch):
        mock_run.return_value = Mock(returncode=1, stdout='FAILED:git checkout failed\n', stderr='')
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_relaunch.assert_called_once_with('/fake/root', 8787)

    @patch('web.update_apply._relaunch_ui')
    @patch('web.update_apply.subprocess.run', side_effect=Exception('subprocess plumbing exploded'))
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_relaunches_even_if_apply_step_raises(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch):
        """Belt-and-suspenders: even an unexpected exception from the
        apply subprocess call itself must not skip the relaunch."""
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        mock_relaunch.assert_called_once_with('/fake/root', 8787)

    @patch('web.update_apply._relaunch_ui', side_effect=Exception('could not spawn'))
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

    @patch('web.update_apply._relaunch_ui')
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

    def test_shut_down_old_server_uses_taskkill(self, monkeypatch):
        monkeypatch.setattr('web.update_apply.os.name', 'nt')
        with patch('web.update_apply._pid_alive', side_effect=[True, False]), \
                patch('web.update_apply.subprocess.run') as mock_run, \
                patch('web.update_apply.time.sleep'):
            _shut_down_old_server(1234, timeout=5)
        assert mock_run.call_args[0][0][0] == 'taskkill'

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

    @patch('web.update_apply._relaunch_ui')
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
    @patch('web.update_apply._relaunch_ui')
    @patch('web.update_apply.subprocess.run')
    @patch('web.update_apply._shut_down_old_server')
    @patch('web.update_apply.time.sleep')
    def test_apply_stderr_is_logged(self, mock_sleep, mock_shutdown, mock_run, mock_relaunch, capsys):
        mock_run.return_value = Mock(returncode=1, stdout='FAILED:git checkout failed\n', stderr='fatal: something')
        _run_worker('/fake/root', 12345, '127.0.0.1', 8787)
        out = capsys.readouterr().out
        assert 'fatal: something' in out
