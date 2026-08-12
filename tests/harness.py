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
Deterministic scoring/pipeline harness.

A reusable, committed harness for verifying that a scoring/pipeline
refactor doesn't silently change output. It:

  1. Loads pinned, fully-synthetic fixtures (tests/fixtures/scoring_harness/
     movies_cache_fixture.json, user_profile_fixture.json) - shaped exactly
     like the real cache/all_movies_cache.json and the user_profile dict
     recommenders/movie.py's _calculate_similarity_from_cache builds from
     cache/watched_cache_plex_<user>.json, but with invented titles,
     cast/director names, and keywords. No real Plex data, usernames, or
     watch history - see this module's own git history / CHANGELOG for
     why that's a hard requirement here.
  2. Recomputes similarity scores from scratch via
     utils.scoring.calculate_similarity_score for every fixture movie -
     always a cache-miss recompute, never a profile_hash cache-hit
     shortcut (recommenders/base.py's get_recommendations() normally
     takes the cache-hit path in production, which would mask exactly
     the kind of nondeterminism this harness exists to catch).
  3. Seeds utils.scoring.select_tiered_recommendations's RNG explicitly
     (see that function's `rng` parameter).
  4. Is meant to be run as a subprocess with PYTHONHASHSEED pinned
     (belt-and-braces on top of the explicit seed above - see
     tests/test_harness.py) since Python's string-hash randomization seed
     is fixed at interpreter startup and can't be changed mid-process.

Usage (module, not a script - keeps `python -m` import semantics so
`from tests...` imports resolve the same way pytest collects them):

    PYTHONHASHSEED=0 python -m tests.harness

Prints a JSON document to stdout with per-title exact score bit patterns
(float.hex(), not a rounded display value - a display rounding would mask
the exact float non-associativity this harness's Task 4 use case is
checking for) and the final tiered-selection title order. Two invocations
with the same PYTHONHASHSEED and seed must be byte-identical; see
tests/test_harness.py for the assertions this backs.

---------------------------------------------------------------------------
Profile-builder harness (#273) - run_profile_builders()
---------------------------------------------------------------------------
Issue #273 found FOUR divergent user-profile builders (recommenders/
movie.py's _get_plex_watched_data, recommenders/tv.py's
_get_plex_watched_shows_data, recommenders/base.py's
_get_managed_users_watched_data, recommenders/external.py's
build_user_profile/load_user_profile_from_cache), three with verified
production bugs. This is the PR0 hard gate for that issue: nothing in
that sequence is verifiable without a harness that can actually catch
those bugs first - the existing tests/e2e_plex_fixture.py fixture alone
CANNOT (it emits history XML with no userRating, faithfully reproducing
the real Plex endpoint, and its FakeMediaItem had no userRating attribute
at all before this same PR extended it - see that module's own updated
docstrings).

run_profile_builders() drives all four builders directly (not through
run_recommender_main's CLI wrapper) against that extended fixture and
returns a JSON-serializable snapshot dict, one key per builder call,
capturing every counter key name, magnitude (as float.hex() - same
non-associativity reasoning as Task 4 above), sign (negative-signal
counters), and which dimensions are populated vs empty. `python -m
tests.harness --profile-builders` prints that snapshot; `--write`
(re)writes the committed golden fixture
(tests/fixtures/profile_builder_harness/profile_builder_snapshot.json)
that tests/test_profile_builder_harness.py pins the live snapshot
against. A future #273 PR that intentionally changes one of the four
builders' output regenerates this golden fixture and explains the diff
in that PR's description - see that test module's own docstring.
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.scoring import calculate_similarity_score, select_tiered_recommendations  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "scoring_harness")
PROFILE_BUILDER_FIXTURES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "profile_builder_harness"
)
PROFILE_BUILDER_GOLDEN_FILENAME = "profile_builder_snapshot.json"

# Arbitrary, pinned - not meaningful beyond "always the same value" so
# repeated harness runs are comparable.
DEFAULT_SEED = 20260726
DEFAULT_LIMIT = 15


