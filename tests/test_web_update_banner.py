"""Tests for the dismissible update-available banner (web/app.py's
_update_banner_context context processor + /update/dismiss route) and
its gating by general.update_mode.

The version-check itself (utils/update_check.py) is unit-tested
separately in tests/test_update_check.py - these tests mock
web.app.update_available so no test here ever touches the network.

As of v2.8.31, the banner renders for EVERY update_mode (including
'off') whenever a newer version is known - dismissal is a server-side
7-day snooze (utils/update_dismissal.py), not a permanent-per-version
browser cookie. The snooze/version-override mechanics themselves are
unit-tested in tests/test_update_dismissal.py; TestDismiss below covers
this file's own concern: the /update/dismiss route + banner context
processor actually wiring into that module correctly.
"""

import json
import os
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from web.app import create_app
from web.config_io import module_path


@pytest.fixture
def client(curatarr_web_root):
    app = create_app(project_root=curatarr_web_root)
    app.testing = True
    return app.test_client(), app, curatarr_web_root


@pytest.fixture(autouse=True)
def _isolated_dismissal_dir(tmp_path, monkeypatch):
    """Overrides tests/conftest.py's suite-wide
    _isolated_update_dismissal_dir (which hands every call a FRESH
    throwaway tmp dir, fine for tests that never care about dismissal
    state) with a single STABLE directory for the duration of each test
    here - this file's TestDismiss tests write a dismissal via one
    request and need a later request to actually see it, which requires
    utils.update_dismissal.get_project_root() to keep resolving to the
    same place across calls within a test. Same layering
    tests/test_update_check.py's own _isolated_cache_dir documents.
    """
    monkeypatch.setattr("utils.update_dismissal.get_project_root", lambda: str(tmp_path))
    return tmp_path


def _write_config(root, update_mode=None):
    config_path = module_path(root, "config")
    general = f"general:\n  update_mode: {update_mode}\n" if update_mode else ""
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'plex:\n  url: "http://localhost:32400"\nusers:\n  list: "alice, bob"\n{general}')


def _seed_dismissal(root, version, dismissed_at):
    """Directly writes utils.update_dismissal's on-disk state (matches
    that module's own project_root/cache/dismissed_update.json
    convention - see _isolated_dismissal_dir above for why `root` here
    is the same directory utils.update_dismissal.get_project_root() is
    patched to resolve to) - lets snooze-boundary tests control the
    dismissed_at timestamp precisely without waiting real days or
    monkeypatching the global time module."""
    path = os.path.join(str(root), "cache", "dismissed_update.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": version, "dismissed_at": dismissed_at}, f)


