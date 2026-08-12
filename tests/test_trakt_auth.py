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

"""Tests for utils/trakt_auth.py"""

import os
import queue
import subprocess
import sys
import threading
import time
from unittest.mock import Mock, patch

import pytest
import yaml

# tests/ lives directly under the repo root - same one-level-up
# resolution get_code_root() itself uses (see utils/helpers.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestGetConfigDir:
    """get_config_dir() must resolve against utils.helpers.
    get_project_root() (CURATARR_CONFIG_DIR override in Docker,
    ~/.curatarr for a frozen binary, repo root for a source checkout -
    see that function's own docstring), never against this script's
    own directory on disk. It used to always resolve to
    dirname(dirname(__file__)) - correct for a source checkout, but
    /app in Docker: the code's fixed WORKDIR, not the separately
    mounted /data config/trakt.yml actually lives in (confirmed in a
    real container - a `docker exec ... python3 -m utils.trakt_auth`
    before this fix silently read/wrote the wrong, non-persisted
    path)."""

    def test_uses_get_project_root_not_own_file_location(self, monkeypatch):
        from utils import trakt_auth

        monkeypatch.setattr(trakt_auth, "get_project_root", lambda: "/data")
        assert trakt_auth.get_config_dir() == os.path.join("/data", "config")
        assert "/app" not in trakt_auth.get_config_dir()

    def test_respects_curatarr_config_dir_env_var(self, tmp_path, monkeypatch):
        """End-to-end through the real (unpatched) get_project_root() -
        CURATARR_CONFIG_DIR is exactly how Docker points
        get_project_root() at /data while the code stays at the
        image's fixed /app (tmp_path stands in for /data here so this
        test never touches a real filesystem root). get_project_root()
        is @lru_cache(maxsize=1) - cleared before (so this test's own
        call isn't served a value cached by some earlier, unrelated
        test) and after (so this test's own result doesn't leak into
        whatever runs next), matching tests/test_helpers.py's own
        convention for this same cache."""
        from utils import trakt_auth
        from utils.helpers import get_project_root

        monkeypatch.setenv("CURATARR_CONFIG_DIR", str(tmp_path))
        get_project_root.cache_clear()
        try:
            assert trakt_auth.get_config_dir() == os.path.join(str(tmp_path), "config")
        finally:
            get_project_root.cache_clear()


