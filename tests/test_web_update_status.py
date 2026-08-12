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
Tests for the update progress/outcome reporting added alongside the
"Update now" progress UI.

The reason any of this is file-based: the server process does NOT survive
an update. The worker kills it, applies, and starts a fresh one, so the
page reconnects to a process with no memory of what just happened.
Anything held in memory dies with the old server; the outcome has to be
written somewhere both processes can see.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.app import create_app
from web.update_apply import (
    UPDATE_OUTCOME_ABORTED,
    UPDATE_OUTCOME_FAILED,
    UPDATE_OUTCOME_NO_UPDATE,
    UPDATE_OUTCOME_UPDATED,
    UPDATE_PHASES,
    _record_apply_outcome,
    read_update_status,
    update_status_path,
    write_update_status,
)


class TestStatusFileRoundTrip:
    def test_missing_file_reads_as_empty(self, tmp_path):
        assert read_update_status(str(tmp_path)) == {}

    def test_write_then_read(self, tmp_path):
        write_update_status(str(tmp_path), phase="applying", step=3)
        assert read_update_status(str(tmp_path))["phase"] == "applying"

    def test_writes_merge_rather_than_replace(self, tmp_path):
        """The worker updates one field at a time as it progresses; an
        earlier write's `tag` must survive a later `phase` write."""
        write_update_status(str(tmp_path), tag="v9.9.9", started_at=1.0)
        write_update_status(str(tmp_path), phase="relaunching")
        status = read_update_status(str(tmp_path))
        assert status["tag"] == "v9.9.9"
        assert status["started_at"] == 1.0
        assert status["phase"] == "relaunching"

    def test_malformed_file_reads_as_empty_not_raises(self, tmp_path):
        """Corrupt status means "we don't know" - which the UI renders as
        neutral. It must never raise into a request handler."""
        with open(update_status_path(str(tmp_path)), "w", encoding="utf-8") as f:
            f.write("{not json")
        assert read_update_status(str(tmp_path)) == {}

    def test_non_dict_json_reads_as_empty(self, tmp_path):
        with open(update_status_path(str(tmp_path)), "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        assert read_update_status(str(tmp_path)) == {}

    def test_leaves_no_temp_file_behind(self, tmp_path):
        """Written via a temp file + rename so a concurrent reader in the
        OTHER process can never see a half-written file."""
        write_update_status(str(tmp_path), phase="applying")
        leftovers = [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]
        assert leftovers == []

    def test_write_never_raises_on_an_unwritable_dir(self, tmp_path):
        """Best-effort by contract: progress reporting must never be the
        thing that breaks an otherwise-fine update."""
        target = tmp_path / "nested"
        target.write_text("I am a file, not a directory", encoding="utf-8")
        write_update_status(str(target), phase="applying")  # must not raise


class TestApplyOutcomeMapping:
    """run.sh's --apply-verified-update contract is exactly one of
    UPDATED:<tag> / NO_UPDATE / FAILED:<reason>."""

    def test_updated_records_tag(self, tmp_path):
        _record_apply_outcome(str(tmp_path), "UPDATED:v2.10.88")
        status = read_update_status(str(tmp_path))
        assert status["outcome"] == UPDATE_OUTCOME_UPDATED
        assert status["tag"] == "v2.10.88"
        assert "v2.10.88" in status["detail"]
        assert status["finished_at"] is not None

    def test_no_update(self, tmp_path):
        _record_apply_outcome(str(tmp_path), "NO_UPDATE")
        assert read_update_status(str(tmp_path))["outcome"] == UPDATE_OUTCOME_NO_UPDATE

    def test_failed_carries_the_reason_through(self, tmp_path):
        _record_apply_outcome(str(tmp_path), "FAILED:Python 3.9.6 is below the 3.10 floor")
        status = read_update_status(str(tmp_path))
        assert status["outcome"] == UPDATE_OUTCOME_FAILED
        assert status["detail"] == "Python 3.9.6 is below the 3.10 floor"

    def test_failed_with_no_reason_still_has_a_message(self, tmp_path):
        _record_apply_outcome(str(tmp_path), "FAILED:")
        assert read_update_status(str(tmp_path))["detail"]

    @pytest.mark.parametrize("garbage", ["", "something else entirely", "UPDATED", "ok"])
    def test_unrecognized_output_is_failure_not_success(self, tmp_path, garbage):
        """A confident "updated" off an unrecognized string is exactly the
        false success this reporting exists to prevent."""
        _record_apply_outcome(str(tmp_path), garbage)
        assert read_update_status(str(tmp_path))["outcome"] == UPDATE_OUTCOME_FAILED


@pytest.fixture
def web(curatarr_web_root):
    """Same shape as tests/test_web_routes.py's client fixture."""
    app = create_app(project_root=curatarr_web_root, code_root=curatarr_web_root)
    app.testing = True
    return app.test_client(), app


class TestUpdateStatusRoute:
    def test_returns_empty_shape_when_no_update_has_run(self, web):
        client, _app = web
        resp = client.get("/update/status")
        assert resp.status_code == 200
        assert resp.get_json()["outcome"] is None

    def test_surfaces_a_recorded_outcome(self, web):
        client, app = web
        write_update_status(
            app.config["LOGS_DIR"],
            outcome=UPDATE_OUTCOME_UPDATED,
            tag="v2.10.88",
            detail="Updated to v2.10.88.",
            started_at=123.0,
            finished_at=456.0,
        )
        body = client.get("/update/status").get_json()
        assert body["outcome"] == "updated"
        assert body["tag"] == "v2.10.88"
        assert body["started_at"] == 123.0

    def test_surfaces_in_flight_phase(self, web):
        client, app = web
        write_update_status(app.config["LOGS_DIR"], phase="applying", step=3, total_steps=4)
        body = client.get("/update/status").get_json()
        assert body["phase"] == "applying"
        assert body["step"] == 3
        assert body["outcome"] is None

    def test_does_not_leak_extra_fields(self, web):
        """Deliberately renders only what the UI needs - no paths, no
        command lines, no stderr."""
        client, app = web
        write_update_status(app.config["LOGS_DIR"], phase="applying", secret_path="/home/someone/token")
        body = client.get("/update/status").get_json()
        assert "secret_path" not in body

    def test_aborted_outcome_survives_to_the_route(self, web):
        client, app = web
        write_update_status(
            app.config["LOGS_DIR"],
            outcome=UPDATE_OUTCOME_ABORTED,
            detail="A recommender run is in progress - nothing was changed.",
        )
        assert client.get("/update/status").get_json()["outcome"] == "aborted"


class TestProgressMarkupIsRendered:
    """The script in base.html looks these up by id and silently does
    nothing useful if they're absent, so a template edit that dropped
    them would leave the update flow with no visible progress at all -
    and no test failure anywhere else to catch it."""

    @pytest.fixture
    def banner_client(self, curatarr_web_root, monkeypatch):
        import web.app as app_module

        monkeypatch.setattr(app_module, "update_available", lambda **kw: ("99.0.0", "1.0.0", True))
        monkeypatch.setattr(app_module, "is_dismissed", lambda v: False)
        app = create_app(project_root=curatarr_web_root, code_root=curatarr_web_root)
        app.testing = True
        return app.test_client()

    def test_banner_renders_with_progress_elements(self, banner_client):
        html = banner_client.get("/").get_data(as_text=True)
        assert 'id="update-banner"' in html, "no update banner rendered - fixture setup is wrong"
        for element_id in ("update-progress", "update-progress-bar", "update-progress-step"):
            assert f'id="{element_id}"' in html, f"progress element #{element_id} missing from base.html"

    def test_progress_starts_hidden(self, banner_client):
        """It must not be visible until an update is actually running."""
        html = banner_client.get("/").get_data(as_text=True)
        progress_div = html[html.index('id="update-progress"') :][:200]
        assert "hidden" in progress_div

    def test_script_reads_the_status_endpoint(self, banner_client):
        """The whole point of the change: report the real outcome instead
        of reloading and leaving the user to infer it."""
        html = banner_client.get("/").get_data(as_text=True)
        assert "/update/status" in html


class TestBeginUpdateResetsStaleOutcome:
    def test_previous_outcome_is_cleared_before_spawning(self, tmp_path, monkeypatch):
        """The status file survives the restart by design, so last time's
        verdict is still on disk. Left there, the page would read the OLD
        update's result as this one's the moment it reconnected."""
        from web.update_apply import UpdateManager

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        write_update_status(str(logs_dir), outcome=UPDATE_OUTCOME_FAILED, detail="last time it broke")

        manager = UpdateManager(project_root=str(tmp_path), logs_dir=str(logs_dir))
        monkeypatch.setattr("web.update_apply.check_verified_update", lambda root: "v9.9.9")
        monkeypatch.setattr(UpdateManager, "_spawn_worker", lambda self, host, port: None)

        manager.begin_update("127.0.0.1", 8787)

        status = read_update_status(str(logs_dir))
        assert status["outcome"] is None
        assert status["tag"] == "v9.9.9"
        assert status["phase"] == UPDATE_PHASES[0]
        assert status["started_at"] is not None
