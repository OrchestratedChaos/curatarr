"""Tests for web/security.py - secret redaction and safe path joins."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from web.app import create_app
from web.security import is_allowed_host, redact, redact_lines, safe_join
from web.security import _is_loopback_bind


class TestRedact:
    """Tests for redact()"""

    def test_masks_key_value_secret(self):
        assert redact("token=abcd1234efgh") == "token=***REDACTED***"

    def test_masks_case_insensitive_key(self):
        assert redact("API_KEY: supersecretvalue123") == "API_KEY=***REDACTED***"

    def test_masks_quoted_value(self):
        assert redact('password="hunter2hunter2"') == "password=***REDACTED***"

    def test_masks_plex_token_in_url(self):
        text = "GET http://localhost:32400/library?X-Plex-Token=abcd1234efgh5678"
        result = redact(text)
        assert "abcd1234efgh5678" not in result
        assert "***REDACTED***" in result

    def test_masks_bearer_header(self):
        result = redact("Authorization: Bearer abcdefghijklmnop")
        assert "abcdefghijklmnop" not in result
        assert "Bearer ***REDACTED***" in result

    def test_leaves_normal_text_untouched(self):
        text = "Processing recommendations for alice: 20 movies found"
        assert redact(text) == text

    def test_empty_string_passthrough(self):
        assert redact("") == ""

    def test_none_passthrough(self):
        assert redact(None) is None

    def test_redact_lines(self):
        lines = ["normal line", "token=secretvalue1"]
        result = redact_lines(lines)
        assert result[0] == "normal line"
        assert "secretvalue1" not in result[1]

    def test_masks_value_with_leading_special_char(self):
        # A value starting with a non-alnum char (`$`, `#`, `!`, a
        # leading base64 `+`/`/`, ...) used to fall entirely outside the
        # old character class, so the whole key=value pair passed
        # through unredacted.
        result = redact('token: "$ecretValue123"')
        assert "$ecretValue123" not in result
        assert "token=***REDACTED***" in result

    def test_masks_value_with_leading_special_char_unquoted(self):
        result = redact("api_key=#deadbeef!123")
        assert "#deadbeef!123" not in result
        assert "api_key=***REDACTED***" in result

    def test_masks_bare_known_prefix_token(self):
        # No "key: "/"key=" prefix at all - just a raw vendor-formatted
        # token (e.g. echoed inside a stack trace argument).
        result = redact("auth failed using ghp_16C7e42F292c6912E7710c838347Ae178B4a during request")
        assert "ghp_16C7e42F292c6912E7710c838347Ae178B4a" not in result
        assert "ghp_***REDACTED***" in result

    def test_masks_bare_aws_style_prefix_token(self):
        result = redact("found leaked key AKIAABCDEFGHIJKLMNOP in output")
        assert "AKIAABCDEFGHIJKLMNOP" not in result
        assert "AKIA***REDACTED***" in result

    def test_does_not_touch_unrelated_short_word_with_prefix_substring(self):
        # Sanity check the prefix match isn't so loose it eats normal text.
        text = "the skyline was beautiful"
        assert redact(text) == text


class TestSafeJoin:
    """Tests for safe_join()"""

    def test_joins_within_base_dir(self, tmp_path):
        (tmp_path / "a.log").write_text("hi")
        result = safe_join(str(tmp_path), "a.log")
        assert result == str(tmp_path / "a.log")

    def test_rejects_parent_traversal(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            safe_join(str(tmp_path), "../secret.txt")

    def test_rejects_absolute_path_escape(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            safe_join(str(tmp_path), os.path.join(os.sep, "etc", "passwd"))

    def test_rejects_symlink_escape(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.log").write_text("TOP SECRET")
        base = tmp_path / "base"
        base.mkdir()
        link = base / "escape.log"
        try:
            os.symlink(str(outside / "secret.log"), str(link))
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported in this environment")
        with pytest.raises(FileNotFoundError):
            safe_join(str(base), "escape.log")


class TestIsAllowedHost:
    """Tests for is_allowed_host() - the Host/Origin allow-list used by
    register_origin_host_guard()."""

    def test_accepts_bare_localhost(self):
        assert is_allowed_host("localhost") is True

    def test_accepts_localhost_with_port(self):
        assert is_allowed_host("localhost:8787") is True

    def test_accepts_bare_loopback_ip(self):
        assert is_allowed_host("127.0.0.1") is True

    def test_accepts_loopback_ip_with_port(self):
        assert is_allowed_host("127.0.0.1:8787") is True

    def test_rejects_other_hostnames(self):
        assert is_allowed_host("evil.example.com") is False

    def test_rejects_other_hostnames_with_port(self):
        assert is_allowed_host("evil.example.com:8787") is False

    def test_rejects_empty(self):
        assert is_allowed_host("") is False
        assert is_allowed_host(None) is False

    def test_rejects_lan_ip_even_though_it_could_reach_the_server(self):
        # A LAN IP could, in principle, also route to this machine, but
        # the app only ever binds 127.0.0.1 - a request claiming a LAN
        # Host is either misconfigured or a rebinding attempt either way.
        assert is_allowed_host("192.168.1.50:8787") is False


class TestIsAllowedHostDockerOverride:
    """Tests for the CURATARR_ALLOWED_HOSTS additive override (see
    web/docker_server.py's module docstring for why this exists: a
    container bound to 0.0.0.0 and reached via a LAN IP or reverse-proxy
    hostname sends that value in its Host header, which the hardcoded
    127.0.0.1/localhost allowlist would otherwise always reject)."""

    def test_unset_does_not_change_default_behavior(self, monkeypatch):
        monkeypatch.delenv('CURATARR_ALLOWED_HOSTS', raising=False)
        assert is_allowed_host("192.168.1.50:8787") is False
        assert is_allowed_host("localhost:8787") is True

    def test_listed_host_is_allowed(self, monkeypatch):
        monkeypatch.setenv('CURATARR_ALLOWED_HOSTS', '192.168.1.50:8787')
        assert is_allowed_host("192.168.1.50:8787") is True

    def test_comma_separated_list_supported(self, monkeypatch):
        monkeypatch.setenv(
            'CURATARR_ALLOWED_HOSTS', '192.168.1.50:8787, curatarr.example.lan',
        )
        assert is_allowed_host("192.168.1.50:8787") is True
        assert is_allowed_host("curatarr.example.lan") is True

    def test_case_insensitive_match(self, monkeypatch):
        monkeypatch.setenv('CURATARR_ALLOWED_HOSTS', 'Curatarr.Example.LAN')
        assert is_allowed_host("curatarr.example.lan") is True

    def test_unlisted_host_still_rejected(self, monkeypatch):
        monkeypatch.setenv('CURATARR_ALLOWED_HOSTS', '192.168.1.50:8787')
        assert is_allowed_host("evil.example.com") is False

    def test_default_localhost_still_allowed_alongside_override(self, monkeypatch):
        """The override is additive - it never replaces the hardcoded
        default."""
        monkeypatch.setenv('CURATARR_ALLOWED_HOSTS', '192.168.1.50:8787')
        assert is_allowed_host("localhost:8787") is True
        assert is_allowed_host("127.0.0.1") is True


class TestIsLoopbackBind:
    """Tests for _is_loopback_bind() - the server BIND address check
    driving register_token_auth/web.docker_server's fail-closed startup
    gate. NOT the same thing as is_allowed_host (a request's Host
    header) - see that function's own docstring."""

    def test_127_0_0_1_is_loopback(self):
        assert _is_loopback_bind('127.0.0.1') is True

    def test_ipv6_loopback_is_loopback(self):
        assert _is_loopback_bind('::1') is True

    def test_bare_localhost_is_loopback(self):
        assert _is_loopback_bind('localhost') is True

    def test_0_0_0_0_is_not_loopback(self):
        assert _is_loopback_bind('0.0.0.0') is False

    def test_lan_interface_is_not_loopback(self):
        assert _is_loopback_bind('10.0.0.5') is False

    def test_empty_or_none_is_not_loopback(self):
        assert _is_loopback_bind('') is False
        assert _is_loopback_bind(None) is False

    def test_case_and_whitespace_insensitive(self):
        assert _is_loopback_bind(' LOCALHOST ') is True


