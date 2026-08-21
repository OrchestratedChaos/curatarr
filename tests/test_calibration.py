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
Tests for utils/calibration.py - calibrated recommendation re-ranking
(Steck, "Calibrated Recommendations", RecSys 2018).
"""

import math

import pytest

from utils.calibration import (
    CalibrationDimension,
    build_certificate_distribution,
    build_target_distribution,
    calibrate_multi,
    calibrate_recommendations,
    calibration_report,
    is_sufficiently_sampled,
    item_genre_distribution,
    kl_divergence,
    list_distribution,
    projected_distribution,
)
from utils.config import CALIBRATION_MIN_PROFILE_SAMPLE, CALIBRATION_SMOOTHING_ALPHA


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


class TestBuildCertificateDistribution:
    """build_certificate_distribution() - p(cert|u)."""

    def test_normalizes_counts(self):
        dist = build_certificate_distribution(["PG", "R", "R", "R"])
        assert dist["R"] == pytest.approx(0.75)
        assert dist["PG"] == pytest.approx(0.25)

    def test_missing_certificates_are_skipped_not_bucketed(self):
        """
        An unrated film is not evidence about audience. Giving "unknown"
        its own share would let a library with patchy metadata calibrate
        toward unknown.
        """
        dist = build_certificate_distribution(["PG", None, "", "PG"])
        assert dist == {"PG": pytest.approx(1.0)}

    def test_all_missing_returns_empty(self):
        assert build_certificate_distribution([None, "", None]) == {}

    def test_empty_input_returns_empty(self):
        assert build_certificate_distribution([]) == {}

    def test_whitespace_is_normalized(self):
        assert build_certificate_distribution([" PG ", "PG"]) == {"PG": pytest.approx(1.0)}


class TestCalibrateMulti:
    """
    calibrate_multi() - calibration across several attributes.

    Motivating measurement (real library): genre tags are unreliable for
    audience. `family` is attached to Frequency and Skyscraper; the
    live-action R.I.P.D. carries `animation`; Invisible Sister and
    Goosebumps 2 are kid films with no kid genre. The certificate splits
    the same set cleanly.
    """

    @staticmethod
    def _item(title, genres, cert, score):
        return {"title": title, "genres": genres, "cert": cert, "score": score}

    @staticmethod
    def _dims(genre_target, cert_target=None, genre_weight=1.0, cert_weight=1.0):
        dims = [CalibrationDimension("genre", genre_target, lambda i: i["genres"], genre_weight)]
        if cert_target:
            dims.append(
                CalibrationDimension(
                    "certificate", cert_target, lambda i: [i["cert"]] if i["cert"] else [], cert_weight
                )
            )
        return dims

    def _run(self, items, limit, dims, strength):
        return calibrate_multi(items, limit, lambda i: i["score"], dims, strength)

    def test_certificate_dimension_suppresses_kid_certificates(self):
        """The defect: a profile that is mostly PG-13/R, a pool that is
        mostly G/PG, and genre tags that cannot tell them apart."""
        # Genre is deliberately IDENTICAL across both groups, so genre
        # calibration alone is powerless and only the certificate can act.
        items = [self._item(f"kid{i}", ["adventure"], "PG", 0.60) for i in range(20)]
        items += [self._item(f"adult{i}", ["adventure"], "R", 0.55) for i in range(20)]
        genre_target = {"adventure": 1.0}
        cert_target = {"R": 0.9, "PG": 0.1}

        genre_only = self._run(items, 10, self._dims(genre_target), 0.5)
        with_cert = self._run(items, 10, self._dims(genre_target, cert_target), 0.5)

        kid = lambda sel: sum(1 for i in sel if i["cert"] == "PG")  # noqa: E731
        assert kid(genre_only) == 10, "genre alone should be powerless here"
        assert kid(with_cert) < kid(genre_only), "certificate dimension did not act"

    def test_does_not_eliminate_a_certificate_the_user_watches(self):
        """Calibration is not exclusion, on this axis either."""
        items = [self._item(f"pg{i}", ["a"], "PG", 0.3) for i in range(20)]
        items += [self._item(f"r{i}", ["a"], "R", 0.9) for i in range(20)]
        sel = self._run(items, 10, self._dims({"a": 1.0}, {"R": 0.7, "PG": 0.3}), 0.5)
        assert any(i["cert"] == "PG" for i in sel)

    def test_zero_weight_dimension_is_inert(self):
        items = [self._item(f"pg{i}", ["a"], "PG", 0.6) for i in range(20)]
        items += [self._item(f"r{i}", ["a"], "R", 0.5) for i in range(20)]
        dims = self._dims({"a": 1.0}, {"R": 0.99, "PG": 0.01}, cert_weight=0.0)
        sel = self._run(items, 10, dims, 0.5)
        assert all(i["cert"] == "PG" for i in sel), "a zero-weighted dimension must not influence selection"

    def test_disabled_strength_preserves_score_order(self):
        items = [self._item("a", ["x"], "R", 0.9), self._item("b", ["y"], "PG", 0.5)]
        sel = self._run(items, 2, self._dims({"x": 0.5, "y": 0.5}, {"R": 0.5, "PG": 0.5}), 0.0)
        assert [i["title"] for i in sel] == ["a", "b"]

    def test_no_active_dimensions_falls_back_to_score_order(self):
        items = [self._item("a", ["x"], "R", 0.9), self._item("b", ["y"], "PG", 0.5)]
        sel = self._run(items, 2, [CalibrationDimension("genre", {}, lambda i: i["genres"], 1.0)], 0.5)
        assert [i["title"] for i in sel] == ["a", "b"]

    def test_candidates_within_limit_untouched(self):
        items = [self._item("a", ["x"], "R", 0.9), self._item("b", ["y"], "PG", 0.5)]
        assert len(self._run(items, 5, self._dims({"x": 1.0}, {"R": 1.0}), 1.0)) == 2

    def test_respects_limit_and_no_duplicates(self):
        items = [self._item(str(i), ["x"], "R", 0.5) for i in range(20)]
        sel = self._run(items, 7, self._dims({"x": 1.0}, {"R": 1.0}), 0.5)
        assert len(sel) == 7
        assert len({id(i) for i in sel}) == 7

    def test_zero_limit_returns_empty(self):
        assert self._run([self._item("a", ["x"], "R", 0.9)], 0, self._dims({"x": 1.0}), 0.5) == []

    def test_items_with_no_certificate_do_not_crash(self):
        items = [self._item(f"n{i}", ["x"], None, 0.5) for i in range(10)]
        items += [self._item(f"r{i}", ["x"], "R", 0.4) for i in range(10)]
        sel = self._run(items, 5, self._dims({"x": 1.0}, {"R": 1.0}), 0.5)
        assert len(sel) == 5


class TestMinimumProfileSample:
    """
    Calibration must refuse a target it cannot trust.

    It reproduces its target faithfully, so an under-sampled target is a
    confidently WRONG one, not a weak one. Measured on a real server: one
    user had two watched shows, both TV-G. Calibrating that profile would
    have driven their entire collection to ~100% TV-G off a two-item
    sample. The profiles where calibration demonstrably works range from
    47 to 239 watched titles.
    """

    @staticmethod
    def _dim(sample):
        # Multi-category on purpose: a single-category target is rejected
        # independently of sample size (see test_degenerate_target_*).
        return CalibrationDimension("genre", {"a": 0.6, "b": 0.4}, lambda i: i["genres"], 1.0, sample)

    def test_unstated_sample_is_allowed(self):
        """Callers predating the check must be unaffected."""
        assert is_sufficiently_sampled(CalibrationDimension("g", {"a": 0.5, "b": 0.5}, lambda i: []))

    def test_degenerate_single_category_target_is_rejected(self):
        """
        Ruinous at ANY sample count: calibrating to a one-value target
        drives the whole collection onto that value. The real case was a
        user whose two watched shows were both TV-G.
        """
        assert not is_sufficiently_sampled(CalibrationDimension("c", {"TV-G": 1.0}, lambda i: [], 1.0, 2))
        assert not is_sufficiently_sampled(CalibrationDimension("c", {"R": 1.0}, lambda i: [], 1.0, 9999))

    def test_real_world_21_sample_certificate_target_is_allowed(self):
        """
        Regression: an earlier threshold of 25 blocked a 21-sample
        certificate profile, and that user's collection went from 14% to
        22% G/PG against a 9% profile because only genre calibration ran.
        21 noisy samples beat calibrating on an attribute that does not
        track audience at all.
        """
        target = {"G": 0.05, "PG": 0.05, "PG-13": 0.6, "R": 0.3}
        assert is_sufficiently_sampled(CalibrationDimension("certificate", target, lambda i: [], 1.0, 21))

    def test_tiny_sample_is_rejected(self):
        assert not is_sufficiently_sampled(self._dim(2))

    def test_boundary_is_inclusive(self):
        assert is_sufficiently_sampled(self._dim(CALIBRATION_MIN_PROFILE_SAMPLE))
        assert not is_sufficiently_sampled(self._dim(CALIBRATION_MIN_PROFILE_SAMPLE - 1))

    def test_ample_sample_is_allowed(self):
        assert is_sufficiently_sampled(self._dim(239))

    def test_undersampled_dimension_does_not_steer_selection(self):
        """
        The real hazard: a 2-item target that would otherwise be obeyed.
        With the guard, selection falls back to score order.
        """
        items = [{"title": f"kid{i}", "genres": ["family"], "score": 0.4} for i in range(20)]
        items += [{"title": f"adult{i}", "genres": ["thriller"], "score": 0.9} for i in range(20)]
        # A target overwhelmingly favouring family, from 2 samples.
        thin = CalibrationDimension("genre", {"family": 0.95, "thriller": 0.05}, lambda i: i["genres"], 1.0, 2)
        sel = calibrate_multi(items, 10, lambda i: i["score"], [thin], 0.5)
        assert all(i["genres"] == ["thriller"] for i in sel), "an untrustworthy target steered the collection"

    def test_well_sampled_dimension_still_steers(self):
        """The guard must not disable calibration for real profiles."""
        items = [{"title": f"kid{i}", "genres": ["family"], "score": 0.9} for i in range(20)]
        items += [{"title": f"adult{i}", "genres": ["thriller"], "score": 0.4} for i in range(20)]
        good = CalibrationDimension("genre", {"thriller": 0.95, "family": 0.05}, lambda i: i["genres"], 1.0, 200)
        sel = calibrate_multi(items, 10, lambda i: i["score"], [good], 0.5)
        family = sum(1 for i in sel if i["genres"] == ["family"])
        assert family < 10, "a well-sampled target was ignored"

    def test_mixed_sampling_keeps_only_the_trustworthy_dimension(self):
        items = [{"g": ["family"], "c": "G", "score": 0.5} for _ in range(20)]
        items += [{"g": ["thriller"], "c": "R", "score": 0.5} for _ in range(20)]
        # Both multi-category, so only the SAMPLE SIZE differs between
        # them - otherwise the degenerate-target check would reject both.
        good = CalibrationDimension("genre", {"thriller": 0.9, "family": 0.1}, lambda i: i["g"], 1.0, 200)
        thin = CalibrationDimension("certificate", {"G": 0.9, "R": 0.1}, lambda i: [i["c"]], 1.0, 2)
        sel = calibrate_multi(items, 10, lambda i: i["score"], [good, thin], 0.5)
        # Genre (trusted, wants thriller) must win; certificate (2 samples,
        # wants G) must be ignored entirely.
        assert sum(1 for i in sel if i["g"] == ["thriller"]) > 5


class TestProjectedDistribution:
    """projected_distribution() - unfilled slots held at target."""

    def test_full_list_is_returned_unchanged(self):
        actual = {"action": 0.7, "drama": 0.3}
        target = {"action": 0.5, "drama": 0.5}
        assert projected_distribution(actual, target, 4, 4) == actual

    def test_overfull_list_is_returned_unchanged(self):
        actual = {"action": 1.0}
        assert projected_distribution(actual, {"drama": 1.0}, 5, 4) == actual

    def test_non_positive_total_is_returned_unchanged(self):
        actual = {"action": 1.0}
        assert projected_distribution(actual, {"drama": 1.0}, 0, 0) == actual

    def test_blends_toward_target_by_filled_share(self):
        actual = {"action": 1.0}
        target = {"drama": 1.0}
        projected = projected_distribution(actual, target, 1, 4)
        assert projected["action"] == pytest.approx(0.25)
        assert projected["drama"] == pytest.approx(0.75)

    def test_stays_a_distribution(self):
        actual = {"action": 0.6, "comedy": 0.4}
        target = {"action": 0.2, "drama": 0.8}
        projected = projected_distribution(actual, target, 2, 5)
        assert sum(projected.values()) == pytest.approx(1.0)

    def test_early_pick_barely_moves_the_projection(self):
        """
        The whole point: one item out of fifty can only shift the mix by
        1/50, so its divergence penalty must be small enough that the
        similarity term still decides. Before this, pick 1 was scored on
        how well that single item reproduced the entire profile.
        """
        target = {"action": 0.5, "drama": 0.3, "comedy": 0.2}
        one_item = list_distribution([["action"]])
        projected = projected_distribution(one_item, target, 1, 50)
        assert kl_divergence(target, projected) < kl_divergence(target, one_item)


class TestGreedyDoesNotFavorBroadlyTaggedItems:
    """
    Regression: the pre-fix greedy scored each candidate on how much of
    the profile it covered BY ITSELF, so the most-tagged candidate won
    the early slots regardless of score. On this library that is a
    standing bias toward kid films, which carry 4.69 genre tags on
    average against 3.25 for everything else (see CLAUDE.md on genre
    tags being unreliable).
    """

    def test_high_scorer_beats_a_broadly_tagged_low_scorer(self):
        target = {"action": 0.4, "thriller": 0.3, "science fiction": 0.3}
        # Carries a tag for nearly every genre in the profile, but is a
        # poor match; the pre-fix objective picked this first.
        broad = _item("Broad", ["action", "thriller", "science fiction", "family", "animation"], 0.05)
        strong = _item("Strong", ["action", "thriller"], 0.90)
        filler = [_item(f"Filler {i}", ["action"], 0.10) for i in range(10)]

        selected = _calibrate([broad, strong] + filler, 4, target, 0.5)

        assert selected[0]["title"] == "Strong"

    def test_still_selects_for_the_profile_mix(self):
        """The fix must not turn calibration back into plain top-N."""
        target = {"action": 0.5, "romance": 0.5}
        action = [_item(f"Action {i}", ["action"], 0.9 - i * 0.01) for i in range(10)]
        romance = [_item(f"Romance {i}", ["romance"], 0.2 - i * 0.01) for i in range(10)]

        selected = _calibrate(action + romance, 6, target, 0.5)

        genres = [i["genres"][0] for i in selected]
        assert genres.count("romance") >= 2, genres
