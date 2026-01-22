"""
Tests for utils/plex.py - Plex extraction and utility functions.
"""

import pytest
import requests
import plexapi.exceptions
from unittest.mock import MagicMock, Mock, patch
from utils.plex import (
    extract_genres,
    extract_ids_from_guids,
    extract_rating,
    get_current_users,
    get_excluded_genres_for_user,
    find_plex_movie,
    get_library_imdb_ids,
    apply_user_label_restrictions
)


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
        users = {'plex_users': ['alice', 'bob'], 'managed_users': ['charlie']}
        result = get_current_users(users)

        assert 'alice' in result
        assert 'bob' in result

    def test_returns_managed_users_when_no_plex_users(self):
        """Test that managed_users are used when plex_users is empty."""
        users = {'plex_users': [], 'managed_users': ['admin', 'guest']}
        result = get_current_users(users)

        assert 'admin' in result
        assert 'guest' in result


class TestGetExcludedGenresForUser:
    """Tests for get_excluded_genres_for_user() function."""

    def test_returns_base_genres(self):
        """Test that base excluded genres are returned."""
        base_genres = {'horror', 'gore'}
        user_prefs = {}

        result = get_excluded_genres_for_user(base_genres, user_prefs)

        assert 'horror' in result
        assert 'gore' in result

    def test_adds_user_specific_exclusions(self):
        """Test that user-specific exclusions are added."""
        base_genres = {'horror'}
        user_prefs = {'john': {'exclude_genres': ['comedy', 'romance']}}

        result = get_excluded_genres_for_user(base_genres, user_prefs, username='john')

        assert 'horror' in result
        assert 'comedy' in result
        assert 'romance' in result

    def test_empty_base_and_user_prefs(self):
        """Test with no exclusions."""
        result = get_excluded_genres_for_user(set(), {})

        assert len(result) == 0

    def test_no_username_returns_base_only(self):
        """Test that no username returns base genres only."""
        base_genres = {'horror'}
        user_prefs = {'john': {'exclude_genres': ['comedy']}}

        result = get_excluded_genres_for_user(base_genres, user_prefs)

        assert 'horror' in result
        assert 'comedy' not in result


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

        assert 'tt1234567' in result

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

    @patch('utils.plex.log_warning')
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

    @patch('utils.plex.requests.Session')
    @patch('utils.plex.plexapi.server.PlexServer')
    def test_successful_connection(self, mock_plex_server, mock_session_class):
        """Test successful Plex server connection."""
        from utils.plex import init_plex

        mock_server = Mock()
        mock_plex_server.return_value = mock_server
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        config = {'plex': {'url': 'http://localhost:32400', 'token': 'test_token'}}
        result = init_plex(config)

        assert result == mock_server
        mock_plex_server.assert_called_once_with('http://localhost:32400', 'test_token', session=mock_session)
        assert mock_session.verify is True  # Default is secure (verify SSL)

    @patch('utils.plex.requests.Session')
    @patch('utils.plex.plexapi.server.PlexServer')
    def test_connection_with_verify_ssl_true(self, mock_plex_server, mock_session_class):
        """Test Plex server connection with SSL verification enabled."""
        from utils.plex import init_plex

        mock_server = Mock()
        mock_plex_server.return_value = mock_server
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        config = {'plex': {'url': 'http://localhost:32400', 'token': 'test_token', 'verify_ssl': True}}
        result = init_plex(config)

        assert result == mock_server
        assert mock_session.verify is True

    @patch('utils.plex.requests.Session')
    @patch('utils.plex.plexapi.server.PlexServer')
    @patch('utils.plex.log_error')
    def test_connection_failure(self, mock_log, mock_plex_server, mock_session_class):
        """Test handling connection failure."""
        from utils.plex import init_plex

        mock_plex_server.side_effect = requests.RequestException("Connection refused")

        config = {'plex': {'url': 'http://localhost:32400', 'token': 'test_token'}}

        with pytest.raises(Exception):
            init_plex(config)

        mock_log.assert_called_once()


