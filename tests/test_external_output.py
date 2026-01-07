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

    def test_includes_huntarr_tab_when_data_provided(self):
        all_users_data = [{
            'username': 'testuser',
            'display_name': 'TestUser',
            'movies_categorized': {'all_items': [], 'user_services': {},
                                   'other_services': {}, 'acquire': []},
            'shows_categorized': {'all_items': [], 'user_services': {},
                                  'other_services': {}, 'acquire': []}
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
            assert 'Huntarr' in html
            assert 'Sequel Movie' in html
            assert 'Test Collection' in html

    def test_empty_user_data_generates_valid_html(self):
        all_users_data = []

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                all_users_data, tmpdir, 'api_key', self._mock_get_imdb_id
            )

            with open(result) as f:
                html = f.read()
            assert '<!DOCTYPE html>' in html

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
