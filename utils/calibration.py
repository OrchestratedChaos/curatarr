"""
Calibrated recommendation re-ranking.

Implements the calibration method from Harald Steck, "Calibrated
Recommendations" (Netflix, RecSys 2018).

The problem it solves: ranking candidates purely by similarity score does
NOT preserve the user's taste distribution. Greedy top-N lets whatever
happens to be abundant in the candidate pool dominate the final list,
regardless of how much of the user's actual watch history that genre
represents.

Both failure directions are the same defect - the list's genre mix has
drifted from the profile's:

  - Steck's example (minority interest erased): a user who watches 70%
    action / 30% romance gets a list that is 100% action.
  - The inverse (minority interest amplified): a user whose profile is
    3.8% family, recommended from a pool that is 38% family because
    they have already watched most of the non-family library, gets a
    list that is 38% family.

The fix in both cases is to score a candidate list on two axes at once -
how well its items match the user, and how closely its genre distribution
matches the user's - and greedily build the list that maximizes:

    (1 - lambda) * sum(similarity) - lambda * KL(target || list)

`lambda` (calibration_strength) trades relevance against calibration.
0.0 reproduces plain top-N by score; higher values enforce the taste
distribution more aggressively.

Note this is deliberately NOT a genre exclusion. A user who genuinely
watches some family content still gets family content - just at the rate
they actually watch it, and the highest-scoring examples of it.
"""

import math
from typing import Callable, Dict, Iterable, List, Sequence, Tuple, TypeVar

from utils.config import (
    CALIBRATION_DIVERGENCE_SCALE,
    CALIBRATION_SMOOTHING_ALPHA,
    DEFAULT_CALIBRATION_STRENGTH,
)

T = TypeVar("T")


def build_target_distribution(genre_counter: Dict[str, float]) -> Dict[str, float]:
    """
    Normalize a user's weighted genre counter into a probability
    distribution p(g|u) - the share of the user's watch history that
    each genre represents.

    The counter values are the recency/rating-weighted counts that
    utils/counters.py already produces, so this deliberately preserves
    those weights rather than counting raw titles: a genre the user
    watched heavily last month should carry more target mass than one
    they watched once in 2019.

    Returns an empty dict for an empty/zero-mass profile, which callers
    treat as "no calibration possible" (see calibrate_recommendations).
    """
    if not genre_counter:
        return {}

    total = sum(v for v in genre_counter.values() if v > 0)
    if total <= 0:
        return {}

    return {g: v / total for g, v in genre_counter.items() if v > 0}


def item_genre_distribution(genres: Sequence[str]) -> Dict[str, float]:
    """
    p(g|i) for a single item: its genre mass split evenly across the
    genres it carries.

    Splitting rather than assigning 1.0 per genre matters here - the
    library's kid-oriented titles carry markedly more genre tags than
    everything else (5.19 vs 3.25 on the reference library), so counting
    each tag at full weight would let broadly-tagged items contribute
    outsized mass to the list distribution and distort the KL term.
    """
    if not genres:
        return {}
    share = 1.0 / len(genres)
    return {g: share for g in genres}


def list_distribution(genre_lists: Iterable[Sequence[str]]) -> Dict[str, float]:
    """
    q(g) for a candidate list: the mean of its items' genre
    distributions.
    """
    dist: Dict[str, float] = {}
    count = 0
    for genres in genre_lists:
        count += 1
        for g, share in item_genre_distribution(genres).items():
            dist[g] = dist.get(g, 0.0) + share
    if count == 0:
        return {}
    return {g: v / count for g, v in dist.items()}


def kl_divergence(
    target: Dict[str, float],
    actual: Dict[str, float],
    alpha: float = CALIBRATION_SMOOTHING_ALPHA,
) -> float:
    """
    KL(target || actual) with Steck's smoothing.

    KL is undefined where actual(g) == 0 but target(g) > 0, which is
    exactly the case that matters most (a genre the user likes that the
    list omits entirely). Steck's fix is to interpolate the actual
    distribution toward the target:

        actual~(g) = (1 - alpha) * actual(g) + alpha * target(g)

    so a missing genre yields a large but finite penalty rather than an
    infinity that would make every incomplete list incomparable.
    """
    divergence = 0.0
    for g, p in target.items():
        if p <= 0:
            continue
        q = (1 - alpha) * actual.get(g, 0.0) + alpha * p
        if q <= 0:
            continue
        divergence += p * math.log(p / q)
    return divergence