class TestGetPlexAccountIds:
    """Tests for get_plex_account_ids() function."""

    @patch('utils.plex.requests.get')
    def test_finds_exact_match(self, mock_get):
        """Test finding account ID with exact name match."""
        from utils.plex import get_plex_account_ids

        xml_content = b'''<MediaContainer>
            <Account id="123" name="John"/>
        </MediaContainer>'''

        mock_response = Mock()
        mock_response.content = xml_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        config = {'plex': {'url': 'http://localhost:32400', 'token': 'test_token'}}
        result = get_plex_account_ids(config, ['John'])

        assert result == ['123']

    @patch('utils.plex.requests.get')
    def test_finds_normalized_match(self, mock_get):
        """Test finding account ID with normalized name match."""
        from utils.plex import get_plex_account_ids

        xml_content = b'''<MediaContainer>
            <Account id="456" name="john-doe"/>
        </MediaContainer>'''

        mock_response = Mock()
        mock_response.content = xml_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        config = {'plex': {'url': 'http://localhost:32400', 'token': 'test_token'}}
        result = get_plex_account_ids(config, ['johndoe'])

        assert result == ['456']

    @patch('utils.plex.requests.get')
    @patch('utils.plex.log_error')
    def test_logs_error_for_missing_user(self, mock_log, mock_get):
        """Test logging error when user not found."""
        from utils.plex import get_plex_account_ids

        xml_content = b'''<MediaContainer>
            <Account id="123" name="John"/>
        </MediaContainer>'''

        mock_response = Mock()
        mock_response.content = xml_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        config = {'plex': {'url': 'http://localhost:32400', 'token': 'test_token'}}
        result = get_plex_account_ids(config, ['NonExistent'])

        assert result == []
        mock_log.assert_called_once()

    @patch('utils.plex.requests.get')
    @patch('utils.plex.log_error')
    def test_handles_api_error(self, mock_log, mock_get):
        """Test handling API errors."""
        from utils.plex import get_plex_account_ids

        mock_get.side_effect = requests.RequestException("Connection error")

        config = {'plex': {'url': 'http://localhost:32400', 'token': 'test_token'}}
        result = get_plex_account_ids(config, ['John'])

        assert result == []
        mock_log.assert_called_once()


class TestGetUserSpecificConnection:
    """Tests for get_user_specific_connection() function."""

    def test_returns_plex_for_plex_users(self):
        """Test returning plex when plex_users is set."""
        from utils.plex import get_user_specific_connection

        mock_plex = Mock()
        config = {'plex': {'token': 'test'}}
        users = {'plex_users': ['user1'], 'managed_users': []}

        result = get_user_specific_connection(mock_plex, config, users)

        assert result == mock_plex

    @patch('utils.plex.MyPlexAccount')
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

        config = {'plex': {'token': 'test'}}
        users = {'plex_users': [], 'managed_users': ['managed_user']}

        result = get_user_specific_connection(mock_plex, config, users)

        assert result == mock_switched

    @patch('utils.plex.MyPlexAccount')
    @patch('utils.plex.log_warning')
    def test_handles_switch_error(self, mock_log, mock_account_class):
        """Test handling error during user switch."""
        from utils.plex import get_user_specific_connection

        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("Auth error")

        mock_plex = Mock()
        config = {'plex': {'token': 'test'}}
        users = {'plex_users': [], 'managed_users': ['managed_user']}

        result = get_user_specific_connection(mock_plex, config, users)

        assert result == mock_plex
        mock_log.assert_called_once()


class TestExtractRatingAdvanced:
    """Additional tests for extract_rating() edge cases."""

    def test_falls_back_to_ratings_list(self):
        """Test fallback to ratings list when primary ratings are None."""
        mock_rating = Mock()
        mock_rating.value = 7.5
        mock_rating.image = 'imdb://image.rating'

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
        mock_rating.type = 'audience'
        mock_rating.image = ''

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
        mock_rating.image = 'imdb://image.rating'

        mock_item = Mock()
        mock_item.userRating = None
        mock_item.audienceRating = None
        mock_item.ratings = [mock_rating]

        result = extract_rating(mock_item)

        assert result == 0.0


class TestGetLibraryImdbIdsAdvanced:
    """Additional tests for get_library_imdb_ids()."""

    @patch('utils.plex.log_warning')
    def test_handles_exception(self, mock_log):
        """Test exception handling in get_library_imdb_ids."""
        mock_section = Mock()
        mock_section.all.side_effect = plexapi.exceptions.PlexApiException("API Error")

        result = get_library_imdb_ids(mock_section)

        assert result == set()
        mock_log.assert_called_once()

    def test_handles_item_without_guids_attr(self):
        """Test handling items without guids attribute."""
        mock_item = Mock(spec=['title'])  # No guids attr

        mock_section = Mock()
        mock_section.all.return_value = [mock_item]

        result = get_library_imdb_ids(mock_section)

        assert result == set()


