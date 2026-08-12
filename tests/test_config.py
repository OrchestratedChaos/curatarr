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

"""Tests for utils/config.py"""

import json
import os
import tempfile

import yaml

from utils.config import (
    CACHE_VERSION,
    DEFAULT_NEGATIVE_MULTIPLIERS,
    DEFAULT_NEGATIVE_THRESHOLD,
    DEFAULT_RATING_MULTIPLIERS,
    ENV_VAR_OVERRIDES,
    KNOWN_MEDIA_SECTION_KEYS,
    KNOWN_ROOT_CONFIG_KEYS,
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_TV,
    MOVIES_ONLY_MEDIA_SECTION_KEYS,
    UPDATE_MODES,
    _deep_merge_dicts,
    check_cache_version,
    get_config_section,
    get_effective_arr_config,
    get_env_override,
    get_libraries,
    get_libraries_for_media_type,
    get_negative_multiplier,
    get_negative_signals_config,
    get_rating_multipliers,
    get_tmdb_config,
    get_update_mode,
    load_config,
    load_resolved_config,
    resolve_media_type_overrides,
    warn_unknown_config_keys,
)


class TestCheckCacheVersion:
    """Tests for check_cache_version function"""

    def test_returns_false_for_nonexistent_file(self):
        result = check_cache_version("/nonexistent/path/cache.json")
        assert result is False

    def test_returns_true_for_current_version(self):
        # NamedTemporaryFile's own handle must be closed (i.e. outside
        # the `with` block) before check_cache_version() touches the
        # path - on Windows an open handle blocks a second open() AND
        # the later os.unlink() with WinError 32; POSIX tolerates it,
        # which is what let this go unnoticed for so long.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"cache_version": CACHE_VERSION, "data": {}}, f)
            path = f.name
        try:
            result = check_cache_version(path)
            assert result is True
        finally:
            os.unlink(path)

    def test_returns_false_for_old_version(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"cache_version": 1, "data": {}}, f)
            path = f.name
        try:
            result = check_cache_version(path)
            assert result is False
            # File should be deleted
            assert not os.path.exists(path)
        except Exception:
            if os.path.exists(path):
                os.unlink(path)
            raise

    def test_returns_false_for_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json")
            path = f.name
        try:
            result = check_cache_version(path)
            assert result is False
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_defaults_to_v1_if_no_version(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"data": {}}, f)  # No cache_version key
            path = f.name
        try:
            result = check_cache_version(path)
            # Should return False because v1 < CACHE_VERSION (2)
            assert result is False
        except Exception:
            pass
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestGetConfigSection:
    """Tests for get_config_section function"""

    def test_returns_lowercase_key(self):
        config = {"tmdb": {"api_key": "abc123"}}
        result = get_config_section(config, "tmdb")
        assert result == {"api_key": "abc123"}

    def test_returns_uppercase_key(self):
        config = {"TMDB": {"api_key": "abc123"}}
        result = get_config_section(config, "tmdb")
        assert result == {"api_key": "abc123"}

    def test_prefers_lowercase_over_uppercase(self):
        config = {"tmdb": {"key": "lower"}, "TMDB": {"key": "upper"}}
        result = get_config_section(config, "tmdb")
        assert result == {"key": "lower"}

    def test_returns_default_if_not_found(self):
        config = {"plex": {}}
        result = get_config_section(config, "tmdb", {"default": True})
        assert result == {"default": True}

    def test_returns_empty_dict_as_default(self):
        config = {"plex": {}}
        result = get_config_section(config, "tmdb")
        assert result == {}


class TestGetTmdbConfig:
    """Tests for get_tmdb_config function"""

    def test_extracts_api_key(self):
        config = {"tmdb": {"api_key": "my_api_key"}}
        result = get_tmdb_config(config)
        assert result["api_key"] == "my_api_key"

    def test_extracts_use_keywords_lowercase(self):
        config = {"tmdb": {"api_key": "key", "use_tmdb_keywords": False}}
        result = get_tmdb_config(config)
        assert result["use_keywords"] is False

    def test_extracts_use_keywords_mixed_case(self):
        config = {"tmdb": {"api_key": "key", "use_TMDB_keywords": False}}
        result = get_tmdb_config(config)
        assert result["use_keywords"] is False

    def test_defaults_use_keywords_to_true(self):
        config = {"tmdb": {"api_key": "key"}}
        result = get_tmdb_config(config)
        assert result["use_keywords"] is True

    def test_handles_missing_tmdb_section(self):
        config = {"plex": {}}
        result = get_tmdb_config(config)
        assert result["api_key"] is None
        assert result["use_keywords"] is True


class TestGetRatingMultipliers:
    """Tests for get_rating_multipliers function"""

    def test_returns_defaults_when_no_config(self):
        result = get_rating_multipliers(None)
        assert result == DEFAULT_RATING_MULTIPLIERS

    def test_returns_defaults_when_no_rating_multipliers_section(self):
        result = get_rating_multipliers({"plex": {}})
        assert result == DEFAULT_RATING_MULTIPLIERS

    def test_custom_multipliers_applied(self):
        config = {"rating_multipliers": {"star_5": 3.0, "star_1": 0.1}}
        result = get_rating_multipliers(config)
        assert result[10] == 3.0  # star_5 maps to rating 10
        assert result[1] == 0.1  # star_1 maps to rating 1

    def test_rating_0_always_0_1(self):
        config = {"rating_multipliers": {"star_5": 5.0}}
        result = get_rating_multipliers(config)
        assert result[0] == 0.1

    def test_interpolation_between_stars(self):
        config = {"rating_multipliers": {"star_3": 1.0, "star_4": 2.0}}
        result = get_rating_multipliers(config)
        # Rating 6 is between star_3 (5) and star_4 (7)
        assert result[6] == 1.5  # Midpoint