class TestRegisterTokenAuth:
    """FIX 1: real token authentication whenever the server is bound to
    something other than loopback - see web/security.py's
    register_token_auth. web/docker_server.py's own
    _require_auth_token_or_exit is what stops a non-loopback bind from
    ever running with no token configured at all (see
    tests/test_web_docker_server.py's TestRequireAuthTokenOrExit); these
    tests exercise the per-request guard itself, directly against a real
    Flask app/test client - not a mock.
    """

    NON_LOOPBACK_HOST = '0.0.0.0'
    TOKEN = 'a' * 32

    def _client(self, curatarr_web_root, bind_host, monkeypatch, token=None):
        if token is not None:
            monkeypatch.setenv('CURATARR_AUTH_TOKEN', token)
        else:
            monkeypatch.delenv('CURATARR_AUTH_TOKEN', raising=False)
        monkeypatch.delenv('CURATARR_TRUSTED_NETWORK', raising=False)
        app = create_app(project_root=curatarr_web_root, bind_host=bind_host)
        app.testing = True
        return app.test_client()

    def test_no_token_supplied_rejected(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/')
        assert resp.status_code == 401

    def test_wrong_token_rejected(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/', headers={'X-Curatarr-Token': 'w' * 32})
        assert resp.status_code == 401

    def test_correct_token_via_x_curatarr_token_header_accepted(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/', headers={'X-Curatarr-Token': self.TOKEN})
        assert resp.status_code == 200

    def test_correct_token_via_bearer_header_accepted(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/', headers={'Authorization': f'Bearer {self.TOKEN}'})
        assert resp.status_code == 200

    def test_correct_token_via_cookie_accepted(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        c.set_cookie('curatarr_token', self.TOKEN)
        resp = c.get('/')
        assert resp.status_code == 200

    def test_healthz_reachable_without_token(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/healthz')
        assert resp.status_code == 200

    def test_login_form_reachable_without_token(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/login')
        assert resp.status_code == 200

    def test_login_submit_with_correct_token_sets_cookie_and_redirects(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.post('/login', data={'token': self.TOKEN}, follow_redirects=False)
        assert resp.status_code == 303
        set_cookie = resp.headers.get('Set-Cookie', '')
        assert 'curatarr_token' in set_cookie
        assert 'HttpOnly' in set_cookie
        assert 'SameSite=Strict' in set_cookie
        assert 'Secure' not in set_cookie  # plain HTTP by design - see login_submit's comment

    def test_login_submit_with_wrong_token_does_not_set_cookie(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.post('/login', data={'token': 'nope'}, follow_redirects=False)
        assert resp.status_code == 303
        assert 'curatarr_token' not in resp.headers.get('Set-Cookie', '')

    def test_loopback_bind_requires_no_token(self, curatarr_web_root, monkeypatch):
        """Byte-for-byte unchanged native-install behavior - see
        register_token_auth's own docstring."""
        c = self._client(curatarr_web_root, '127.0.0.1', monkeypatch, token=None)
        resp = c.get('/')
        assert resp.status_code == 200

    def test_forged_host_header_without_token_still_rejected(self, curatarr_web_root, monkeypatch):
        """The exact scenario the audit proved live with curl: a non-
        browser client sets Host: localhost to sail straight through
        the Host/Origin guard (web/security.py's
        register_origin_host_guard, which only ever proves something
        about a BROWSER). Getting 401 here (not 400) proves the request
        DID pass that guard and was rejected specifically for having no
        valid token - the guard alone is not authentication."""
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/', headers={'Host': 'localhost'})
        assert resp.status_code == 401

    def test_trusted_network_opt_out_allows_no_token(self, curatarr_web_root, monkeypatch):
        monkeypatch.delenv('CURATARR_AUTH_TOKEN', raising=False)
        monkeypatch.setenv('CURATARR_TRUSTED_NETWORK', 'true')
        app = create_app(project_root=curatarr_web_root, bind_host=self.NON_LOOPBACK_HOST)
        app.testing = True
        resp = app.test_client().get('/')
        assert resp.status_code == 200

    def test_trusted_network_opt_out_ignored_once_a_token_is_configured(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv('CURATARR_AUTH_TOKEN', self.TOKEN)
        monkeypatch.setenv('CURATARR_TRUSTED_NETWORK', 'true')
        app = create_app(project_root=curatarr_web_root, bind_host=self.NON_LOOPBACK_HOST)
        app.testing = True
        resp = app.test_client().get('/')
        assert resp.status_code == 401  # a configured token always wins
