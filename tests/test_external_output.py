"""Tests for recommenders/external_output.py - HTML generation and streaming icons"""

import pytest
from unittest.mock import Mock, patch, mock_open
from datetime import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recommenders.external_output import (
    render_streaming_icons,
    generate_combined_html,
    generate_markdown,
    SERVICE_SHORT_NAMES,
)


class TestRenderStreamingIcons:
    """Tests for render_streaming_icons function"""

    def test_returns_acquire_when_no_services(self):
        result = render_streaming_icons([], [])
        assert 'Acquire' in result
        assert 'streaming-icon acquire' in result

    def test_returns_acquire_when_services_is_none(self):
        result = render_streaming_icons(None, [])
        assert 'Acquire' in result

    def test_renders_single_service(self):
        result = render_streaming_icons(['netflix'], [])
        assert 'netflix' in result
        assert 'streaming-icon' in result
        assert 'Acquire' not in result

    def test_renders_multiple_services(self):
        result = render_streaming_icons(['netflix', 'hulu', 'disney_plus'], [])
        assert 'netflix' in result
        assert 'hulu' in result
        assert 'disney_plus' in result

    def test_highlights_user_services_with_gold_border(self):
        result = render_streaming_icons(['netflix', 'hulu'], ['netflix'])
        assert 'user-service' in result
        # Netflix should have user-service class
        assert 'streaming-icon netflix user-service' in result

    def test_user_service_not_in_list_no_highlight(self):
        result = render_streaming_icons(['hulu'], ['netflix'])
        assert 'user-service' not in result

    def test_all_user_services_highlighted(self):
        result = render_streaming_icons(['netflix', 'hulu'], ['netflix', 'hulu'])
        # Both should have user-service class
        assert result.count('user-service') == 2

    def test_uses_short_names_from_mapping(self):
        result = render_streaming_icons(['disney_plus'], [])
        # Should use the short name from SERVICE_SHORT_NAMES
        short_name = SERVICE_SHORT_NAMES.get('disney_plus', 'disney_plus')
        assert short_name in result

    def test_unknown_service_uses_title_case(self):
        result = render_streaming_icons(['unknown_service'], [])
        # Unknown services should use title case
        assert 'Unknown_Service' in result or 'unknown_service' in result

    def test_shows_rent_badge_when_no_streaming(self):
        """When no streaming but rent available, show rent badge."""
        result = render_streaming_icons([], [], rent_services=['Apple TV', 'Amazon'])
        assert 'Rent:' in result
        assert 'Apple TV' in result
        assert 'streaming-icon rent' in result
        assert 'Acquire' not in result

    def test_shows_buy_badge_when_no_streaming_or_rent(self):
        """When no streaming or rent but buy available, show buy badge."""
        result = render_streaming_icons([], [], rent_services=[], buy_services=['Google Play'])
        assert 'Buy:' in result
        assert 'Google Play' in result
        assert 'streaming-icon buy' in result
        assert 'Acquire' not in result

    def test_rent_takes_priority_over_buy(self):
        """Rent badge shown even if buy also available."""
        result = render_streaming_icons([], [], rent_services=['Apple TV'], buy_services=['Google Play'])
        assert 'Rent:' in result
        assert 'Buy:' not in result

    def test_streaming_takes_priority_over_rent(self):
        """Streaming badges shown even if rent available."""
        result = render_streaming_icons(['netflix'], [], rent_services=['Apple TV'])
        assert 'netflix' in result
        assert 'Rent:' not in result

    def test_rent_badge_limits_display_shows_all_in_tooltip(self):
        """Rent badge shows 2 providers in display, all in tooltip."""
        result = render_streaming_icons([], [], rent_services=['A', 'B', 'C', 'D', 'E'])
        # Display shows first 2 + count
        assert 'Rent: A, B +3' in result
        # Tooltip shows all
        assert 'title="Available: A, B, C, D, E"' in result


class TestServiceShortNames:
    """Tests for SERVICE_SHORT_NAMES constant"""

    def test_contains_major_services(self):
        assert 'netflix' in SERVICE_SHORT_NAMES
        assert 'hulu' in SERVICE_SHORT_NAMES
        assert 'disney_plus' in SERVICE_SHORT_NAMES
        assert 'amazon_prime' in SERVICE_SHORT_NAMES

    def test_short_names_are_concise(self):
        for service, short_name in SERVICE_SHORT_NAMES.items():
            assert len(short_name) <= 10, f"{service} short name too long: {short_name}"


