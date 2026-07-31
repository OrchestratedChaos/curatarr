"""
Tests for utils/library_health.py - candidate supply measurement.
"""

import pytest

from utils.config import POOL_DEPLETION_RATIO
from utils.library_health import (
    assess_pool_health,
    find_supply_gaps,
    format_health_report,
    gaps_to_dict,
    prioritize_discovery_genres,
)


class TestAssessPoolHealth:
    def test_healthy_pool_is_not_flagged(self):
        health = assess_pool_health(500, 50)
        assert health.ratio == 10.0
        assert not health.depleted

    def test_depleted_pool_is_flagged(self):
        """The reported case: 127 candidates for a 50-item collection."""
        health = assess_pool_health(127, 50)
        assert health.ratio == pytest.approx(2.54)
        assert health.depleted

    def test_boundary_ratio_is_healthy(self):
        health = assess_pool_health(int(50 * POOL_DEPLETION_RATIO), 50)
        assert not health.depleted

    def test_zero_target_does_not_divide_by_zero(self):
        health = assess_pool_health(10, 0)
        assert health.ratio == 0.0
        assert not health.depleted

    def test_empty_pool(self):
        assert assess_pool_health(0, 50).depleted

    def test_summary_mentions_state(self):
        assert "DEPLETED" in assess_pool_health(10, 50).summary()
        assert "healthy" in assess_pool_health(500, 50).summary()


class TestFindSupplyGaps:
    def test_finds_genre_the_library_cannot_supply(self):
        """Profile wants thriller; the unwatched pool is all comedy."""
        target = {"thriller": 0.6, "comedy": 0.4}
        gaps = find_supply_gaps(target, [["comedy"], ["comedy"], ["comedy"]])
        assert [g.genre for g in gaps] == ["thriller"]

    def test_no_gap_when_supply_matches_demand(self):
        target = {"thriller": 0.5, "comedy": 0.5}
        gaps = find_supply_gaps(target, [["thriller"], ["comedy"]])
        assert gaps == []

    def test_ignores_genres_the_user_barely_watches(self):
        """A 0.5%-of-profile genre is not a supply problem worth acting on."""
        target = {"thriller": 0.995, "polka": 0.005}
        gaps = find_supply_gaps(target, [["thriller"]])
        assert all(g.genre != "polka" for g in gaps)

    def test_ignores_shortfalls_below_noise_threshold(self):
        target = {"thriller": 0.50, "comedy": 0.50}
        gaps = find_supply_gaps(target, [["thriller"], ["thriller"], ["comedy"]], min_shortfall=0.30)
        assert gaps == []

    def test_sorted_worst_shortfall_first(self):
        target = {"thriller": 0.5, "scifi": 0.3, "comedy": 0.2}
        gaps = find_supply_gaps(target, [["comedy"]])
        assert [g.genre for g in gaps] == ["thriller", "scifi"]

    def test_shortfall_is_demand_minus_supply(self):
        gaps = find_supply_gaps({"thriller": 0.8, "comedy": 0.2}, [["comedy"]])
        assert gaps[0].shortfall == pytest.approx(0.8)

    def test_empty_target_yields_no_gaps(self):
        assert find_supply_gaps({}, [["comedy"]]) == []

    def test_empty_pool_makes_every_wanted_genre_a_gap(self):
        gaps = find_supply_gaps({"thriller": 0.6, "comedy": 0.4}, [])
        assert {g.genre for g in gaps} == {"thriller", "comedy"}


class TestPrioritizeDiscoveryGenres:
    def test_gap_genres_lead(self):
        """
        The actual fix: acquisition must target what is missing, not
        fetch more of what the library already holds most of.
        """
        gaps = find_supply_gaps({"thriller": 0.7, "comedy": 0.3}, [["comedy"]])
        ordered = prioritize_discovery_genres(gaps, {"comedy": 100.0, "thriller": 5.0})
        assert ordered[0] == "thriller"

    def test_profile_genres_follow_as_filler(self):
        gaps = find_supply_gaps({"thriller": 0.7, "comedy": 0.3}, [["comedy"]])
        ordered = prioritize_discovery_genres(gaps, {"comedy": 100.0, "thriller": 5.0})
        assert "comedy" in ordered

    def test_no_gaps_degrades_to_top_genre_order(self):
        """A healthy library must behave exactly as it did before."""
        ordered = prioritize_discovery_genres([], {"comedy": 100.0, "thriller": 50.0, "drama": 10.0})
        assert ordered == ["comedy", "thriller", "drama"]

    def test_no_duplicates(self):
        gaps = find_supply_gaps({"thriller": 0.7, "comedy": 0.3}, [["comedy"]])
        ordered = prioritize_discovery_genres(gaps, {"thriller": 100.0, "comedy": 50.0})
        assert len(ordered) == len(set(ordered))

    def test_respects_limit(self):
        profile = {f"g{i}": float(i) for i in range(30)}
        assert len(prioritize_discovery_genres([], profile, limit=5)) == 5


class TestFormatHealthReport:
    def test_healthy_report_omits_the_warning(self):
        lines = format_health_report(assess_pool_health(500, 50), [])
        assert not any("exhausted" in ln for ln in lines)

    def test_depleted_report_says_new_content_is_the_fix(self):
        lines = format_health_report(assess_pool_health(60, 50), [])
        assert any("new content is the fix" in ln for ln in lines)

    def test_gaps_are_listed(self):
        gaps = find_supply_gaps({"thriller": 0.8, "comedy": 0.2}, [["comedy"]])
        lines = format_health_report(assess_pool_health(60, 50), gaps)
        assert any("thriller" in ln for ln in lines)


class TestGapsToDict:
    def test_serializes_expected_fields(self):
        gaps = find_supply_gaps({"thriller": 0.8, "comedy": 0.2}, [["comedy"]])
        row = gaps_to_dict(gaps)[0]
        assert set(row) == {"genre", "profile_share", "available_share", "shortfall"}
        assert row["genre"] == "thriller"

    def test_empty_gaps(self):
        assert gaps_to_dict([]) == []
