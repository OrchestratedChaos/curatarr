"""Tests for web/docker_server.py - the container-only production
entrypoint for the web UI (see Dockerfile / docker-entrypoint.sh).

Deliberately separate from web/app.py's own main(), which is (and must
stay) hardcoded to bind 127.0.0.1 ONLY - see
tests/test_web_routes.py::TestBindingGuardrail, which source-inspects
web/app.py for exactly that. This file's job is the mirror image: prove
web/docker_server.py binds 0.0.0.0 by default, entirely independently
of web/app.py's own guarantee.
"""

import os
import signal
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import web.docker_server as docker_server

# A strong-enough token for tests that need main()/
# _require_auth_token_or_exit to NOT fail closed - see
# web/security.py's MIN_AUTH_TOKEN_LENGTH.
_STRONG_TOKEN = "a" * 32


@pytest.fixture(autouse=True)
def _restore_process_signal_handlers():
    """#263: main() now really does call signal.signal(SIGINT/SIGTERM, ...)
    with a real handler (previously it registered none at all - that was
    the bug) - every TestMain test below calls main() with waitress.serve
    mocked out so it returns immediately, which means this process's own
    SIGINT/SIGTERM handlers get overwritten as a side effect of just
    running these tests. Save/restore around every test in this module
    so that side effect can never leak into a later test or into
    pytest's own Ctrl-C handling for the rest of the session.
    """
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGINT, original_sigint)
    signal.signal(signal.SIGTERM, original_sigterm)


@pytest.fixture(autouse=True)
def _mock_scheduler_thread():
    """#264: main() now also constructs and start()s a real
    SchedulerThread - every TestMain test below calls main() with
    waitress.serve mocked out, so without this, each one would leave a
    REAL (if idle - fake_app is a bare Mock, so every tick just hits
    the "invalid schedule config" path once and goes quiet) background
    thread running for the rest of the test session. Mocked out here so
    no test in this module ever spawns one; TestSchedulerWiring below
    explicitly un-mocks this to assert main() wires it up correctly.
    """
    with patch.object(docker_server, "SchedulerThread") as mock_cls:
        mock_cls.return_value = Mock()
        yield mock_cls


