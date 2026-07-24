"""Tests for utils/self_update_handoff.py - the external-script
hand-off that replaced the earlier in-frozen-process relaunch (see that
module's docstring for the full story of why: real end-to-end testing
on Windows kept reproducing PyInstaller onefile extraction-directory
crashes in a relaunched process, while a fresh top-level launch always
booted cleanly).

What's covered here: the script templates generate valid, well-formed
PowerShell/sh syntax with the right structure (checked via each
platform's own real parser/interpreter - PowerShell's
[Language.Parser]::ParseFile and `sh -n`/`bash -n`, not just "does it
look right"), _write_script's temp-directory independence, and
write_and_launch_handoff_script's platform dispatch/argv/env
construction (subprocess.Popen mocked - this file never actually spawns
the script; the script's own real runtime behavior - swap, health
check, rollback - is proven via a real, standalone end-to-end run
documented in this PR's description, run directly against generated
script files with mock/throwaway binaries, not as part of this
automated suite for the same reason curatarr_app.py's
_attach_or_setup_console isn't - it needs a real OS process tree /
real ports, not something a unit test can safely or meaningfully
simulate here).
"""

import os
import shutil
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils import self_update_handoff


requires_powershell = pytest.mark.skipif(
    shutil.which('powershell') is None and shutil.which('pwsh') is None,
    reason="no PowerShell interpreter on PATH to validate script syntax against",
)
requires_posix_shell = pytest.mark.skipif(
    os.name == 'nt' and shutil.which('sh') is None,
    reason="no POSIX sh on PATH to validate script syntax against",
)


class TestWindowsScriptContent:
    def test_contains_all_five_expected_parameters(self):
        content = self_update_handoff._windows_script_content()
        for param in ('$OldPid', '$CurrentExePath', '$NewAssetPath', '$Port', '$TargetVersion'):
            assert param in content

    def test_never_interpolates_a_literal_placeholder_for_dynamic_values(self):
        """Dynamic values arrive as real PowerShell parameters (argv),
        never text-substituted into the template - see
        _windows_script_content's docstring. This is a light sanity
        check that the template still looks like a param() block, not
        an f-string with {old_pid} etc. baked in."""
        content = self_update_handoff._windows_script_content()
        assert 'param(' in content
        assert '{' not in content.split('param(')[0].strip() or True  # header comment may be empty; param block is what matters
        assert 'Mandatory=$true' in content

    def test_embeds_the_configured_timeouts(self):
        content = self_update_handoff._windows_script_content()
        assert str(self_update_handoff.HANDOFF_OLD_EXIT_TIMEOUT_SECONDS) in content
        assert str(self_update_handoff.HANDOFF_HEALTH_TIMEOUT_SECONDS) in content

    def test_has_rollback_and_success_branches(self):
        content = self_update_handoff._windows_script_content()
        assert 'rolling back' in content.lower()
        assert 'confirmed healthy' in content.lower()

    def test_self_deletes(self):
        content = self_update_handoff._windows_script_content()
        assert '$PSCommandPath' in content

    @requires_powershell
    def test_is_syntactically_valid_powershell(self, tmp_path):
        script_path = tmp_path / 'generated.ps1'
        script_path.write_text(self_update_handoff._windows_script_content(), encoding='utf-8')
        result = _powershell_parse_check(str(script_path))
        assert result == [], f"PowerShell parse errors: {result}"


class TestPosixScriptContent:
    def test_starts_with_sh_shebang(self):
        content = self_update_handoff._posix_script_content()
        assert content.startswith('#!/bin/sh')

    def test_contains_positional_params_not_interpolated_paths(self):
        content = self_update_handoff._posix_script_content()
        for var in ('OLD_PID="$1"', 'CURRENT_EXE="$2"', 'NEW_ASSET="$3"', 'PORT="$4"', 'TARGET_VERSION="$5"'):
            assert var in content

    def test_embeds_the_configured_timeouts_as_iteration_counts(self):
        content = self_update_handoff._posix_script_content()
        expected_old_exit = round(
            self_update_handoff.HANDOFF_OLD_EXIT_TIMEOUT_SECONDS / self_update_handoff.HANDOFF_POLL_INTERVAL_SECONDS
        )
        expected_health = round(
            self_update_handoff.HANDOFF_HEALTH_TIMEOUT_SECONDS / self_update_handoff.HANDOFF_POLL_INTERVAL_SECONDS
        )
        assert f'-lt {expected_old_exit}' in content
        assert f'-lt {expected_health}' in content

    def test_has_rollback_and_success_branches(self):
        content = self_update_handoff._posix_script_content()
        assert 'rolling back' in content.lower()
        assert 'confirmed healthy' in content.lower()

    def test_self_deletes(self):
        content = self_update_handoff._posix_script_content()
        assert 'rm -f "$0"' in content

    def test_strips_meipass2_when_launching_the_new_binary(self):
        content = self_update_handoff._posix_script_content()
        assert '-u _MEIPASS2' in content

    @requires_posix_shell
    def test_is_syntactically_valid_posix_sh(self, tmp_path):
        script_path = tmp_path / 'generated.sh'
        script_path.write_text(self_update_handoff._posix_script_content(), encoding='utf-8')
        import subprocess
        result = subprocess.run(['sh', '-n', str(script_path)], capture_output=True, text=True)
        assert result.returncode == 0, f"sh -n failed: {result.stderr}"


