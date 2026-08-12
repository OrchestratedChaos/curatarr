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

"""Tests for the /config/connections screen: form render, save
(correct modular YAML + round-trip), secret masking/blank-keeps-
existing, validation rejection without corrupting the file, and the
/config/test/<service> endpoints (clients mocked - no real network)."""

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


def _read_yaml(root, name):
    path = module_path(root, name)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


VALID_FORM = {
    "plex_url": "http://localhost:32400",
    "plex_token": "",
    "tmdb_api_key": "",
    "tautulli_url": "",
    "tautulli_api_key": "",
    "sonarr_url": "http://localhost:8989",
    "sonarr_api_key": "sonarr-key-123",
    "sonarr_user_mode": "mapping",
    "sonarr_plex_users": "alice",
    "radarr_url": "http://localhost:7878",
    "radarr_api_key": "radarr-key-123",
    "radarr_user_mode": "mapping",
    "radarr_plex_users": "alice",
    "trakt_client_id": "client-id-123",
    "trakt_client_secret": "client-secret-123",
    "trakt_user_mode": "mapping",
    "trakt_plex_users": "alice",
}


class TestGet:
    def test_renders_form(self, client):
        c, app, root = client
        resp = c.get("/config/connections")
        assert resp.status_code == 200
        assert b"Setup / Connections" in resp.data

    def test_shows_masked_secret_status_not_raw_value(self, client):
        c, app, root = client
        resp = c.get("/config/connections")
        assert b"not-a-real-token" not in resp.data
        assert b"configured" in resp.data