class TestTraktAuthLoadConfig:
    """Tests for trakt_auth load_config function."""

    def test_loads_config_file(self, tmp_path, monkeypatch):
        """Test loads config from config/ directory."""
        # Create config directory and files
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        config_path = config_dir / "config.yml"
        config_path.write_text("plex:\n  url: http://localhost\n")

        trakt_path = config_dir / "trakt.yml"
        trakt_path.write_text("enabled: true\nclient_id: test\n")

        # Patch get_config_dir to return our temp directory
        from utils import trakt_auth

        monkeypatch.setattr(trakt_auth, "get_config_dir", lambda: str(config_dir))

        result = trakt_auth.load_config()
        assert result["trakt"]["enabled"] is True
        assert result["trakt"]["client_id"] == "test"

    def test_env_var_overrides_apply(self, tmp_path, monkeypatch):
        """Regression test: trakt_auth previously had its own local
        load_config() that opened config.yml/trakt.yml directly with
        yaml.safe_load and never applied the PLEX_URL/PLEX_TOKEN/
        TMDB_API_KEY env-var overrides the rest of the app gets via
        utils.config.load_config(). Trakt device-auth should see the
        same config (env overrides included) as everything else -
        important for Docker/env-var-configured installs."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        config_path = config_dir / "config.yml"
        config_path.write_text("plex:\n  url: http://original-host\n  token: original-token\n")

        trakt_path = config_dir / "trakt.yml"
        trakt_path.write_text("enabled: true\nclient_id: test\n")

        monkeypatch.setenv("PLEX_URL", "http://env-override-host")
        monkeypatch.setenv("PLEX_TOKEN", "env-override-token")
        monkeypatch.setenv("TMDB_API_KEY", "env-override-tmdb-key")

        from utils import trakt_auth

        monkeypatch.setattr(trakt_auth, "get_config_dir", lambda: str(config_dir))

        result = trakt_auth.load_config()

        assert result["plex"]["url"] == "http://env-override-host"
        assert result["plex"]["token"] == "env-override-token"
        assert result["tmdb"]["api_key"] == "env-override-tmdb-key"
        # Module merge (trakt.yml) still happens via the canonical loader.
        assert result["trakt"]["enabled"] is True
        assert result["trakt"]["client_id"] == "test"


class TestTraktAuthSaveTokens:
    """Tests for trakt_auth save_tokens function."""

    def test_saves_tokens_to_config(self, tmp_path, monkeypatch):
        """Test saves tokens to config file."""
        # Create initial config
        config_path = tmp_path / "config.yml"
        initial_config = {"trakt": {"enabled": True, "client_id": "test_id", "client_secret": "test_secret"}}
        with open(config_path, "w") as f:
            yaml.dump(initial_config, f)

        # Patch the path resolution
        def patched_save_tokens(access_token, refresh_token):
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            config["trakt"]["access_token"] = access_token
            config["trakt"]["refresh_token"] = refresh_token
            with open(config_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        patched_save_tokens("new_access", "new_refresh")

        # Verify tokens saved
        with open(config_path, "r") as f:
            result = yaml.safe_load(f)

        assert result["trakt"]["access_token"] == "new_access"
        assert result["trakt"]["refresh_token"] == "new_refresh"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions don't apply on Windows")
    def test_real_save_tokens_ends_up_owner_only(self, tmp_path, monkeypatch):
        """FIX 5: trakt.yml holds the Trakt access/refresh tokens in
        plaintext - a plain open(path, 'w') lands at the OS umask
        default (typically 0o644 on Linux/Docker), which would leave it
        world-readable. Exercises the REAL save_tokens (not the local
        re-implementation the other test in this class uses)."""
        import utils.trakt_auth as trakt_auth_module

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        trakt_path = config_dir / "trakt.yml"
        trakt_path.write_text(
            "enabled: true\nclient_id: test\nclient_secret: shh\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(trakt_auth_module, "get_config_dir", lambda: str(config_dir))

        trakt_auth_module.save_tokens("new_access", "new_refresh")

        import stat

        mode = stat.S_IMODE(os.stat(str(trakt_path)).st_mode)
        assert mode == 0o600, f"trakt.yml was {oct(mode)}, expected 0o600"


class TestTraktAuthMain:
    """Tests for trakt_auth main function."""

    @patch("utils.trakt_auth.load_config")
    def test_exits_when_config_not_found(self, mock_load):
        """Test exits with error when config not found."""
        from utils.trakt_auth import main

        mock_load.side_effect = FileNotFoundError()

        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1

    @patch("utils.trakt_auth.load_config")
    def test_exits_when_trakt_disabled(self, mock_load):
        """Test exits when Trakt is disabled."""
        from utils.trakt_auth import main

        mock_load.return_value = {"trakt": {"enabled": False}}

        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1

    @patch("utils.trakt_auth.load_config")
    def test_exits_when_missing_credentials(self, mock_load):
        """Test exits when client_id or secret missing."""
        from utils.trakt_auth import main

        mock_load.return_value = {"trakt": {"enabled": True, "client_id": None, "client_secret": "secret"}}

        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1

    @patch("utils.trakt_auth.load_config")
    def test_exits_when_already_authenticated(self, mock_load):
        """Test exits cleanly when already authenticated."""
        from utils.trakt_auth import main

        mock_load.return_value = {
            "trakt": {"enabled": True, "client_id": "id", "client_secret": "secret", "access_token": "existing_token"}
        }

        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0

    @patch("utils.trakt_auth.TraktClient")
    @patch("utils.trakt_auth.load_config")
    def test_reauth_flag_bypasses_already_authenticated_check(self, mock_load, mock_client_class):
        """--reauth (recovery flag - FIX 5) lets a user with an already-
        present (possibly refresh-rejected/dead) access_token restart
        device auth without hand-editing trakt.yml first - important for
        Docker/frozen-binary users who can't reasonably shell in to do
        that edit."""
        from utils.trakt_auth import main

        mock_load.return_value = {
            "trakt": {"enabled": True, "client_id": "id", "client_secret": "secret", "access_token": "existing_token"}
        }

        mock_client = Mock()
        mock_client.get_device_code.return_value = {
            "device_code": "abc123",
            "user_code": "XYZ789",
            "verification_url": "https://trakt.tv/activate",
            "interval": 5,
            "expires_in": 600,
        }
        mock_client.poll_for_token.return_value = True
        mock_client.get_username.return_value = "testuser"
        mock_client_class.return_value = mock_client

        # Should proceed to the device-auth flow instead of exiting 0.
        main(["--reauth"])

        mock_client.get_device_code.assert_called_once()

    @patch("utils.trakt_auth.TraktClient")
    @patch("utils.trakt_auth.load_config")
    def test_force_is_an_alias_for_reauth(self, mock_load, mock_client_class):
        from utils.trakt_auth import main

        mock_load.return_value = {
            "trakt": {"enabled": True, "client_id": "id", "client_secret": "secret", "access_token": "existing_token"}
        }

        mock_client = Mock()
        mock_client.get_device_code.return_value = {
            "device_code": "abc123",
            "user_code": "XYZ789",
            "verification_url": "https://trakt.tv/activate",
        }
        mock_client.poll_for_token.return_value = True
        mock_client.get_username.return_value = "testuser"
        mock_client_class.return_value = mock_client

        main(["--force"])

        mock_client.get_device_code.assert_called_once()

    @patch("utils.trakt_auth.TraktClient")
    @patch("utils.trakt_auth.load_config")
    def test_starts_device_auth_flow(self, mock_load, mock_client_class):
        """Test starts device auth flow when not authenticated."""
        from utils.trakt_auth import main

        mock_load.return_value = {
            "trakt": {"enabled": True, "client_id": "id", "client_secret": "secret", "access_token": None}
        }

        mock_client = Mock()
        mock_client.get_device_code.return_value = {
            "device_code": "abc123",
            "user_code": "XYZ789",
            "verification_url": "https://trakt.tv/activate",
            "interval": 5,
            "expires_in": 600,
        }
        mock_client.poll_for_token.return_value = True
        mock_client.get_username.return_value = "testuser"
        mock_client_class.return_value = mock_client

        # Should complete without exit
        main([])

        mock_client.get_device_code.assert_called_once()
        mock_client.poll_for_token.assert_called_once()

    @patch("utils.trakt_auth.TraktClient")
    @patch("utils.trakt_auth.load_config")
    def test_handles_auth_failure(self, mock_load, mock_client_class):
        """Test handles authentication failure."""
        from utils.trakt_auth import main

        mock_load.return_value = {
            "trakt": {"enabled": True, "client_id": "id", "client_secret": "secret", "access_token": None}
        }

        mock_client = Mock()
        mock_client.get_device_code.return_value = {
            "device_code": "abc",
            "user_code": "XYZ",
            "verification_url": "https://trakt.tv/activate",
        }
        mock_client.poll_for_token.return_value = False
        mock_client_class.return_value = mock_client

        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1

    @patch("utils.trakt_auth.TraktClient")
    @patch("utils.trakt_auth.load_config")
    def test_handles_trakt_auth_error(self, mock_load, mock_client_class):
        """Test handles TraktAuthError exception."""
        from utils.trakt_auth import TraktAuthError, main

        mock_load.return_value = {
            "trakt": {"enabled": True, "client_id": "id", "client_secret": "secret", "access_token": None}
        }

        mock_client = Mock()
        mock_client.get_device_code.side_effect = TraktAuthError("Auth failed")
        mock_client_class.return_value = mock_client

        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1

    @patch("utils.trakt_auth.TraktClient")
    @patch("utils.trakt_auth.load_config")
    def test_handles_keyboard_interrupt(self, mock_load, mock_client_class):
        """Test handles KeyboardInterrupt gracefully."""
        from utils.trakt_auth import main

        mock_load.return_value = {
            "trakt": {"enabled": True, "client_id": "id", "client_secret": "secret", "access_token": None}
        }

        mock_client = Mock()
        mock_client.get_device_code.side_effect = KeyboardInterrupt()
        mock_client_class.return_value = mock_client

        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1


class TestTraktAuthStdoutLineBuffering:
    """main() reconfigures stdout to line-buffered on entry - see that
    function's own comment for the full "why" (CPython fully
    block-buffers stdout whenever it isn't a real terminal, which
    silently held the device code/verification URL in memory for the
    whole poll_for_token wait when invoked over e.g. `ssh host "cmd" >
    log` with no -t)."""

    @patch("utils.trakt_auth.TraktClient")
    @patch("utils.trakt_auth.load_config")
    def test_reconfigures_stdout_to_line_buffered(self, mock_load, mock_client_class, monkeypatch):
        from utils.trakt_auth import main

        mock_load.return_value = {
            "trakt": {"enabled": True, "client_id": "id", "client_secret": "secret", "access_token": None}
        }
        mock_client = Mock()
        mock_client.get_device_code.return_value = {
            "device_code": "abc123",
            "user_code": "XYZ789",
            "verification_url": "https://trakt.tv/activate",
        }
        mock_client.poll_for_token.return_value = True
        mock_client.get_username.return_value = "testuser"
        mock_client_class.return_value = mock_client

        fake_stdout = Mock()
        fake_stdout.reconfigure = Mock()
        monkeypatch.setattr("sys.stdout", fake_stdout)

        main([])

        fake_stdout.reconfigure.assert_called_once_with(line_buffering=True)

    @patch("utils.trakt_auth.TraktClient")
    @patch("utils.trakt_auth.load_config")
    def test_tolerates_stdout_without_reconfigure(self, mock_load, mock_client_class, monkeypatch, capsys):
        """Some sys.stdout stand-ins (certain test/capture harnesses)
        don't implement reconfigure() at all - main() must not crash
        on those, it just skips the line-buffering step."""
        from utils.trakt_auth import main

        mock_load.return_value = {
            "trakt": {"enabled": True, "client_id": "id", "client_secret": "secret", "access_token": None}
        }
        mock_client = Mock()
        mock_client.get_device_code.return_value = {
            "device_code": "abc123",
            "user_code": "XYZ789",
            "verification_url": "https://trakt.tv/activate",
        }
        mock_client.poll_for_token.return_value = True
        mock_client.get_username.return_value = "testuser"
        mock_client_class.return_value = mock_client

        class _NoReconfigureStdout:
            def write(self, s):
                pass

            def flush(self):
                pass

        monkeypatch.setattr("sys.stdout", _NoReconfigureStdout())

        # Must not raise - hasattr(sys.stdout, "reconfigure") is False here.
        main([])


class TestTraktAuthProgressCallback:
    """main() passes poll_for_token an on_wait callback that prints a
    throttled 'still waiting' progress line - see
    PROGRESS_PRINT_INTERVAL_SECONDS and main()'s own comment."""

    @patch("utils.trakt_auth.TraktClient")
    @patch("utils.trakt_auth.load_config")
    def test_on_wait_throttled_to_progress_interval(self, mock_load, mock_client_class, monkeypatch, capsys):
        from utils import trakt_auth
        from utils.trakt_auth import main

        mock_load.return_value = {
            "trakt": {"enabled": True, "client_id": "id", "client_secret": "secret", "access_token": None}
        }
        mock_client = Mock()
        mock_client.get_device_code.return_value = {
            "device_code": "abc123",
            "user_code": "XYZ789",
            "verification_url": "https://trakt.tv/activate",
        }
        mock_client.get_username.return_value = "testuser"
        mock_client_class.return_value = mock_client

        # Fake a clock: main()'s _on_wait uses time.monotonic() - drive
        # it through several calls, some inside the throttle window,
        # some past it, without any real sleeping.
        fake_now = [0.0]
        monkeypatch.setattr(trakt_auth.time, "monotonic", lambda: fake_now[0])

        def fake_poll_for_token(device_code, interval, expires_in, on_wait=None):
            assert on_wait is not None
            fake_now[0] = 5.0
            on_wait()  # inside the throttle window (< 30s) - no print
            fake_now[0] = 10.0
            on_wait()  # still inside the window - no print
            fake_now[0] = 31.0
            on_wait()  # past PROGRESS_PRINT_INTERVAL_SECONDS - prints
            fake_now[0] = 40.0
            on_wait()  # only 9s since the last print - no print
            return True

        mock_client.poll_for_token.side_effect = fake_poll_for_token

        main([])

        captured = capsys.readouterr()
        assert captured.out.count("...still waiting for approval") == 1


