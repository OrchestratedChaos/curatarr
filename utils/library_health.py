"""
Library supply health - measuring when ranking has run out of material.

A recommender's quality is bounded by its candidate pool long before it
is bounded by its scoring. A large streaming catalog is effectively
inexhaustible per user - a subscriber has seen a fraction of a percent of
it - so ranking is the binding constraint and that is where the
engineering goes. A personal library is the opposite: a long-standing
user can have watched most of it, at which point no amount of scoring or
calibration produces good recommendations, because there is nothing good
left to recommend.

The reference case: a user with 233 of 334 movies watched, leaving 127
candidates to fill a 50-item collection. 38% of the survivors were
family/animation - not because the scorer preferred them (measured bias:
1.01x, i.e. none) but because they were what remained after fifteen years
of watching everything else. The collection faithfully reported the
composition of an exhausted shelf.

This module makes that condition visible and, more usefully,
actionable:

  - `assess_pool_health` answers "is ranking still the constraint here?"
  - `find_supply_gaps` answers "which genres does this user want that
    the library can no longer supply?"

The second is what closes the loop. External/Radarr discovery searches
by a profile's TOP genres, which fetches more of what the library is
already thickest in. Feeding it the gap list instead targets acquisition
at the genres the user demonstrably wants and the shelf cannot serve.
"""

from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Sequence

from utils.config import (
    POOL_DEPLETION_RATIO,
    SUPPLY_GAP_MIN_PROFILE_SHARE,
    SUPPLY_GAP_MIN_SHORTFALL,
)


class PoolHealth(NamedTuple):
    """Candidate supply relative to what the collection needs."""

    candidates: int
    target: int
    ratio: float
    depleted: bool

    def summary(self) -> str:
        state = "DEPLETED" if self.depleted else "healthy"
        return f"{self.candidates} candidates for {self.target} slots ({self.ratio:.1f}:1) - {state}"


class SupplyGap(NamedTuple):
    """A genre the profile wants more of than the library can supply."""

    genre: str
    profile_share: float
    available_share: float

    @property
    def shortfall(self) -> float:
        return self.profile_share - self.available_share


def assess_pool_health(candidate_count: int, target_count: int) -> PoolHealth:
    """
    Compare available candidates against collection size.

    A pool below POOL_DEPLETION_RATIO times the target leaves the
    selection stage almost no discretion - at 1:1 the "top N" is simply
    "all of them", and quality filters or calibration downstream have
    nothing to choose between.
    """
    if target_count <= 0:
        return PoolHealth(candidate_count, target_count, 0.0, False)
    ratio = candidate_count / target_count
    return PoolHealth(candidate_count, target_count, ratio, ratio < POOL_DEPLETION_RATIO)


def find_supply_gaps(
    target_distribution: Mapping[str, float],
    candidate_genre_lists: Iterable[Sequence[str]],
    min_profile_share: float = SUPPLY_GAP_MIN_PROFILE_SHARE,
    min_shortfall: float = SUPPLY_GAP_MIN_SHORTFALL,
) -> List[SupplyGap]:
    """
    Find genres the user watches more of than the unwatched pool offers.

    Both sides are genre-mass distributions (see utils/calibration.py's
    list_distribution) rather than title counts, so the comparison is
    like-for-like with how calibration measures the collection.

    Genres below `min_profile_share` are ignored - a genre the user
    barely watches is not a supply problem worth acting on - as are
    shortfalls below `min_shortfall`, which are noise.

    Returns gaps worst-first, i.e. the acquisition priority order.
    """
    # Local import: utils/__init__ imports both modules, and importing
    # calibration at module scope here would order-depend on which lands
    # first during package init.
    from utils.calibration import list_distribution

    available = list_distribution(candidate_genre_lists)

    gaps = []
    for genre, wanted in target_distribution.items():
        if wanted < min_profile_share:
            continue
        supplied = available.get(genre, 0.0)
        if wanted - supplied >= min_shortfall:
            gaps.append(SupplyGap(genre, wanted, supplied))

    gaps.sort(key=lambda g: g.shortfall, reverse=True)
    return gaps


def prioritize_discovery_genres(
    gaps: Sequence[SupplyGap],
    profile_genres: Mapping[str, float],
    limit: int = 10,
) -> List[str]:
    """
    Build the genre list external discovery should actually search.

    Gap genres lead, in shortfall order - those are the ones worth
    acquiring. The profile's own top genres follow as filler so a healthy
    library (no gaps) degrades to exactly the previous behavior rather
    than to an empty search.
    """
    ordered = [gap.genre for gap in gaps]
    seen = set(ordered)
    for genre, _count in sorted(profile_genres.items(), key=lambda kv: kv[1], reverse=True):
        if genre not in seen:
            ordered.append(genre)
            seen.add(genre)
    return ordered[:limit]


def format_health_report(health: PoolHealth, gaps: Sequence[SupplyGap], media_key: str = "movies") -> List[str]:
    """Render the human-readable lines the recommender logs."""
    lines = [f"Library supply: {health.summary()}"]
    if health.depleted:
        lines.append(
            f"  This profile has watched most of the available {media_key}. Scoring and "
            f"calibration cannot improve on an exhausted pool - new content is the fix."
        )
    if gaps:
        lines.append("  Under-supplied genres (profile wants / library offers):")
        for gap in gaps[:5]:
            lines.append(f"    {gap.genre:<20} {gap.profile_share * 100:5.1f}% / {gap.available_share * 100:5.1f}%")
    return lines


def gaps_to_dict(gaps: Sequence[SupplyGap]) -> List[Dict[str, Any]]:
    """Serialize gaps for run-status/metrics consumers."""
    return [
        {
            "genre": gap.genre,
            "profile_share": round(gap.profile_share, 4),
            "available_share": round(gap.available_share, 4),
            "shortfall": round(gap.shortfall, 4),
        }
        for gap in gaps
    ]
