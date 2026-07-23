"""Tests for the /config/libraries screen (#157 Phase 4): render existing
libraries, edit/add/remove repeatable rows, per-library *arr routing +
instance connection fields, validation (media_type/required/duplicate/
at-least-one), instance api_key masking/blank-keeps-existing, and
round-trip of unrelated config.yml keys (plex/users)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import yaml

from web.app import create_app
from web.config_io import module_path


@pytest.fixture
def client(curatarr_web_root):
    app = create_app(project_root=curatarr_web_root)
    app.testing = True
    return app.test_client(), app, curatarr_web_root


def _read_config(root):
    with open(module_path(root, 'config'), encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _lib(libs, lib_id):
    return next(l for l in libs if l['id'] == lib_id)


# Base form for the two libraries seeded by tests/conftest.py's
# curatarr_web_root fixture: 'movies' (movie, with an instance override
# + api_key) and 'tv-shows' (tv, no overrides at all).
def _base_form(**overrides):
    form = {
        'library_count': '2',
        'library_id_0': 'movies',
        'name_0': 'Movies',
        'section_0': 'Movies',
        'media_type_0': 'movie',
        'arr_0_root_folder': '/data/movies',
        'arr_0_quality_profile': 'HD-1080p',
        'arr_0_tag': '',
        'arr_0_minimum_availability': 'released',
        'arr_0_series_type': '',
        'instance_url_0': 'http://localhost:7878',
        'instance_api_key_0': '',
        'library_id_1': 'tv-shows',
        'name_1': 'TV Shows',
        'section_1': 'TV Shows',
        'media_type_1': 'tv',
        'arr_1_root_folder': '',
        'arr_1_quality_profile': '',
        'arr_1_tag': '',
        'arr_1_minimum_availability': '',
        'arr_1_series_type': '',
        'instance_url_1': '',
        'instance_api_key_1': '',
        'new_name': '',
        'new_section': '',
        'new_media_type': 'movie',
    }
    form.update(overrides)
    return form


class TestGet:
    def test_renders_existing_libraries_from_fixture(self, client):
        c, app, root = client
        resp = c.get('/config/libraries')
        assert resp.status_code == 200
        assert b'Movies' in resp.data
        assert b'TV Shows' in resp.data

    def test_shows_masked_secret_status_not_raw_value(self, client):
        c, app, root = client
        resp = c.get('/config/libraries')
        assert b'not-a-real-radarr-key' not in resp.data
        assert b'configured' in resp.data
        assert b'not set' in resp.data  # tv-shows has no instance api_key

    def test_no_libraries_block_shows_zero_rows_not_synthesized(self, tmp_path):
        """The screen reads core.get('libraries') directly, NOT through
        utils.config.get_libraries()'s legacy plex.movie_library/
        tv_library synthesis fallback - an editor screen must never
        silently materialize a synthesized pair into config.yml just
        because someone opened it."""
        root = tmp_path
        (root / 'config').mkdir()
        (root / 'config' / 'config.yml').write_text(
            'plex:\n  url: "http://localhost:32400"\n  movie_library: Movies\n  tv_library: "TV Shows"\n',
            encoding='utf-8',
        )
        (root / 'logs').mkdir()
        app = create_app(project_root=str(root))
        app.testing = True
        c = app.test_client()

        resp = c.get('/config/libraries')
        assert resp.status_code == 200
        assert b'No libraries configured yet' in resp.data


class TestSave:
    def test_edits_existing_library_routing(self, client):
        c, app, root = client
        form = _base_form(**{'arr_0_root_folder': '/data/movies-edited'})
        resp = c.post('/config/libraries', data=form)
        assert resp.status_code == 303

        core = _read_config(root)
        movies = _lib(core['libraries'], 'movies')
        assert movies['arr']['root_folder'] == '/data/movies-edited'
        assert movies['arr']['quality_profile'] == 'HD-1080p'

    def test_edit_preserves_immutable_id_across_rename(self, client):
        c, app, root = client
        form = _base_form(**{'name_0': 'Feature Films', 'section_0': 'Feature Films'})
        c.post('/config/libraries', data=form)

        core = _read_config(root)
        assert any(l['id'] == 'movies' and l['name'] == 'Feature Films' for l in core['libraries'])

    def test_adds_new_library(self, client):
        c, app, root = client
        form = _base_form(**{'new_name': 'Anime', 'new_section': 'Anime', 'new_media_type': 'tv'})
        c.post('/config/libraries', data=form)

        core = _read_config(root)
        assert len(core['libraries']) == 3
        anime = _lib(core['libraries'], 'anime')
        assert anime['name'] == 'Anime'
        assert anime['media_type'] == 'tv'

    def test_removes_a_library(self, client):
        c, app, root = client
        form = _base_form(**{'remove_1': 'on'})
        resp = c.post('/config/libraries', data=form)
        assert resp.status_code == 303

        core = _read_config(root)
        assert len(core['libraries']) == 1
        assert core['libraries'][0]['id'] == 'movies'

    def test_round_trip_preserves_unrelated_config_keys(self, client):
        c, app, root = client
        c.post('/config/libraries', data=_base_form(**{'arr_0_tag': 'Curatarr'}))

        core = _read_config(root)
        assert core['plex']['url'] == 'http://localhost:32400'
        assert core['users']['list'] == 'alice, bob'
        assert core['users']['preferences']['alice']['display_name'] == 'Alice A'


class TestInstanceSecret:
    def test_blank_api_key_on_resave_keeps_existing_value(self, client):
        c, app, root = client
        c.post('/config/libraries', data=_base_form(**{'instance_url_0': 'http://localhost:9999'}))

        core = _read_config(root)
        movies = _lib(core['libraries'], 'movies')
        assert movies['arr']['instance']['api_key'] == 'not-a-real-radarr-key'
        assert movies['arr']['instance']['url'] == 'http://localhost:9999'

    def test_nonblank_api_key_overwrites(self, client):
        c, app, root = client
        c.post('/config/libraries', data=_base_form(**{'instance_api_key_0': 'brand-new-key'}))

        core = _read_config(root)
        movies = _lib(core['libraries'], 'movies')
        assert movies['arr']['instance']['api_key'] == 'brand-new-key'

    def test_new_library_api_key_not_leaked_from_another_row(self, client):
        """A brand new row's id doesn't match any on-disk library, so a
        blank instance_api_key on it must NOT pick up 'movies'' saved key."""
        c, app, root = client
        form = _base_form(**{
            'new_name': 'Anime', 'new_section': 'Anime', 'new_media_type': 'tv',
            'new_instance_url': 'http://localhost:8990',
            'new_instance_api_key': '',
        })
        c.post('/config/libraries', data=form)

        core = _read_config(root)
        anime = _lib(core['libraries'], 'anime')
        assert 'instance' not in anime.get('arr', {}) or 'api_key' not in anime['arr'].get('instance', {})

    def test_never_renders_secret_after_save(self, client):
        c, app, root = client
        c.post('/config/libraries', data=_base_form(**{'instance_api_key_0': 'brand-new-key'}))
        resp = c.get('/config/libraries')
        assert b'brand-new-key' not in resp.data


