"""
Tests for tests/harness.py's #273 profile-builder harness
(run_profile_builders()) - the PR0 hard gate for issue #273 (four
divergent user-profile builders, recommenders/movie.py's
_get_plex_watched_data, recommenders/tv.py's
_get_plex_watched_shows_data, recommenders/base.py's
_get_managed_users_watched_data, recommenders/external.py's
build_user_profile/load_user_profile_from_cache).

TestProfileBuilderHarnessPinsCurrentBehavior pins the CURRENT (bug-
present) output of all four builders against a committed golden
snapshot (tests/fixtures/profile_builder_harness/
profile_builder_snapshot.json) so a future #273 PR's intentional
behavior change shows up as an explicit, reviewed golden-fixture diff
(regenerate via `python -m tests.harness --profile-builders --write`
and explain the diff in that PR's description) rather than silently
passing because nothing was actually asserting on these four functions'
output shape before this PR.

TestProfileBuilderHarnessCatchesTheFourBugs goes one step further and
proves the harness can actually TELL buggy from fixed behavior, not just
freeze whatever it happens to see today - seeded directly by the
verified real-library findings in this issue's own report:
  - Bug #1 (recommenders/movie.py): Plex's history API
    (/status/sessions/history/all) never carries userRating; movie.py's
    _get_plex_watched_data() only ever reads userRating off HISTORY
    items (unlike recommenders/tv.py, which correctly reads it off the
    LIBRARY item) - so a movie profile's negative-signal rating weight
    never fires, even for a movie the user has genuinely rated low.
  - Bug #2 (recommenders/movie.py + tv.py): viewCount/userRating are
    per-account Plex state, but both builders fetch the library
    snapshot through the shared ADMIN token
    (BaseRecommender._get_all_library_items()) - so every configured
    user's profile reads the exact same (here: always-empty) admin view
    instead of their own.
  - Bug #4 (recommenders/base.py): _get_managed_users_watched_data()
    passes no `weight` to process_counters_from_cache() at all - every
    watched item counts exactly 1.0, with no recency/rewatch/rating
    signal whatsoever.
  - Bug #3 (recommenders/external.py): build_user_profile()'s
    `username` parameter has zero effect on its output - the
    MyPlexAccount it constructs to "validate" the user is immediately
    discarded, and the actual scan runs against whatever `plex`
    connection the caller already had.

tests/e2e_plex_fixture.py's MOVIE_PER_USER_LIBRARY_STATE/
SHOW_PER_USER_LIBRARY_STATE (added by this same PR) give alice and bob
genuinely different, nonzero view_count/user_rating values on movies/
shows they've each actually watched - exactly the per-account state the
shared admin-token snapshot (build_fake_plex_server(), used by every
builder here) can never see, and always reports as 0/None for every
item, regardless of user. Without that fixture extension, none of the
four assertions below could ever fail - which is the exact gap this
PR's issue called out: "the existing e2e fixture CANNOT catch these
bugs... it will pin the bug instead of the behavior."
"""

import json

from tests.harness import load_profile_builder_golden, run_profile_builders
from utils import RATING_MULTIPLIER_UNRATED
from utils.scoring import calculate_recency_multiplier, calculate_rewatch_multiplier


class TestProfileBuilderHarnessPinsCurrentBehavior:
    def test_snapshot_matches_committed_golden_fixture(self):
        live = run_profile_builders()
        golden = load_profile_builder_golden()
        assert live == golden, (
            "Live profile-builder output no longer matches the committed golden "
            "snapshot (tests/fixtures/profile_builder_harness/"
            "profile_builder_snapshot.json). If this is an INTENTIONAL change "
            "(e.g. a #273 PR fixing one of the four builders), regenerate it - "
            "`python -m tests.harness --profile-builders --write` - and explain "
            "the diff in that PR's description."
        )

    def test_two_independent_runs_are_byte_identical(self):
        """Determinism: the harness's own recompute must be stable run to
        run - the fixture is fully synthetic/deterministic, nothing in
        this call graph depends on real time-of-run or randomness (the
        one thing that could - recency decay - is pinned to fixture
        timestamps far enough in the past to stay in the same decay
        bucket indefinitely; see tests/e2e_plex_fixture.py)."""
        first = run_profile_builders()
        second = run_profile_builders()
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


