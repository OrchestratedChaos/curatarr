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
Franchise-ordered recommendations - start people at the beginning.

Ranking purely by similarity happily hands someone Rocky IV, The Godfather
Part III or Cult of Chucky as their first contact with a series. The score
is not wrong - those really are the closest match to the profile - but the
recommendation is, because scoring treats every candidate as independently
watchable and a franchise entry is not. Curatarr made this MORE likely
rather than less: the existing collection bonus (COLLECTION_BONUS_* in
utils/config.py) deliberately boosts a title when the user has watched
others in its collection, which is right in principle but says nothing
about which entry to serve next.

This module supplies that missing half. When a candidate belongs to a TMDB
collection, the recommendation slot goes to the earliest entry of that
collection the user has not already watched:

    watched nothing        -> Rocky (1976)
    watched Rocky          -> Rocky II (1979)
    watched Rocky I and II -> Rocky III (1982)

Four things are deliberate, and each one is a decision that could
reasonably have gone the other way:

1. **Library-only.** A promoted entry must already be in the Plex library,
   because the artifact is a Plex collection - a recommendation the user
   cannot press play on is not a recommendation. When the true first entry
   is missing entirely, the earliest one they DO own is promoted instead
   and the gap is reported (see find_library_gaps), which is precisely the
   input Sequel Huntarr already turns into a Radarr request.

