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
    _redistribute_weights,
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


class TestCalculateRecencyMultiplier:
    """Tests for calculate_recency_multiplier() function."""

    def test_disabled_returns_one(self):
        """Test that disabled recency returns 1.0."""
        from datetime import datetime, timezone
        viewed_at = datetime.now(timezone.utc).timestamp()
        result = calculate_recency_multiplier(viewed_at, {'enabled': False})
        assert result == 1.0

    def test_recent_0_30_days(self):
        """Test multiplier for 0-30 day old views."""
        from datetime import datetime, timezone, timedelta
        viewed_at = (datetime.now(timezone.utc) - timedelta(days=15)).timestamp()
        config = {'enabled': True, 'days_0_30': 1.0}
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 1.0

    def test_31_90_days(self):
        """Test multiplier for 31-90 day old views."""
        from datetime import datetime, timezone, timedelta
        viewed_at = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
        config = {'enabled': True, 'days_31_90': 0.75}
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 0.75

    def test_91_180_days(self):
        """Test multiplier for 91-180 day old views."""
        from datetime import datetime, timezone, timedelta
        viewed_at = (datetime.now(timezone.utc) - timedelta(days=120)).timestamp()
        config = {'enabled': True, 'days_91_180': 0.50}
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 0.50

    def test_181_365_days(self):
        """Test multiplier for 181-365 day old views."""
        from datetime import datetime, timezone, timedelta
        viewed_at = (datetime.now(timezone.utc) - timedelta(days=300)).timestamp()
        config = {'enabled': True, 'days_181_365': 0.25}
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 0.25

    def test_over_365_days(self):
        """Test multiplier for views older than 365 days."""
        from datetime import datetime, timezone, timedelta
        viewed_at = (datetime.now(timezone.utc) - timedelta(days=400)).timestamp()
        config = {'enabled': True, 'days_365_plus': 0.10}
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 0.10

    def test_default_enabled_true(self):
        """Test that enabled defaults to True when not specified."""
        from datetime import datetime, timezone, timedelta
        viewed_at = (datetime.now(timezone.utc) - timedelta(days=15)).timestamp()
        config = {'days_0_30': 0.9}  # No 'enabled' key
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 0.9