def load_fixtures():
    """Load the pinned synthetic fixtures. Returns (movies_cache, user_profile)."""
    with open(os.path.join(FIXTURES_DIR, "movies_cache_fixture.json"), encoding="utf-8") as f:
        movies_cache = json.load(f)
    with open(os.path.join(FIXTURES_DIR, "user_profile_fixture.json"), encoding="utf-8") as f:
        user_profile = json.load(f)
    return movies_cache, user_profile


def run(seed: int = DEFAULT_SEED, limit: int = DEFAULT_LIMIT) -> dict:
    """Recompute scores for every fixture movie against the fixture user
    profile (always a from-scratch recompute) and run the tiered/random
    selection with an explicitly seeded RNG.

    Returns a JSON-serializable dict - see module docstring for why
    `score_hex` (float.hex(), the exact bit pattern) is reported alongside
    the plain float.
    """
    movies_cache, user_profile = load_fixtures()
    movies = movies_cache["movies"]

    scored = []
    for rating_key, item in sorted(movies.items()):
        content_info = {
            "genres": item.get("genres", []),
            "directors": item.get("directors", []),
            "cast": item.get("cast", []),
            "language": item.get("language", "N/A"),
            "keywords": item.get("tmdb_keywords", []),
            "vote_count": item.get("vote_count", 0),
            "collection_id": item.get("collection_id"),
        }
        score, _breakdown = calculate_similarity_score(
            content_info=content_info,
            user_profile=user_profile,
            media_type="movie",
        )
        scored.append(
            {
                "rating_key": rating_key,
                "title": item["title"],
                "score": score,
                "score_hex": float(score).hex(),
                "similarity_score": score,
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)

    rng = random.Random(seed)
    selected = select_tiered_recommendations(scored, limit, rng=rng)

    return {
        "seed": seed,
        "limit": limit,
        "num_fixture_movies": len(movies),
        "all_scores": [{"title": s["title"], "score_hex": s["score_hex"]} for s in scored],
        "selected_titles": [s["title"] for s in selected],
    }


def _counters_to_jsonable(counters: dict) -> dict:
    """Convert a #273 builder's returned counters dict (Counter objects,
    a plain int-keyed dict for tv.py's production_companies, and a
    tmdb_ids set) into a deterministic, JSON-serializable snapshot.

    Every value is reported as float.hex() (exact bit pattern - same
    non-associativity reasoning as run()'s score_hex above) rather than a
    rounded display value, sorted by str(key) so int-keyed and str-keyed
    dicts both sort the same way, and sets become sorted lists.
    """
    out: dict = {}
    for key, value in sorted(counters.items(), key=lambda kv: str(kv[0])):
        if isinstance(value, set):
            out[key] = sorted(str(v) for v in value)
        elif isinstance(value, dict):
            out[key] = {str(k): float(v).hex() for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
        else:
            out[key] = value
    return out


def _write_profile_builder_project(root: str, *, managed_users: bool) -> str:
    """Writes a real, on-disk config.yml + tuning.yml + pre-seeded caches
    for a throwaway project root (mirrors tests/test_e2e_pipeline.py's
    own _write_project_root helper), returning the config.yml path.

    managed_users=False: users.list = "alice, bob" - drives movie.py's/
    tv.py's OWN watched-data builders (_get_plex_watched_data /
    _get_plex_watched_shows_data).
    managed_users=True: plex.managed_users = "alice, bob" instead, no
    users.list at all - drives recommenders/base.py's shared
    _get_managed_users_watched_data() (the path taken when no
    users.list is configured).
    """
    import yaml

    os.makedirs(os.path.join(root, "config"), exist_ok=True)
    os.makedirs(os.path.join(root, "cache"), exist_ok=True)
    os.makedirs(os.path.join(root, "logs"), exist_ok=True)

    config_yaml: dict = {
        "plex": {"url": "http://127.0.0.1:32400", "token": "test-harness-token"},
        "tmdb": {"api_key": None},
        "general": {"update_mode": "off", "log_retention_days": 0, "cache_prune": {"enabled": False}},
        "libraries": [
            {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
            {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"},
        ],
    }
    if managed_users:
        config_yaml["plex"]["managed_users"] = "alice, bob"
    else:
        config_yaml["users"] = {"list": "alice, bob"}

    tuning_yaml = {
        "movies": {"quality_filters": {"min_rating": 0.0, "min_vote_count": 0}},
        "tv": {"quality_filters": {"min_rating": 0.0, "min_vote_count": 0}},
        "negative_signals": {"enabled": True, "dropped_shows": {"enabled": False}},
    }

    config_path = os.path.join(root, "config", "config.yml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_yaml, f, sort_keys=False)
    with open(os.path.join(root, "config", "tuning.yml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(tuning_yaml, f, sort_keys=False)

    from tests.e2e_plex_fixture import build_movies_cache_payload, build_shows_cache_payload

    with open(os.path.join(root, "cache", "all_movies_cache.json"), "w", encoding="utf-8") as f:
        json.dump(build_movies_cache_payload(), f)
    with open(os.path.join(root, "cache", "all_shows_cache.json"), "w", encoding="utf-8") as f:
        json.dump(build_shows_cache_payload(), f)

    return config_path


def run_profile_builders() -> dict:
    """Drives all four #273 profile-builder entry points against the
    (extended - see module docstring) tests/e2e_plex_fixture.py catalog
    and returns a JSON-serializable snapshot dict. See module docstring
    for the full rationale.

    Safe to call standalone (`python -m tests.harness --profile-builders`,
    outside pytest) or in-process from a test: explicitly no-ops
    recommenders.base.migrate_legacy_cache_dir for the duration of this
    call rather than relying on tests/conftest.py's autouse fixture of
    the same name (which only applies under pytest) - left un-mocked,
    that function is reachable in exactly this harness's own shape
    (source install, CURATARR_CONFIG_DIR set - see its own docstring)
    and would silently MOVE real production watched_cache_plex_<user>.json
    files out of the real repo's cache/ dir into this function's
    throwaway temp dir, which is then deleted on the way out - i.e.
    permanently destroying real user data. Never touches the real repo's
    cache/ dir, never opens a real socket (utils.plex._capped_get and
    every MyPlexAccount binding this call graph touches are patched to
    the synthetic tests/e2e_plex_fixture.py fakes for the duration).
    """
    import shutil
    import tempfile
    from unittest.mock import patch

    from recommenders import external as external_module
    from recommenders.movie import PlexMovieRecommender
    from recommenders.tv import PlexTVRecommender
    from tests.e2e_plex_fixture import FakeMyPlexAccount, build_fake_plex_server, make_fake_capped_get

    tmp_root = tempfile.mkdtemp(prefix="curatarr_profile_harness_")
    snapshot: dict = {}
    old_cwd_env = os.environ.get("CURATARR_CONFIG_DIR")
    try:
        plex_users_root = os.path.join(tmp_root, "plex_users_project")
        managed_root = os.path.join(tmp_root, "managed_users_project")
        os.makedirs(plex_users_root)
        os.makedirs(managed_root)

        plex_users_config_path = _write_profile_builder_project(plex_users_root, managed_users=False)
        managed_config_path = _write_profile_builder_project(managed_root, managed_users=True)

        fake_plex = build_fake_plex_server()

        with (
            patch("recommenders.base.init_plex", lambda config: fake_plex),
            patch("utils.plex.MyPlexAccount", FakeMyPlexAccount),
            patch("recommenders.base.MyPlexAccount", FakeMyPlexAccount),
            patch("recommenders.external.MyPlexAccount", FakeMyPlexAccount),
            patch("recommenders.base.migrate_legacy_cache_dir", lambda legacy_dir, new_dir: None),
            patch("utils.plex._capped_get", make_fake_capped_get()),
        ):
            # --- (1) & (2): movie.py's / tv.py's own per-user builders ---
            os.environ["CURATARR_CONFIG_DIR"] = plex_users_root
            for username in ("alice", "bob"):
                movie_rec = PlexMovieRecommender(plex_users_config_path, single_user=username)
                snapshot[f"movie_plex_watched_{username}"] = _counters_to_jsonable(movie_rec.watched_data_counters)

                tv_rec = PlexTVRecommender(plex_users_config_path, single_user=username)
                snapshot[f"tv_plex_watched_{username}"] = _counters_to_jsonable(tv_rec.watched_data_counters)

            # --- (3): base.py's shared managed-users builder ---
            os.environ["CURATARR_CONFIG_DIR"] = managed_root
            managed_movie_rec = PlexMovieRecommender(managed_config_path)
            snapshot["movie_managed_users"] = _counters_to_jsonable(managed_movie_rec.watched_data_counters)
            managed_tv_rec = PlexTVRecommender(managed_config_path)
            snapshot["tv_managed_users"] = _counters_to_jsonable(managed_tv_rec.watched_data_counters)

            # --- (4): external.py's load_user_profile_from_cache()
            # (reads the REAL on-disk cache files (1) above just wrote,
            # via each recommender's own __init__-time _save_watched_cache()
            # call). Doesn't read config.yml off disk, so
            # CURATARR_CONFIG_DIR is irrelevant here; a small, standalone
            # config dict is enough. ---
            external_config = {
                "plex": {
                    "url": "http://127.0.0.1:32400",
                    "token": "test-harness-token",
                    "movie_library": "Movies",
                    "tv_library": "TV Shows",
                },
                "tmdb": {"api_key": "test_key"},
                "cache_dir": os.path.join(plex_users_root, "cache"),
                "recency_decay": {"enabled": True},
            }
            for username in ("alice", "bob"):
                for media_type in ("movie", "tv"):
                    loaded = external_module.load_user_profile_from_cache(external_config, username, media_type)
                    snapshot[f"external_load_cache_{media_type}_{username}"] = (
                        _counters_to_jsonable(loaded) if loaded else None
                    )

            # --- (5): external.py's _build_profile_via_recommender()
            # (#273 PR3 - replaces the deleted build_user_profile(),
            # which had bug #3: username was inert). Unlike
            # load_user_profile_from_cache() above, this constructs a
            # REAL PlexMovieRecommender internally (the "shared path" -
            # see its own docstring), resolving config_path via
            # get_project_root(), so CURATARR_CONFIG_DIR must be pointed
            # at the plex_users project again (left set to managed_root
            # by step (3) above). ---
            os.environ["CURATARR_CONFIG_DIR"] = plex_users_root
            for username in ("alice", "bob"):
                built = external_module._build_profile_via_recommender(username, "movie")
                snapshot[f"external_profile_via_recommender_movie_{username}"] = _counters_to_jsonable(built)
    finally:
        if old_cwd_env is None:
            os.environ.pop("CURATARR_CONFIG_DIR", None)
        else:
            os.environ["CURATARR_CONFIG_DIR"] = old_cwd_env
        shutil.rmtree(tmp_root, ignore_errors=True)

    return snapshot


def _profile_builder_golden_path() -> str:
    return os.path.join(PROFILE_BUILDER_FIXTURES_DIR, PROFILE_BUILDER_GOLDEN_FILENAME)


def write_profile_builder_golden(snapshot: dict) -> None:
    """(Re)writes the committed golden snapshot fixture - see
    tests/test_profile_builder_harness.py for what pins against it."""
    os.makedirs(PROFILE_BUILDER_FIXTURES_DIR, exist_ok=True)
    with open(_profile_builder_golden_path(), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
        f.write("\n")


def load_profile_builder_golden() -> dict:
    with open(_profile_builder_golden_path(), encoding="utf-8") as f:
        return json.load(f)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile-builders",
        action="store_true",
        help="Run the #273 profile-builder harness (run_profile_builders()) instead of the scoring harness.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="(--profile-builders only) (re)write the committed golden snapshot fixture instead of printing it.",
    )
    args = parser.parse_args()

    if args.profile_builders:
        snapshot = run_profile_builders()
        if args.write:
            write_profile_builder_golden(snapshot)
            print(f"Wrote golden snapshot ({len(snapshot)} builder calls) to {_profile_builder_golden_path()}")
        else:
            print(json.dumps(snapshot, indent=2, sort_keys=True))
        return

    result = run()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
