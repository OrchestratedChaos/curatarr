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
Tests for utils/franchise.py - starting people at the beginning of a
series instead of wherever the similarity score happened to land.
"""

import json
import os
import tempfile

from utils.franchise import (
    DECISION_PROMOTED,
    DECISION_SUPPRESSED,
    UNKNOWN_YEAR_SORT,
    apply_franchise_ordering,
    build_franchise_index,
    coerce_year,
    collect_library_tmdb_ids,
    decisions_of_kind,
    find_library_gaps,
    find_next_unwatched,
    is_promotable,
    load_collection_details,
    normalize_collection_id,
    summarize_decisions,
)

ROCKY = 1575
CHUCKY = 10455


def _movie(rating_key, title, year, collection_id=ROCKY, collection_name="Rocky Collection", **extra):
    """One media-cache entry, in the shape recommenders/base.py caches."""
    info = {
        "title": title,
        "year": year,
        "collection_id": collection_id,
        "collection_name": collection_name,
        "genres": [],
        "content_rating": "PG",
    }
    info.update(extra)
    return str(rating_key), info


def _library(*pairs):
    return dict(pairs)


ROCKY_LIBRARY = _library(
    _movie(1, "Rocky", 1976),
    _movie(2, "Rocky II", 1979),
    _movie(3, "Rocky III", 1982),
    _movie(4, "Rocky IV", 1985),
)


def _scored(library, rating_key, score):
    """
    Mimic get_recommendations(): the scored candidates ARE the cached
    dicts, with plex_rating_key/similarity_score written onto them.
    """
    info = library[str(rating_key)]
    info["plex_rating_key"] = rating_key
    info["similarity_score"] = score
    return info


class TestCoerceYear:
    def test_int_year(self):
        assert coerce_year(1976) == 1976

    def test_string_year(self):
        assert coerce_year("1976") == 1976

    def test_release_date_string(self):
        assert coerce_year("1976-11-21") == 1976

    def test_none(self):
        assert coerce_year(None) is None

    def test_garbage(self):
        assert coerce_year("unknown") is None

    def test_empty_string(self):
        assert coerce_year("") is None

    def test_bool_is_not_a_year(self):
        """True is an int in Python; a year it is not."""
        assert coerce_year(True) is None

    def test_implausible_year_rejected(self):
        assert coerce_year(12) is None
        assert coerce_year(99999) is None


class TestNormalizeCollectionId:
    def test_int_passes_through(self):
        assert normalize_collection_id(1575) == 1575

    def test_numeric_string_becomes_int(self):
        """The movie cache holds the int, the Huntarr cache keys by the
        string - a franchise splitting in two over that would be
        invisible, because each half would look like a single-entry
        collection and simply never reorder."""
        assert normalize_collection_id("1575") == 1575

    def test_non_numeric_string_kept(self):
        assert normalize_collection_id("abc") == "abc"

    def test_none_and_blank(self):
        assert normalize_collection_id(None) is None
        assert normalize_collection_id("   ") is None

    def test_bool_rejected(self):
        assert normalize_collection_id(True) is None

    def test_unexpected_types_rejected(self):
        assert normalize_collection_id(15.75) is None
        assert normalize_collection_id(["1575"]) is None


class TestBuildFranchiseIndex:
    def test_groups_by_collection_in_release_order(self):
        index = build_franchise_index(ROCKY_LIBRARY)
        assert [e.title for e in index[ROCKY]] == ["Rocky", "Rocky II", "Rocky III", "Rocky IV"]

    def test_out_of_order_input_is_sorted(self):
        library = _library(_movie(4, "Rocky IV", 1985), _movie(1, "Rocky", 1976), _movie(2, "Rocky II", 1979))
        index = build_franchise_index(library)
        assert [e.year for e in index[ROCKY]] == [1976, 1979, 1985]

    def test_unknown_year_sorts_last(self):
        """A single missing year must never take over position one - that
        is the exact outcome this module exists to prevent."""
        library = _library(_movie(9, "Rocky Mystery", None), _movie(1, "Rocky", 1976))
        index = build_franchise_index(library)
        assert [e.title for e in index[ROCKY]] == ["Rocky", "Rocky Mystery"]
        assert index[ROCKY][-1].sort_key[0] == UNKNOWN_YEAR_SORT

    def test_string_and_int_collection_ids_land_in_one_group(self):
        library = _library(
            _movie(1, "Rocky", 1976, collection_id=ROCKY), _movie(2, "Rocky II", 1979, collection_id="1575")
        )
        index = build_franchise_index(library)
        assert len(index) == 1
        assert len(index[ROCKY]) == 2

    def test_items_without_collection_are_skipped(self):
        library = _library(_movie(1, "Heat", 1995, collection_id=None))
        assert build_franchise_index(library) == {}

    def test_separate_collections_stay_separate(self):
        library = _library(
            _movie(1, "Rocky", 1976),
            _movie(5, "Child's Play", 1988, collection_id=CHUCKY, collection_name="Chucky Collection"),
        )
        index = build_franchise_index(library)
        assert set(index) == {ROCKY, CHUCKY}

    def test_non_numeric_rating_key_skipped(self):
        library = {"not-a-key": dict(_movie(1, "Rocky", 1976)[1])}
        assert build_franchise_index(library) == {}

    def test_non_mapping_values_skipped(self):
        assert build_franchise_index({"1": "junk"}) == {}

    def test_empty_input(self):
        assert build_franchise_index({}) == {}
        assert build_franchise_index(None) == {}

    def test_single_entry_collections_are_kept(self):
        """They can never produce a substitution, but gap reporting still
        has something to say about them."""
        index = build_franchise_index(_library(_movie(1, "Rocky", 1976)))
        assert len(index[ROCKY]) == 1


class TestIsPromotable:
    def test_plain_entry_is_promotable(self):
        _key, info = _movie(1, "Rocky", 1976)
        assert is_promotable(info, 1) is True

    def test_declined_entry_is_not(self):
        _key, info = _movie(1, "Rocky", 1976)
        assert is_promotable(info, 1, declined_ids={1}) is False

    def test_excluded_genre_is_not(self):
        _key, info = _movie(1, "Rocky", 1976, genres=["drama", "horror"])
        assert is_promotable(info, 1, excluded_genres={"horror"}) is False

    def test_excluded_genre_matching_is_case_insensitive(self):
        _key, info = _movie(1, "Rocky", 1976, genres=["Horror"])
        assert is_promotable(info, 1, excluded_genres={"horror"}) is False

    def test_over_max_rating_is_not(self):
        _key, info = _movie(1, "Rocky", 1976, content_rating="R")
        assert is_promotable(info, 1, max_rating="PG") is False

    def test_within_max_rating_is(self):
        _key, info = _movie(1, "Rocky", 1976, content_rating="PG")
        assert is_promotable(info, 1, max_rating="PG-13") is True

    def test_quality_fields_are_not_consulted(self):
        """Thin vote counts and low ratings size a collection; they do
        not state a preference, so they never block a promotion."""
        _key, info = _movie(1, "Rocky", 1976, rating=1.0, vote_count=0)
        assert is_promotable(info, 1) is True


class TestFindNextUnwatched:
    def _entries(self, library=None):
        return build_franchise_index(library or ROCKY_LIBRARY)[ROCKY]

    def test_nothing_watched_returns_the_first(self):
        assert find_next_unwatched(self._entries(), set()).title == "Rocky"

    def test_first_watched_returns_the_second(self):
        assert find_next_unwatched(self._entries(), {1}).title == "Rocky II"

    def test_first_two_watched_returns_the_third(self):
        assert find_next_unwatched(self._entries(), {1, 2}).title == "Rocky III"

    def test_watching_a_later_entry_does_not_skip_the_first(self):
        """Somebody who saw Rocky IV but not Rocky is still owed Rocky."""
        assert find_next_unwatched(self._entries(), {4}).title == "Rocky"

    def test_all_watched_returns_none(self):
        assert find_next_unwatched(self._entries(), {1, 2, 3, 4}) is None

    def test_disqualified_entry_is_skipped(self):
        assert find_next_unwatched(self._entries(), set(), declined_ids={1}).title == "Rocky II"

    def test_all_disqualified_returns_none(self):
        assert find_next_unwatched(self._entries(), set(), declined_ids={1, 2, 3, 4}) is None

    def test_excluded_genre_entry_is_skipped(self):
        library = _library(_movie(1, "Rocky", 1976, genres=["horror"]), _movie(2, "Rocky II", 1979))
        entries = self._entries(library)
        assert find_next_unwatched(entries, set(), excluded_genres={"horror"}).title == "Rocky II"

    def test_empty_entries(self):
        assert find_next_unwatched([], set()) is None


class TestApplyFranchiseOrdering:
    """The started/unstarted split is the heart of this module - see
    apply_franchise_ordering's docstring for why the two cases are not
    the same recommendation."""

    def _order(self, library, scored, watched=frozenset(), **kwargs):
        return apply_franchise_ordering(scored, build_franchise_index(library), set(watched), **kwargs)

    # -- Started series: promote and inherit the slot -------------------

    def test_started_series_promotes_to_the_next_unwatched_entry(self):
        library = dict(ROCKY_LIBRARY)
        ordered, decisions = self._order(library, [_scored(library, 4, 0.7)], watched={1})
        assert [i["title"] for i in ordered] == ["Rocky II"]
        assert [d.kind for d in decisions] == [DECISION_PROMOTED]

    def test_started_series_walks_forward_as_entries_are_watched(self):
        library = dict(ROCKY_LIBRARY)
        ordered, _d = self._order(library, [_scored(library, 4, 0.7)], watched={1, 2})
        assert [i["title"] for i in ordered] == ["Rocky III"]

    def test_promoted_entry_inherits_the_score_and_the_rank(self):
        """The slot is the sequel's; the title in it is the earlier one's."""
        library = dict(ROCKY_LIBRARY)
        ordered, _d = self._order(library, [_scored(library, 4, 0.73)], watched={1})
        assert ordered[0]["similarity_score"] == 0.73
        assert ordered[0]["plex_rating_key"] == 2

    def test_promotion_target_need_not_be_in_the_scored_pool(self):
        """The point of running after the quality/score gates: on a series
        being worked through, an entry those gates dropped is still owed."""
        library = dict(ROCKY_LIBRARY)
        library["2"]["rating"], library["2"]["vote_count"] = 1.0, 0
        ordered, _d = self._order(library, [_scored(library, 4, 0.5)], watched={1})
        assert ordered[0]["title"] == "Rocky II"

    def test_started_franchise_collapses_to_one_slot(self):
        library = dict(ROCKY_LIBRARY)
        scored = [_scored(library, 4, 0.9), _scored(library, 3, 0.8)]
        ordered, decisions = self._order(library, scored, watched={1})
        assert [i["title"] for i in ordered] == ["Rocky II"]
        assert len(decisions) == 2

    def test_started_franchise_keeps_the_best_rank_any_member_earned(self):
        library = dict(ROCKY_LIBRARY)
        other = {"title": "Heat", "year": 1995, "collection_id": None, "plex_rating_key": 99, "similarity_score": 0.85}
        scored = [_scored(library, 4, 0.9), other, _scored(library, 3, 0.8)]
        ordered, _d = self._order(library, scored, watched={1})
        assert [i["title"] for i in ordered] == ["Rocky II", "Heat"]

    def test_target_already_in_the_pool_is_not_duplicated(self):
        library = dict(ROCKY_LIBRARY)
        scored = [_scored(library, 4, 0.9), _scored(library, 2, 0.2)]
        ordered, _d = self._order(library, scored, watched={1})
        assert [i["plex_rating_key"] for i in ordered] == [2]
        assert ordered[0]["similarity_score"] == 0.9

    # -- Unstarted series: suppress, never promote ----------------------

    def test_unstarted_series_drops_the_sequel_instead_of_promoting(self):
        """Rocky IV matching the profile is not evidence that a 1976
        boxing drama deserves its top-50 slot."""
        library = dict(ROCKY_LIBRARY)
        ordered, decisions = self._order(library, [_scored(library, 4, 0.7)])
        assert ordered == []
        assert [d.kind for d in decisions] == [DECISION_SUPPRESSED]
        assert decisions[0].target_title == "Rocky"

    def test_unstarted_first_entry_keeps_its_own_rank_and_score(self):
        library = dict(ROCKY_LIBRARY)
        scored = [_scored(library, 4, 0.9), _scored(library, 1, 0.2)]
        ordered, _d = self._order(library, scored)
        assert [i["plex_rating_key"] for i in ordered] == [1]
        assert ordered[0]["similarity_score"] == 0.2, "no inheritance on an unstarted series"

    def test_unstarted_series_absent_from_the_pool_disappears_entirely(self):
        library = dict(ROCKY_LIBRARY)
        scored = [_scored(library, 4, 0.9), _scored(library, 3, 0.8)]
        ordered, decisions = self._order(library, scored)
        assert ordered == []
        assert all(d.kind == DECISION_SUPPRESSED for d in decisions)

    def test_unstarted_first_entry_alone_is_left_alone(self):
        library = dict(ROCKY_LIBRARY)
        ordered, decisions = self._order(library, [_scored(library, 1, 0.7)])
        assert [i["title"] for i in ordered] == ["Rocky"]
        assert decisions == []

    def test_suppression_never_removes_a_standalone_film(self):
        heat = {"title": "Heat", "year": 1995, "collection_id": None, "plex_rating_key": 99, "similarity_score": 0.8}
        ordered, decisions = self._order(ROCKY_LIBRARY, [heat])
        assert ordered == [heat]
        assert decisions == []

    # -- Eligibility rules, shared by both cases ------------------------

    def test_never_moves_to_a_later_entry(self):
        """With the first entry declined, the second stays put rather than
        being displaced by a third."""
        library = dict(ROCKY_LIBRARY)
        ordered, decisions = self._order(library, [_scored(library, 2, 0.5)], declined_ids={1})
        assert ordered[0]["title"] == "Rocky II"
        assert decisions == []

    def test_declined_first_entry_is_skipped(self):
        library = dict(ROCKY_LIBRARY)
        ordered, _d = self._order(library, [_scored(library, 4, 0.5)], watched={2}, declined_ids={1})
        assert ordered[0]["title"] == "Rocky III"

    def test_max_rating_blocks_the_original(self):
        """The R-rated first entry is skipped for a PG-capped user; the
        series still advances to the earliest entry they may watch."""
        library = _library(
            _movie(1, "Rocky", 1976, content_rating="R"),
            _movie(2, "Rocky II", 1979, content_rating="PG"),
            _movie(3, "Rocky III", 1982, content_rating="PG"),
            _movie(4, "Rocky IV", 1985, content_rating="PG"),
        )
        ordered, _d = self._order(library, [_scored(library, 4, 0.5)], watched={2}, max_rating="PG")
        assert [i["title"] for i in ordered] == ["Rocky III"]

    def test_single_entry_collection_is_a_no_op(self):
        library = _library(_movie(7, "Highlander", 1986, collection_id=999))
        ordered, decisions = self._order(library, [_scored(library, 7, 0.8)])
        assert [i["title"] for i in ordered] == ["Highlander"]
        assert decisions == []

    def test_fully_watched_franchise_leaves_the_candidate_alone(self):
        library = dict(ROCKY_LIBRARY)
        ordered, decisions = self._order(library, [_scored(library, 4, 0.7)], watched={1, 2, 3, 4})
        assert [i["title"] for i in ordered] == ["Rocky IV"]
        assert decisions == []

    # -- Cache hygiene --------------------------------------------------

    def test_cached_dict_is_never_mutated(self):
        """The media cache's own dicts are what get_recommendations()
        persists - writing a borrowed score onto one would poison the
        score cache for every later run."""
        library = dict(ROCKY_LIBRARY)
        library["2"]["cached_score"] = 0.11
        library["2"]["profile_hash"] = "old-profile"
        self._order(library, [_scored(library, 4, 0.9)], watched={1})
        assert library["2"]["cached_score"] == 0.11
        assert library["2"].get("similarity_score") != 0.9

    def test_promoted_copy_drops_stale_score_cache_keys(self):
        library = dict(ROCKY_LIBRARY)
        library["2"]["cached_score"] = 0.11
        library["2"]["profile_hash"] = "old-profile"
        ordered, _d = self._order(library, [_scored(library, 4, 0.9)], watched={1})
        assert "cached_score" not in ordered[0]
        assert "profile_hash" not in ordered[0]

    def test_promoted_copy_records_where_the_slot_came_from(self):
        library = dict(ROCKY_LIBRARY)
        ordered, _d = self._order(library, [_scored(library, 4, 0.9)], watched={1})
        assert ordered[0]["franchise_promoted_from"]["title"] == "Rocky IV"
        assert "franchise" in ordered[0]["score_breakdown"]["details"]

    def test_missing_rating_key_is_left_alone(self):
        item = {"title": "Rocky IV", "year": 1985, "collection_id": ROCKY, "similarity_score": 0.5}
        ordered, decisions = self._order(ROCKY_LIBRARY, [item])
        assert ordered == [item]
        assert decisions == []

    def test_empty_input(self):
        assert self._order(ROCKY_LIBRARY, []) == ([], [])


class TestDecisionsOfKind:
    def test_splits_by_kind(self):
        library = _library(
            _movie(1, "Rocky", 1976),
            _movie(2, "Rocky II", 1979),
            _movie(3, "Rocky III", 1982),
            _movie(5, "Jaws", 1975, collection_id=999, collection_name="Jaws Collection"),
            _movie(6, "Jaws 2", 1978, collection_id=999, collection_name="Jaws Collection"),
        )
        # Rocky started (part 1 watched) -> promote; Jaws untouched -> suppress.
        scored = [_scored(library, 3, 0.9), _scored(library, 6, 0.8)]
        _ordered, decisions = apply_franchise_ordering(scored, build_franchise_index(library), {1})
        assert [d.collection_name for d in decisions_of_kind(decisions, DECISION_PROMOTED)] == ["Rocky Collection"]
        assert [d.collection_name for d in decisions_of_kind(decisions, DECISION_SUPPRESSED)] == ["Jaws Collection"]

    def test_empty(self):
        assert decisions_of_kind([], DECISION_PROMOTED) == []


class TestFindLibraryGaps:
    DETAIL = {
        "collection_id": ROCKY,
        "collection_name": "Rocky Collection",
        "movies": [
            {"tmdb_id": 1366, "title": "Rocky", "year": "1976", "release_date": "1976-11-21"},
            {"tmdb_id": 1367, "title": "Rocky II", "year": "1979", "release_date": "1979-06-15"},
            {"tmdb_id": 1371, "title": "Rocky III", "year": "1982", "release_date": "1982-05-28"},
        ],
    }

    def test_reports_earlier_entries_the_library_lacks(self):
        gaps = find_library_gaps(self.DETAIL, 1982, {1371})
        assert [g["title"] for g in gaps] == ["Rocky", "Rocky II"]

    def test_owned_entries_are_not_gaps(self):
        gaps = find_library_gaps(self.DETAIL, 1982, {1366, 1371})
        assert [g["title"] for g in gaps] == ["Rocky II"]

    def test_later_entries_are_not_gaps(self):
        assert find_library_gaps(self.DETAIL, 1976, {1366}) == []

    def test_unknown_reference_year_reports_nothing(self):
        assert find_library_gaps(self.DETAIL, None, set()) == []

    def test_undated_members_are_skipped(self):
        detail = {"movies": [{"tmdb_id": 5, "title": "Untitled Rocky", "year": None, "release_date": None}]}
        assert find_library_gaps(detail, 1982, set()) == []

    def test_release_date_is_used_when_year_is_absent(self):
        detail = {"movies": [{"tmdb_id": 1366, "title": "Rocky", "release_date": "1976-11-21"}]}
        assert [g["year"] for g in find_library_gaps(detail, 1982, set())] == [1976]

    def test_gaps_are_sorted_oldest_first(self):
        gaps = find_library_gaps(self.DETAIL, 1982, set())
        assert [g["year"] for g in gaps] == [1976, 1979]

    def test_malformed_detail(self):
        assert find_library_gaps({}, 1982, set()) == []
        assert find_library_gaps({"movies": ["junk"]}, 1982, set()) == []


class TestLoadCollectionDetails:
    def _write(self, payload):
        tmpdir = tempfile.mkdtemp()
        with open(os.path.join(tmpdir, "huntarr_cache.json"), "w", encoding="utf-8") as fh:
            if isinstance(payload, str):
                fh.write(payload)
            else:
                json.dump(payload, fh)
        return tmpdir

    def test_missing_file_is_not_an_error(self):
        assert load_collection_details(tempfile.mkdtemp()) == {}

    def test_corrupt_file_is_not_an_error(self):
        assert load_collection_details(self._write("{not json")) == {}

    def test_string_keys_are_normalized_to_ints(self):
        details = load_collection_details(self._write({"collection_details": {"1575": {"movies": []}}}))
        assert set(details) == {1575}

    def test_missing_section(self):
        assert load_collection_details(self._write({"version": 4})) == {}

    def test_non_dict_entries_skipped(self):
        details = load_collection_details(self._write({"collection_details": {"1575": "junk"}}))
        assert details == {}

    def test_version_is_not_enforced(self):
        """Gap reporting is informational - tolerating an older file
        beats refusing to report anything."""
        details = load_collection_details(self._write({"version": 1, "collection_details": {"1575": {"movies": []}}}))
        assert set(details) == {1575}


class TestCollectLibraryTmdbIds:
    def test_collects_int_ids_only(self):
        library = _library(
            _movie(1, "Rocky", 1976, tmdb_id=1366),
            _movie(2, "Rocky II", 1979, tmdb_id=None),
            _movie(3, "Rocky III", 1982, tmdb_id="1371"),
        )
        assert collect_library_tmdb_ids(library) == {1366}

    def test_empty(self):
        assert collect_library_tmdb_ids({}) == set()


class TestSummarizeDecisions:
    def _decisions(self, n):
        """n sequels of one series the user HAS started (Part 1 watched),
        so every decision is a promotion."""
        library = _library(*[_movie(i, f"Part {i}", 1970 + i) for i in range(1, n + 3)])
        scored = [_scored(library, i, 0.5) for i in range(3, n + 3)]
        _ordered, decisions = apply_franchise_ordering(scored, build_franchise_index(library), {1})
        return decisions

    def test_describes_a_promotion(self):
        lines = summarize_decisions(self._decisions(1))
        assert "Part 3 (1973) -> Part 2 (1972)" in lines[0]

    def test_describes_a_suppression(self):
        library = dict(ROCKY_LIBRARY)
        _ordered, decisions = apply_franchise_ordering(
            [_scored(library, 4, 0.5)], build_franchise_index(library), set()
        )
        line = summarize_decisions(decisions)[0]
        assert "Rocky IV (1985) held back" in line
        assert "begins at Rocky (1976)" in line

    def test_truncation_is_explicit(self):
        lines = summarize_decisions(self._decisions(12), limit=10)
        assert len(lines) == 11
        assert lines[-1] == "... and 2 more"

    def test_no_truncation_marker_when_under_limit(self):
        lines = summarize_decisions(self._decisions(3), limit=10)
        assert len(lines) == 3

    def test_unknown_years_render_as_na(self):
        library = _library(_movie(1, "Part 1", None), _movie(2, "Part 2", None))
        _ordered, decisions = apply_franchise_ordering(
            [_scored(library, 2, 0.5)], build_franchise_index(library), set()
        )
        assert "(N/A)" in summarize_decisions(decisions)[0]