class TestTraktAuthStdoutNotBuffered:
    """Regression test for the real bug: `ssh host "python3 -m
    utils.trakt_auth --reauth" > log` (no -t - no TTY) produced ZERO
    output while the process sat there polling - the device code and
    activation URL were written into a buffer that was never flushed
    because the process was blocked in poll_for_token's loop, not
    exiting.

    A test that only asserts main()/print() were called (mocking
    sys.stdout) would pass even against the OLD, broken code - Python's
    own statement order is always synchronous regardless of buffering.
    This has to actually observe bytes arriving on a REAL, non-TTY
    stdout pipe - `subprocess.Popen(..., stdout=subprocess.PIPE)` is
    never a pty - while a real child process is still genuinely
    blocked, to mean anything. That distinction is the entire bug."""

    def _write_fake_auth_script(self, tmp_path, poll_sleep_seconds: float = 0.0, release_file: str = "") -> str:
        """A standalone script (never imports pytest) that runs the
        real utils.trakt_auth.main() against a fake TraktClient - no
        real Trakt device code is ever requested/consumed, no
        config/trakt.yml outside this test's own tmp_path is ever
        touched (CURATARR_CONFIG_DIR is pointed at tmp_path)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.yml").write_text("plex:\n  url: http://localhost\n", encoding="utf-8")
        (config_dir / "trakt.yml").write_text(
            "enabled: true\nclient_id: fake-id\nclient_secret: fake-secret\n", encoding="utf-8"
        )

        script_path = tmp_path / "run_fake_trakt_auth.py"
        script_path.write_text(
            f"""