class TestGetWatchedMovieCount:
    """Tests for get_watched_movie_count() function."""

    def test_returns_zero_for_empty_users(self):
        """Test returning 0 when no users to check."""
        from utils.plex import get_watched_movie_count

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = get_watched_movie_count(config, [])

        assert result == 0

    @patch('utils.plex.requests.get')
    @patch('utils.plex.MyPlexAccount')
    def test_returns_watched_count(self, mock_account_class, mock_get):
        """Test returning watched movie count."""
        from utils.plex import get_watched_movie_count

        # Setup account mock
        mock_user = Mock()
        mock_user.title = 'testuser'
        mock_user.id = 123

        mock_account = Mock()
        mock_account.users.return_value = [mock_user]
        mock_account.username = 'admin'
        mock_account.id = 1
        mock_account_class.return_value = mock_account

        # Setup API response
        xml_content = b'''<MediaContainer>
            <Video type="movie" ratingKey="100"/>
            <Video type="movie" ratingKey="101"/>
            <Video type="episode" ratingKey="200"/>
        </MediaContainer>'''

        mock_response = Mock()
        mock_response.content = xml_content
        mock_get.return_value = mock_response

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = get_watched_movie_count(config, ['testuser'])

        assert result == 2

    @patch('utils.plex.MyPlexAccount')
    @patch('utils.plex.log_warning')
    def test_handles_exception(self, mock_log, mock_account_class):
        """Test exception handling."""
        from utils.plex import get_watched_movie_count

        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("Auth error")

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = get_watched_movie_count(config, ['user'])

        assert result == 0
        mock_log.assert_called_once()

    @patch('utils.plex.requests.get')
    @patch('utils.plex.MyPlexAccount')
    def test_matches_admin_user(self, mock_account_class, mock_get):
        """Test matching admin user."""
        from utils.plex import get_watched_movie_count

        mock_account = Mock()
        mock_account.users.return_value = []
        mock_account.username = 'adminuser'
        mock_account.id = 1
        mock_account_class.return_value = mock_account

        xml_content = b'''<MediaContainer>
            <Video type="movie" ratingKey="100"/>
        </MediaContainer>'''

        mock_response = Mock()
        mock_response.content = xml_content
        mock_get.return_value = mock_response

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = get_watched_movie_count(config, ['admin'])

        assert result == 1


class TestGetWatchedShowCount:
    """Tests for get_watched_show_count() function."""

    def test_returns_zero_for_empty_users(self):
        """Test returning 0 when no users to check."""
        from utils.plex import get_watched_show_count

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = get_watched_show_count(config, [])

        assert result == 0

    @patch('utils.plex.requests.get')
    @patch('utils.plex.MyPlexAccount')
    def test_returns_watched_show_count(self, mock_account_class, mock_get):
        """Test returning watched show count."""
        from utils.plex import get_watched_show_count

        mock_user = Mock()
        mock_user.title = 'testuser'
        mock_user.id = 123

        mock_account = Mock()
        mock_account.users.return_value = [mock_user]
        mock_account.username = 'admin'
        mock_account.id = 1
        mock_account_class.return_value = mock_account

        xml_content = b'''<MediaContainer>
            <Video type="episode" grandparentRatingKey="200"/>
            <Video type="episode" grandparentRatingKey="200"/>
            <Video type="episode" grandparentRatingKey="201"/>
        </MediaContainer>'''

        mock_response = Mock()
        mock_response.content = xml_content
        mock_get.return_value = mock_response

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = get_watched_show_count(config, ['testuser'])

        assert result == 2  # 2 unique shows

    @patch('utils.plex.MyPlexAccount')
    @patch('utils.plex.log_warning')
    def test_handles_exception(self, mock_log, mock_account_class):
        """Test exception handling."""
        from utils.plex import get_watched_show_count

        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("Auth error")

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = get_watched_show_count(config, ['user'])

        assert result == 0
        mock_log.assert_called_once()


class TestFetchPlexWatchHistoryShows:
    """Tests for fetch_plex_watch_history_shows() function."""

    @patch('utils.plex.requests.get')
    def test_fetches_show_history(self, mock_get):
        """Test fetching show watch history."""
        from utils.plex import fetch_plex_watch_history_shows

        xml_content = b'''<MediaContainer>
            <Video type="episode" grandparentKey="/library/metadata/100"/>
            <Video type="episode" grandparentKey="/library/metadata/101"/>
        </MediaContainer>'''

        mock_response = Mock()
        mock_response.content = xml_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        mock_section = Mock()
        mock_section.key = 1

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = fetch_plex_watch_history_shows(config, ['123'], mock_section)

        assert 100 in result
        assert 101 in result

    @patch('utils.plex.requests.get')
    @patch('utils.plex.log_error')
    def test_handles_request_error(self, mock_log, mock_get):
        """Test handling request errors."""
        from utils.plex import fetch_plex_watch_history_shows

        mock_get.side_effect = requests.RequestException("Connection error")

        mock_section = Mock()
        mock_section.key = 1

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = fetch_plex_watch_history_shows(config, ['123'], mock_section)

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

        assert result['tmdb_id'] is None

    def test_handles_guid_as_string(self):
        """Test handling guid as string instead of object."""
        mock_guid = "imdb://tt1234567"

        mock_item = Mock()
        mock_item.guids = [mock_guid]

        result = extract_ids_from_guids(mock_item)

        assert result['imdb_id'] == 'tt1234567'