2. **Release order, from the library's own `year`.** Costs no API calls -
   `collection_id`/`collection_name`/`year` are already cached per movie
   (recommenders/base.py's MovieCache/_backfill_collection_data). Release
   order is not always narrative order (prequels exist), but it is the
   order the films were made to be seen in, and it is the only order the
   cache can answer for free. Unknown years sort last rather than first,
   so a missing year can never hijack position one.

3. **Hard filters are respected; sizing filters are not.** An entry is
   never promoted past an excluded genre, a per-user max content rating,
   or a recommendation the user has already visibly declined
   (utils/ignored_recs.py) - those are direct statements of intent. It IS
   promoted past `quality_filters` and `min_similarity`, which exist to
   size the collection rather than to express a preference: a 1976
   original with a thin TMDB vote count should not be withheld while its
   own sequel is recommended.

4. **One slot per franchise.** Rocky III and Rocky IV both resolving to
   Rocky is not three recommendations, it is one - so the duplicates
   collapse and the franchise keeps the best rank any of its members
   earned. Because this runs before the candidate buffer is truncated,
   the freed slots refill from the tail rather than shrinking the
   collection.
"""

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import AbstractSet, Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from utils.plex_policy import is_rating_allowed

logger = logging.getLogger(__name__)

# Sorts an entry with no usable year to the END of its collection. The
# alternative (treating unknown as 0) would make a single missing year
# silently take over position one for the whole franchise, which is the
# one outcome this module exists to prevent.
UNKNOWN_YEAR_SORT = 9999

# Written by recommenders/huntarr.py. Read here strictly opportunistically
# and read-only: it is the one place the FULL TMDB member list of a
# collection is already on disk, which is what makes "you own Rocky II-V
# but not Rocky" answerable without a single extra API call. Absent,
# stale or unreadable simply means no gap reporting.
HUNTARR_CACHE_FILENAME = "huntarr_cache.json"


@dataclass(frozen=True, eq=False)
class FranchiseEntry:
    """One library movie, positioned within its TMDB collection."""

    rating_key: int
    collection_id: Any
    collection_name: str
    title: str
    year: Optional[int]
    info: Mapping = field(repr=False)

    @property
    def sort_key(self) -> Tuple[int, str]:
        """Release order, with unknown years last (see UNKNOWN_YEAR_SORT)."""
        return (self.year if self.year is not None else UNKNOWN_YEAR_SORT, self.title.lower())

    def label(self) -> str:
        return f"{self.title} ({self.year if self.year is not None else 'N/A'})"


@dataclass(frozen=True, eq=False)
class FranchiseSubstitution:
    """A recommendation slot handed from a later entry to an earlier one."""

    collection_id: Any
    collection_name: str
    original_title: str
    original_year: Optional[int]
    original_rating_key: Optional[int]
    promoted_title: str
    promoted_year: Optional[int]
    promoted_rating_key: int

    def describe(self) -> str:
        original_year = self.original_year if self.original_year is not None else "N/A"
        promoted_year = self.promoted_year if self.promoted_year is not None else "N/A"
        return (
            f"{self.original_title} ({original_year}) -> "
            f"{self.promoted_title} ({promoted_year})  [{self.collection_name}]"
        )


def coerce_year(value: Any) -> Optional[int]:
    """
    Best-effort year from the several shapes the caches actually hold.

    The movie cache stores an int, the Huntarr cache stores a string, and
    both store None for items TMDB has no release date for. Anything that
    isn't a plausible 4-digit year becomes None (sorted last) rather than
    a guess.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1000 <= value <= 2999 else None
    if isinstance(value, str):
        digits = value.strip()[:4]
        if digits.isdigit():
            year = int(digits)
            return year if 1000 <= year <= 2999 else None
    return None


def normalize_collection_id(value: Any) -> Optional[Any]:
    """
    Collection ids compare as ints wherever they can.

    JSON round-trips have left these as both `10455` and `"10455"` in
    different caches (the movie cache holds the int, the Huntarr cache
    keys its dict by the string), and a franchise silently splitting in
    two because of that would be invisible - each half would look like a
    single-entry collection and simply never reorder.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return int(text) if text.isdigit() else text
    return None


def build_franchise_index(all_items: Mapping[str, Mapping]) -> Dict[Any, List[FranchiseEntry]]:
    """
    Group the media cache into collection_id -> entries in release order.

    Args:
        all_items: The media cache's item dict (ratingKey -> info), i.e.
            recommenders/base.py's `media_cache.cache[self.media_key]`.
            Deliberately the WHOLE cache including watched and
            filtered-out items - "which entries has the user already
            seen" is unanswerable from the unwatched candidate pool
            alone.

    Returns:
        collection_id -> entries sorted by (year, title). Collections
        with no `collection_id` (TV, or a standalone film) never appear;
        single-entry collections do, because gap reporting still has
        something to say about them.
    """
    groups: Dict[Any, List[FranchiseEntry]] = defaultdict(list)

    for raw_key, info in (all_items or {}).items():
        if not isinstance(info, Mapping):
            continue
        collection_id = normalize_collection_id(info.get("collection_id"))
        if collection_id is None:
            continue
        try:
            rating_key = int(str(raw_key))
        except (TypeError, ValueError):
            continue

        groups[collection_id].append(
            FranchiseEntry(
                rating_key=rating_key,
                collection_id=collection_id,
                collection_name=info.get("collection_name") or "Unknown Collection",
                title=info.get("title") or str(raw_key),
                year=coerce_year(info.get("year")),
                info=info,
            )
        )

    return {cid: sorted(entries, key=lambda e: e.sort_key) for cid, entries in groups.items()}


def is_promotable(
    info: Mapping,
    rating_key: int,
    *,
    declined_ids: AbstractSet[int] = frozenset(),
    excluded_genres: AbstractSet[str] = frozenset(),
    max_rating: Optional[str] = None,
    media_type: str = "movie",
) -> bool:
    """
    May this entry be handed a recommendation slot?

    The three disqualifiers are the ones that express what a user WANTS,
    as opposed to how large their collection should be:

    - an excluded genre (general.exclude_genre / per-user preferences),
    - a per-user max content rating (users.preferences.max_rating),
    - a recommendation they were shown and visibly declined
      (utils/ignored_recs.py).

    Quality thresholds and `min_similarity` are intentionally absent -
    see this module's docstring for why a promoted original is allowed
    past them.
    """
    if rating_key in declined_ids:
        return False

    if excluded_genres:
        genres = info.get("genres") or []
        if any(isinstance(g, str) and g.lower() in excluded_genres for g in genres):
            return False

    if max_rating and not is_rating_allowed(info.get("content_rating"), max_rating, media_type):
        return False

    return True


def find_next_unwatched(
    entries: Sequence[FranchiseEntry],
    watched_ids: Set[int],
    *,
    declined_ids: AbstractSet[int] = frozenset(),
    excluded_genres: AbstractSet[str] = frozenset(),
    max_rating: Optional[str] = None,
    media_type: str = "movie",
) -> Optional[FranchiseEntry]:
    """
    The earliest entry of this collection the user hasn't watched.

    Walks release order and returns the first entry that is neither
    already watched nor disqualified (see is_promotable). Watched entries
    LATER in the run do not stop the walk - somebody who saw Rocky IV but
    not Rocky is still owed Rocky.

    Returns None when every entry is watched or disqualified.
    """
    for entry in entries:
        if entry.rating_key in watched_ids:
            continue
        if not is_promotable(
            entry.info,
            entry.rating_key,
            declined_ids=declined_ids,
            excluded_genres=excluded_genres,
            max_rating=max_rating,
            media_type=media_type,
        ):
            continue
        return entry
    return None


def _promote(entry: FranchiseEntry, original: Mapping) -> Dict:
    """
    Build the recommendation dict for a promoted entry.

    A COPY, never the cached dict itself: the media cache's own item
    dicts are the same objects `get_recommendations()` writes
    `cached_score`/`profile_hash` into and then persists, so writing a
    borrowed score onto one would poison the score cache for every later
    run. `cached_score`/`profile_hash` are dropped from the copy for the
    same reason in reverse - they describe a score this dict no longer
    carries, and leaving them would invite a future reader to trust them.
    """
    promoted = dict(entry.info)
    promoted.pop("cached_score", None)
    promoted.pop("profile_hash", None)
    promoted["plex_rating_key"] = entry.rating_key
    promoted["similarity_score"] = original.get("similarity_score", 0.0)

    # The slot's score is inherited, so the breakdown shown for it is the
    # inheriting one, annotated. Using the promoted entry's OWN cached
    # breakdown would be worse than useless: it may have been computed
    # against a different profile_hash entirely (see CLAUDE.md).
    breakdown = dict(original.get("score_breakdown") or {})
    details = dict(breakdown.get("details") or {})
    original_year = original.get("year") if original.get("year") is not None else "N/A"
    details["franchise"] = (
        f"earliest unwatched entry in {entry.collection_name}; "
        f"score inherited from {original.get('title', 'unknown')} ({original_year})"
    )
    breakdown["details"] = details
    promoted["score_breakdown"] = breakdown

    promoted["franchise_promoted_from"] = {
        "title": original.get("title"),
        "year": original.get("year"),
        "plex_rating_key": original.get("plex_rating_key"),
    }
    return promoted


def apply_franchise_ordering(
    scored_items: Sequence[Mapping],
    franchise_index: Mapping[Any, List[FranchiseEntry]],
    watched_ids: Set[int],
    *,
    declined_ids: AbstractSet[int] = frozenset(),
    excluded_genres: AbstractSet[str] = frozenset(),
    max_rating: Optional[str] = None,
    media_type: str = "movie",
) -> Tuple[List[Dict], List[FranchiseSubstitution]]:
    """
    Re-point every franchise recommendation at its earliest unwatched entry.

    Args:
        scored_items: Scored candidates in rank order (highest first).
        franchise_index: Output of build_franchise_index().
        watched_ids: Rating keys this user has watched. Pass the union of
            `watched_ids` and `user_played_ids` - the per-user Plex view
            knows about plays the shared admin history does not, and a
            part wrongly believed unwatched sends the whole franchise
            back to the start.
        declined_ids / excluded_genres / max_rating / media_type: the
            promotion eligibility rules - see is_promotable().

    Returns:
        (reordered items, substitutions made). The reordered list holds
        the same ranks as the input with promoted entries swapped in, and
        with any franchise appearing exactly once - so it can be SHORTER
        than the input. Call this before truncating to the candidate
        buffer, so the freed slots refill from the tail.
    """
    ordered: List[Dict] = []
    substitutions: List[FranchiseSubstitution] = []
    seen: Set[int] = set()

    for item in scored_items:
        target: Dict = item if isinstance(item, dict) else dict(item)
        rating_key = item.get("plex_rating_key")
        collection_id = normalize_collection_id(item.get("collection_id"))
        entries = franchise_index.get(collection_id) if collection_id is not None else None

        if entries and len(entries) > 1 and rating_key is not None:
            nxt = find_next_unwatched(
                entries,
                watched_ids,
                declined_ids=declined_ids,
                excluded_genres=excluded_genres,
                max_rating=max_rating,
                media_type=media_type,
            )
            current = next((e for e in entries if e.rating_key == rating_key), None)
            # Only ever move EARLIER. Without this an entry the user is
            # already correctly being offered could be displaced by a
            # later one whenever eligibility rules skipped it.
            if (
                nxt is not None
                and nxt.rating_key != rating_key
                and (current is None or nxt.sort_key < current.sort_key)
            ):
                target = _promote(nxt, item)
                substitutions.append(
                    FranchiseSubstitution(
                        collection_id=collection_id,
                        collection_name=nxt.collection_name,
                        original_title=item.get("title", "unknown"),
                        original_year=item.get("year"),
                        original_rating_key=rating_key,
                        promoted_title=nxt.title,
                        promoted_year=nxt.year,
                        promoted_rating_key=nxt.rating_key,
                    )
                )

        target_key = target.get("plex_rating_key")
        if target_key is not None:
            if target_key in seen:
                # Same franchise, lower-ranked member - already served.
                continue
            seen.add(target_key)
        ordered.append(target)

    return ordered, substitutions


def load_collection_details(cache_dir: str) -> Dict[Any, Dict]:
    """
    Read Sequel Huntarr's cached TMDB collection member lists, if any.

    Read-only and version-blind on purpose: this feeds reporting, never a
    recommendation, so tolerating an older/staler file beats refusing to
    report anything. Every failure mode - no file, unreadable file,
    unexpected shape - returns {} and the caller silently skips gap
    reporting.
    """
    path = os.path.join(cache_dir, HUNTARR_CACHE_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        logger.debug(f"No Huntarr collection details available for franchise gap reporting: {e}")
        return {}

    raw = data.get("collection_details") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}

    details: Dict[Any, Dict] = {}
    for cid, detail in raw.items():
        collection_id = normalize_collection_id(cid)
        if collection_id is not None and isinstance(detail, dict):
            details[collection_id] = detail
    return details


def find_library_gaps(
    collection_detail: Mapping,
    before_year: Optional[int],
    library_tmdb_ids: Set[int],
) -> List[Dict]:
    """
    Entries of this collection released before `before_year` that the
    library doesn't hold.

    This is the "you are being handed Rocky II because you don't own
    Rocky" case. Purely informational - Sequel Huntarr is what actually
    requests these - and it is why option (a) of the missing-part
    decision is safe: the user still gets the earliest entry they can
    actually play, plus a note about the one they can't.
    """
    if before_year is None:
        return []

    gaps: List[Dict] = []
    for movie in collection_detail.get("movies") or []:
        if not isinstance(movie, Mapping):
            continue
        year = coerce_year(movie.get("year")) or coerce_year(movie.get("release_date"))
        if year is None or year >= before_year:
            continue
        tmdb_id = movie.get("tmdb_id")
        if tmdb_id in library_tmdb_ids:
            continue
        gaps.append({"tmdb_id": tmdb_id, "title": movie.get("title") or "Unknown", "year": year})

    return sorted(gaps, key=lambda g: (g["year"], g["title"]))


def collect_library_tmdb_ids(all_items: Mapping[str, Mapping]) -> Set[int]:
    """TMDB ids present in the media cache - the 'do we own it' lookup."""
    ids: Set[int] = set()
    for info in (all_items or {}).values():
        if isinstance(info, Mapping) and isinstance(info.get("tmdb_id"), int):
            ids.add(info["tmdb_id"])
    return ids


def summarize_substitutions(substitutions: Iterable[FranchiseSubstitution], limit: int = 10) -> List[str]:
    """Human-readable lines for the run log, capped, with an explicit
    "... and N more" rather than a silent truncation."""
    lines = [s.describe() for s in substitutions]
    if len(lines) > limit:
        remaining = len(lines) - limit
        lines = lines[:limit] + [f"... and {remaining} more"]
    return lines
