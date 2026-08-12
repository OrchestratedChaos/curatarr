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
Negative feedback from ignored recommendations.

The strongest signal a recommender gets is not what a user watched - it
is what it put in front of them that they declined to watch. Every large
streaming service logs impressions for exactly this reason: a title shown
for weeks and never played is a far more direct statement of "not for me"
than the absence of a play, because the absence of a play is ambiguous
(never offered? never noticed?) while a long-lived impression is not.

Curatarr already had the raw material and never used it. `label_dates`
records when each item first entered a user's collection (see
utils/labels.py), and utils/scoring.py has always handled negative
profile counts (`elif genre_count < 0` / `elif count < 0`) - but nothing
ever wrote one. Items sat in a collection indefinitely, were never
watched, and were re-recommended with an unchanged score every night.

This module closes that loop: an item labeled for at least
`min_days_shown` days and still unwatched becomes an anti-preference,
decrementing the profile counters for its genres and keywords so its
whole neighborhood scores lower next run.

The penalty is deliberately partial and capped (see
apply_ignored_penalties): a single ignored title is weak evidence -
people leave things for later - but twenty ignored titles sharing a genre
is not.
"""

import logging
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from utils.config import (
    IGNORED_REC_MAX_PROFILE_FRACTION,
    IGNORED_REC_MIN_DAYS_SHOWN,
    IGNORED_REC_PENALTY,
)

logger = logging.getLogger(__name__)


def find_ignored_recommendations(
    label_dates: Mapping[str, str],
    label_name: str,
    watched_ids: Set[int],
    now: Optional[datetime] = None,
    min_days_shown: int = IGNORED_REC_MIN_DAYS_SHOWN,
) -> List[Tuple[int, int]]:
    """
    Identify recommendations that have been on display long enough,
    unwatched, to count as declined.

    label_dates is keyed "<ratingKey>_<label_name>" (utils/labels.py), so
    entries for other users' labels are skipped rather than counted
    against this profile.

    Args:
        label_dates: The persisted label -> ISO-timestamp map.
        label_name: This user's collection label.
        watched_ids: Rating keys the user has since watched.
        now: Injectable clock for testing.
        min_days_shown: Days on display before an item counts as ignored.

    Returns:
        (rating_key, days_shown) for each ignored item, longest first.
    """
    current = now or datetime.now()
    suffix = f"_{label_name}"
    ignored: List[Tuple[int, int]] = []

    for key, iso in label_dates.items():
        if not key.endswith(suffix):
            continue
        raw_id = key[: -len(suffix)]
        try:
            rating_key = int(raw_id)
        except (TypeError, ValueError):
            continue

        # Watching it is the opposite signal - the recommendation worked.
        if rating_key in watched_ids:
            continue

        try:
            shown_since = datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            # A malformed timestamp is not evidence of anything; skip it
            # rather than guess a date and manufacture a penalty.
            logger.debug(f"Skipping unparseable label date for {key}: {iso!r}")
            continue

        days = (current - shown_since).days
        if days >= min_days_shown:
            ignored.append((rating_key, days))

    ignored.sort(key=lambda pair: pair[1], reverse=True)
    return ignored


def apply_ignored_penalties(
    counters: Dict[str, Dict[str, float]],
    ignored_items: Iterable[Mapping],
    penalty: float = IGNORED_REC_PENALTY,
    max_fraction: float = IGNORED_REC_MAX_PROFILE_FRACTION,
) -> Dict[str, int]:
    """
    Decrement genre/keyword counters for ignored items, in place.

    Two safeguards, because this signal is inferred rather than stated:

    1. Each item's penalty is divided across the terms it carries, so a
       7-genre title does not deliver seven times the punishment of a
       2-genre one for the same single act of being ignored.
    2. No term may be driven below -max_fraction of the profile's
       largest positive count. Without a floor, a user who ignores a
       long run of recommendations could drive a genre so far negative
       that nothing in it could ever surface again - the profile would
       be unable to recover even if their taste changed back.

    Note the counters this writes are consumed by scoring's existing
    negative-count branches; no scoring change was needed to read them.

    Returns a per-dimension count of how many terms were penalized.
    """
    applied = {"genres": 0, "tmdb_keywords": 0}

    for dimension, field in (("genres", "genres"), ("tmdb_keywords", "tmdb_keywords")):
        counter = counters.get(dimension)
        if counter is None:
            continue

        positives = [v for v in counter.values() if v > 0]
        floor = -(max(positives) * max_fraction) if positives else -penalty

        for item in ignored_items:
            terms = [t.lower() for t in (item.get(field) or []) if isinstance(t, str)]
            if not terms:
                continue
            share = penalty / len(terms)
            for term in terms:
                current = counter.get(term, 0.0)
                counter[term] = max(floor, current - share)
                applied[dimension] += 1

    return applied
