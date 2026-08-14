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
Tests for utils/plex.py - Plex extraction and utility functions.

Also covers utils/plex_policy.py (rating/label policy split out of
utils/plex.py - see that module's docstring) rather than a separate
test file, since this suite predates that split and stays organized by
"Plex-related behavior", not by which of the two modules a given
function now lives in.
"""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import plexapi.exceptions
import pytest
import requests

from utils.plex import (
    extract_genres,
    extract_ids_from_guids,
    extract_rating,
    find_plex_movie,
    get_current_users,
    get_excluded_genres_for_user,
    get_library_imdb_ids,
    get_streaming_services_for_user,
)
from utils.plex_policy import apply_user_label_restrictions


class TestExtractGenres:
    """Tests for extract_genres() function."""

    def test_extract_genres_with_tag_objects(self):
        """Test extracting genres from Plex Genre objects with .tag attribute."""
        # Mock a Plex item with Genre objects
        mock_genre1 = MagicMock()
        mock_genre1.tag = "Action"
        mock_genre2 = MagicMock()
        mock_genre2.tag = "Comedy"

        mock_item = MagicMock()
        mock_item.genres = [mock_genre1, mock_genre2]

        result = extract_genres(mock_item)

        assert result == ["action", "comedy"]

    def test_extract_genres_with_string_list(self):
        """Test extracting genres when genres is a list of strings."""
        mock_item = MagicMock()
        mock_item.genres = ["Drama", "Thriller"]

        result = extract_genres(mock_item)

        assert result == ["drama", "thriller"]

    def test_extract_genres_empty_list(self):
        """Test extracting genres when genres list is empty."""
        mock_item = MagicMock()
        mock_item.genres = []

        result = extract_genres(mock_item)

        assert result == []

    def test_extract_genres_no_genres_attr(self):
        """Test extracting genres when item has no genres attribute."""
        mock_item = MagicMock(spec=[])  # No attributes

        result = extract_genres(mock_item)

        assert result == []

    def test_extract_genres_none_genres(self):
        """Test extracting genres when genres is None."""
        mock_item = MagicMock()
        mock_item.genres = None

        result = extract_genres(mock_item)

        assert result == []

    def test_extract_genres_mixed_case(self):
        """Test that genres are normalized to lowercase."""
        mock_genre = MagicMock()
        mock_genre.tag = "Sci-Fi & Fantasy"

        mock_item = MagicMock()
        mock_item.genres = [mock_genre]

        result = extract_genres(mock_item)

        assert result == ["sci-fi & fantasy"]


class TestExtractIdsFromGuids:
    """Tests for extract_ids_from_guids() function."""

    def test_extract_both_ids(self):
        """Test extracting both IMDB and TMDB IDs."""
        mock_guid1 = MagicMock()
        mock_guid1.id = "imdb://tt1234567"
        mock_guid2 = MagicMock()
        mock_guid2.id = "tmdb://12345"

        mock_item = MagicMock()
        mock_item.guids = [mock_guid1, mock_guid2]

        result = extract_ids_from_guids(mock_item)

        assert result == {"imdb_id": "tt1234567", "tmdb_id": 12345}

    def test_extract_imdb_only(self):
        """Test extracting only IMDB ID."""
        mock_guid = MagicMock()
        mock_guid.id = "imdb://tt9876543"

        mock_item = MagicMock()
        mock_item.guids = [mock_guid]

        result = extract_ids_from_guids(mock_item)

        assert result["imdb_id"] == "tt9876543"
        assert result["tmdb_id"] is None

    def test_extract_tmdb_only(self):
        """Test extracting only TMDB ID."""
        mock_guid = MagicMock()
        mock_guid.id = "tmdb://67890"

        mock_item = MagicMock()
        mock_item.guids = [mock_guid]

        result = extract_ids_from_guids(mock_item)

        assert result["imdb_id"] is None
        assert result["tmdb_id"] == 67890

    def test_extract_themoviedb_format(self):
        """Test extracting TMDB ID with 'themoviedb://' format."""
        mock_guid = MagicMock()
        mock_guid.id = "themoviedb://11111"

        mock_item = MagicMock()
        mock_item.guids = [mock_guid]

        result = extract_ids_from_guids(mock_item)

        assert result["tmdb_id"] == 11111

    def test_extract_no_guids_attr(self):
        """Test when item has no guids attribute."""
        mock_item = MagicMock(spec=[])

        result = extract_ids_from_guids(mock_item)

        assert result == {"imdb_id": None, "tmdb_id": None}

    def test_extract_empty_guids(self):
        """Test when guids list is empty."""
        mock_item = MagicMock()
        mock_item.guids = []

        result = extract_ids_from_guids(mock_item)

        assert result == {"imdb_id": None, "tmdb_id": None}

    def test_extract_imdb_with_query_params(self):
        """Test extracting IMDB ID when URL has query parameters."""
        mock_guid = MagicMock()
        mock_guid.id = "imdb://tt1234567?lang=en"

        mock_item = MagicMock()
        mock_item.guids = [mock_guid]

        result = extract_ids_from_guids(mock_item)

        assert result["imdb_id"] == "tt1234567"


class TestExtractRating:
    """Tests for extract_rating() function."""

    def test_extract_user_rating_preferred(self):
        """Test that userRating is preferred when prefer_user_rating=True."""
        mock_item = MagicMock()
        mock_item.userRating = 8.5
        mock_item.audienceRating = 7.0

        result = extract_rating(mock_item, prefer_user_rating=True)

        assert result == 8.5

    def test_extract_audience_rating_preferred(self):
        """Test that audienceRating is preferred when prefer_user_rating=False."""
        mock_item = MagicMock()
        mock_item.userRating = 8.5
        mock_item.audienceRating = 7.0

        result = extract_rating(mock_item, prefer_user_rating=False)

        assert result == 7.0

    def test_extract_fallback_to_audience(self):
        """Test fallback to audienceRating when userRating is None."""
        mock_item = MagicMock()
        mock_item.userRating = None
        mock_item.audienceRating = 6.5

        result = extract_rating(mock_item, prefer_user_rating=True)

        assert result == 6.5

    def test_extract_no_ratings(self):
        """Test when no ratings are available."""
        mock_item = MagicMock()
        mock_item.userRating = None
        mock_item.audienceRating = None
        mock_item.ratings = []

        result = extract_rating(mock_item)

        assert result == 0.0

    def test_extract_rating_no_attrs(self):
        """Test when item has no rating attributes."""
        mock_item = MagicMock(spec=[])

        result = extract_rating(mock_item)

        assert result == 0.0


class TestGetCurrentUsers:
    """Tests for get_current_users() function."""

    def test_returns_plex_users(self):
        """Test that plex_users are returned when present."""
        users = {"plex_users": ["alice", "bob"], "managed_users": ["charlie"]}
        result = get_current_users(users)

        assert "alice" in result
        assert "bob" in result

    def test_returns_managed_users_when_no_plex_users(self):
        """Test that managed_users are used when plex_users is empty."""
        users = {"plex_users": [], "managed_users": ["admin", "guest"]}
        result = get_current_users(users)

        assert "admin" in result
        assert "guest" in result


class TestGetExcludedGenresForUser:
    """Tests for get_excluded_genres_for_user() function."""

    def test_returns_base_genres(self):
        """Test that base excluded genres are returned."""
        base_genres = {"horror", "gore"}
        user_prefs = {}

        result = get_excluded_genres_for_user(base_genres, user_prefs)

        assert "horror" in result
        assert "gore" in result

    def test_adds_user_specific_exclusions(self):
        """Test that user-specific exclusions are added."""
        base_genres = {"horror"}
        user_prefs = {"john": {"exclude_genres": ["comedy", "romance"]}}

        result = get_excluded_genres_for_user(base_genres, user_prefs, username="john")

        assert "horror" in result
        assert "comedy" in result
        assert "romance" in result

    def test_empty_base_and_user_prefs(self):
        """Test with no exclusions."""
        result = get_excluded_genres_for_user(set(), {})

        assert len(result) == 0

    def test_no_username_returns_base_only(self):
        """Test that no username returns base genres only."""
        base_genres = {"horror"}
        user_prefs = {"john": {"exclude_genres": ["comedy"]}}

        result = get_excluded_genres_for_user(base_genres, user_prefs)

        assert "horror" in result
        assert "comedy" not in result


class TestGetStreamingServicesForUser:
    """Tests for get_streaming_services_for_user() (PR2 audit remediation:
    per-user streaming_services was previously dead config in
    recommenders/external.py - see that module's CHANGELOG entry). Mirrors
    TestGetExcludedGenresForUser above one-for-one: same merge (not
    override) semantics, global list UNIONed with any per-user override."""

    def test_returns_global_services_with_no_username(self):
        global_services = ["netflix", "hulu"]
        user_prefs = {}

        result = get_streaming_services_for_user(global_services, user_prefs)

        assert result == ["netflix", "hulu"]

    def test_merges_user_specific_services_onto_global(self):
        global_services = ["netflix"]
        user_prefs = {"john": {"streaming_services": ["hulu", "disney_plus"]}}

        result = get_streaming_services_for_user(global_services, user_prefs, username="john")

        assert result == ["netflix", "hulu", "disney_plus"]

    def test_does_not_duplicate_a_service_in_both_lists(self):
        global_services = ["netflix", "hulu"]
        user_prefs = {"john": {"streaming_services": ["hulu", "disney_plus"]}}

        result = get_streaming_services_for_user(global_services, user_prefs, username="john")

        assert result == ["netflix", "hulu", "disney_plus"]

    def test_empty_global_and_user_prefs(self):
        result = get_streaming_services_for_user([], {})

        assert result == []

    def test_no_username_returns_global_only(self):
        global_services = ["netflix"]
        user_prefs = {"john": {"streaming_services": ["hulu"]}}

        result = get_streaming_services_for_user(global_services, user_prefs)

        assert result == ["netflix"]

    def test_user_with_no_override_gets_global_only(self):
        """A configured user with no personal streaming_services override
        behaves exactly as before this fix - global list only."""
        global_services = ["netflix", "hulu"]
        user_prefs = {"john": {"display_name": "John"}}

        result = get_streaming_services_for_user(global_services, user_prefs, username="john")

        assert result == ["netflix", "hulu"]


class TestFindPlexMovie:
    """Tests for find_plex_movie() function."""

    def test_finds_exact_match(self):
        """Test finding movie with exact title match."""
        mock_movie = Mock()
        mock_movie.title = "The Matrix"
        mock_movie.year = 1999

        mock_section = Mock()
        mock_section.search.return_value = [mock_movie]

        result = find_plex_movie(mock_section, "The Matrix", 1999)

        assert result == mock_movie

    def test_finds_match_without_year(self):
        """Test finding movie without specifying year."""
        mock_movie = Mock()
        mock_movie.title = "Inception"
        mock_movie.year = 2010

        mock_section = Mock()
        mock_section.search.return_value = [mock_movie]

        result = find_plex_movie(mock_section, "Inception")

        assert result == mock_movie

    def test_returns_none_when_not_found(self):
        """Test that None is returned when movie not found."""
        mock_section = Mock()
        mock_section.search.return_value = []
        mock_section.all.return_value = []  # Also mock .all()

        result = find_plex_movie(mock_section, "Nonexistent Movie")

        assert result is None

    def test_filters_by_year(self):
        """Test that year is used to filter results."""
        mock_movie_old = Mock()
        mock_movie_old.title = "Movie"
        mock_movie_old.year = 2000

        mock_movie_new = Mock()
        mock_movie_new.title = "Movie"
        mock_movie_new.year = 2020

        mock_section = Mock()
        mock_section.search.return_value = [mock_movie_old, mock_movie_new]

        result = find_plex_movie(mock_section, "Movie", 2020)

        assert result == mock_movie_new

    def test_fuzzy_match_via_all(self):
        """Test fuzzy matching when search fails."""
        mock_movie = Mock()
        mock_movie.title = "Avatar 4K"
        mock_movie.year = 2009

        mock_section = Mock()
        mock_section.search.return_value = []
        mock_section.all.return_value = [mock_movie]

        result = find_plex_movie(mock_section, "Avatar", 2009)

        assert result == mock_movie


class TestGetLibraryImdbIds:
    """Tests for get_library_imdb_ids() function."""

    def test_extracts_imdb_ids(self):
        """Test extracting IMDb IDs from library."""
        mock_guid = Mock()
        mock_guid.id = "imdb://tt1234567"

        mock_item = Mock()
        mock_item.guids = [mock_guid]

        mock_section = Mock()
        mock_section.all.return_value = [mock_item]

        result = get_library_imdb_ids(mock_section)

        assert "tt1234567" in result

    def test_handles_items_without_imdb(self):
        """Test handling items without IMDb ID."""
        mock_guid = Mock()
        mock_guid.id = "tmdb://12345"

        mock_item = Mock()
        mock_item.guids = [mock_guid]

        mock_section = Mock()
        mock_section.all.return_value = [mock_item]

        result = get_library_imdb_ids(mock_section)

        assert len(result) == 0

    def test_returns_set(self):
        """Test that result is a set."""
        mock_section = Mock()
        mock_section.all.return_value = []

        result = get_library_imdb_ids(mock_section)

        assert isinstance(result, set)


class TestUpdatePlexCollection:
    """Tests for update_plex_collection() function."""

    def test_returns_false_for_empty_items(self):
        """Test that empty items list returns False."""
        from utils.plex import update_plex_collection

        mock_section = Mock()
        result = update_plex_collection(mock_section, "Test Collection", [])

        assert result is False

    def test_creates_new_collection(self):
        """Test creating a new collection when none exists."""
        from utils.plex import update_plex_collection

        mock_section = Mock()
        mock_section.collections.return_value = []

        mock_item = Mock()
        mock_item.title = "Test Movie"

        result = update_plex_collection(mock_section, "New Collection", [mock_item])

        assert result is True
        mock_section.createCollection.assert_called_once()

    def test_updates_existing_collection(self):
        """Test updating an existing collection."""
        from utils.plex import update_plex_collection

        mock_existing = Mock()
        mock_existing.title = "Existing Collection"
        mock_existing.items.return_value = [Mock()]

        mock_section = Mock()
        mock_section.collections.return_value = [mock_existing]

        mock_item = Mock()
        mock_item.title = "New Movie"

        result = update_plex_collection(mock_section, "Existing Collection", [mock_item])

        assert result is True
        mock_existing.removeItems.assert_called_once()
        mock_existing.addItems.assert_called_once()

    def test_handles_exception(self):
        """Test handling exceptions during collection update."""
        from utils.plex import update_plex_collection

        mock_section = Mock()
        mock_section.collections.side_effect = plexapi.exceptions.PlexApiException("API Error")

        result = update_plex_collection(mock_section, "Test", [Mock()])

        assert result is False

    def test_with_logger(self):
        """Test collection update with logger."""
        from utils.plex import update_plex_collection

        mock_logger = Mock()
        mock_section = Mock()
        mock_section.collections.return_value = []

        result = update_plex_collection(mock_section, "Test", [Mock()], logger=mock_logger)

        assert result is True
        mock_logger.info.assert_called_once()


class TestCleanupOldCollections:
    """Tests for cleanup_old_collections() function."""

    def test_deletes_old_patterns(self):
        """Test deleting collections matching old patterns."""
        from utils.plex import cleanup_old_collections

        mock_old_collection = Mock()
        mock_old_collection.title = "🎬 john - Recommendation"

        mock_section = Mock()
        mock_section.collections.return_value = [mock_old_collection]

        cleanup_old_collections(mock_section, "🎬 John's Recommended", "john", "🎬")

        mock_old_collection.delete.assert_called_once()

    def test_skips_current_collection(self):
        """Test that current collection is not deleted."""
        from utils.plex import cleanup_old_collections

        mock_collection = Mock()
        mock_collection.title = "🎬 John's Recommended"

        mock_section = Mock()
        mock_section.collections.return_value = [mock_collection]

        cleanup_old_collections(mock_section, "🎬 John's Recommended", "john", "🎬")

        mock_collection.delete.assert_not_called()

    def test_handles_exception(self):
        """Test exception handling during cleanup."""
        from utils.plex import cleanup_old_collections

        mock_section = Mock()
        mock_section.collections.side_effect = plexapi.exceptions.PlexApiException("API Error")

        # Should not raise
        cleanup_old_collections(mock_section, "Test", "user", "🎬")

    def test_with_logger(self):
        """Test cleanup with logger."""
        from utils.plex import cleanup_old_collections

        mock_logger = Mock()
        mock_old_collection = Mock()
        mock_old_collection.title = "john - Recommendation"

        mock_section = Mock()
        mock_section.collections.return_value = [mock_old_collection]

        cleanup_old_collections(mock_section, "New Collection", "john", "🎬", logger=mock_logger)

        mock_logger.info.assert_called_once()


class TestCleanupLegacyUnnamedCollection:
    """Tests for cleanup_legacy_unnamed_collection() - #261 migration.

    Installs that ran under the collections.append_usernames: false code
    default (fixed in #261) all produced one identically-titled shared
    collection: "{emoji} Recommended - Recommendation". cleanup_old_
    collections() above can never find it (its patterns are all built
    from a real username, and this title contains none), so it needs its
    own, separate cleanup path.
    """

    def test_deletes_legacy_collection_and_strips_item_labels(self):
        from utils.plex import cleanup_legacy_unnamed_collection

        legacy_item = Mock(ratingKey=101)
        mock_legacy_collection = Mock()
        mock_legacy_collection.title = "🎬 Recommended - Recommendation"
        mock_legacy_collection.items.return_value = [legacy_item]

        mock_section = Mock()
        mock_section.collections.return_value = [mock_legacy_collection]

        cleanup_legacy_unnamed_collection(mock_section, "🎬 Alice - Recommendation", "🎬")

        mock_legacy_collection.delete.assert_called_once()
        legacy_item.removeLabel.assert_called_once_with("Recommended")

    def test_leaves_current_collection_alone(self):
        """A real user literally named 'Recommended' would legitimately
        produce this exact title today - never delete the collection
        this run just created/updated."""
        from utils.plex import cleanup_legacy_unnamed_collection

        mock_section = Mock()

        cleanup_legacy_unnamed_collection(mock_section, "🎬 Recommended - Recommendation", "🎬")

        mock_section.collections.assert_not_called()

    def test_leaves_unrelated_collections_alone(self):
        from utils.plex import cleanup_legacy_unnamed_collection

        mock_other = Mock()
        mock_other.title = "🎬 Alice - Recommendation"

        mock_section = Mock()
        mock_section.collections.return_value = [mock_other]

        cleanup_legacy_unnamed_collection(mock_section, "🎬 Alice - Recommendation", "🎬")

        mock_other.delete.assert_not_called()

    def test_idempotent_when_already_cleaned_up(self):
        """No legacy collection left - a no-op, not an error."""
        from utils.plex import cleanup_legacy_unnamed_collection

        mock_section = Mock()
        mock_section.collections.return_value = []

        cleanup_legacy_unnamed_collection(mock_section, "🎬 Alice - Recommendation", "🎬")

    def test_handles_exception(self):
        from utils.plex import cleanup_legacy_unnamed_collection

        mock_section = Mock()
        mock_section.collections.side_effect = plexapi.exceptions.PlexApiException("API Error")

        # Should not raise
        cleanup_legacy_unnamed_collection(mock_section, "🎬 Alice - Recommendation", "🎬")

    def test_with_logger(self):
        from utils.plex import cleanup_legacy_unnamed_collection

        mock_logger = Mock()
        mock_legacy_collection = Mock()
        mock_legacy_collection.title = "🎬 Recommended - Recommendation"
        mock_legacy_collection.items.return_value = []

        mock_section = Mock()
        mock_section.collections.return_value = [mock_legacy_collection]

        cleanup_legacy_unnamed_collection(mock_section, "🎬 Alice - Recommendation", "🎬", logger=mock_logger)

        mock_logger.info.assert_called_once()


class TestGetPlexUserIds:
    """Tests for get_plex_user_ids() function."""

    def test_returns_user_ids(self):
        """Test returning user IDs for managed users."""
        from utils.plex import get_plex_user_ids

        mock_user = Mock()
        mock_user.title = "John"
        mock_user.id = 12345

        mock_account = Mock()
        mock_account.users.return_value = [mock_user]

        mock_plex = Mock()
        mock_plex.myPlexAccount.return_value = mock_account

        result = get_plex_user_ids(mock_plex, ["John"])

        assert result == {"John": 12345}

    def test_skips_unmatched_users(self):
        """Test that unmatched users are skipped."""
        from utils.plex import get_plex_user_ids

        mock_user = Mock()
        mock_user.title = "John"
        mock_user.id = 12345

        mock_account = Mock()
        mock_account.users.return_value = [mock_user]

        mock_plex = Mock()
        mock_plex.myPlexAccount.return_value = mock_account

        result = get_plex_user_ids(mock_plex, ["Jane"])

        assert result == {}

    @patch("utils.plex.log_warning")
    def test_handles_exception(self, mock_log):
        """Test exception handling."""
        from utils.plex import get_plex_user_ids

        mock_plex = Mock()
        mock_plex.myPlexAccount.side_effect = plexapi.exceptions.PlexApiException("API Error")

        result = get_plex_user_ids(mock_plex, ["John"])

        assert result == {}
        mock_log.assert_called_once()


class TestInitPlex:
    """Tests for init_plex() function."""

    @patch("utils.plex.requests.Session")
    @patch("utils.plex.plexapi.server.PlexServer")
    def test_successful_connection(self, mock_plex_server, mock_session_class):
        """Test successful Plex server connection."""
        from utils.plex import init_plex

        mock_server = Mock()
        mock_plex_server.return_value = mock_server
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        config = {"plex": {"url": "http://localhost:32400", "token": "test_token"}}
        result = init_plex(config)

        assert result == mock_server
        mock_plex_server.assert_called_once_with("http://localhost:32400", "test_token", session=mock_session)
        assert mock_session.verify is True  # Default is secure (verify SSL)

    @patch("utils.plex.requests.Session")
    @patch("utils.plex.plexapi.server.PlexServer")
    def test_connection_with_verify_ssl_true(self, mock_plex_server, mock_session_class):
        """Test Plex server connection with SSL verification enabled."""
        from utils.plex import init_plex

        mock_server = Mock()
        mock_plex_server.return_value = mock_server
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        config = {"plex": {"url": "http://localhost:32400", "token": "test_token", "verify_ssl": True}}
        result = init_plex(config)

        assert result == mock_server
        assert mock_session.verify is True

    @patch("utils.plex.requests.Session")
    @patch("utils.plex.plexapi.server.PlexServer")
    @patch("utils.plex.log_error")
    def test_connection_failure(self, mock_log, mock_plex_server, mock_session_class):
        """Test handling connection failure."""
        from utils.plex import init_plex

        mock_plex_server.side_effect = requests.RequestException("Connection refused")

        config = {"plex": {"url": "http://localhost:32400", "token": "test_token"}}

        with pytest.raises(requests.RequestException, match="Connection refused"):
            init_plex(config)

        mock_log.assert_called_once()


class TestGetPlexAccountIds:
    """Tests for get_plex_account_ids() function."""

    @patch("utils.plex.requests.get")
    def test_finds_exact_match(self, mock_get):
        """Test finding account ID with exact name match."""
        from utils.plex import get_plex_account_ids

        xml_content = b"""<MediaContainer>
            <Account id="123" name="John"/>
        </MediaContainer>"""

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.content = xml_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        config = {"plex": {"url": "http://localhost:32400", "token": "test_token"}}
        result = get_plex_account_ids(config, ["John"])

        assert result == ["123"]

    @patch("utils.plex.requests.get")
    def test_finds_normalized_match(self, mock_get):
        """Test finding account ID with normalized name match."""
        from utils.plex import get_plex_account_ids

        xml_content = b"""<MediaContainer>
            <Account id="456" name="john-doe"/>
        </MediaContainer>"""

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.content = xml_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        config = {"plex": {"url": "http://localhost:32400", "token": "test_token"}}
        result = get_plex_account_ids(config, ["johndoe"])

        assert result == ["456"]

    @patch("utils.plex.requests.get")
    @patch("utils.plex.log_error")
    def test_logs_error_for_missing_user(self, mock_log, mock_get):
        """Test logging error when user not found."""
        from utils.plex import get_plex_account_ids

        xml_content = b"""<MediaContainer>
            <Account id="123" name="John"/>
        </MediaContainer>"""

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.content = xml_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        config = {"plex": {"url": "http://localhost:32400", "token": "test_token"}}
        result = get_plex_account_ids(config, ["NonExistent"])

        assert result == []
        mock_log.assert_called_once()

    @patch("utils.plex.requests.get")
    @patch("utils.plex.log_error")
    def test_handles_api_error(self, mock_log, mock_get):
        """Test handling API errors."""
        from utils.plex import get_plex_account_ids

        mock_get.side_effect = requests.RequestException("Connection error")

        config = {"plex": {"url": "http://localhost:32400", "token": "test_token"}}
        result = get_plex_account_ids(config, ["John"])

        assert result == []
        mock_log.assert_called_once()


class TestGetUserSpecificConnection:
    """Tests for get_user_specific_connection() function."""

    def test_returns_plex_for_plex_users(self):
        """Test returning plex when plex_users is set."""
        from utils.plex import get_user_specific_connection

        mock_plex = Mock()
        config = {"plex": {"token": "test"}}
        users = {"plex_users": ["user1"], "managed_users": []}

        result = get_user_specific_connection(mock_plex, config, users)

        assert result == mock_plex

    @patch("utils.plex.MyPlexAccount")
    def test_switches_to_managed_user(self, mock_account_class):
        """Test switching to managed user context."""
        from utils.plex import get_user_specific_connection

        mock_user = Mock()
        mock_account = Mock()
        mock_account.user.return_value = mock_user
        mock_account_class.return_value = mock_account

        mock_switched = Mock()
        mock_plex = Mock()
        mock_plex.switchUser.return_value = mock_switched

        config = {"plex": {"token": "test"}}
        users = {"plex_users": [], "managed_users": ["managed_user"]}

        result = get_user_specific_connection(mock_plex, config, users)

        assert result == mock_switched

    @patch("utils.plex.MyPlexAccount")
    @patch("utils.plex.log_warning")
    def test_handles_switch_error(self, mock_log, mock_account_class):
        """Test handling error during user switch."""
        from utils.plex import get_user_specific_connection

        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("Auth error")

        mock_plex = Mock()
        config = {"plex": {"token": "test"}}
        users = {"plex_users": [], "managed_users": ["managed_user"]}

        result = get_user_specific_connection(mock_plex, config, users)

        assert result == mock_plex
        mock_log.assert_called_once()


class TestExtractRatingAdvanced:
    """Additional tests for extract_rating() edge cases."""

    def test_falls_back_to_ratings_list(self):
        """Test fallback to ratings list when primary ratings are None."""
        mock_rating = Mock()
        mock_rating.value = 7.5
        mock_rating.image = "imdb://image.rating"

        mock_item = Mock()
        mock_item.userRating = None
        mock_item.audienceRating = None
        mock_item.ratings = [mock_rating]

        result = extract_rating(mock_item)

        assert result == 7.5

    def test_falls_back_to_audience_type_rating(self):
        """Test fallback to audience type rating."""
        mock_rating = Mock()
        mock_rating.value = 8.0
        mock_rating.type = "audience"
        mock_rating.image = ""

        mock_item = Mock()
        mock_item.userRating = None
        mock_item.audienceRating = None
        mock_item.ratings = [mock_rating]

        result = extract_rating(mock_item)

        assert result == 8.0

    def test_prefer_user_rating_false_with_fallback(self):
        """Test prefer_user_rating=False falls back to userRating."""
        mock_item = Mock()
        mock_item.userRating = 9.0
        mock_item.audienceRating = None

        result = extract_rating(mock_item, prefer_user_rating=False)

        assert result == 9.0

    def test_handles_invalid_rating_value(self):
        """Test handling invalid rating value in ratings list."""
        mock_rating = Mock()
        mock_rating.value = "invalid"
        mock_rating.image = "imdb://image.rating"

        mock_item = Mock()
        mock_item.userRating = None
        mock_item.audienceRating = None
        mock_item.ratings = [mock_rating]

        result = extract_rating(mock_item)

        assert result == 0.0


class TestGetLibraryImdbIdsAdvanced:
    """Additional tests for get_library_imdb_ids()."""

    @patch("utils.plex.log_warning")
    def test_handles_exception(self, mock_log):
        """Test exception handling in get_library_imdb_ids."""
        mock_section = Mock()
        mock_section.all.side_effect = plexapi.exceptions.PlexApiException("API Error")

        result = get_library_imdb_ids(mock_section)

        assert result == set()
        mock_log.assert_called_once()

    def test_handles_item_without_guids_attr(self):
        """Test handling items without guids attribute."""
        mock_item = Mock(spec=["title"])  # No guids attr

        mock_section = Mock()
        mock_section.all.return_value = [mock_item]

        result = get_library_imdb_ids(mock_section)

        assert result == set()


class TestGetWatchedMovieCount:
    """Tests for get_watched_movie_count() function."""

    def test_returns_zero_for_empty_users(self):
        """Test returning 0 when no users to check."""
        from utils.plex import get_watched_movie_count

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = get_watched_movie_count(config, [])

        assert result == 0

    @patch("utils.plex.requests.get")
    @patch("utils.plex.MyPlexAccount")
    def test_returns_watched_count(self, mock_account_class, mock_get):
        """Test returning watched movie count."""
        from utils.plex import get_watched_movie_count

        # Setup account mock
        mock_user = Mock()
        mock_user.title = "testuser"
        mock_user.id = 123

        mock_account = Mock()
        mock_account.users.return_value = [mock_user]
        mock_account.username = "admin"
        mock_account.id = 1
        mock_account_class.return_value = mock_account

        # Setup API response
        xml_content = b"""<MediaContainer>
            <Video type="movie" ratingKey="100"/>
            <Video type="movie" ratingKey="101"/>
            <Video type="episode" ratingKey="200"/>
        </MediaContainer>"""

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.content = xml_content
        mock_get.return_value = mock_response

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = get_watched_movie_count(config, ["testuser"])

        assert result == 2

    @patch("utils.plex.MyPlexAccount")
    @patch("utils.plex.log_warning")
    def test_handles_exception(self, mock_log, mock_account_class):
        """Test exception handling."""
        from utils.plex import get_watched_movie_count

        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("Auth error")

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = get_watched_movie_count(config, ["user"])

        assert result == 0
        mock_log.assert_called_once()

    @patch("utils.plex.requests.get")
    @patch("utils.plex.MyPlexAccount")
    def test_matches_admin_user(self, mock_account_class, mock_get):
        """Test matching admin user."""
        from utils.plex import get_watched_movie_count

        mock_account = Mock()
        mock_account.users.return_value = []
        mock_account.username = "adminuser"
        mock_account.id = 1
        mock_account_class.return_value = mock_account

        xml_content = b"""<MediaContainer>
            <Video type="movie" ratingKey="100"/>
        </MediaContainer>"""

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.content = xml_content
        mock_get.return_value = mock_response

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = get_watched_movie_count(config, ["admin"])

        assert result == 1


class TestGetWatchedShowCount:
    """Tests for get_watched_show_count() function."""

    def test_returns_zero_for_empty_users(self):
        """Test returning 0 when no users to check."""
        from utils.plex import get_watched_show_count

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = get_watched_show_count(config, [])

        assert result == 0

    @patch("utils.plex.requests.get")
    @patch("utils.plex.MyPlexAccount")
    def test_returns_watched_show_count(self, mock_account_class, mock_get):
        """Test returning watched show count."""
        from utils.plex import get_watched_show_count

        mock_user = Mock()
        mock_user.title = "testuser"
        mock_user.id = 123

        mock_account = Mock()
        mock_account.users.return_value = [mock_user]
        mock_account.username = "admin"
        mock_account.id = 1
        mock_account_class.return_value = mock_account

        xml_content = b"""<MediaContainer>
            <Video type="episode" grandparentRatingKey="200"/>
            <Video type="episode" grandparentRatingKey="200"/>
            <Video type="episode" grandparentRatingKey="201"/>
        </MediaContainer>"""

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.content = xml_content
        mock_get.return_value = mock_response

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = get_watched_show_count(config, ["testuser"])

        assert result == 2  # 2 unique shows

    @patch("utils.plex.MyPlexAccount")
    @patch("utils.plex.log_warning")
    def test_handles_exception(self, mock_log, mock_account_class):
        """Test exception handling."""
        from utils.plex import get_watched_show_count

        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("Auth error")

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = get_watched_show_count(config, ["user"])

        assert result == 0
        mock_log.assert_called_once()


class TestFetchPlexWatchHistoryShows:
    """Tests for fetch_plex_watch_history_shows() function."""

    @patch("utils.plex.requests.get")
    def test_fetches_show_history(self, mock_get):
        """Test fetching show watch history."""
        from utils.plex import fetch_plex_watch_history_shows

        xml_content = b"""<MediaContainer>
            <Video type="episode" grandparentKey="/library/metadata/100"/>
            <Video type="episode" grandparentKey="/library/metadata/101"/>
        </MediaContainer>"""

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.content = xml_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        mock_section = Mock()
        mock_section.key = 1

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = fetch_plex_watch_history_shows(config, ["123"], mock_section)

        assert 100 in result
        assert 101 in result

    @patch("utils.plex.requests.get")
    @patch("utils.plex.log_error")
    def test_handles_request_error(self, mock_log, mock_get):
        """Test handling request errors."""
        from utils.plex import fetch_plex_watch_history_shows

        mock_get.side_effect = requests.RequestException("Connection error")

        mock_section = Mock()
        mock_section.key = 1

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = fetch_plex_watch_history_shows(config, ["123"], mock_section)

        assert result == set()
        mock_log.assert_called()


class TestFindPlexMovieAdvanced:
    """Additional tests for find_plex_movie()."""

    def test_fuzzy_match_normalized_title(self):
        """Test fuzzy matching with normalized title."""
        mock_movie = Mock()
        mock_movie.title = "Avatar 4K"
        mock_movie.year = 2009

        mock_section = Mock()
        mock_section.search.return_value = []
        mock_section.all.return_value = [mock_movie]

        result = find_plex_movie(mock_section, "Avatar", 2009)

        assert result == mock_movie

    def test_partial_title_match(self):
        """Test partial title matching."""
        mock_movie = Mock()
        mock_movie.title = "Avatar: The Way of Water"
        mock_movie.year = 2022

        mock_section = Mock()
        mock_section.search.return_value = []
        mock_section.all.return_value = [mock_movie]

        result = find_plex_movie(mock_section, "Avatar", 2022)

        assert result == mock_movie

    def test_no_match_wrong_year(self):
        """Test no match when year doesn't match."""
        mock_movie = Mock()
        mock_movie.title = "Avatar"
        mock_movie.year = 2009

        mock_section = Mock()
        mock_section.search.return_value = []
        mock_section.all.return_value = [mock_movie]

        result = find_plex_movie(mock_section, "Avatar", 2022)

        assert result is None


class TestExtractGenresAdvanced:
    """Additional tests for extract_genres()."""

    def test_handles_exception_gracefully(self):
        """Test handling exception during genre extraction."""
        mock_item = Mock()
        mock_item.genres = Mock(side_effect=AttributeError("Error"))

        # Should not raise, should return empty list
        result = extract_genres(mock_item)
        # When accessing genres causes an exception, try block catches it
        assert result == [] or isinstance(result, list)


class TestExtractIdsFromGuidsAdvanced:
    """Additional tests for extract_ids_from_guids()."""

    def test_handles_invalid_tmdb_id(self):
        """Test handling invalid TMDB ID."""
        mock_guid = Mock()
        mock_guid.id = "tmdb://invalid"

        mock_item = Mock()
        mock_item.guids = [mock_guid]

        result = extract_ids_from_guids(mock_item)

        assert result["tmdb_id"] is None

    def test_handles_guid_as_string(self):
        """Test handling guid as string instead of object."""
        mock_guid = "imdb://tt1234567"

        mock_item = Mock()
        mock_item.guids = [mock_guid]

        result = extract_ids_from_guids(mock_item)

        assert result["imdb_id"] == "tt1234567"


class TestFetchPlexWatchHistoryMovies:
    """Tests for fetch_plex_watch_history_movies() function."""

    @patch("utils.plex.MyPlexAccount")
    @patch("utils.plex.requests.get")
    def test_fetches_movie_history(self, mock_get, mock_account_class):
        """Test fetching movie watch history."""
        from utils.plex import fetch_plex_watch_history_movies

        mock_user = Mock()
        mock_user.id = 123

        mock_account = Mock()
        mock_account.users.return_value = [mock_user]
        mock_account_class.return_value = mock_account

        xml_content = b"""<MediaContainer>
            <Video ratingKey="100" viewedAt="1700000000" userRating="8.5"/>
            <Video ratingKey="101" viewedAt="1700001000"/>
        </MediaContainer>"""

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.content = xml_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        mock_section = Mock()
        mock_section.key = 1

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        history, dates = fetch_plex_watch_history_movies(config, ["123"], mock_section)

        assert len(history) == 2

    @patch("utils.plex.MyPlexAccount")
    @patch("utils.plex.log_error")
    def test_handles_exception(self, mock_log, mock_account_class):
        """Test exception handling."""
        from utils.plex import fetch_plex_watch_history_movies

        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("Auth error")

        mock_section = Mock()
        config = {"plex": {"url": "http://localhost", "token": "test"}}

        history, dates = fetch_plex_watch_history_movies(config, ["123"], mock_section)

        assert history == []
        assert dates == {}
        mock_log.assert_called()

    @patch("utils.plex.MyPlexAccount")
    @patch("utils.plex.requests.get")
    def test_skips_unknown_account(self, mock_get, mock_account_class):
        """Test skipping unknown account IDs."""
        from utils.plex import fetch_plex_watch_history_movies

        mock_account = Mock()
        mock_account.users.return_value = []  # No managed users
        mock_account_class.return_value = mock_account

        mock_section = Mock()
        mock_section.key = 1

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        # Use account ID that won't match owner or managed users
        history, dates = fetch_plex_watch_history_movies(config, ["999"], mock_section)

        # Should return empty since no matching accounts
        assert history == []

    @patch("utils.plex.MyPlexAccount")
    @patch("utils.plex.requests.get")
    @patch("utils.plex.log_error")
    def test_per_account_fetch_error_routed_through_log_error_not_bare_print(
        self, mock_log, mock_get, mock_account_class
    ):
        """Regression: the per-account fetch's except clause used to be
        a bare print() with no log_error call at all - the one choke
        point in this module NOT routed through the level-gated logging
        module #306 added (every sibling function -
        fetch_plex_watch_history_shows, fetch_show_completion_data -
        already called log_error/log_warning here). A per-account
        failure must be logged (so logging.verbosity actually governs
        it, and it's visible in a real log file, not just a
        console-only print), and must not raise - one account's
        failure should not abort every other account's fetch."""
        from utils.plex import fetch_plex_watch_history_movies

        mock_user = Mock()
        mock_user.id = 123
        mock_account = Mock()
        mock_account.users.return_value = [mock_user]
        mock_account_class.return_value = mock_account

        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        mock_section = Mock()
        mock_section.key = 1

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        history, dates = fetch_plex_watch_history_movies(config, ["123"], mock_section)

        assert history == []
        assert dates == {}
        mock_log.assert_called_once()
        logged_message = mock_log.call_args[0][0]
        assert "123" in logged_message


class TestFetchWatchHistoryWithTmdb:
    """Tests for fetch_watch_history_with_tmdb() function."""

    @patch("utils.plex.requests.get")
    def test_fetches_movie_with_tmdb(self, mock_get):
        """Test fetching movie watch history with TMDB IDs."""
        from utils.plex import fetch_watch_history_with_tmdb

        xml_content = b"""<MediaContainer>
            <Video type="movie" ratingKey="100"/>
        </MediaContainer>"""

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.content = xml_content
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        mock_guid = Mock()
        mock_guid.id = "tmdb://12345"

        mock_item = Mock()
        mock_item.guids = [mock_guid]
        mock_item.title = "Test Movie"
        mock_item.year = 2020

        mock_plex = Mock()
        mock_plex.fetchItem.return_value = mock_item

        mock_section = Mock()
        mock_section.key = 1

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = fetch_watch_history_with_tmdb(mock_plex, config, ["123"], mock_section, "movie")

        assert len(result) == 1
        assert result[0]["tmdb_id"] == 12345

    @patch("utils.plex.requests.get")
    def test_handles_non_200_response(self, mock_get):
        """Test handling non-200 response."""
        from utils.plex import fetch_watch_history_with_tmdb

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.key = 1

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = fetch_watch_history_with_tmdb(mock_plex, config, ["123"], mock_section, "movie")

        assert result == []

    @patch("utils.plex.requests.get")
    def test_fetches_show_with_tmdb(self, mock_get):
        """Test fetching show watch history with TMDB IDs."""
        from utils.plex import fetch_watch_history_with_tmdb

        xml_content = b"""<MediaContainer>
            <Video type="episode" grandparentKey="/library/metadata/200"/>
        </MediaContainer>"""

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.content = xml_content
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        mock_guid = Mock()
        mock_guid.id = "tmdb://54321"

        mock_item = Mock()
        mock_item.guids = [mock_guid]
        mock_item.title = "Test Show"
        mock_item.year = 2021

        mock_plex = Mock()
        mock_plex.fetchItem.return_value = mock_item

        mock_section = Mock()
        mock_section.key = 1

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = fetch_watch_history_with_tmdb(mock_plex, config, ["123"], mock_section, "show")

        assert len(result) == 1
        assert result[0]["tmdb_id"] == 54321

    @patch("utils.plex.requests.get")
    def test_handles_exception_in_loop(self, mock_get):
        """Test handling exception when processing items."""
        from utils.plex import fetch_watch_history_with_tmdb

        mock_get.side_effect = requests.RequestException("Connection error")

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.key = 1

        config = {"plex": {"url": "http://localhost", "token": "test"}}
        result = fetch_watch_history_with_tmdb(mock_plex, config, ["123"], mock_section, "movie")

        assert result == []


class TestGetConfiguredUsers:
    """Tests for get_configured_users() function."""

    @patch("utils.plex.MyPlexAccount")
    def test_returns_configured_users(self, mock_account_class):
        """Test returning configured users."""
        from utils.plex import get_configured_users

        mock_user = Mock()
        mock_user.title = "TestUser"

        mock_account = Mock()
        mock_account.username = "AdminUser"
        mock_account.users.return_value = [mock_user]
        mock_account_class.return_value = mock_account

        config = {"plex": {"token": "test", "managed_users": "TestUser"}, "plex_users": {"users": None}}

        result = get_configured_users(config)

        assert result["admin_user"] == "AdminUser"
        assert "TestUser" in result["managed_users"]

    @patch("utils.plex.MyPlexAccount")
    def test_maps_admin_alias(self, mock_account_class):
        """Test mapping 'admin' to actual admin username."""
        from utils.plex import get_configured_users

        mock_account = Mock()
        mock_account.username = "RealAdmin"
        mock_account.users.return_value = []
        mock_account_class.return_value = mock_account

        config = {"plex": {"token": "test", "managed_users": "admin"}, "plex_users": {"users": None}}

        result = get_configured_users(config)

        assert "RealAdmin" in result["managed_users"]

    @patch("utils.plex.MyPlexAccount")
    @patch("utils.plex.log_error")
    def test_raises_for_unknown_user(self, mock_log, mock_account_class):
        """Test raising error for unknown user."""
        from utils.plex import get_configured_users

        mock_account = Mock()
        mock_account.username = "Admin"
        mock_account.users.return_value = []
        mock_account_class.return_value = mock_account

        config = {"plex": {"token": "test", "managed_users": "UnknownUser"}, "plex_users": {"users": None}}

        with pytest.raises(ValueError):
            get_configured_users(config)

    @patch("utils.plex.MyPlexAccount")
    def test_handles_plex_users_list(self, mock_account_class):
        """Test handling plex_users as list."""
        from utils.plex import get_configured_users

        mock_account = Mock()
        mock_account.username = "Admin"
        mock_account.users.return_value = []
        mock_account_class.return_value = mock_account

        config = {"plex": {"token": "test", "managed_users": ""}, "plex_users": {"users": ["user1", "user2"]}}

        result = get_configured_users(config)

        assert result["plex_users"] == ["user1", "user2"]

    @patch("utils.plex.MyPlexAccount")
    def test_handles_plex_users_string(self, mock_account_class):
        """Test handling plex_users as comma-separated string."""
        from utils.plex import get_configured_users

        mock_account = Mock()
        mock_account.username = "Admin"
        mock_account.users.return_value = []
        mock_account_class.return_value = mock_account

        config = {"plex": {"token": "test", "managed_users": ""}, "plex_users": {"users": "user1, user2"}}

        result = get_configured_users(config)

        assert "user1" in result["plex_users"]
        assert "user2" in result["plex_users"]

    @patch("utils.plex.MyPlexAccount")
    def test_deduplicates_managed_users(self, mock_account_class):
        """Test deduplication of managed users."""
        from utils.plex import get_configured_users

        mock_user = Mock()
        mock_user.title = "TestUser"

        mock_account = Mock()
        mock_account.username = "Admin"
        mock_account.users.return_value = [mock_user]
        mock_account_class.return_value = mock_account

        config = {
            "plex": {"token": "test", "managed_users": "TestUser, testuser"},  # Same user twice (different case)
            "plex_users": {"users": None},
        }

        result = get_configured_users(config)

        # Should deduplicate
        assert len(result["managed_users"]) == 1

    @patch("utils.plex.MyPlexAccount")
    def test_managed_users_keeps_config_spelling_not_live_title(self, mock_account_class):
        """#352: the legacy plex.managed_users path used to substitute
        whatever Plex's live .title happened to be this run, so the same
        physical account could resolve to a different string on every
        run (the actual field bug). Existence is still confirmed against
        the live account list (case-insensitively - the only tolerance
        get_configured_users' own matching rule has ever offered; a
        space/punctuation difference is a separate, pre-existing gap
        this fix doesn't touch), but the value used downstream must be
        exactly what's in config.yml."""
        from utils.plex import get_configured_users

        mock_user = Mock()
        mock_user.title = "ALEXPIGOT"  # what Plex resolves it to THIS run

        mock_account = Mock()
        mock_account.username = "Admin"
        mock_account.users.return_value = [mock_user]
        mock_account_class.return_value = mock_account

        config = {"plex": {"token": "test", "managed_users": "alexpigot"}, "plex_users": {"users": None}}

        result = get_configured_users(config)

        assert result["managed_users"] == ["alexpigot"]

    @patch("utils.plex.MyPlexAccount")
    def test_managed_users_stable_across_a_simulated_title_flap(self, mock_account_class):
        """The same config.yml, run three times while Plex's live title
        for the same account flaps alexpigot -> ALEXPIGOT -> alexpigot,
        must resolve to the identical managed_users value every time."""
        from utils.plex import get_configured_users

        mock_account = Mock()
        mock_account.username = "Admin"
        mock_account_class.return_value = mock_account

        config = {"plex": {"token": "test", "managed_users": "alexpigot"}, "plex_users": {"users": None}}

        results = []
        for live_title in ("alexpigot", "ALEXPIGOT", "alexpigot"):
            live_user = Mock()
            live_user.title = live_title
            mock_account.users.return_value = [live_user]
            results.append(get_configured_users(config)["managed_users"])

        assert results == [["alexpigot"], ["alexpigot"], ["alexpigot"]]

    @patch("utils.plex.MyPlexAccount")
    def test_tolerates_a_pending_not_yet_confirmed_rename(self, mock_account_class, tmp_path):
        """#356: on the first run after a genuine (more-than-case) Plex
        rename, config.yml's managed_users still holds the OLD spelling
        (the #352 debounce delays the rewrite until a second consecutive
        run corroborates it), but this run's live account list already
        reflects the NEW title. cache/user_id_map.json already recorded
        that new title as "pending" (not yet confirmed) against this
        exact account earlier in the same run (utils.user_migration.
        migrate_renamed_plex_users) - that must be enough to resolve the
        old spelling here too, rather than raising."""
        from utils.plex import get_configured_users
        from utils.user_migration import save_user_id_map

        save_user_id_map(str(tmp_path), {"1": {"username": "AlexPigot", "pending": "Alex Pigot"}})

        live_user = Mock()
        live_user.title = "Alex Pigot"  # the new, not-yet-confirmed title

        mock_account = Mock()
        mock_account.username = "Admin"
        mock_account.users.return_value = [live_user]
        mock_account_class.return_value = mock_account

        config = {"plex": {"token": "test", "managed_users": "AlexPigot"}, "plex_users": {"users": None}}

        result = get_configured_users(config, cache_dir=str(tmp_path))

        assert result["managed_users"] == ["AlexPigot"], "config.yml's own (old, still-configured) spelling was kept"

    @patch("utils.plex.MyPlexAccount")
    @patch("utils.plex.log_error")
    def test_pending_rename_tolerance_never_masks_a_genuine_departure(self, mock_log, mock_account_class, tmp_path):
        """The pending-rename tolerance is scoped to the exact old
        spelling recorded as a pending observation's prior confirmed
        name - an unrelated user who is genuinely gone must still raise,
        never silently pass just because *some* account somewhere has a
        pending rename this run."""
        from utils.plex import get_configured_users
        from utils.user_migration import save_user_id_map

        save_user_id_map(str(tmp_path), {"1": {"username": "AlexPigot", "pending": "Alex Pigot"}})

        live_user = Mock()
        live_user.title = "Alex Pigot"

        mock_account = Mock()
        mock_account.username = "Admin"
        mock_account.users.return_value = [live_user]
        mock_account_class.return_value = mock_account

        config = {"plex": {"token": "test", "managed_users": "TotallyUnrelatedUser"}, "plex_users": {"users": None}}

        with pytest.raises(ValueError):
            get_configured_users(config, cache_dir=str(tmp_path))


class TestFetchPlexUsers:
    """Tests for fetch_plex_users() - #266 (web UI 'Fetch from Plex')."""

    @patch("utils.plex.MyPlexAccount")
    def test_admin_listed_first(self, mock_account_class):
        from utils.plex import fetch_plex_users

        mock_account = Mock()
        mock_account.username = "AdminUser"
        mock_account.users.return_value = []
        mock_account_class.return_value = mock_account

        result = fetch_plex_users({"plex": {"token": "test"}})

        assert result == [{"username": "AdminUser", "title": "AdminUser", "is_admin": True}]

    @patch("utils.plex.MyPlexAccount")
    def test_includes_every_account_user(self, mock_account_class):
        from utils.plex import fetch_plex_users

        alice = Mock(username="alice", title="Alice")
        bob = Mock(username="bob", title="Bob")

        mock_account = Mock()
        mock_account.username = "AdminUser"
        mock_account.users.return_value = [alice, bob]
        mock_account_class.return_value = mock_account

        result = fetch_plex_users({"plex": {"token": "test"}})

        assert result == [
            {"username": "AdminUser", "title": "AdminUser", "is_admin": True},
            {"username": "alice", "title": "Alice", "is_admin": False},
            {"username": "bob", "title": "Bob", "is_admin": False},
        ]

    @patch("utils.plex.MyPlexAccount")
    def test_blank_username_falls_back_to_title(self, mock_account_class):
        """A Home user with no linked Plex account/email has a blank
        plexapi username - fall back to title (what get_configured_users
        already matches managed_users against)."""
        from utils.plex import fetch_plex_users

        home_user = Mock(username="", title="Kid")

        mock_account = Mock()
        mock_account.username = "AdminUser"
        mock_account.users.return_value = [home_user]
        mock_account_class.return_value = mock_account

        result = fetch_plex_users({"plex": {"token": "test"}})

        assert result[1] == {"username": "Kid", "title": "Kid", "is_admin": False}

    @patch("utils.plex.MyPlexAccount")
    def test_propagates_connection_failure(self, mock_account_class):
        """Fails loud (raises) - the web route (web/config_users.py) is
        responsible for catching this and showing a friendly message,
        same convention as init_plex/get_configured_users."""
        from utils.plex import fetch_plex_users

        mock_account_class.side_effect = plexapi.exceptions.Unauthorized("bad token")

        with pytest.raises(plexapi.exceptions.Unauthorized):
            fetch_plex_users({"plex": {"token": "bad"}})


class TestFetchPlexLibraries:
    """Tests for fetch_plex_libraries() - #266 (web UI 'Fetch from Plex')."""

    @patch("utils.plex.init_plex")
    def test_maps_movie_and_show_sections(self, mock_init_plex):
        from utils.plex import fetch_plex_libraries

        movies_section = Mock(title="Movies", type="movie")
        shows_section = Mock(title="TV Shows", type="show")

        mock_server = Mock()
        mock_server.library.sections.return_value = [movies_section, shows_section]
        mock_init_plex.return_value = mock_server

        result = fetch_plex_libraries({"plex": {"url": "http://x", "token": "y"}})

        assert result == [
            {"section": "Movies", "media_type": "movie"},
            {"section": "TV Shows", "media_type": "tv"},
        ]

    @patch("utils.plex.init_plex")
    def test_skips_unmanaged_section_types(self, mock_init_plex):
        """music/photo/etc. sections aren't something curatarr manages -
        must be silently skipped, not surfaced as some third media_type."""
        from utils.plex import fetch_plex_libraries

        music_section = Mock(title="Music", type="artist")

        mock_server = Mock()
        mock_server.library.sections.return_value = [music_section]
        mock_init_plex.return_value = mock_server

        result = fetch_plex_libraries({"plex": {"url": "http://x", "token": "y"}})

        assert result == []

    @patch("utils.plex.init_plex")
    def test_propagates_connection_failure(self, mock_init_plex):
        from utils.plex import fetch_plex_libraries

        mock_init_plex.side_effect = plexapi.exceptions.PlexApiException("connection refused")

        with pytest.raises(plexapi.exceptions.PlexApiException):
            fetch_plex_libraries({"plex": {"url": "http://x", "token": "y"}})


class TestUpdatePlexCollectionAdvanced:
    """Additional tests for update_plex_collection()."""

    def test_updates_existing_with_empty_items(self):
        """Test updating existing collection when it has no items."""
        from utils.plex import update_plex_collection

        mock_existing = Mock()
        mock_existing.title = "Existing"
        mock_existing.items.return_value = []  # Empty current items

        mock_section = Mock()
        mock_section.collections.return_value = [mock_existing]

        mock_item = Mock()
        result = update_plex_collection(mock_section, "Existing", [mock_item])

        assert result is True
        # removeItems should not be called since items is empty
        mock_existing.addItems.assert_called_once()

    def test_logs_with_logger_on_update(self):
        """Test logging on collection update with logger."""
        from utils.plex import update_plex_collection

        mock_logger = Mock()
        mock_existing = Mock()
        mock_existing.title = "Existing"
        mock_existing.items.return_value = [Mock()]

        mock_section = Mock()
        mock_section.collections.return_value = [mock_existing]

        result = update_plex_collection(mock_section, "Existing", [Mock()], logger=mock_logger)

        assert result is True
        mock_logger.info.assert_called()

    def test_logs_error_with_logger(self):
        """Test logging error with logger."""
        from utils.plex import update_plex_collection

        mock_logger = Mock()
        mock_section = Mock()
        mock_section.collections.side_effect = plexapi.exceptions.PlexApiException("Error")

        result = update_plex_collection(mock_section, "Test", [Mock()], logger=mock_logger)

        assert result is False
        mock_logger.error.assert_called_once()


class TestUpdatePlexCollectionRenameOnTemplateChange:
    """Tests for update_plex_collection()'s rename-on-template-change
    behavior (#267 follow-up): a movie_name_template/tv_name_template edit
    that changes the rendered collection name renames the collection
    previously identified by its PrivateCollection_<user> label, instead
    of leaving it orphaned while a second one gets created."""

    @staticmethod
    def _make_collection(title, labels=()):
        collection = Mock()
        collection.title = title
        collection.items.return_value = [Mock()]
        collection.labels = [Mock(tag=t) for t in labels]
        return collection

    def test_renames_old_named_collection_found_by_label(self):
        from utils.plex import update_plex_collection

        stale = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        mock_section = Mock()
        mock_section.collections.return_value = [stale]

        result = update_plex_collection(
            mock_section,
            "Recommended movies - Alice",
            [Mock()],
            label_name="Recommended_alice",
            private_label="PrivateCollection_alice",
        )

        assert result is True
        stale.editTitle.assert_called_once_with("Recommended movies - Alice")
        stale.addItems.assert_called_once()
        mock_section.createCollection.assert_not_called()

    def test_rename_compares_full_title_including_multi_library_suffix(self):
        """The multi-library disambiguation suffix is part of collection_name
        by the time it reaches this function - the rename must compare/act
        on the full, final title, not a pre-suffix version."""
        from utils.plex import update_plex_collection

        stale = self._make_collection(
            "🎬 Alice - Recommendation (Movies 4K)", labels=["PrivateCollection_alice_movies-4k"]
        )
        mock_section = Mock()
        mock_section.collections.return_value = [stale]

        update_plex_collection(
            mock_section,
            "Recommended movies - Alice (Movies 4K)",
            [Mock()],
            label_name="Recommended_alice_movies-4k",
            private_label="PrivateCollection_alice_movies-4k",
        )

        stale.editTitle.assert_called_once_with("Recommended movies - Alice (Movies 4K)")

    def test_no_op_when_old_named_collection_absent(self):
        """No collection carries our private_label - nothing to rename,
        just create as normal. No error."""
        from utils.plex import update_plex_collection

        mock_section = Mock()
        mock_section.collections.return_value = []
        mock_section.createCollection.return_value = Mock(labels=[])

        result = update_plex_collection(
            mock_section,
            "Recommended movies - Alice",
            [Mock()],
            label_name="Recommended_alice",
            private_label="PrivateCollection_alice",
        )

        assert result is True
        mock_section.createCollection.assert_called_once()

    def test_no_op_when_template_renders_unchanged(self):
        """Existing collection's title already equals the freshly-rendered
        name - no rename call, no duplicate creation."""
        from utils.plex import update_plex_collection

        current = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        mock_section = Mock()
        mock_section.collections.return_value = [current]

        result = update_plex_collection(
            mock_section,
            "🎬 Alice - Recommendation",
            [Mock()],
            label_name="Recommended_alice",
            private_label="PrivateCollection_alice",
        )

        assert result is True
        current.editTitle.assert_not_called()
        current.addItems.assert_called_once()
        mock_section.createCollection.assert_not_called()

    def test_does_not_rename_when_both_old_and_new_named_collections_exist(self):
        """Both an old-named (stale-labeled) and a new-named (exact title
        match) collection already exist - renaming would produce a
        duplicate title. Leave both alone; sync continues against the
        new-named one."""
        from utils.plex import update_plex_collection

        stale = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        current = self._make_collection("Recommended movies - Alice", labels=["PrivateCollection_alice"])
        mock_section = Mock()
        mock_section.collections.return_value = [stale, current]

        result = update_plex_collection(
            mock_section,
            "Recommended movies - Alice",
            [Mock()],
            label_name="Recommended_alice",
            private_label="PrivateCollection_alice",
        )

        assert result is True
        stale.editTitle.assert_not_called()
        current.editTitle.assert_not_called()
        current.addItems.assert_called_once()
        mock_section.createCollection.assert_not_called()

    def test_does_not_rename_when_ambiguous_multiple_stale_matches(self):
        """More than one collection carries our private_label with a
        mismatched title - never guess which to rename; leave all alone
        and create a new one like pre-#267-rename behavior."""
        from utils.plex import update_plex_collection

        stale_1 = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        stale_2 = self._make_collection("Old duplicate - Alice", labels=["PrivateCollection_alice"])
        mock_section = Mock()
        mock_section.collections.return_value = [stale_1, stale_2]
        mock_section.createCollection.return_value = Mock(labels=[])

        result = update_plex_collection(
            mock_section,
            "Recommended movies - Alice",
            [Mock()],
            label_name="Recommended_alice",
            private_label="PrivateCollection_alice",
        )

        assert result is True
        stale_1.editTitle.assert_not_called()
        stale_2.editTitle.assert_not_called()
        mock_section.createCollection.assert_called_once()

    def test_rename_disabled_via_flag_creates_duplicate_instead(self):
        """collections.rename_on_template_change: false - old orphaning
        behavior is preserved verbatim."""
        from utils.plex import update_plex_collection

        stale = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        mock_section = Mock()
        mock_section.collections.return_value = [stale]
        mock_section.createCollection.return_value = Mock(labels=[])

        result = update_plex_collection(
            mock_section,
            "Recommended movies - Alice",
            [Mock()],
            label_name="Recommended_alice",
            private_label="PrivateCollection_alice",
            rename_on_template_change=False,
        )

        assert result is True
        stale.editTitle.assert_not_called()
        mock_section.createCollection.assert_called_once()

    def test_no_rename_search_without_private_label(self):
        """No private_label given at all (e.g. a caller that never sets
        one) - behaves exactly like before this feature existed."""
        from utils.plex import update_plex_collection

        stale = self._make_collection("🎬 Alice - Recommendation")
        mock_section = Mock()
        mock_section.collections.return_value = [stale]

        result = update_plex_collection(mock_section, "Recommended movies - Alice", [Mock()])

        assert result is True
        stale.editTitle.assert_not_called()
        mock_section.createCollection.assert_called_once()

    def test_rename_failure_falls_back_to_creating_new_collection(self):
        """editTitle raising must not lose this run's sync - fall back to
        creating a new collection rather than failing outright."""
        from utils.plex import update_plex_collection

        stale = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        stale.editTitle.side_effect = plexapi.exceptions.PlexApiException("locked field")
        mock_section = Mock()
        mock_section.collections.return_value = [stale]
        mock_section.createCollection.return_value = Mock(labels=[])

        result = update_plex_collection(
            mock_section,
            "Recommended movies - Alice",
            [Mock()],
            label_name="Recommended_alice",
            private_label="PrivateCollection_alice",
        )

        assert result is True
        mock_section.createCollection.assert_called_once()

    def test_one_users_rename_never_touches_another_users_collection(self):
        """Two users' collections coexist in the same library, each
        carrying their own private_label - renaming alice's must never
        touch bob's, even though both are stale relative to their own
        new names."""
        from utils.plex import update_plex_collection

        alice_stale = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        bob_stale = self._make_collection("🎬 Bob - Recommendation", labels=["PrivateCollection_bob"])
        mock_section = Mock()
        mock_section.collections.return_value = [alice_stale, bob_stale]

        update_plex_collection(
            mock_section,
            "Recommended movies - Alice",
            [Mock()],
            label_name="Recommended_alice",
            private_label="PrivateCollection_alice",
        )

        alice_stale.editTitle.assert_called_once_with("Recommended movies - Alice")
        bob_stale.editTitle.assert_not_called()

    def test_legacy_private_labels_extends_the_stale_search(self):
        """#357 compound case: rename_on_template_change fires on the
        very first run since a movie_name_template/tv_name_template edit,
        for a user whose existing collection still carries only the
        pre-#352 legacy label (never refreshed - e.g. it predates #352,
        or recommend_for_no_history was toggled off immediately after
        upgrading). private_label alone (the current, normalized form)
        cannot find it - only legacy_private_labels lets the search
        recognize it as the SAME collection to rename, rather than
        leaving it an orphan while a second, new-named duplicate gets
        created."""
        from utils.plex import update_plex_collection

        stale = self._make_collection("🎬 Alex Pigot - Recommendation", labels=["PrivateCollection_Alex_Pigot"])
        mock_section = Mock()
        mock_section.collections.return_value = [stale]

        result = update_plex_collection(
            mock_section,
            "Recommended movies - Alex Pigot",
            [Mock()],
            label_name="Recommended_alexpigot",
            private_label="PrivateCollection_alexpigot",
            legacy_private_labels=["PrivateCollection_Alex_Pigot"],
        )

        assert result is True
        stale.editTitle.assert_called_once_with("Recommended movies - Alex Pigot")
        mock_section.createCollection.assert_not_called()


class TestUpdatePlexCollectionOldLabelUpgrade:
    """Pins down the fix/352 upgrade property: the existing_collection
    lookup above (line ~873) matches purely on collection.title ==
    collection_name - never on label - so a user whose collection already
    has the right title but still carries only the pre-#352 old-form
    label (e.g. "PrivateCollection_Alex_Pigot", built from raw config
    text) is found and updated in place on the first post-upgrade run.
    The new, normalized label (e.g. "PrivateCollection_alexpigot") is
    then added additively (line ~980-984) - the old form is never
    stripped here.

    If existing_collection lookup were ever changed to match by label
    instead of by title, this collection - whose only label is the OLD
    form - would not be found (private_label passed in is the NEW form),
    and _find_stale_owned_collections wouldn't match it either (its
    title already equals collection_name, so it isn't "stale-titled").
    With no match either way, update_plex_collection falls through to
    section.createCollection(...), producing a second collection with
    the IDENTICAL title - the exact duplicate this test guards against.
    """

    @staticmethod
    def _make_collection(title, labels=()):
        collection = Mock()
        collection.title = title
        collection.items.return_value = [Mock()]
        collection.labels = [Mock(tag=t) for t in labels]
        return collection

    def test_post_upgrade_run_updates_in_place_no_duplicate_collection(self):
        from utils.plex import update_plex_collection

        existing = self._make_collection("Recommended movies - Alex Pigot", labels=["PrivateCollection_Alex_Pigot"])
        mock_section = Mock()
        mock_section.collections.return_value = [existing]

        result = update_plex_collection(
            mock_section,
            "Recommended movies - Alex Pigot",
            [Mock()],
            label_name="Recommended_alexpigot",
            private_label="PrivateCollection_alexpigot",
        )

        assert result is True

        # Exactly one collection exists afterward - found by title and
        # updated in place, never duplicated.
        mock_section.createCollection.assert_not_called()
        existing.addItems.assert_called_once()

        # New, normalized label form was added additively...
        existing.addLabel.assert_called_once_with("PrivateCollection_alexpigot")
        # ...while the old form is still present (never stripped here).
        old_form_labels = [label.tag for label in existing.labels]
        assert "PrivateCollection_Alex_Pigot" in old_form_labels
        existing.removeLabel.assert_not_called()


class TestRemoveOwnedCollection:
    """Tests for utils.plex.remove_owned_collection (#291
    recommend_for_no_history: false path) - the removal path's actual
    find/confirm/remove logic. Ownership is confirmed ONLY via the
    PrivateCollection_<user> label already on the collection (the same
    marker TestUpdatePlexCollectionRenameOnTemplateChange's
    rename-on-template-change coverage above trusts, #274) - never
    inferred from title, emoji, or name pattern."""

    @staticmethod
    def _make_collection(title, labels=()):
        collection = Mock()
        collection.title = title
        collection.labels = [Mock(tag=t) for t in labels]
        return collection

    def test_removes_collection_confirmed_by_label(self):
        from utils.plex import remove_owned_collection

        owned = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        mock_section = Mock()
        mock_section.collections.return_value = [owned]

        result = remove_owned_collection(mock_section, "PrivateCollection_alice", "alice", "no watch history")

        assert result is True
        owned.delete.assert_called_once()

    def test_does_not_remove_when_label_absent(self):
        """A collection (even one with a matching title) that doesn't
        carry the PrivateCollection_<user> label is never removed -
        ownership must be positively confirmed, never guessed."""
        from utils.plex import remove_owned_collection

        unrelated = self._make_collection("🎬 Alice - Recommendation")  # no labels at all
        mock_section = Mock()
        mock_section.collections.return_value = [unrelated]

        result = remove_owned_collection(mock_section, "PrivateCollection_alice", "alice", "no watch history")

        assert result is False
        unrelated.delete.assert_not_called()

    def test_does_not_remove_when_ambiguous(self):
        """More than one collection carries this same private_label -
        never guess which one to delete; leave all alone and log why."""
        from utils.plex import remove_owned_collection

        dup_1 = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        dup_2 = self._make_collection("Old duplicate - Alice", labels=["PrivateCollection_alice"])
        mock_section = Mock()
        mock_section.collections.return_value = [dup_1, dup_2]
        mock_logger = Mock()

        result = remove_owned_collection(
            mock_section, "PrivateCollection_alice", "alice", "no watch history", logger=mock_logger
        )

        assert result is False
        dup_1.delete.assert_not_called()
        dup_2.delete.assert_not_called()
        mock_logger.warning.assert_called_once()
        assert "ambiguous" in mock_logger.warning.call_args[0][0]

    def test_never_touches_another_users_collection(self):
        """Two users' collections coexist in the same library, each with
        their own private_label - removing alice's must never touch
        bob's."""
        from utils.plex import remove_owned_collection

        alice_owned = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        bob_owned = self._make_collection("🎬 Bob - Recommendation", labels=["PrivateCollection_bob"])
        mock_section = Mock()
        mock_section.collections.return_value = [alice_owned, bob_owned]

        remove_owned_collection(mock_section, "PrivateCollection_alice", "alice", "no watch history")

        alice_owned.delete.assert_called_once()
        bob_owned.delete.assert_not_called()

    def test_logs_removal_with_user_collection_and_reason(self):
        """Every removal is logged at a visible level - never silent,
        even when configured."""
        from utils.plex import remove_owned_collection

        owned = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        mock_section = Mock()
        mock_section.collections.return_value = [owned]
        mock_logger = Mock()

        remove_owned_collection(
            mock_section,
            "PrivateCollection_alice",
            "alice",
            "no watch history and recommend_for_no_history is disabled",
            logger=mock_logger,
        )

        mock_logger.warning.assert_called_once()
        message = mock_logger.warning.call_args[0][0]
        assert "alice" in message
        assert "🎬 Alice - Recommendation" in message
        assert "no watch history" in message

    def test_no_matching_collection_is_a_silent_no_op(self):
        from utils.plex import remove_owned_collection

        mock_section = Mock()
        mock_section.collections.return_value = []

        result = remove_owned_collection(mock_section, "PrivateCollection_alice", "alice", "no watch history")

        assert result is False

    def test_delete_failure_logs_and_returns_false(self):
        from utils.plex import remove_owned_collection

        owned = self._make_collection("🎬 Alice - Recommendation", labels=["PrivateCollection_alice"])
        owned.delete.side_effect = plexapi.exceptions.PlexApiException("locked")
        mock_section = Mock()
        mock_section.collections.return_value = [owned]
        mock_logger = Mock()

        result = remove_owned_collection(
            mock_section, "PrivateCollection_alice", "alice", "no watch history", logger=mock_logger
        )

        assert result is False
        mock_logger.warning.assert_called_once()

    def test_list_collections_failure_logs_and_returns_false(self):
        from utils.plex import remove_owned_collection

        mock_section = Mock()
        mock_section.collections.side_effect = plexapi.exceptions.PlexApiException("boom")
        mock_logger = Mock()

        result = remove_owned_collection(
            mock_section, "PrivateCollection_alice", "alice", "no watch history", logger=mock_logger
        )

        assert result is False
        mock_logger.warning.assert_called_once()

    def test_matches_legacy_label_form_when_given_a_list_of_forms(self):
        """#357: a recommend_for_no_history: false user's collection may
        still carry only the pre-#352 legacy label form (never refreshed,
        since this path never goes through manage_plex_labels). Passing
        both the current and legacy forms as a list must still find and
        remove it - matching by ANY form, not just the first."""
        from utils.plex import remove_owned_collection

        owned = self._make_collection("🎬 Alex Pigot - Recommendation", labels=["PrivateCollection_Alex_Pigot"])
        mock_section = Mock()
        mock_section.collections.return_value = [owned]

        result = remove_owned_collection(
            mock_section,
            ["PrivateCollection_alexpigot", "PrivateCollection_Alex_Pigot"],
            "alexpigot",
            "no watch history",
        )

        assert result is True
        owned.delete.assert_called_once()

    def test_a_bare_string_is_never_iterated_character_by_character(self):
        """Guards _as_label_forms' str-vs-iterable branch: passing a bare
        string must behave exactly as it always has, never get split into
        individual characters as bare `for label in private_label` would
        do."""
        from utils.plex import remove_owned_collection

        collection_named_after_one_letter = self._make_collection("Stray", labels=["P"])
        mock_section = Mock()
        mock_section.collections.return_value = [collection_named_after_one_letter]

        result = remove_owned_collection(mock_section, "PrivateCollection_alice", "alice", "no watch history")

        assert result is False
        collection_named_after_one_letter.delete.assert_not_called()


class TestCleanupOldCollectionsAdvanced:
    """Additional tests for cleanup_old_collections()."""

    def test_logs_warning_on_error_with_logger(self):
        """Test logging warning on error with logger."""
        from utils.plex import cleanup_old_collections

        mock_logger = Mock()
        mock_section = Mock()
        mock_section.collections.side_effect = plexapi.exceptions.PlexApiException("Error")

        cleanup_old_collections(mock_section, "Test", "user", "🎬", logger=mock_logger)

        mock_logger.warning.assert_called_once()

    def test_deletes_by_username_match(self):
        """Test deleting collections that contain username and Recommend."""
        from utils.plex import cleanup_old_collections

        mock_collection = Mock()
        mock_collection.title = "Some john Recommended"

        mock_section = Mock()
        mock_section.collections.return_value = [mock_collection]

        cleanup_old_collections(mock_section, "New Collection", "john", "🎬")

        mock_collection.delete.assert_called_once()


class TestIdentifyDroppedShows:
    """Tests for identify_dropped_shows() function."""

    def test_identifies_dropped_show(self):
        """Test identifying a show as dropped."""
        from utils.plex import identify_dropped_shows

        show_data = {1: {"watched_episodes": 3, "completion_percent": 15, "total_episodes": 20}}
        config = {
            "negative_signals": {
                "enabled": True,
                "dropped_shows": {"enabled": True, "min_episodes_watched": 2, "max_completion_percent": 25},
            }
        }

        result = identify_dropped_shows(show_data, config)

        assert 1 in result

    def test_does_not_drop_completed_show(self):
        """Test that completed shows are not marked as dropped."""
        from utils.plex import identify_dropped_shows

        show_data = {1: {"watched_episodes": 10, "completion_percent": 80, "total_episodes": 12}}
        config = {
            "negative_signals": {
                "enabled": True,
                "dropped_shows": {"enabled": True, "min_episodes_watched": 2, "max_completion_percent": 25},
            }
        }

        result = identify_dropped_shows(show_data, config)

        assert 1 not in result

    def test_skips_show_with_too_few_watched(self):
        """Test that shows with too few watched episodes are skipped."""
        from utils.plex import identify_dropped_shows

        show_data = {
            1: {
                "watched_episodes": 1,  # Less than min_episodes_watched
                "completion_percent": 5,
                "total_episodes": 20,
            }
        }
        config = {
            "negative_signals": {
                "enabled": True,
                "dropped_shows": {"enabled": True, "min_episodes_watched": 2, "max_completion_percent": 25},
            }
        }

        result = identify_dropped_shows(show_data, config)

        assert 1 not in result

    def test_skips_short_series(self):
        """Test that short series are not marked as dropped."""
        from utils.plex import identify_dropped_shows

        show_data = {
            1: {
                "watched_episodes": 2,
                "completion_percent": 50,
                "total_episodes": 2,  # Total equals min_episodes_watched
            }
        }
        config = {
            "negative_signals": {
                "enabled": True,
                "dropped_shows": {"enabled": True, "min_episodes_watched": 2, "max_completion_percent": 25},
            }
        }

        result = identify_dropped_shows(show_data, config)

        assert 1 not in result

    def test_returns_empty_when_disabled(self):
        """Test that empty set is returned when feature is disabled."""
        from utils.plex import identify_dropped_shows

        show_data = {1: {"watched_episodes": 3, "completion_percent": 15, "total_episodes": 20}}
        config = {"negative_signals": {"enabled": False}}

        result = identify_dropped_shows(show_data, config)

        assert result == set()

    def test_returns_empty_when_dropped_shows_disabled(self):
        """Test that empty set is returned when dropped_shows is disabled."""
        from utils.plex import identify_dropped_shows

        show_data = {1: {"watched_episodes": 3, "completion_percent": 15, "total_episodes": 20}}
        config = {"negative_signals": {"enabled": True, "dropped_shows": {"enabled": False}}}

        result = identify_dropped_shows(show_data, config)

        assert result == set()


class TestFetchShowCompletionData:
    """Tests for fetch_show_completion_data() function."""

    @patch("utils.plex.requests.get")
    def test_returns_empty_dict_on_error(self, mock_get):
        """Test that empty dict is returned on API error."""
        from utils.plex import fetch_show_completion_data

        mock_get.side_effect = requests.RequestException("API Error")

        config = {"plex": {"url": "http://localhost", "token": "test", "verify_ssl": False}}
        mock_section = Mock()
        mock_section.key = "1"
        mock_section.all.return_value = []

        result = fetch_show_completion_data(config, ["account1"], mock_section)

        assert result == {}

    @patch("utils.plex.requests.get")
    def test_processes_episode_data(self, mock_get):
        """Test processing episode watch data."""
        from utils.plex import fetch_show_completion_data

        # Mock response XML
        xml_response = b"""<?xml version="1.0"?>
        <MediaContainer>
            <Video type="episode" grandparentKey="/library/metadata/100" ratingKey="200" viewedAt="1704067200"/>
        </MediaContainer>"""

        mock_response = Mock()
        # _capped_get/_capped_put now stream+cap the response body (see
        # utils.helpers.read_response_capped) - a real requests.Response
        # supports .headers (a real Mapping) and .iter_content()
        # natively; a plain Mock() needs both spelled out explicitly so
        # that code path doesn't choke on auto-generated Mock attributes.
        # Doesn't touch .content (a separate, independently-mocked
        # attribute here, not derived from it the way a real
        # Response's is).
        mock_response.headers = {}
        mock_response.iter_content = Mock(return_value=[])
        mock_response.content = xml_response
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Mock show in library
        mock_show = Mock()
        mock_show.ratingKey = 100
        mock_show.title = "Test Show"
        mock_episode = Mock()
        mock_show.episodes.return_value = [mock_episode] * 10

        mock_section = Mock()
        mock_section.key = "1"
        mock_section.all.return_value = [mock_show]

        config = {"plex": {"url": "http://localhost", "token": "test", "verify_ssl": False}}

        result = fetch_show_completion_data(config, ["account1"], mock_section)

        assert 100 in result
        assert result[100]["watched_episodes"] == 1
        assert result[100]["total_episodes"] == 10


class TestUpdatePlexCollectionSort:
    """Tests for collection sorting in update_plex_collection()."""

    def test_sets_custom_sort_order(self):
        """Test that custom sort order is set on collection."""
        from utils.plex import update_plex_collection

        mock_item1 = Mock()
        mock_item2 = Mock()
        items = [mock_item1, mock_item2]

        mock_collection = Mock()
        mock_section = Mock()
        mock_section.collections.return_value = []
        mock_section.createCollection.return_value = mock_collection

        update_plex_collection(mock_section, "Test Collection", items)

        mock_collection.sortUpdate.assert_called_once_with(sort="custom")

    def test_moves_items_in_order(self):
        """Test that items are moved in correct order."""
        from utils.plex import update_plex_collection

        mock_item1 = Mock()
        mock_item2 = Mock()
        mock_item3 = Mock()
        items = [mock_item1, mock_item2, mock_item3]

        mock_collection = Mock()
        mock_section = Mock()
        mock_section.collections.return_value = []
        mock_section.createCollection.return_value = mock_collection

        update_plex_collection(mock_section, "Test Collection", items)

        # Should call moveItem for each item in reverse order
        assert mock_collection.moveItem.call_count == 3

    def test_handles_sort_error_gracefully(self):
        """Test that sort errors are handled gracefully."""
        from utils.plex import update_plex_collection

        mock_item1 = Mock()
        mock_item2 = Mock()
        items = [mock_item1, mock_item2]

        mock_collection = Mock()
        mock_collection.sortUpdate.side_effect = plexapi.exceptions.PlexApiException("Sort error")
        mock_section = Mock()
        mock_section.collections.return_value = []
        mock_section.createCollection.return_value = mock_collection

        mock_logger = Mock()

        # Should not raise, just log warning
        result = update_plex_collection(mock_section, "Test Collection", items, logger=mock_logger)

        assert result is True
        mock_logger.warning.assert_called_once()


class TestApplyUserLabelRestrictions:
    """Tests for apply_user_label_restrictions() function."""

    @patch("utils.plex_policy.requests.put")
    @patch("utils.plex_policy.requests.get")
    @patch("utils.plex_policy.MyPlexAccount")
    def test_exclude_filter_uses_passed_labels_verbatim(self, mock_account_class, mock_get, mock_put):
        """#261: exclude labels are used exactly as passed in, never
        derived by string-replacing a "Recommended_" prefix off them -
        that derivation silently produced the wrong (unprefixed) label
        whenever a caller's label wasn't literally "Recommended_<user>",
        e.g. every install running with collections.append_usernames:
        false. Passing deliberately odd label strings here (no
        "Recommended_"/"PrivateCollection_" prefix at all) proves
        nothing is being rewritten internally."""
        mock_account = Mock()
        mock_account.username = "adminuser"
        mock_account_class.return_value = mock_account

        mock_get_response = Mock()
        mock_get_response.headers = {}
        mock_get_response.iter_content = Mock(return_value=[])
        mock_get_response.content = b"""<MediaContainer>
            <User id="123" title="Jason" username="jason"/>
            <User id="456" title="Sarah" username="sarah"/>
        </MediaContainer>"""
        mock_get_response.raise_for_status = Mock()
        mock_get.return_value = mock_get_response

        mock_put_response = Mock()
        mock_put_response.headers = {}
        mock_put_response.iter_content = Mock(return_value=[])
        mock_put_response.raise_for_status = Mock()
        mock_put.return_value = mock_put_response

        config = {"plex": {"token": "test_token"}}
        all_user_private_labels = {"Jason": "SomeOtherLabel_Jason", "Sarah": "SomeOtherLabel_Sarah"}

        result = apply_user_label_restrictions(config, all_user_private_labels)

        assert result is True
        put_filter_values = {call.kwargs["params"]["filterMovies"] for call in mock_put.call_args_list}
        assert put_filter_values == {"label!=SomeOtherLabel_Sarah", "label!=SomeOtherLabel_Jason"}

    @patch("utils.plex_policy.requests.put")
    @patch("utils.plex_policy.requests.get")
    @patch("utils.plex_policy.MyPlexAccount")
    def test_applies_exclude_restrictions_to_users(self, mock_account_class, mock_get, mock_put):
        """Test that exclude restrictions are applied to each user."""

        # Setup mock account
        mock_account = Mock()
        mock_account.username = "AdminUser"
        mock_account_class.return_value = mock_account

        # Setup mock GET response for users list (XML)
        mock_get_response = Mock()
        mock_get_response.headers = {}
        mock_get_response.iter_content = Mock(return_value=[])
        mock_get_response.content = b"""<MediaContainer>
            <User id="123" title="Jason" username="jason"/>
            <User id="456" title="Sarah" username="sarah"/>
        </MediaContainer>"""
        mock_get_response.raise_for_status = Mock()
        mock_get.return_value = mock_get_response

        # Setup mock PUT response
        mock_put_response = Mock()
        mock_put_response.headers = {}
        mock_put_response.iter_content = Mock(return_value=[])
        mock_put_response.raise_for_status = Mock()
        mock_put.return_value = mock_put_response

        config = {"plex": {"token": "test_token", "server_name": "MyServer"}}

        all_user_labels = {"Jason": "Recommended_Jason", "Sarah": "Recommended_Sarah"}

        result = apply_user_label_restrictions(config, all_user_labels)

        assert result is True
        # Should be called twice (once for each non-admin user)
        assert mock_put.call_count == 2

    @patch("utils.plex_policy.requests.put")
    @patch("utils.plex_policy.requests.get")
    @patch("utils.plex_policy.MyPlexAccount")
    def test_applied_exclusions_log_line_includes_the_account_id(self, mock_account_class, mock_get, mock_put, capsys):
        """#359: the account id must be in the "Applied exclusions"
        line, not just the display name - two distinct Plex accounts
        that happen to share a name/email (e.g. Plex Home profiles) are
        otherwise indistinguishable from a single account being written
        to redundantly, purely from the log."""
        mock_account = Mock()
        mock_account.username = "adminuser"
        mock_account_class.return_value = mock_account

        mock_get_response = Mock()
        mock_get_response.headers = {}
        mock_get_response.iter_content = Mock(return_value=[])
        mock_get_response.content = b"""<MediaContainer>
            <User id="123" title="Jason" username="jason"/>
            <User id="456" title="Sarah" username="sarah"/>
        </MediaContainer>"""
        mock_get_response.raise_for_status = Mock()
        mock_get.return_value = mock_get_response

        mock_put_response = Mock()
        mock_put_response.headers = {}
        mock_put_response.iter_content = Mock(return_value=[])
        mock_put_response.raise_for_status = Mock()
        mock_put.return_value = mock_put_response

        config = {"plex": {"token": "test_token"}}
        all_user_labels = {"Jason": "PrivateCollection_Jason", "Sarah": "PrivateCollection_Sarah"}

        result = apply_user_label_restrictions(config, all_user_labels)

        assert result is True
        out = capsys.readouterr().out
        assert "Applied exclusions for Jason" in out
        assert "123" in out, "account id missing from the applied-exclusions log line"
        assert "Applied exclusions for Sarah" in out
        assert "456" in out, "account id missing from the applied-exclusions log line"

    @patch("utils.plex_policy.requests.put")
    @patch("utils.plex_policy.requests.get")
    @patch("utils.plex_policy.MyPlexAccount")
    def test_skips_admin_user(self, mock_account_class, mock_get, mock_put):
        """Test that admin user is skipped (can't have restrictions)."""

        mock_account = Mock()
        mock_account.username = "AdminUser"
        mock_account_class.return_value = mock_account

        mock_get_response = Mock()
        mock_get_response.headers = {}
        mock_get_response.iter_content = Mock(return_value=[])
        mock_get_response.content = b"""<MediaContainer>
            <User id="123" title="OtherUser" username="otheruser"/>
        </MediaContainer>"""
        mock_get_response.raise_for_status = Mock()
        mock_get.return_value = mock_get_response

        mock_put_response = Mock()
        mock_put_response.headers = {}
        mock_put_response.iter_content = Mock(return_value=[])
        mock_put_response.raise_for_status = Mock()
        mock_put.return_value = mock_put_response

        config = {"plex": {"token": "test_token"}}

        all_user_labels = {"AdminUser": "Recommended_AdminUser", "OtherUser": "Recommended_OtherUser"}

        result = apply_user_label_restrictions(config, all_user_labels)

        assert result is True
        # Should only be called once (for OtherUser, not AdminUser)
        mock_put.assert_called_once()

    @patch("utils.plex_policy.requests.put")
    @patch("utils.plex_policy.requests.get")
    @patch("utils.plex_policy.MyPlexAccount")
    def test_returns_false_for_unknown_user(self, mock_account_class, mock_get, mock_put):
        """Test that unknown users result in partial failure."""

        mock_account = Mock()
        mock_account.username = "AdminUser"
        mock_account_class.return_value = mock_account

        mock_get_response = Mock()
        mock_get_response.headers = {}
        mock_get_response.iter_content = Mock(return_value=[])
        mock_get_response.content = b"""<MediaContainer>
            <User id="123" title="KnownUser" username="knownuser"/>
        </MediaContainer>"""
        mock_get_response.raise_for_status = Mock()
        mock_get.return_value = mock_get_response

        mock_put_response = Mock()
        mock_put_response.headers = {}
        mock_put_response.iter_content = Mock(return_value=[])
        mock_put_response.raise_for_status = Mock()
        mock_put.return_value = mock_put_response

        config = {"plex": {"token": "test_token"}}

        all_user_labels = {"KnownUser": "Recommended_KnownUser", "UnknownUser": "Recommended_UnknownUser"}

        result = apply_user_label_restrictions(config, all_user_labels)

        # Returns False because one user wasn't found
        assert result is False
        # But should still apply restrictions for KnownUser
        mock_put.assert_called_once()

    @patch("utils.plex_policy.MyPlexAccount")
    def test_handles_plex_api_error(self, mock_account_class):
        """Test that PlexApiException is handled gracefully."""

        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("Auth failed")

        config = {"plex": {"token": "test_token"}}

        # Need multiple users to trigger API call (single user returns early)
        all_user_labels = {"TestUser": "Recommended_TestUser", "OtherUser": "Recommended_OtherUser"}

        result = apply_user_label_restrictions(config, all_user_labels)

        assert result is False

    @patch("utils.plex_policy.MyPlexAccount")
    def test_single_user_short_circuits_when_not_covering_the_server(self, mock_account_class):
        """With unconfigured-user coverage off, one user hides from nobody."""
        config = {"plex": {"token": "test_token"}}
        all_user_labels = {"Jason": "Recommended_Jason"}

        result = apply_user_label_restrictions(config, all_user_labels, restrict_unconfigured_users=False)

        assert result is True
        # No work to do, so no network at all.
        mock_account_class.assert_not_called()

    @patch("utils.plex_policy._capped_get")
    @patch("utils.plex_policy.MyPlexAccount")
    def test_single_user_still_proceeds_when_covering_the_server(self, mock_account_class, mock_get):
        """
        #332/#340: a lone CONFIGURED user still has a collection that
        every OTHER Plex user on the server should not see, so the old
        unconditional single-user short-circuit silently disabled that
        coverage entirely.
        """
        config = {"plex": {"token": "test_token"}}
        mock_account_class.return_value = Mock(username="admin")
        mock_get.return_value = Mock(content=b"<MediaContainer/>", raise_for_status=Mock())

        apply_user_label_restrictions(config, {"Jason": "Recommended_Jason"})

        mock_account_class.assert_called()

    @patch("utils.plex_policy.MyPlexAccount")
    def test_returns_true_for_empty_labels(self, mock_account_class):
        """Test that empty labels dict returns True."""

        config = {"plex": {"token": "test_token"}}

        result = apply_user_label_restrictions(config, {})

        assert result is True
        mock_account_class.assert_not_called()

    @patch("utils.plex_policy.requests.put")
    @patch("utils.plex_policy.requests.get")
    @patch("utils.plex_policy.MyPlexAccount")
    def test_case_insensitive_username_match(self, mock_account_class, mock_get, mock_put):
        """Test that username matching is case insensitive."""

        mock_account = Mock()
        mock_account.username = "AdminUser"
        mock_account_class.return_value = mock_account

        mock_get_response = Mock()
        mock_get_response.headers = {}
        mock_get_response.iter_content = Mock(return_value=[])
        mock_get_response.content = b"""<MediaContainer>
            <User id="123" title="TestUser" username="testuser"/>
        </MediaContainer>"""
        mock_get_response.raise_for_status = Mock()
        mock_get.return_value = mock_get_response

        mock_put_response = Mock()
        mock_put_response.headers = {}
        mock_put_response.iter_content = Mock(return_value=[])
        mock_put_response.raise_for_status = Mock()
        mock_put.return_value = mock_put_response

        config = {"plex": {"token": "test_token"}}

        # Use lowercase in the labels dict
        all_user_labels = {"testuser": "Recommended_testuser", "anotheruser": "Recommended_anotheruser"}

        result = apply_user_label_restrictions(config, all_user_labels)

        # Should still match TestUser despite case difference
        # Returns False because 'anotheruser' wasn't found, but TestUser was processed
        assert result is False
        mock_put.assert_called_once()


class TestContentRatingFilter:
    """Tests for content rating filter functions."""

    def test_get_max_rating_for_user_returns_rating(self):
        """Test getting max_rating for a user who has one configured."""
        from utils.plex_policy import get_max_rating_for_user

        user_prefs = {
            "kids": {"display_name": "Kids", "max_rating": "PG"},
            "teen": {"display_name": "Teen", "max_rating": "PG-13"},
        }

        assert get_max_rating_for_user(user_prefs, "kids") == "PG"
        assert get_max_rating_for_user(user_prefs, "teen") == "PG-13"

    def test_get_max_rating_for_user_returns_none_when_not_set(self):
        """Test getting max_rating returns None when not configured."""
        from utils.plex_policy import get_max_rating_for_user

        user_prefs = {
            "adult": {"display_name": "Adult"}  # No max_rating
        }

        assert get_max_rating_for_user(user_prefs, "adult") is None

    def test_get_max_rating_for_user_returns_none_for_unknown_user(self):
        """Test getting max_rating returns None for unknown user."""
        from utils.plex_policy import get_max_rating_for_user

        user_prefs = {"kids": {"max_rating": "PG"}}

        assert get_max_rating_for_user(user_prefs, "unknown") is None
        assert get_max_rating_for_user(user_prefs, None) is None

    def test_is_rating_allowed_movie_hierarchy(self):
        """Test movie rating hierarchy: G < PG < PG-13 < R < NC-17."""
        from utils.plex_policy import is_rating_allowed

        # PG-13 max rating
        assert is_rating_allowed("G", "PG-13", "movie") is True
        assert is_rating_allowed("PG", "PG-13", "movie") is True
        assert is_rating_allowed("PG-13", "PG-13", "movie") is True
        assert is_rating_allowed("R", "PG-13", "movie") is False
        assert is_rating_allowed("NC-17", "PG-13", "movie") is False

        # PG max rating
        assert is_rating_allowed("G", "PG", "movie") is True
        assert is_rating_allowed("PG", "PG", "movie") is True
        assert is_rating_allowed("PG-13", "PG", "movie") is False
        assert is_rating_allowed("R", "PG", "movie") is False

    def test_is_rating_allowed_tv_hierarchy(self):
        """Test TV rating hierarchy: TV-Y < TV-Y7 < TV-G < TV-PG < TV-14 < TV-MA."""
        from utils.plex_policy import is_rating_allowed

        # TV-PG max rating
        assert is_rating_allowed("TV-Y", "TV-PG", "tv") is True
        assert is_rating_allowed("TV-Y7", "TV-PG", "tv") is True
        assert is_rating_allowed("TV-G", "TV-PG", "tv") is True
        assert is_rating_allowed("TV-PG", "TV-PG", "tv") is True
        assert is_rating_allowed("TV-14", "TV-PG", "tv") is False
        assert is_rating_allowed("TV-MA", "TV-PG", "tv") is False

        # TV-14 max rating
        assert is_rating_allowed("TV-PG", "TV-14", "tv") is True
        assert is_rating_allowed("TV-14", "TV-14", "tv") is True
        assert is_rating_allowed("TV-MA", "TV-14", "tv") is False

    def test_is_rating_allowed_case_insensitive(self):
        """Test rating comparison is case insensitive."""
        from utils.plex_policy import is_rating_allowed

        assert is_rating_allowed("pg-13", "PG-13", "movie") is True
        assert is_rating_allowed("PG-13", "pg-13", "movie") is True
        assert is_rating_allowed("tv-pg", "TV-PG", "tv") is True

    def test_is_rating_allowed_no_max_rating(self):
        """Test that no max_rating allows all content."""
        from utils.plex_policy import is_rating_allowed

        assert is_rating_allowed("R", None, "movie") is True
        assert is_rating_allowed("NC-17", None, "movie") is True
        assert is_rating_allowed("TV-MA", None, "tv") is True

    def test_is_rating_allowed_no_content_rating(self):
        """Test that missing content_rating allows the content."""
        from utils.plex_policy import is_rating_allowed

        assert is_rating_allowed(None, "PG-13", "movie") is True
        assert is_rating_allowed("", "PG-13", "movie") is True

    def test_is_rating_allowed_unknown_rating(self):
        """Test that unknown ratings (NR, Unrated) are allowed."""
        from utils.plex_policy import is_rating_allowed

        assert is_rating_allowed("NR", "PG-13", "movie") is True
        assert is_rating_allowed("Unrated", "PG", "movie") is True
        assert is_rating_allowed("Not Rated", "R", "movie") is True


class TestResolvePlexUser:
    """
    resolve_plex_user() - config username -> MyPlexUser.

    config lists users by USERNAME ("homehouse165"); account.users()
    surfaces them by friendly TITLE ("home house"). Matching on title
    alone misses every user whose display name differs from their login,
    which on a real Plex Home is all of them.
    """

    @staticmethod
    def _user(username=None, email=None, title=None):
        u = Mock()
        u.username = username
        u.email = email
        u.title = title
        return u

    def test_matches_on_username_first(self):
        from utils.plex import resolve_plex_user

        target = self._user(username="homehouse165", title="home house")
        account = Mock(users=Mock(return_value=[self._user(username="other"), target]))
        assert resolve_plex_user(account, "homehouse165") is target

    def test_matches_on_email(self):
        from utils.plex import resolve_plex_user

        target = self._user(username="x", email="house11457@gmail.com", title="home house")
        account = Mock(users=Mock(return_value=[target]))
        assert resolve_plex_user(account, "house11457@gmail.com") is target

    def test_falls_back_to_title(self):
        from utils.plex import resolve_plex_user

        target = self._user(username=None, title="home house")
        account = Mock(users=Mock(return_value=[target]))
        assert resolve_plex_user(account, "home house") is target

    def test_username_wins_over_another_users_title(self):
        """A title collision must not outrank an exact username match."""
        from utils.plex import resolve_plex_user

        decoy = self._user(username="someoneelse", title="homehouse165")
        target = self._user(username="homehouse165", title="home house")
        account = Mock(users=Mock(return_value=[decoy, target]))
        assert resolve_plex_user(account, "homehouse165") is target

    def test_case_insensitive(self):
        from utils.plex import resolve_plex_user

        target = self._user(username="HomeHouse165")
        account = Mock(users=Mock(return_value=[target]))
        assert resolve_plex_user(account, "homehouse165") is target

    def test_unknown_user_returns_none(self):
        from utils.plex import resolve_plex_user

        account = Mock(users=Mock(return_value=[self._user(username="other")]))
        assert resolve_plex_user(account, "nobody") is None

    def test_empty_username_returns_none(self):
        from utils.plex import resolve_plex_user

        assert resolve_plex_user(Mock(), "") is None


class TestGetUserConnection:
    """get_user_connection() - a connection that sees the library as a given user."""

    def test_owner_gets_the_admin_connection_unchanged(self):
        """The owner is absent from account.users(); admin IS their connection."""
        from utils.plex import get_user_connection

        plex = Mock()
        plex.myPlexAccount.return_value = Mock(username="jasonsmith523")
        assert get_user_connection(plex, {}, "jasonsmith523") is plex
        plex.switchUser.assert_not_called()

    def test_switches_for_another_user(self):
        from utils.plex import get_user_connection

        target = Mock(username="homehouse165", email=None, title="home house")
        switched = Mock()
        plex = Mock()
        plex.myPlexAccount.return_value = Mock(username="jasonsmith523", users=Mock(return_value=[target]))
        plex.switchUser.return_value = switched
        assert get_user_connection(plex, {}, "homehouse165") is switched

    def test_no_username_returns_admin(self):
        from utils.plex import get_user_connection

        plex = Mock()
        assert get_user_connection(plex, {}, None) is plex

    def test_unresolvable_user_falls_back_to_admin(self):
        from utils.plex import get_user_connection

        plex = Mock()
        plex.myPlexAccount.return_value = Mock(username="owner", users=Mock(return_value=[]))
        assert get_user_connection(plex, {}, "ghost") is plex

    def test_switch_failure_falls_back_to_admin(self):
        """Degrade to previous behavior rather than failing the run."""
        from utils.plex import get_user_connection

        target = Mock(username="homehouse165", email=None, title="home house")
        plex = Mock()
        plex.myPlexAccount.return_value = Mock(username="owner", users=Mock(return_value=[target]))
        plex.switchUser.side_effect = plexapi.exceptions.PlexApiException("nope")
        assert get_user_connection(plex, {}, "homehouse165") is plex


class TestFetchUserPlayedIds:
    """fetch_user_played_ids() - that user's own watched rating keys."""

    @staticmethod
    def _plex_with(watched_keys):
        section = Mock()
        section.search.return_value = [Mock(ratingKey=k) for k in watched_keys]
        plex = Mock()
        plex.myPlexAccount.return_value = Mock(username="owner", users=Mock(return_value=[]))
        plex.library.section.return_value = section
        return plex, section

    def test_returns_rating_keys_as_ints(self):
        from utils.plex import fetch_user_played_ids

        plex, _ = self._plex_with(["1", "2"])
        assert fetch_user_played_ids(plex, {}, None, "Movies") == {1, 2}

    def test_falls_back_to_full_scan_when_filter_unsupported(self):
        """Older plexapi rejects search(unwatched=...)."""
        from utils.plex import fetch_user_played_ids

        plex, section = self._plex_with([])
        section.search.side_effect = TypeError("unexpected kwarg")
        section.all.return_value = [Mock(ratingKey=7, isPlayed=True), Mock(ratingKey=8, isPlayed=False)]
        assert fetch_user_played_ids(plex, {}, None, "Movies") == {7}

    def test_failure_returns_empty_set_not_everything(self):
        """
        Empty is the SAFE direction. An item wrongly thought unwatched is
        a redundant recommendation; one wrongly thought watched silently
        vanishes from consideration - the defect this fixes.
        """
        from utils.plex import fetch_user_played_ids

        plex = Mock()
        plex.myPlexAccount.return_value = Mock(username="owner", users=Mock(return_value=[]))
        plex.library.section.side_effect = plexapi.exceptions.PlexApiException("down")
        assert fetch_user_played_ids(plex, {}, "someone", "Movies") == set()

    def test_unexpected_api_shape_does_not_abort_a_run(self):
        """A Mock/malformed users() once raised TypeError straight through."""
        from utils.plex import fetch_user_played_ids

        plex = Mock()
        plex.myPlexAccount.return_value = Mock(username="owner")  # .users() -> non-iterable Mock
        plex.library.section.return_value = Mock(search=Mock(return_value=[]))
        assert fetch_user_played_ids(plex, {}, "someone", "Movies") == set()


class TestBuildAllPrivateLabels:
    """
    build_all_private_labels() - #332 claim 1.

    A user owns one PrivateCollection_* label PER LIBRARY, because
    recommenders/base.py roots it at "PrivateCollection" +
    _library_suffix_for_label(), which qualifies by library id whenever a
    media type has more than one library. Each media type's run therefore
    knew only its own labels, and since apply_user_label_restrictions()
    writes BOTH filterMovies and filterTelevision every time, the later
    run (TV) overwrote the earlier one's (movies).
    """

    @staticmethod
    def _config(movie_libs, tv_libs):
        libs = [{"id": i, "media_type": "movie", "name": i, "section": i} for i in movie_libs]
        libs += [{"id": i, "media_type": "tv", "name": i, "section": i} for i in tv_libs]
        return {"libraries": libs}

    def test_single_library_per_type_keeps_todays_unqualified_names(self):
        from utils.plex_policy import build_all_private_labels

        cfg = self._config(["movies"], ["tv-shows"])
        labels = build_all_private_labels(cfg, ["alice", "bob"], True)
        # Per media type (#340) - filterMovies and filterTelevision are
        # separate filters and must not receive each other's labels.
        assert labels["alice"] == {"movie": ["PrivateCollection_alice"], "tv": ["PrivateCollection_alice"]}
        assert labels["bob"]["movie"] == ["PrivateCollection_bob"]

    def test_multi_library_yields_a_label_per_library(self):
        """The reported case: movie labels must not be lost."""
        from utils.plex_policy import build_all_private_labels

        cfg = self._config(["movies", "movies4k"], ["tv-shows"])
        labels = build_all_private_labels(cfg, ["alice"], True)
        assert "PrivateCollection_movies_alice" in labels["alice"]["movie"]
        assert "PrivateCollection_movies4k_alice" in labels["alice"]["movie"]
        assert labels["alice"]["tv"] == ["PrivateCollection_alice"], "single-library TV label missing"
        # #340: movie labels must NOT leak into the television filter.
        assert "PrivateCollection_movies_alice" not in labels["alice"]["tv"]

    def test_no_duplicate_labels(self):
        from utils.plex_policy import build_all_private_labels

        cfg = self._config(["movies"], ["tv-shows"])
        labels = build_all_private_labels(cfg, ["alice"], True)
        for media_labels in labels["alice"].values():
            assert len(media_labels) == len(set(media_labels))

    def test_covers_every_user(self):
        from utils.plex_policy import build_all_private_labels

        cfg = self._config(["movies"], ["tv"])
        labels = build_all_private_labels(cfg, ["alice", "bob", "carol"], True)
        assert set(labels) == {"alice", "bob", "carol"}


class TestApplyRestrictionsCoverage:
    """
    apply_user_label_restrictions() - #332 claims 1 and 2.

    Claim 1: a user's labels are a list now, and all of them must land in
    the other users' exclude filters.
    Claim 2: a Plex user absent from config previously got no filter at
    all, so they saw everyone's collections.
    """

    USERS_XML = (
        b"<MediaContainer>"
        b'<User id="1" title="alice" username="alice" email="a@x"/>'
        b'<User id="2" title="bob" username="bob" email="b@x"/>'
        b'<User id="3" title="carol" username="carol" email="c@x"/>'
        b"</MediaContainer>"
    )

    def _run(self, labels, **kwargs):
        from utils import plex_policy

        put_calls = []

        def fake_put(url, params=None, **kw):
            put_calls.append((url, params))
            return Mock(raise_for_status=Mock())

        with (
            patch.object(plex_policy, "MyPlexAccount", return_value=Mock(username="admin")),
            patch.object(
                plex_policy, "_capped_get", return_value=Mock(content=self.USERS_XML, raise_for_status=Mock())
            ),
            patch.object(plex_policy, "_capped_put", side_effect=fake_put),
        ):
            ok = plex_policy.apply_user_label_restrictions({"plex": {"token": "t"}}, labels, **kwargs)
        return ok, put_calls

    def test_all_of_a_users_labels_are_excluded_from_others(self):
        """Claim 1: every library's label, not just one."""
        labels = {
            "alice": ["PrivateCollection_movies_alice", "PrivateCollection_tv_alice"],
            "bob": ["PrivateCollection_movies_bob"],
        }
        _ok, calls = self._run(labels)
        bob_filter = next(p["filterMovies"] for url, p in calls if url.endswith("/2"))
        assert "PrivateCollection_movies_alice" in bob_filter
        assert "PrivateCollection_tv_alice" in bob_filter, "a library's labels were dropped"

    def test_a_bare_string_still_works(self):
        """Callers predating #332 pass one label, not a list."""
        _ok, calls = self._run({"alice": "PrivateCollection_alice", "bob": "PrivateCollection_bob"})
        bob_filter = next(p["filterMovies"] for url, p in calls if url.endswith("/2"))
        assert "PrivateCollection_alice" in bob_filter

    def test_unconfigured_server_user_also_gets_restricted(self):
        """Claim 2: carol is on the server but not in config."""
        labels = {"alice": ["PrivateCollection_alice"], "bob": ["PrivateCollection_bob"]}
        _ok, calls = self._run(labels)
        carol = [p for url, p in calls if url.endswith("/3")]
        assert carol, "a Plex user absent from config received no restriction"
        assert "PrivateCollection_alice" in carol[0]["filterMovies"]
        assert "PrivateCollection_bob" in carol[0]["filterMovies"]

    def test_unconfigured_coverage_can_be_disabled(self):
        labels = {"alice": ["PrivateCollection_alice"], "bob": ["PrivateCollection_bob"]}
        _ok, calls = self._run(labels, restrict_unconfigured_users=False)
        assert not [p for url, p in calls if url.endswith("/3")]

    def test_a_user_never_has_their_own_label_excluded(self):
        labels = {"alice": ["PrivateCollection_alice"], "bob": ["PrivateCollection_bob"]}
        _ok, calls = self._run(labels)
        alice_filter = next(p["filterMovies"] for url, p in calls if url.endswith("/1"))
        assert "PrivateCollection_alice" not in alice_filter
        assert "PrivateCollection_bob" in alice_filter

    def test_both_media_filters_are_written(self):
        labels = {"alice": ["PrivateCollection_alice"], "bob": ["PrivateCollection_bob"]}
        _ok, calls = self._run(labels)
        _url, params = calls[0]
        assert params["filterMovies"] == params["filterTelevision"]


class TestExclusionLabelRegressions:
    """
    #340 - three defects introduced by the #332 fix, all confirmed on a
    live server before being fixed here.
    """

    # Deliberately gives one user three aliases, which is what Plex
    # actually returns and what the #332 fix mishandled.
    USERS_XML = (
        b"<MediaContainer>"
        b'<User id="1" title="home house" username="homehouse165" email="h@x"'
        b' filterMovies="" filterTelevision=""/>'
        b'<User id="2" title="Bob" username="bob" email="b@x" filterMovies="" filterTelevision=""/>'
        b"</MediaContainer>"
    )

    def _run(self, labels, users_xml=None, **kwargs):
        from utils import plex_policy

        puts = []

        def fake_put(url, params=None, **kw):
            puts.append((url, params))
            return Mock(raise_for_status=Mock())

        with (
            patch.object(plex_policy, "MyPlexAccount", return_value=Mock(username="admin")),
            patch.object(
                plex_policy,
                "_capped_get",
                return_value=Mock(content=users_xml or self.USERS_XML, raise_for_status=Mock()),
            ),
            patch.object(plex_policy, "_capped_put", side_effect=fake_put),
        ):
            ok = plex_policy.apply_user_label_restrictions({"plex": {"token": "t"}}, labels, **kwargs)
        return ok, puts

    def test_a_users_own_label_is_never_excluded_from_themselves(self):
        """
        The regression: Plex lists each user under title AND username AND
        email. Iterating those name keys treated the aliases of a
        configured user as separate unconfigured users, so that person's
        OWN collection was excluded from their own library.
        """
        labels = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
            "bob": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]},
        }
        _ok, puts = self._run(labels)
        home = [p for url, p in puts if url.endswith("/1")]
        assert home, "configured user received no restriction"
        assert "PrivateCollection_homehouse165" not in home[0]["filterMovies"]
        assert "PrivateCollection_bob" in home[0]["filterMovies"]

    def test_each_user_is_written_once_despite_three_aliases(self):
        """Same defect, other symptom: one PUT per alias, per run."""
        labels = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
            "bob": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]},
        }
        _ok, puts = self._run(labels)
        assert len([u for u, _ in puts if u.endswith("/1")]) == 1

    def test_movie_and_tv_labels_are_not_merged(self):
        """
        filterMovies governs movie libraries and filterTelevision
        television ones; a label from the other kind can never match
        there and does not belong in it.
        """
        labels = {
            "homehouse165": {"movie": ["PC_movies_home"], "tv": ["PC_tv_home"]},
            "bob": {"movie": ["PC_movies_bob"], "tv": ["PC_tv_bob"]},
        }
        _ok, puts = self._run(labels)
        home = next(p for url, p in puts if url.endswith("/1"))
        assert "PC_movies_bob" in home["filterMovies"]
        assert "PC_tv_bob" not in home["filterMovies"], "tv labels leaked into filterMovies"
        assert "PC_tv_bob" in home["filterTelevision"]
        assert "PC_movies_bob" not in home["filterTelevision"], "movie labels leaked into filterTelevision"

    def test_unchanged_filters_are_not_rewritten(self):
        """Every run re-PUT an identical filter for every user."""
        already = (
            b"<MediaContainer>"
            b'<User id="1" title="home house" username="homehouse165" email="h@x"'
            b' filterMovies="label!=PrivateCollection_bob" filterTelevision="label!=PrivateCollection_bob"/>'
            b'<User id="2" title="Bob" username="bob" email="b@x" filterMovies="" filterTelevision=""/>'
            b"</MediaContainer>"
        )
        labels = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
            "bob": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]},
        }
        _ok, puts = self._run(labels, users_xml=already)
        assert not [u for u, _ in puts if u.endswith("/1")], "rewrote an already-correct filter"
        assert [u for u, _ in puts if u.endswith("/2")], "bob's filter did need writing"

    def test_unconfigured_user_still_covered_and_gets_everything(self):
        """#332's second claim must keep working."""
        labels = {"bob": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]}}
        _ok, puts = self._run(labels)
        home = next(p for url, p in puts if url.endswith("/1"))
        assert "PrivateCollection_bob" in home["filterMovies"]


