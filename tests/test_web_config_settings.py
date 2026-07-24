"""Tests for the /config/settings screen: tuning.yml weights (with
sum-to-1.0 validation), quality filters, recency decay, rating
multipliers, negative signals, external recommendations, general/
logging, and the sonarr/radarr/trakt export-safety toggles."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import yaml

from utils.config import get_update_mode
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
    monkeypatch.setattr('web.app.update_available', lambda **kwargs: (None, '0.0.0', False))


def _read_yaml(root, name):
    path = module_path(root, name)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


VALID_FORM = {
    'movies_weight_genre': '0.25', 'movies_weight_director': '0.05',
    'movies_weight_actor': '0.20', 'movies_weight_keyword': '0.50',
    'movies_limit_results': '50', 'movies_min_rating': '5.0', 'movies_min_vote_count': '50',

    'tv_weight_genre': '0.25', 'tv_weight_studio': '0.10',
    'tv_weight_actor': '0.20', 'tv_weight_keyword': '0.45',
    'tv_limit_results': '20', 'tv_min_rating': '0.0', 'tv_min_vote_count': '0',

    'recency_days_0_30': '1.0', 'recency_days_31_90': '0.75', 'recency_days_91_180': '0.5',
    'recency_days_181_365': '0.25', 'recency_days_365_plus': '0.1',

    'rating_star_5': '2.5', 'rating_star_4': '1.7', 'rating_star_3': '1.0',
    'rating_star_2': '0.4', 'rating_star_1': '0.2',

    'negsig_bad_ratings_threshold': '3', 'negsig_bad_ratings_cap_penalty': '0.5',
    'negsig_dropped_min_episodes': '2', 'negsig_dropped_max_completion': '25',
    'negsig_dropped_penalty_multiplier': '-0.4',

    'ext_movie_limit': '50', 'ext_show_limit': '20', 'ext_min_relevance_score': '0.65',
    'ext_min_votes': '50', 'ext_max_iterations': '5', 'ext_language': '',

    'general_log_retention_days': '7',
    'logging_level': 'INFO',

    'sonarr_user_mode': 'mapping', 'sonarr_plex_users': 'alice',
    'radarr_user_mode': 'mapping', 'radarr_plex_users': 'alice',
    'trakt_user_mode': 'mapping', 'trakt_plex_users': 'alice',
}


class TestGet:
    def test_renders_defaults(self, client):
        c, app, root = client
        resp = c.get('/config/settings')
        assert resp.status_code == 200
        assert b'Settings / Tuning' in resp.data

    def test_surfaces_sync_safety_warning(self, client):
        c, app, root = client
        resp = c.get('/config/settings')
        assert b'Auto-sync' in resp.data

    def test_defaults_update_mode_to_notify_when_unset(self, client):
        c, app, root = client
        # The shared curatarr_web_root fixture sets update_mode: off (so
        # the rest of the web test suite never makes a real network
        # call) - write a config.yml with no update_mode/auto_update at
        # all to actually exercise the true "neither key present"
        # default from get_update_mode().
        config_path = module_path(root, 'config')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write('plex:\n  url: "http://localhost:32400"\n'
                     'users:\n  list: "alice, bob"\n')

        resp = c.get('/config/settings')
        assert resp.status_code == 200
        assert b'value="notify" selected' in resp.data

    def test_shows_effective_update_mode_derived_from_legacy_auto_update(self, client):
        """A config.yml with only the legacy auto_update flag (no
        update_mode) must show the *effective* mode - force here - not
        fall back to the notify default, matching get_update_mode()."""
        c, app, root = client
        config_path = module_path(root, 'config')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write('plex:\n  url: "http://localhost:32400"\n'
                     'users:\n  list: "alice, bob"\n'
                     'general:\n  auto_update: true\n')

        resp = c.get('/config/settings')
        assert resp.status_code == 200
        assert b'value="force" selected' in resp.data


class TestSave:
    def test_saves_weights_and_quality_filters_to_tuning_yml(self, client):
        c, app, root = client
        resp = c.post('/config/settings', data=VALID_FORM)
        assert resp.status_code == 303

        tuning = _read_yaml(root, 'tuning')
        assert tuning['movies']['weights']['genre'] == 0.25
        assert tuning['movies']['weights']['keyword'] == 0.50
        assert tuning['movies']['quality_filters']['min_rating'] == 5.0
        assert tuning['tv']['weights']['studio'] == 0.10
        assert tuning['tv']['limit_results'] == 20

    def test_saves_recency_rating_negsig_external(self, client):
        c, app, root = client
        c.post('/config/settings', data=VALID_FORM)
        tuning = _read_yaml(root, 'tuning')

        assert tuning['recency_decay']['days_0_30'] == 1.0
        assert tuning['rating_multipliers']['star_5'] == 2.5
        assert tuning['negative_signals']['bad_ratings']['threshold'] == 3
        assert tuning['negative_signals']['dropped_shows']['penalty_multiplier'] == -0.4
        assert tuning['external_recommendations']['movie_limit'] == 50

    def test_saves_general_and_logging_to_config_yml(self, client):
        c, app, root = client
        c.post('/config/settings', data=VALID_FORM)
        core = _read_yaml(root, 'config')
        assert core['general']['log_retention_days'] == 7
        assert core['logging']['level'] == 'INFO'

    def test_saves_update_mode_to_config_yml(self, client):
        c, app, root = client
        form = dict(VALID_FORM)
        form['general_update_mode'] = 'force'
        c.post('/config/settings', data=form)
        core = _read_yaml(root, 'config')
        assert core['general']['update_mode'] == 'force'

    def test_defaults_update_mode_to_notify_when_field_omitted(self, client):
        c, app, root = client
        assert 'general_update_mode' not in VALID_FORM
        c.post('/config/settings', data=VALID_FORM)
        core = _read_yaml(root, 'config')
        assert core['general']['update_mode'] == 'notify'

    def test_saving_update_mode_preserves_legacy_auto_update_key(self, client):
        """Additive write: saving a new update_mode must not delete a
        pre-existing legacy auto_update key - matches _apply_settings'
        merge-only-the-submitted-keys behavior."""
        c, app, root = client
        config_path = module_path(root, 'config')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write('plex:\n  url: "http://localhost:32400"\n'
                     'users:\n  list: "alice, bob"\n'
                     'general:\n  auto_update: true\n')

        form = dict(VALID_FORM)
        form['general_update_mode'] = 'off'
        c.post('/config/settings', data=form)

        core = _read_yaml(root, 'config')
        # Not core['general']['update_mode'] == 'off' as a raw string:
        # ruamel.yaml (like PyYAML) writes an unquoted `off` per YAML
        # 1.1's boolean literals, so plain yaml.safe_load reads it back
        # as False, not 'off' - get_update_mode() is what normalizes
        # that back to the 'off' mode (see its docstring), so assert
        # the effective mode through it rather than the raw value.
        assert get_update_mode(core) == 'off'
        assert core['general']['auto_update'] is True

    def test_saves_sync_safety_toggles_to_module_files(self, client):
        c, app, root = client
        form = dict(VALID_FORM)
        form['sonarr_auto_sync'] = 'on'
        c.post('/config/settings', data=form)

        sonarr = _read_yaml(root, 'sonarr')
        assert sonarr['auto_sync'] is True
        assert sonarr['user_mode'] == 'mapping'
        assert sonarr['plex_users'] == ['alice']

    def test_round_trip_preserves_untouched_keys(self, client):
        c, app, root = client
        tuning_path = module_path(root, 'tuning')
        os.makedirs(os.path.dirname(tuning_path), exist_ok=True)
        with open(tuning_path, 'w', encoding='utf-8') as f:
            f.write(
                "# Curatarr Tuning Configuration\n"
                "collections:\n"
                "  add_label: true\n"
                "  label_name: Recommended\n"
            )

        c.post('/config/settings', data=VALID_FORM)

        content = open(tuning_path, encoding='utf-8').read()
        assert '# Curatarr Tuning Configuration' in content
        assert 'label_name: Recommended' in content


class TestValidation:
    def test_weights_not_summing_to_one_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad['movies_weight_genre'] = '0.9'  # now sums to > 1
        resp = c.post('/config/settings', data=bad)
        assert resp.status_code == 400
        assert b'sum to 1.0' in resp.data

    def test_tv_weights_not_summing_to_one_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad['tv_weight_genre'] = '0.9'
        resp = c.post('/config/settings', data=bad)
        assert resp.status_code == 400

    def test_non_numeric_weight_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad['movies_weight_genre'] = 'not-a-number'
        resp = c.post('/config/settings', data=bad)
        assert resp.status_code == 400

    def test_invalid_logging_level_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad['logging_level'] = 'VERBOSE'
        resp = c.post('/config/settings', data=bad)
        assert resp.status_code == 400

    def test_invalid_update_mode_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad['general_update_mode'] = 'yolo'
        resp = c.post('/config/settings', data=bad)
        assert resp.status_code == 400

    def test_invalid_input_does_not_corrupt_existing_tuning_file(self, client):
        c, app, root = client
        c.post('/config/settings', data=VALID_FORM)  # establish a valid baseline
        before = _read_yaml(root, 'tuning')

        bad = dict(VALID_FORM)
        bad['movies_weight_genre'] = '0.9'
        c.post('/config/settings', data=bad)

        after = _read_yaml(root, 'tuning')
        assert after == before


class TestNullSection:
    def test_null_general_section_in_hand_edited_yaml_does_not_500(self, client):
        """M2: a bare `general:` line (parses to None, not {}) must not
        500 - it should be treated the same as a missing section."""
        c, app, root = client
        config_path = module_path(root, 'config')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write('plex:\n  url: "http://localhost:32400"\ngeneral:\nusers:\n  list: "alice, bob"\n')

        resp = c.post('/config/settings', data=VALID_FORM)
        assert resp.status_code == 303

        core = _read_yaml(root, 'config')
        assert core['general']['log_retention_days'] == 7

    def test_null_negative_signals_section_in_tuning_yml_does_not_500(self, client):
        c, app, root = client
        tuning_path = module_path(root, 'tuning')
        os.makedirs(os.path.dirname(tuning_path), exist_ok=True)
        with open(tuning_path, 'w', encoding='utf-8') as f:
            f.write('negative_signals:\n')

        resp = c.post('/config/settings', data=VALID_FORM)
        assert resp.status_code == 303

        tuning = _read_yaml(root, 'tuning')
        assert tuning['negative_signals']['bad_ratings']['threshold'] == 3