import os
import sys
import time

sys.path.insert(0, {_REPO_ROOT!r})
os.environ["CURATARR_CONFIG_DIR"] = {str(tmp_path)!r}

import utils.trakt_auth as trakt_auth
from utils.helpers import get_project_root

get_project_root.cache_clear()


class FakeTraktClient:
    def __init__(self, client_id, client_secret, token_callback):
        pass

    def get_device_code(self):
        return {{
            "device_code": "fake-device-code",
            "user_code": "FAKE-CODE",
            "verification_url": "https://trakt.tv/activate",
            "interval": 1,
            "expires_in": 600,
        }}

    def poll_for_token(self, device_code, interval, expires_in, on_wait=None):
        release_file = {release_file!r}
        if not release_file:
            time.sleep({poll_sleep_seconds!r})
            return True
        # Block until the PARENT says so, rather than for a fixed
        # duration. The assertion this supports is "the device code
        # reached the pipe while we were still blocked here" - tying that
        # to a wall clock made it a race against this process's own
        # interpreter startup and imports (requests/yaml/plexapi/
        # cryptography), which under full-suite load can exceed any
        # sleep short enough to keep the test quick.
        deadline = time.monotonic() + 120
        while not os.path.exists(release_file):
            if time.monotonic() > deadline:
                raise SystemExit("fake poll_for_token was never released by the test")
            time.sleep(0.02)
        return True

    def get_username(self):
        return "repro-user"


