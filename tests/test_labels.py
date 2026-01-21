"""
Tests for utils/labels.py - Label management functions.
"""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta
from utils.labels import (
    build_label_name,
    categorize_labeled_items,
    remove_labels_from_items,
    add_labels_to_items
)


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


class TestCategorizeLabeledItems:
    """Tests for categorize_labeled_items() function."""

    def _create_mock_item(self, rating_key, genres=None, is_played=False):
        """Helper to create mock Plex item."""
        item = Mock()
        item.ratingKey = rating_key
        item.reload = Mock()
        item.isPlayed = is_played
        if genres:
            item.genres = [Mock(tag=g) for g in genres]
        else:
            item.genres = []
        return item

    def test_categorizes_watched_items(self):
        """Test that watched items are correctly categorized."""
        item = self._create_mock_item(123)
        watched_ids = {123}
        label_dates = {}

        result = categorize_labeled_items(
            [item], watched_ids, [], 'Recommended', label_dates
        )

        assert item in result['watched']
        assert item not in result['fresh']

    def test_categorizes_watched_via_isPlayed(self):
        """Test that items with isPlayed=True are categorized as watched even if not in watched_ids."""
        item = self._create_mock_item(999, is_played=True)
        watched_ids = set()  # Empty - item not in cache
        label_dates = {}

        result = categorize_labeled_items(
            [item], watched_ids, [], 'Recommended', label_dates
        )

        assert item in result['watched']
        assert item not in result['fresh']

    def test_categorizes_fresh_items(self):
        """Test that fresh items are correctly categorized."""
        item = self._create_mock_item(456)
        watched_ids = set()
        label_dates = {}

        result = categorize_labeled_items(
            [item], watched_ids, [], 'Recommended', label_dates
        )

        assert item in result['fresh']
        assert item not in result['watched']

    def test_categorizes_excluded_genre_items(self):
        """Test that items with excluded genres are categorized."""
        item = self._create_mock_item(789, genres=['horror', 'thriller'])
        watched_ids = set()
        label_dates = {}

        result = categorize_labeled_items(
            [item], watched_ids, ['horror'], 'Recommended', label_dates
        )

        assert item in result['excluded']

    def test_old_items_stay_fresh_no_staleness(self):
        """Test that old items stay fresh - staleness no longer removes items.

        Score-based eviction in _update_labels_by_rank handles rotation instead.
        """
        item = self._create_mock_item(999)
        watched_ids = set()

        # Set label date to 10 days ago - should NOT matter anymore
        old_date = (datetime.now() - timedelta(days=10)).isoformat()
        label_dates = {'999_Recommended': old_date}

        result = categorize_labeled_items(
            [item], watched_ids, [], 'Recommended', label_dates, stale_days=7
        )

        # Old items stay fresh - stale list is always empty now
        assert item in result['fresh']
        assert result['stale'] == []

    def test_fresh_item_gets_date_tracked(self):
        """Test that fresh items get their label date tracked."""
        item = self._create_mock_item(111)
        watched_ids = set()
        label_dates = {}

        categorize_labeled_items(
            [item], watched_ids, [], 'Recommended', label_dates
        )

        assert '111_Recommended' in label_dates

    def test_empty_list_returns_empty_categories(self):
        """Test with empty items list."""
        result = categorize_labeled_items(
            [], set(), [], 'Recommended', {}
        )

        assert result['fresh'] == []
        assert result['watched'] == []
        assert result['stale'] == []
        assert result['excluded'] == []


class TestRemoveLabelsFromItems:
    """Tests for remove_labels_from_items() function."""

    @patch('utils.labels.log_info')
    def test_removes_label_from_item(self, mock_log):
        """Test that label is removed from item."""
        item = Mock()
        item.ratingKey = 123
        item.title = "Test Movie"
        label_dates = {'123_Recommended': '2024-01-01'}

        remove_labels_from_items([item], 'Recommended', label_dates, 'test reason')

        item.removeLabel.assert_called_once_with('Recommended')
        assert '123_Recommended' not in label_dates

    @patch('utils.labels.log_info')
    def test_logs_reason_when_provided(self, mock_log):
        """Test that reason is logged."""
        item = Mock()
        item.ratingKey = 123
        item.title = "Test Movie"

        remove_labels_from_items([item], 'Recommended', {}, 'expired')

        mock_log.assert_called_once()
        assert 'expired' in mock_log.call_args[0][0]

    @patch('utils.labels.log_info')
    def test_no_log_when_no_reason(self, mock_log):
        """Test that no log when reason is empty."""
        item = Mock()
        item.ratingKey = 123
        item.title = "Test Movie"

        remove_labels_from_items([item], 'Recommended', {}, '')

        mock_log.assert_not_called()

    @patch('utils.labels.log_info')
    def test_removes_multiple_items(self, mock_log):
        """Test removing labels from multiple items."""
        items = [Mock(ratingKey=i, title=f"Movie {i}") for i in range(3)]
        label_dates = {f'{i}_Recommended': '2024-01-01' for i in range(3)}

        remove_labels_from_items(items, 'Recommended', label_dates, 'cleanup')

        for item in items:
            item.removeLabel.assert_called_once_with('Recommended')
        assert len(label_dates) == 0


class TestAddLabelsToItems:
    """Tests for add_labels_to_items() function."""

    def test_adds_label_to_item_without_label(self):
        """Test adding label to item that doesn't have it."""
        item = Mock()
        item.ratingKey = 123
        item.title = "Test Movie"
        item.labels = []
        label_dates = {}

        count = add_labels_to_items([item], 'Recommended', label_dates)

        item.addLabel.assert_called_once_with('Recommended')
        assert count == 1
        assert '123_Recommended' in label_dates

    def test_skips_item_with_existing_label(self):
        """Test that item with existing label is skipped."""
        item = Mock()
        item.ratingKey = 123
        item.title = "Test Movie"
        item.labels = [Mock(tag='Recommended')]
        label_dates = {}

        count = add_labels_to_items([item], 'Recommended', label_dates)

        item.addLabel.assert_not_called()
        assert count == 0

    def test_adds_labels_to_multiple_items(self):
        """Test adding labels to multiple items."""
        items = []
        for i in range(3):
            item = Mock()
            item.ratingKey = i
            item.title = f"Movie {i}"
            item.labels = []
            items.append(item)

        label_dates = {}

        count = add_labels_to_items(items, 'Recommended', label_dates)

        assert count == 3
        for item in items:
            item.addLabel.assert_called_once_with('Recommended')

    def test_mixed_existing_and_new_labels(self):
        """Test with mix of items with and without label."""
        item1 = Mock(ratingKey=1, title="Movie 1", labels=[])
        item2 = Mock(ratingKey=2, title="Movie 2", labels=[Mock(tag='Recommended')])
        item3 = Mock(ratingKey=3, title="Movie 3", labels=[])

        label_dates = {}

        count = add_labels_to_items([item1, item2, item3], 'Recommended', label_dates)

        assert count == 2
        item1.addLabel.assert_called_once()
        item2.addLabel.assert_not_called()
        item3.addLabel.assert_called_once()
