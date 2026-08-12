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

"""
Tests for utils/labels.py - Label management functions.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from utils.labels import (
    DEFAULT_MOVIE_NAME_TEMPLATE,
    DEFAULT_TV_NAME_TEMPLATE,
    add_labels_to_items,
    build_label_name,
    categorize_labeled_items,
    remove_labels_from_items,
    render_collection_name,
)


class TestRenderCollectionName:
    """Tests for render_collection_name() - #267 custom collection-name
    templates."""

    def test_default_movie_template_matches_pre_267_format(self):
        result = render_collection_name(DEFAULT_MOVIE_NAME_TEMPLATE, "Alice", "movie")
        assert result == "🎬 Alice - Recommendation"

    def test_default_tv_template_matches_pre_267_format(self):
        result = render_collection_name(DEFAULT_TV_NAME_TEMPLATE, "Alice", "tv")
        assert result == "📺 Alice - Recommendation"

    def test_custom_template_with_user_placeholder(self):
        result = render_collection_name("Recommended movies - {user}", "Alice", "movie")
        assert result == "Recommended movies - Alice"

    def test_media_type_placeholder_movie_renders_title_case(self):
        result = render_collection_name("{media_type} picks - {user}", "Alice", "movie")
        assert result == "Movie picks - Alice"

    def test_media_type_placeholder_tv_renders_title_case(self):
        result = render_collection_name("{media_type} picks - {user}", "Alice", "tv")
        assert result == "TV picks - Alice"

    def test_template_with_no_placeholders_at_all(self):
        """A template that drops {user} entirely is still valid - not
        every install wants the username in the collection title."""
        result = render_collection_name("My Curated Picks", "Alice", "movie")
        assert result == "My Curated Picks"

    @patch("utils.labels.log_warning")
    def test_unknown_placeholder_falls_back_to_movie_default_and_warns(self, mock_warn):
        result = render_collection_name("Oops {typo}", "Alice", "movie")
        assert result == "🎬 Alice - Recommendation"
        mock_warn.assert_called_once()

    @patch("utils.labels.log_warning")
    def test_unknown_placeholder_falls_back_to_tv_default_and_warns(self, mock_warn):
        result = render_collection_name("Oops {typo}", "Alice", "tv")
        assert result == "📺 Alice - Recommendation"
        mock_warn.assert_called_once()

    @patch("utils.labels.log_warning")
    def test_bad_positional_placeholder_falls_back_and_warns(self, mock_warn):
        """A bare {} (positional, no args passed to .format) raises
        IndexError - must fall back exactly like an unknown named
        placeholder (KeyError) does."""
        result = render_collection_name("Oops {}", "Alice", "movie")
        assert result == "🎬 Alice - Recommendation"
        mock_warn.assert_called_once()


class TestBuildLabelName:
    """Tests for build_label_name() function."""

    def test_single_user(self):
        """Test building label name with a single user."""
        result = build_label_name(base_label="Recommended", users=["Jason"], single_user="Jason", append_usernames=True)

        assert result == "Recommended_Jason"

    def test_multiple_users(self):
        """Test building label name with multiple users."""
        result = build_label_name(
            base_label="Recommended", users=["Jason", "Sarah"], single_user=None, append_usernames=True
        )

        assert result == "Recommended_Jason_Sarah"

    def test_no_append_usernames(self):
        """Test that base label is returned when append_usernames=False."""
        result = build_label_name(
            base_label="Recommended", users=["Jason", "Sarah"], single_user=None, append_usernames=False
        )

        assert result == "Recommended"

    def test_empty_users_list(self):
        """Test with empty users list and no single_user."""
        result = build_label_name(base_label="Recommended", users=[], single_user=None, append_usernames=True)

        assert result == "Recommended"

    def test_special_characters_sanitized(self):
        """Test that special characters are replaced with underscores."""
        result = build_label_name(base_label="Recommended", users=[], single_user="John Doe", append_usernames=True)

        assert result == "Recommended_John_Doe"

    def test_special_chars_in_username(self):
        """Test various special characters in username."""
        result = build_label_name(base_label="Recommended", users=[], single_user="user@home!", append_usernames=True)

        assert result == "Recommended_user_home_"

    def test_single_user_overrides_users_list(self):
        """Test that single_user takes precedence over users list."""
        result = build_label_name(
            base_label="Recommended", users=["UserA", "UserB"], single_user="SingleUser", append_usernames=True
        )

        assert result == "Recommended_SingleUser"

    def test_whitespace_trimmed(self):
        """Test that whitespace is trimmed from usernames."""
        result = build_label_name(base_label="Recommended", users=[], single_user="  Jason  ", append_usernames=True)

        assert result == "Recommended_Jason"

    def test_different_base_labels(self):
        """Test with different base label names."""
        result = build_label_name(base_label="ToWatch", users=["User1"], single_user=None, append_usernames=True)

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

        result = categorize_labeled_items([item], watched_ids, [], "Recommended", label_dates)

        assert item in result["watched"]
        assert item not in result["fresh"]

    def test_isPlayed_alone_does_NOT_mark_an_item_watched(self):
        """
        isPlayed must be ignored here - it is the ADMIN's watched state.

        These items come from the admin Plex connection, which reports
        isPlayed for the token's owner regardless of which user is being
        processed. Trusting it evicted every other user's still-unwatched
        recommendations as "watched": measured on a real server, 141 of
        143 titles the admin had seen reported isPlayed=True through the
        admin connection and 0 through the actual user's own.

        Callers now pass a watched set that already unions the user's own
        history with their own Plex played state - see
        utils/plex.fetch_user_played_ids and the caller in
        recommenders/base.py's _remove_outdated_labels.
        """
        item = self._create_mock_item(999, is_played=True)

        result = categorize_labeled_items([item], set(), [], "Recommended", {})

        assert item in result["fresh"], "admin isPlayed leaked into another user's categorization"
        assert item not in result["watched"]

    def test_watched_set_still_governs(self):
        """The same item IS watched when the caller's set says so."""
        item = self._create_mock_item(999, is_played=True)

        result = categorize_labeled_items([item], {999}, [], "Recommended", {})

        assert item in result["watched"]
        assert item not in result["fresh"]

    def test_categorizes_fresh_items(self):
        """Test that fresh items are correctly categorized."""
        item = self._create_mock_item(456)
        watched_ids = set()
        label_dates = {}

        result = categorize_labeled_items([item], watched_ids, [], "Recommended", label_dates)

        assert item in result["fresh"]
        assert item not in result["watched"]

    def test_categorizes_excluded_genre_items(self):
        """Test that items with excluded genres are categorized."""
        item = self._create_mock_item(789, genres=["horror", "thriller"])
        watched_ids = set()
        label_dates = {}

        result = categorize_labeled_items([item], watched_ids, ["horror"], "Recommended", label_dates)

        assert item in result["excluded"]

    def test_old_items_stay_fresh_no_staleness(self):
        """Test that old items stay fresh - staleness no longer removes items.

        Score-based eviction in _update_labels_by_rank handles rotation instead.
        """
        item = self._create_mock_item(999)
        watched_ids = set()

        # Set label date to 10 days ago - should NOT matter anymore
        old_date = (datetime.now() - timedelta(days=10)).isoformat()
        label_dates = {"999_Recommended": old_date}

        result = categorize_labeled_items([item], watched_ids, [], "Recommended", label_dates, stale_days=7)

        # Old items stay fresh - stale list is always empty now
        assert item in result["fresh"]
        assert result["stale"] == []

    def test_fresh_item_gets_date_tracked(self):
        """Test that fresh items get their label date tracked."""
        item = self._create_mock_item(111)
        watched_ids = set()
        label_dates = {}

        categorize_labeled_items([item], watched_ids, [], "Recommended", label_dates)

        assert "111_Recommended" in label_dates

    def test_empty_list_returns_empty_categories(self):
        """Test with empty items list."""
        result = categorize_labeled_items([], set(), [], "Recommended", {})

        assert result["fresh"] == []
        assert result["watched"] == []
        assert result["stale"] == []
        assert result["excluded"] == []