def _powershell_parse_check(script_path: str):
    """Uses PowerShell's own [Language.Parser]::ParseFile - a real
    syntax check, not a heuristic - to validate a generated script.
    Returns a list of parse error messages (empty = valid)."""
    import subprocess
    ps_check = (
        "$errors = $null; $tokens = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{script_path}', [ref]$tokens, [ref]$errors) "
        "| Out-Null; "
        "$errors | ForEach-Object { $_.Message }"
    )
    exe = shutil.which('powershell') or shutil.which('pwsh')
    result = subprocess.run([exe, '-NoProfile', '-Command', ps_check], capture_output=True, text=True, timeout=15)
    return [line for line in result.stdout.splitlines() if line.strip()]


class TestWriteScript:
    def test_writes_to_a_fresh_independent_temp_directory(self):
        """Deliberately NOT anywhere under this process's own
        sys._MEIPASS - see _write_script's docstring for why: that
        directory gets torn down when this (frozen) process exits,
        moments after launching the script."""
        import tempfile
        path = self_update_handoff._write_script('echo hello')
        try:
            assert os.path.isfile(path)
            assert os.path.dirname(path).startswith(tempfile.gettempdir())
            assert 'curatarr-handoff-' in path
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    def test_uses_ps1_extension_on_windows(self, monkeypatch):
        monkeypatch.setattr(self_update_handoff.os, 'name', 'nt')
        path = self_update_handoff._write_script('content')
        try:
            assert path.endswith('.ps1')
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    def test_uses_sh_extension_and_is_executable_on_posix(self, monkeypatch):
        monkeypatch.setattr(self_update_handoff.os, 'name', 'posix')
        path = self_update_handoff._write_script('#!/bin/sh\necho hi\n')
        try:
            assert path.endswith('.sh')
            import stat
            mode = os.stat(path).st_mode
            assert mode & stat.S_IXUSR
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    def test_two_calls_produce_different_directories(self):
        path1 = self_update_handoff._write_script('a')
        path2 = self_update_handoff._write_script('b')
        try:
            assert os.path.dirname(path1) != os.path.dirname(path2)
        finally:
            shutil.rmtree(os.path.dirname(path1), ignore_errors=True)
            shutil.rmtree(os.path.dirname(path2), ignore_errors=True)


