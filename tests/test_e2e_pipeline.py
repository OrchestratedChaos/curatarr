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
End-to-end recommendation-pipeline test: config -> Plex fetch -> watched-
history build -> profile -> scoring -> selection -> output/labels.

Unlike every other test in this suite (which mocks the Plex layer per-
function or exercises a single slice), this drives the REAL production
entrypoints - utils.cli.run_recommender_main (movie/TV flagship tests) and
PlexMovieRecommender/PlexTVRecommender.get_recommendations() directly (the
narrower isolation/stability tests) - against a synthetic-but-realistic
Plex library (see tests/e2e_plex_fixture.py), with real config.yml/
tuning.yml files on disk, real YAML loading/merging, real watched-history
XML parsing, real profile-building, real utils.scoring similarity scoring,
and real tiered/random selection.

What's real (unmocked) in every test here:
  - config.yml/tuning.yml loading and merging (utils/config.py's
    load_config/resolve_media_type_overrides/get_libraries_for_media_type)
    from real files on disk, via a real CURATARR_CONFIG_DIR override.
  - The watch-history/account-id HTTP business logic in utils/plex.py
    (get_plex_account_ids, get_watched_movie_count/get_watched_show_count,
    fetch_plex_watch_history_movies/shows) - real XML parsing, real
    per-account matching, real recency timestamps - running against
    synthetic XML (see make_fake_capped_get()) instead of a live server.
  - Profile building (recommenders/movie.py's/tv.py's
    _get_plex_watched_data()/_get_plex_watched_shows_data(),
    utils/counters.py's process_counters_from_cache).
  - Similarity scoring (utils/scoring.py's calculate_similarity_score) and
    tiered/random selection (utils/scoring.py's
    select_tiered_recommendations), with genre exclusion (global +
    per-user) and quality-filter thresholds applied by
    BaseRecommender.get_recommendations() exactly as in production.
  - The per-user loop, single-vs-multi-library resolution, and per-library
    item-fetch sharing in utils.cli.run_recommender_main (for the two
    "_end_to_end" flagship tests below).

What's mocked, and why:
  - recommenders.base.init_plex - replaced with a duck-typed FakePlexServer
    (tests/e2e_plex_fixture.py) instead of a real plexapi.server.PlexServer
    handshake. Its library.section(...).all() serves this test's synthetic
    catalog for real to every real (unmocked) consumer.
  - utils.plex._capped_get / utils.plex.MyPlexAccount - the raw-HTTP/
    plexapi-account seam behind the watch-history functions above; replaced
    with a fake responder serving synthetic-but-real-shaped Plex XML (see
    module docstring in tests/e2e_plex_fixture.py for why this is the
    narrowest defensible seam, not the pipeline logic itself).
  - The movie/show metadata cache (cache/all_movies_cache.json /
    all_shows_cache.json) is pre-seeded so BaseCache.update_cache() takes
    its real "cache is up to date" branch - see tests/e2e_plex_fixture.py's
    module docstring for why this deliberately does NOT exercise TMDB
    enrichment (_process_item's new-item branch), mirroring tests/
    harness.py's own established fixture convention.
  - PlexMovieRecommender.manage_plex_labels / PlexTVRecommender.
    manage_plex_labels - per this test's mandate, the collection/label-
    writing stage must never reach real Plex. Replaced with a plain
    function (not a MagicMock) so `self` is still bound normally, letting
    each test assert on exactly what recommended_items/state WOULD have
    been written, without executing a single line of the real label/
    collection code (FakeSection.search()/FakePlexServer.fetchItem() would
    raise if that code path were ever reached by mistake - see
    tests/e2e_plex_fixture.py).
  - utils.cli.migrate_renamed_plex_users - a distinct feature (Plex-
    account-rename detection), not part of the recommendation pipeline
    under test; tests/test_cli.py's own run_recommender_main tests already
    patch this out for the same reason.

What this test deliberately does NOT cover (see also each test's own
docstring):
  - TMDB metadata enrichment (BaseCache._process_item's new-item /
    _get_tmdb_data path) - the cache is always pre-seeded "up to date" (see
    above). A real Plex/TMDB integration test is a different, larger
    undertaking than this pipeline-wiring test.
  - The actual Plex collection/label write (manage_plex_labels' internals -
    _find_plex_items_for_recs, _build_scored_candidates,
    _update_labels_by_rank, update_plex_collection, content-rating
    filtering via get_max_rating_for_user/is_rating_allowed). Mocked
    wholesale per this test's mandate.
  - TV quality_filters: recommenders/tv.py's ShowCache._process_item was
    fixed to populate "rating"/"vote_count" on show cache entries the same
    way MovieCache always has (see CHANGELOG) - TV quality_filters is no
    longer a production no-op. This fixture's SHOW_CATALOG still doesn't
    carry rating/vote_count values, so tv: quality_filters stays at 0.0/0
    here (a nonzero threshold would exclude every show in *this* catalog,
    same as it would for any pre-fix on-disk show cache) and this test
    asserts genre exclusion for TV but does not claim TV quality-filter
    coverage - that's covered directly by tests/test_base.py and
    tests/test_tv.py instead.
  - Negative-signal ratings/rewatch weighting and TV's dropped-show
    detection (negative_signals.dropped_shows) - explicitly disabled in
    this fixture's config to keep the fake watch-history HTTP layer to a
    single endpoint shape; covered elsewhere by tests/test_movie.py,
    tests/test_tv.py, tests/test_plex.py's own targeted unit tests.
  - Trakt/Tautulli merge paths, huntarr, external recommendations - all
    left at their disabled defaults; out of scope for this pipeline test.
  - True cross-process determinism (PYTHONHASHSEED pinning via a fresh
    interpreter, the way tests/harness.py's subprocess invocation does).
    The seeded-RNG tests below reseed Python's global `random` module
    in-process instead: the only two set()s on this pipeline's hot path
    (watched_ids, excluded_genres) are used solely for O(1) membership
    tests, never iterated in an order-sensitive way, so hash-seed-
    dependent set ordering has no observable effect on this pipeline's
    output - an in-process reseed is a faithful determinism proof here.
"""

import json
import os
import random
import sys
from unittest.mock import patch

import pytest
import yaml

from recommenders.movie import PlexMovieRecommender, process_recommendations as movie_process_recommendations
from recommenders.tv import PlexTVRecommender, process_recommendations as tv_process_recommendations
from tests.e2e_plex_fixture import (
    MOVIE_HORROR_IDS,
    MOVIE_QUALITY_EXCLUDED_IDS,
    MOVIE_ROMANCE_IDS,
    MOVIE_WATCHED_BY,
    SHOW_HORROR_IDS,
    SHOW_WATCHED_BY,
    FakeMyPlexAccount,
    build_fake_plex_server,
    build_movies_cache_payload,
    build_shows_cache_payload,
    make_fake_capped_get,
)
from utils.cli import run_recommender_main

# Fixed, arbitrary seed - only meaningful as "always the same value" so
# repeated runs in the seeded-RNG tests below are comparable (mirrors
# tests/harness.py's own DEFAULT_SEED convention).
E2E_SEED = 20260726


def _write_project_root(tmp_path, *, movies_randomize: bool, tv_randomize: bool) -> str:
    """Writes a real, on-disk config.yml + tuning.yml + pre-seeded caches
    for a throwaway project root, and returns the config.yml path."""
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "cache").mkdir()
    (root / "logs").mkdir()

    config_yaml = {
        "plex": {"url": "http://127.0.0.1:32400", "token": "test-e2e-token"},
        "tmdb": {"api_key": None},
        "users": {
            "list": "alice, bob",
            "preferences": {"bob": {"exclude_genres": ["romance"]}},
        },
        "general": {
            "update_mode": "off",
            "exclude_genre": "horror",
            "log_retention_days": 0,
            "cache_prune": {"enabled": False},
        },
        # Explicit libraries: (rather than the legacy plex.movie_library/
        # tv_library) so loading this config never trips utils/
        # migrate_config.py's needs_migration() auto-migration rewrite.
        "libraries": [
            {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
            {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"},
        ],
    }
    tuning_yaml = {
        "movies": {
            "limit_results": 5,
            "randomize_recommendations": movies_randomize,
            "show_summary": False,
            "quality_filters": {"min_rating": 5.0, "min_vote_count": 50},
            "weights": {"genre": 0.35, "director": 0.05, "actor": 0.15, "keyword": 0.45},
        },
        "tv": {
            "limit_results": 3,
            "randomize_recommendations": tv_randomize,
            # Left at 0.0/0 deliberately - see module docstring: this
            # fixture's SHOW_CATALOG doesn't carry rating/vote_count
            # values, so a nonzero threshold here would exclude every show.
            "quality_filters": {"min_rating": 0.0, "min_vote_count": 0},
            "weights": {"genre": 0.30, "studio": 0.10, "actor": 0.15, "keyword": 0.40, "language": 0.05},
        },
        "collections": {"add_label": True},
        "negative_signals": {"enabled": True, "dropped_shows": {"enabled": False}},
    }

    (root / "config" / "config.yml").write_text(yaml.safe_dump(config_yaml, sort_keys=False), encoding="utf-8")
    (root / "config" / "tuning.yml").write_text(yaml.safe_dump(tuning_yaml, sort_keys=False), encoding="utf-8")
    (root / "cache" / "all_movies_cache.json").write_text(json.dumps(build_movies_cache_payload()), encoding="utf-8")
    (root / "cache" / "all_shows_cache.json").write_text(json.dumps(build_shows_cache_payload()), encoding="utf-8")
    return str(root / "config" / "config.yml")


@pytest.fixture
def e2e_project(tmp_path, monkeypatch):
    """Factory fixture: wires every monkeypatch this E2E test needs (see
    module docstring's "What's mocked" section) and returns a builder
    function so each test can pick its own randomize_recommendations
    setting. Returns the config.yml path."""

    def _build(*, movies_randomize: bool = True, tv_randomize: bool = True) -> str:
        config_path = _write_project_root(tmp_path, movies_randomize=movies_randomize, tv_randomize=tv_randomize)
        project_root = os.path.dirname(os.path.dirname(config_path))
        # The real utils.helpers.get_project_root() (used unpatched by
        # utils.cli.run_recommender_main, and honored by the conftest.py
        # autouse fixtures that patch recommenders.base/external's own
        # bindings) takes this override at top priority - the same
        # mechanism Docker installs use, not a test-only shortcut.
        monkeypatch.setenv("CURATARR_CONFIG_DIR", project_root)

        fake_plex = build_fake_plex_server()
        monkeypatch.setattr("recommenders.base.init_plex", lambda config: fake_plex)
        monkeypatch.setattr("utils.plex.MyPlexAccount", FakeMyPlexAccount)
        monkeypatch.setattr("utils.plex._capped_get", make_fake_capped_get())
        monkeypatch.setattr("utils.cli.migrate_renamed_plex_users", lambda *a, **kw: {})
        return config_path

    return _build


class TestMoviePipelineEndToEnd:
    """Flagship movie test: drives utils.cli.run_recommender_main exactly
    as production's run.sh does (real argv, real per-user loop, real
    library resolution) with recommenders.movie.process_recommendations as
    the real process_func."""

    def test_full_pipeline_via_run_recommender_main(self, e2e_project, monkeypatch):
        config_path = e2e_project(movies_randomize=True, tv_randomize=True)
        random.seed(E2E_SEED)

        captured = {}

        def _fake_manage_plex_labels(self, recommended_items):
            # Plain function (not a MagicMock) so `self` binds normally -
            # see module docstring's "What's mocked" section. Captures
            # exactly what the real label/collection stage would have
            # received, without letting it touch FakeSection/FakePlexServer.
            captured[self.single_user] = {
                "items": list(recommended_items),
                "limit_plex_results": self.limit_plex_results,
                "watched_ids": set(self.watched_ids),
                "watched_genres": set(self.watched_data.get("genres", {})),
            }
            return True

        monkeypatch.setattr(sys, "argv", ["movie.py"])
        with patch.object(PlexMovieRecommender, "manage_plex_labels", new=_fake_manage_plex_labels):
            run_recommender_main(
                media_type="Movie",
                description="Movie Recommendations for Plex (E2E test)",
                process_func=movie_process_recommendations,
                media_type_key="movie",
            )

        assert os.path.exists(config_path)
        assert set(captured) == {"alice", "bob"}
        alice, bob = captured["alice"], captured["bob"]

        # Per-user isolation, verified at its source: each recommender's
        # own watched_ids/profile reflect ONLY that user's Plex history -
        # never the other user's.
        assert alice["watched_ids"] == {int(x) for x in MOVIE_WATCHED_BY["alice"]}
        assert bob["watched_ids"] == {int(x) for x in MOVIE_WATCHED_BY["bob"]}
        assert "comedy" not in alice["watched_genres"] and "romance" not in alice["watched_genres"]
        assert "action" not in bob["watched_genres"] and "sci-fi" not in bob["watched_genres"]

        alice_ids = {str(i["plex_rating_key"]) for i in alice["items"]}
        bob_ids = {str(i["plex_rating_key"]) for i in bob["items"]}

        # Already-watched items excluded (and never leak from one user's
        # history into the other user's recommendations).
        assert alice_ids.isdisjoint(set(MOVIE_WATCHED_BY["alice"]))
        assert bob_ids.isdisjoint(set(MOVIE_WATCHED_BY["bob"]))

        # Global genre exclusion (general.exclude_genre: horror) honored
        # for both users.
        assert alice_ids.isdisjoint(MOVIE_HORROR_IDS)
        assert bob_ids.isdisjoint(MOVIE_HORROR_IDS)

        # Per-user genre exclusion (users.preferences.bob.exclude_genres)
        # honored for bob only - alice has no such restriction.
        assert bob_ids.isdisjoint(MOVIE_ROMANCE_IDS)

        # Quality filter (movies.quality_filters) applied for real, on
        # real per-item cache data - not just inferred from exclusion.
        for item in list(alice["items"]) + list(bob["items"]):
            assert item["rating"] >= 5.0
            assert item["vote_count"] >= 50
            assert "similarity_score" in item  # scoring stage actually ran
            assert "horror" not in item.get("genres", [])

        # Tiered/random selection path actually ran: candidate pool (16
        # eligible movies per user - see tests/e2e_plex_fixture.py) exceeds
        # limit_plex_results for both users.
        assert 0 < len(alice["items"]) <= alice["limit_plex_results"]
        assert 0 < len(bob["items"]) <= bob["limit_plex_results"]
        assert alice["limit_plex_results"] < 16
        assert bob["limit_plex_results"] < 16


class TestTVPipelineEndToEnd:
    """TV mirror of TestMoviePipelineEndToEnd - same real entrypoint, same
    fixture project, the TV media type only."""

    def test_full_pipeline_via_run_recommender_main(self, e2e_project, monkeypatch):
        config_path = e2e_project(movies_randomize=True, tv_randomize=True)
        random.seed(E2E_SEED)

        captured = {}

        def _fake_manage_plex_labels(self, recommended_items):
            captured[self.single_user] = {
                "items": list(recommended_items),
                "limit_plex_results": self.limit_plex_results,
                "watched_ids": set(self.watched_ids),
                "watched_genres": set(self.watched_data.get("genres", {})),
            }
            return True

        monkeypatch.setattr(sys, "argv", ["tv.py"])
        with patch.object(PlexTVRecommender, "manage_plex_labels", new=_fake_manage_plex_labels):
            run_recommender_main(
                media_type="TV Show",
                description="TV Show Recommendations for Plex (E2E test)",
                process_func=tv_process_recommendations,
                media_type_key="tv",
            )

        assert os.path.exists(config_path)
        assert set(captured) == {"alice", "bob"}
        alice, bob = captured["alice"], captured["bob"]

        assert alice["watched_ids"] == {int(x) for x in SHOW_WATCHED_BY["alice"]}
        assert bob["watched_ids"] == {int(x) for x in SHOW_WATCHED_BY["bob"]}
        assert "comedy" not in alice["watched_genres"]
        assert "sci-fi" not in bob["watched_genres"] and "crime" not in bob["watched_genres"]

        alice_ids = {str(i["plex_rating_key"]) for i in alice["items"]}
        bob_ids = {str(i["plex_rating_key"]) for i in bob["items"]}

        assert alice_ids.isdisjoint(set(SHOW_WATCHED_BY["alice"]))
        assert bob_ids.isdisjoint(set(SHOW_WATCHED_BY["bob"]))
        assert alice_ids.isdisjoint(SHOW_HORROR_IDS)
        assert bob_ids.isdisjoint(SHOW_HORROR_IDS)

        for item in list(alice["items"]) + list(bob["items"]):
            assert "similarity_score" in item
            assert "horror" not in item.get("genres", [])

        assert 0 < len(alice["items"]) <= alice["limit_plex_results"]
        assert 0 < len(bob["items"]) <= bob["limit_plex_results"]


class TestMoviePipelineDeterminism:
    """Narrower tests built directly on PlexMovieRecommender.get_recommendations()
    (the pipeline's core, without the CLI/label-writing layers) - isolation
    at the profile level, and reproducibility of both the deterministic
    (randomize_recommendations: false) and seeded-random (tiered) paths."""

    def test_per_user_watched_profile_isolation(self, e2e_project):
        config_path = e2e_project(movies_randomize=False, tv_randomize=False)

        alice = PlexMovieRecommender(config_path, single_user="alice")
        bob = PlexMovieRecommender(config_path, single_user="bob")

        assert alice.watched_ids == {int(x) for x in MOVIE_WATCHED_BY["alice"]}
        assert bob.watched_ids == {int(x) for x in MOVIE_WATCHED_BY["bob"]}

        # alice only ever watched action/sci-fi movies - bob's comedy/
        # romance genres must never appear in her profile, and vice versa.
        alice_genres = set(alice.watched_data.get("genres", {}))
        bob_genres = set(bob.watched_data.get("genres", {}))
        assert alice_genres == {"action", "sci-fi"}
        assert bob_genres == {"comedy", "romance"}

    def test_randomize_false_produces_stable_output(self, e2e_project):
        config_path = e2e_project(movies_randomize=False, tv_randomize=False)
        recommender = PlexMovieRecommender(config_path, single_user="alice")

        first = recommender.get_recommendations()["plex_recommendations"]
        second = recommender.get_recommendations()["plex_recommendations"]

        assert len(first) > 0
        assert [i["plex_rating_key"] for i in first] == [i["plex_rating_key"] for i in second]
        assert [i["similarity_score"] for i in first] == [i["similarity_score"] for i in second]

    def test_tiered_random_selection_is_seed_reproducible(self, e2e_project):
        config_path = e2e_project(movies_randomize=True, tv_randomize=True)
        recommender = PlexMovieRecommender(config_path, single_user="bob")

        random.seed(E2E_SEED)
        first = recommender.get_recommendations()["plex_recommendations"]
        random.seed(E2E_SEED)
        second = recommender.get_recommendations()["plex_recommendations"]

        assert len(first) > 0
        assert [i["plex_rating_key"] for i in first] == [i["plex_rating_key"] for i in second]

    def test_quality_filter_excludes_low_rated_and_low_vote_items(self, e2e_project):
        config_path = e2e_project(movies_randomize=False, tv_randomize=False)
        recommender = PlexMovieRecommender(config_path, single_user="alice")

        recs = recommender.get_recommendations()["plex_recommendations"]
        rec_ids = {str(i["plex_rating_key"]) for i in recs}

        assert rec_ids.isdisjoint(MOVIE_QUALITY_EXCLUDED_IDS)
        assert all(i["rating"] >= 5.0 and i["vote_count"] >= 50 for i in recs)
