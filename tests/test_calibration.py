"""
Tests for utils/calibration.py - calibrated recommendation re-ranking
(Steck, "Calibrated Recommendations", RecSys 2018).
"""

import math

import pytest

from utils.calibration import (
    build_target_distribution,
    calibrate_recommendations,
    calibration_report,
    item_genre_distribution,
    kl_divergence,
    list_distribution,
)
from utils.config import CALIBRATION_SMOOTHING_ALPHA


def _item(title, genres, score):
    return {"title": title, "genres": genres, "score": score}


def _calibrate(items, limit, target, strength):
    return calibrate_recommendations(
        items,
        limit,
        get_genres=lambda i: i["genres"],
        get_score=lambda i: i["score"],
        target_distribution=target,
        calibration_strength=strength,
    )


class TestBuildTargetDistribution:
    """build_target_distribution() - profile counter -> p(g|u)."""

    def test_normalizes_to_one(self):
        dist = build_target_distribution({"action": 30.0, "drama": 10.0})
        assert dist["action"] == pytest.approx(0.75)
        assert dist["drama"] == pytest.approx(0.25)
        assert sum(dist.values()) == pytest.approx(1.0)

    def test_preserves_weighting_not_just_presence(self):
        """
        The counter values are recency/rating-weighted, so a heavily
        watched genre must carry proportionally more target mass - not
        be flattened to "genres the user has seen".
        """
        dist = build_target_distribution({"thriller": 38.45, "family": 6.91})
        assert dist["thriller"] > 5 * dist["family"]

    def test_empty_profile_returns_empty(self):
        assert build_target_distribution({}) == {}

    def test_zero_mass_profile_returns_empty(self):
        """A counter of all zeros has no distribution to speak of."""
        assert build_target_distribution({"action": 0.0}) == {}

    def test_negative_counts_excluded(self):
        """Negative counters are explicit dislike signals, not target mass."""
        dist = build_target_distribution({"action": 10.0, "horror": -5.0})
        assert "horror" not in dist
        assert dist["action"] == pytest.approx(1.0)


class TestItemGenreDistribution:
    """item_genre_distribution() - p(g|i)."""

    def test_splits_mass_evenly(self):
        dist = item_genre_distribution(["action", "comedy", "family"])
        assert all(v == pytest.approx(1 / 3) for v in dist.values())
        assert sum(dist.values()) == pytest.approx(1.0)

    def test_broadly_tagged_item_contributes_less_per_genre(self):
        """
        The library's kid titles carry ~5.2 genre tags against ~3.3 for
        everything else. Splitting rather than counting each tag at full
        weight is what stops that breadth from inflating their
        contribution to the list distribution.
        """
        narrow = item_genre_distribution(["thriller", "drama"])
        broad = item_genre_distribution(["comedy", "family", "animation", "adventure", "fantasy", "music", "musical"])
        assert narrow["thriller"] > broad["family"]

    def test_empty_genres_returns_empty(self):
        assert item_genre_distribution([]) == {}


class TestListDistribution:
    """list_distribution() - q(g)."""

    def test_averages_item_distributions(self):
        dist = list_distribution([["action"], ["drama"]])
        assert dist["action"] == pytest.approx(0.5)
        assert dist["drama"] == pytest.approx(0.5)

    def test_sums_to_one(self):
        dist = list_distribution([["a", "b"], ["b", "c"], ["a"]])
        assert sum(dist.values()) == pytest.approx(1.0)

    def test_empty_list_returns_empty(self):
        assert list_distribution([]) == {}


class TestKlDivergence:
    """kl_divergence() with Steck's smoothing."""

    def test_identical_distributions_are_zero(self):
        p = {"action": 0.5, "drama": 0.5}
        assert kl_divergence(p, p) == pytest.approx(0.0, abs=1e-9)

    def test_is_non_negative(self):
        p = {"action": 0.7, "drama": 0.3}
        q = {"action": 0.2, "drama": 0.8}
        assert kl_divergence(p, q) >= 0

    def test_missing_genre_is_finite_not_infinite(self):
        """
        The case that matters most: a genre the user watches that the
        list omits entirely. Without smoothing this is log(p/0) = inf,
        which would make every incomplete list equally (in)comparable.
        """
        divergence = kl_divergence({"action": 0.5, "drama": 0.5}, {"action": 1.0})
        assert math.isfinite(divergence)
        assert divergence > 0

    def test_larger_mismatch_diverges_more(self):
        p = {"action": 0.9, "family": 0.1}
        close = kl_divergence(p, {"action": 0.8, "family": 0.2})
        far = kl_divergence(p, {"action": 0.2, "family": 0.8})
        assert far > close

    def test_smoothing_alpha_is_applied(self):
        """alpha interpolates the actual distribution toward the target."""
        p = {"action": 1.0}
        unsmoothed_q = {"action": 0.0}
        divergence = kl_divergence(p, unsmoothed_q, alpha=CALIBRATION_SMOOTHING_ALPHA)
        assert divergence == pytest.approx(-math.log(CALIBRATION_SMOOTHING_ALPHA))


