"""
Tests for utils/labels.py - Label management functions.
"""

import pytest
from utils.labels import build_label_name


class TestBuildLabelName:
    """Tests for build_label_name() function."""

    def test_single_user(self):
        """Test building label name with a single user."""
        result = build_label_name(
            base_label="Recommended",
            users=["Jason"],
            single_user="Jason",
            append_usernames=True
        )

        assert result == "Recommended_Jason"

    def test_multiple_users(self):
        """Test building label name with multiple users."""
        result = build_label_name(
            base_label="Recommended",
            users=["Jason", "Sarah"],
            single_user=None,
            append_usernames=True
        )

        assert result == "Recommended_Jason_Sarah"

    def test_no_append_usernames(self):
        """Test that base label is returned when append_usernames=False."""
        result = build_label_name(
            base_label="Recommended",
            users=["Jason", "Sarah"],
            single_user=None,
            append_usernames=False
        )

        assert result == "Recommended"

    def test_empty_users_list(self):
        """Test with empty users list and no single_user."""
        result = build_label_name(
            base_label="Recommended",
            users=[],
            single_user=None,
            append_usernames=True
        )

        assert result == "Recommended"

    def test_special_characters_sanitized(self):
        """Test that special characters are replaced with underscores."""
        result = build_label_name(
            base_label="Recommended",
            users=[],
            single_user="John Doe",
            append_usernames=True
        )

        assert result == "Recommended_John_Doe"

    def test_special_chars_in_username(self):
        """Test various special characters in username."""
        result = build_label_name(
            base_label="Recommended",
            users=[],
            single_user="user@home!",
            append_usernames=True
        )

        assert result == "Recommended_user_home_"

    def test_single_user_overrides_users_list(self):
        """Test that single_user takes precedence over users list."""
        result = build_label_name(
            base_label="Recommended",
            users=["UserA", "UserB"],
            single_user="SingleUser",
            append_usernames=True
        )

        assert result == "Recommended_SingleUser"

    def test_whitespace_trimmed(self):
        """Test that whitespace is trimmed from usernames."""
        result = build_label_name(
            base_label="Recommended",
            users=[],
            single_user="  Jason  ",
            append_usernames=True
        )

        assert result == "Recommended_Jason"

    def test_different_base_labels(self):
        """Test with different base label names."""
        result = build_label_name(
            base_label="ToWatch",
            users=["User1"],
            single_user=None,
            append_usernames=True
        )

        assert result == "ToWatch_User1"
