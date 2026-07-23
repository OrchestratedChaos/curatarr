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
    _load_imdb_cache,
    _save_imdb_cache,
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

    def test_default_filename_has_no_library_suffix(self):
        """#157 Phase 3.5 HARD invariant: no library_suffix arg (or '') keeps
        the exact legacy filename - required for single-library back-compat."""
        movies_categorized = {'user_services': {}, 'other_services': {}, 'acquire': []}
        shows_categorized = {'user_services': {}, 'other_services': {}, 'acquire': []}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_markdown(
                'testuser', 'TestUser', movies_categorized, shows_categorized, tmpdir
            )

            assert os.path.basename(result) == 'testuser_watchlist.md'

    def test_library_suffix_qualifies_filename(self):
        """#157 Phase 3.5: a non-empty library_suffix produces a
        library-qualified filename, so per-library fan-out runs for the same
        user don't overwrite each other."""
        movies_categorized = {'user_services': {}, 'other_services': {}, 'acquire': []}
        shows_categorized = {'user_services': {}, 'other_services': {}, 'acquire': []}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_markdown(
                'testuser', 'TestUser', movies_categorized, shows_categorized, tmpdir,
                library_suffix='_kids-movies'
            )

            assert os.path.basename(result) == 'testuser_kids-movies_watchlist.md'
            # Ignore-file instructions still reference the unqualified name -
            # the ignore list is shared across a user's libraries
            with open(result) as f:
                content = f.read()
            assert 'testuser_ignore.txt' in content