class TestCalibrateRecommendations:
    """calibrate_recommendations() - the greedy selection."""

    def test_disabled_strength_preserves_score_order(self):
        items = [_item("a", ["action"], 0.9), _item("b", ["drama"], 0.5)]
        result = _calibrate(items, 2, {"action": 0.5, "drama": 0.5}, 0.0)
        assert [i["title"] for i in result] == ["a", "b"]

    def test_empty_target_falls_back_to_score_order(self):
        """Cold start: no profile means nothing to calibrate against."""
        items = [_item("a", ["action"], 0.9), _item("b", ["drama"], 0.5)]
        result = _calibrate(items, 2, {}, 0.5)
        assert [i["title"] for i in result] == ["a", "b"]

    def test_candidates_fitting_within_limit_are_untouched(self):
        """Nothing to trade off when everything is selected anyway."""
        items = [_item("a", ["action"], 0.9), _item("b", ["family"], 0.8)]
        result = _calibrate(items, 5, {"action": 0.99, "family": 0.01}, 1.0)
        assert len(result) == 2

    def test_respects_limit(self):
        items = [_item(str(i), ["action"], 0.5) for i in range(20)]
        assert len(_calibrate(items, 7, {"action": 1.0}, 0.5)) == 7

    def test_zero_limit_returns_empty(self):
        items = [_item("a", ["action"], 0.9)]
        assert _calibrate(items, 0, {"action": 1.0}, 0.5) == []

    def test_empty_candidates_returns_empty(self):
        assert _calibrate([], 10, {"action": 1.0}, 0.5) == []

    def test_suppresses_genre_overrepresented_relative_to_profile(self):
        """
        The reported defect: a profile that is mostly thriller, and a
        candidate pool that is mostly family because the user has
        already watched the thrillers, previously yielded a
        mostly-family collection. Calibration must pull the family
        share back down toward the profile's.
        """
        target = {"thriller": 0.95, "family": 0.05}
        items = [_item(f"fam{i}", ["family"], 0.60) for i in range(20)]
        items += [_item(f"thr{i}", ["thriller"], 0.55) for i in range(20)]

        uncalibrated = _calibrate(items, 10, target, 0.0)
        calibrated = _calibrate(items, 10, target, 0.5)

        def family_count(sel):
            return sum(1 for i in sel if "family" in i["genres"])

        # Score order alone hands back an all-family list.
        assert family_count(uncalibrated) == 10
        assert family_count(calibrated) < family_count(uncalibrated)

    def test_does_not_eliminate_a_genre_the_user_actually_watches(self):
        """
        Calibration is not exclusion - this is the whole reason to
        prefer it. A genre with real profile mass must still appear,
        just at its proper rate.
        """
        target = {"thriller": 0.8, "family": 0.2}
        items = [_item(f"fam{i}", ["family"], 0.30) for i in range(20)]
        items += [_item(f"thr{i}", ["thriller"], 0.90) for i in range(20)]

        calibrated = _calibrate(items, 10, target, 0.5)
        family = [i for i in calibrated if "family" in i["genres"]]
        assert len(family) > 0, "calibration eliminated a genre the profile contains"

    def test_keeps_the_best_examples_of_a_downweighted_genre(self):
        """
        When calibration trims a genre it should keep that genre's
        highest-scoring titles - "the right ones", not an arbitrary
        slice.
        """
        target = {"thriller": 0.9, "family": 0.1}
        items = [_item("good-family", ["family"], 0.90)]
        items += [_item(f"weak-family{i}", ["family"], 0.20) for i in range(10)]
        items += [_item(f"thr{i}", ["thriller"], 0.50) for i in range(10)]

        calibrated = _calibrate(items, 6, target, 0.5)
        family = [i["title"] for i in calibrated if "family" in i["genres"]]
        assert "good-family" in family

    def test_higher_strength_calibrates_more_closely(self):
        target = {"thriller": 0.9, "family": 0.1}
        items = [_item(f"fam{i}", ["family"], 0.80) for i in range(20)]
        items += [_item(f"thr{i}", ["thriller"], 0.50) for i in range(20)]

        def divergence_at(strength):
            sel = _calibrate(items, 10, target, strength)
            return kl_divergence(target, list_distribution([i["genres"] for i in sel]))

        assert divergence_at(0.5) < divergence_at(0.0)

    def test_returns_only_original_candidate_objects(self):
        """No copying/wrapping - callers rely on identity downstream."""
        items = [_item("a", ["action"], 0.9), _item("b", ["family"], 0.8)]
        result = _calibrate(items, 1, {"action": 0.9, "family": 0.1}, 0.5)
        assert result[0] is items[0] or result[0] is items[1]

    def test_no_duplicate_selections(self):
        items = [_item(str(i), ["action", "drama"], 0.5) for i in range(10)]
        result = _calibrate(items, 5, {"action": 0.5, "drama": 0.5}, 0.5)
        assert len({id(i) for i in result}) == 5


class TestCalibrationReport:
    """calibration_report() - the logging comparison."""

    def test_reports_worst_divergence_first(self):
        target = {"action": 0.5, "drama": 0.4, "family": 0.1}
        rows = calibration_report(target, [["family"], ["family"]], top_n=3)
        assert rows[0][0] == "family"

    def test_returns_target_and_actual_shares(self):
        rows = calibration_report({"action": 1.0}, [["action"]], top_n=1)
        genre, target_share, actual_share = rows[0]
        assert genre == "action"
        assert target_share == pytest.approx(1.0)
        assert actual_share == pytest.approx(1.0)

    def test_respects_top_n(self):
        target = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}
        assert len(calibration_report(target, [["a"]], top_n=2)) == 2

    def test_includes_genres_absent_from_the_list(self):
        """A genre the collection omits entirely is exactly what to surface."""
        rows = calibration_report({"action": 0.5, "drama": 0.5}, [["action"]], top_n=5)
        assert "drama" in {r[0] for r in rows}