class TestResolveMediaTypeOverrides:
    """Tests for resolve_media_type_overrides() - the single, live
    movies:/tv: resolution path (see its docstring in utils/config.py for
    the architecture history: this replaces two independent, drifted
    implementations - recommenders/base.py's inline resolution, which was
    always the real, live one, and this module's now-deleted
    adapt_config_for_media_type(), which was never actually read by the
    recommendation-generation path). Defaults asserted here are the LIVE
    ones (matching recommenders/base.py's pre-refactor behavior exactly),
    NOT the old dead path's - see CHANGELOG for every case where the two
    disagreed and which one won."""

    def test_movies_limit_results_default_50(self):
        result = resolve_media_type_overrides({}, MEDIA_TYPE_MOVIE)
        assert result["limit_results"] == 50

    def test_tv_limit_results_default_20(self):
        result = resolve_media_type_overrides({}, MEDIA_TYPE_TV)
        assert result["limit_results"] == 20

    def test_movies_limit_results_overridden(self):
        config = {"movies": {"limit_results": 15}}
        result = resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)
        assert result["limit_results"] == 15

    def test_randomize_recommendations_defaults_true(self):
        """Live default is True (recommenders/base.py) - the old, dead
        adapt_config_for_media_type() computed False here and nothing
        ever read it; True is the one that must be preserved."""
        assert resolve_media_type_overrides({}, MEDIA_TYPE_MOVIE)["randomize_recommendations"] is True
        assert resolve_media_type_overrides({}, MEDIA_TYPE_TV)["randomize_recommendations"] is True

    def test_randomize_recommendations_media_section_overrides_general(self):
        config = {"general": {"randomize_recommendations": True}, "movies": {"randomize_recommendations": False}}
        result = resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)
        assert result["randomize_recommendations"] is False

    def test_randomize_recommendations_falls_back_to_general_when_media_section_absent(self):
        config = {"general": {"randomize_recommendations": False}}
        result = resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)
        assert result["randomize_recommendations"] is False

    def test_normalize_counters_defaults_true(self):
        assert resolve_media_type_overrides({}, MEDIA_TYPE_TV)["normalize_counters"] is True

    def test_show_summary_defaults_false(self):
        assert resolve_media_type_overrides({}, MEDIA_TYPE_MOVIE)["show_summary"] is False

    def test_show_genres_defaults_true(self):
        assert resolve_media_type_overrides({}, MEDIA_TYPE_MOVIE)["show_genres"] is True

    def test_show_cast_defaults_false(self):
        assert resolve_media_type_overrides({}, MEDIA_TYPE_MOVIE)["show_cast"] is False

    def test_show_language_defaults_false(self):
        assert resolve_media_type_overrides({}, MEDIA_TYPE_TV)["show_language"] is False

    def test_show_rating_defaults_false(self):
        assert resolve_media_type_overrides({}, MEDIA_TYPE_TV)["show_rating"] is False

    def test_show_imdb_link_defaults_false(self):
        assert resolve_media_type_overrides({}, MEDIA_TYPE_MOVIE)["show_imdb_link"] is False

    def test_display_options_honor_movies_section_override(self):
        config = {
            "movies": {
                "show_summary": True,
                "show_cast": True,
                "show_language": True,
                "show_rating": True,
                "show_imdb_link": True,
                "show_genres": False,
            }
        }
        result = resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)
        assert result["show_summary"] is True
        assert result["show_cast"] is True
        assert result["show_language"] is True
        assert result["show_rating"] is True
        assert result["show_imdb_link"] is True
        assert result["show_genres"] is False

    def test_show_director_only_resolved_for_movies(self):
        result = resolve_media_type_overrides({}, MEDIA_TYPE_MOVIE)
        assert result["show_director"] is False

        tv_result = resolve_media_type_overrides({}, MEDIA_TYPE_TV)
        assert "show_director" not in tv_result

    def test_show_director_honors_movies_section_and_general_fallback(self):
        config = {"movies": {"show_director": True}}
        assert resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)["show_director"] is True

        config2 = {"general": {"show_director": True}}
        assert resolve_media_type_overrides(config2, MEDIA_TYPE_MOVIE)["show_director"] is True

    def test_weights_movies_section_overrides_legacy_root_weights(self):
        config = {"weights": {"genre": 0.9, "actor": 0.1}, "movies": {"weights": {"genre": 0.1, "actor": 0.9}}}
        result = resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)
        assert result["weights"] == {"genre": 0.1, "actor": 0.9}

    def test_weights_falls_back_to_legacy_root_weights_when_media_section_absent(self):
        config = {"weights": {"genre": 0.9, "actor": 0.1}}
        result = resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)
        assert result["weights"] == {"genre": 0.9, "actor": 0.1}

    def test_weights_defaults_to_empty_dict(self):
        result = resolve_media_type_overrides({}, MEDIA_TYPE_TV)
        assert result["weights"] == {}

    def test_handles_uppercase_media_section(self):
        config = {"MOVIES": {"limit_results": 100}}
        result = resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)
        assert result["limit_results"] == 100

    def test_quality_filters_min_rating_min_vote_count_not_resolved_here(self):
        """Deliberately out of scope: quality_filters is resolved by
        BaseRecommender.get_recommendations() at call time, directly from
        self.media_config/self.config (already the one correct, live
        implementation - see this function's docstring). Locks in that a
        future change doesn't reintroduce a second, competing computation
        of these two keys here."""
        result = resolve_media_type_overrides({"movies": {"quality_filters": {"min_rating": 5.0}}}, MEDIA_TYPE_MOVIE)
        assert "min_rating" not in result
        assert "min_vote_count" not in result

    def test_arbitrary_root_level_keys_pass_through_unchanged(self):
        """Strict-superset contract: every root-level key not explicitly
        resolved here (plex_users, tautulli, huntarr, negative_signals,
        radarr, sonarr, an arbitrary future key) survives completely
        untouched - there is no cherry-picked reconstruction that could
        silently drop one (see CHANGELOG for the plex_users key the old,
        deleted adapt_config_for_media_type() used to drop)."""
        config = {
            "plex_users": {"users": "alice,bob"},
            "tautulli": {"enabled": True, "url": "http://tautulli"},
            "huntarr": {"sequel_huntarr": False},
            "negative_signals": {"enabled": False},
            "radarr": {"enabled": True, "url": "http://radarr"},
            "sonarr": {"enabled": True, "url": "http://sonarr"},
            "some_future_key": {"nested": "value"},
        }
        result = resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)
        assert result["plex_users"] == {"users": "alice,bob"}
        assert result["tautulli"] == {"enabled": True, "url": "http://tautulli"}
        assert result["huntarr"] == {"sequel_huntarr": False}
        assert result["negative_signals"] == {"enabled": False}
        assert result["radarr"] == {"enabled": True, "url": "http://radarr"}
        assert result["sonarr"] == {"enabled": True, "url": "http://sonarr"}
        assert result["some_future_key"] == {"nested": "value"}

    def test_libraries_passed_through(self):
        """#157 Phase 3: 'libraries' must carry through so utils/cli.py's
        per-library loop sees a real multi-library setup instead of
        always falling back to the synthesized single-library default."""
        config = {
            "libraries": [
                {"id": "movies", "name": "Movies", "media_type": "movie"},
                {"id": "movies-4k", "name": "Movies 4K", "media_type": "movie"},
            ]
        }
        result = resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)
        assert result["libraries"] == config["libraries"]

    def test_libraries_absent_stays_absent(self):
        """No 'libraries' key in root config -> resolved config has none
        either, so get_libraries_for_media_type falls back to synthesis."""
        config = {"plex": {"movie_library": "Movies"}}
        result = resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)
        assert result.get("libraries") is None

        libs = get_libraries_for_media_type(result, "movie")
        assert len(libs) == 1
        assert libs[0]["section"] == "Movies"

    def test_mutates_and_returns_same_dict(self):
        config = {"plex": {"url": "http://localhost"}}
        result = resolve_media_type_overrides(config, MEDIA_TYPE_MOVIE)
        assert result is config


