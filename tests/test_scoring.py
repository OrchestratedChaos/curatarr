"""
Tests for utils/scoring.py - Scoring and similarity functions.
"""

import pytest
import math
from utils.scoring import (
    normalize_genre,
    fuzzy_keyword_match,
    calculate_recency_multiplier,
    calculate_rewatch_multiplier,
    calculate_similarity_score,
    GENRE_NORMALIZATION
)


class TestNormalizeGenre:
    """Tests for normalize_genre() function."""

    def test_lowercase_conversion(self):
        """Test that genres are converted to lowercase."""
        assert normalize_genre("Action") == "action"
        assert normalize_genre("COMEDY") == "comedy"
        assert normalize_genre("Drama") == "drama"

    def test_sci_fi_normalization(self):
        """Test various sci-fi spellings are normalized."""
        assert normalize_genre("Sci-Fi") == "science fiction"
        assert normalize_genre("SciFi") == "science fiction"
        assert normalize_genre("Science-Fiction") == "science fiction"

    def test_action_adventure_normalization(self):
        """Test action/adventure variants are normalized."""
        assert normalize_genre("Action & Adventure") == "action"
        assert normalize_genre("Action/Adventure") == "action"

    def test_kids_to_family(self):
        """Test that 'kids' normalizes to 'family'."""
        assert normalize_genre("Kids") == "family"

    def test_empty_string(self):
        """Test handling of empty string."""
        assert normalize_genre("") == ""

    def test_none_input(self):
        """Test handling of None input."""
        assert normalize_genre(None) is None

    def test_whitespace_stripped(self):
        """Test that whitespace is stripped."""
        assert normalize_genre("  Action  ") == "action"

    def test_unmapped_genre_lowercase(self):
        """Test that unmapped genres are just lowercased."""
        assert normalize_genre("Western") == "western"
        assert normalize_genre("Mystery") == "mystery"


class TestFuzzyKeywordMatch:
    """Tests for fuzzy_keyword_match() function."""

    def test_exact_match(self):
        """Test exact keyword match."""
        user_keywords = {"superhero": 5, "action": 3}
        score, matched = fuzzy_keyword_match("superhero", user_keywords)

        assert score == 5
        assert matched == "superhero"

    def test_partial_match_contained(self):
        """Test partial match when keyword is contained in user keyword."""
        user_keywords = {"superhero movie": 5}
        score, matched = fuzzy_keyword_match("superhero", user_keywords)

        assert score > 0
        assert matched == "superhero movie"

    def test_partial_match_contains(self):
        """Test partial match when user keyword is contained in keyword."""
        user_keywords = {"hero": 5}
        score, matched = fuzzy_keyword_match("superhero", user_keywords)

        assert score > 0

    def test_no_match(self):
        """Test when no match is found."""
        user_keywords = {"comedy": 5, "romance": 3}
        score, matched = fuzzy_keyword_match("horror", user_keywords)

        assert score == 0
        assert matched is None

    def test_empty_keyword(self):
        """Test with empty keyword."""
        user_keywords = {"action": 5}
        score, matched = fuzzy_keyword_match("", user_keywords)

        assert score == 0
        assert matched is None

    def test_empty_user_keywords(self):
        """Test with empty user keywords."""
        score, matched = fuzzy_keyword_match("action", {})

        assert score == 0
        assert matched is None

    def test_case_insensitive(self):
        """Test that matching is case-insensitive."""
        user_keywords = {"superhero": 5}
        score, matched = fuzzy_keyword_match("SUPERHERO", user_keywords)

        assert score == 5