class TestRedistributeWeights:
    """Tests for _redistribute_weights() function."""

    def test_no_redistribution_when_all_data(self):
        """Test that weights are not redistributed when all data present."""
        weights = {'genre': 0.25, 'director': 0.15, 'actor': 0.20, 'keyword': 0.40}
        profile = {
            'genres': {'action': 1},
            'directors': {'Dir X': 1},
            'actors': {'Actor A': 1},
            'keywords': {'kw1': 1}
        }
        result = _redistribute_weights(weights, profile, 'movie')
        # Weights should be close to original (some redistribution due to language=0)
        assert result['genre'] > 0
        assert result['director'] > 0
        assert result['actor'] > 0
        assert result['keyword'] > 0

    def test_redistribution_when_missing_keywords(self):
        """Test weight redistribution when keywords are missing."""
        weights = {'genre': 0.25, 'actor': 0.25, 'keyword': 0.50}
        profile = {
            'genres': {'action': 1},
            'actors': {'Actor A': 1},
            # No keywords
        }
        result = _redistribute_weights(weights, profile, 'movie')
        # Keyword weight should be 0, others should be higher
        assert result['keyword'] == 0
        assert result['genre'] > 0.25
        assert result['actor'] > 0.25

    def test_returns_original_when_no_data(self):
        """Test returns original weights when no profile data."""
        weights = {'genre': 0.25, 'actor': 0.25, 'keyword': 0.50}
        profile = {}  # Empty profile
        result = _redistribute_weights(weights, profile, 'movie')
        assert result == weights

    def test_tv_uses_studio_not_director(self):
        """Test that TV mode uses studio instead of director."""
        weights = {'genre': 0.25, 'studio': 0.15, 'director': 0.15, 'actor': 0.20, 'keyword': 0.25}
        profile = {
            'genres': {'drama': 1},
            'studios': {'HBO': 1},
            'actors': {'Actor A': 1}
        }
        result = _redistribute_weights(weights, profile, 'tv')
        # Studio should have weight, director should be 0 for TV
        assert result['studio'] > 0
        assert result['director'] == 0


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

    def test_language_match(self):
        """Test language matching."""
        content = {"language": "English", "genres": ["action"]}
        profile = {"languages": {"english": 5}, "genres": {"action": 1}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown['language_score'] >= 0

    def test_language_na_ignored(self):
        """Test that N/A language is ignored."""
        content = {"language": "N/A", "genres": ["action"]}
        profile = {"languages": {"english": 5}, "genres": {"action": 1}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert breakdown['language_score'] == 0

    def test_normalize_counters_false(self):
        """Test with normalize_counters=False."""
        content = {"genres": ["action"]}
        profile = {"genres": {"action": 5}}

        score, breakdown = calculate_similarity_score(
            content, profile, normalize_counters=False
        )

        assert score > 0
        assert breakdown['genre_score'] > 0

    def test_fuzzy_keywords_disabled(self):
        """Test with fuzzy keywords disabled."""
        content = {"keywords": ["superhero movie"]}
        profile = {"keywords": {"superhero": 5}}

        score, breakdown = calculate_similarity_score(
            content, profile, use_fuzzy_keywords=False
        )

        # Without fuzzy matching, "superhero movie" won't match "superhero"
        assert breakdown['keyword_score'] == 0

    def test_studio_as_list_tv(self):
        """Test studio matching when studio is a list (TV)."""
        content = {"studios": ["HBO", "Netflix"]}
        profile = {"studios": {"hbo": 5, "netflix": 3}}

        score, breakdown = calculate_similarity_score(
            content, profile, media_type='tv'
        )

        assert score > 0
        assert breakdown['studio_score'] > 0

    def test_studio_na_ignored(self):
        """Test that N/A studio is ignored."""
        content = {"studio": "N/A"}
        profile = {"studios": {"hbo": 5}}

        score, breakdown = calculate_similarity_score(
            content, profile, media_type='tv'
        )

        assert breakdown['studio_score'] == 0

    def test_custom_weights(self):
        """Test with custom weights."""
        content = {"genres": ["action"]}
        profile = {"genres": {"action": 5}}

        custom_weights = {'genre': 0.80, 'actor': 0.10, 'keyword': 0.10}
        score, breakdown = calculate_similarity_score(
            content, profile, weights=custom_weights
        )

        assert score > 0
        # Genre should dominate due to high weight
        assert breakdown['genre_score'] > 0

    def test_director_case_insensitive(self):
        """Test director matching is case-insensitive."""
        content = {"directors": ["christopher nolan"]}
        profile = {"directors": {"Christopher Nolan": 5}}

        score, breakdown = calculate_similarity_score(
            content, profile, media_type='movie'
        )

        assert score > 0
        assert breakdown['director_score'] > 0

    def test_actor_case_insensitive(self):
        """Test actor matching is case-insensitive."""
        content = {"cast": ["TOM HANKS"]}
        profile = {"actors": {"Tom Hanks": 5}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown['actor_score'] > 0

    def test_genre_normalization_in_score(self):
        """Test that genre normalization is applied during scoring."""
        content = {"genres": ["Sci-Fi"]}
        profile = {"genres": {"science fiction": 5}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown['genre_score'] > 0

    def test_multiple_genres_cumulative(self):
        """Test that multiple matching genres contribute cumulatively."""
        content_single = {"genres": ["action"]}
        content_multi = {"genres": ["action", "comedy", "drama"]}
        profile = {"genres": {"action": 5, "comedy": 5, "drama": 5}}

        score_single, _ = calculate_similarity_score(content_single, profile)
        score_multi, _ = calculate_similarity_score(content_multi, profile)

        assert score_multi > score_single

    def test_per_item_weight_redistribution(self):
        """Test per-item weight redistribution when some components don't match."""
        # Content with genres but no keywords
        content = {"genres": ["action"]}
        profile = {
            "genres": {"action": 5},
            "keywords": {"superhero": 10}  # Profile has keywords but content doesn't
        }

        score, breakdown = calculate_similarity_score(content, profile)

        # Should still get a score from genres
        assert score > 0
        assert breakdown['genre_score'] > 0


class TestNegativeSignalsScoring:
    """Tests for negative signal handling in calculate_similarity_score()."""

    def test_negative_genre_reduces_score(self):
        """Test that negative genre preference reduces score."""
        content = {"genres": ["action", "comedy"]}
        profile_positive = {"genres": {"action": 5, "comedy": 5}}
        profile_with_negative = {"genres": {"action": 5, "comedy": -3}}

        score_positive, _ = calculate_similarity_score(content, profile_positive)
        score_negative, breakdown = calculate_similarity_score(content, profile_with_negative)

        assert score_negative < score_positive
        # Check breakdown shows negative
        assert any("NEGATIVE" in str(d) for d in breakdown['details']['genres'])

    def test_negative_actor_reduces_score(self):
        """Test that negative actor preference reduces score."""
        content = {"cast": ["Actor A", "Actor B"]}
        profile_positive = {"actors": {"Actor A": 5, "Actor B": 3}}
        profile_with_negative = {"actors": {"Actor A": 5, "Actor B": -2}}

        score_positive, _ = calculate_similarity_score(content, profile_positive)
        score_negative, _ = calculate_similarity_score(content, profile_with_negative)

        assert score_negative < score_positive

    def test_negative_keyword_reduces_score(self):
        """Test that negative keyword preference reduces score."""
        content = {"keywords": ["superhero", "origin story"]}
        profile_positive = {"tmdb_keywords": {"superhero": 10, "origin story": 5}}
        profile_with_negative = {"tmdb_keywords": {"superhero": 10, "origin story": -3}}

        score_positive, _ = calculate_similarity_score(content, profile_positive)
        score_negative, _ = calculate_similarity_score(content, profile_with_negative)

        assert score_negative < score_positive

    def test_negative_director_reduces_score(self):
        """Test that negative director preference reduces score."""
        content = {"directors": ["Director X", "Director Y"]}
        profile_positive = {"directors": {"Director X": 5, "Director Y": 3}}
        profile_with_negative = {"directors": {"Director X": 5, "Director Y": -2}}

        score_positive, _ = calculate_similarity_score(content, profile_positive, media_type='movie')
        score_negative, _ = calculate_similarity_score(content, profile_with_negative, media_type='movie')

        assert score_negative < score_positive

    def test_negative_studio_reduces_score(self):
        """Test that negative studio preference reduces score."""
        content = {"studio": "hbo"}
        profile_positive = {"studios": {"hbo": 5}}
        profile_with_negative = {"studios": {"hbo": -3}}

        score_positive, _ = calculate_similarity_score(content, profile_positive, media_type='tv')
        score_negative, breakdown = calculate_similarity_score(content, profile_with_negative, media_type='tv')

        assert score_negative < score_positive
        assert "NEGATIVE" in str(breakdown['details']['studio'])

    def test_score_not_negative(self):
        """Test that score doesn't go below 0 even with all negative signals."""
        content = {"genres": ["action", "comedy"], "cast": ["Actor A"]}
        profile = {
            "genres": {"action": -5, "comedy": -5},
            "actors": {"Actor A": -10}
        }

        score, breakdown = calculate_similarity_score(content, profile)

        # Score should be non-negative
        assert score >= 0
        assert breakdown['genre_score'] >= 0
        assert breakdown['actor_score'] >= 0

    def test_max_positive_ignores_negatives(self):
        """Test that max_positive calculation ignores negative values."""
        content = {"genres": ["action"]}
        # Profile with one highly positive and one highly negative
        profile = {"genres": {"action": 10, "horror": -100}}

        score, breakdown = calculate_similarity_score(content, profile)

        # Should score based on action:10, not affected by horror:-100
        assert score > 0
        assert breakdown['genre_score'] > 0

    def test_mixed_positive_negative_genres(self):
        """Test content with mix of positive and negative genre matches."""
        content = {"genres": ["action", "horror", "comedy"]}
        profile = {
            "genres": {
                "action": 10,   # User loves action
                "horror": -5,   # User dislikes horror
                "comedy": 3     # User likes comedy
            }
        }

        score_with_horror, breakdown = calculate_similarity_score(content, profile)

        # Compare to content without horror
        content_no_horror = {"genres": ["action", "comedy"]}
        score_no_horror, _ = calculate_similarity_score(content_no_horror, profile)

        # Having horror (which user dislikes) should reduce score
        assert score_with_horror < score_no_horror

    def test_negative_penalty_in_breakdown_details(self):
        """Test that negative penalty is shown in breakdown details."""
        content = {"genres": ["horror"]}
        profile = {"genres": {"horror": -5}}

        score, breakdown = calculate_similarity_score(content, profile)

        # Check that breakdown details show the negative signal
        genre_details = breakdown['details']['genres']
        assert len(genre_details) > 0
        assert "NEGATIVE" in genre_details[0]
        assert "penalty" in genre_details[0]
