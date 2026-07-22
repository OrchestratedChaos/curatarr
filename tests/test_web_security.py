"""Tests for web/security.py - secret redaction and safe path joins."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from web.security import redact, redact_lines, safe_join


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