class TestEnvVarSecretStatus:
    """#289: an integration configured ONLY via its environment-variable
    override (nothing at all on disk) must show "configured", not a
    misleading "not set" - see web/config_io.py's secret_status_with_env.
    """

    def test_sonarr_api_key_from_env_shows_configured(self, client, monkeypatch):
        c, app, root = client
        # No sonarr.yml at all in this fixture root - api_key is
        # unconfigured on disk, so without the env-var fix this would
        # show "not set" even though SONARR_API_KEY makes it fully
        # functional at runtime.
        monkeypatch.setenv("SONARR_API_KEY", "env-sonarr-key")

        from web.config_connections import _connections_view, _load_connections

        data = _load_connections(root)
        view = _connections_view(data)

        assert view["sonarr"]["api_key_status"] == "configured"
        # Never the raw value itself, even in this internal view dict.
        resp = c.get("/config/connections")
        assert b"env-sonarr-key" not in resp.data

    def test_trakt_access_token_from_env_shows_configured(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setenv("TRAKT_ACCESS_TOKEN", "env-trakt-token")

        from web.config_connections import _connections_view, _load_connections

        data = _load_connections(root)
        view = _connections_view(data)

        assert view["trakt"]["access_token_status"] == "configured"

    def test_no_env_var_set_falls_back_to_disk_not_set(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.delenv("SONARR_API_KEY", raising=False)

        from web.config_connections import _connections_view, _load_connections

        data = _load_connections(root)
        view = _connections_view(data)

        assert view["sonarr"]["api_key_status"] == "not set"


class TestTraktReauthHint:
    """The Trakt re-auth instructions shown below the fields must match
    how a user can actually reach this screen in the first place - a
    Docker user can't shell into a source-checkout venv, and a
    packaged (frozen) binary user has no loose utils/trakt_auth.py or
    --run-recommender-style dispatch to run it with at all. See
    web/config_connections.py's own _connections_view docstring on
    "trakt_reauth"."""

    @staticmethod
    def _text(resp):
        """Collapse the template's own line-wrapping (real whitespace
        the browser would collapse too) so multi-word assertions below
        don't depend on exactly where a line happens to wrap in the
        .html source."""
        return " ".join(resp.data.decode().split())

    def test_source_install_shows_module_invocation_and_venv_hint(self, client, monkeypatch):
        monkeypatch.delenv("RUNNING_IN_DOCKER", raising=False)
        c, app, root = client
        resp = c.get("/config/connections")
        text = self._text(resp)
        assert "python3 -m utils.trakt_auth</code> to link/relink" in text
        assert "virtual environment active" in text
        assert "docker exec" not in text

    def test_docker_shows_docker_exec_command(self, client, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        c, app, root = client
        resp = c.get("/config/connections")
        text = self._text(resp)
        assert "docker exec -it" in text
        assert "python3 -m utils.trakt_auth" in text

    def test_frozen_states_gap_plainly_instead_of_a_dead_command(self, client, monkeypatch):
        monkeypatch.delenv("RUNNING_IN_DOCKER", raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        c, app, root = client
        resp = c.get("/config/connections")
        text = self._text(resp)
        assert "isn't available yet" in text
        assert "docker exec" not in text
        # Never claim the module invocation works here - it can't
        # (recommenders/<x>.py's own equivalent is --run-recommender,
        # not a loose script - see this module's docstring).
        assert "python3 -m utils.trakt_auth</code> to link" not in text


class TestSave:
    def test_saves_plex_and_tmdb_to_config_yml(self, client):
        c, app, root = client
        resp = c.post("/config/connections", data=VALID_FORM)
        assert resp.status_code == 303

        core = _read_yaml(root, "config")
        assert core["plex"]["url"] == "http://localhost:32400"

    def test_saves_sonarr_radarr_trakt_to_their_own_files(self, client):
        c, app, root = client
        c.post("/config/connections", data=VALID_FORM)

        sonarr = _read_yaml(root, "sonarr")
        assert sonarr["url"] == "http://localhost:8989"
        assert sonarr["api_key"] == "sonarr-key-123"
        assert sonarr["plex_users"] == ["alice"]

        radarr = _read_yaml(root, "radarr")
        assert radarr["url"] == "http://localhost:7878"

        trakt = _read_yaml(root, "trakt")
        assert trakt["client_id"] == "client-id-123"
        assert trakt["export"]["plex_users"] == ["alice"]

    def test_never_renders_secret_after_save(self, client):
        c, app, root = client
        c.post("/config/connections", data=VALID_FORM)
        resp = c.get("/config/connections")
        assert b"sonarr-key-123" not in resp.data
        assert b"radarr-key-123" not in resp.data
        assert b"client-secret-123" not in resp.data

    def test_blank_secret_on_resave_keeps_existing_value(self, client):
        c, app, root = client
        c.post("/config/connections", data=VALID_FORM)

        second = dict(VALID_FORM)
        second["sonarr_api_key"] = ""  # blank = keep existing
        second["sonarr_url"] = "http://localhost:9999"  # change a non-secret field too
        c.post("/config/connections", data=second)

        sonarr = _read_yaml(root, "sonarr")
        assert sonarr["api_key"] == "sonarr-key-123"  # unchanged
        assert sonarr["url"] == "http://localhost:9999"  # changed

    def test_nonblank_secret_overwrites(self, client):
        c, app, root = client
        c.post("/config/connections", data=VALID_FORM)

        second = dict(VALID_FORM)
        second["sonarr_api_key"] = "brand-new-key"
        c.post("/config/connections", data=second)

        sonarr = _read_yaml(root, "sonarr")
        assert sonarr["api_key"] == "brand-new-key"

    def test_round_trip_preserves_untouched_yaml_comments_and_keys(self, client):
        c, app, root = client
        sonarr_path = module_path(root, "sonarr")
        os.makedirs(os.path.dirname(sonarr_path), exist_ok=True)
        with open(sonarr_path, "w", encoding="utf-8") as f:
            f.write(
                "# Curatarr Sonarr Configuration\n"
                "enabled: true\n"
                "url: http://localhost:8989\n"
                "api_key: old-key\n"
                "root_folder: /Volumes/TV\n"
                "quality_profile: HD-1080p\n"
            )

        c.post("/config/connections", data=VALID_FORM)

        content = open(sonarr_path, encoding="utf-8").read()
        assert "# Curatarr Sonarr Configuration" in content
        assert "root_folder: /Volumes/TV" in content
        assert "quality_profile: HD-1080p" in content


class TestValidation:
    def test_invalid_plex_url_rejected_with_400(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad["plex_url"] = "not-a-url"
        resp = c.post("/config/connections", data=bad)
        assert resp.status_code == 400
        assert b"valid http" in resp.data or b"Must be a valid" in resp.data

    def test_invalid_input_does_not_corrupt_existing_file(self, client):
        c, app, root = client
        c.post("/config/connections", data=VALID_FORM)
        before = _read_yaml(root, "config")

        bad = dict(VALID_FORM)
        bad["plex_url"] = "not-a-url"
        c.post("/config/connections", data=bad)

        after = _read_yaml(root, "config")
        assert after == before

    def test_invalid_user_mode_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad["sonarr_user_mode"] = "not-a-real-mode"
        resp = c.post("/config/connections", data=bad)
        assert resp.status_code == 400


class TestConnectionsTestEndpoint:
    def test_unknown_service_404s(self, client):
        c, app, root = client
        resp = c.post("/config/test/not-a-service")
        assert resp.status_code == 404

    def test_plex_test_success(self, client, monkeypatch):
        c, app, root = client

        class _FakeServer:
            class library:
                @staticmethod
                def sections():
                    return [object()]

        import web.config_test_connection as cc

        monkeypatch.setattr(cc, "init_plex", lambda config: _FakeServer())

        resp = c.post("/config/test/plex", data={"url": "http://localhost:32400", "token": "tok"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_plex_test_failure_message_is_redacted(self, client, monkeypatch):
        c, app, root = client

        def _raise(config):
            raise ConnectionError("failed for X-Plex-Token=abcdef1234567890 on request")

        import web.config_test_connection as cc

        monkeypatch.setattr(cc, "init_plex", _raise)

        resp = c.post("/config/test/plex", data={"url": "http://localhost:32400", "token": "abcdef1234567890"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert "abcdef1234567890" not in body["message"]

    def test_sonarr_test_uses_saved_key_when_submission_blank(self, client, monkeypatch):
        c, app, root = client
        c.post("/config/connections", data=VALID_FORM)  # saves sonarr api_key

        captured = {}

        class _FakeClient:
            def __init__(self, url, api_key):
                captured["url"] = url
                captured["api_key"] = api_key

            def test_connection(self):
                return True

        import web.config_test_connection as cc

        monkeypatch.setattr(cc, "SonarrClient", _FakeClient)

        resp = c.post("/config/test/sonarr", data={"url": "http://localhost:8989", "api_key": ""})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert captured["api_key"] == "sonarr-key-123"

    def test_trakt_test_reports_missing_auth(self, client):
        c, app, root = client
        resp = c.post("/config/test/trakt", data={"client_id": "cid", "client_secret": "csecret"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert "trakt_auth" in body["message"]

    def test_sonarr_test_with_different_url_does_not_leak_saved_key(self, client, monkeypatch):
        """HIGH #1: submitting a blank secret alongside a URL that
        differs from the saved one must never cause the stored secret to
        be sent to that (possibly attacker-controlled) URL."""
        c, app, root = client
        c.post("/config/connections", data=VALID_FORM)  # saves sonarr api_key = sonarr-key-123

        captured = {}

        class _FakeClient:
            def __init__(self, url, api_key):
                captured["url"] = url
                captured["api_key"] = api_key

            def test_connection(self):
                return True

        import web.config_test_connection as cc

        monkeypatch.setattr(cc, "SonarrClient", _FakeClient)

        resp = c.post(
            "/config/test/sonarr",
            data={"url": "http://attacker.example.com", "api_key": ""},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        # test_sonarr() fails fast on a blank api_key before ever
        # constructing SonarrClient - the saved key was never merged in
        # (and never even reaches the network).
        assert body["ok"] is False
        assert captured == {}

    def test_plex_test_with_same_saved_url_still_uses_saved_token(self, client, monkeypatch):
        """The blank-secret convenience still works for the normal case:
        testing the URL that's already saved."""
        c, app, root = client
        form = dict(VALID_FORM)
        form["plex_token"] = "plex-token-123"
        c.post("/config/connections", data=form)

        captured = {}

        class _FakeServer:
            class library:
                @staticmethod
                def sections():
                    captured["called"] = True
                    return [object()]

        import web.config_test_connection as cc

        def _fake_init_plex(config):
            captured["token"] = config["plex"]["token"]
            return _FakeServer()

        monkeypatch.setattr(cc, "init_plex", _fake_init_plex)

        resp = c.post("/config/test/plex", data={"url": "http://localhost:32400", "token": ""})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert captured["token"] == "plex-token-123"

    def test_null_plex_section_in_hand_edited_yaml_does_not_500(self, client):
        """M2: a bare `plex:` line (parses to None, not {}) must not 500
        or half-save the connections form."""
        c, app, root = client
        config_path = module_path(root, "config")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write('plex:\nusers:\n  list: "alice, bob"\n')

        resp = c.post("/config/connections", data=VALID_FORM)
        assert resp.status_code == 303

        core = _read_yaml(root, "config")
        assert core["plex"]["url"] == "http://localhost:32400"
        sonarr = _read_yaml(root, "sonarr")
        assert sonarr["url"] == "http://localhost:8989"

    def test_merge_validation_failure_prevents_write_and_returns_500(self, client, monkeypatch):
        """M4: a merge that fails the pre-write dry-run must not write
        ANY of the module files for this save."""
        c, app, root = client
        # commit_modules (called by every config screen's *_save route)
        # and the validate_merge it wraps both live in web.config_io as
        # of the audit remediation batch F/I, PR1(a) config_app.py split
        # - patching validate_merge there is what actually intercepts the
        # call commit_modules makes, regardless of which screen's route
        # triggered it.
        import web.config_io as config_io_mod

        monkeypatch.setattr(
            config_io_mod,
            "validate_merge",
            lambda project_root, modules: "simulated merge failure",
        )

        resp = c.post("/config/connections", data=VALID_FORM)
        assert resp.status_code == 500

        # sonarr.yml didn't exist before this save - if _commit_modules
        # correctly gates every write on the dry-run passing, it's still
        # absent (empty) after a failed one.
        sonarr = _read_yaml(root, "sonarr")
        assert sonarr == {}


class TestArrGlobalDefaults:
    """
    #339: sonarr.yml/radarr.yml global defaults (root_folder,
    quality_profile, tag, monitor...) were only editable by hand - the
    Connections screen exposed connection and sync-policy fields only.
    These are the values every library that does not override them
    inherits, so being unable to set them made the *arr integration
    unusable from the UI.
    """

    def test_saves_sonarr_defaults(self, client):
        c, _app, root = client
        form = dict(VALID_FORM)
        form.update(
            {
                "sonarr_root_folder": "/data/tv",
                "sonarr_quality_profile": "HD-1080p",
                "sonarr_tag": "Curatarr",
                "sonarr_series_type": "anime",
                "sonarr_season_folder": "on",
                "sonarr_monitor": "on",
                "sonarr_monitor_option": "future",
                "sonarr_search_missing": "on",
            }
        )
        assert c.post("/config/connections", data=form).status_code == 303

        sonarr = _read_yaml(root, "sonarr")
        assert sonarr["root_folder"] == "/data/tv"
        assert sonarr["quality_profile"] == "HD-1080p"
        assert sonarr["tag"] == "Curatarr"
        assert sonarr["series_type"] == "anime"
        assert sonarr["season_folder"] is True
        assert sonarr["monitor"] is True
        assert sonarr["monitor_option"] == "future"
        assert sonarr["search_missing"] is True

    def test_saves_radarr_defaults(self, client):
        c, _app, root = client
        form = dict(VALID_FORM)
        form.update(
            {
                "radarr_root_folder": "/data/movies",
                "radarr_quality_profile": "Ultra-HD",
                "radarr_tag": "Curatarr",
                "radarr_minimum_availability": "inCinemas",
                "radarr_monitor": "on",
            }
        )
        assert c.post("/config/connections", data=form).status_code == 303

        radarr = _read_yaml(root, "radarr")
        assert radarr["root_folder"] == "/data/movies"
        assert radarr["quality_profile"] == "Ultra-HD"
        assert radarr["minimum_availability"] == "inCinemas"
        assert radarr["monitor"] is True
        assert radarr["search_for_movie"] is False  # unchecked box

    def test_a_form_without_these_fields_does_not_blank_stored_values(self, client):
        """
        The data-loss case, caught by the round-trip test while building
        this. An older template or a scripted POST that omits the
        defaults must leave them alone rather than writing "" over a real
        root_folder.
        """
        c, _app, root = client
        seeded = dict(VALID_FORM)
        seeded.update({"sonarr_root_folder": "/data/tv", "sonarr_quality_profile": "HD-1080p"})
        c.post("/config/connections", data=seeded)
        assert _read_yaml(root, "sonarr")["root_folder"] == "/data/tv"

        # VALID_FORM carries no *_root_folder key at all.
        c.post("/config/connections", data=VALID_FORM)
        assert _read_yaml(root, "sonarr")["root_folder"] == "/data/tv", "stored root_folder was blanked"

    def test_blank_submitted_value_does_clear_it(self, client):
        """Presence gates the write; an explicitly emptied box still clears."""
        c, _app, root = client
        seeded = dict(VALID_FORM)
        seeded.update({"sonarr_root_folder": "/data/tv"})
        c.post("/config/connections", data=seeded)

        cleared = dict(VALID_FORM)
        cleared.update({"sonarr_root_folder": ""})
        c.post("/config/connections", data=cleared)
        assert _read_yaml(root, "sonarr")["root_folder"] == ""

    def test_invalid_choice_is_rejected_not_written(self, client):
        """An invalid series_type would fail mid-sync against a real
        Sonarr; reject it at the form instead."""
        c, _app, root = client
        form = dict(VALID_FORM)
        form.update({"sonarr_root_folder": "/data/tv", "sonarr_series_type": "not-a-type"})
        resp = c.post("/config/connections", data=form)
        # 400 + re-render, matching this screen's existing convention for
        # an invalid choice (see test_invalid_user_mode_rejected).
        assert resp.status_code == 400
        assert _read_yaml(root, "sonarr").get("series_type") != "not-a-type"

    def test_invalid_minimum_availability_is_rejected(self, client):
        c, _app, root = client
        form = dict(VALID_FORM)
        form.update({"radarr_root_folder": "/data/movies", "radarr_minimum_availability": "whenever"})
        assert c.post("/config/connections", data=form).status_code == 400
        assert _read_yaml(root, "radarr").get("minimum_availability") != "whenever"

    def test_defaults_are_rendered_on_the_page(self, client):
        c, _app, _root = client
        body = c.get("/config/connections").get_data(as_text=True)
        for field in (
            "sonarr_root_folder",
            "sonarr_quality_profile",
            "sonarr_monitor_option",
            "radarr_root_folder",
            "radarr_minimum_availability",
        ):
            assert field in body, f"{field} missing from the Connections page"