class TestGenerateCombinedHtml:
    """Tests for generate_combined_html function"""

    def _mock_get_imdb_id(self, api_key, tmdb_id, media_type):
        """Mock IMDB ID fetcher."""
        return f'tt{tmdb_id}'

    def test_generates_html_with_tabs(self):
        all_users_data = [{
            'username': 'testuser',
            'display_name': 'TestUser',
            'movies_categorized': {
                'all_items': [
                    {'title': 'Movie 1', 'year': '2024', 'rating': 7.5, 'score': 0.75,
                     'tmdb_id': 123, 'streaming_services': ['netflix'],
                     'on_user_services': ['netflix'], 'added_date': '2024-01-01T00:00:00'}
                ],
                'user_services': {}, 'other_services': {}, 'acquire': []
            },
            'shows_categorized': {
                'all_items': [],
                'user_services': {}, 'other_services': {}, 'acquire': []
            }
        }]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id
            )

            assert os.path.exists(result)
            with open(result) as f:
                html = f.read()
            assert '<!DOCTYPE html>' in html
            assert 'TestUser' in html
            assert 'Movie 1' in html

    def test_includes_sequel_huntarr_tab_when_data_provided(self):
        all_users_data = [{
            'username': 'testuser',
            'display_name': 'TestUser',
            'movies_categorized': {'all_items': [], 'user_services': {},
                                   'other_services': {}, 'acquire': []},
            'shows_categorized': {'all_items': [], 'user_services': {},
                                  'other_services': {}, 'acquire': []},
            'user_services': []
        }]
        missing_sequels = [
            {'title': 'Sequel Movie', 'year': '2024', 'collection_name': 'Test Collection',
             'owned_count': 2, 'total_count': 3, 'tmdb_id': 456,
             'streaming_services': [], 'on_user_services': []}
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id,
                missing_sequels=missing_sequels
            )

            with open(result) as f:
                html = f.read()
            assert 'Sequel Huntarr' in html
            assert 'Sequel Movie' in html
            assert 'Test Collection' in html

    def test_includes_horizon_huntarr_tab_when_data_provided(self):
        all_users_data = [{
            'username': 'testuser',
            'display_name': 'TestUser',
            'movies_categorized': {'all_items': [], 'user_services': {},
                                   'other_services': {}, 'acquire': []},
            'shows_categorized': {'all_items': [], 'user_services': {},
                                  'other_services': {}, 'acquire': []},
            'user_services': []
        }]
        horizon_movies = [
            {'title': 'Future Movie', 'collection_name': 'Future Collection',
             'tmdb_id': 789, 'release_date': '2026-06-15', 'status': 'In Production'}
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id,
                horizon_movies=horizon_movies
            )

            with open(result) as f:
                html = f.read()
            assert 'Horizon Huntarr' in html
            assert 'Future Movie' in html
            assert 'Future Collection' in html
            assert 'In Production' in html

    def test_empty_user_data_generates_valid_html(self):
        all_users_data = []

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id
            )

            with open(result) as f:
                html = f.read()
            assert '<!DOCTYPE html>' in html

    def test_huntarr_only_mode_activates_first_tab(self):
        """When no user data, first huntarr tab should be active."""
        all_users_data = []
        missing_sequels = [
            {'title': 'Sequel Movie', 'year': '2024', 'collection_name': 'Test Collection',
             'owned_count': 2, 'total_count': 3, 'tmdb_id': 456,
             'streaming_services': [], 'on_user_services': []}
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id,
                missing_sequels=missing_sequels
            )

            with open(result) as f:
                html = f.read()
            # First huntarr tab should be active
            assert 'tab-btn active' in html
            assert 'data-user="sequel-huntarr"' in html
            # Panel should also be active
            assert 'tab-panel active' in html

    def test_html_includes_sortable_columns(self):
        all_users_data = [{
            'username': 'user1',
            'display_name': 'User1',
            'movies_categorized': {
                'all_items': [
                    {'title': 'A Movie', 'year': '2024', 'rating': 8.0, 'score': 0.80,
                     'tmdb_id': 1, 'streaming_services': ['netflix'],
                     'on_user_services': [], 'added_date': '2024-01-01T00:00:00'}
                ],
                'user_services': {}, 'other_services': {}, 'acquire': []
            },
            'shows_categorized': {'all_items': [], 'user_services': {},
                                  'other_services': {}, 'acquire': []}
        }]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id
            )

            with open(result) as f:
                html = f.read()
            assert 'sortable' in html
            assert 'Title' in html
            assert 'Score' in html
            assert 'Streaming' in html

    def test_html_includes_streaming_icons_css(self):
        all_users_data = [{
            'username': 'user1',
            'display_name': 'User1',
            'movies_categorized': {'all_items': [], 'user_services': {},
                                   'other_services': {}, 'acquire': []},
            'shows_categorized': {'all_items': [], 'user_services': {},
                                  'other_services': {}, 'acquire': []}
        }]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id
            )

            with open(result) as f:
                html = f.read()
            assert '.streaming-icon' in html
            assert '.streaming-icon.netflix' in html
            assert '.streaming-icon.user-service' in html

    def test_html_includes_tv_shows_section(self):
        """Test that TV shows are rendered in the HTML output."""
        all_users_data = [{
            'username': 'user1',
            'display_name': 'User1',
            'movies_categorized': {'all_items': [], 'user_services': {},
                                   'other_services': {}, 'acquire': []},
            'shows_categorized': {
                'all_items': [
                    {'title': 'Breaking Bad', 'year': '2008', 'rating': 9.5, 'score': 0.95,
                     'tmdb_id': 1396, 'streaming_services': ['netflix'],
                     'on_user_services': ['netflix'], 'added_date': '2024-01-01T00:00:00'}
                ],
                'user_services': {}, 'other_services': {}, 'acquire': []
            }
        }]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id
            )

            with open(result) as f:
                html = f.read()
            assert 'TV Shows to Watch' in html
            assert 'Breaking Bad' in html

    def test_handles_items_without_tmdb_id(self):
        """Test that items without tmdb_id are handled gracefully."""
        all_users_data = [{
            'username': 'user1',
            'display_name': 'User1',
            'movies_categorized': {
                'all_items': [
                    {'title': 'Movie With ID', 'year': '2024', 'rating': 7.0, 'score': 0.70,
                     'tmdb_id': 123, 'streaming_services': [], 'on_user_services': [],
                     'added_date': '2024-01-01T00:00:00'},
                    {'title': 'Movie Without ID', 'year': '2024', 'rating': 6.0, 'score': 0.60,
                     'streaming_services': [], 'on_user_services': [],
                     'added_date': '2024-01-01T00:00:00'}
                ],
                'user_services': {}, 'other_services': {}, 'acquire': []
            },
            'shows_categorized': {'all_items': [], 'user_services': {},
                                  'other_services': {}, 'acquire': []}
        }]
        missing_sequels = [
            {'title': 'Sequel Without ID', 'year': '2024', 'collection_name': 'Test',
             'owned_count': 1, 'total_count': 2, 'streaming_services': [], 'on_user_services': []}
        ]
        horizon_movies = [
            {'title': 'Future Without ID', 'collection_name': 'Future',
             'release_date': '2026-12-15', 'status': 'Planned'}
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id,
                missing_sequels=missing_sequels, horizon_movies=horizon_movies
            )
            assert os.path.exists(result)