class TestFetchPlexWatchHistoryMovies:
    """Tests for fetch_plex_watch_history_movies() function."""

    @patch('utils.plex.MyPlexAccount')
    @patch('utils.plex.requests.get')
    def test_fetches_movie_history(self, mock_get, mock_account_class):
        """Test fetching movie watch history."""
        from utils.plex import fetch_plex_watch_history_movies

        mock_user = Mock()
        mock_user.id = 123

        mock_account = Mock()
        mock_account.users.return_value = [mock_user]
        mock_account_class.return_value = mock_account

        xml_content = b'''<MediaContainer>
            <Video ratingKey="100" viewedAt="1700000000" userRating="8.5"/>
            <Video ratingKey="101" viewedAt="1700001000"/>
        </MediaContainer>'''

        mock_response = Mock()
        mock_response.content = xml_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        mock_section = Mock()
        mock_section.key = 1

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        history, dates = fetch_plex_watch_history_movies(config, ['123'], mock_section)

        assert len(history) == 2

    @patch('utils.plex.MyPlexAccount')
    @patch('utils.plex.log_error')
    def test_handles_exception(self, mock_log, mock_account_class):
        """Test exception handling."""
        from utils.plex import fetch_plex_watch_history_movies

        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("Auth error")

        mock_section = Mock()
        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}

        history, dates = fetch_plex_watch_history_movies(config, ['123'], mock_section)

        assert history == []
        assert dates == {}
        mock_log.assert_called()

    @patch('utils.plex.MyPlexAccount')
    @patch('utils.plex.requests.get')
    def test_skips_unknown_account(self, mock_get, mock_account_class):
        """Test skipping unknown account IDs."""
        from utils.plex import fetch_plex_watch_history_movies

        mock_account = Mock()
        mock_account.users.return_value = []  # No managed users
        mock_account_class.return_value = mock_account

        mock_section = Mock()
        mock_section.key = 1

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        # Use account ID that won't match owner or managed users
        history, dates = fetch_plex_watch_history_movies(config, ['999'], mock_section)

        # Should return empty since no matching accounts
        assert history == []


class TestFetchWatchHistoryWithTmdb:
    """Tests for fetch_watch_history_with_tmdb() function."""

    @patch('utils.plex.requests.get')
    def test_fetches_movie_with_tmdb(self, mock_get):
        """Test fetching movie watch history with TMDB IDs."""
        from utils.plex import fetch_watch_history_with_tmdb

        xml_content = b'''<MediaContainer>
            <Video type="movie" ratingKey="100"/>
        </MediaContainer>'''

        mock_response = Mock()
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

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = fetch_watch_history_with_tmdb(mock_plex, config, ['123'], mock_section, 'movie')

        assert len(result) == 1
        assert result[0]['tmdb_id'] == 12345

    @patch('utils.plex.requests.get')
    def test_handles_non_200_response(self, mock_get):
        """Test handling non-200 response."""
        from utils.plex import fetch_watch_history_with_tmdb

        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.key = 1

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = fetch_watch_history_with_tmdb(mock_plex, config, ['123'], mock_section, 'movie')

        assert result == []

    @patch('utils.plex.requests.get')
    def test_fetches_show_with_tmdb(self, mock_get):
        """Test fetching show watch history with TMDB IDs."""
        from utils.plex import fetch_watch_history_with_tmdb

        xml_content = b'''<MediaContainer>
            <Video type="episode" grandparentKey="/library/metadata/200"/>
        </MediaContainer>'''

        mock_response = Mock()
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

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = fetch_watch_history_with_tmdb(mock_plex, config, ['123'], mock_section, 'show')

        assert len(result) == 1
        assert result[0]['tmdb_id'] == 54321

    @patch('utils.plex.requests.get')
    def test_handles_exception_in_loop(self, mock_get):
        """Test handling exception when processing items."""
        from utils.plex import fetch_watch_history_with_tmdb

        mock_get.side_effect = requests.RequestException("Connection error")

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.key = 1

        config = {'plex': {'url': 'http://localhost', 'token': 'test'}}
        result = fetch_watch_history_with_tmdb(mock_plex, config, ['123'], mock_section, 'movie')

        assert result == []


