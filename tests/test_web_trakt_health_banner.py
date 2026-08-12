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

"""Tests for the Trakt export/sync health banner (web/app.py's
_trakt_health_context context processor) and /status.json's
trakt_export key - the explicit integration-health signal that
replaces log-string matching for surfacing a Trakt auth/export failure
in the web UI (see utils/integration_status.py and CHANGELOG's Trakt
token-refresh-persistence entry for why log-grepping was fragile enough
to hide a months-long silent failure)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils.integration_status import record_integration_status
from web.app import create_app
from web.config_io import module_path


@pytest.fixture
def client(curatarr_web_root):
    app = create_app(project_root=curatarr_web_root)
    app.testing = True
    return app.test_client(), app, curatarr_web_root


def _write_config(root, trakt_enabled=True):
    config_path = module_path(root, "config")
    trakt_section = f"trakt:\n  enabled: {str(trakt_enabled).lower()}\n"
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'plex:\n  url: "http://localhost:32400"\nusers:\n  list: "alice"\n{trakt_section}')


class TestTraktHealthBanner:
    def test_no_banner_when_trakt_disabled(self, client):
        c, app, root = client
        _write_config(root, trakt_enabled=False)
        record_integration_status(os.path.join(root, "cache"), "trakt_export", False, "should never show")

        resp = c.get("/")

        assert b"trakt-banner" not in resp.data

    def test_no_banner_when_nothing_recorded_yet(self, client):
        c, app, root = client
        _write_config(root, trakt_enabled=True)

        resp = c.get("/")

        assert b"trakt-banner" not in resp.data

    def test_no_banner_when_last_attempt_succeeded(self, client):
        c, app, root = client
        _write_config(root, trakt_enabled=True)
        record_integration_status(os.path.join(root, "cache"), "trakt_export", True)

        resp = c.get("/")

        assert b"trakt-banner" not in resp.data

    def test_banner_shown_when_last_attempt_failed(self, client):
        c, app, root = client
        _write_config(root, trakt_enabled=True)
        record_integration_status(os.path.join(root, "cache"), "trakt_export", False, "Trakt client not authenticated")

        resp = c.get("/")

        assert b"trakt-banner" in resp.data
        assert b"Trakt client not authenticated" in resp.data

    def test_banner_shown_on_every_page_not_just_dashboard(self, client):
        c, app, root = client
        _write_config(root, trakt_enabled=True)
        record_integration_status(os.path.join(root, "cache"), "trakt_export", False, "boom")

        resp = c.get("/run")

        assert b"trakt-banner" in resp.data


class TestStatusJsonTraktExport:
    def test_trakt_export_none_when_disabled(self, client):
        c, app, root = client
        _write_config(root, trakt_enabled=False)

        resp = c.get("/status.json")

        assert resp.json["trakt_export"] is None

    def test_trakt_export_none_when_nothing_recorded(self, client):
        c, app, root = client
        _write_config(root, trakt_enabled=True)

        resp = c.get("/status.json")

        assert resp.json["trakt_export"] is None

    def test_trakt_export_reflects_last_recorded_failure(self, client):
        c, app, root = client
        _write_config(root, trakt_enabled=True)
        record_integration_status(os.path.join(root, "cache"), "trakt_export", False, "refresh rejected")

        resp = c.get("/status.json")

        assert resp.json["trakt_export"]["success"] is False
        assert resp.json["trakt_export"]["detail"] == "refresh rejected"

    def test_trakt_export_reflects_last_recorded_success(self, client):
        c, app, root = client
        _write_config(root, trakt_enabled=True)
        record_integration_status(os.path.join(root, "cache"), "trakt_export", True)

        resp = c.get("/status.json")

        assert resp.json["trakt_export"]["success"] is True