def calibrate_recommendations(
    candidates: Sequence[T],
    limit: int,
    get_genres: Callable[[T], Sequence[str]],
    get_score: Callable[[T], float],
    target_distribution: Dict[str, float],
    calibration_strength: float = DEFAULT_CALIBRATION_STRENGTH,
) -> List[T]:
    """
    Greedily select up to `limit` items maximizing

        (1 - s) * sum(score) - s * SCALE * KL(target || list)

    Steck shows this greedy construction is a (1 - 1/e) approximation of
    the optimal calibrated list, because the objective is submodular and
    monotone. In practice the greedy list is close enough that the
    optimal version is not worth the combinatorial cost.

    At each step every already-selected item contributes the same
    sum(score) to all candidates under consideration, so the comparison
    reduces to the marginal term:

        (1 - s) * score(candidate) - s * SCALE * KL(list + candidate)

    SCALE is CALIBRATION_DIVERGENCE_SCALE; see its comment in
    utils/config.py for why the raw Steck objective needs it to make
    `calibration_strength` behave as a plain 0.0-1.0 dial.

    Note this calibrates genre MASS, not title count. An item's genre
    mass is split across the genres it carries (see
    item_genre_distribution), so a 6-genre action/adventure/comedy/
    sci-fi/family title contributes only ~1/6 of its weight to `family`.
    A collection calibrated to a 2.5% family profile can therefore still
    contain a noticeable number of titles that carry a family tag among
    several others - they are being counted as the action/adventure
    films they also are. Calibration bounds how much of the collection's
    character is family, not how many titles mention it.

    Falls back to plain score order (i.e. the previous behavior) when
    calibration cannot apply: no target distribution (cold start / empty
    profile), a non-positive strength, or a candidate set that already
    fits within the limit.

    Args:
        candidates: Items to select from. Order is irrelevant; ties are
            broken by the caller's own pre-sort via stable iteration.
        limit: Maximum number of items to return.
        get_genres: Extract an item's genre list (lowercased by caller).
        get_score: Extract an item's similarity score.
        target_distribution: p(g|u) from build_target_distribution().
        calibration_strength: lambda in [0, 1). 0 disables calibration.

    Returns:
        The selected items, best-first.
    """
    if limit <= 0:
        return []

    ordered = list(candidates)

    # Nothing to trade off - keep the existing score ordering untouched.
    if not target_distribution or calibration_strength <= 0 or len(ordered) <= limit:
        return ordered[:limit]

    selected: List[T] = []
    selected_genres: List[Sequence[str]] = []
    remaining = ordered[:]

    while len(selected) < limit and remaining:
        best_index = 0
        best_value = -math.inf

        for i, candidate in enumerate(remaining):
            trial_genres = selected_genres + [get_genres(candidate)]
            divergence = kl_divergence(target_distribution, list_distribution(trial_genres))
            value = (1 - calibration_strength) * get_score(candidate) - (
                calibration_strength * CALIBRATION_DIVERGENCE_SCALE * divergence
            )

            if value > best_value:
                best_value = value
                best_index = i

        chosen = remaining.pop(best_index)
        selected.append(chosen)
        selected_genres.append(get_genres(chosen))

    return selected


def calibration_report(
    target_distribution: Dict[str, float],
    genre_lists: Sequence[Sequence[str]],
    top_n: int = 5,
) -> List[Tuple[str, float, float]]:
    """
    Compare target vs delivered genre share, for logging what calibration
    actually did.

    Returns (genre, target_share, delivered_share) for the `top_n` genres
    with the largest absolute divergence, worst first.
    """
    actual = list_distribution(genre_lists)
    genres = set(target_distribution) | set(actual)
    rows = [(g, target_distribution.get(g, 0.0), actual.get(g, 0.0)) for g in genres]
    rows.sort(key=lambda r: abs(r[1] - r[2]), reverse=True)
    return rows[:top_n]