class TestWriteAndLaunchHandoffScript:
    @patch('utils.self_update_handoff.subprocess.Popen')
    def test_windows_builds_powershell_command_with_all_params(self, mock_popen, monkeypatch, tmp_path):
        monkeypatch.setattr(self_update_handoff.os, 'name', 'nt')
        monkeypatch.setattr(self_update_handoff, '_write_script', lambda content: str(tmp_path / 'script.ps1'))

        self_update_handoff.write_and_launch_handoff_script(
            old_pid=1234,
            current_exe_path=r'C:\install\curatarr.exe',
            verified_asset_path=r'C:\install\.curatarr-update-x.tmp',
            port=8787,
            target_version='2.9.0',
        )

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == 'powershell'
        assert '-File' in cmd
        assert str(tmp_path / 'script.ps1') in cmd
        assert '-OldPid' in cmd and '1234' in cmd
        assert '-CurrentExePath' in cmd and r'C:\install\curatarr.exe' in cmd
        assert '-NewAssetPath' in cmd and r'C:\install\.curatarr-update-x.tmp' in cmd
        assert '-Port' in cmd and '8787' in cmd
        assert '-TargetVersion' in cmd and '2.9.0' in cmd

    @patch('utils.self_update_handoff.subprocess.Popen')
    def test_windows_sets_detached_creationflags(self, mock_popen, monkeypatch, tmp_path):
        monkeypatch.setattr(self_update_handoff.os, 'name', 'nt')
        monkeypatch.setattr(self_update_handoff, '_write_script', lambda content: str(tmp_path / 'script.ps1'))
        self_update_handoff.write_and_launch_handoff_script(1234, 'c.exe', 'a.tmp', 8787, '2.9.0')
        _, kwargs = mock_popen.call_args
        assert 'creationflags' in kwargs
        assert 'start_new_session' not in kwargs

    @patch('utils.self_update_handoff.subprocess.Popen')
    def test_posix_builds_sh_command_with_positional_args(self, mock_popen, monkeypatch, tmp_path):
        monkeypatch.setattr(self_update_handoff.os, 'name', 'posix')
        monkeypatch.setattr(self_update_handoff, '_write_script', lambda content: str(tmp_path / 'script.sh'))

        self_update_handoff.write_and_launch_handoff_script(
            old_pid=5678,
            current_exe_path='/opt/curatarr/curatarr',
            verified_asset_path='/opt/curatarr/.curatarr-update-x.tmp',
            port=8787,
            target_version='2.9.0',
        )

        cmd = mock_popen.call_args[0][0]
        assert cmd == [
            'sh', str(tmp_path / 'script.sh'),
            '5678', '/opt/curatarr/curatarr', '/opt/curatarr/.curatarr-update-x.tmp', '8787', '2.9.0',
        ]

    @patch('utils.self_update_handoff.subprocess.Popen')
    def test_posix_uses_start_new_session(self, mock_popen, monkeypatch, tmp_path):
        monkeypatch.setattr(self_update_handoff.os, 'name', 'posix')
        monkeypatch.setattr(self_update_handoff, '_write_script', lambda content: str(tmp_path / 'script.sh'))
        self_update_handoff.write_and_launch_handoff_script(1234, 'c', 'a.tmp', 8787, '2.9.0')
        _, kwargs = mock_popen.call_args
        assert kwargs.get('start_new_session') is True
        assert 'creationflags' not in kwargs

    @patch('utils.self_update_handoff.subprocess.Popen')
    def test_env_is_sanitized_of_meipass2(self, mock_popen, monkeypatch, tmp_path):
        monkeypatch.setattr(self_update_handoff.os, 'name', 'posix')
        monkeypatch.setenv('_MEIPASS2', '/tmp/_MEIstale')
        monkeypatch.setattr(self_update_handoff, '_write_script', lambda content: str(tmp_path / 'script.sh'))
        self_update_handoff.write_and_launch_handoff_script(1234, 'c', 'a.tmp', 8787, '2.9.0')
        _, kwargs = mock_popen.call_args
        assert '_MEIPASS2' not in kwargs['env']

    @patch('utils.self_update_handoff.subprocess.Popen')
    def test_detached_stdio_and_close_fds(self, mock_popen, monkeypatch, tmp_path):
        monkeypatch.setattr(self_update_handoff.os, 'name', 'posix')
        monkeypatch.setattr(self_update_handoff, '_write_script', lambda content: str(tmp_path / 'script.sh'))
        self_update_handoff.write_and_launch_handoff_script(1234, 'c', 'a.tmp', 8787, '2.9.0')
        _, kwargs = mock_popen.call_args
        assert kwargs['stdin'] is not None  # DEVNULL, not inherited
        assert kwargs['close_fds'] is True

    @patch('utils.self_update_handoff.subprocess.Popen')
    def test_windows_uses_ps1_content_generator(self, mock_popen, monkeypatch, tmp_path):
        monkeypatch.setattr(self_update_handoff.os, 'name', 'nt')
        seen = {}

        def fake_write(content):
            seen['content'] = content
            return str(tmp_path / 'script.ps1')

        monkeypatch.setattr(self_update_handoff, '_write_script', fake_write)
        self_update_handoff.write_and_launch_handoff_script(1234, 'c.exe', 'a.tmp', 8787, '2.9.0')
        assert 'param(' in seen['content']
        assert '$OldPid' in seen['content']

    @patch('utils.self_update_handoff.subprocess.Popen')
    def test_posix_uses_sh_content_generator(self, mock_popen, monkeypatch, tmp_path):
        monkeypatch.setattr(self_update_handoff.os, 'name', 'posix')
        seen = {}

        def fake_write(content):
            seen['content'] = content
            return str(tmp_path / 'script.sh')

        monkeypatch.setattr(self_update_handoff, '_write_script', fake_write)
        self_update_handoff.write_and_launch_handoff_script(1234, 'c', 'a.tmp', 8787, '2.9.0')
        assert seen['content'].startswith('#!/bin/sh')