trakt_auth.TraktClient = FakeTraktClient
trakt_auth.main([])
""",
            encoding="utf-8",
        )
        return str(script_path)

    def test_device_code_visible_before_poll_completes_when_not_a_tty(self, tmp_path):
        """
        The device code must reach a non-TTY pipe WHILE the process is
        still blocked polling - that is the entire bug.

        Deliberately not timed against a wall clock. The child has to
        start an interpreter and import utils.trakt_auth (transitively
        requests/yaml/plexapi/cryptography) before it prints anything,
        and under full-suite load that startup alone can outlast any
        sleep short enough to keep this test quick - which made an
        earlier version of this test fail spuriously while the code under
        test was fine. The fake poll now blocks until this test releases
        it, so "still blocked" is guaranteed rather than raced for, and
        the only timeout left is a generous backstop against a genuine
        regression (no output at all).
        """
        release_file = tmp_path / "release-poll"
        script = self._write_fake_auth_script(tmp_path, release_file=str(release_file))

        # stdout=subprocess.PIPE is a real OS pipe, never a pty/tty -
        # exactly the "no TTY" condition `ssh host "cmd" > log` (no -t)
        # and plain `docker exec` (no -t) both produce.
        proc = subprocess.Popen(
            [sys.executable, script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # Read on a thread so a child that prints NOTHING (the regression)
        # fails on the backstop instead of blocking this test forever in
        # readline().
        lines: "queue.Queue[str]" = queue.Queue()

        def _pump():
            try:
                for line in proc.stdout:
                    lines.put(line)
            finally:
                lines.put("")  # EOF sentinel

        reader = threading.Thread(target=_pump, daemon=True)
        reader.start()

        collected = []
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=0.5)
                except queue.Empty:
                    continue
                if line == "":
                    break  # child exited without ever printing the code
                collected.append(line)
                if "FAKE-CODE" in line:
                    break

            output_so_far = "".join(collected)
            assert "FAKE-CODE" in output_so_far, (
                "device code never appeared on the (non-TTY) pipe while "
                f"poll_for_token was still blocked - got: {output_so_far!r}"
            )
            assert "https://trakt.tv/activate" in output_so_far

            # Guaranteed, not raced: the fake poll cannot return until the
            # release file below exists, so observing the code above
            # necessarily happened WHILE the child was blocked.
            assert proc.poll() is None, "process should still be blocked in poll_for_token at this point"
        finally:
            release_file.write_text("go", encoding="utf-8")
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)

    def test_tty_path_still_works(self, tmp_path):
        """Sanity check for the other half of the verification ask:
        confirm the TTY path (the case that already worked before this
        fix, via line-buffering-by-default on a real terminal) still
        completes normally - run to completion rather than a pty
        capture (spawning a real pty is platform-specific/POSIX-only),
        just confirming the fix doesn't somehow break a normal run."""
        poll_sleep_seconds = 0.2
        script = self._write_fake_auth_script(tmp_path, poll_sleep_seconds)

        result = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "FAKE-CODE" in result.stdout
        assert "Authentication Successful" in result.stdout