class TestProfileBuilderHarnessCatchesTheFourBugs:
    """See module docstring - proves the harness distinguishes buggy from
    fixed behavior, not just freezes whatever it happens to see today."""

    def test_bug1_movie_rating_never_negative_today(self):
        """Bug #1: alice's fixture override gives her a rating of 2.0 on
        movie 102 ("Iron Horizon", genre "action") - a real dislike,
        below DEFAULT_NEGATIVE_THRESHOLD. Today, every one of her
        genre-counter values is still >= 0: movie.py's own
        _get_plex_watched_data() never sees that rating (it only reads
        userRating off Plex history items, which never carry it)."""
        live = run_profile_builders()
        genres = live["movie_plex_watched_alice"]["genres"]
        assert genres, "expected at least one genre counted for alice's movie profile"
        assert all(float.fromhex(v) >= 0 for v in genres.values()), (
            "a genre counter went negative for alice's movie profile - movie.py's "
            "rating-source fix (#273 PR1) may have already landed, which would "
            "make this pinned-bug assertion stale."
        )

    def test_bug2_movie_watched_data_shows_no_rewatch_or_rating_signal_today(self):
        """Bug #2: alice's and bob's fixture overrides give them
        genuinely different, nonzero view_counts/ratings on the movies
        they've each watched (see MOVIE_PER_USER_LIBRARY_STATE) - but
        movie.py's _get_plex_watched_data() only ever reads that state
        through the shared ADMIN-token library snapshot
        (_get_all_library_items()), which reports 0/None for every item
        regardless of user. The only per-movie unit weight in play
        today is therefore RATING_MULTIPLIER_UNRATED (unrated) times
        whatever recency bucket this fixture's watch timestamps fall
        into (tests/e2e_plex_fixture.py's history timestamps are fixed
        at ~2023-11-14 - already >365 days in the past and only growing
        more so, always the oldest/smallest decay bucket) times
        rewatch_multiplier(1) (view_count is never seen at all on this
        legacy path) - every genre-counter value for BOTH users should
        be a plain integer multiple of that one unit."""
        live = run_profile_builders()
        unit = RATING_MULTIPLIER_UNRATED * calculate_recency_multiplier(0, {}) * calculate_rewatch_multiplier(1)
        for username in ("alice", "bob"):
            genres = live[f"movie_plex_watched_{username}"]["genres"]
            assert genres, f"expected at least one genre counted for {username}'s movie profile"
            for value_hex in genres.values():
                value = float.fromhex(value_hex)
                multiple = value / unit
                assert abs(multiple - round(multiple)) < 1e-9, (
                    f"{username}'s genre weight {value} is not a plain multiple of the "
                    f"unrated/oldest-recency-bucket unit weight ({unit}) - per-user "
                    "rewatch/rating weighting may already be wired up correctly (#273 "
                    "PR1), which would make this pinned-bug assertion stale."
                )

    def test_bug4_managed_users_builder_applies_no_weighting_today(self):
        """Bug #4: base.py's _get_managed_users_watched_data() passes no
        `weight` to process_counters_from_cache() - every watched item
        counts exactly 1.0 (no recency/rewatch/rating multiplier at
        all), so every counter value should be a plain positive
        integer."""
        live = run_profile_builders()
        for key in ("movie_managed_users", "tv_managed_users"):
            genres = live[key]["genres"]
            assert genres, f"expected at least one genre counted for {key}"
            for value_hex in genres.values():
                value = float.fromhex(value_hex)
                assert value == int(value), f"{key}: expected an integer (weight=1.0 always), got {value}"

    def test_bug3_external_build_user_profile_ignores_username(self):
        """Bug #3: build_user_profile()'s username parameter has zero
        effect on its output - alice's and bob's calls (against the
        exact same shared admin-token `plex` connection every builder
        here receives) produce byte-identical results."""
        live = run_profile_builders()
        assert live["external_build_user_profile_movie_alice"] == live["external_build_user_profile_movie_bob"]

    def test_external_load_cache_adapter_renames_tmdb_keywords_to_keywords(self):
        """recommenders/external.py's load_user_profile_from_cache() is
        the one adapter that survives #273's later PRs unchanged (PR3
        reuses it) - pin its key-renaming contract directly: the
        on-disk cache key `tmdb_keywords` (utils/counters.py's
        create_empty_counters shape - what every real
        watched_cache_plex_<user>.json on disk actually uses) comes back
        out as `keywords` (recommenders/external.py's own internal
        naming - see #273's PR3/PR4 notes on this exact mismatch)."""
        live = run_profile_builders()
        loaded = live["external_load_cache_movie_alice"]
        assert loaded is not None
        assert "keywords" in loaded
        assert "tmdb_keywords" not in loaded
        assert loaded["keywords"], "expected alice's cached movie keywords to be non-empty"