class TestBannerGating:
    def test_shown_when_update_mode_off_and_newer_version_available(self, client):
        """As of v2.8.31, 'off' only means "don't auto-apply" - it must
        NOT suppress the banner itself (that was the bug this fixed:
        opted-out users silently missing updates forever)."""
        c, app, root = client
        _write_config(root, update_mode="off")

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-banner" in resp.data
        assert b"v2.9.0" in resp.data

    def test_hidden_when_update_mode_off_and_no_newer_version(self, client):
        c, app, root = client
        _write_config(root, update_mode="off")

        with patch("web.app.update_available", return_value=("2.8.28", "2.8.28", False)):
            resp = c.get("/")

        assert b"update-banner" not in resp.data

    def test_hidden_when_no_newer_version(self, client):
        c, app, root = client
        _write_config(root, update_mode="notify")

        with patch("web.app.update_available", return_value=("2.8.28", "2.8.28", False)):
            resp = c.get("/")

        assert b"update-banner" not in resp.data

    def test_hidden_when_config_missing_never_calls_update_available(self, client):
        """Distinct from the update_mode='off' case above: a config that
        can't even be loaded must still skip update_available() entirely
        (see test_broken_config_fails_open_no_banner_no_500 below for the
        "config exists but is invalid YAML" variant) - there's simply no
        update_mode to resolve yet."""
        c, app, root = client
        os.remove(module_path(root, "config"))

        with patch("web.app.update_available") as mock_update_available:
            resp = c.get("/")
            mock_update_available.assert_not_called()

        assert b"update-banner" not in resp.data

    def test_shown_when_newer_version_available(self, client):
        c, app, root = client
        _write_config(root, update_mode="notify")

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-banner" in resp.data
        assert b"v2.9.0" in resp.data
        assert b"v2.8.28" in resp.data

    def test_shown_in_force_mode_too(self, client):
        """force mode still shows the banner - it's the source install's
        run.sh/run.ps1 (not the web UI) that auto-applies in force mode."""
        c, app, root = client
        _write_config(root, update_mode="force")

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-banner" in resp.data

    def test_banner_renders_on_config_screens_too(self, client):
        """The context processor is registered on the shared app, so it
        must cover config_app.py's routes as well, not just the
        dashboard/run/results routes defined directly in web/app.py."""
        c, app, root = client
        _write_config(root, update_mode="notify")

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/config/settings")

        assert b"update-banner" in resp.data

    def test_broken_config_fails_open_no_banner_no_500(self, client):
        c, app, root = client
        config_path = module_path(root, "config")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("not: [valid, yaml: structure\n")

        with patch("web.app.update_available", side_effect=Exception("should not be reached")) as mock_update_available:
            resp = c.get("/")
            mock_update_available.assert_not_called()

        assert resp.status_code == 200
        assert b"update-banner" not in resp.data

    def test_update_available_raising_fails_open_no_500(self, client):
        """Belt-and-suspenders: even if update_available() itself somehow
        raised (it's fail-open internally and shouldn't), the context
        processor's own try/except must still turn that into "no
        banner", never a 500."""
        c, app, root = client
        _write_config(root, update_mode="notify")

        with patch("web.app.update_available", side_effect=RuntimeError("unexpected")):
            resp = c.get("/")

        assert resp.status_code == 200
        assert b"update-banner" not in resp.data


class TestBannerContent:
    def test_frozen_binary_shows_update_now_button(self, client, monkeypatch):
        """As of v2.8.29, frozen binaries get the same one-click
        "Update now" button as source installs (in-binary self-update -
        see utils/self_update.py) - see
        tests/test_web_update_apply.py::TestFrozenAndSourceBothGetTheButton
        for the /update/apply route-level assertions."""
        c, app, root = client
        _write_config(root, update_mode="notify")
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-now-btn" in resp.data

    def test_frozen_binary_mentions_verification(self, client, monkeypatch):
        c, app, root = client
        _write_config(root, update_mode="notify")
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"verified" in resp.data.lower()

    def test_source_install_shows_update_now_button(self, client, monkeypatch):
        c, app, root = client
        _write_config(root, update_mode="notify")
        monkeypatch.setattr(sys, "frozen", False, raising=False)

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-now-btn" in resp.data

    def test_docker_hides_update_now_button_and_points_at_docker_pull(self, client, monkeypatch):
        """RUNNING_IN_DOCKER=true (set by the Dockerfile) still shows the
        banner - there IS a newer version - but never a button that
        would just fail (see web/update_apply.py's UpdateManager.
        begin_update RUNNING_IN_DOCKER gate); instead it tells the user
        to `docker pull`."""
        c, app, root = client
        _write_config(root, update_mode="notify")
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        monkeypatch.setattr(sys, "frozen", False, raising=False)

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-banner" in resp.data
        # The <button> element itself must be gone - not just checking
        # for the bare id substring, which also appears (harmlessly,
        # guarded by `if (!btn) { return; }`) inside the banner's own
        # always-rendered <script> block.
        assert b'id="update-now-btn"' not in resp.data
        assert b"docker pull" in resp.data

    def test_non_docker_unaffected_by_running_in_docker_unset(self, client, monkeypatch):
        c, app, root = client
        _write_config(root, update_mode="notify")
        monkeypatch.delenv("RUNNING_IN_DOCKER", raising=False)
        monkeypatch.setattr(sys, "frozen", False, raising=False)

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b'id="update-now-btn"' in resp.data
        assert b"docker pull" not in resp.data


