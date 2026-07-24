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
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web.docker_server as docker_server


class TestMain:
    def test_binds_0_0_0_0_by_default(self, monkeypatch):
        monkeypatch.delenv('CURATARR_UI_HOST', raising=False)
        monkeypatch.delenv('CURATARR_UI_PORT', raising=False)
        fake_app = Mock()
        with patch.object(docker_server, 'create_app', return_value=fake_app) as mock_create_app, \
                patch.object(docker_server.waitress, 'serve') as mock_serve:
            docker_server.main()
        mock_create_app.assert_called_once_with()
        mock_serve.assert_called_once_with(
            fake_app, host='0.0.0.0', port=docker_server.DEFAULT_PORT,
            threads=docker_server.THREADS,
        )

    def test_default_port_is_8787(self):
        assert docker_server.DEFAULT_PORT == 8787

    def test_curatarr_ui_port_env_override(self, monkeypatch):
        monkeypatch.delenv('CURATARR_UI_HOST', raising=False)
        monkeypatch.setenv('CURATARR_UI_PORT', '9000')
        fake_app = Mock()
        with patch.object(docker_server, 'create_app', return_value=fake_app), \
                patch.object(docker_server.waitress, 'serve') as mock_serve:
            docker_server.main()
        mock_serve.assert_called_once_with(
            fake_app, host='0.0.0.0', port=9000, threads=docker_server.THREADS,
        )

    def test_curatarr_ui_host_env_override(self, monkeypatch):
        """A caller can bind a more restrictive interface than 0.0.0.0
        if they want to - never a MORE permissive one than what they
        explicitly ask for, but this module's whole job is to NOT be
        hardcoded to 127.0.0.1 the way web/app.py's main() is."""
        monkeypatch.setenv('CURATARR_UI_HOST', '10.0.0.5')
        monkeypatch.delenv('CURATARR_UI_PORT', raising=False)
        fake_app = Mock()
        with patch.object(docker_server, 'create_app', return_value=fake_app), \
                patch.object(docker_server.waitress, 'serve') as mock_serve:
            docker_server.main()
        mock_serve.assert_called_once_with(
            fake_app, host='10.0.0.5', port=docker_server.DEFAULT_PORT,
            threads=docker_server.THREADS,
        )


class TestIndependenceFromNativeAppGuardrail:
    """Sanity check that this module is what actually binds 0.0.0.0 in
    the container - not a change smuggled into web/app.py's main()
    (which tests/test_web_routes.py::TestBindingGuardrail independently
    locks down to 127.0.0.1 only)."""

    def test_source_contains_wildcard_bind_default(self):
        import inspect

        source = inspect.getsource(docker_server)
        assert "'0.0.0.0'" in source

    def test_does_not_define_its_own_flask_app(self):
        """Must reuse web.app.create_app() (same routes/guards/config
        loading as the native app), never a parallel Flask app
        definition that could drift out of sync with it."""
        import inspect

        source = inspect.getsource(docker_server)
        assert 'create_app' in source
        assert 'Flask(' not in source

    def test_uses_waitress_not_flask_dev_server(self):
        """Regression guard: this module must serve via waitress (a
        production WSGI server appropriate for a long-running
        container), never fall back to app.run() (Flask's single-
        threaded dev server - fine for web/app.py's native, single-user,
        localhost-only main(), not for this one)."""
        import inspect

        source = inspect.getsource(docker_server)
        assert 'waitress.serve(' in source
        assert '.run(' not in source