class TestCalculateRewatchMultiplier:
    """Tests for calculate_rewatch_multiplier() function."""

    def test_single_view(self):
        """Test multiplier for single view."""
        assert calculate_rewatch_multiplier(1) == 1.0

    def test_zero_views(self):
        """Test multiplier for zero views."""
        assert calculate_rewatch_multiplier(0) == 1.0

    def test_two_views(self):
        """Test multiplier for two views (log2(2) + 1 = 2.0)."""
        assert calculate_rewatch_multiplier(2) == 2.0

    def test_four_views(self):
        """Test multiplier for four views (log2(4) + 1 = 3.0)."""
        assert calculate_rewatch_multiplier(4) == 3.0

    def test_eight_views(self):
        """Test multiplier for eight views (log2(8) + 1 = 4.0)."""
        assert calculate_rewatch_multiplier(8) == 4.0

    def test_none_views(self):
        """Test multiplier for None views."""
        assert calculate_rewatch_multiplier(None) == 1.0

    def test_logarithmic_scaling(self):
        """Test that scaling is logarithmic (diminishing returns)."""
        mult_2 = calculate_rewatch_multiplier(2)
        mult_4 = calculate_rewatch_multiplier(4)
        mult_8 = calculate_rewatch_multiplier(8)

        # Each doubling adds 1.0 to the multiplier
        assert mult_4 - mult_2 == pytest.approx(1.0)
        assert mult_8 - mult_4 == pytest.approx(1.0)


class TestCalculateSimilarityScore:
    """Tests for calculate_similarity_score() function."""

    def test_empty_content_info(self):
        """Test with empty content info."""
        score, breakdown = calculate_similarity_score({}, {"genres": {"action": 1}})
        assert score == 0.0

    def test_empty_user_profile(self):
        """Test with empty user profile."""
        score, breakdown = calculate_similarity_score(
            {"genres": ["action"]},
            {}
        )
        assert score == 0.0

    def test_genre_match(self):
        """Test basic genre matching."""
        content = {"genres": ["action", "comedy"]}
        profile = {"genres": {"action": 5, "comedy": 3}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown['genre_score'] > 0

    def test_keyword_match(self):
        """Test keyword matching."""
        content = {"keywords": ["superhero", "origin story"]}
        profile = {"tmdb_keywords": {"superhero": 10, "origin story": 5}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown['keyword_score'] > 0

    def test_actor_match(self):
        """Test actor matching."""
        content = {"cast": ["Actor A", "Actor B"]}
        profile = {"actors": {"Actor A": 5, "Actor B": 3}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown['actor_score'] > 0

    def test_director_match_movie(self):
        """Test director matching for movies."""
        content = {"directors": ["Director X"]}
        profile = {"directors": {"Director X": 5}}

        score, breakdown = calculate_similarity_score(
            content, profile, media_type='movie'
        )

        assert score > 0
        assert breakdown['director_score'] > 0

    def test_studio_match_tv(self):
        """Test studio matching for TV shows."""
        content = {"studio": "HBO"}
        profile = {"studio": {"hbo": 5}}

        score, breakdown = calculate_similarity_score(
            content, profile, media_type='tv'
        )

        assert score > 0
        assert breakdown['studio_score'] > 0

    def test_case_insensitive_matching(self):
        """Test that matching is case-insensitive."""
        content = {"genres": ["ACTION"]}
        profile = {"genres": {"action": 5}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown['genre_score'] > 0

    def test_score_capped_at_one(self):
        """Test that score is capped at 1.0 (100%)."""
        # Create a scenario that would exceed 1.0 without capping
        content = {
            "genres": ["action", "comedy", "drama", "thriller"],
            "cast": ["A", "B", "C", "D", "E"],
            "keywords": ["kw1", "kw2", "kw3", "kw4", "kw5"],
            "directors": ["Dir1"]
        }
        profile = {
            "genres": {"action": 100, "comedy": 100, "drama": 100, "thriller": 100},
            "actors": {"A": 100, "B": 100, "C": 100, "D": 100, "E": 100},
            "tmdb_keywords": {"kw1": 100, "kw2": 100, "kw3": 100, "kw4": 100, "kw5": 100},
            "directors": {"Dir1": 100}
        }

        score, breakdown = calculate_similarity_score(content, profile)

        assert score <= 1.0

    def test_breakdown_structure(self):
        """Test that breakdown has expected structure."""
        score, breakdown = calculate_similarity_score(
            {"genres": ["action"]},
            {"genres": {"action": 5}}
        )

        assert 'genre_score' in breakdown
        assert 'director_score' in breakdown
        assert 'actor_score' in breakdown
        assert 'keyword_score' in breakdown
        assert 'language_score' in breakdown
        assert 'details' in breakdown