class TestHtmlEscaping:
    """Tests for the XSS fix: TMDB-derived (and locally-configured)
    fields must be HTML-escaped before being interpolated into
    watchlist.html - see web-audit finding #4."""

    def _mock_get_imdb_id(self, api_key, tmdb_id, media_type):
        return f'tt{tmdb_id}'

    def test_malicious_movie_title_is_escaped_not_executable(self):
        payload = '<script>alert(1)</script>'
        all_users_data = [{
            'username': 'user1',
            'display_name': 'User1',
            'movies_categorized': {
                'all_items': [
                    {'title': payload, 'year': '2024', 'rating': 7.0, 'score': 0.70,
                     'tmdb_id': 1, 'streaming_services': [], 'on_user_services': [],
                     'added_date': '2024-01-01T00:00:00'}
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
            assert payload not in html
            assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html

    def test_malicious_display_name_is_escaped(self):
        payload = '"><img src=x onerror=alert(1)>'
        all_users_data = [{
            'username': 'user1',
            'display_name': payload,
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
            assert payload not in html
            # The dangerous part is the payload breaking out into a real
            # <img> tag - html.escape() neutralizes that by escaping the
            # angle brackets/quotes, even though the literal substring
            # "onerror=alert(1)" (no HTML-meaningful chars of its own)
            # still appears as inert escaped text content.
            assert '<img' not in html
            assert '&lt;img' in html

    def test_malicious_sequel_collection_name_is_escaped(self):
        missing_sequels = [
            {'title': 'Normal Movie', 'year': '2024',
             'collection_name': '<script>alert(2)</script>',
             'owned_count': 1, 'total_count': 2, 'tmdb_id': 456,
             'streaming_services': [], 'on_user_services': []}
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                [], tmpdir, 'api_key', self._mock_get_imdb_id,
                missing_sequels=missing_sequels,
            )
            with open(result) as f:
                html = f.read()
            assert '<script>alert(2)</script>' not in html

    def test_malicious_horizon_status_is_escaped(self):
        horizon_movies = [
            {'title': 'Normal Movie', 'collection_name': 'Normal Collection',
             'tmdb_id': 789, 'release_date': '2026-06-15',
             'status': '"><script>alert(3)</script>'}
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                [], tmpdir, 'api_key', self._mock_get_imdb_id,
                horizon_movies=horizon_movies,
            )
            with open(result) as f:
                html = f.read()
            assert '<script>alert(3)</script>' not in html

    def test_normal_titles_still_render_readably(self):
        # Sanity check the fix doesn't mangle ordinary titles containing
        # characters that are legitimately part of HTML escaping's
        # domain (apostrophes, ampersands) but aren't attacks.
        all_users_data = [{
            'username': 'user1',
            'display_name': 'User1',
            'movies_categorized': {
                'all_items': [
                    {'title': "Tom & Jerry: It's a Wonderful Movie", 'year': '2024',
                     'rating': 7.0, 'score': 0.70, 'tmdb_id': 1,
                     'streaming_services': [], 'on_user_services': [],
                     'added_date': '2024-01-01T00:00:00'}
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
            assert 'Tom &amp; Jerry: It&#x27;s a Wonderful Movie' in html


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


class TestImdbCache:
    """Tests for _load_imdb_cache / _save_imdb_cache and the IMDB lookup/
    cache-write path inside generate_combined_html. These use an isolated
    temp root (output_dir nested one level below a fresh mkdtemp()) so the
    computed cache path (dirname(output_dir)/cache/imdb_ids_cache.json)
    can never collide with another test or a prior run - passing a bare
    tempfile.TemporaryDirectory() directly as output_dir would put the
    cache at the shared OS temp root and make cache-hit/miss behavior
    depend on test execution history."""

    def _mock_get_imdb_id(self, api_key, tmdb_id, media_type):
        return f'tt{tmdb_id}'

    def _isolated_output_dir(self, root):
        output_dir = os.path.join(root, 'watchlists')
        os.makedirs(output_dir, exist_ok=True)
        return output_dir

    def test_load_returns_empty_dict_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _load_imdb_cache(os.path.join(tmpdir, 'nope.json'))
        assert result == {}

    def test_load_returns_empty_dict_on_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, 'imdb_ids_cache.json')
            with open(cache_path, 'w') as f:
                f.write('{not valid json')

            result = _load_imdb_cache(cache_path)

        assert result == {}

    def test_save_then_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, 'nested', 'imdb_ids_cache.json')
            _save_imdb_cache(cache_path, {'123_movie': 'tt123'})

            result = _load_imdb_cache(cache_path)

        assert result == {'123_movie': 'tt123'}

    def test_save_swallows_ioerror(self):
        """A cache directory that can't be created shouldn't crash the run."""
        with patch('recommenders.external_output.os.makedirs', side_effect=IOError("disk full")):
            _save_imdb_cache('/some/path/imdb_ids_cache.json', {'a': 'b'})  # should not raise

    def test_new_lookup_fetches_and_persists_cache(self):
        """First-ever run against a fresh cache path takes the cache-miss
        path: calls get_imdb_id_func and writes the cache file."""
        with tempfile.TemporaryDirectory() as root:
            output_dir = self._isolated_output_dir(root)
            all_users_data = [{
                'username': 'user1',
                'display_name': 'User1',
                'movies_categorized': {
                    'all_items': [
                        {'title': 'Fresh Movie', 'year': '2024', 'rating': 7.0, 'score': 0.70,
                         'tmdb_id': 999001, 'streaming_services': [], 'on_user_services': [],
                         'added_date': '2024-01-01T00:00:00'}
                    ],
                    'user_services': {}, 'other_services': {}, 'acquire': []
                },
                'shows_categorized': {'all_items': [], 'user_services': {},
                                      'other_services': {}, 'acquire': []}
            }]

            get_imdb_id = Mock(side_effect=self._mock_get_imdb_id)
            result = generate_combined_html(all_users_data, output_dir, 'api_key', get_imdb_id)

            get_imdb_id.assert_called_once_with('api_key', 999001, 'movie')

            cache_path = os.path.join(root, 'cache', 'imdb_ids_cache.json')
            assert os.path.exists(cache_path)
            cache = _load_imdb_cache(cache_path)
            assert cache['999001_movie'] == 'tt999001'

            with open(result) as f:
                html = f.read()
            assert 'data-imdb="tt999001"' in html

    def test_cached_lookup_skips_refetch(self):
        """A tmdb_id already present in the on-disk cache is not re-fetched."""
        with tempfile.TemporaryDirectory() as root:
            output_dir = self._isolated_output_dir(root)
            cache_path = os.path.join(root, 'cache', 'imdb_ids_cache.json')
            _save_imdb_cache(cache_path, {'999002_movie': 'tt999002'})

            all_users_data = [{
                'username': 'user1',
                'display_name': 'User1',
                'movies_categorized': {
                    'all_items': [
                        {'title': 'Cached Movie', 'year': '2024', 'rating': 7.0, 'score': 0.70,
                         'tmdb_id': 999002, 'streaming_services': [], 'on_user_services': [],
                         'added_date': '2024-01-01T00:00:00'}
                    ],
                    'user_services': {}, 'other_services': {}, 'acquire': []
                },
                'shows_categorized': {'all_items': [], 'user_services': {},
                                      'other_services': {}, 'acquire': []}
            }]

            get_imdb_id = Mock(side_effect=self._mock_get_imdb_id)
            result = generate_combined_html(all_users_data, output_dir, 'api_key', get_imdb_id)

            get_imdb_id.assert_not_called()
            with open(result) as f:
                html = f.read()
            assert 'data-imdb="tt999002"' in html

    def test_missing_sequels_and_horizon_movies_also_use_pending_lookup_path(self):
        """The missing_sequels / horizon_movies TMDB-ID-collection branches
        (separate from the per-user movies/shows branch) also queue a
        pending lookup and land in the persisted cache."""
        with tempfile.TemporaryDirectory() as root:
            output_dir = self._isolated_output_dir(root)
            missing_sequels = [
                {'title': 'Sequel Movie', 'year': '2024', 'collection_name': 'Coll',
                 'owned_count': 1, 'total_count': 2, 'tmdb_id': 999003,
                 'streaming_services': [], 'on_user_services': []}
            ]
            horizon_movies = [
                {'title': 'Horizon Movie', 'collection_name': 'Coll2',
                 'tmdb_id': 999004, 'release_date': '2026-01-01', 'status': 'Planned'}
            ]

            get_imdb_id = Mock(side_effect=self._mock_get_imdb_id)
            generate_combined_html(
                [], output_dir, 'api_key', get_imdb_id,
                missing_sequels=missing_sequels, horizon_movies=horizon_movies,
            )

            assert get_imdb_id.call_count == 2
            cache_path = os.path.join(root, 'cache', 'imdb_ids_cache.json')
            cache = _load_imdb_cache(cache_path)
            assert cache['999003_movie'] == 'tt999003'
            assert cache['999004_movie'] == 'tt999004'


class TestCollectTmdbIdsInlineFallback:
    """generate_combined_html's internal collect_tmdb_ids_from_categorized
    falls back to walking user_services/other_services/acquire when
    'all_items' is absent or empty - covers the dict-of-lists branches."""

    def _mock_get_imdb_id(self, api_key, tmdb_id, media_type):
        return f'tt{tmdb_id}'

    def test_collects_from_user_services_and_other_services_dicts(self):
        all_users_data = [{
            'username': 'user1',
            'display_name': 'User1',
            'movies_categorized': {
                'user_services': {
                    'netflix': [{'title': 'A', 'year': '2024', 'rating': 7.0, 'score': 0.7,
                                 'tmdb_id': 999005, 'streaming_services': ['netflix'],
                                 'added_date': '2024-01-01T00:00:00'}]
                },
                'other_services': {
                    'hulu': [{'title': 'B', 'year': '2024', 'rating': 6.0, 'score': 0.6,
                              'tmdb_id': 999006, 'streaming_services': ['hulu'],
                              'added_date': '2024-01-01T00:00:00'}]
                },
                'acquire': [],
            },
            'shows_categorized': {'user_services': {}, 'other_services': {}, 'acquire': []},
        }]

        with tempfile.TemporaryDirectory() as root:
            output_dir = os.path.join(root, 'watchlists')
            os.makedirs(output_dir, exist_ok=True)
            get_imdb_id = Mock(side_effect=self._mock_get_imdb_id)

            generate_combined_html(all_users_data, output_dir, 'api_key', get_imdb_id)

            fetched_ids = {c.args[1] for c in get_imdb_id.call_args_list}
            assert {999005, 999006}.issubset(fetched_ids)


class TestGenerateMarkdownOtherServicesAndAcquireForShows:
    """generate_markdown's TV shows 'Other Services' and 'Acquire' branches
    (the movies-side equivalents are already covered by
    test_generates_markdown_file)."""

    def test_shows_other_services_and_acquire_sections_render(self):
        movies_categorized = {'user_services': {}, 'other_services': {}, 'acquire': []}
        shows_categorized = {
            'user_services': {},
            'other_services': {
                'hulu': [{'title': 'Other Show', 'year': '2023', 'rating': 7.2, 'score': 0.72,
                          'added_date': '2024-01-01T00:00:00'}]
            },
            'acquire': [
                {'title': 'Acquire Show', 'year': '2021', 'rating': 6.5, 'score': 0.65,
                 'added_date': '2024-01-01T00:00:00'}
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_markdown(
                'testuser', 'TestUser', movies_categorized, shows_categorized, tmpdir
            )
            with open(result) as f:
                content = f.read()

        assert 'Available on Other Services' in content
        assert 'Other Show' in content
        assert 'Acquire Show' in content
        assert '*Consider subscribing if many recommendations are on a single service*' in content
        assert '*Not available on any streaming service - need physical/digital copy*' in content


class TestSequelBadges:
    """render_sequels_table's is_animated / is_tv_movie badge branches."""

    def _mock_get_imdb_id(self, api_key, tmdb_id, media_type):
        return f'tt{tmdb_id}'

    def test_animated_and_tv_special_badges_render(self):
        missing_sequels = [
            {'title': 'Animated Sequel', 'year': '2024', 'collection_name': 'Coll',
             'owned_count': 1, 'total_count': 2, 'tmdb_id': 999007,
             'streaming_services': [], 'on_user_services': [],
             'is_animated': True, 'is_tv_movie': True}
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_combined_html(
                [], tmpdir, 'api_key', self._mock_get_imdb_id,
                missing_sequels=missing_sequels,
            )
            with open(result) as f:
                html = f.read()

        assert 'animated-badge">Animated</span>' in html
        assert 'tv-special-badge">TV Special</span>' in html