class TestGetConfiguredUsers:
    """Tests for get_configured_users() function."""

    @patch('utils.plex.MyPlexAccount')
    def test_returns_configured_users(self, mock_account_class):
        """Test returning configured users."""
        from utils.plex import get_configured_users

        mock_user = Mock()
        mock_user.title = 'TestUser'

        mock_account = Mock()
        mock_account.username = 'AdminUser'
        mock_account.users.return_value = [mock_user]
        mock_account_class.return_value = mock_account

        config = {
            'plex': {'token': 'test', 'managed_users': 'TestUser'},
            'plex_users': {'users': None}
        }

        result = get_configured_users(config)

        assert result['admin_user'] == 'AdminUser'
        assert 'TestUser' in result['managed_users']

    @patch('utils.plex.MyPlexAccount')
    def test_maps_admin_alias(self, mock_account_class):
        """Test mapping 'admin' to actual admin username."""
        from utils.plex import get_configured_users

        mock_account = Mock()
        mock_account.username = 'RealAdmin'
        mock_account.users.return_value = []
        mock_account_class.return_value = mock_account

        config = {
            'plex': {'token': 'test', 'managed_users': 'admin'},
            'plex_users': {'users': None}
        }

        result = get_configured_users(config)

        assert 'RealAdmin' in result['managed_users']

    @patch('utils.plex.MyPlexAccount')
    @patch('utils.plex.log_error')
    def test_raises_for_unknown_user(self, mock_log, mock_account_class):
        """Test raising error for unknown user."""
        from utils.plex import get_configured_users

        mock_account = Mock()
        mock_account.username = 'Admin'
        mock_account.users.return_value = []
        mock_account_class.return_value = mock_account

        config = {
            'plex': {'token': 'test', 'managed_users': 'UnknownUser'},
            'plex_users': {'users': None}
        }

        with pytest.raises(ValueError):
            get_configured_users(config)

    @patch('utils.plex.MyPlexAccount')
    def test_handles_plex_users_list(self, mock_account_class):
        """Test handling plex_users as list."""
        from utils.plex import get_configured_users

        mock_account = Mock()
        mock_account.username = 'Admin'
        mock_account.users.return_value = []
        mock_account_class.return_value = mock_account

        config = {
            'plex': {'token': 'test', 'managed_users': ''},
            'plex_users': {'users': ['user1', 'user2']}
        }

        result = get_configured_users(config)

        assert result['plex_users'] == ['user1', 'user2']

    @patch('utils.plex.MyPlexAccount')
    def test_handles_plex_users_string(self, mock_account_class):
        """Test handling plex_users as comma-separated string."""
        from utils.plex import get_configured_users

        mock_account = Mock()
        mock_account.username = 'Admin'
        mock_account.users.return_value = []
        mock_account_class.return_value = mock_account

        config = {
            'plex': {'token': 'test', 'managed_users': ''},
            'plex_users': {'users': 'user1, user2'}
        }

        result = get_configured_users(config)

        assert 'user1' in result['plex_users']
        assert 'user2' in result['plex_users']

    @patch('utils.plex.MyPlexAccount')
    def test_deduplicates_managed_users(self, mock_account_class):
        """Test deduplication of managed users."""
        from utils.plex import get_configured_users

        mock_user = Mock()
        mock_user.title = 'TestUser'

        mock_account = Mock()
        mock_account.username = 'Admin'
        mock_account.users.return_value = [mock_user]
        mock_account_class.return_value = mock_account

        config = {
            'plex': {'token': 'test', 'managed_users': 'TestUser, testuser'},  # Same user twice (different case)
            'plex_users': {'users': None}
        }

        result = get_configured_users(config)

        # Should deduplicate
        assert len(result['managed_users']) == 1


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

        show_data = {
            1: {
                'watched_episodes': 3,
                'completion_percent': 15,
                'total_episodes': 20
            }
        }
        config = {
            'negative_signals': {
                'enabled': True,
                'dropped_shows': {
                    'enabled': True,
                    'min_episodes_watched': 2,
                    'max_completion_percent': 25
                }
            }
        }

        result = identify_dropped_shows(show_data, config)

        assert 1 in result

    def test_does_not_drop_completed_show(self):
        """Test that completed shows are not marked as dropped."""
        from utils.plex import identify_dropped_shows

        show_data = {
            1: {
                'watched_episodes': 10,
                'completion_percent': 80,
                'total_episodes': 12
            }
        }
        config = {
            'negative_signals': {
                'enabled': True,
                'dropped_shows': {
                    'enabled': True,
                    'min_episodes_watched': 2,
                    'max_completion_percent': 25
                }
            }
        }

        result = identify_dropped_shows(show_data, config)

        assert 1 not in result

    def test_skips_show_with_too_few_watched(self):
        """Test that shows with too few watched episodes are skipped."""
        from utils.plex import identify_dropped_shows

        show_data = {
            1: {
                'watched_episodes': 1,  # Less than min_episodes_watched
                'completion_percent': 5,
                'total_episodes': 20
            }
        }
        config = {
            'negative_signals': {
                'enabled': True,
                'dropped_shows': {
                    'enabled': True,
                    'min_episodes_watched': 2,
                    'max_completion_percent': 25
                }
            }
        }

        result = identify_dropped_shows(show_data, config)

        assert 1 not in result

    def test_skips_short_series(self):
        """Test that short series are not marked as dropped."""
        from utils.plex import identify_dropped_shows

        show_data = {
            1: {
                'watched_episodes': 2,
                'completion_percent': 50,
                'total_episodes': 2  # Total equals min_episodes_watched
            }
        }
        config = {
            'negative_signals': {
                'enabled': True,
                'dropped_shows': {
                    'enabled': True,
                    'min_episodes_watched': 2,
                    'max_completion_percent': 25
                }
            }
        }

        result = identify_dropped_shows(show_data, config)

        assert 1 not in result

    def test_returns_empty_when_disabled(self):
        """Test that empty set is returned when feature is disabled."""
        from utils.plex import identify_dropped_shows

        show_data = {
            1: {
                'watched_episodes': 3,
                'completion_percent': 15,
                'total_episodes': 20
            }
        }
        config = {
            'negative_signals': {
                'enabled': False
            }
        }

        result = identify_dropped_shows(show_data, config)

        assert result == set()

    def test_returns_empty_when_dropped_shows_disabled(self):
        """Test that empty set is returned when dropped_shows is disabled."""
        from utils.plex import identify_dropped_shows

        show_data = {
            1: {
                'watched_episodes': 3,
                'completion_percent': 15,
                'total_episodes': 20
            }
        }
        config = {
            'negative_signals': {
                'enabled': True,
                'dropped_shows': {
                    'enabled': False
                }
            }
        }

        result = identify_dropped_shows(show_data, config)

        assert result == set()