class TestDismiss:
    def test_dismiss_persists_server_side_and_redirects(self, client):
        """As of v2.8.31, dismissal is server-side state (utils/
        update_dismissal.py), not a cookie - see that module's docstring
        for why (this app has no other session boundary, and the CLI
        notice needs to see the same dismissal a browser cookie never
        could)."""
        c, app, root = client
        _write_config(root, update_mode="notify")

        resp = c.post("/update/dismiss", data={"version": "2.9.0", "next": "/"})

        assert resp.status_code == 303
        # No cookie at all - server-side is the sole source of truth now.
        assert "curatarr_update_dismissed" not in resp.headers.get("Set-Cookie", "")

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")
        assert b"update-banner" not in resp.data

    def test_dismiss_redirects_to_next(self, client):
        c, app, root = client
        _write_config(root, update_mode="notify")

        resp = c.post("/update/dismiss", data={"version": "2.9.0", "next": "/results"})

        assert resp.headers["Location"].endswith("/results")

    def test_dismiss_rejects_external_next_url(self, client):
        """'next' must never turn this into an open redirect."""
        c, app, root = client
        _write_config(root, update_mode="notify")

        resp = c.post("/update/dismiss", data={"version": "2.9.0", "next": "http://evil.example.com"})

        assert "evil.example.com" not in resp.headers["Location"]

    def test_dismissed_version_suppresses_banner(self, client):
        c, app, root = client
        _write_config(root, update_mode="notify")
        c.post("/update/dismiss", data={"version": "2.9.0", "next": "/"})

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-banner" not in resp.data

    def test_dismissing_older_version_does_not_suppress_a_newer_one(self, client):
        c, app, root = client
        _write_config(root, update_mode="notify")
        c.post("/update/dismiss", data={"version": "2.9.0", "next": "/"})

        with patch("web.app.update_available", return_value=("2.10.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-banner" in resp.data
        assert b"v2.10.0" in resp.data

    def test_dismiss_works_when_update_mode_is_off(self, client):
        """The dismiss button is reachable from an 'off'-mode banner too
        (it now renders one - see TestBannerGating above), and must
        snooze it exactly the same way."""
        c, app, root = client
        _write_config(root, update_mode="off")
        c.post("/update/dismiss", data={"version": "2.9.0", "next": "/"})

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-banner" not in resp.data


class TestDismissSnooze:
    """7-day snooze window + persistence-across-app-instances - the
    banner-level integration of utils/update_dismissal.py's contract
    (unit-tested in isolation, including the exact 7-day boundary, in
    tests/test_update_dismissal.py). Seeds the on-disk dismissal state
    directly (_seed_dismissal) rather than going through a real 7-day
    wait or monkeypatching the global time module."""

    def test_dismissal_within_snooze_window_hides_banner(self, client):
        c, app, root = client
        _write_config(root, update_mode="notify")
        _seed_dismissal(root, "2.9.0", time.time() - 6 * 24 * 60 * 60)  # 6 days ago

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-banner" not in resp.data

    def test_dismissal_expires_after_seven_days(self, client):
        c, app, root = client
        _write_config(root, update_mode="notify")
        _seed_dismissal(root, "2.9.0", time.time() - (7 * 24 * 60 * 60 + 1))  # just over 7 days ago

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-banner" in resp.data
        assert b"v2.9.0" in resp.data

    def test_newer_version_overrides_an_active_snooze(self, client):
        c, app, root = client
        _write_config(root, update_mode="notify")
        _seed_dismissal(root, "2.9.0", time.time() - 60)  # dismissed 1 minute ago, well within snooze

        with patch("web.app.update_available", return_value=("2.10.0", "2.8.28", True)):
            resp = c.get("/")

        assert b"update-banner" in resp.data
        assert b"v2.10.0" in resp.data

    def test_dismissal_persists_across_a_fresh_app_instance(self, client):
        """Proves the dismissal is genuine server-side/on-disk state, not
        anything cached in-process on the Flask app object - a brand new
        create_app() call (i.e. a server restart) against the same
        project root must still see it."""
        c, app, root = client
        _write_config(root, update_mode="notify")
        c.post("/update/dismiss", data={"version": "2.9.0", "next": "/"})

        fresh_app = create_app(project_root=root)
        fresh_app.testing = True
        fresh_client = fresh_app.test_client()

        with patch("web.app.update_available", return_value=("2.9.0", "2.8.28", True)):
            resp = fresh_client.get("/")

        assert b"update-banner" not in resp.data