class TestPrivateLabelOwnerRetention:
    """
    #351: a user removed from config must not have their PrivateCollection_*
    label silently un-hidden from everyone else's filterMovies/
    filterTelevision. utils.plex_policy persists every owner it has
    applied that label for (cache/private_label_owners.json - see
    utils/private_label_cache.py) and retains a departed owner's label in
    the exclude computation until collections.prune_orphaned_private_labels
    explicitly tears it down.
    """

    USERS_XML = (
        b"<MediaContainer>"
        b'<User id="1" title="home house" username="homehouse165" email="h@x"'
        b' filterMovies="" filterTelevision=""/>'
        b'<User id="2" title="Bob" username="bob" email="b@x" filterMovies="" filterTelevision=""/>'
        b"</MediaContainer>"
    )

    BOTH_CONFIGURED = {
        "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
        "bob": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]},
    }
    ONLY_HOME_CONFIGURED = {
        "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
    }

    def _run(self, config, labels, cache_dir, users_xml=None, puts=None):
        from utils import plex_policy

        puts = puts if puts is not None else []

        def fake_put(url, params=None, **kw):
            puts.append((url, params))
            return Mock(raise_for_status=Mock())

        with (
            patch.object(plex_policy, "MyPlexAccount", return_value=Mock(username="admin")),
            patch.object(
                plex_policy,
                "_capped_get",
                return_value=Mock(content=users_xml or self.USERS_XML, raise_for_status=Mock()),
            ),
            patch.object(plex_policy, "_capped_put", side_effect=fake_put),
        ):
            ok = plex_policy.apply_user_label_restrictions(config, labels, cache_dir=str(cache_dir))
        return ok, puts

    def test_departed_owner_label_is_retained_across_a_subsequent_run(self, tmp_path):
        """Required regression #1: bob leaves config, but his label must
        still show up in home's exclude filter on the very next run."""
        config = {"plex": {"token": "t"}}
        self._run(config, self.BOTH_CONFIGURED, tmp_path)

        _ok, puts = self._run(config, self.ONLY_HOME_CONFIGURED, tmp_path)

        home_put = next(p for url, p in puts if url.endswith("/1"))
        assert "PrivateCollection_bob" in home_put["filterMovies"], "departed owner's label was dropped"
        assert "PrivateCollection_bob" in home_put["filterTelevision"], "departed owner's label was dropped"

    def test_self_exclusion_invariant_holds_with_departed_owners_present(self, tmp_path):
        """Required regression #2: retaining bob's label must never leak
        into home's OWN exclude filter."""
        config = {"plex": {"token": "t"}}
        self._run(config, self.BOTH_CONFIGURED, tmp_path)

        _ok, puts = self._run(config, self.ONLY_HOME_CONFIGURED, tmp_path)

        home_put = next(p for url, p in puts if url.endswith("/1"))
        assert "PrivateCollection_homehouse165" not in home_put["filterMovies"]
        assert "PrivateCollection_homehouse165" not in home_put["filterTelevision"]

    def test_prune_disabled_by_default_warns_but_never_deletes(self, tmp_path):
        """Required regression #3: prune is off by default - an orphan
        is detected and warned about, but nothing in Plex is touched and
        the owner stays tracked (i.e. their collection is left alone)."""
        from utils import plex_policy
        from utils.private_label_cache import load_private_label_owners

        config = {"plex": {"token": "t"}}
        self._run(config, self.BOTH_CONFIGURED, tmp_path)

        with (
            patch.object(plex_policy, "prune_orphaned_private_collections") as mock_prune,
            patch.object(plex_policy, "log_warning") as mock_warn,
        ):
            self._run(config, self.ONLY_HOME_CONFIGURED, tmp_path)

        mock_prune.assert_not_called()
        assert any("bob" in str(call) for call in mock_warn.call_args_list), "no warning named the departed owner"

        owners = load_private_label_owners(str(tmp_path))
        assert "2" in owners, "orphaned owner must stay tracked while prune is disabled"

    def test_prune_enabled_deletes_collection_updates_cache_and_stops_excluding(self, tmp_path):
        """Required regression #4: opting in tears the orphan down -
        collection deleted, cache entry dropped, and (once that takes
        effect) remaining users' filters stop carrying the label.

        Uses a THIRD always-configured user (carol) alongside home/bob so
        home's exclude list never drops to empty when bob departs - an
        exclude list going empty hits a separate, pre-existing "value
        unchanged from a caller's point of view" early-continue in
        apply_user_label_restrictions that skips the PUT needed to CLEAR
        an existing filter back down to nothing. That gap predates #351
        and isn't part of what this fix touches; carol keeps this test
        isolated to what #351 actually changed.
        """
        from utils.private_label_cache import load_private_label_owners

        users_xml = (
            b"<MediaContainer>"
            b'<User id="1" title="home house" username="homehouse165" email="h@x"'
            b' filterMovies="" filterTelevision=""/>'
            b'<User id="2" title="Bob" username="bob" email="b@x" filterMovies="" filterTelevision=""/>'
            b'<User id="3" title="Carol" username="carol" email="c@x" filterMovies="" filterTelevision=""/>'
            b"</MediaContainer>"
        )
        all_three = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
            "bob": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]},
            "carol": {"movie": ["PrivateCollection_carol"], "tv": ["PrivateCollection_carol"]},
        }
        home_and_carol = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
            "carol": {"movie": ["PrivateCollection_carol"], "tv": ["PrivateCollection_carol"]},
        }

        config = {"plex": {"token": "t"}}
        self._run(config, all_three, tmp_path, users_xml=users_xml)

        prune_config = {"plex": {"token": "t"}, "collections": {"prune_orphaned_private_labels": True}}

        mock_collection = Mock()
        mock_collection.title = "Bob's Collection"
        mock_collection.labels = [Mock(tag="PrivateCollection_bob")]
        mock_section = Mock()
        mock_section.collections.return_value = [mock_collection]
        mock_plex = Mock()
        mock_plex.library.section.return_value = mock_section

        puts_run2 = []
        with patch("utils.plex.init_plex", return_value=mock_plex):
            self._run(prune_config, home_and_carol, tmp_path, users_xml=users_xml, puts=puts_run2)

        assert mock_collection.delete.called, "orphaned collection was never deleted"

        owners_after = load_private_label_owners(str(tmp_path))
        assert "2" not in owners_after, "departed owner was not dropped from the cache"
        assert "1" in owners_after and "3" in owners_after, "still-configured owners must stay tracked"

        # Run 3 reflects what run 2 actually applied to home's filter
        # (the #340 no-op gate only PUTs a genuinely changed value), so
        # this proves pruning actually changes future behavior rather
        # than just a dict key disappearing.
        home_put_run2 = next(p for url, p in puts_run2 if url.endswith("/1"))
        xml_after_run2 = (
            b"<MediaContainer>"
            b'<User id="1" title="home house" username="homehouse165" email="h@x"'
            b' filterMovies="' + home_put_run2["filterMovies"].encode() + b'"'
            b' filterTelevision="' + home_put_run2["filterTelevision"].encode() + b'"/>'
            b'<User id="2" title="Bob" username="bob" email="b@x" filterMovies="" filterTelevision=""/>'
            b'<User id="3" title="Carol" username="carol" email="c@x" filterMovies="" filterTelevision=""/>'
            b"</MediaContainer>"
        )
        _ok, puts_run3 = self._run(prune_config, home_and_carol, tmp_path, users_xml=xml_after_run2)

        home_put_run3 = next(p for url, p in puts_run3 if url.endswith("/1"))
        assert "PrivateCollection_bob" not in home_put_run3["filterMovies"], "pruned owner's label still excluded"
        assert "PrivateCollection_carol" in home_put_run3["filterMovies"], "still-configured owner wrongly dropped"

    def test_prune_enabled_but_plex_unreachable_orphan_stays_tracked_and_excluded(self, tmp_path):
        """Regression for the #351 follow-up: prune_orphaned_private_collections
        returning cleanly with nothing deleted (Plex unreachable) must not
        be read by the caller as "go ahead and forget this owner". bob's
        entry has to survive in the persisted cache AND keep showing up
        in everyone else's exclude filter on the run after this one -
        exactly the gap test_connect_failure_returns_empty_list, checked
        only at the low-level prune function in isolation, could not
        catch.
        """
        from utils.private_label_cache import load_private_label_owners

        users_xml = (
            b"<MediaContainer>"
            b'<User id="1" title="home house" username="homehouse165" email="h@x"'
            b' filterMovies="" filterTelevision=""/>'
            b'<User id="2" title="Bob" username="bob" email="b@x" filterMovies="" filterTelevision=""/>'
            b'<User id="3" title="Carol" username="carol" email="c@x" filterMovies="" filterTelevision=""/>'
            b"</MediaContainer>"
        )
        all_three = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
            "bob": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]},
            "carol": {"movie": ["PrivateCollection_carol"], "tv": ["PrivateCollection_carol"]},
        }
        home_and_carol = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
            "carol": {"movie": ["PrivateCollection_carol"], "tv": ["PrivateCollection_carol"]},
        }

        config = {"plex": {"token": "t"}}
        self._run(config, all_three, tmp_path, users_xml=users_xml)

        prune_config = {"plex": {"token": "t"}, "collections": {"prune_orphaned_private_labels": True}}

        with patch("utils.plex.init_plex", side_effect=Exception("connection refused")):
            self._run(prune_config, home_and_carol, tmp_path, users_xml=users_xml)

        owners_after = load_private_label_owners(str(tmp_path))
        assert "2" in owners_after, "orphan was forgotten despite prune never reaching Plex"

        _ok, puts_run3 = self._run(prune_config, home_and_carol, tmp_path, users_xml=users_xml)
        home_put_run3 = next(p for url, p in puts_run3 if url.endswith("/1"))
        assert "PrivateCollection_bob" in home_put_run3["filterMovies"], "un-hid a collection that was never deleted"

    def test_prune_enabled_but_section_raises_orphan_stays_tracked_and_excluded(self, tmp_path):
        """Same gap as above, but the failure mode is a single library
        section raising PlexApiException mid-scan rather than Plex being
        fully unreachable - bob's collection is never located, so bob
        must stay tracked and excluded exactly as if prune had never run.
        """
        from utils.private_label_cache import load_private_label_owners

        users_xml = (
            b"<MediaContainer>"
            b'<User id="1" title="home house" username="homehouse165" email="h@x"'
            b' filterMovies="" filterTelevision=""/>'
            b'<User id="2" title="Bob" username="bob" email="b@x" filterMovies="" filterTelevision=""/>'
            b'<User id="3" title="Carol" username="carol" email="c@x" filterMovies="" filterTelevision=""/>'
            b"</MediaContainer>"
        )
        all_three = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
            "bob": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]},
            "carol": {"movie": ["PrivateCollection_carol"], "tv": ["PrivateCollection_carol"]},
        }
        home_and_carol = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
            "carol": {"movie": ["PrivateCollection_carol"], "tv": ["PrivateCollection_carol"]},
        }

        config = {"plex": {"token": "t"}}
        self._run(config, all_three, tmp_path, users_xml=users_xml)

        prune_config = {"plex": {"token": "t"}, "collections": {"prune_orphaned_private_labels": True}}

        mock_plex = Mock()
        mock_plex.library.section.side_effect = plexapi.exceptions.PlexApiException("section unavailable")

        with patch("utils.plex.init_plex", return_value=mock_plex):
            self._run(prune_config, home_and_carol, tmp_path, users_xml=users_xml)

        owners_after = load_private_label_owners(str(tmp_path))
        assert "2" in owners_after, "orphan was forgotten despite its section never being reachable"

        _ok, puts_run3 = self._run(prune_config, home_and_carol, tmp_path, users_xml=users_xml)
        home_put_run3 = next(p for url, p in puts_run3 if url.endswith("/1"))
        assert "PrivateCollection_bob" in home_put_run3["filterMovies"], "un-hid a collection that was never deleted"

    def test_corrupt_cache_file_degrades_safely(self, tmp_path):
        """Required regression #5: a corrupt/unreadable cache file must
        never crash a run - just behave as if nothing was persisted."""
        (tmp_path / "private_label_owners.json").write_text("not valid json", encoding="utf-8")

        config = {"plex": {"token": "t"}}
        ok, puts = self._run(config, self.ONLY_HOME_CONFIGURED, tmp_path)

        assert ok is True
        assert puts  # a normal run still proceeds and applies restrictions

    def test_live_owner_wins_over_a_stale_persisted_entry_with_the_same_name(self, tmp_path):
        """A departed owner's persisted account id and a currently-live
        account that happens to share its exact old username (#354 -
        realistic for Plex Home profiles, where title is admin-chosen and
        not unique) are never conflated: the live account's own
        effective_labels/cache entry holds ONLY its own freshly computed
        label, but the departed account's OLD collection - a physically
        different object still sitting in the library - must stay
        excluded from every other user's filter too, merged in rather
        than dropped.

        The original Plex account (id 2) is gone entirely by run 2 - a
        DIFFERENT Plex account (id 5) now resolves to the literal
        username "bob", the same way a server could hand that username
        to a new person after the old account was removed. This is the
        only way persisted-id "2" actually ends up orphaned (find_orphaned_owners
        works on ids, never on username text) - a fixture that reuses
        the same id across both runs never produces an orphan at all and
        would pass even if the privacy guard were deleted.
        """
        from utils.private_label_cache import load_private_label_owners

        config = {"plex": {"token": "t"}}
        self._run(config, self.BOTH_CONFIGURED, tmp_path)

        new_bob_xml = (
            b"<MediaContainer>"
            b'<User id="1" title="home house" username="homehouse165" email="h@x"'
            b' filterMovies="" filterTelevision=""/>'
            b'<User id="5" title="Bob" username="bob" email="newbob@x" filterMovies="" filterTelevision=""/>'
            b"</MediaContainer>"
        )
        replaced = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
            "bob": {"movie": ["PrivateCollection_2_bob"], "tv": ["PrivateCollection_2_bob"]},
        }
        _ok, puts = self._run(config, replaced, tmp_path, users_xml=new_bob_xml)

        home_put = next(p for url, p in puts if url.endswith("/1"))
        assert "PrivateCollection_2_bob" in home_put["filterMovies"], "live entry was not used"
        assert "PrivateCollection_bob" in home_put["filterMovies"], (
            "departed account's still-live collection stopped being excluded once a "
            "different live account reused its old alias - the privacy leak #354 fixes"
        )

        owners = load_private_label_owners(str(tmp_path))
        assert owners["5"]["labels"]["movie"] == ["PrivateCollection_2_bob"], "live account id must hold the live label"
        assert owners["2"]["labels"]["movie"] == ["PrivateCollection_bob"], (
            "departed account id must stay tracked separately from the live account that reused its alias"
        )

    def test_self_exclusion_invariant_holds_for_the_departed_owner_too(self, tmp_path):
        """bob leaves config but is still an actual user on the Plex
        server (#332 covers every server user, not only configured
        ones), so his own filter still gets recomputed on this run. His
        own retained label must never end up excluding his own
        collection from himself - the same self-exclusion invariant that
        already holds for a still-configured user must hold for him
        too."""
        config = {"plex": {"token": "t"}}
        self._run(config, self.BOTH_CONFIGURED, tmp_path)

        _ok, puts = self._run(config, self.ONLY_HOME_CONFIGURED, tmp_path)

        bob_put = next(p for url, p in puts if url.endswith("/2"))
        assert "PrivateCollection_bob" not in bob_put["filterMovies"]
        assert "PrivateCollection_bob" not in bob_put["filterTelevision"]

    def test_resolution_failure_never_makes_prune_delete_a_configured_user(self, tmp_path):
        """A currently-configured user whose name fails to resolve
        against this run's live Plex user list (e.g. a transient lookup
        glitch) must never be treated as orphaned - a resolution miss is
        not the same thing as a departure, and
        collections.prune_orphaned_private_labels must never delete a
        real, still-configured user's collection over a single bad
        run."""
        from utils import plex_policy

        config = {"plex": {"token": "t"}}
        self._run(config, self.BOTH_CONFIGURED, tmp_path)

        # bob is still configured (still a key in all_user_private_labels)
        # but this run's Plex user list no longer contains anyone
        # resolving to him, simulating a transient lookup failure rather
        # than an actual departure.
        users_xml_without_bob = (
            b"<MediaContainer>"
            b'<User id="1" title="home house" username="homehouse165" email="h@x"'
            b' filterMovies="" filterTelevision=""/>'
            b"</MediaContainer>"
        )
        prune_config = {"plex": {"token": "t"}, "collections": {"prune_orphaned_private_labels": True}}

        with patch.object(plex_policy, "prune_orphaned_private_collections") as mock_prune:
            self._run(prune_config, self.BOTH_CONFIGURED, tmp_path, users_xml=users_xml_without_bob)

        mock_prune.assert_not_called()

    def test_long_exclude_filter_logs_a_loud_warning_instead_of_truncating(self, tmp_path):
        """#351 point 5: retention makes filterMovies/filterTelevision
        monotonically non-shrinking, where it previously could only ever
        be as long as the configured user count. Once retaining many
        departed owners grows the built filter value past
        MAX_EXCLUDE_FILTER_LENGTH, this must log loudly - never silently
        truncate what actually gets sent to Plex."""
        from utils import plex_policy

        home_label = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]}
        }
        many_configured = dict(home_label)
        many_configured.update(
            {f"user_{i}": {"movie": [f"PrivateCollection_user_{i}" * 20], "tv": []} for i in range(30)}
        )
        # Every one of the 31 users must actually resolve to a Plex id in
        # run 1 for their label to be persisted at all.
        run1_xml_users = [
            '<User id="1" title="home house" username="homehouse165" email="h@x" filterMovies="" filterTelevision=""/>'
        ]
        run1_xml_users += [
            f'<User id="{i + 2}" title="user_{i}" username="user_{i}" email="u{i}@x" '
            f'filterMovies="" filterTelevision=""/>'
            for i in range(30)
        ]
        run1_xml = ("<MediaContainer>" + "".join(run1_xml_users) + "</MediaContainer>").encode()

        # Run 2: home is the only user left configured - all 30 others
        # have left config (whether or not they're still live Plex
        # users is irrelevant to "departed"; a minimal XML is enough).
        run2_xml = (
            b"<MediaContainer>"
            b'<User id="1" title="home house" username="homehouse165" email="h@x"'
            b' filterMovies="" filterTelevision=""/>'
            b"</MediaContainer>"
        )

        config = {"plex": {"token": "t"}}
        self._run(config, many_configured, tmp_path, users_xml=run1_xml)

        with patch.object(plex_policy, "log_warning") as mock_warn:
            ok, puts = self._run(config, home_label, tmp_path, users_xml=run2_xml)

        assert ok is True
        home_put = next(p for url, p in puts if url.endswith("/1"))
        assert len(home_put["filterMovies"]) > plex_policy.MAX_EXCLUDE_FILTER_LENGTH
        # Every one of the 30 retained labels is present - nothing was
        # dropped/truncated to fit under the length.
        assert all(f"PrivateCollection_user_{i}" in home_put["filterMovies"] for i in range(30))
        assert any("may exceed what Plex" in str(call) for call in mock_warn.call_args_list)

    def test_legacy_label_form_stays_excluded_and_is_not_orphaned_after_upgrade(self, tmp_path):
        """#352: an install upgrading across the private-label case/space
        normalization fix (utils.labels._sanitize_user_token) may already
        have a REAL, still-existing collection labeled with the old,
        un-normalized form (e.g. "PrivateCollection_Bob_Smith") for an
        account that is still fully configured today - this is NOT a
        departure (#351's orphan handling doesn't apply), just a label
        SHAPE change. That legacy form must stay excluded from every
        other user's filter, and must stay in the persisted cache -
        never silently dropped the moment this run computes the new,
        normalized form instead.
        """
        from utils.private_label_cache import load_private_label_owners, save_private_label_owners

        # Simulates a pre-#352 run having already persisted the
        # un-normalized label for bob's account id.
        save_private_label_owners(
            str(tmp_path),
            {
                "2": {
                    "username": "bob",
                    "labels": {
                        "movie": ["PrivateCollection_Bob_Smith"],
                        "tv": ["PrivateCollection_Bob_Smith"],
                    },
                }
            },
        )

        # This run computes the new, normalized form for the SAME
        # still-configured bob (build_all_private_labels post-#352).
        config = {"plex": {"token": "t"}}
        labels = {
            "homehouse165": {"movie": ["PrivateCollection_homehouse165"], "tv": ["PrivateCollection_homehouse165"]},
            "bob": {"movie": ["PrivateCollection_bobsmith"], "tv": ["PrivateCollection_bobsmith"]},
        }
        _ok, puts = self._run(config, labels, tmp_path)

        home_put = next(p for url, p in puts if url.endswith("/1"))
        assert "PrivateCollection_Bob_Smith" in home_put["filterMovies"], "legacy label form was orphaned"
        assert "PrivateCollection_bobsmith" in home_put["filterMovies"], "freshly computed label form missing"
        assert "PrivateCollection_Bob_Smith" in home_put["filterTelevision"], "legacy label form was orphaned"
        assert "PrivateCollection_bobsmith" in home_put["filterTelevision"], "freshly computed label form missing"

        owners = load_private_label_owners(str(tmp_path))
        assert set(owners["2"]["labels"]["movie"]) == {"PrivateCollection_Bob_Smith", "PrivateCollection_bobsmith"}
        assert set(owners["2"]["labels"]["tv"]) == {"PrivateCollection_Bob_Smith", "PrivateCollection_bobsmith"}

        # Self-exclusion invariant: neither form of bob's OWN label may
        # ever show up in his own exclude filter.
        bob_put = next(p for url, p in puts if url.endswith("/2"))
        assert "PrivateCollection_Bob_Smith" not in bob_put["filterMovies"]
        assert "PrivateCollection_bobsmith" not in bob_put["filterMovies"]

        # And the union persists going forward - a second run starting
        # from the merged cache must not lose either form again.
        _ok2, puts2 = self._run(config, labels, tmp_path)
        home_put2 = next(p for url, p in puts2 if url.endswith("/1"))
        assert "PrivateCollection_Bob_Smith" in home_put2["filterMovies"]
        assert "PrivateCollection_bobsmith" in home_put2["filterMovies"]

    def test_legacy_label_migration_never_applies_to_an_orphaned_owner(self, tmp_path):
        """The #352 legacy-form union is keyed off id_to_configured - an
        owner who has genuinely departed (#351's orphan path) must keep
        going through find_orphaned_owners/prune exactly as before, not
        get a second, parallel "still configured" code path just because
        their persisted entry also exists."""
        config = {"plex": {"token": "t"}}
        self._run(config, self.BOTH_CONFIGURED, tmp_path)

        _ok, puts = self._run(config, self.ONLY_HOME_CONFIGURED, tmp_path)

        home_put = next(p for url, p in puts if url.endswith("/1"))
        # Still retained via the #351 orphan path, exactly once - not
        # duplicated by also matching the #352 still-configured merge.
        assert home_put["filterMovies"].count("PrivateCollection_bob") == 1


