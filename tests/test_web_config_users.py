"""Tests for the /config/users screen: add/remove users.list, per-user
preferences (display_name, exclude_genres, max_rating,
streaming_services), and validation/round-trip behavior."""

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


class TestGet:
    def test_renders_existing_users_from_fixture(self, client):
        c, app, root = client
        resp = c.get('/config/users')
        assert resp.status_code == 200
        assert b'alice' in resp.data
        assert b'bob' in resp.data


class TestSave:
    def test_edits_existing_user_preferences(self, client):
        c, app, root = client
        resp = c.post('/config/users', data={
            'user_count': '2',
            'username_0': 'alice',
            'display_name_0': 'Alice A',
            'exclude_genres_0': 'horror, children',
            'max_rating_0': 'PG-13',
            'streaming_services_0': 'netflix, hulu',
            'username_1': 'bob',
            'display_name_1': 'Bob B',
            'exclude_genres_1': '',
            'max_rating_1': '',
            'streaming_services_1': '',
            'new_username': '',
        })
        assert resp.status_code == 303

        core = _read_config(root)
        prefs = core['users']['preferences']
        assert prefs['alice']['display_name'] == 'Alice A'
        assert prefs['alice']['exclude_genres'] == ['horror', 'children']
        assert prefs['alice']['max_rating'] == 'PG-13'
        assert prefs['alice']['streaming_services'] == ['netflix', 'hulu']
        assert prefs['bob']['display_name'] == 'Bob B'
        assert 'exclude_genres' not in prefs['bob']

    def test_adds_new_user(self, client):
        c, app, root = client
        c.post('/config/users', data={
            'user_count': '2',
            'username_0': 'alice', 'display_name_0': '', 'exclude_genres_0': '',
            'max_rating_0': '', 'streaming_services_0': '',
            'username_1': 'bob', 'display_name_1': '', 'exclude_genres_1': '',
            'max_rating_1': '', 'streaming_services_1': '',
            'new_username': 'carol',
        })

        core = _read_config(root)
        usernames = [u.strip() for u in core['users']['list'].split(',')]
        assert 'carol' in usernames
        assert core['users']['preferences']['carol']['display_name'] == 'carol'

    def test_removes_a_user(self, client):
        c, app, root = client
        c.post('/config/users', data={
            'user_count': '2',
            'username_0': 'alice', 'display_name_0': '', 'exclude_genres_0': '',
            'max_rating_0': '', 'streaming_services_0': '', 'remove_0': 'on',
            'username_1': 'bob', 'display_name_1': '', 'exclude_genres_1': '',
            'max_rating_1': '', 'streaming_services_1': '',
            'new_username': '',
        })

        core = _read_config(root)
        usernames = [u.strip() for u in core['users']['list'].split(',')]
        assert 'alice' not in usernames
        assert 'bob' in usernames
        assert 'alice' not in (core['users'].get('preferences') or {})


class TestValidation:
    def test_invalid_max_rating_rejected(self, client):
        c, app, root = client
        resp = c.post('/config/users', data={
            'user_count': '1',
            'username_0': 'alice', 'display_name_0': '', 'exclude_genres_0': '',
            'max_rating_0': 'NOT-A-RATING', 'streaming_services_0': '',
            'new_username': '',
        })
        assert resp.status_code == 400

    def test_duplicate_new_username_rejected(self, client):
        c, app, root = client
        resp = c.post('/config/users', data={
            'user_count': '1',
            'username_0': 'alice', 'display_name_0': '', 'exclude_genres_0': '',
            'max_rating_0': '', 'streaming_services_0': '',
            'new_username': 'alice',
        })
        assert resp.status_code == 400

    def test_invalid_input_does_not_corrupt_existing_file(self, client):
        c, app, root = client
        before = _read_config(root)
        c.post('/config/users', data={
            'user_count': '1',
            'username_0': 'alice', 'display_name_0': '', 'exclude_genres_0': '',
            'max_rating_0': 'GARBAGE', 'streaming_services_0': '',
            'new_username': '',
        })
        after = _read_config(root)
        assert after == before