class TestValidation:
    def test_invalid_media_type_rejected(self, client):
        c, app, root = client
        resp = c.post('/config/libraries', data=_base_form(**{'media_type_0': 'not-a-type'}))
        assert resp.status_code == 400

    def test_missing_name_rejected(self, client):
        c, app, root = client
        resp = c.post('/config/libraries', data=_base_form(**{'name_0': ''}))
        assert resp.status_code == 400

    def test_missing_section_rejected(self, client):
        c, app, root = client
        resp = c.post('/config/libraries', data=_base_form(**{'section_0': ''}))
        assert resp.status_code == 400

    def test_invalid_instance_url_rejected(self, client):
        c, app, root = client
        resp = c.post('/config/libraries', data=_base_form(**{'instance_url_0': 'not-a-url'}))
        assert resp.status_code == 400

    def test_duplicate_new_name_rejected(self, client):
        c, app, root = client
        resp = c.post('/config/libraries', data=_base_form(**{
            'new_name': 'Movies', 'new_section': 'Movies 2', 'new_media_type': 'movie',
        }))
        assert resp.status_code == 400

    def test_duplicate_rename_rejected(self, client):
        """Renaming an existing row to collide with another existing
        row's name must also be rejected, not just the add-fieldset case."""
        c, app, root = client
        resp = c.post('/config/libraries', data=_base_form(**{'name_1': 'Movies'}))
        assert resp.status_code == 400

    def test_removing_all_libraries_rejected(self, client):
        c, app, root = client
        resp = c.post('/config/libraries', data=_base_form(**{'remove_0': 'on', 'remove_1': 'on'}))
        assert resp.status_code == 400

    def test_invalid_input_does_not_corrupt_existing_file(self, client):
        c, app, root = client
        before = _read_config(root)
        c.post('/config/libraries', data=_base_form(**{'media_type_0': 'not-a-type'}))
        after = _read_config(root)
        assert after == before
