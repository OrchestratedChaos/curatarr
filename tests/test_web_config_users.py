"""Tests for the /config/users screen: add/remove users.list, per-user
preferences (display_name, exclude_genres, max_rating,
streaming_services), and validation/round-trip behavior."""

import os
import sys
from unittest.mock import patch

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
    with open(module_path(root, "config"), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class TestGet:
    def test_renders_existing_users_from_fixture(self, client):
        c, app, root = client
        resp = c.get("/config/users")
        assert resp.status_code == 200
        assert b"alice" in resp.data
        assert b"bob" in resp.data


class TestSave:
    def test_edits_existing_user_preferences(self, client):
        c, app, root = client
        resp = c.post(
            "/config/users",
            data={
                "user_count": "2",
                "username_0": "alice",
                "display_name_0": "Alice A",
                "exclude_genres_0": "horror, children",
                "max_rating_0": "PG-13",
                "streaming_services_0": "netflix, hulu",
                "username_1": "bob",
                "display_name_1": "Bob B",
                "exclude_genres_1": "",
                "max_rating_1": "",
                "streaming_services_1": "",
                "new_username": "",
            },
        )
        assert resp.status_code == 303

        core = _read_config(root)
        prefs = core["users"]["preferences"]
        assert prefs["alice"]["display_name"] == "Alice A"
        assert prefs["alice"]["exclude_genres"] == ["horror", "children"]
        assert prefs["alice"]["max_rating"] == "PG-13"
        assert prefs["alice"]["streaming_services"] == ["netflix", "hulu"]
        assert prefs["bob"]["display_name"] == "Bob B"
        assert "exclude_genres" not in prefs["bob"]

    def test_adds_new_user(self, client):
        c, app, root = client
        c.post(
            "/config/users",
            data={
                "user_count": "2",
                "username_0": "alice",
                "display_name_0": "",
                "exclude_genres_0": "",
                "max_rating_0": "",
                "streaming_services_0": "",
                "username_1": "bob",
                "display_name_1": "",
                "exclude_genres_1": "",
                "max_rating_1": "",
                "streaming_services_1": "",
                "new_username": "carol",
            },
        )

        core = _read_config(root)
        usernames = [u.strip() for u in core["users"]["list"].split(",")]
        assert "carol" in usernames
        assert core["users"]["preferences"]["carol"]["display_name"] == "carol"

    def test_removes_a_user(self, client):
        c, app, root = client
        c.post(
            "/config/users",
            data={
                "user_count": "2",
                "username_0": "alice",
                "display_name_0": "",
                "exclude_genres_0": "",
                "max_rating_0": "",
                "streaming_services_0": "",
                "remove_0": "on",
                "username_1": "bob",
                "display_name_1": "",
                "exclude_genres_1": "",
                "max_rating_1": "",
                "streaming_services_1": "",
                "new_username": "",
            },
        )

        core = _read_config(root)
        usernames = [u.strip() for u in core["users"]["list"].split(",")]
        assert "alice" not in usernames
        assert "bob" in usernames
        assert "alice" not in (core["users"].get("preferences") or {})


class TestValidation:
    def test_invalid_max_rating_rejected(self, client):
        c, app, root = client
        resp = c.post(
            "/config/users",
            data={
                "user_count": "1",
                "username_0": "alice",
                "display_name_0": "",
                "exclude_genres_0": "",
                "max_rating_0": "NOT-A-RATING",
                "streaming_services_0": "",
                "new_username": "",
            },
        )
        assert resp.status_code == 400

    def test_duplicate_new_username_rejected(self, client):
        c, app, root = client
        resp = c.post(
            "/config/users",
            data={
                "user_count": "1",
                "username_0": "alice",
                "display_name_0": "",
                "exclude_genres_0": "",
                "max_rating_0": "",
                "streaming_services_0": "",
                "new_username": "alice",
            },
        )
        assert resp.status_code == 400

    def test_invalid_input_does_not_corrupt_existing_file(self, client):
        c, app, root = client
        before = _read_config(root)
        c.post(
            "/config/users",
            data={
                "user_count": "1",
                "username_0": "alice",
                "display_name_0": "",
                "exclude_genres_0": "",
                "max_rating_0": "GARBAGE",
                "streaming_services_0": "",
                "new_username": "",
            },
        )
        after = _read_config(root)
        assert after == before


class TestNullSection:
    def test_null_users_section_in_hand_edited_yaml_does_not_500(self, client):
        """M2: a bare `users:` line (parses to None, not {}) must not
        500 - it should be treated the same as a missing section."""
        c, app, root = client
        config_path = module_path(root, "config")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write('plex:\n  url: "http://localhost:32400"\nusers:\n')

        resp = c.post(
            "/config/users",
            data={
                "user_count": "1",
                "username_0": "carol",
                "display_name_0": "",
                "exclude_genres_0": "",
                "max_rating_0": "",
                "streaming_services_0": "",
                "new_username": "",
            },
        )
        assert resp.status_code == 303

        core = _read_config(root)
        assert "carol" in core["users"]["list"]

    def test_null_preferences_subsection_does_not_500(self, client):
        c, app, root = client
        config_path = module_path(root, "config")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write('plex:\n  url: "http://localhost:32400"\nusers:\n  list: "alice"\n  preferences:\n')

        resp = c.post(
            "/config/users",
            data={
                "user_count": "1",
                "username_0": "alice",
                "display_name_0": "Alice A",
                "exclude_genres_0": "",
                "max_rating_0": "",
                "streaming_services_0": "",
                "new_username": "",
            },
        )
        assert resp.status_code == 303

        core = _read_config(root)
        assert core["users"]["preferences"]["alice"]["display_name"] == "Alice A"


class TestFetchFromPlex:
    """#266: 'Fetch from Plex' checklist - POST /config/users/fetch-plex
    (preview, never writes) and POST /config/users/add-plex (saves the
    checked selection)."""

    def test_fetch_shows_only_users_not_already_configured(self, client):
        c, app, root = client
        fetched = [
            {"username": "alice", "title": "Alice", "is_admin": True},
            {"username": "charlie", "title": "Charlie", "is_admin": False},
        ]
        with patch("web.config_users.fetch_plex_users", return_value=fetched):
            resp = c.post("/config/users/fetch-plex")

        assert resp.status_code == 200
        # alice is already configured (see _CONFIG_YML fixture) - must
        # not be offered again on the Fetch-from-Plex checklist
        # specifically (the regular users table above still has its
        # own, unrelated value="alice" hidden field for the existing
        # row - only the checklist's checkbox is what must exclude it).
        assert b"Charlie" in resp.data
        assert b'name="selected_username" value="charlie"' in resp.data
        assert b'name="selected_username" value="alice"' not in resp.data

    def test_fetch_all_already_configured_shows_nothing_to_add(self, client):
        c, app, root = client
        fetched = [
            {"username": "alice", "title": "Alice", "is_admin": True},
            {"username": "bob", "title": "Bob", "is_admin": False},
        ]
        with patch("web.config_users.fetch_plex_users", return_value=fetched):
            resp = c.post("/config/users/fetch-plex")

        assert resp.status_code == 200
        assert b"already configured" in resp.data

    def test_fetch_missing_token_shows_message(self, client):
        c, app, root = client
        config_path = module_path(root, "config")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write('plex:\n  url: "http://localhost:32400"\nusers:\n  list: "alice"\n')

        resp = c.post("/config/users/fetch-plex")

        assert resp.status_code == 200
        assert b"Plex token is not configured" in resp.data

    def test_fetch_connection_failure_shows_redacted_message(self, client):
        c, app, root = client
        with patch("web.config_users.fetch_plex_users", side_effect=Exception("boom X-Plex-Token=abcdef123456")):
            resp = c.post("/config/users/fetch-plex")

        assert resp.status_code == 200
        assert b"Could not fetch users from Plex" in resp.data
        assert b"abcdef123456" not in resp.data

    def test_add_plex_appends_selected_users(self, client):
        c, app, root = client
        resp = c.post("/config/users/add-plex", data={"selected_username": ["charlie", "dave"]})

        assert resp.status_code == 303
        core = _read_config(root)
        usernames = [u.strip() for u in core["users"]["list"].split(",")]
        assert "alice" in usernames
        assert "bob" in usernames
        assert "charlie" in usernames
        assert "dave" in usernames

    def test_add_plex_skips_already_configured_duplicate(self, client):
        c, app, root = client
        resp = c.post("/config/users/add-plex", data={"selected_username": ["alice", "eve"]})

        assert resp.status_code == 303
        core = _read_config(root)
        usernames = [u.strip() for u in core["users"]["list"].split(",")]
        assert usernames.count("alice") == 1
        assert "eve" in usernames

    def test_add_plex_preserves_existing_preferences(self, client):
        c, app, root = client
        c.post("/config/users/add-plex", data={"selected_username": ["charlie"]})

        core = _read_config(root)
        assert core["users"]["preferences"]["alice"]["display_name"] == "Alice A"

    def test_add_plex_no_selection_is_a_noop(self, client):
        c, app, root = client
        resp = c.post("/config/users/add-plex", data={})

        assert resp.status_code == 303
        core = _read_config(root)
        usernames = [u.strip() for u in core["users"]["list"].split(",")]
        assert usernames == ["alice", "bob"]