class TestGenerateMarkdown:
    """Tests for generate_markdown function"""

    def test_generates_markdown_file(self):
        """Test that markdown file is created with correct structure."""
        movies_categorized = {
            'user_services': {
                'netflix': [
                    {'title': 'Movie A', 'year': '2024', 'rating': 8.0, 'score': 0.80,
                     'added_date': '2024-01-01T00:00:00'}
                ]
            },
            'other_services': {
                'hulu': [
                    {'title': 'Movie B', 'year': '2023', 'rating': 7.5, 'score': 0.75,
                     'added_date': '2024-01-01T00:00:00'}
                ]
            },
            'acquire': [
                {'title': 'Movie C', 'year': '2022', 'rating': 7.0, 'score': 0.70,
                 'added_date': '2024-01-01T00:00:00'}
            ]
        }
        shows_categorized = {
            'user_services': {
                'netflix': [
                    {'title': 'Show A', 'year': '2024', 'rating': 9.0, 'score': 0.90,
                     'added_date': '2024-01-01T00:00:00'}
                ]
            },
            'other_services': {},
            'acquire': []
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_markdown(
                'testuser', 'TestUser', movies_categorized, shows_categorized, tmpdir
            )

            assert os.path.exists(result)
            with open(result) as f:
                content = f.read()

            assert '# Watchlist for TestUser' in content
            assert 'Movies to Watch' in content
            assert 'TV Shows to Watch' in content
            assert 'Movie A' in content
            assert 'Movie B' in content
            assert 'Movie C' in content
            assert 'Show A' in content
            assert 'Available on Your Services' in content
            assert 'Available on Other Services' in content
            assert 'Acquire' in content

    def test_empty_categories_skipped(self):
        """Test that empty categories don't appear in output."""
        movies_categorized = {'user_services': {}, 'other_services': {}, 'acquire': []}
        shows_categorized = {'user_services': {}, 'other_services': {}, 'acquire': []}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_markdown(
                'emptyuser', 'EmptyUser', movies_categorized, shows_categorized, tmpdir
            )

            with open(result) as f:
                content = f.read()

            assert 'Movies to Watch' not in content
            assert 'TV Shows to Watch' not in content


class TestHtmlSorting:
    """Tests for HTML table sorting functionality"""

    def _mock_get_imdb_id(self, api_key, tmdb_id, media_type):
        return f'tt{tmdb_id}'

    def test_html_includes_sort_javascript(self):
        all_users_data = [{
            'username': 'user1',
            'display_name': 'User1',
            'movies_categorized': {'all_items': [], 'user_services': {},
                                   'other_services': {}, 'acquire': []},
            'shows_categorized': {'all_items': [], 'user_services': {},
                                  'other_services': {}, 'acquire': []}
        }]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id
            )

            with open(result) as f:
                html = f.read()
            # Check for sort-related JavaScript
            assert 'querySelectorAll' in html
            assert 'sortable' in html
            assert 'localeCompare' in html

    def test_html_handles_percentage_sorting(self):
        all_users_data = [{
            'username': 'user1',
            'display_name': 'User1',
            'movies_categorized': {'all_items': [], 'user_services': {},
                                   'other_services': {}, 'acquire': []},
            'shows_categorized': {'all_items': [], 'user_services': {},
                                  'other_services': {}, 'acquire': []}
        }]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id
            )

            with open(result) as f:
                html = f.read()
            # Check for percentage handling in sort logic
            assert "endsWith('%')" in html