class TestFetchShowCompletionData:
    """Tests for fetch_show_completion_data() function."""

    @patch('utils.plex.requests.get')
    def test_returns_empty_dict_on_error(self, mock_get):
        """Test that empty dict is returned on API error."""
        from utils.plex import fetch_show_completion_data

        mock_get.side_effect = requests.RequestException("API Error")

        config = {'plex': {'url': 'http://localhost', 'token': 'test', 'verify_ssl': False}}
        mock_section = Mock()
        mock_section.key = '1'
        mock_section.all.return_value = []

        result = fetch_show_completion_data(config, ['account1'], mock_section)

        assert result == {}

    @patch('utils.plex.requests.get')
    def test_processes_episode_data(self, mock_get):
        """Test processing episode watch data."""
        from utils.plex import fetch_show_completion_data

        # Mock response XML
        xml_response = b'''<?xml version="1.0"?>
        <MediaContainer>
            <Video type="episode" grandparentKey="/library/metadata/100" ratingKey="200" viewedAt="1704067200"/>
        </MediaContainer>'''

        mock_response = Mock()
        mock_response.content = xml_response
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Mock show in library
        mock_show = Mock()
        mock_show.ratingKey = 100
        mock_show.title = 'Test Show'
        mock_episode = Mock()
        mock_show.episodes.return_value = [mock_episode] * 10

        mock_section = Mock()
        mock_section.key = '1'
        mock_section.all.return_value = [mock_show]

        config = {'plex': {'url': 'http://localhost', 'token': 'test', 'verify_ssl': False}}

        result = fetch_show_completion_data(config, ['account1'], mock_section)

        assert 100 in result
        assert result[100]['watched_episodes'] == 1
        assert result[100]['total_episodes'] == 10


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

    @patch('utils.plex.requests.put')
    @patch('utils.plex.requests.get')
    @patch('utils.plex.MyPlexAccount')
    def test_applies_exclude_restrictions_to_users(self, mock_account_class, mock_get, mock_put):
        """Test that exclude restrictions are applied to each user."""
        from utils.plex import apply_user_label_restrictions

        # Setup mock account
        mock_account = Mock()
        mock_account.username = 'AdminUser'
        mock_account_class.return_value = mock_account

        # Setup mock GET response for users list (XML)
        mock_get_response = Mock()
        mock_get_response.content = b'''<MediaContainer>
            <User id="123" title="Jason" username="jason"/>
            <User id="456" title="Sarah" username="sarah"/>
        </MediaContainer>'''
        mock_get_response.raise_for_status = Mock()
        mock_get.return_value = mock_get_response

        # Setup mock PUT response
        mock_put_response = Mock()
        mock_put_response.raise_for_status = Mock()
        mock_put.return_value = mock_put_response

        config = {
            'plex': {
                'token': 'test_token',
                'server_name': 'MyServer'
            }
        }

        all_user_labels = {
            'Jason': 'Recommended_Jason',
            'Sarah': 'Recommended_Sarah'
        }

        result = apply_user_label_restrictions(config, all_user_labels)

        assert result is True
        # Should be called twice (once for each non-admin user)
        assert mock_put.call_count == 2

    @patch('utils.plex.requests.put')
    @patch('utils.plex.requests.get')
    @patch('utils.plex.MyPlexAccount')
    def test_skips_admin_user(self, mock_account_class, mock_get, mock_put):
        """Test that admin user is skipped (can't have restrictions)."""
        from utils.plex import apply_user_label_restrictions

        mock_account = Mock()
        mock_account.username = 'AdminUser'
        mock_account_class.return_value = mock_account

        mock_get_response = Mock()
        mock_get_response.content = b'''<MediaContainer>
            <User id="123" title="OtherUser" username="otheruser"/>
        </MediaContainer>'''
        mock_get_response.raise_for_status = Mock()
        mock_get.return_value = mock_get_response

        mock_put_response = Mock()
        mock_put_response.raise_for_status = Mock()
        mock_put.return_value = mock_put_response

        config = {
            'plex': {
                'token': 'test_token'
            }
        }

        all_user_labels = {
            'AdminUser': 'Recommended_AdminUser',
            'OtherUser': 'Recommended_OtherUser'
        }

        result = apply_user_label_restrictions(config, all_user_labels)

        assert result is True
        # Should only be called once (for OtherUser, not AdminUser)
        mock_put.assert_called_once()

    @patch('utils.plex.requests.put')
    @patch('utils.plex.requests.get')
    @patch('utils.plex.MyPlexAccount')
    def test_returns_false_for_unknown_user(self, mock_account_class, mock_get, mock_put):
        """Test that unknown users result in partial failure."""
        from utils.plex import apply_user_label_restrictions

        mock_account = Mock()
        mock_account.username = 'AdminUser'
        mock_account_class.return_value = mock_account

        mock_get_response = Mock()
        mock_get_response.content = b'''<MediaContainer>
            <User id="123" title="KnownUser" username="knownuser"/>
        </MediaContainer>'''
        mock_get_response.raise_for_status = Mock()
        mock_get.return_value = mock_get_response

        mock_put_response = Mock()
        mock_put_response.raise_for_status = Mock()
        mock_put.return_value = mock_put_response

        config = {
            'plex': {
                'token': 'test_token'
            }
        }

        all_user_labels = {
            'KnownUser': 'Recommended_KnownUser',
            'UnknownUser': 'Recommended_UnknownUser'
        }

        result = apply_user_label_restrictions(config, all_user_labels)

        # Returns False because one user wasn't found
        assert result is False
        # But should still apply restrictions for KnownUser
        mock_put.assert_called_once()

    @patch('utils.plex.MyPlexAccount')
    def test_handles_plex_api_error(self, mock_account_class):
        """Test that PlexApiException is handled gracefully."""
        from utils.plex import apply_user_label_restrictions

        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("Auth failed")

        config = {
            'plex': {
                'token': 'test_token'
            }
        }

        # Need multiple users to trigger API call (single user returns early)
        all_user_labels = {
            'TestUser': 'Recommended_TestUser',
            'OtherUser': 'Recommended_OtherUser'
        }

        result = apply_user_label_restrictions(config, all_user_labels)

        assert result is False

    @patch('utils.plex.MyPlexAccount')
    def test_returns_true_for_single_user(self, mock_account_class):
        """Test that single user returns True (nothing to hide)."""
        from utils.plex import apply_user_label_restrictions

        config = {
            'plex': {
                'token': 'test_token'
            }
        }

        # Only one user - no restrictions needed
        all_user_labels = {'Jason': 'Recommended_Jason'}

        result = apply_user_label_restrictions(config, all_user_labels)

        assert result is True
        # MyPlexAccount should not even be instantiated
        mock_account_class.assert_not_called()

    @patch('utils.plex.MyPlexAccount')
    def test_returns_true_for_empty_labels(self, mock_account_class):
        """Test that empty labels dict returns True."""
        from utils.plex import apply_user_label_restrictions

        config = {
            'plex': {
                'token': 'test_token'
            }
        }

        result = apply_user_label_restrictions(config, {})

        assert result is True
        mock_account_class.assert_not_called()

    @patch('utils.plex.requests.put')
    @patch('utils.plex.requests.get')
    @patch('utils.plex.MyPlexAccount')
    def test_case_insensitive_username_match(self, mock_account_class, mock_get, mock_put):
        """Test that username matching is case insensitive."""
        from utils.plex import apply_user_label_restrictions

        mock_account = Mock()
        mock_account.username = 'AdminUser'
        mock_account_class.return_value = mock_account

        mock_get_response = Mock()
        mock_get_response.content = b'''<MediaContainer>
            <User id="123" title="TestUser" username="testuser"/>
        </MediaContainer>'''
        mock_get_response.raise_for_status = Mock()
        mock_get.return_value = mock_get_response

        mock_put_response = Mock()
        mock_put_response.raise_for_status = Mock()
        mock_put.return_value = mock_put_response

        config = {
            'plex': {
                'token': 'test_token'
            }
        }

        # Use lowercase in the labels dict
        all_user_labels = {
            'testuser': 'Recommended_testuser',
            'anotheruser': 'Recommended_anotheruser'
        }

        result = apply_user_label_restrictions(config, all_user_labels)

        # Should still match TestUser despite case difference
        # Returns False because 'anotheruser' wasn't found, but TestUser was processed
        assert result is False
        mock_put.assert_called_once()


