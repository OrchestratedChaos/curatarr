"""
Drives the browser-side tests in tests/static/ from pytest.

web/static/app.js has real logic in it (the run page's output renderer),
and a bug there is as user-visible as a bug in the recommender - the
2.10.90 quadratic that froze the page for minutes lived in this file. It
needs tests that actually execute it, not just assertions about the
Python that serves it.

There is no Node dependency in this project and adding one for a single
test file would be a poor trade, so this runs under whatever JS engine
the machine already has: Node on CI's Linux runners, JavaScriptCore
(bundled with macOS, no install) for local development.
"""

import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# macOS ships JavaScriptCore inside the framework; it is not on PATH.
JSC_PATH = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"


def _find_js_engine():
    """Return argv prefix for an available JS engine, or None."""
    for name in ("node", "deno", "bun"):
        found = shutil.which(name)
        if found:
            return [found, "run"] if name == "deno" else [found]
    if os.path.exists(JSC_PATH):
        return [JSC_PATH]
    return None


JS_ENGINE = _find_js_engine()


@pytest.mark.skipif(JS_ENGINE is None, reason="no JavaScript engine available (node/deno/bun/jsc)")
class TestRunPageProgressCollapsing:
    """tests/static/test_progress_collapse.js - see that file for cases."""

    def test_progress_collapse_suite_passes(self):
        script = os.path.join("tests", "static", "test_progress_collapse.js")
        result = subprocess.run(
            JS_ENGINE + [script],
            cwd=REPO_ROOT,  # the script reads web/static/app.js by relative path
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"browser-side progress-collapse tests failed:\n{result.stdout}\n{result.stderr}"
        assert "ok - " in result.stdout, result.stdout


class TestStaticAssetIntegrity:
    """Cheap guards that run everywhere, engine or not."""

    def test_app_js_renderer_block_markers_exist(self):
        """
        The JS suite extracts the renderer by these two markers. If a
        refactor renames them the suite would silently test nothing, so
        assert their presence separately - this failing is the signal to
        update the extraction, not to delete the check.
        """
        with open(os.path.join(REPO_ROOT, "web", "static", "app.js"), encoding="utf-8") as f:
            src = f.read()
        assert "  var MAX_LINES = 5000;" in src
        assert "  function appendLine(text) {" in src

    def test_progress_rule_is_mirrored_in_both_languages(self):
        """
        web/job_runner.py and web/static/app.js implement the same
        collapsing rule independently (server: clean stored log + backlog
        replay; client: clean live DOM). They can drift. This does not
        compare the regexes character by character - they are written in
        different dialects - but it does assert neither side quietly lost
        its half.
        """
        with open(os.path.join(REPO_ROOT, "web", "static", "app.js"), encoding="utf-8") as f:
            js = f.read()
        with open(os.path.join(REPO_ROOT, "web", "job_runner.py"), encoding="utf-8") as f:
            py = f.read()
        assert "progressFamily" in js, "client lost its progress-collapsing rule"
        assert "def progress_family" in py, "server lost its progress-collapsing rule"
