"""
Tests for utils/scoring.py - Scoring and similarity functions.
"""

import dataclasses
from collections import Counter

import pytest

from utils.scoring import (
    ScoringOptions,
    _apply_active_weight_redistribution,
    _apply_popularity_dampening,
    _normalize_user_genre_counts,
    _redistribute_weights,
    _score_actor_component,
    _score_director_component,
    _score_genre_component,
    _score_keyword_component,
    _score_language_component,
    _score_studio_component,
    _tfidf_threshold,
    calculate_recency_multiplier,
    calculate_rewatch_multiplier,
    calculate_similarity_score,
    fuzzy_keyword_match,
    normalize_genre,
    select_tiered_recommendations,
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
        result = calculate_recency_multiplier(viewed_at, {"enabled": False})
        assert result == 1.0

    def test_recent_0_30_days(self):
        """Test multiplier for 0-30 day old views."""
        from datetime import datetime, timedelta, timezone

        viewed_at = (datetime.now(timezone.utc) - timedelta(days=15)).timestamp()
        config = {"enabled": True, "days_0_30": 1.0}
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 1.0

    def test_31_90_days(self):
        """Test multiplier for 31-90 day old views."""
        from datetime import datetime, timedelta, timezone

        viewed_at = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
        config = {"enabled": True, "days_31_90": 0.75}
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 0.75

    def test_91_180_days(self):
        """Test multiplier for 91-180 day old views."""
        from datetime import datetime, timedelta, timezone

        viewed_at = (datetime.now(timezone.utc) - timedelta(days=120)).timestamp()
        config = {"enabled": True, "days_91_180": 0.50}
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 0.50

    def test_181_365_days(self):
        """Test multiplier for 181-365 day old views."""
        from datetime import datetime, timedelta, timezone

        viewed_at = (datetime.now(timezone.utc) - timedelta(days=300)).timestamp()
        config = {"enabled": True, "days_181_365": 0.25}
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 0.25

    def test_over_365_days(self):
        """Test multiplier for views older than 365 days."""
        from datetime import datetime, timedelta, timezone

        viewed_at = (datetime.now(timezone.utc) - timedelta(days=400)).timestamp()
        config = {"enabled": True, "days_365_plus": 0.10}
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 0.10

    def test_default_enabled_true(self):
        """Test that enabled defaults to True when not specified."""
        from datetime import datetime, timedelta, timezone

        viewed_at = (datetime.now(timezone.utc) - timedelta(days=15)).timestamp()
        config = {"days_0_30": 0.9}  # No 'enabled' key
        result = calculate_recency_multiplier(viewed_at, config)
        assert result == 0.9


class TestRedistributeWeights:
    """Tests for _redistribute_weights() function."""

    def test_no_redistribution_when_all_data(self):
        """Test that weights are not redistributed when all data present."""
        weights = {"genre": 0.25, "director": 0.15, "actor": 0.20, "keyword": 0.40}
        profile = {"genres": {"action": 1}, "directors": {"Dir X": 1}, "actors": {"Actor A": 1}, "keywords": {"kw1": 1}}
        result = _redistribute_weights(weights, profile, "movie")
        # Weights should be close to original (some redistribution due to language=0)
        assert result["genre"] > 0
        assert result["director"] > 0
        assert result["actor"] > 0
        assert result["keyword"] > 0

    def test_redistribution_when_missing_keywords(self):
        """Test weight redistribution when keywords are missing."""
        weights = {"genre": 0.25, "actor": 0.25, "keyword": 0.50}
        profile = {
            "genres": {"action": 1},
            "actors": {"Actor A": 1},
            # No keywords
        }
        result = _redistribute_weights(weights, profile, "movie")
        # Keyword weight should be 0, others should be higher
        assert result["keyword"] == 0
        assert result["genre"] > 0.25
        assert result["actor"] > 0.25

    def test_returns_original_when_no_data(self):
        """Test returns original weights when no profile data."""
        weights = {"genre": 0.25, "actor": 0.25, "keyword": 0.50}
        profile = {}  # Empty profile
        result = _redistribute_weights(weights, profile, "movie")
        assert result == weights

    def test_tv_uses_studio_not_director(self):
        """Test that TV mode uses studio instead of director."""
        weights = {"genre": 0.25, "studio": 0.15, "director": 0.15, "actor": 0.20, "keyword": 0.25}
        profile = {"genres": {"drama": 1}, "studios": {"HBO": 1}, "actors": {"Actor A": 1}}
        result = _redistribute_weights(weights, profile, "tv")
        # Studio should have weight, director should be 0 for TV
        assert result["studio"] > 0
        assert result["director"] == 0


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
        score, breakdown = calculate_similarity_score({"genres": ["action"]}, {})
        assert score == 0.0

    def test_genre_match(self):
        """Test basic genre matching."""
        content = {"genres": ["action", "comedy"]}
        profile = {"genres": {"action": 5, "comedy": 3}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown["genre_score"] > 0

    def test_keyword_match(self):
        """Test keyword matching."""
        content = {"keywords": ["superhero", "origin story"]}
        # "keywords" (#273 PR4 - the one canonical name every real caller
        # uses; the "tmdb_keywords" dual-key tolerance this test used to
        # exercise was dead code no real caller ever needed - see
        # CHANGELOG).
        profile = {"keywords": {"superhero": 10, "origin story": 5}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown["keyword_score"] > 0

    def test_actor_match(self):
        """Test actor matching."""
        content = {"cast": ["Actor A", "Actor B"]}
        profile = {"actors": {"Actor A": 5, "Actor B": 3}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown["actor_score"] > 0

    def test_director_match_movie(self):
        """Test director matching for movies."""
        content = {"directors": ["Director X"]}
        profile = {"directors": {"Director X": 5}}

        score, breakdown = calculate_similarity_score(content, profile, media_type="movie")

        assert score > 0
        assert breakdown["director_score"] > 0

    def test_studio_match_tv(self):
        """Test studio matching for TV shows."""
        content = {"studio": "HBO"}
        profile = {"studios": {"hbo": 5}}

        score, breakdown = calculate_similarity_score(content, profile, media_type="tv")

        assert score > 0
        assert breakdown["studio_score"] > 0

    def test_case_insensitive_matching(self):
        """Test that matching is case-insensitive."""
        content = {"genres": ["ACTION"]}
        profile = {"genres": {"action": 5}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown["genre_score"] > 0

    def test_score_capped_at_one(self):
        """Test that score is capped at 1.0 (100%)."""
        # Create a scenario that would exceed 1.0 without capping
        content = {
            "genres": ["action", "comedy", "drama", "thriller"],
            "cast": ["A", "B", "C", "D", "E"],
            "keywords": ["kw1", "kw2", "kw3", "kw4", "kw5"],
            "directors": ["Dir1"],
        }
        profile = {
            "genres": {"action": 100, "comedy": 100, "drama": 100, "thriller": 100},
            "actors": {"A": 100, "B": 100, "C": 100, "D": 100, "E": 100},
            "keywords": {"kw1": 100, "kw2": 100, "kw3": 100, "kw4": 100, "kw5": 100},  # #273 PR4
            "directors": {"Dir1": 100},
        }

        score, breakdown = calculate_similarity_score(content, profile)

        assert score <= 1.0

    def test_breakdown_structure(self):
        """Test that breakdown has expected structure."""
        score, breakdown = calculate_similarity_score({"genres": ["action"]}, {"genres": {"action": 5}})

        assert "genre_score" in breakdown
        assert "director_score" in breakdown
        assert "actor_score" in breakdown
        assert "keyword_score" in breakdown
        assert "language_score" in breakdown
        assert "details" in breakdown

    def test_language_match(self):
        """Test language matching."""
        content = {"language": "English", "genres": ["action"]}
        profile = {"languages": {"english": 5}, "genres": {"action": 1}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown["language_score"] >= 0

    def test_language_na_ignored(self):
        """Test that N/A language is ignored."""
        content = {"language": "N/A", "genres": ["action"]}
        profile = {"languages": {"english": 5}, "genres": {"action": 1}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert breakdown["language_score"] == 0

    def test_normalize_counters_false(self):
        """Test with normalize_counters=False."""
        content = {"genres": ["action"]}
        profile = {"genres": {"action": 5}}

        score, breakdown = calculate_similarity_score(content, profile, normalize_counters=False)

        assert score > 0
        assert breakdown["genre_score"] > 0

    def test_fuzzy_keywords_disabled(self):
        """Test with fuzzy keywords disabled."""
        content = {"keywords": ["superhero movie"]}
        profile = {"keywords": {"superhero": 5}}

        score, breakdown = calculate_similarity_score(content, profile, use_fuzzy_keywords=False)

        # Without fuzzy matching, "superhero movie" won't match "superhero"
        assert breakdown["keyword_score"] == 0

    def test_studio_as_list_tv(self):
        """Test studio matching when studio is a list (TV)."""
        content = {"studios": ["HBO", "Netflix"]}
        profile = {"studios": {"hbo": 5, "netflix": 3}}

        score, breakdown = calculate_similarity_score(content, profile, media_type="tv")

        assert score > 0
        assert breakdown["studio_score"] > 0

    def test_studio_na_ignored(self):
        """Test that N/A studio is ignored."""
        content = {"studio": "N/A"}
        profile = {"studios": {"hbo": 5}}

        score, breakdown = calculate_similarity_score(content, profile, media_type="tv")

        assert breakdown["studio_score"] == 0

    def test_custom_weights(self):
        """Test with custom weights."""
        content = {"genres": ["action"]}
        profile = {"genres": {"action": 5}}

        custom_weights = {"genre": 0.80, "actor": 0.10, "keyword": 0.10}
        score, breakdown = calculate_similarity_score(content, profile, weights=custom_weights)

        assert score > 0
        # Genre should dominate due to high weight
        assert breakdown["genre_score"] > 0

    def test_director_case_insensitive(self):
        """Test director matching is case-insensitive."""
        content = {"directors": ["christopher nolan"]}
        profile = {"directors": {"Christopher Nolan": 5}}

        score, breakdown = calculate_similarity_score(content, profile, media_type="movie")

        assert score > 0
        assert breakdown["director_score"] > 0

    def test_actor_case_insensitive(self):
        """Test actor matching is case-insensitive."""
        content = {"cast": ["TOM HANKS"]}
        profile = {"actors": {"Tom Hanks": 5}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown["actor_score"] > 0

    def test_genre_normalization_in_score(self):
        """Test that genre normalization is applied during scoring."""
        content = {"genres": ["Sci-Fi"]}
        profile = {"genres": {"science fiction": 5}}

        score, breakdown = calculate_similarity_score(content, profile)

        assert score > 0
        assert breakdown["genre_score"] > 0

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
            "keywords": {"superhero": 10},  # Profile has keywords but content doesn't
        }

        score, breakdown = calculate_similarity_score(content, profile)

        # Should still get a score from genres
        assert score > 0
        assert breakdown["genre_score"] > 0


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
        assert any("NEGATIVE" in str(d) for d in breakdown["details"]["genres"])

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
        # "keywords" (#273 PR4) - see test_keyword_match's own note above.
        profile_positive = {"keywords": {"superhero": 10, "origin story": 5}}
        profile_with_negative = {"keywords": {"superhero": 10, "origin story": -3}}

        score_positive, _ = calculate_similarity_score(content, profile_positive)
        score_negative, _ = calculate_similarity_score(content, profile_with_negative)

        assert score_negative < score_positive

    def test_negative_director_reduces_score(self):
        """Test that negative director preference reduces score."""
        content = {"directors": ["Director X", "Director Y"]}
        profile_positive = {"directors": {"Director X": 5, "Director Y": 3}}
        profile_with_negative = {"directors": {"Director X": 5, "Director Y": -2}}

        score_positive, _ = calculate_similarity_score(content, profile_positive, media_type="movie")
        score_negative, _ = calculate_similarity_score(content, profile_with_negative, media_type="movie")

        assert score_negative < score_positive

    def test_negative_studio_reduces_score(self):
        """Test that negative studio preference reduces score."""
        content = {"studio": "hbo"}
        profile_positive = {"studios": {"hbo": 5}}
        profile_with_negative = {"studios": {"hbo": -3}}

        score_positive, _ = calculate_similarity_score(content, profile_positive, media_type="tv")
        score_negative, breakdown = calculate_similarity_score(content, profile_with_negative, media_type="tv")

        assert score_negative < score_positive
        assert "NEGATIVE" in str(breakdown["details"]["studio"])

    def test_score_not_negative(self):
        """Test that score doesn't go below 0 even with all negative signals."""
        content = {"genres": ["action", "comedy"], "cast": ["Actor A"]}
        profile = {"genres": {"action": -5, "comedy": -5}, "actors": {"Actor A": -10}}

        score, breakdown = calculate_similarity_score(content, profile)

        # Score should be non-negative
        assert score >= 0
        assert breakdown["genre_score"] >= 0
        assert breakdown["actor_score"] >= 0

    def test_max_positive_ignores_negatives(self):
        """Test that max_positive calculation ignores negative values."""
        content = {"genres": ["action"]}
        # Profile with one highly positive and one highly negative
        profile = {"genres": {"action": 10, "horror": -100}}

        score, breakdown = calculate_similarity_score(content, profile)

        # Should score based on action:10, not affected by horror:-100
        assert score > 0
        assert breakdown["genre_score"] > 0

    def test_mixed_positive_negative_genres(self):
        """Test content with mix of positive and negative genre matches."""
        content = {"genres": ["action", "horror", "comedy"]}
        profile = {
            "genres": {
                "action": 10,  # User loves action
                "horror": -5,  # User dislikes horror
                "comedy": 3,  # User likes comedy
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
        genre_details = breakdown["details"]["genres"]
        assert len(genre_details) > 0
        assert "NEGATIVE" in genre_details[0]
        assert "penalty" in genre_details[0]


class TestSelectTieredRecommendations:
    """Tests for select_tiered_recommendations() function."""

    def test_empty_list_returns_empty(self):
        """Test that empty input returns empty output."""
        result = select_tiered_recommendations([], 10)
        assert result == []

    def test_returns_correct_count(self):
        """Test that function returns requested number of items."""
        items = [{"similarity_score": 0.9 - i * 0.05} for i in range(50)]
        result = select_tiered_recommendations(items, 10)
        assert len(result) == 10

    def test_fewer_items_than_limit(self):
        """Test when fewer items available than requested."""
        items = [{"similarity_score": 0.9 - i * 0.1} for i in range(5)]
        result = select_tiered_recommendations(items, 10)
        assert len(result) == 5

    def test_includes_high_score_items(self):
        """Test that highest scored items are included (safe picks)."""
        items = [{"similarity_score": 0.9 - i * 0.01, "title": f"Item{i}"} for i in range(100)]
        result = select_tiered_recommendations(items, 10)

        # Top item should be in result
        titles = [r["title"] for r in result]
        assert "Item0" in titles

    def test_includes_variety(self):
        """Test that result includes items from different tiers."""
        items = [{"similarity_score": 1.0 - i * 0.01, "title": f"Item{i}"} for i in range(100)]
        result = select_tiered_recommendations(items, 10)

        # Check we have items from different score ranges
        scores = [r["similarity_score"] for r in result]
        score_range = max(scores) - min(scores)
        # Should have some variety (not all from top tier)
        assert score_range > 0.1

    def test_result_sorted_by_score(self):
        """Test that final result is sorted by score."""
        items = [{"similarity_score": 0.9 - i * 0.01} for i in range(100)]
        result = select_tiered_recommendations(items, 10)

        # Result should be sorted descending
        scores = [r["similarity_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_custom_tier_percentages(self):
        """Test with custom tier percentages."""
        items = [{"similarity_score": 0.9 - i * 0.01} for i in range(100)]
        # All safe picks
        result = select_tiered_recommendations(items, 10, safe_percent=1.0, diverse_percent=0.0, wildcard_percent=0.0)
        assert len(result) == 10

    def test_works_with_score_key(self):
        """Test with 'score' key instead of 'similarity_score'."""
        items = [{"score": 0.9 - i * 0.01, "title": f"Item{i}"} for i in range(50)]
        result = select_tiered_recommendations(items, 10)
        assert len(result) == 10

    def test_seeded_rng_is_deterministic(self):
        """A seeded random.Random passed via `rng` must produce
        byte-identical selection across repeated calls (deterministic
        harness requirement) without touching the process-global
        `random` module state."""
        items = [{"similarity_score": 1.0 - i * 0.01, "title": f"Item{i}"} for i in range(100)]

        import random as random_module

        result_a = select_tiered_recommendations(items, 20, rng=random_module.Random(42))
        result_b = select_tiered_recommendations(items, 20, rng=random_module.Random(42))

        assert [r["title"] for r in result_a] == [r["title"] for r in result_b]

    def test_different_seeds_can_differ(self):
        """Sanity check that the seed actually drives the diverse/wildcard
        picks (i.e. this isn't accidentally a no-op)."""
        items = [{"similarity_score": 1.0 - i * 0.01, "title": f"Item{i}"} for i in range(100)]

        import random as random_module

        results = {
            seed: tuple(r["title"] for r in select_tiered_recommendations(items, 20, rng=random_module.Random(seed)))
            for seed in range(10)
        }
        assert len(set(results.values())) > 1

    def test_omitting_rng_uses_module_level_random(self):
        """Default (no `rng` passed) must preserve pre-existing behavior:
        draws from the module-level `random`, so seeding the module-level
        random module directly still makes two calls identical."""
        items = [{"similarity_score": 1.0 - i * 0.01, "title": f"Item{i}"} for i in range(100)]

        import random as random_module

        random_module.seed(7)
        result_a = select_tiered_recommendations(items, 20)
        random_module.seed(7)
        result_b = select_tiered_recommendations(items, 20)

        assert [r["title"] for r in result_a] == [r["title"] for r in result_b]


class TestPopularityDampening:
    """Tests for popularity dampening in calculate_similarity_score."""

    def test_no_dampening_below_threshold(self):
        """Test that content below threshold gets no dampening."""
        content = {"genres": ["action"], "vote_count": 10000}
        profile = {"genres": {"action": 10}}
        score, breakdown = calculate_similarity_score(
            content, profile, use_popularity_dampening=True, popularity_threshold=50000
        )
        assert "popularity_dampening" not in breakdown

    def test_dampening_above_threshold(self):
        """Test that very popular content gets dampened."""
        content = {"genres": ["action"], "vote_count": 500000}
        profile = {"genres": {"action": 10}}
        score, breakdown = calculate_similarity_score(
            content, profile, use_popularity_dampening=True, popularity_threshold=50000
        )
        assert "popularity_dampening" in breakdown
        assert breakdown["popularity_dampening"] < 1.0

    def test_dampening_reduces_score(self):
        """Test that dampening reduces final score."""
        content_popular = {"genres": ["action"], "vote_count": 500000}
        content_normal = {"genres": ["action"], "vote_count": 10000}
        profile = {"genres": {"action": 10}}

        score_popular, _ = calculate_similarity_score(
            content_popular, profile, use_popularity_dampening=True, popularity_threshold=50000
        )
        score_normal, _ = calculate_similarity_score(
            content_normal, profile, use_popularity_dampening=True, popularity_threshold=50000
        )
        assert score_popular < score_normal

    def test_dampening_disabled(self):
        """Test that dampening can be disabled."""
        content = {"genres": ["action"], "vote_count": 5000000}
        profile = {"genres": {"action": 10}}
        score, breakdown = calculate_similarity_score(content, profile, use_popularity_dampening=False)
        assert "popularity_dampening" not in breakdown

    def test_dampening_capped_at_90_percent(self):
        """Test that dampening doesn't exceed 10% penalty."""
        content = {"genres": ["action"], "vote_count": 50000000}  # Very high
        profile = {"genres": {"action": 10}}
        score, breakdown = calculate_similarity_score(
            content, profile, use_popularity_dampening=True, popularity_threshold=50000
        )
        assert breakdown.get("popularity_dampening", 1.0) >= 0.90


class TestScoringOptions:
    """Tests for the ScoringOptions dataclass (PR: collapsed tuning flags)."""

    def test_defaults_match_calculate_similarity_score_defaults(self):
        """ScoringOptions()'s defaults must match calculate_similarity_score()'s
        pre-existing individual-kwarg defaults exactly."""
        options = ScoringOptions()
        assert options.normalize_counters is True
        assert options.use_fuzzy_keywords is True
        assert options.use_tfidf is True
        assert options.tfidf_penalty_threshold == 0.15
        assert options.use_popularity_dampening is True
        assert options.popularity_threshold == 50000

    def test_frozen(self):
        """ScoringOptions is frozen - instances must be immutable."""
        options = ScoringOptions()
        with pytest.raises(dataclasses.FrozenInstanceError):
            options.use_tfidf = False

    def test_individual_kwargs_still_work_without_options(self):
        """Existing call style (no options=) is unaffected - same result as
        passing the equivalent explicit ScoringOptions()."""
        content = {"genres": ["action"]}
        profile = {"genres": {"action": 5}}
        score_default, _ = calculate_similarity_score(content, profile)
        score_via_options, _ = calculate_similarity_score(content, profile, options=ScoringOptions())
        assert score_default == score_via_options

    def test_options_takes_precedence_over_individual_kwargs(self):
        """When both options= and individual kwargs are given, options wins."""
        content = {"genres": ["horror"], "vote_count": 500000}
        profile = {"genres": {"action": 10}}

        score_options_no_dampening, _ = calculate_similarity_score(
            content, profile, options=ScoringOptions(use_popularity_dampening=False), use_popularity_dampening=True
        )
        score_kwarg_no_dampening, _ = calculate_similarity_score(content, profile, use_popularity_dampening=False)
        assert score_options_no_dampening == score_kwarg_no_dampening


class TestTfidfThreshold:
    """Tests for _tfidf_threshold() - shared TF-IDF rarity threshold helper."""

    def test_disabled_returns_zero(self):
        options = ScoringOptions(use_tfidf=False)
        assert _tfidf_threshold({}, "genres", 100, options) == 0

    def test_computes_from_max_count_when_not_precomputed(self):
        options = ScoringOptions(use_tfidf=True, tfidf_penalty_threshold=0.2)
        assert _tfidf_threshold({}, "genres", 100, options) == 20.0

    def test_prefers_precomputed_thresholds(self):
        options = ScoringOptions(use_tfidf=True, tfidf_penalty_threshold=0.2)
        user_profile = {"_tfidf_thresholds": {"genres": 42}}
        assert _tfidf_threshold(user_profile, "genres", 100, options) == 42


class TestNormalizeUserGenreCounts:
    """Tests for _normalize_user_genre_counts()."""

    def test_collapses_variant_names(self):
        counter = Counter({"sci-fi": 5, "science fiction": 3})
        normalized, max_count = _normalize_user_genre_counts(counter)
        assert normalized["science fiction"] == 5
        assert max_count == 5

    def test_empty_counter_defaults_max_to_one(self):
        normalized, max_count = _normalize_user_genre_counts(Counter())
        assert normalized == {}
        assert max_count == 1


class TestScoreGenreComponent:
    """Tests for _score_genre_component() - the genre scoring dimension."""

    def test_matching_genre_scores_positive(self):
        user_genres = Counter({"action": 10})
        normalized, max_count = _normalize_user_genre_counts(user_genres)
        options = ScoringOptions()
        threshold = _tfidf_threshold({}, "genres", max_count, options)
        final, penalty, details = _score_genre_component(
            {"action"}, user_genres, normalized, max_count, 0.25, threshold, options
        )
        assert final > 0
        assert penalty == 0
        assert len(details) == 1
        assert "count" in details[0]

    def test_no_content_genres_scores_zero(self):
        options = ScoringOptions()
        final, penalty, details = _score_genre_component(set(), Counter(), {}, 1, 0.25, 0, options)
        assert (final, penalty, details) == (0.0, 0.0, [])

    def test_negative_signal_penalizes(self):
        user_genres = Counter({"horror": -5})
        normalized, max_count = _normalize_user_genre_counts(user_genres)
        options = ScoringOptions()
        final, penalty, details = _score_genre_component(
            {"horror"}, user_genres, normalized, max_count, 0.25, 0, options
        )
        assert "NEGATIVE" in details[0]

    def test_tfidf_rarity_penalty_applied(self):
        """A genre the user has but rarely (below the TF-IDF threshold) is
        penalized rather than scored positively."""
        user_genres = Counter({"action": 1, "comedy": 100})
        normalized, max_count = _normalize_user_genre_counts(user_genres)
        options = ScoringOptions(use_tfidf=True, tfidf_penalty_threshold=0.5)
        threshold = _tfidf_threshold({}, "genres", max_count, options)
        final, penalty, details = _score_genre_component(
            {"action"}, user_genres, normalized, max_count, 0.25, threshold, options
        )
        assert penalty > 0
        assert "TF-IDF" in details[0]


class TestScoreDirectorComponent:
    """Tests for _score_director_component() - movies-only dimension."""

    def test_matching_director_scores_positive(self):
        user_prefs = {"directors": Counter({"Jane Doe": 5})}
        options = ScoringOptions()
        final, penalty, details = _score_director_component(["Jane Doe"], user_prefs, {"directors": 5}, 0.15, options)
        assert final > 0
        assert details

    def test_no_directors_scores_zero(self):
        options = ScoringOptions()
        final, penalty, details = _score_director_component(
            [], {"directors": Counter()}, {"directors": 1}, 0.15, options
        )
        assert (final, penalty, details) == (0.0, 0.0, [])

    def test_case_insensitive_match_via_lower_fallback(self):
        user_prefs = {"directors": Counter({"Jane Doe": 5})}
        options = ScoringOptions()
        final, penalty, details = _score_director_component(["jane doe"], user_prefs, {"directors": 5}, 0.15, options)
        assert final > 0

    def test_negative_signal_penalizes(self):
        user_prefs = {"directors": Counter({"Bad Director": -3})}
        options = ScoringOptions()
        final, penalty, details = _score_director_component(
            ["Bad Director"], user_prefs, {"directors": 3}, 0.15, options
        )
        assert penalty > 0
        assert "NEGATIVE" in details[0]


class TestScoreStudioComponent:
    """Tests for _score_studio_component() - TV-only dimension."""

    def test_matching_studio_scores_positive(self):
        user_prefs = {"studios": Counter({"hbo": 5})}
        options = ScoringOptions()
        final, penalty, detail = _score_studio_component("HBO", user_prefs, {"studios": 5}, 0.15, options)
        assert final > 0
        assert detail is not None

    def test_no_studio_scores_zero(self):
        options = ScoringOptions()
        final, penalty, detail = _score_studio_component("N/A", {"studios": Counter()}, {"studios": 1}, 0.15, options)
        assert (final, penalty, detail) == (0.0, 0.0, None)

    def test_list_of_studios_accepted(self):
        user_prefs = {"studios": Counter({"hbo": 5, "amc": 2})}
        options = ScoringOptions()
        final, penalty, detail = _score_studio_component(["HBO", "AMC"], user_prefs, {"studios": 5}, 0.15, options)
        assert final > 0

    def test_detail_reflects_last_studio_processed(self):
        """score_breakdown['details']['studio'] is a single string
        (assignment, not append) - the helper must match that: detail is
        whichever studio in the list was processed last, not the first."""
        user_prefs = {"studios": Counter({"hbo": 5, "amc": 2})}
        options = ScoringOptions()
        _final, _penalty, detail = _score_studio_component(["HBO", "AMC"], user_prefs, {"studios": 5}, 0.15, options)
        assert detail.startswith("AMC")


class TestScoreActorComponent:
    """Tests for _score_actor_component()."""

    def test_matching_actor_scores_positive(self):
        user_prefs = {"actors": Counter({"Tom Hanks": 8})}
        options = ScoringOptions()
        final, penalty, details = _score_actor_component(["Tom Hanks"], user_prefs, {"actors": 8}, 0.20, options)
        assert final > 0
        assert details

    def test_no_cast_scores_zero(self):
        options = ScoringOptions()
        final, penalty, details = _score_actor_component([], {"actors": Counter()}, {"actors": 1}, 0.20, options)
        assert (final, penalty, details) == (0.0, 0.0, [])

    def test_negative_signal_penalizes(self):
        user_prefs = {"actors": Counter({"Bad Actor": -4})}
        options = ScoringOptions()
        final, penalty, details = _score_actor_component(["Bad Actor"], user_prefs, {"actors": 4}, 0.20, options)
        assert penalty > 0
        assert "NEGATIVE" in details[0]


class TestScoreLanguageComponent:
    """Tests for _score_language_component()."""

    def test_matching_language_scores_positive(self):
        user_prefs = {"languages": Counter({"en": 10})}
        options = ScoringOptions()
        final, detail = _score_language_component("en", user_prefs, {"languages": 10}, 0.05, options)
        assert final > 0
        assert detail is not None

    def test_na_language_scores_zero(self):
        options = ScoringOptions()
        final, detail = _score_language_component("N/A", {"languages": Counter()}, {"languages": 1}, 0.05, options)
        assert (final, detail) == (0.0, None)

    def test_unseen_language_scores_zero(self):
        user_prefs = {"languages": Counter({"en": 10})}
        options = ScoringOptions()
        final, detail = _score_language_component("fr", user_prefs, {"languages": 10}, 0.05, options)
        assert (final, detail) == (0.0, None)


class TestScoreKeywordComponent:
    """Tests for _score_keyword_component()."""

    def test_matching_keyword_scores_positive(self):
        user_prefs = {"keywords": Counter({"superhero": 10})}
        options = ScoringOptions()
        threshold = _tfidf_threshold({}, "keywords", 10, options)
        final, penalty, details = _score_keyword_component(
            ["superhero"], user_prefs, {"keywords": 10}, 0.45, threshold, options
        )
        assert final > 0
        assert details

    def test_no_keywords_scores_zero(self):
        options = ScoringOptions()
        final, penalty, details = _score_keyword_component(
            [], {"keywords": Counter()}, {"keywords": 1}, 0.45, 0, options
        )
        assert (final, penalty, details) == (0.0, 0.0, [])

    def test_fuzzy_match_used_when_no_exact_match(self):
        user_prefs = {"keywords": Counter({"space opera": 10})}
        options = ScoringOptions(use_fuzzy_keywords=True)
        threshold = _tfidf_threshold({}, "keywords", 10, options)
        final, penalty, details = _score_keyword_component(
            ["space"], user_prefs, {"keywords": 10}, 0.45, threshold, options
        )
        assert final > 0

    def test_fuzzy_disabled_no_match(self):
        user_prefs = {"keywords": Counter({"space opera": 10})}
        options = ScoringOptions(use_fuzzy_keywords=False, use_tfidf=False)
        final, penalty, details = _score_keyword_component(["space"], user_prefs, {"keywords": 10}, 0.45, 0, options)
        assert final == 0.0


class TestApplyActiveWeightRedistribution:
    """Tests for _apply_active_weight_redistribution() - the per-item
    weight redistribution pass (distinct from _redistribute_weights)."""

    def test_redistributes_zero_scoring_dimension_weight(self):
        component_scores = {"genre": 0.2, "director": 0.0, "studio": 0.0, "actor": 0.0, "language": 0.0, "keyword": 0.0}
        effective_weights = {
            "genre": 0.25,
            "director": 0.15,
            "studio": 0,
            "actor": 0.20,
            "language": 0.05,
            "keyword": 0.45,
        }
        score = _apply_active_weight_redistribution(0.2, component_scores, effective_weights)
        assert score > 0.2

    def test_no_redistribution_when_all_active(self):
        component_scores = {"genre": 0.1, "director": 0.1, "studio": 0, "actor": 0.1, "language": 0.1, "keyword": 0.1}
        effective_weights = {"genre": 0.2, "director": 0.2, "studio": 0, "actor": 0.2, "language": 0.2, "keyword": 0.2}
        score = _apply_active_weight_redistribution(0.5, component_scores, effective_weights)
        assert score == pytest.approx(0.5)


class TestApplyPopularityDampeningHelper:
    """Tests for _apply_popularity_dampening() (see also TestPopularityDampening
    above, which exercises the same behavior through the public function)."""

    def test_disabled_returns_unchanged(self):
        options = ScoringOptions(use_popularity_dampening=False)
        score, dampening = _apply_popularity_dampening(0.8, {"vote_count": 1000000}, options)
        assert score == 0.8
        assert dampening is None

    def test_below_threshold_no_dampening(self):
        options = ScoringOptions(use_popularity_dampening=True, popularity_threshold=50000)
        score, dampening = _apply_popularity_dampening(0.8, {"vote_count": 1000}, options)
        assert score == 0.8
        assert dampening is None

    def test_above_threshold_dampens(self):
        options = ScoringOptions(use_popularity_dampening=True, popularity_threshold=50000)
        score, dampening = _apply_popularity_dampening(0.8, {"vote_count": 500000}, options)
        assert score < 0.8
        assert dampening is not None
