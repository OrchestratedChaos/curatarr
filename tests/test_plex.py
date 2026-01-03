"""
Tests for utils/plex.py - Plex extraction and utility functions.
"""

import pytest
from unittest.mock import MagicMock, Mock
from utils.plex import extract_genres, extract_ids_from_guids, extract_rating


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