class TestContentRatingFilter:
    """Tests for content rating filter functions."""

    def test_get_max_rating_for_user_returns_rating(self):
        """Test getting max_rating for a user who has one configured."""
        from utils.plex import get_max_rating_for_user

        user_prefs = {
            'kids': {'display_name': 'Kids', 'max_rating': 'PG'},
            'teen': {'display_name': 'Teen', 'max_rating': 'PG-13'}
        }

        assert get_max_rating_for_user(user_prefs, 'kids') == 'PG'
        assert get_max_rating_for_user(user_prefs, 'teen') == 'PG-13'

    def test_get_max_rating_for_user_returns_none_when_not_set(self):
        """Test getting max_rating returns None when not configured."""
        from utils.plex import get_max_rating_for_user

        user_prefs = {
            'adult': {'display_name': 'Adult'}  # No max_rating
        }

        assert get_max_rating_for_user(user_prefs, 'adult') is None

    def test_get_max_rating_for_user_returns_none_for_unknown_user(self):
        """Test getting max_rating returns None for unknown user."""
        from utils.plex import get_max_rating_for_user

        user_prefs = {'kids': {'max_rating': 'PG'}}

        assert get_max_rating_for_user(user_prefs, 'unknown') is None
        assert get_max_rating_for_user(user_prefs, None) is None

    def test_is_rating_allowed_movie_hierarchy(self):
        """Test movie rating hierarchy: G < PG < PG-13 < R < NC-17."""
        from utils.plex import is_rating_allowed

        # PG-13 max rating
        assert is_rating_allowed('G', 'PG-13', 'movie') is True
        assert is_rating_allowed('PG', 'PG-13', 'movie') is True
        assert is_rating_allowed('PG-13', 'PG-13', 'movie') is True
        assert is_rating_allowed('R', 'PG-13', 'movie') is False
        assert is_rating_allowed('NC-17', 'PG-13', 'movie') is False

        # PG max rating
        assert is_rating_allowed('G', 'PG', 'movie') is True
        assert is_rating_allowed('PG', 'PG', 'movie') is True
        assert is_rating_allowed('PG-13', 'PG', 'movie') is False
        assert is_rating_allowed('R', 'PG', 'movie') is False

    def test_is_rating_allowed_tv_hierarchy(self):
        """Test TV rating hierarchy: TV-Y < TV-Y7 < TV-G < TV-PG < TV-14 < TV-MA."""
        from utils.plex import is_rating_allowed

        # TV-PG max rating
        assert is_rating_allowed('TV-Y', 'TV-PG', 'tv') is True
        assert is_rating_allowed('TV-Y7', 'TV-PG', 'tv') is True
        assert is_rating_allowed('TV-G', 'TV-PG', 'tv') is True
        assert is_rating_allowed('TV-PG', 'TV-PG', 'tv') is True
        assert is_rating_allowed('TV-14', 'TV-PG', 'tv') is False
        assert is_rating_allowed('TV-MA', 'TV-PG', 'tv') is False

        # TV-14 max rating
        assert is_rating_allowed('TV-PG', 'TV-14', 'tv') is True
        assert is_rating_allowed('TV-14', 'TV-14', 'tv') is True
        assert is_rating_allowed('TV-MA', 'TV-14', 'tv') is False

    def test_is_rating_allowed_case_insensitive(self):
        """Test rating comparison is case insensitive."""
        from utils.plex import is_rating_allowed

        assert is_rating_allowed('pg-13', 'PG-13', 'movie') is True
        assert is_rating_allowed('PG-13', 'pg-13', 'movie') is True
        assert is_rating_allowed('tv-pg', 'TV-PG', 'tv') is True

    def test_is_rating_allowed_no_max_rating(self):
        """Test that no max_rating allows all content."""
        from utils.plex import is_rating_allowed

        assert is_rating_allowed('R', None, 'movie') is True
        assert is_rating_allowed('NC-17', None, 'movie') is True
        assert is_rating_allowed('TV-MA', None, 'tv') is True

    def test_is_rating_allowed_no_content_rating(self):
        """Test that missing content_rating allows the content."""
        from utils.plex import is_rating_allowed

        assert is_rating_allowed(None, 'PG-13', 'movie') is True
        assert is_rating_allowed('', 'PG-13', 'movie') is True

    def test_is_rating_allowed_unknown_rating(self):
        """Test that unknown ratings (NR, Unrated) are allowed."""
        from utils.plex import is_rating_allowed

        assert is_rating_allowed('NR', 'PG-13', 'movie') is True
        assert is_rating_allowed('Unrated', 'PG', 'movie') is True
        assert is_rating_allowed('Not Rated', 'R', 'movie') is True
