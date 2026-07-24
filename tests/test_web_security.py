"""Tests for web/security.py - secret redaction and safe path joins."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from web.security import is_allowed_host, redact, redact_lines, safe_join


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
