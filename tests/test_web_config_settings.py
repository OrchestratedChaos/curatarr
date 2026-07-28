"""Tests for the /config/settings screen: tuning.yml weights (with
sum-to-1.0 validation), quality filters, recency decay, rating
multipliers, negative signals, external recommendations, and general/
logging.

#290: the sonarr/radarr/trakt export-safety fields (auto_sync/
user_mode/plex_users) used to ALSO be editable and saved from here,
duplicating the Connections screen's own editable copy of the exact
same fields - saving either page silently reverted whatever the other
had last saved, with no warning. This screen now only ever displays
those fields read-only (Connections is the sole writer) - see
TestSaveNeverTouchesSyncSafety below for the regression coverage."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import yaml

from utils.config import get_update_mode
from utils.labels import DEFAULT_MOVIE_NAME_TEMPLATE, DEFAULT_TV_NAME_TEMPLATE
from web.app import create_app
from web.config_io import module_path


@pytest.fixture
def client(curatarr_web_root):
    app = create_app(project_root=curatarr_web_root)
    app.testing = True
    return app.test_client(), app, curatarr_web_root


@pytest.fixture(autouse=True)
def _no_real_update_check(monkeypatch):
    """This file deliberately writes config.yml files with a non-off
    update_mode (to test the Settings screen's own rendering of the
    effective mode) - that would otherwise make the page's update-banner
    context processor (web/app.py) attempt a real network call on every
    such GET. Neutralize it here regardless of config content; the
    banner itself is covered separately in test_web_update_banner.py."""
    monkeypatch.setattr("web.app.update_available", lambda **kwargs: (None, "0.0.0", False))


def _read_yaml(root, name):
    path = module_path(root, name)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


VALID_FORM = {
    "movies_weight_genre": "0.25",
    "movies_weight_director": "0.05",
    "movies_weight_actor": "0.20",
    "movies_weight_keyword": "0.50",
    "movies_limit_results": "50",
    "movies_min_rating": "5.0",
    "movies_min_vote_count": "50",
    "tv_weight_genre": "0.25",
    "tv_weight_studio": "0.10",
    "tv_weight_actor": "0.20",
    "tv_weight_keyword": "0.45",
    "tv_limit_results": "20",
    "tv_min_rating": "0.0",
    "tv_min_vote_count": "0",
    "recency_days_0_30": "1.0",
    "recency_days_31_90": "0.75",
    "recency_days_91_180": "0.5",
    "recency_days_181_365": "0.25",
    "recency_days_365_plus": "0.1",
    "rating_star_5": "2.5",
    "rating_star_4": "1.7",
    "rating_star_3": "1.0",
    "rating_star_2": "0.4",
    "rating_star_1": "0.2",
    "negsig_bad_ratings_threshold": "3",
    "negsig_bad_ratings_cap_penalty": "0.5",
    "negsig_dropped_min_episodes": "2",
    "negsig_dropped_max_completion": "25",
    "negsig_dropped_penalty_multiplier": "-0.4",
    "ext_movie_limit": "50",
    "ext_show_limit": "20",
    "ext_min_relevance_score": "0.65",
    "ext_min_votes": "50",
    "ext_max_iterations": "5",
    "ext_language": "",
    "general_log_retention_days": "7",
    "logging_level": "INFO",
}


class TestGet:
    def test_renders_defaults(self, client):
        c, app, root = client
        resp = c.get("/config/settings")
        assert resp.status_code == 200
        assert b"Settings / Tuning" in resp.data

    def test_surfaces_sync_safety_warning(self, client):
        c, app, root = client
        resp = c.get("/config/settings")
        assert b"Auto-sync" in resp.data

    def test_defaults_update_mode_to_notify_when_unset(self, client):
        c, app, root = client
        # The shared curatarr_web_root fixture sets update_mode: off (so
        # the rest of the web test suite never makes a real network
        # call) - write a config.yml with no update_mode/auto_update at
        # all to actually exercise the true "neither key present"
        # default from get_update_mode().
        config_path = module_path(root, "config")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write('plex:\n  url: "http://localhost:32400"\nusers:\n  list: "alice, bob"\n')

        resp = c.get("/config/settings")
        assert resp.status_code == 200
        assert b'value="notify" selected' in resp.data

    def test_shows_effective_update_mode_derived_from_legacy_auto_update(self, client):
        """A config.yml with only the legacy auto_update flag (no
        update_mode) must show the *effective* mode - force here - not
        fall back to the notify default, matching get_update_mode()."""
        c, app, root = client
        config_path = module_path(root, "config")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                'plex:\n  url: "http://localhost:32400"\nusers:\n  list: "alice, bob"\ngeneral:\n  auto_update: true\n'
            )

        resp = c.get("/config/settings")
        assert resp.status_code == 200
        assert b'value="force" selected' in resp.data


class TestSave:
    def test_saves_weights_and_quality_filters_to_tuning_yml(self, client):
        c, app, root = client
        resp = c.post("/config/settings", data=VALID_FORM)
        assert resp.status_code == 303

        tuning = _read_yaml(root, "tuning")
        assert tuning["movies"]["weights"]["genre"] == 0.25
        assert tuning["movies"]["weights"]["keyword"] == 0.50
        assert tuning["movies"]["quality_filters"]["min_rating"] == 5.0
        assert tuning["tv"]["weights"]["studio"] == 0.10
        assert tuning["tv"]["limit_results"] == 20

    def test_saves_recency_rating_negsig_external(self, client):
        c, app, root = client
        c.post("/config/settings", data=VALID_FORM)
        tuning = _read_yaml(root, "tuning")

        assert tuning["recency_decay"]["days_0_30"] == 1.0
        assert tuning["rating_multipliers"]["star_5"] == 2.5
        assert tuning["negative_signals"]["bad_ratings"]["threshold"] == 3
        assert tuning["negative_signals"]["dropped_shows"]["penalty_multiplier"] == -0.4
        assert tuning["external_recommendations"]["movie_limit"] == 50

    def test_saves_general_and_logging_to_config_yml(self, client):
        c, app, root = client
        c.post("/config/settings", data=VALID_FORM)
        core = _read_yaml(root, "config")
        assert core["general"]["log_retention_days"] == 7
        assert core["logging"]["level"] == "INFO"

    def test_saves_update_mode_to_config_yml(self, client):
        c, app, root = client
        form = dict(VALID_FORM)
        form["general_update_mode"] = "force"
        c.post("/config/settings", data=form)
        core = _read_yaml(root, "config")
        assert core["general"]["update_mode"] == "force"

    def test_defaults_update_mode_to_notify_when_field_omitted(self, client):
        c, app, root = client
        assert "general_update_mode" not in VALID_FORM
        c.post("/config/settings", data=VALID_FORM)
        core = _read_yaml(root, "config")
        assert core["general"]["update_mode"] == "notify"

    def test_saving_update_mode_preserves_legacy_auto_update_key(self, client):
        """Additive write: saving a new update_mode must not delete a
        pre-existing legacy auto_update key - matches _apply_settings'
        merge-only-the-submitted-keys behavior."""
        c, app, root = client
        config_path = module_path(root, "config")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                'plex:\n  url: "http://localhost:32400"\nusers:\n  list: "alice, bob"\ngeneral:\n  auto_update: true\n'
            )

        form = dict(VALID_FORM)
        form["general_update_mode"] = "off"
        c.post("/config/settings", data=form)

        core = _read_yaml(root, "config")
        # Not core['general']['update_mode'] == 'off' as a raw string:
        # ruamel.yaml (like PyYAML) writes an unquoted `off` per YAML
        # 1.1's boolean literals, so plain yaml.safe_load reads it back
        # as False, not 'off' - get_update_mode() is what normalizes
        # that back to the 'off' mode (see its docstring), so assert
        # the effective mode through it rather than the raw value.
        assert get_update_mode(core) == "off"
        assert core["general"]["auto_update"] is True

    def test_round_trip_preserves_untouched_keys(self, client):
        c, app, root = client
        tuning_path = module_path(root, "tuning")
        os.makedirs(os.path.dirname(tuning_path), exist_ok=True)
        with open(tuning_path, "w", encoding="utf-8") as f:
            f.write("# Curatarr Tuning Configuration\ncollections:\n  add_label: true\n  label_name: Recommended\n")

        c.post("/config/settings", data=VALID_FORM)

        content = open(tuning_path, encoding="utf-8").read()
        assert "# Curatarr Tuning Configuration" in content
        assert "label_name: Recommended" in content


class TestSaveNeverTouchesSyncSafety:
    """#290: Settings must never write sonarr/radarr/trakt.yml's
    auto_sync/user_mode/plex_users - Connections (web/config_connections.py)
    is the sole writer. Before this fix, saving either page silently
    reverted whatever the other had last saved, with no warning - both
    still said "Saved." either way."""

    # Minimal valid Connections form - the fields under test
    # (sonarr_user_mode/plex_users) plus whatever else that screen's
    # own validation requires to accept the submission at all.
    CONNECTIONS_FORM = {
        "plex_url": "http://localhost:32400",
        "plex_token": "",
        "tmdb_api_key": "",
        "tautulli_url": "",
        "tautulli_api_key": "",
        "sonarr_url": "http://localhost:8989",
        "sonarr_api_key": "sonarr-key-123",
        "sonarr_auto_sync": "on",
        "sonarr_user_mode": "combined",
        "sonarr_plex_users": "",
        "radarr_url": "http://localhost:7878",
        "radarr_api_key": "radarr-key-123",
        "radarr_user_mode": "mapping",
        "radarr_plex_users": "bob",
        "trakt_client_id": "client-id-123",
        "trakt_client_secret": "client-secret-123",
        "trakt_user_mode": "mapping",
        "trakt_plex_users": "alice",
    }

    def test_settings_save_does_not_write_sonarr_radarr_trakt_files(self, client):
        c, app, root = client
        sonarr_path = module_path(root, "sonarr")
        radarr_path = module_path(root, "radarr")
        trakt_path = module_path(root, "trakt")
        before = tuple(
            os.path.getmtime(p) if os.path.isfile(p) else None for p in (sonarr_path, radarr_path, trakt_path)
        )

        c.post("/config/settings", data=VALID_FORM)

        after = tuple(
            os.path.getmtime(p) if os.path.isfile(p) else None for p in (sonarr_path, radarr_path, trakt_path)
        )
        assert before == after

    def test_settings_save_does_not_revert_a_connections_save(self, client):
        """The actual #290 clobber scenario end to end: save distinct
        sync-safety values via Connections, then save Settings (its own
        unrelated fields) - the Connections values must survive
        untouched."""
        c, app, root = client
        c.post("/config/connections", data=self.CONNECTIONS_FORM)

        sonarr = _read_yaml(root, "sonarr")
        assert sonarr["auto_sync"] is True
        assert sonarr["user_mode"] == "combined"

        c.post("/config/settings", data=VALID_FORM)

        sonarr_after = _read_yaml(root, "sonarr")
        radarr_after = _read_yaml(root, "radarr")
        assert sonarr_after["auto_sync"] is True
        assert sonarr_after["user_mode"] == "combined"
        assert radarr_after["user_mode"] == "mapping"
        assert radarr_after["plex_users"] == ["bob"]

    def test_settings_screen_renders_sync_safety_read_only(self, client):
        """No <input>/<select> named {svc}_auto_sync/user_mode/
        plex_users on this screen at all - the only way a save here can
        structurally never carry a value for them (see this module's
        own docstring)."""
        c, app, root = client
        c.post("/config/connections", data=self.CONNECTIONS_FORM)
        resp = c.get("/config/settings")
        text = resp.data.decode()
        for svc in ("sonarr", "radarr", "trakt"):
            for field in ("auto_sync", "user_mode", "plex_users"):
                assert f'name="{svc}_{field}"' not in text
        # Still shows the current (Connections-owned) value read-only.
        assert "combined" in text  # sonarr_user_mode from CONNECTIONS_FORM above


class TestCollectionNaming:
    """#286: surfaces collections.movie_name_template/tv_name_template
    (#267/PR#274) on this screen. The existing default must render
    byte-identical to today's output for anyone who never touches this
    - see utils.labels.DEFAULT_MOVIE_NAME_TEMPLATE/DEFAULT_TV_NAME_TEMPLATE,
    which this screen falls back to and this test class imports rather
    than re-typing (avoids the class of bug #261 already covers:
    hand-copied defaults drifting out of sync with the real ones)."""

    def test_get_shows_the_real_default_when_unset(self, client):
        c, app, root = client
        resp = c.get("/config/settings")
        text = resp.data.decode()
        assert f'value="{DEFAULT_MOVIE_NAME_TEMPLATE}"' in text
        assert f'value="{DEFAULT_TV_NAME_TEMPLATE}"' in text

    def test_saves_custom_templates_to_tuning_yml(self, client):
        c, app, root = client
        form = dict(VALID_FORM)
        form["collections_movie_name_template"] = "Movie picks for {user}"
        form["collections_tv_name_template"] = "{media_type} picks for {user}"
        resp = c.post("/config/settings", data=form)
        assert resp.status_code == 303

        tuning = _read_yaml(root, "tuning")
        assert tuning["collections"]["movie_name_template"] == "Movie picks for {user}"
        assert tuning["collections"]["tv_name_template"] == "{media_type} picks for {user}"

    def test_blank_field_saves_the_real_default_rather_than_empty_string(self, client):
        """Leaving the field blank means "use the default" (matches
        config/tuning.example.yml's own documented way to do this by
        deleting the line) - not a literal empty-string template."""
        c, app, root = client
        form = dict(VALID_FORM)
        form["collections_movie_name_template"] = "   "
        form["collections_tv_name_template"] = ""
        c.post("/config/settings", data=form)

        tuning = _read_yaml(root, "tuning")
        assert tuning["collections"]["movie_name_template"] == DEFAULT_MOVIE_NAME_TEMPLATE
        assert tuning["collections"]["tv_name_template"] == DEFAULT_TV_NAME_TEMPLATE

    def test_omitted_fields_also_default(self, client):
        """VALID_FORM itself never sets either field - the baseline
        save path (every other test in this file using VALID_FORM
        as-is) must not blow up or write a blank template."""
        c, app, root = client
        assert "collections_movie_name_template" not in VALID_FORM
        c.post("/config/settings", data=VALID_FORM)

        tuning = _read_yaml(root, "tuning")
        assert tuning["collections"]["movie_name_template"] == DEFAULT_MOVIE_NAME_TEMPLATE
        assert tuning["collections"]["tv_name_template"] == DEFAULT_TV_NAME_TEMPLATE

    def test_invalid_movie_template_rejected_with_visible_error(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad["collections_movie_name_template"] = "Oops {typo}"
        resp = c.post("/config/settings", data=bad)
        assert resp.status_code == 400
        assert b"Invalid template" in resp.data

    def test_invalid_tv_template_rejected_with_visible_error(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad["collections_tv_name_template"] = "Oops {"
        resp = c.post("/config/settings", data=bad)
        assert resp.status_code == 400
        assert b"Invalid template" in resp.data

    def test_invalid_template_does_not_corrupt_existing_tuning_file(self, client):
        c, app, root = client
        c.post("/config/settings", data=VALID_FORM)  # establish a valid baseline
        before = _read_yaml(root, "tuning")

        bad = dict(VALID_FORM)
        bad["collections_movie_name_template"] = "Oops {typo}"
        c.post("/config/settings", data=bad)

        after = _read_yaml(root, "tuning")
        assert after == before

    def test_save_does_not_touch_other_collections_keys(self, client):
        """add_label/label_name/append_usernames/rename_on_template_change/
        private_collections all live under the same collections: section
        but are config-file-only (not owned by this screen) - saving the
        two template fields here must not clobber them, same field-
        scoping guarantee #290 already established for other sections."""
        c, app, root = client
        tuning_path = module_path(root, "tuning")
        with open(tuning_path, "w", encoding="utf-8") as f:
            f.write(
                "collections:\n"
                "  add_label: false\n"
                "  label_name: CustomLabel\n"
                "  append_usernames: false\n"
                "  private_collections: false\n"
            )

        form = dict(VALID_FORM)
        form["collections_movie_name_template"] = "Movie picks for {user}"
        c.post("/config/settings", data=form)

        tuning = _read_yaml(root, "tuning")
        assert tuning["collections"]["movie_name_template"] == "Movie picks for {user}"
        assert tuning["collections"]["add_label"] is False
        assert tuning["collections"]["label_name"] == "CustomLabel"
        assert tuning["collections"]["append_usernames"] is False
        assert tuning["collections"]["private_collections"] is False

    def test_explains_multi_library_suffix_is_appended_separately(self, client):
        c, app, root = client
        resp = c.get("/config/settings")
        assert b"disambiguation suffix" in resp.data


class TestValidation:
    def test_weights_not_summing_to_one_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad["movies_weight_genre"] = "0.9"  # now sums to > 1
        resp = c.post("/config/settings", data=bad)
        assert resp.status_code == 400
        assert b"sum to 1.0" in resp.data

    def test_tv_weights_not_summing_to_one_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad["tv_weight_genre"] = "0.9"
        resp = c.post("/config/settings", data=bad)
        assert resp.status_code == 400

    def test_non_numeric_weight_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad["movies_weight_genre"] = "not-a-number"
        resp = c.post("/config/settings", data=bad)
        assert resp.status_code == 400

    def test_invalid_logging_level_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad["logging_level"] = "VERBOSE"
        resp = c.post("/config/settings", data=bad)
        assert resp.status_code == 400

    def test_invalid_update_mode_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad["general_update_mode"] = "yolo"
        resp = c.post("/config/settings", data=bad)
        assert resp.status_code == 400

    def test_invalid_input_does_not_corrupt_existing_tuning_file(self, client):
        c, app, root = client
        c.post("/config/settings", data=VALID_FORM)  # establish a valid baseline
        before = _read_yaml(root, "tuning")

        bad = dict(VALID_FORM)
        bad["movies_weight_genre"] = "0.9"
        c.post("/config/settings", data=bad)

        after = _read_yaml(root, "tuning")
        assert after == before


class TestNullSection:
    def test_null_general_section_in_hand_edited_yaml_does_not_500(self, client):
        """M2: a bare `general:` line (parses to None, not {}) must not
        500 - it should be treated the same as a missing section."""
        c, app, root = client
        config_path = module_path(root, "config")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write('plex:\n  url: "http://localhost:32400"\ngeneral:\nusers:\n  list: "alice, bob"\n')

        resp = c.post("/config/settings", data=VALID_FORM)
        assert resp.status_code == 303

        core = _read_yaml(root, "config")
        assert core["general"]["log_retention_days"] == 7

    def test_null_negative_signals_section_in_tuning_yml_does_not_500(self, client):
        c, app, root = client
        tuning_path = module_path(root, "tuning")
        os.makedirs(os.path.dirname(tuning_path), exist_ok=True)
        with open(tuning_path, "w", encoding="utf-8") as f:
            f.write("negative_signals:\n")

        resp = c.post("/config/settings", data=VALID_FORM)
        assert resp.status_code == 303

        tuning = _read_yaml(root, "tuning")
        assert tuning["negative_signals"]["bad_ratings"]["threshold"] == 3


class TestScheduleSettings:
    """#264: the Scheduling fieldset on /config/settings - reads/writes
    config.yml's schedule: section, validated via the same
    utils.scheduler.parse_schedule_config() the background thread uses."""

    def test_get_renders_defaults_when_unset(self, client):
        c, app, root = client
        resp = c.get("/config/settings")
        assert resp.status_code == 200
        assert b'name="schedule_time" value="03:00"' in resp.data
        assert b"Scheduler is currently disabled" in resp.data

    def test_saves_enabled_schedule_with_time_and_weekdays(self, client):
        c, app, root = client
        form = dict(VALID_FORM)
        form.update(
            {
                "schedule_enabled": "on",
                "schedule_time": "04:30",
                "schedule_weekday_monday": "on",
                "schedule_weekday_friday": "on",
            }
        )
        resp = c.post("/config/settings", data=form)

        assert resp.status_code == 303
        core = _read_yaml(root, "config")
        assert core["schedule"]["enabled"] is True
        assert core["schedule"]["time"] == "04:30"
        assert set(core["schedule"]["weekdays"]) == {"monday", "friday"}

    def test_no_weekdays_checked_omits_weekdays_key(self, client):
        """Every day - see _apply_settings' own comment for why this is
        an omitted key, not an empty list, on disk."""
        c, app, root = client
        form = dict(VALID_FORM)
        form["schedule_enabled"] = "on"
        form["schedule_time"] = "03:00"
        resp = c.post("/config/settings", data=form)

        assert resp.status_code == 303
        core = _read_yaml(root, "config")
        assert "weekdays" not in core["schedule"]

    def test_disabled_schedule_still_saves_time_for_next_time(self, client):
        c, app, root = client
        form = dict(VALID_FORM)
        form["schedule_time"] = "05:00"
        resp = c.post("/config/settings", data=form)

        assert resp.status_code == 303
        core = _read_yaml(root, "config")
        assert core["schedule"]["enabled"] is False
        assert core["schedule"]["time"] == "05:00"

    def test_invalid_time_rejected_with_field_error(self, client):
        c, app, root = client
        form = dict(VALID_FORM)
        form["schedule_enabled"] = "on"
        form["schedule_time"] = "25:99"
        resp = c.post("/config/settings", data=form)

        assert resp.status_code == 400
        assert b"HH:MM" in resp.data
        core = _read_yaml(root, "config")
        assert core.get("schedule", {}).get("time") != "25:99"  # the bad value never reached disk

    def test_get_shows_next_run_when_enabled(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setenv("TZ", "UTC")
        config_path = module_path(root, "config")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                'plex:\n  url: "http://localhost:32400"\n  token: "not-a-real-token"\n'
                'users:\n  list: "alice"\n'
                'schedule:\n  enabled: true\n  time: "03:00"\n'
            )

        resp = c.get("/config/settings")

        assert resp.status_code == 200
        assert b"Next run" in resp.data
        assert b"UTC" in resp.data

    def test_get_shows_error_for_invalid_saved_schedule(self, client):
        """A hand-edited tuning.yml/config.yml with a bad schedule.time
        must show why, not 500 or silently show nothing (#264:
        describe_next_run() never raises)."""
        c, app, root = client
        config_path = module_path(root, "config")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                'plex:\n  url: "http://localhost:32400"\n  token: "not-a-real-token"\n'
                'users:\n  list: "alice"\n'
                'schedule:\n  enabled: true\n  time: "not-a-time"\n'
            )

        resp = c.get("/config/settings")

        assert resp.status_code == 200
        assert b"Schedule is invalid" in resp.data
