"""
Tests for utils/ignored_recs.py - negative feedback from recommendations
that were shown and never watched.
"""

from datetime import datetime, timedelta

from utils.config import IGNORED_REC_MAX_PROFILE_FRACTION, IGNORED_REC_MIN_DAYS_SHOWN
from utils.ignored_recs import apply_ignored_penalties, find_ignored_recommendations

NOW = datetime(2026, 7, 31, 12, 0, 0)
LABEL = "Recommended_alice"


def _days_ago(n):
    return (NOW - timedelta(days=n)).isoformat()


class TestFindIgnoredRecommendations:
    def test_finds_item_shown_long_enough_and_unwatched(self):
        dates = {f"1_{LABEL}": _days_ago(IGNORED_REC_MIN_DAYS_SHOWN + 1)}
        assert find_ignored_recommendations(dates, LABEL, set(), now=NOW) == [(1, IGNORED_REC_MIN_DAYS_SHOWN + 1)]

    def test_ignores_item_not_shown_long_enough(self):
        dates = {f"1_{LABEL}": _days_ago(IGNORED_REC_MIN_DAYS_SHOWN - 1)}
        assert find_ignored_recommendations(dates, LABEL, set(), now=NOW) == []

    def test_boundary_day_counts_as_ignored(self):
        dates = {f"1_{LABEL}": _days_ago(IGNORED_REC_MIN_DAYS_SHOWN)}
        assert len(find_ignored_recommendations(dates, LABEL, set(), now=NOW)) == 1

    def test_watched_item_is_not_ignored(self):
        """Watching it is the opposite signal - the recommendation worked."""
        dates = {f"1_{LABEL}": _days_ago(90)}
        assert find_ignored_recommendations(dates, LABEL, {1}, now=NOW) == []

    def test_other_users_labels_are_not_counted(self):
        """
        label_dates is shared across users; charging one profile for
        another's untouched recommendations would be flatly wrong.
        """
        dates = {"1_Recommended_bob": _days_ago(90)}
        assert find_ignored_recommendations(dates, LABEL, set(), now=NOW) == []

    def test_malformed_timestamp_is_skipped_not_guessed(self):
        dates = {f"1_{LABEL}": "not-a-date"}
        assert find_ignored_recommendations(dates, LABEL, set(), now=NOW) == []

    def test_non_numeric_rating_key_is_skipped(self):
        dates = {f"abc_{LABEL}": _days_ago(90)}
        assert find_ignored_recommendations(dates, LABEL, set(), now=NOW) == []

    def test_sorted_longest_shown_first(self):
        dates = {
            f"1_{LABEL}": _days_ago(30),
            f"2_{LABEL}": _days_ago(90),
            f"3_{LABEL}": _days_ago(60),
        }
        assert [rk for rk, _ in find_ignored_recommendations(dates, LABEL, set(), now=NOW)] == [2, 3, 1]

    def test_empty_label_dates(self):
        assert find_ignored_recommendations({}, LABEL, set(), now=NOW) == []

    def test_custom_min_days_is_respected(self):
        dates = {f"1_{LABEL}": _days_ago(5)}
        assert len(find_ignored_recommendations(dates, LABEL, set(), now=NOW, min_days_shown=3)) == 1


class TestApplyIgnoredPenalties:
    def test_drives_genre_counter_downward(self):
        counters = {"genres": {"family": 10.0}, "tmdb_keywords": {}}
        apply_ignored_penalties(counters, [{"genres": ["family"]}], penalty=0.5)
        assert counters["genres"]["family"] < 10.0

    def test_can_push_an_unseen_genre_negative(self):
        """
        Negative counts are what utils/scoring.py's existing
        `genre_count < 0` branch consumes - the branch that had no
        producer before this module.
        """
        counters = {"genres": {"action": 10.0}, "tmdb_keywords": {}}
        apply_ignored_penalties(counters, [{"genres": ["polka"]}] * 5, penalty=0.5)
        assert counters["genres"]["polka"] < 0

    def test_penalty_is_split_across_an_items_terms(self):
        """
        A 7-genre title must not deliver 7x the punishment of a 2-genre
        one for the same single act of being ignored.
        """
        narrow = {"genres": {"a": 10.0, "b": 10.0}, "tmdb_keywords": {}}
        broad = {"genres": {"a": 10.0, "b": 10.0}, "tmdb_keywords": {}}
        apply_ignored_penalties(narrow, [{"genres": ["a", "b"]}], penalty=1.0)
        apply_ignored_penalties(broad, [{"genres": ["a", "b", "c", "d", "e", "f", "g"]}], penalty=1.0)
        assert narrow["genres"]["a"] < broad["genres"]["a"]

    def test_floor_prevents_unrecoverable_burial(self):
        """
        Without a floor, a long run of ignored recommendations could bury
        a genre so deep the profile could never recover if taste swung
        back.
        """
        counters = {"genres": {"action": 100.0, "family": 10.0}, "tmdb_keywords": {}}
        apply_ignored_penalties(counters, [{"genres": ["family"]}] * 500, penalty=1.0)
        floor = -(100.0 * IGNORED_REC_MAX_PROFILE_FRACTION)
        assert counters["genres"]["family"] >= floor

    def test_penalizes_keywords_too(self):
        counters = {"genres": {}, "tmdb_keywords": {"sequel": 5.0}}
        apply_ignored_penalties(counters, [{"tmdb_keywords": ["sequel"]}], penalty=0.5)
        assert counters["tmdb_keywords"]["sequel"] < 5.0

    def test_reports_how_many_terms_were_penalized(self):
        counters = {"genres": {"a": 1.0}, "tmdb_keywords": {}}
        applied = apply_ignored_penalties(counters, [{"genres": ["x", "y"]}], penalty=0.5)
        assert applied["genres"] == 2

    def test_no_ignored_items_is_a_noop(self):
        counters = {"genres": {"action": 10.0}, "tmdb_keywords": {}}
        apply_ignored_penalties(counters, [], penalty=0.5)
        assert counters["genres"]["action"] == 10.0

    def test_item_with_no_terms_is_skipped(self):
        counters = {"genres": {"action": 10.0}, "tmdb_keywords": {}}
        apply_ignored_penalties(counters, [{"genres": []}], penalty=0.5)
        assert counters["genres"]["action"] == 10.0

    def test_missing_dimension_does_not_raise(self):
        counters = {"genres": {"action": 10.0}}
        apply_ignored_penalties(counters, [{"tmdb_keywords": ["x"]}], penalty=0.5)
        assert counters["genres"]["action"] == 10.0

    def test_repeated_ignores_of_same_genre_compound(self):
        once = {"genres": {"family": 10.0}, "tmdb_keywords": {}}
        many = {"genres": {"family": 10.0}, "tmdb_keywords": {}}
        apply_ignored_penalties(once, [{"genres": ["family"]}], penalty=0.5)
        apply_ignored_penalties(many, [{"genres": ["family"]}] * 5, penalty=0.5)
        assert many["genres"]["family"] < once["genres"]["family"]