def _root():
    """Whatever conftest's isolation fixture pointed get_project_root at."""
    from utils.plex import get_project_root

    return get_project_root()


class TestPerUserTokenCache:
    """
    Per-user Plex token caching.

    switchUser() resolves a token from plex.tv on every call, so reading
    per-user watched state for six users across the movie and TV
    recommenders issued a dozen plex.tv requests per run where there had
    previously been none. The tokens are stable per (server, user).
    """

    def _config(self, subdir="cache"):
        """cache_dir is a NAME joined onto get_project_root(), not an
        absolute path - see _user_token_cache_path."""
        return {"plex": {"token": "admin-token"}, "cache_dir": subdir}

    def _admin(self, target_username="alice"):
        target = Mock(username=target_username, email=None, title=target_username)
        plex = Mock()
        plex.machineIdentifier = "machine-1"
        plex._baseurl = "http://localhost:32400"
        plex._session = Mock()
        plex.myPlexAccount.return_value = Mock(username="owner", users=Mock(return_value=[target]))
        plex.switchUser.return_value = Mock(_token="alice-token")
        return plex

    def test_first_call_resolves_and_caches(self, tmp_path):
        from utils.plex import get_user_connection

        plex = self._admin()
        with patch("utils.plex.plexapi.server.PlexServer") as mock_server:
            get_user_connection(plex, self._config(), "alice")
        plex.switchUser.assert_called_once()
        assert (Path(_root()) / "cache" / "plex_user_tokens.json").exists()
        mock_server.assert_not_called()  # nothing cached yet on the first call

    def test_second_call_uses_the_cache_and_does_not_hit_plex_tv(self, tmp_path):
        from utils.plex import get_user_connection

        cfg = self._config()
        plex = self._admin()
        with patch("utils.plex.plexapi.server.PlexServer"):
            get_user_connection(plex, cfg, "alice")
            plex.switchUser.reset_mock()
            get_user_connection(plex, cfg, "alice")
        plex.switchUser.assert_not_called(), "second call re-resolved the token from plex.tv"

    def test_cache_survives_a_new_process(self, tmp_path):
        """
        The point of writing it to disk - runs are separate processes.

        There is no in-process memo to clear (see the comment on
        _USER_TOKEN_CACHE_FILE): the cache is the file, so reading it
        back IS the cross-process path.
        """
        from utils.plex import _load_user_token, get_user_connection

        cfg = self._config()
        plex = self._admin()
        with patch("utils.plex.plexapi.server.PlexServer"):
            get_user_connection(plex, cfg, "alice")

        assert _load_user_token(cfg, "machine-1:alice") == "alice-token"

    def test_a_different_cache_dir_does_not_share_tokens(self, tmp_path):
        """
        Keying only by (server, user) let two configs with different
        cache directories share entries - which leaked between tests when
        this was first written, and would be wrong on a host running two
        instances.
        """
        from utils.plex import get_user_connection

        plex = self._admin()
        with patch("utils.plex.plexapi.server.PlexServer"):
            get_user_connection(plex, self._config("cache-a"), "alice")
            plex.switchUser.reset_mock()
            get_user_connection(plex, self._config("cache-b"), "alice")
        plex.switchUser.assert_called_once(), "a separate cache dir reused another's token"

    def test_token_file_is_owner_only(self, tmp_path):
        """These are credentials, written no more loosely than config.yml."""
        import os
        import stat

        from utils.plex import get_user_connection

        plex = self._admin()
        with patch("utils.plex.plexapi.server.PlexServer"):
            get_user_connection(plex, self._config(), "alice")
        mode = os.stat(Path(_root()) / "cache" / "plex_user_tokens.json").st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_rejected_cached_token_is_dropped_and_refetched(self, tmp_path):
        """
        A revoked token must not wedge the user permanently - the whole
        reason this is invalidate-and-retry rather than trust-forever.
        """
        from utils.plex import get_user_connection

        cfg = self._config()
        plex = self._admin()
        with patch("utils.plex.plexapi.server.PlexServer"):
            get_user_connection(plex, cfg, "alice")

        plex.switchUser.reset_mock()
        with patch("utils.plex.plexapi.server.PlexServer", side_effect=plexapi.exceptions.Unauthorized("401")):
            get_user_connection(plex, cfg, "alice")
        plex.switchUser.assert_called_once(), "a rejected cached token was not refetched"

    def test_owner_never_consults_the_cache(self, tmp_path):
        from utils.plex import get_user_connection

        plex = self._admin()
        plex.myPlexAccount.return_value = Mock(username="alice", users=Mock(return_value=[]))
        assert get_user_connection(plex, self._config(), "alice") is plex
        assert not (Path(_root()) / "cache" / "plex_user_tokens.json").exists()

    def test_unwritable_cache_is_not_fatal(self, tmp_path):
        """A cache that cannot be written is a perf problem, not a correctness one."""
        from utils.plex import get_user_connection

        cfg = {"plex": {"token": "t"}, "cache_dir": "\x00invalid"}
        plex = self._admin()
        with patch("utils.plex.plexapi.server.PlexServer"):
            result = get_user_connection(plex, cfg, "alice")
        assert result is plex.switchUser.return_value