class TestMain:
    """CURATARR_AUTH_TOKEN (or CURATARR_TRUSTED_NETWORK=true) is now
    required for every one of these to actually reach create_app()/
    waitress.serve() - see TestRequireAuthTokenOrExit below for the
    fail-closed/opt-out gate itself; these tests set a strong token so
    they can keep testing bind-host/port resolution, main()'s own job.
    """

    def test_binds_0_0_0_0_by_default(self, monkeypatch):
        monkeypatch.delenv("CURATARR_UI_HOST", raising=False)
        monkeypatch.delenv("CURATARR_UI_PORT", raising=False)
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", _STRONG_TOKEN)
        fake_app = Mock()
        with (
            patch.object(docker_server, "create_app", return_value=fake_app) as mock_create_app,
            patch.object(docker_server.waitress, "serve") as mock_serve,
        ):
            docker_server.main()
        mock_create_app.assert_called_once_with(bind_host="0.0.0.0")
        mock_serve.assert_called_once_with(
            fake_app,
            host="0.0.0.0",
            port=docker_server.DEFAULT_PORT,
            threads=docker_server.THREADS,
        )

    def test_default_port_is_8787(self):
        assert docker_server.DEFAULT_PORT == 8787

    def test_curatarr_ui_port_env_override(self, monkeypatch):
        monkeypatch.delenv("CURATARR_UI_HOST", raising=False)
        monkeypatch.setenv("CURATARR_UI_PORT", "9000")
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", _STRONG_TOKEN)
        fake_app = Mock()
        with (
            patch.object(docker_server, "create_app", return_value=fake_app),
            patch.object(docker_server.waitress, "serve") as mock_serve,
        ):
            docker_server.main()
        mock_serve.assert_called_once_with(
            fake_app,
            host="0.0.0.0",
            port=9000,
            threads=docker_server.THREADS,
        )

    def test_curatarr_ui_host_env_override(self, monkeypatch):
        """A caller can bind a more restrictive interface than 0.0.0.0
        if they want to - never a MORE permissive one than what they
        explicitly ask for, but this module's whole job is to NOT be
        hardcoded to 127.0.0.1 the way web/app.py's main() is."""
        monkeypatch.setenv("CURATARR_UI_HOST", "10.0.0.5")
        monkeypatch.delenv("CURATARR_UI_PORT", raising=False)
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", _STRONG_TOKEN)
        fake_app = Mock()
        with (
            patch.object(docker_server, "create_app", return_value=fake_app),
            patch.object(docker_server.waitress, "serve") as mock_serve,
        ):
            docker_server.main()
        mock_serve.assert_called_once_with(
            fake_app,
            host="10.0.0.5",
            port=docker_server.DEFAULT_PORT,
            threads=docker_server.THREADS,
        )

    def test_loopback_host_never_needs_a_token(self, monkeypatch):
        """A loopback bind_host is exempt from the whole auth-token
        requirement - see _is_loopback_bind/_require_auth_token_or_exit."""
        monkeypatch.setenv("CURATARR_UI_HOST", "127.0.0.1")
        monkeypatch.delenv("CURATARR_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("CURATARR_TRUSTED_NETWORK", raising=False)
        fake_app = Mock()
        with (
            patch.object(docker_server, "create_app", return_value=fake_app),
            patch.object(docker_server.waitress, "serve") as mock_serve,
        ):
            docker_server.main()
        mock_serve.assert_called_once()


class TestRequireAuthTokenOrExit:
    """SECURITY: web/docker_server.py's whole reason for existing is
    binding non-loopback (see module docstring) - _require_auth_token_or_exit
    is what stops that from ever happening completely unauthenticated by
    accident. See web/security.py's register_token_auth for the
    per-request half of this same guarantee."""

    def test_no_token_and_no_trusted_network_exits(self, monkeypatch, capsys):
        monkeypatch.delenv("CURATARR_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("CURATARR_TRUSTED_NETWORK", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            docker_server._require_auth_token_or_exit("0.0.0.0")
        assert exc_info.value.code == 1
        stderr = capsys.readouterr().err
        assert "CURATARR_AUTH_TOKEN" in stderr
        assert "CURATARR_TRUSTED_NETWORK" in stderr

    def test_short_token_is_treated_as_no_token(self, monkeypatch):
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", "short")
        monkeypatch.delenv("CURATARR_TRUSTED_NETWORK", raising=False)
        with pytest.raises(SystemExit):
            docker_server._require_auth_token_or_exit("0.0.0.0")

    def test_strong_token_starts_without_exit(self, monkeypatch):
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", _STRONG_TOKEN)
        monkeypatch.delenv("CURATARR_TRUSTED_NETWORK", raising=False)
        docker_server._require_auth_token_or_exit("0.0.0.0")  # must not raise

    def test_trusted_network_opt_out_starts_without_a_token(self, monkeypatch, capsys):
        monkeypatch.delenv("CURATARR_AUTH_TOKEN", raising=False)
        monkeypatch.setenv("CURATARR_TRUSTED_NETWORK", "true")
        docker_server._require_auth_token_or_exit("0.0.0.0")  # must not raise
        stderr = capsys.readouterr().err
        assert "UNAUTHENTICATED" in stderr

    def test_trusted_network_opt_out_ignored_if_token_also_set(self, monkeypatch):
        """A configured token always wins - CURATARR_TRUSTED_NETWORK
        only ever loosens the "no token" case, never overrides an
        actually-configured one (see register_token_auth's docstring
        for the matching per-request behavior)."""
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", _STRONG_TOKEN)
        monkeypatch.setenv("CURATARR_TRUSTED_NETWORK", "true")
        docker_server._require_auth_token_or_exit("0.0.0.0")  # must not raise

    def test_loopback_bind_never_requires_a_token(self, monkeypatch):
        monkeypatch.delenv("CURATARR_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("CURATARR_TRUSTED_NETWORK", raising=False)
        docker_server._require_auth_token_or_exit("127.0.0.1")  # must not raise


class TestIndependenceFromNativeAppGuardrail:
    """Sanity check that this module is what actually binds 0.0.0.0 in
    the container - not a change smuggled into web/app.py's main()
    (which tests/test_web_routes.py::TestBindingGuardrail independently
    locks down to 127.0.0.1 only)."""

    def test_source_contains_wildcard_bind_default(self):
        import inspect

        source = inspect.getsource(docker_server)
        assert '"0.0.0.0"' in source

    def test_does_not_define_its_own_flask_app(self):
        """Must reuse web.app.create_app() (same routes/guards/config
        loading as the native app), never a parallel Flask app
        definition that could drift out of sync with it."""
        import inspect

        source = inspect.getsource(docker_server)
        assert "create_app" in source
        assert "Flask(" not in source

    def test_uses_waitress_not_flask_dev_server(self):
        """Regression guard: this module must serve via waitress (a
        production WSGI server appropriate for a long-running
        container), never fall back to app.run() (Flask's single-
        threaded dev server - fine for web/app.py's native, single-user,
        localhost-only main(), not for this one)."""
        import inspect

        source = inspect.getsource(docker_server)
        assert "waitress.serve(" in source
        assert ".run(" not in source


class TestShutdownHandling:
    """#263: docker_server.py's main() caught SIGINT but never SIGTERM
    (confirmed via /proc/1/status's SigCgt bitmask in a real container),
    so every `docker stop`/`compose restart`/recreate ate the full stop
    grace period before being SIGKILLed - and any in-flight recommender
    run's log lost whatever hadn't been flushed. main() must register
    the same atexit + SIGINT/SIGTERM -> terminate_running() handling
    web/app.py's own main() (native mode) has always had.
    """

    def _run_main_with_mocks(self, monkeypatch):
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", _STRONG_TOKEN)
        fake_app = Mock()
        with (
            patch.object(docker_server, "create_app", return_value=fake_app),
            patch.object(docker_server.waitress, "serve"),
            patch.object(docker_server.atexit, "register") as mock_atexit_register,
        ):
            docker_server.main()
        return fake_app, mock_atexit_register

    def test_registers_atexit_terminate_running(self, monkeypatch):
        fake_app, mock_atexit_register = self._run_main_with_mocks(monkeypatch)
        mock_atexit_register.assert_called_once_with(fake_app.job_manager.terminate_running)

    def test_sigterm_handler_terminates_running_job_and_exits(self, monkeypatch):
        fake_app, _mock_atexit_register = self._run_main_with_mocks(monkeypatch)

        sigterm_handler = signal.getsignal(signal.SIGTERM)
        assert sigterm_handler is not None
        assert sigterm_handler not in (signal.SIG_DFL, signal.SIG_IGN)

        with pytest.raises(SystemExit) as exc_info:
            sigterm_handler(signal.SIGTERM, None)

        fake_app.job_manager.terminate_running.assert_called_once()
        assert exc_info.value.code == 0

    def test_sigint_handler_also_terminates_running_job(self, monkeypatch):
        fake_app, _mock_atexit_register = self._run_main_with_mocks(monkeypatch)

        sigint_handler = signal.getsignal(signal.SIGINT)
        assert sigint_handler is not None
        assert sigint_handler not in (signal.SIG_DFL, signal.SIG_IGN)

        with pytest.raises(SystemExit):
            sigint_handler(signal.SIGINT, None)

        fake_app.job_manager.terminate_running.assert_called_once()


class TestSchedulerWiring:
    """#264: main() must construct a real SchedulerThread (using the
    app's job_manager and load_config_cached) and start() it - the
    _mock_scheduler_thread autouse fixture above intercepts the class
    itself, so these tests inspect what it was CALLED with rather than
    letting a real thread spawn."""

    def test_constructs_scheduler_thread_with_job_manager_and_config_loader(self, monkeypatch, _mock_scheduler_thread):
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", _STRONG_TOKEN)
        fake_app = Mock()
        with (
            patch.object(docker_server, "create_app", return_value=fake_app),
            patch.object(docker_server.waitress, "serve"),
        ):
            docker_server.main()

        _mock_scheduler_thread.assert_called_once_with(fake_app.job_manager, fake_app.load_config_cached)

    def test_starts_the_scheduler_thread(self, monkeypatch, _mock_scheduler_thread):
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", _STRONG_TOKEN)
        fake_app = Mock()
        with (
            patch.object(docker_server, "create_app", return_value=fake_app),
            patch.object(docker_server.waitress, "serve"),
        ):
            docker_server.main()

        fake_scheduler_thread = _mock_scheduler_thread.return_value
        fake_scheduler_thread.start.assert_called_once()