class TestRemoveLabelsFromItems:
    """Tests for remove_labels_from_items() function."""

    @patch("utils.labels.log_info")
    def test_removes_label_from_item(self, mock_log):
        """Test that label is removed from item."""
        item = Mock()
        item.ratingKey = 123
        item.title = "Test Movie"
        label_dates = {"123_Recommended": "2024-01-01"}

        remove_labels_from_items([item], "Recommended", label_dates, "test reason")

        item.removeLabel.assert_called_once_with("Recommended")
        assert "123_Recommended" not in label_dates

    @patch("utils.labels.log_info")
    def test_logs_reason_when_provided(self, mock_log):
        """Test that reason is logged."""
        item = Mock()
        item.ratingKey = 123
        item.title = "Test Movie"

        remove_labels_from_items([item], "Recommended", {}, "expired")

        mock_log.assert_called_once()
        assert "expired" in mock_log.call_args[0][0]

    @patch("utils.labels.log_info")
    def test_no_log_when_no_reason(self, mock_log):
        """Test that no log when reason is empty."""
        item = Mock()
        item.ratingKey = 123
        item.title = "Test Movie"

        remove_labels_from_items([item], "Recommended", {}, "")

        mock_log.assert_not_called()

    @patch("utils.labels.log_info")
    def test_removes_multiple_items(self, mock_log):
        """Test removing labels from multiple items."""
        items = [Mock(ratingKey=i, title=f"Movie {i}") for i in range(3)]
        label_dates = {f"{i}_Recommended": "2024-01-01" for i in range(3)}

        remove_labels_from_items(items, "Recommended", label_dates, "cleanup")

        for item in items:
            item.removeLabel.assert_called_once_with("Recommended")
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

        count = add_labels_to_items([item], "Recommended", label_dates)

        item.addLabel.assert_called_once_with("Recommended")
        assert count == 1
        assert "123_Recommended" in label_dates

    def test_skips_item_with_existing_label(self):
        """Test that item with existing label is skipped."""
        item = Mock()
        item.ratingKey = 123
        item.title = "Test Movie"
        item.labels = [Mock(tag="Recommended")]
        label_dates = {}

        count = add_labels_to_items([item], "Recommended", label_dates)

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

        count = add_labels_to_items(items, "Recommended", label_dates)

        assert count == 3
        for item in items:
            item.addLabel.assert_called_once_with("Recommended")

    def test_mixed_existing_and_new_labels(self):
        """Test with mix of items with and without label."""
        item1 = Mock(ratingKey=1, title="Movie 1", labels=[])
        item2 = Mock(ratingKey=2, title="Movie 2", labels=[Mock(tag="Recommended")])
        item3 = Mock(ratingKey=3, title="Movie 3", labels=[])

        label_dates = {}

        count = add_labels_to_items([item1, item2, item3], "Recommended", label_dates)

        assert count == 2
        item1.addLabel.assert_called_once()
        item2.addLabel.assert_not_called()
        item3.addLabel.assert_called_once()
