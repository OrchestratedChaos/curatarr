"""Tests for web/security.py - secret redaction and safe path joins."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from web.app import create_app
from web.security import (
    _client_ip,
    _is_loopback_bind,
    _login_failures,
    is_allowed_host,
    redact,
    redact_lines,
    safe_join,
)


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
        monkeypatch.delenv("CURATARR_ALLOWED_HOSTS", raising=False)
        assert is_allowed_host("192.168.1.50:8787") is False
        assert is_allowed_host("localhost:8787") is True

    def test_listed_host_is_allowed(self, monkeypatch):
        monkeypatch.setenv("CURATARR_ALLOWED_HOSTS", "192.168.1.50:8787")
        assert is_allowed_host("192.168.1.50:8787") is True

    def test_comma_separated_list_supported(self, monkeypatch):
        monkeypatch.setenv(
            "CURATARR_ALLOWED_HOSTS",
            "192.168.1.50:8787, curatarr.example.lan",
        )
        assert is_allowed_host("192.168.1.50:8787") is True
        assert is_allowed_host("curatarr.example.lan") is True

    def test_case_insensitive_match(self, monkeypatch):
        monkeypatch.setenv("CURATARR_ALLOWED_HOSTS", "Curatarr.Example.LAN")
        assert is_allowed_host("curatarr.example.lan") is True

    def test_unlisted_host_still_rejected(self, monkeypatch):
        monkeypatch.setenv("CURATARR_ALLOWED_HOSTS", "192.168.1.50:8787")
        assert is_allowed_host("evil.example.com") is False

    def test_default_localhost_still_allowed_alongside_override(self, monkeypatch):
        """The override is additive - it never replaces the hardcoded
        default."""
        monkeypatch.setenv("CURATARR_ALLOWED_HOSTS", "192.168.1.50:8787")
        assert is_allowed_host("localhost:8787") is True
        assert is_allowed_host("127.0.0.1") is True


class TestIsLoopbackBind:
    """Tests for _is_loopback_bind() - the server BIND address check
    driving register_token_auth/web.docker_server's fail-closed startup
    gate. NOT the same thing as is_allowed_host (a request's Host
    header) - see that function's own docstring."""

    def test_127_0_0_1_is_loopback(self):
        assert _is_loopback_bind("127.0.0.1") is True

    def test_ipv6_loopback_is_loopback(self):
        assert _is_loopback_bind("::1") is True

    def test_bare_localhost_is_loopback(self):
        assert _is_loopback_bind("localhost") is True

    def test_0_0_0_0_is_not_loopback(self):
        assert _is_loopback_bind("0.0.0.0") is False

    def test_lan_interface_is_not_loopback(self):
        assert _is_loopback_bind("10.0.0.5") is False

    def test_empty_or_none_is_not_loopback(self):
        assert _is_loopback_bind("") is False
        assert _is_loopback_bind(None) is False

    def test_case_and_whitespace_insensitive(self):
        assert _is_loopback_bind(" LOCALHOST ") is True


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

    NON_LOOPBACK_HOST = "0.0.0.0"
    TOKEN = "a" * 32

    def _client(self, curatarr_web_root, bind_host, monkeypatch, token=None):
        if token is not None:
            monkeypatch.setenv("CURATARR_AUTH_TOKEN", token)
        else:
            monkeypatch.delenv("CURATARR_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("CURATARR_TRUSTED_NETWORK", raising=False)
        app = create_app(project_root=curatarr_web_root, bind_host=bind_host)
        app.testing = True
        return app.test_client()

    def test_no_token_supplied_rejected(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get("/")
        assert resp.status_code == 401

    def test_wrong_token_rejected(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get("/", headers={"X-Curatarr-Token": "w" * 32})
        assert resp.status_code == 401

    def test_correct_token_via_x_curatarr_token_header_accepted(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get("/", headers={"X-Curatarr-Token": self.TOKEN})
        assert resp.status_code == 200

    def test_correct_token_via_bearer_header_accepted(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get("/", headers={"Authorization": f"Bearer {self.TOKEN}"})
        assert resp.status_code == 200

    def test_correct_token_via_cookie_accepted(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        c.set_cookie("curatarr_token", self.TOKEN)
        resp = c.get("/")
        assert resp.status_code == 200

    def test_healthz_reachable_without_token(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get("/healthz")
        assert resp.status_code == 200

    def test_login_form_reachable_without_token(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get("/login")
        assert resp.status_code == 200

    def test_login_submit_with_correct_token_sets_cookie_and_redirects(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.post("/login", data={"token": self.TOKEN}, follow_redirects=False)
        assert resp.status_code == 303
        set_cookie = resp.headers.get("Set-Cookie", "")
        assert "curatarr_token" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=Strict" in set_cookie
        assert "Secure" not in set_cookie  # plain HTTP by design - see login_submit's comment

    def test_login_submit_with_wrong_token_does_not_set_cookie(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.post("/login", data={"token": "nope"}, follow_redirects=False)
        assert resp.status_code == 303
        assert "curatarr_token" not in resp.headers.get("Set-Cookie", "")

    def test_loopback_bind_requires_no_token(self, curatarr_web_root, monkeypatch):
        """Byte-for-byte unchanged native-install behavior - see
        register_token_auth's own docstring."""
        c = self._client(curatarr_web_root, "127.0.0.1", monkeypatch, token=None)
        resp = c.get("/")
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
        resp = c.get("/", headers={"Host": "localhost"})
        assert resp.status_code == 401

    def test_trusted_network_opt_out_allows_no_token(self, curatarr_web_root, monkeypatch):
        monkeypatch.delenv("CURATARR_AUTH_TOKEN", raising=False)
        monkeypatch.setenv("CURATARR_TRUSTED_NETWORK", "true")
        app = create_app(project_root=curatarr_web_root, bind_host=self.NON_LOOPBACK_HOST)
        app.testing = True
        resp = app.test_client().get("/")
        assert resp.status_code == 200

    def test_trusted_network_opt_out_ignored_once_a_token_is_configured(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", self.TOKEN)
        monkeypatch.setenv("CURATARR_TRUSTED_NETWORK", "true")
        app = create_app(project_root=curatarr_web_root, bind_host=self.NON_LOOPBACK_HOST)
        app.testing = True
        resp = app.test_client().get("/")
        assert resp.status_code == 401  # a configured token always wins


class TestConditionalSecureCookie:
    """PR2(b): the curatarr_token cookie's Secure attribute is set only
    when the request actually arrived over TLS - directly
    (request.is_secure) or, opt-in only, via a trusted reverse proxy's
    X-Forwarded-Proto header - see web/security.py's
    _request_is_secure. Never set on a plain-HTTP request with no
    opt-in, matching this app's plain-HTTP-by-design default (see
    TestRegisterTokenAuth.test_login_submit_with_correct_token_sets_cookie_and_redirects
    above, unchanged)."""

    NON_LOOPBACK_HOST = "0.0.0.0"
    TOKEN = "a" * 32

    def _client(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", self.TOKEN)
        monkeypatch.delenv("CURATARR_TRUSTED_NETWORK", raising=False)
        app = create_app(project_root=curatarr_web_root, bind_host=self.NON_LOOPBACK_HOST)
        app.testing = True
        return app.test_client()

    def test_plain_http_request_gets_no_secure_flag(self, curatarr_web_root, monkeypatch):
        monkeypatch.delenv("CURATARR_TRUST_PROXY_PROTO", raising=False)
        c = self._client(curatarr_web_root, monkeypatch)
        resp = c.post("/login", data={"token": self.TOKEN}, follow_redirects=False)
        assert "Secure" not in resp.headers.get("Set-Cookie", "")

    def test_direct_tls_request_gets_secure_flag(self, curatarr_web_root, monkeypatch):
        monkeypatch.delenv("CURATARR_TRUST_PROXY_PROTO", raising=False)
        c = self._client(curatarr_web_root, monkeypatch)
        resp = c.post(
            "/login",
            data={"token": self.TOKEN},
            follow_redirects=False,
            environ_overrides={"wsgi.url_scheme": "https"},
        )
        assert "Secure" in resp.headers.get("Set-Cookie", "")

    def test_forwarded_proto_ignored_without_opt_in(self, curatarr_web_root, monkeypatch):
        """A direct caller can set X-Forwarded-Proto to whatever it
        wants - without CURATARR_TRUST_PROXY_PROTO=true, it must not be
        trusted (see _request_is_secure's docstring)."""
        monkeypatch.delenv("CURATARR_TRUST_PROXY_PROTO", raising=False)
        c = self._client(curatarr_web_root, monkeypatch)
        resp = c.post(
            "/login",
            data={"token": self.TOKEN},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert "Secure" not in resp.headers.get("Set-Cookie", "")

    def test_forwarded_proto_honored_with_explicit_opt_in(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv("CURATARR_TRUST_PROXY_PROTO", "true")
        c = self._client(curatarr_web_root, monkeypatch)
        resp = c.post(
            "/login",
            data={"token": self.TOKEN},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https"},
        )
        assert "Secure" in resp.headers.get("Set-Cookie", "")

    def test_forwarded_proto_http_with_opt_in_stays_insecure(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv("CURATARR_TRUST_PROXY_PROTO", "true")
        c = self._client(curatarr_web_root, monkeypatch)
        resp = c.post(
            "/login",
            data={"token": self.TOKEN},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "http"},
        )
        assert "Secure" not in resp.headers.get("Set-Cookie", "")

    def test_forwarded_proto_takes_last_value_in_chain(self, curatarr_web_root, monkeypatch):
        """Only the nearest hop's appended value is trusted - see
        _request_is_secure's docstring on single-hop trust."""
        monkeypatch.setenv("CURATARR_TRUST_PROXY_PROTO", "true")
        c = self._client(curatarr_web_root, monkeypatch)
        resp = c.post(
            "/login",
            data={"token": self.TOKEN},
            follow_redirects=False,
            headers={"X-Forwarded-Proto": "https, http"},
        )
        assert "Secure" not in resp.headers.get("Set-Cookie", "")


class TestStaticAssetsExemptFromToken:
    """PR2(c): the login page's own static assets must be reachable
    before a browser has a token - see web/security.py's
    _TOKEN_EXEMPT_STATIC_PREFIX. Nothing else under /static-like paths
    is exempted beyond that one prefix."""

    NON_LOOPBACK_HOST = "0.0.0.0"
    TOKEN = "a" * 32

    def _client(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", self.TOKEN)
        monkeypatch.delenv("CURATARR_TRUSTED_NETWORK", raising=False)
        app = create_app(project_root=curatarr_web_root, bind_host=self.NON_LOOPBACK_HOST)
        app.testing = True
        return app.test_client()

    def test_static_css_reachable_without_token(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, monkeypatch)
        resp = c.get("/static/style.css")
        assert resp.status_code != 401

    def test_other_paths_still_require_token(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, monkeypatch)
        resp = c.get("/config/connections")
        assert resp.status_code == 401


class TestLoginRateLimiting:
    """PR2(a): per-IP failed-attempt lockout on POST /login - see
    web/security.py's _is_locked_out/_record_login_failure. Policy:
    at most 5 failed attempts per source IP within a rolling 60-second
    window; never permanent - see web/security.py's module docstring
    above register_token_auth for the full rationale.
    """

    NON_LOOPBACK_HOST = "0.0.0.0"
    TOKEN = "a" * 32
    IP = "203.0.113.5"

    def setup_method(self):
        _login_failures.clear()

    def teardown_method(self):
        _login_failures.clear()

    def _client(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv("CURATARR_AUTH_TOKEN", self.TOKEN)
        monkeypatch.delenv("CURATARR_TRUSTED_NETWORK", raising=False)
        app = create_app(project_root=curatarr_web_root, bind_host=self.NON_LOOPBACK_HOST)
        app.testing = True
        return app.test_client()

    def _fail(self, c, times, ip=None):
        for _ in range(times):
            c.post(
                "/login",
                data={"token": "wrong"},
                follow_redirects=False,
                environ_overrides={"REMOTE_ADDR": ip or self.IP},
            )

    def test_below_threshold_still_gets_invalid_token_error(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, monkeypatch)
        self._fail(c, 4)
        resp = c.post(
            "/login",
            data={"token": "wrong"},
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": self.IP},
        )
        assert resp.headers["Location"] == "/login?error=1"

    def test_reaching_threshold_locks_out(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, monkeypatch)
        self._fail(c, 5)
        resp = c.post(
            "/login",
            data={"token": self.TOKEN},  # even the CORRECT token is rejected while locked out
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": self.IP},
        )
        assert resp.headers["Location"] == "/login?error=locked"
        assert "curatarr_token" not in resp.headers.get("Set-Cookie", "")

    def test_lockout_is_scoped_per_ip(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, monkeypatch)
        self._fail(c, 5, ip=self.IP)
        resp = c.post(
            "/login",
            data={"token": self.TOKEN},
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": "198.51.100.9"},
        )
        assert resp.status_code == 303
        assert "curatarr_token" in resp.headers.get("Set-Cookie", "")

    def test_successful_login_clears_failure_history(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, monkeypatch)
        self._fail(c, 4)
        resp = c.post(
            "/login",
            data={"token": self.TOKEN},
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": self.IP},
        )
        assert "curatarr_token" in resp.headers.get("Set-Cookie", "")
        assert _client_ip.__module__ == "web.security"  # sanity the right module is under test
        # A fresh run of failures now needs the full threshold again,
        # not just one more (proves the history was actually cleared).
        self._fail(c, 4)
        resp = c.post(
            "/login",
            data={"token": "wrong"},
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": self.IP},
        )
        assert resp.headers["Location"] == "/login?error=1"

    def test_lockout_expires_after_window(self, curatarr_web_root, monkeypatch):
        """Never permanent - a rolling window that ages out on its
        own. Simulated by monkeypatching time.monotonic rather than
        actually sleeping 60s."""
        import web.security as security_module

        fake_now = [1000.0]
        monkeypatch.setattr(security_module.time, "monotonic", lambda: fake_now[0])

        c = self._client(curatarr_web_root, monkeypatch)
        self._fail(c, 5)
        resp = c.post(
            "/login",
            data={"token": self.TOKEN},
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": self.IP},
        )
        assert resp.headers["Location"] == "/login?error=locked"

        fake_now[0] += security_module._LOGIN_WINDOW_SECONDS + 1
        resp = c.post(
            "/login",
            data={"token": self.TOKEN},
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": self.IP},
        )
        assert resp.status_code == 303
        assert "curatarr_token" in resp.headers.get("Set-Cookie", "")

    def test_failed_attempt_is_logged(self, curatarr_web_root, monkeypatch):
        import web.security as security_module

        logged = []
        monkeypatch.setattr(security_module, "log_warning", lambda msg: logged.append(msg))

        c = self._client(curatarr_web_root, monkeypatch)
        c.post(
            "/login",
            data={"token": "wrong"},
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": self.IP},
        )
        assert any(self.IP in msg for msg in logged)
        assert not any("wrong" in msg for msg in logged)  # never log the attempted token

    def test_dict_size_bounded_under_distributed_flood(self, monkeypatch):
        """PR5: a distributed one-request-per-IP flood (each IP hits the
        endpoint exactly once, never revisiting itself) must not grow
        _login_failures without bound - nothing else would ever prune
        those one-shot entries (see _sweep_login_failures_locked)."""
        import web.security as security_module

        monkeypatch.setattr(security_module, "_LOGIN_FAILURES_MAX_TRACKED_IPS", 5)

        for i in range(50):
            security_module._record_login_failure(f"203.0.113.{i}")

        assert len(_login_failures) <= 5

    def test_active_lockout_survives_flood_of_other_ips(self, curatarr_web_root, monkeypatch):
        """An attacker cannot use a flood of throwaway one-shot IPs to
        push a DIFFERENT, already-locked-out IP's entry out of the
        tracker and let it straight back in early."""
        import web.security as security_module

        monkeypatch.setattr(security_module, "_LOGIN_FAILURES_MAX_TRACKED_IPS", 5)
        c = self._client(curatarr_web_root, monkeypatch)
        self._fail(c, 5, ip=self.IP)  # locks out self.IP

        for i in range(50):
            security_module._record_login_failure(f"198.51.100.{i}")

        resp = c.post(
            "/login",
            data={"token": self.TOKEN},
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": self.IP},
        )
        assert resp.headers["Location"] == "/login?error=locked"

    def test_legitimate_user_still_succeeds_after_unrelated_ip_flood(self, curatarr_web_root, monkeypatch):
        """A flood of unrelated one-shot IPs filling up the tracker must
        not prevent a normal user (under the failure threshold) from
        logging in with the correct token afterward - eviction may or may
        not remove their own (not-locked-out) entry, and either outcome
        must still let them through."""
        import web.security as security_module

        monkeypatch.setattr(security_module, "_LOGIN_FAILURES_MAX_TRACKED_IPS", 5)
        c = self._client(curatarr_web_root, monkeypatch)
        self._fail(c, 2, ip=self.IP)  # under threshold

        for i in range(50):
            security_module._record_login_failure(f"198.51.100.{i}")

        resp = c.post(
            "/login",
            data={"token": self.TOKEN},
            follow_redirects=False,
            environ_overrides={"REMOTE_ADDR": self.IP},
        )
        assert resp.status_code == 303
        assert "curatarr_token" in resp.headers.get("Set-Cookie", "")

    def test_expired_entries_pruned_before_evicting_active_ones(self, monkeypatch):
        """The sweep drops naturally-expired entries first, before ever
        considering eviction - a batch of one-shot IPs that has simply
        aged out of the window costs nothing once the window has passed,
        even without a fresh request from any of them."""
        import web.security as security_module

        fake_now = [1000.0]
        monkeypatch.setattr(security_module.time, "monotonic", lambda: fake_now[0])
        monkeypatch.setattr(security_module, "_LOGIN_FAILURES_MAX_TRACKED_IPS", 5)

        for i in range(5):
            security_module._record_login_failure(f"198.51.100.{i}")
        assert len(_login_failures) == 5

        fake_now[0] += security_module._LOGIN_WINDOW_SECONDS + 1  # everything above ages out

        security_module._record_login_failure("203.0.113.1")  # triggers a sweep (6 > 5)

        assert set(_login_failures.keys()) == {"203.0.113.1"}