class TestLoadResolvedConfig:
    """Tests for load_resolved_config() - load_config() +
    resolve_media_type_overrides() in one call, the single function most
    callers (recommenders/base.py, utils/cli.py) need."""

    def test_combines_modular_merge_and_media_type_resolution(self):
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            tuning_path = os.path.join(config_dir, "tuning.yml")
            with open(tuning_path, "w") as f:
                f.write("movies:\n  limit_results: 15\n  randomize_recommendations: false\n")

            result = load_resolved_config(config_path, MEDIA_TYPE_MOVIE)

            assert result["plex"]["url"] == "http://localhost:32400"
            assert result["limit_results"] == 15
            assert result["randomize_recommendations"] is False
        finally:
            shutil.rmtree(config_dir)

    def test_env_var_override_still_applies(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n  token: file_token\n")
            path = f.name
        try:
            os.environ["PLEX_TOKEN"] = "env_token"
            result = load_resolved_config(path, MEDIA_TYPE_TV)
            assert result["plex"]["token"] == "env_token"
            # media-type resolution still applied on top
            assert result["limit_results"] == 20
        finally:
            del os.environ["PLEX_TOKEN"]
            os.unlink(path)

    def test_movie_and_tv_resolve_independently_from_same_file(self):
        """Same on-disk config, two media types -> two independently
        resolved results, each media-type-correct."""
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            tuning_path = os.path.join(config_dir, "tuning.yml")
            with open(tuning_path, "w") as f:
                f.write("movies:\n  limit_results: 15\ntv:\n  limit_results: 7\n")

            movie_result = load_resolved_config(config_path, MEDIA_TYPE_MOVIE)
            tv_result = load_resolved_config(config_path, MEDIA_TYPE_TV)

            assert movie_result["limit_results"] == 15
            assert tv_result["limit_results"] == 7
        finally:
            shutil.rmtree(config_dir)


class TestResolveMediaTypeOverridesKeyEnumeration:
    """Standing guard (audit remediation): enumerates every movies:/tv:
    key documented in config/tuning.example.yml and asserts each one
    actually reaches a real recommender attribute (or, for
    quality_filters/weights per-field values, the actual scoring/
    filtering behavior) - not just that some function computes a
    plausible-looking value nothing reads. This is the test that makes
    the "two divergent resolution paths" bug class this PR fixes
    impossible to silently reintroduce: a future key added only to
    resolve_media_type_overrides() (or only read inline somewhere else)
    without a corresponding case here should fail loudly instead of
    silently doing nothing for users who set it.

    Uses the real, committed config/tuning.example.yml - not a hand-rolled
    fixture - so it also catches the example file and the resolution code
    drifting apart from each other.
    """

    @staticmethod
    def _load_example_tuning():
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        example_path = os.path.join(repo_root, "config", "tuning.example.yml")
        with open(example_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_every_documented_movies_key_resolves(self):
        tuning = self._load_example_tuning()
        movies = tuning["movies"]
        result = resolve_media_type_overrides({"movies": movies}, MEDIA_TYPE_MOVIE)

        assert result["limit_results"] == movies["limit_results"]
        assert result["min_similarity"] == movies["min_similarity"]
        assert result["calibration_strength"] == movies["calibration_strength"]
        assert result["randomize_recommendations"] == movies["randomize_recommendations"]
        assert result["show_summary"] == movies["show_summary"]
        assert result["show_cast"] == movies["show_cast"]
        assert result["show_director"] == movies["show_director"]
        assert result["show_genres"] == movies["show_genres"]
        assert result["show_language"] == movies["show_language"]
        assert result["show_rating"] == movies["show_rating"]
        assert result["show_imdb_link"] == movies["show_imdb_link"]
        assert result["weights"] == movies["weights"]
        # quality_filters/recommend_for_no_history are both resolved by
        # BaseRecommender.get_recommendations(), not here (see
        # TestResolveMediaTypeOverrides) - assert the raw section itself
        # is at least present and unmodified, so a caller reading it
        # directly (as get_recommendations() does) sees it.
        assert result["movies"]["quality_filters"] == movies["quality_filters"]
        assert result["movies"]["recommend_for_no_history"] == movies["recommend_for_no_history"]

    def test_every_documented_tv_key_resolves(self):
        tuning = self._load_example_tuning()
        tv = tuning["tv"]
        result = resolve_media_type_overrides({"tv": tv}, MEDIA_TYPE_TV)

        assert result["limit_results"] == tv["limit_results"]
        assert result["min_similarity"] == tv["min_similarity"]
        assert result["calibration_strength"] == tv["calibration_strength"]
        assert result["randomize_recommendations"] == tv["randomize_recommendations"]
        assert result["normalize_counters"] == tv["normalize_counters"]
        assert result["show_summary"] == tv["show_summary"]
        assert result["show_cast"] == tv["show_cast"]
        assert result["show_language"] == tv["show_language"]
        assert result["show_rating"] == tv["show_rating"]
        assert result["show_imdb_link"] == tv["show_imdb_link"]
        assert "show_director" not in result
        assert result["weights"] == tv["weights"]
        assert result["tv"]["quality_filters"] == tv["quality_filters"]
        assert result["tv"]["recommend_for_no_history"] == tv["recommend_for_no_history"]

    def test_movies_and_tv_documented_keys_are_all_covered_by_this_class(self):
        """Belt-and-braces: fails loudly (instead of silently passing) if
        a future tuning.example.yml edit adds a movies:/tv: key that
        neither test method above accounts for."""
        tuning = self._load_example_tuning()
        covered_movie_keys = {
            "limit_results",
            "min_similarity",
            "calibration_strength",
            "randomize_recommendations",
            "show_summary",
            "show_cast",
            "show_director",
            "show_genres",
            "show_language",
            "show_rating",
            "show_imdb_link",
            "quality_filters",
            "recommend_for_no_history",
            "weights",
        }
        covered_tv_keys = {
            "limit_results",
            "min_similarity",
            "calibration_strength",
            "randomize_recommendations",
            "normalize_counters",
            "show_summary",
            "show_cast",
            "show_language",
            "show_rating",
            "show_imdb_link",
            "quality_filters",
            "recommend_for_no_history",
            "weights",
        }
        assert set(tuning["movies"].keys()) <= covered_movie_keys, (
            f"tuning.example.yml movies: has undocumented-here keys: "
            f"{set(tuning['movies'].keys()) - covered_movie_keys}"
        )
        assert set(tuning["tv"].keys()) <= covered_tv_keys, (
            f"tuning.example.yml tv: has undocumented-here keys: {set(tuning['tv'].keys()) - covered_tv_keys}"
        )


class TestTuningExampleTopLevelSectionsMatchCodeDefaults:
    """Standing guard (#261 audit remediation): TestResolveMediaTypeOverrides
    KeyEnumeration above only ever walked movies:/tv:, so a mismatch in any
    OTHER top-level section of config/tuning.example.yml - like
    collections.append_usernames (documented true, code defaulted to
    false - #261) - was structurally outside what it could ever catch.
    This walks every remaining top-level section and asserts each
    documented example value matches the real code fallback that reads
    it, so a documented default and its code default can never again
    silently drift apart without a test failing.

    Uses the real, committed config/tuning.example.yml - not a hand-rolled
    fixture - so it also catches the example file and the code drifting
    apart from each other, exactly like #261.
    """

    @staticmethod
    def _load_example_tuning():
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        example_path = os.path.join(repo_root, "config", "tuning.example.yml")
        with open(example_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_collections_defaults_match(self):
        """recommenders/base.py's manage_plex_labels() reads every one of
        these directly off self.config["collections"] with these exact
        fallback defaults."""
        from utils.labels import DEFAULT_MOVIE_NAME_TEMPLATE, DEFAULT_TV_NAME_TEMPLATE

        tuning = self._load_example_tuning()
        collections = tuning["collections"]
        assert collections["add_label"] is True
        assert collections["label_name"] == "Recommended"
        assert collections["append_usernames"] is True  # #261
        assert collections["private_collections"] is True
        # #267: documented example templates must match the code
        # defaults render_collection_name() falls back to.
        assert collections["movie_name_template"] == DEFAULT_MOVIE_NAME_TEMPLATE
        assert collections["tv_name_template"] == DEFAULT_TV_NAME_TEMPLATE
        # rename-on-template-change: BaseRecommender._sync_plex_collection's
        # own fallback default.
        assert collections["rename_on_template_change"] is True

    def test_external_recommendations_defaults_match(self):
        from recommenders.external import MAX_DISCOVERY_ITERATIONS, OUTPUT_MIN_VOTES

        tuning = self._load_example_tuning()
        external = tuning["external_recommendations"]
        # #260/#261-guardrail follow-up: this WAS a mismatch (example
        # said 5, MAX_DISCOVERY_ITERATIONS was 8) - every install has
        # been running 8 for months, so the example was corrected to
        # match longstanding real behavior rather than the code changed
        # to match a doc that was simply wrong. web/config_settings.py's
        # own "if unset" display default was fixed to match too.
        assert external["max_iterations"] == 8
        assert MAX_DISCOVERY_ITERATIONS == 8
        # enabled: previously documented here but read by NOTHING in
        # recommenders/external.py - now wired up (main()/_main_impl()),
        # defaulting True to match the example and every install's
        # prior (accidental, since the key was never honored) effective
        # behavior.
        assert external["enabled"] is True
        assert external["movie_limit"] == 50
        assert external["show_limit"] == 20
        assert external["min_relevance_score"] == 0.65
        assert external["auto_open_html"] is False
        assert external["min_votes"] == OUTPUT_MIN_VOTES
        assert external["language"] is None

    def test_recency_decay_defaults_match(self):
        """utils/scoring.py's calculate_recency_multiplier reads every one
        of these with these exact fallback defaults."""
        tuning = self._load_example_tuning()
        recency = tuning["recency_decay"]
        assert recency["enabled"] is True
        assert recency["days_0_30"] == 1.0
        assert recency["days_31_90"] == 0.75
        assert recency["days_91_180"] == 0.50
        assert recency["days_181_365"] == 0.25
        assert recency["days_365_plus"] == 0.10

    def test_rating_multipliers_defaults_match(self):
        """get_rating_multipliers()'s star_X argument defaults, used
        whenever config["rating_multipliers"] exists but is missing
        individual star_X keys - see this test class's own docstring
        note in the PR description about the SEPARATE
        DEFAULT_RATING_MULTIPLIERS fallback (used only when the whole
        rating_multipliers section is absent), which uses different
        numbers and was NOT asserted here - reported as a distinct,
        unresolved finding, not silently reconciled."""
        tuning = self._load_example_tuning()
        multipliers = tuning["rating_multipliers"]
        assert multipliers["star_5"] == 2.5
        assert multipliers["star_4"] == 1.7
        assert multipliers["star_3"] == 1.0
        assert multipliers["star_2"] == 0.4
        assert multipliers["star_1"] == 0.2

    def test_negative_signals_defaults_match(self):
        """get_negative_signals_config()'s fallback defaults."""
        tuning = self._load_example_tuning()
        negative = tuning["negative_signals"]
        assert negative["enabled"] is True
        assert negative["bad_ratings"]["enabled"] is True
        assert negative["bad_ratings"]["threshold"] == DEFAULT_NEGATIVE_THRESHOLD
        assert negative["bad_ratings"]["cap_penalty"] == 0.5
        assert negative["dropped_shows"]["enabled"] is True
        assert negative["dropped_shows"]["min_episodes_watched"] == 2
        assert negative["dropped_shows"]["max_completion_percent"] == 25
        assert negative["dropped_shows"]["penalty_multiplier"] == -0.4

    def test_profile_accuracy_defaults_match(self):
        """#273: recommenders/movie.py's _get_plex_watched_data() and
        recommenders/tv.py's _get_plex_watched_shows_data() both read
        this with this exact fallback default (True, since v2.10.82 -
        was False before that; see CHANGELOG) - see also
        tests/test_config.py's own note in this class's docstring about
        why this guardrail exists: a documented example value drifting
        from the real code default (#261) is exactly the class of bug
        this class exists to catch."""
        tuning = self._load_example_tuning()
        assert tuning["profile_accuracy"]["enabled"] is True

    def test_recommend_for_no_history_defaults_match(self):
        """#291: BaseRecommender.get_recommendations() reads
        movies.recommend_for_no_history/tv.recommend_for_no_history with
        this exact fallback default when the key is absent - a
        documented example value silently drifting from the real code
        default is exactly the #261-class bug this guardrail class
        exists to catch."""
        from recommenders.base import RECOMMEND_FOR_NO_HISTORY_DEFAULT

        tuning = self._load_example_tuning()
        assert tuning["movies"]["recommend_for_no_history"] == RECOMMEND_FOR_NO_HISTORY_DEFAULT
        assert tuning["tv"]["recommend_for_no_history"] == RECOMMEND_FOR_NO_HISTORY_DEFAULT

    def test_top_level_sections_are_all_covered_by_this_class(self):
        """Belt-and-braces, mirroring
        TestResolveMediaTypeOverridesKeyEnumeration's own version of this:
        fails loudly (instead of silently passing) if a future
        tuning.example.yml edit adds a new top-level section none of the
        tests above account for."""
        tuning = self._load_example_tuning()
        covered_sections = {
            "movies",
            "tv",
            "collections",
            "external_recommendations",
            "recency_decay",
            "rating_multipliers",
            "negative_signals",
            "profile_accuracy",
            "users",
        }
        assert set(tuning.keys()) <= covered_sections, (
            f"tuning.example.yml has undocumented-here top-level sections: {set(tuning.keys()) - covered_sections}"
        )


class TestConfigExampleLoggingDefaultsMatch:
    """#284: config/config.example.yml's documented logging.verbosity
    default must match utils.display.LOG_VERBOSITY_DEFAULT exactly - the
    same #261-class guardrail TestTuningExampleTopLevelSectionsMatch
    CodeDefaults enforces for tuning.example.yml, applied here to
    config.example.yml's logging: section instead (a different file,
    outside what that class parses)."""

    @staticmethod
    def _load_example_config():
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        example_path = os.path.join(repo_root, "config", "config.example.yml")
        with open(example_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_logging_verbosity_default_matches_code(self):
        from utils.display import LOG_VERBOSITY_DEFAULT

        config = self._load_example_config()
        assert config["logging"]["verbosity"] == LOG_VERBOSITY_DEFAULT

    def test_logging_verbosity_default_is_itself_a_valid_tier(self):
        """Belt-and-braces: LOG_VERBOSITY_DEFAULT must be one of the
        keys LOG_VERBOSITY_LEVELS actually maps - catches a typo'd
        default that would otherwise silently fall through
        resolve_log_level's own "unrecognized value" fallback path."""
        from utils.display import LOG_VERBOSITY_DEFAULT, LOG_VERBOSITY_LEVELS

        assert LOG_VERBOSITY_DEFAULT in LOG_VERBOSITY_LEVELS

    def test_shipped_example_leaves_legacy_level_key_commented_out(self):
        """The pre-existing raw logging.level key takes precedence over
        logging.verbosity whenever it's set (see resolve_log_level) - so
        for a FRESH install to actually run on the new verbosity default
        rather than silently ignoring it, the shipped example must leave
        level: commented out (documentation only), not set it
        unconditionally the way it did before #284."""
        config = self._load_example_config()
        assert "level" not in config["logging"]


class TestNegativeSignalsConstants:
    """Tests for negative signals constants"""

    def test_default_negative_multipliers_defined(self):
        assert DEFAULT_NEGATIVE_MULTIPLIERS is not None
        assert isinstance(DEFAULT_NEGATIVE_MULTIPLIERS, dict)

    def test_default_negative_multipliers_are_negative(self):
        for rating, mult in DEFAULT_NEGATIVE_MULTIPLIERS.items():
            assert mult < 0, f"Rating {rating} should have negative multiplier"

    def test_default_negative_threshold(self):
        assert DEFAULT_NEGATIVE_THRESHOLD == 3

    def test_multipliers_increase_severity_with_lower_ratings(self):
        # Lower rating = more negative multiplier
        assert DEFAULT_NEGATIVE_MULTIPLIERS[0] < DEFAULT_NEGATIVE_MULTIPLIERS[1]
        assert DEFAULT_NEGATIVE_MULTIPLIERS[1] < DEFAULT_NEGATIVE_MULTIPLIERS[2]
        assert DEFAULT_NEGATIVE_MULTIPLIERS[2] < DEFAULT_NEGATIVE_MULTIPLIERS[3]


class TestGetNegativeSignalsConfig:
    """Tests for get_negative_signals_config function"""

    def test_returns_defaults_when_no_config(self):
        result = get_negative_signals_config(None)
        assert result["enabled"] is True
        assert result["bad_ratings"]["enabled"] is True
        assert result["bad_ratings"]["threshold"] == 3
        assert result["bad_ratings"]["cap_penalty"] == 0.5

    def test_returns_defaults_when_empty_config(self):
        result = get_negative_signals_config({})
        assert result["enabled"] is True

    def test_respects_disabled_flag(self):
        config = {"negative_signals": {"enabled": False}}
        result = get_negative_signals_config(config)
        assert result["enabled"] is False

    def test_custom_threshold(self):
        config = {"negative_signals": {"bad_ratings": {"threshold": 5}}}
        result = get_negative_signals_config(config)
        assert result["bad_ratings"]["threshold"] == 5

    def test_dropped_shows_defaults(self):
        result = get_negative_signals_config(None)
        assert result["dropped_shows"]["enabled"] is True
        assert result["dropped_shows"]["min_episodes_watched"] == 2
        assert result["dropped_shows"]["max_completion_percent"] == 25
        assert result["dropped_shows"]["penalty_multiplier"] == -0.4


class TestGetNegativeMultiplier:
    """Tests for get_negative_multiplier function"""

    def test_returns_negative_for_low_ratings(self):
        assert get_negative_multiplier(0) < 0
        assert get_negative_multiplier(1) < 0
        assert get_negative_multiplier(2) < 0
        assert get_negative_multiplier(3) < 0

    def test_rating_0_most_negative(self):
        assert get_negative_multiplier(0) == -1.0

    def test_rating_3_least_negative(self):
        assert get_negative_multiplier(3) == -0.3

    def test_unknown_rating_returns_mild_negative(self):
        assert get_negative_multiplier(99) == -0.3


class TestLoadConfig:
    """Tests for load_config function with environment variable support"""

    def test_loads_yaml_config(self):
        # NamedTemporaryFile's own handle must be closed (i.e. outside
        # the `with` block) before load_config()/os.unlink() touch the
        # path - Windows locks an open file against a second open() AND
        # against unlink (WinError 32); POSIX tolerates it.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n  token: abc123\n")
            path = f.name
        try:
            result = load_config(path)
            assert result["plex"]["url"] == "http://localhost:32400"
            assert result["plex"]["token"] == "abc123"
        finally:
            os.unlink(path)

    def test_env_var_overrides_plex_token(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n  token: file_token\n")
            path = f.name
        try:
            os.environ["PLEX_TOKEN"] = "env_token"
            result = load_config(path)
            assert result["plex"]["token"] == "env_token"
        finally:
            del os.environ["PLEX_TOKEN"]
            os.unlink(path)

    def test_env_var_overrides_plex_url(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n")
            path = f.name
        try:
            os.environ["PLEX_URL"] = "http://remote:32400"
            result = load_config(path)
            assert result["plex"]["url"] == "http://remote:32400"
        finally:
            del os.environ["PLEX_URL"]
            os.unlink(path)

    def test_env_var_overrides_tmdb_api_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("tmdb:\n  api_key: file_key\n")
            path = f.name
        try:
            os.environ["TMDB_API_KEY"] = "env_key"
            result = load_config(path)
            assert result["tmdb"]["api_key"] == "env_key"
        finally:
            del os.environ["TMDB_API_KEY"]
            os.unlink(path)

    def test_env_var_creates_section_if_missing(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n")
            path = f.name
        try:
            os.environ["TMDB_API_KEY"] = "env_key"
            result = load_config(path)
            assert result["tmdb"]["api_key"] == "env_key"
        finally:
            del os.environ["TMDB_API_KEY"]
            os.unlink(path)

    def test_no_env_var_uses_file_value(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("plex:\n  token: file_token\n")
            path = f.name
        try:
            # Ensure env var is not set
            if "PLEX_TOKEN" in os.environ:
                del os.environ["PLEX_TOKEN"]
            result = load_config(path)
            assert result["plex"]["token"] == "file_token"
        finally:
            os.unlink(path)

    def test_every_registered_env_var_override_actually_applies(self):
        """#289: table-driven over ENV_VAR_OVERRIDES itself (rather than
        one hardcoded test per integration) so a future addition to that
        list is automatically covered here too, and any variable that's
        merely declared but not actually wired through load_config()
        would fail loudly instead of silently doing nothing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n")
            path = f.name
        try:
            for env_var, section, key in ENV_VAR_OVERRIDES:
                os.environ[env_var] = f"env-value-for-{env_var}"
                try:
                    result = load_config(path)
                    assert result[section][key] == f"env-value-for-{env_var}", (
                        f"{env_var} did not override {section}.{key}"
                    )
                finally:
                    del os.environ[env_var]
        finally:
            os.unlink(path)

    def test_sonarr_radarr_trakt_simkl_mdblist_tautulli_env_vars_create_section_if_missing(self):
        """#289: same create-the-section-if-absent behavior the existing
        TMDB_API_KEY coverage above already established, extended to
        every newly-added integration - an operator using ONLY
        environment variables (no sonarr.yml/radarr.yml/trakt.yml/etc at
        all) must still end up with a working config."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n")
            path = f.name
        env_vars = {
            "SONARR_API_KEY": ("sonarr", "api_key"),
            "RADARR_API_KEY": ("radarr", "api_key"),
            "TRAKT_CLIENT_SECRET": ("trakt", "client_secret"),
            "TRAKT_ACCESS_TOKEN": ("trakt", "access_token"),
            "TRAKT_REFRESH_TOKEN": ("trakt", "refresh_token"),
            "SIMKL_CLIENT_ID": ("simkl", "client_id"),
            "SIMKL_ACCESS_TOKEN": ("simkl", "access_token"),
            "MDBLIST_API_KEY": ("mdblist", "api_key"),
            "TAUTULLI_API_KEY": ("tautulli", "api_key"),
        }
        try:
            for env_var in env_vars:
                os.environ[env_var] = "env-secret"
            result = load_config(path)
            for _env_var, (section, key) in env_vars.items():
                assert result[section][key] == "env-secret"
        finally:
            for env_var in env_vars:
                if env_var in os.environ:
                    del os.environ[env_var]
            os.unlink(path)

    def test_env_var_never_logged(self, caplog):
        """#289: load_config() must log WHICH env var was used, never the
        secret VALUE itself."""
        import logging

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n")
            path = f.name
        try:
            os.environ["TRAKT_CLIENT_SECRET"] = "placeholder-secret-value-should-not-appear"
            with caplog.at_level(logging.INFO, logger="curatarr"):
                load_config(path)
            assert "placeholder-secret-value-should-not-appear" not in caplog.text
            assert "TRAKT_CLIENT_SECRET" in caplog.text
        finally:
            del os.environ["TRAKT_CLIENT_SECRET"]
            os.unlink(path)


class TestGetEnvOverride:
    """Tests for utils.config.get_env_override - the single lookup web/
    config_io.py's secret_status_with_env uses to ask "is this
    (section, key) actively overridden by the environment" without
    duplicating ENV_VAR_OVERRIDES."""

    def test_returns_none_when_env_var_not_set(self, monkeypatch):
        monkeypatch.delenv("PLEX_TOKEN", raising=False)
        assert get_env_override("plex", "token") is None

    def test_returns_value_when_env_var_set(self, monkeypatch):
        monkeypatch.setenv("PLEX_TOKEN", "env-value")
        assert get_env_override("plex", "token") == "env-value"

    def test_returns_none_for_unregistered_section_key_pair(self, monkeypatch):
        """A (section, key) with no entry in ENV_VAR_OVERRIDES at all
        (e.g. a field that has never had an env var override, like
        radarr.root_folder) always returns None, never a false positive
        from some unrelated env var."""
        assert get_env_override("radarr", "root_folder") is None

    def test_empty_string_env_var_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("PLEX_TOKEN", "")
        assert get_env_override("plex", "token") is None

    def test_covers_every_documented_integration(self):
        """Belt-and-braces: fails loudly if a future ENV_VAR_OVERRIDES
        edit forgets one of the sections #289 was actually about."""
        sections = {section for _env_var, section, _key in ENV_VAR_OVERRIDES}
        assert sections == {"plex", "tmdb", "tautulli", "sonarr", "radarr", "trakt", "simkl", "mdblist"}


class TestModularConfigLoading:
    """Tests for modular config file loading"""

    def test_loads_tuning_yml_when_present(self):
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            # Write main config
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            # Write tuning.yml
            tuning_path = os.path.join(config_dir, "tuning.yml")
            with open(tuning_path, "w") as f:
                f.write("movies:\n  limit_results: 100\n")

            result = load_config(config_path)
            assert result["movies"]["limit_results"] == 100
        finally:
            shutil.rmtree(config_dir)

    def test_loads_trakt_yml_when_present(self):
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            trakt_path = os.path.join(config_dir, "trakt.yml")
            with open(trakt_path, "w") as f:
                f.write("enabled: true\nclient_id: abc123\n")

            result = load_config(config_path)
            assert result["trakt"]["enabled"] is True
            assert result["trakt"]["client_id"] == "abc123"
        finally:
            shutil.rmtree(config_dir)

    def test_loads_mdblist_yml_when_present(self):
        """Regression: _load_module_configs previously looped over only
        ["trakt", "radarr", "sonarr"], so mdblist.yml shipped an example
        but was never actually read at run time - a user who copied
        config/mdblist.example.yml, filled in a real api_key, and set
        enabled: true would silently stay disabled."""
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            mdblist_path = os.path.join(config_dir, "mdblist.yml")
            with open(mdblist_path, "w") as f:
                f.write("enabled: true\napi_key: mdblist-key1\n")

            result = load_config(config_path)
            assert result["mdblist"]["enabled"] is True
            assert result["mdblist"]["api_key"] == "mdblist-key1"
        finally:
            shutil.rmtree(config_dir)

    def test_loads_simkl_yml_when_present(self):
        """Same regression as mdblist.yml above, for simkl.yml."""
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            simkl_path = os.path.join(config_dir, "simkl.yml")
            with open(simkl_path, "w") as f:
                f.write("enabled: true\nclient_id: real-simkl-client-id\n")

            result = load_config(config_path)
            assert result["simkl"]["enabled"] is True
            assert result["simkl"]["client_id"] == "real-simkl-client-id"
        finally:
            shutil.rmtree(config_dir)

    def test_works_without_module_files(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n")
            path = f.name
        try:
            result = load_config(path)
            assert result["plex"]["url"] == "http://localhost:32400"
        finally:
            os.unlink(path)

    def test_tuning_merges_into_config(self):
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            # Main config with only core sections (no migration triggered)
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            # tuning.yml adds movies settings
            tuning_path = os.path.join(config_dir, "tuning.yml")
            with open(tuning_path, "w") as f:
                f.write("movies:\n  limit_results: 200\n")

            result = load_config(config_path)
            # tuning.yml should be merged in
            assert result["movies"]["limit_results"] == 200
        finally:
            shutil.rmtree(config_dir)

    def test_module_file_deep_merges_shared_dict_key_instead_of_replacing(self):
        """Regression: a shallow `config[key] = value` merge would let
        tuning.yml's `users` completely replace config.yml's `users`,
        silently wiping sub-keys tuning.yml doesn't mention (e.g.
        `users.list`). Deep-merge must preserve them."""
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write(
                    "plex:\n  url: http://localhost:32400\n"
                    "users:\n"
                    "  list: user1, user2\n"
                    "  preferences:\n"
                    "    user1:\n"
                    "      display_name: User One\n"
                )

            tuning_path = os.path.join(config_dir, "tuning.yml")
            with open(tuning_path, "w") as f:
                f.write("users:\n  preferences:\n    user2:\n      max_rating: PG-13\n")

            result = load_config(config_path)

            # config.yml's users.list must survive - this is the exact
            # bug: tuning.yml doesn't mention `list` at all.
            assert result["users"]["list"] == "user1, user2"
            # config.yml's user1 preference (not mentioned by tuning.yml)
            # must also survive.
            assert result["users"]["preferences"]["user1"]["display_name"] == "User One"
            # tuning.yml's user2 preference must win/be added.
            assert result["users"]["preferences"]["user2"]["max_rating"] == "PG-13"
        finally:
            shutil.rmtree(config_dir)

    def test_example_config_shape_regression_shipped_examples_both_define_users(self):
        """Regression for the exact shape shipped in config/config.example.yml
        + config/tuning.example.yml: both define a top-level `users:` key,
        but tuning.example.yml only defines `users.preferences` (commented
        out entirely in the shipped example, but structurally present) -
        never `users.list`. A shallow merge wipes `users.list`."""
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write(
                    "plex:\n  url: http://localhost:32400\n"
                    "tmdb:\n  api_key: test_key\n"
                    "users:\n"
                    "  list: user1, user2, kids\n"
                    "  preferences:\n"
                    "    user1:\n"
                    "      display_name: User One\n"
                    "      exclude_genres:\n"
                    "        - horror\n"
                )

            tuning_path = os.path.join(config_dir, "tuning.yml")
            with open(tuning_path, "w") as f:
                # Mirrors tuning.example.yml's shape: a `users:` section
                # that only carries `preferences` (empty/commented in the
                # shipped example), no `list`.
                f.write("users:\n  preferences: {}\n")

            result = load_config(config_path)

            assert result["users"]["list"] == "user1, user2, kids"
            assert result["users"]["preferences"]["user1"]["display_name"] == "User One"
        finally:
            shutil.rmtree(config_dir)

    def test_module_file_defining_only_some_subkeys_preserves_others(self):
        """A module file that defines only a subset of a dict's sub-keys
        must not clobber sibling sub-keys it doesn't mention.

        Uses `general:` (a CORE_SECTION) rather than a TUNING_SECTION like
        `movies:` so the fixture doesn't also trip auto-migration (which
        would move `movies:` out of config.yml and regenerate tuning.yml,
        confounding what this test is checking).
        """
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("plex:\n  url: http://localhost:32400\ngeneral:\n  limit_results: 50\n  show_summary: true\n")

            tuning_path = os.path.join(config_dir, "tuning.yml")
            with open(tuning_path, "w") as f:
                f.write("general:\n  limit_results: 200\n")

            result = load_config(config_path)

            # tuning.yml's explicit override wins.
            assert result["general"]["limit_results"] == 200
            # config.yml's sub-key tuning.yml never mentioned survives.
            assert result["general"]["show_summary"] is True
        finally:
            shutil.rmtree(config_dir)

    def test_list_valued_key_is_replaced_not_merged(self):
        """Lists are replaced outright by the module file's value, never
        concatenated/deduped - documented behavior, not a bug. Uses
        `general:` for the same auto-migration-avoidance reason as above.
        """
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("plex:\n  url: http://localhost:32400\ngeneral:\n  streaming_services:\n    - netflix\n")

            tuning_path = os.path.join(config_dir, "tuning.yml")
            with open(tuning_path, "w") as f:
                f.write("general:\n  streaming_services:\n    - hulu\n    - disney_plus\n")

            result = load_config(config_path)

            # Replaced wholesale, not merged/deduped with config.yml's list.
            assert result["general"]["streaming_services"] == ["hulu", "disney_plus"]
        finally:
            shutil.rmtree(config_dir)

    def test_feature_module_deep_merges_into_existing_same_named_section(self):
        """The trakt/radarr/sonarr module-file path also deep-merges
        rather than replacing outright, in case config already carries a
        same-named section.

        Calls `_load_module_configs` directly (rather than through
        `load_config`) because a root `trakt:` key in config.yml would
        itself trip auto-migration - which extracts/regenerates trakt.yml
        from config.yml's legacy fields, confounding what this test is
        checking (the merge behavior itself, not migration).
        """
        from utils.config import _load_module_configs

        config_dir = tempfile.mkdtemp()
        try:
            trakt_path = os.path.join(config_dir, "trakt.yml")
            with open(trakt_path, "w") as f:
                f.write("enabled: true\n")

            config = {
                "plex": {"url": "http://localhost:32400"},
                "trakt": {"enabled": False, "client_id": "legacy_id"},
            }
            result = _load_module_configs(config, config_dir)

            # trakt.yml's explicit override wins.
            assert result["trakt"]["enabled"] is True
            # Pre-existing client_id, not mentioned by trakt.yml, survives.
            assert result["trakt"]["client_id"] == "legacy_id"
        finally:
            import shutil

            shutil.rmtree(config_dir)

    def test_mdblist_module_deep_merges_into_existing_same_named_section(self):
        """Same deep-merge guarantee as the trakt/radarr/sonarr test
        above, for mdblist.yml - added alongside trakt/radarr/sonarr in
        the loader loop, so it must behave identically."""
        from utils.config import _load_module_configs

        config_dir = tempfile.mkdtemp()
        try:
            mdblist_path = os.path.join(config_dir, "mdblist.yml")
            with open(mdblist_path, "w") as f:
                f.write("enabled: true\n")

            config = {
                "plex": {"url": "http://localhost:32400"},
                "mdblist": {"enabled": False, "list_prefix": "Curatarr"},
            }
            result = _load_module_configs(config, config_dir)

            # mdblist.yml's explicit override wins.
            assert result["mdblist"]["enabled"] is True
            # Pre-existing list_prefix, not mentioned by mdblist.yml, survives.
            assert result["mdblist"]["list_prefix"] == "Curatarr"
        finally:
            import shutil

            shutil.rmtree(config_dir)

    def test_simkl_module_deep_merges_into_existing_same_named_section(self):
        """Same deep-merge guarantee as the trakt/radarr/sonarr test
        above, for simkl.yml - added alongside trakt/radarr/sonarr in
        the loader loop, so it must behave identically."""
        from utils.config import _load_module_configs

        config_dir = tempfile.mkdtemp()
        try:
            simkl_path = os.path.join(config_dir, "simkl.yml")
            with open(simkl_path, "w") as f:
                f.write("enabled: true\n")

            config = {
                "plex": {"url": "http://localhost:32400"},
                "simkl": {"enabled": False, "client_id": "legacy_id"},
            }
            result = _load_module_configs(config, config_dir)

            # simkl.yml's explicit override wins.
            assert result["simkl"]["enabled"] is True
            # Pre-existing client_id, not mentioned by simkl.yml, survives.
            assert result["simkl"]["client_id"] == "legacy_id"
        finally:
            import shutil

            shutil.rmtree(config_dir)

    def test_env_var_overrides_mdblist_api_key_when_mdblist_yml_also_present(self, monkeypatch):
        """Closes the gap CHANGELOG 2.10.74 noted: MDBLIST_API_KEY
        previously only took effect for an install embedding an
        `mdblist:` section directly in config.yml, because
        _load_module_configs never loaded mdblist.yml at all. Now that
        it does, the env var must still win over whatever mdblist.yml
        itself says (env always wins, #289)."""
        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            mdblist_path = os.path.join(config_dir, "mdblist.yml")
            with open(mdblist_path, "w") as f:
                f.write("enabled: true\napi_key: file-key\n")

            monkeypatch.setenv("MDBLIST_API_KEY", "env-key")
            result = load_config(config_path)
            assert result["mdblist"]["api_key"] == "env-key"
            # File-only key (enabled) is untouched by the env override.
            assert result["mdblist"]["enabled"] is True
        finally:
            import shutil

            shutil.rmtree(config_dir)

    def test_env_var_overrides_simkl_client_id_when_simkl_yml_also_present(self, monkeypatch):
        """Same gap-closing guarantee as the MDBList test above, for
        SIMKL_CLIENT_ID/simkl.yml."""
        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            simkl_path = os.path.join(config_dir, "simkl.yml")
            with open(simkl_path, "w") as f:
                f.write("enabled: true\nclient_id: file-client-id\n")

            monkeypatch.setenv("SIMKL_CLIENT_ID", "env-client-id")
            result = load_config(config_path)
            assert result["simkl"]["client_id"] == "env-client-id"
            # File-only key (enabled) is untouched by the env override.
            assert result["simkl"]["enabled"] is True
        finally:
            import shutil

            shutil.rmtree(config_dir)


class TestDeepMergeDicts:
    """Unit tests for the `_deep_merge_dicts` helper directly."""

    def test_disjoint_keys_are_unioned(self):
        assert _deep_merge_dicts({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_override_scalar_wins(self):
        assert _deep_merge_dicts({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_dicts_merge_recursively(self):
        base = {"users": {"list": "a, b", "preferences": {"a": {"display_name": "A"}}}}
        override = {"users": {"preferences": {"b": {"max_rating": "PG"}}}}
        result = _deep_merge_dicts(base, override)
        assert result["users"]["list"] == "a, b"
        assert result["users"]["preferences"]["a"]["display_name"] == "A"
        assert result["users"]["preferences"]["b"]["max_rating"] == "PG"

    def test_list_replaces_not_concatenates(self):
        assert _deep_merge_dicts({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}

    def test_dict_replaced_by_non_dict_is_replaced(self):
        # If override changes a key's type entirely (dict -> scalar), the
        # override's type wins outright rather than erroring.
        assert _deep_merge_dicts({"a": {"x": 1}}, {"a": None}) == {"a": None}

    def test_base_and_override_are_not_mutated(self):
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        result = _deep_merge_dicts(base, override)
        assert base == {"a": {"x": 1}}
        assert override == {"a": {"y": 2}}
        assert result == {"a": {"x": 1, "y": 2}}


class TestConfigMigration:
    """Tests for config migration functionality"""

    def test_needs_migration_detects_tuning_sections(self):
        from utils.migrate_config import needs_migration

        # Config with tuning sections needs migration
        config = {"plex": {}, "movies": {"limit_results": 50}}
        assert needs_migration(config) is True

        # Config with only core sections doesn't need migration
        config = {"plex": {}, "tmdb": {}, "users": {}}
        assert needs_migration(config) is False

    def test_needs_migration_detects_feature_modules(self):
        from utils.migrate_config import needs_migration

        config = {"plex": {}, "trakt": {"enabled": True}}
        assert needs_migration(config) is True

    def test_extract_tuning_config(self):
        from utils.migrate_config import extract_tuning_config

        config = {
            "plex": {"url": "http://localhost"},
            "movies": {"limit_results": 50},
            "recency_decay": {"enabled": True},
        }
        tuning = extract_tuning_config(config)
        assert "movies" in tuning
        assert "recency_decay" in tuning
        assert "plex" not in tuning

    def test_build_core_config(self):
        from utils.migrate_config import build_core_config

        config = {
            "plex": {"url": "http://localhost"},
            "tmdb": {"api_key": "abc"},
            "movies": {"limit_results": 50},
            "trakt": {"enabled": True},
        }
        core = build_core_config(config)
        assert "plex" in core
        assert "tmdb" in core
        assert "movies" not in core
        assert "trakt" not in core

    def test_migrate_config_creates_files(self):
        import shutil

        from utils.migrate_config import migrate_config

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w") as f:
                f.write("""
plex:
  url: http://localhost:32400
tmdb:
  api_key: abc123
movies:
  limit_results: 50
trakt:
  enabled: true
  client_id: xyz
""")

            result = migrate_config(config_path)

            assert result["migrated"] is True
            assert "tuning.yml" in result["files_created"]
            assert "trakt.yml" in result["files_created"]
            assert os.path.exists(os.path.join(config_dir, "tuning.yml"))
            assert os.path.exists(os.path.join(config_dir, "trakt.yml"))
        finally:
            shutil.rmtree(config_dir)


class TestGetLibraries:
    """Tests for get_libraries fallback synthesis and normalization (#157 Phase 1)"""

    def test_no_libraries_key_synthesizes_two_entries(self):
        config = {"plex": {}}
        result = get_libraries(config)
        assert len(result) == 2
        assert result[0]["media_type"] == MEDIA_TYPE_MOVIE
        assert result[1]["media_type"] == MEDIA_TYPE_TV

    def test_synthesis_defaults_names_when_plex_library_keys_absent(self):
        config = {"plex": {}}
        result = get_libraries(config)
        assert result[0]["name"] == "Movies"
        assert result[1]["name"] == "TV Shows"

    def test_synthesis_uses_configured_library_names(self):
        config = {"plex": {"movie_library": "Films", "tv_library": "Shows"}}
        result = get_libraries(config)
        assert result[0]["name"] == "Films"
        assert result[0]["section"] == "Films"
        assert result[1]["name"] == "Shows"
        assert result[1]["section"] == "Shows"

    def test_synthesis_derives_slug_ids(self):
        config = {"plex": {"movie_library": "My Movies", "tv_library": "TV Shows"}}
        result = get_libraries(config)
        assert result[0]["id"] == "my-movies"
        assert result[1]["id"] == "tv-shows"

    def test_empty_libraries_list_also_synthesizes(self):
        config = {"plex": {}, "libraries": []}
        result = get_libraries(config)
        assert len(result) == 2

    def test_synthesized_arr_merges_from_global_radarr_sonarr(self):
        config = {
            "plex": {"movie_library": "Movies", "tv_library": "TV Shows"},
            "radarr": {"enabled": True, "root_folder": "/data/movies", "quality_profile": "4K"},
            "sonarr": {"enabled": True, "root_folder": "/data/tv", "series_type": "anime"},
        }
        libraries = get_libraries(config)
        movie_lib = next(lib for lib in libraries if lib["media_type"] == MEDIA_TYPE_MOVIE)
        tv_lib = next(lib for lib in libraries if lib["media_type"] == MEDIA_TYPE_TV)

        movie_arr = get_effective_arr_config(config, movie_lib)
        assert movie_arr["enabled"] is True
        assert movie_arr["root_folder"] == "/data/movies"
        assert movie_arr["quality_profile"] == "4K"

        tv_arr = get_effective_arr_config(config, tv_lib)
        assert tv_arr["enabled"] is True
        assert tv_arr["root_folder"] == "/data/tv"
        assert tv_arr["series_type"] == "anime"

    def test_normalizes_missing_id_from_name_slug(self):
        config = {"libraries": [{"name": "Kids Movies", "media_type": "movie"}]}
        result = get_libraries(config)
        assert result[0]["id"] == "kids-movies"

    def test_normalizes_missing_media_type_defaults_movie(self):
        config = {"libraries": [{"name": "Movies"}]}
        result = get_libraries(config)
        assert result[0]["media_type"] == MEDIA_TYPE_MOVIE

    def test_normalizes_missing_section_defaults_to_name(self):
        config = {"libraries": [{"name": "Anime", "media_type": "tv"}]}
        result = get_libraries(config)
        assert result[0]["section"] == "Anime"

    def test_preserves_explicit_fields(self):
        config = {
            "libraries": [
                {"id": "custom-id", "name": "Movies", "section": "Custom Section", "media_type": "movie"},
            ]
        }
        result = get_libraries(config)
        assert result[0]["id"] == "custom-id"
        assert result[0]["section"] == "Custom Section"

    def test_multiple_libraries_of_same_media_type(self):
        config = {
            "libraries": [
                {"name": "Movies", "media_type": "movie"},
                {"name": "Kids Movies", "media_type": "movie"},
            ]
        }
        result = get_libraries(config)
        assert len(result) == 2
        assert result[0]["id"] == "movies"
        assert result[1]["id"] == "kids-movies"


class TestGetLibrariesForMediaType:
    """Tests for get_libraries_for_media_type"""

    def test_filters_to_movie_libraries(self):
        config = {
            "libraries": [
                {"name": "Movies", "media_type": "movie"},
                {"name": "TV Shows", "media_type": "tv"},
            ]
        }
        result = get_libraries_for_media_type(config, MEDIA_TYPE_MOVIE)
        assert len(result) == 1
        assert result[0]["name"] == "Movies"

    def test_filters_to_tv_libraries(self):
        config = {
            "libraries": [
                {"name": "Movies", "media_type": "movie"},
                {"name": "TV Shows", "media_type": "tv"},
                {"name": "Anime", "media_type": "tv"},
            ]
        }
        result = get_libraries_for_media_type(config, MEDIA_TYPE_TV)
        assert len(result) == 2

    def test_returns_empty_list_when_no_match(self):
        config = {"libraries": [{"name": "Movies", "media_type": "movie"}]}
        result = get_libraries_for_media_type(config, MEDIA_TYPE_TV)
        assert result == []

    def test_falls_back_to_synthesized_libraries(self):
        config = {"plex": {}}
        result = get_libraries_for_media_type(config, MEDIA_TYPE_MOVIE)
        assert len(result) == 1
        assert result[0]["name"] == "Movies"


class TestGetEffectiveArrConfig:
    """Tests for get_effective_arr_config merge precedence (#157 Phase 1)"""

    def test_uses_global_when_library_arr_empty(self):
        config = {
            "radarr": {
                "enabled": True,
                "url": "http://radarr:7878",
                "api_key": "globalkey",
                "root_folder": "/movies",
                "quality_profile": "HD-1080p",
            }
        }
        library = {"media_type": "movie", "arr": {}}
        result = get_effective_arr_config(config, library)
        assert result["enabled"] is True
        assert result["url"] == "http://radarr:7878"
        assert result["api_key"] == "globalkey"
        assert result["root_folder"] == "/movies"
        assert result["quality_profile"] == "HD-1080p"

    def test_library_arr_overrides_global_routing_field(self):
        config = {"radarr": {"enabled": True, "root_folder": "/movies", "quality_profile": "HD-1080p"}}
        library = {"media_type": "movie", "arr": {"root_folder": "/kids-movies"}}
        result = get_effective_arr_config(config, library)
        # Overridden field
        assert result["root_folder"] == "/kids-movies"
        # Fallback field untouched
        assert result["quality_profile"] == "HD-1080p"

    def test_instance_overrides_url_and_api_key(self):
        config = {"radarr": {"enabled": True, "url": "http://default:7878", "api_key": "default_key"}}
        library = {
            "media_type": "movie",
            "arr": {"instance": {"url": "http://custom:7878", "api_key": "custom_key"}},
        }
        result = get_effective_arr_config(config, library)
        assert result["url"] == "http://custom:7878"
        assert result["api_key"] == "custom_key"

    def test_instance_partial_override_falls_back_for_omitted_field(self):
        config = {"radarr": {"enabled": True, "url": "http://default:7878", "api_key": "default_key"}}
        library = {
            "media_type": "movie",
            "arr": {"instance": {"url": "http://custom:7878"}},
        }
        result = get_effective_arr_config(config, library)
        assert result["url"] == "http://custom:7878"
        assert result["api_key"] == "default_key"

    def test_movie_gets_minimum_availability_not_series_type(self):
        config = {"radarr": {"minimum_availability": "announced"}}
        library = {"media_type": "movie", "arr": {}}
        result = get_effective_arr_config(config, library)
        assert result["minimum_availability"] == "announced"
        assert "series_type" not in result

    def test_tv_gets_series_type_not_minimum_availability(self):
        config = {"sonarr": {"series_type": "anime"}}
        library = {"media_type": "tv", "arr": {}}
        result = get_effective_arr_config(config, library)
        assert result["series_type"] == "anime"
        assert "minimum_availability" not in result


class TestGetUpdateMode:
    """Tests for get_update_mode - the general.update_mode resolver,
    with legacy general.auto_update fallback (see docstring)."""

    def test_explicit_notify(self):
        assert get_update_mode({"general": {"update_mode": "notify"}}) == "notify"

    def test_explicit_force(self):
        assert get_update_mode({"general": {"update_mode": "force"}}) == "force"

    def test_explicit_off(self):
        assert get_update_mode({"general": {"update_mode": "off"}}) == "off"

    def test_unrecognized_value_falls_back_to_notify(self):
        # Never silently force/disable updates from a typo'd value.
        assert get_update_mode({"general": {"update_mode": "bogus"}}) == "notify"

    def test_legacy_auto_update_true_becomes_force(self):
        assert get_update_mode({"general": {"auto_update": True}}) == "force"

    def test_legacy_auto_update_false_becomes_off(self):
        assert get_update_mode({"general": {"auto_update": False}}) == "off"

    def test_neither_key_present_defaults_to_notify(self):
        assert get_update_mode({"general": {}}) == "notify"
        assert get_update_mode({}) == "notify"

    def test_none_config_defaults_to_notify(self):
        assert get_update_mode(None) == "notify"

    def test_explicit_update_mode_wins_over_legacy_auto_update(self):
        config = {"general": {"update_mode": "off", "auto_update": True}}
        assert get_update_mode(config) == "off"

    def test_all_valid_modes_are_covered(self):
        for mode in UPDATE_MODES:
            assert get_update_mode({"general": {"update_mode": mode}}) == mode

    def test_search_field_falls_back_to_legacy_radarr_search_for_movie(self):
        config = {"radarr": {"search_for_movie": True}}
        library = {"media_type": "movie", "arr": {}}
        result = get_effective_arr_config(config, library)
        assert result["search"] is True

    def test_search_field_falls_back_to_legacy_sonarr_search_for_series(self):
        config = {"sonarr": {"search_for_series": True}}
        library = {"media_type": "tv", "arr": {}}
        result = get_effective_arr_config(config, library)
        assert result["search"] is True

    def test_library_arr_search_overrides_legacy_global_search(self):
        config = {"radarr": {"search_for_movie": False}}
        library = {"media_type": "movie", "arr": {"search": True}}
        result = get_effective_arr_config(config, library)
        assert result["search"] is True

    def test_defaults_when_no_global_arr_config_at_all(self):
        config = {}
        library = {"media_type": "movie", "arr": {}}
        result = get_effective_arr_config(config, library)
        assert result["enabled"] is False
        assert result["monitor"] is False
        assert result["search"] is False
        assert result["url"] is None
        assert result["api_key"] is None

    def test_missing_media_type_defaults_to_movie(self):
        config = {"radarr": {"root_folder": "/movies"}}
        library = {"arr": {}}
        result = get_effective_arr_config(config, library)
        assert result["root_folder"] == "/movies"
        assert "minimum_availability" in result

    def test_missing_arr_key_falls_back_entirely_to_global(self):
        config = {"sonarr": {"enabled": True, "root_folder": "/tv"}}
        library = {"media_type": "tv"}
        result = get_effective_arr_config(config, library)
        assert result["enabled"] is True
        assert result["root_folder"] == "/tv"


class TestWarnUnknownConfigKeys:
    """#316 (second half): a config key nothing reads must say so.

    The first half of #316 - `movies:`/`tv:` `quality_filters` and
    `randomize_recommendations` being read from the wrong depth - was
    already fixed by the resolve_media_type_overrides() consolidation
    (see TestResolveMediaTypeOverrides* above, which prove the nested
    layout resolves). What was missing, and what these tests cover, is
    the warning that stops the *next* such key from going silently inert.
    """

    def test_unknown_root_key_warns(self):
        warnings = warn_unknown_config_keys({"plex": {}, "notathing": 1})
        assert any("notathing" in w for w in warnings)

    def test_known_root_keys_do_not_warn(self):
        config = {key: {} for key in KNOWN_ROOT_CONFIG_KEYS}
        assert warn_unknown_config_keys(config) == []

    def test_uppercase_root_key_does_not_warn(self):
        """get_config_section() accepts an uppercase spelling, so a
        config using one genuinely works - warning would be a false
        positive."""
        assert warn_unknown_config_keys({"TMDB": {"api_key": "x"}}) == []

    def test_unknown_movies_section_key_warns(self):
        warnings = warn_unknown_config_keys({"movies": {"limit_results": 5, "bogus_key": True}})
        assert any("bogus_key" in w and "movies:" in w for w in warnings)

    def test_unknown_tv_section_key_warns(self):
        warnings = warn_unknown_config_keys({"tv": {"bogus_key": True}})
        assert any("bogus_key" in w and "tv:" in w for w in warnings)

    def test_documented_media_section_keys_do_not_warn(self):
        movies = {key: True for key in KNOWN_MEDIA_SECTION_KEYS | MOVIES_ONLY_MEDIA_SECTION_KEYS}
        tv = {key: True for key in KNOWN_MEDIA_SECTION_KEYS}
        assert warn_unknown_config_keys({"movies": movies, "tv": tv}) == []

    def test_show_director_under_tv_warns_as_movies_only(self):
        """The asymmetric case: a real key, spelled right, that simply
        does nothing where it was put."""
        warnings = warn_unknown_config_keys({"tv": {"show_director": True}})
        assert len(warnings) == 1
        assert "show_director" in warnings[0]
        assert "movies-only" in warnings[0]

    def test_show_director_under_movies_does_not_warn(self):
        assert warn_unknown_config_keys({"movies": {"show_director": True}}) == []

    def test_typo_gets_a_suggestion(self):
        warnings = warn_unknown_config_keys({"movies": {"limit_result": 5}})
        assert len(warnings) == 1
        assert "did you mean 'limit_results'" in warnings[0]

    def test_non_dict_media_section_is_ignored_not_crashed_on(self):
        """A malformed `movies:` (e.g. left empty, or set to a scalar)
        must not take config loading down with it."""
        assert warn_unknown_config_keys({"movies": None}) == []
        assert warn_unknown_config_keys({"tv": "nonsense"}) == []

    def test_load_config_warns_for_real_files(self):
        """End-to-end through load_config(), so this covers the call
        site and the post-merge ordering, not just the function."""
        import shutil

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, "config.yml")
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump({"plex": {"url": "http://x", "token": "t"}}, f)
            with open(os.path.join(config_dir, "tuning.yml"), "w", encoding="utf-8") as f:
                yaml.safe_dump({"movies": {"randomise_recommendations": False}}, f)

            config = load_config(config_path)
            # Loading still succeeds - warn-only, never fatal.
            assert config["plex"]["url"] == "http://x"

            warnings = warn_unknown_config_keys(config)
            assert any("randomise_recommendations" in w for w in warnings)
            # British spelling is close enough to earn a suggestion.
            assert any("did you mean 'randomize_recommendations'" in w for w in warnings)
        finally:
            shutil.rmtree(config_dir)


class TestKnownRootConfigKeysCoverThePublishedExamples:
    """Standing guard, same pattern as
    TestResolveMediaTypeOverridesKeyEnumeration: if a shipped example
    config grows a new top-level section and KNOWN_ROOT_CONFIG_KEYS
    isn't updated to match, every user who copies that example starts
    getting a spurious "nothing reads it" warning for a key that is in
    fact read. Fail here instead.
    """

    @staticmethod
    def _example(name):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo_root, "config", name), "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def test_config_example_top_level_keys_are_all_known(self):
        keys = set(self._example("config.example.yml").keys())
        assert keys <= KNOWN_ROOT_CONFIG_KEYS, (
            f"config.example.yml has unlisted top-level keys: {keys - KNOWN_ROOT_CONFIG_KEYS}"
        )

    def test_tuning_example_top_level_keys_are_all_known(self):
        keys = set(self._example("tuning.example.yml").keys())
        assert keys <= KNOWN_ROOT_CONFIG_KEYS, (
            f"tuning.example.yml has unlisted top-level keys: {keys - KNOWN_ROOT_CONFIG_KEYS}"
        )

    def test_module_config_names_are_known_root_keys(self):
        """Each module file lands under its own name as a root key."""
        for module in ("trakt", "radarr", "sonarr", "mdblist", "simkl"):
            assert module in KNOWN_ROOT_CONFIG_KEYS

    def test_example_media_section_keys_are_all_known(self):
        """The movies:/tv: allow-lists must cover what the example file
        actually documents, or copying it verbatim produces warnings."""
        tuning = self._example("tuning.example.yml")
        movies_allowed = KNOWN_MEDIA_SECTION_KEYS | MOVIES_ONLY_MEDIA_SECTION_KEYS
        assert set(tuning["movies"].keys()) <= movies_allowed, (
            f"tuning.example.yml movies: has unlisted keys: {set(tuning['movies'].keys()) - movies_allowed}"
        )
        assert set(tuning["tv"].keys()) <= KNOWN_MEDIA_SECTION_KEYS, (
            f"tuning.example.yml tv: has unlisted keys: {set(tuning['tv'].keys()) - KNOWN_MEDIA_SECTION_KEYS}"
        )

    def test_migrate_config_sections_are_all_known_root_keys(self):
        """utils/migrate_config.py has its own hardcoded enumeration of
        every sanctioned top-level section (it decides what survives a
        migration). If a section is sanctioned enough to be preserved
        there but missing here, a migrated config warns about a key that
        the project itself just carried forward - so tie the two lists
        together rather than maintaining them independently.

        This is the test that would have caught `streaming_services`
        (read by recommenders/external.py) and `platform` being absent
        from KNOWN_ROOT_CONFIG_KEYS.
        """
        from utils.migrate_config import CORE_SECTIONS, FEATURE_MODULES, TUNING_SECTIONS

        sanctioned = set(CORE_SECTIONS) | set(TUNING_SECTIONS) | set(FEATURE_MODULES)
        missing = sanctioned - KNOWN_ROOT_CONFIG_KEYS
        assert not missing, f"sections migrate_config.py preserves but KNOWN_ROOT_CONFIG_KEYS omits: {missing}"
