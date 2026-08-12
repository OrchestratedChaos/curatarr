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
Tests for tests/harness.py - the deterministic scoring/pipeline harness
(scoring/pipeline determinism harness).

Runs the harness as a real subprocess (not an in-process import) so
PYTHONHASHSEED - which can only take effect at interpreter startup, not
mid-process - actually varies between runs. This also exercises the
harness the same way a human auditing a scoring refactor would
(`PYTHONHASHSEED=0 python -m tests.harness`), rather than just asserting
"the function exists".
"""

import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_harness(pythonhashseed: str) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = pythonhashseed
    result = subprocess.run(
        [sys.executable, "-m", "tests.harness"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"harness failed (stderr):\n{result.stderr}"
    return result.stdout


class TestDeterministicHarness:
    """Task 3: the harness itself must be reproducible."""

    def test_identical_runs_produce_byte_identical_output(self):
        """Same PYTHONHASHSEED, same pinned seed, run twice - assert
        byte-identical stdout. This is the actual Task 3 deliverable
        (not just 'select_tiered_recommendations accepts an rng kwarg')."""
        out_a = _run_harness("0")
        out_b = _run_harness("0")
        assert out_a == out_b

    def test_produces_non_trivial_selection(self):
        """Sanity check the fixtures aren't degenerate (all-zero scores,
        empty selection, etc.) - a byte-identical empty result would
        trivially pass the determinism check above without proving
        anything."""
        data = json.loads(_run_harness("0"))
        assert data["num_fixture_movies"] > 0
        assert len(data["all_scores"]) == data["num_fixture_movies"]
        assert len(data["selected_titles"]) == data["limit"]
        assert any(float.fromhex(s["score_hex"]) > 0 for s in data["all_scores"])


class TestFloatNonAssociativityGap:
    """Task 4: calculate_similarity_score (utils/scoring.py) iterates
    content_info's genres/keywords/etc via set(), whose iteration order for
    strings depends on PYTHONHASHSEED. Floating point addition is not
    associative, so if the scoring recompute is sensitive to that
    iteration order, the same content+profile could score *differently* by
    a few ULPs depending on hash seed alone - masked in production because
    scores normally come from cache.py on a profile_hash cache hit, and
    only a genuine cache-miss recompute would ever exercise this path.
    This harness forces that recompute explicitly and compares the exact
    bit pattern (float.hex()) across two different PYTHONHASHSEED values."""

    def test_recompute_is_hash_seed_independent(self):
        data_seed0 = json.loads(_run_harness("0"))
        data_other = json.loads(_run_harness("12345"))

        scores0 = {s["title"]: s["score_hex"] for s in data_seed0["all_scores"]}
        scores_other = {s["title"]: s["score_hex"] for s in data_other["all_scores"]}

        assert scores0 == scores_other, (
            "calculate_similarity_score's recomputed score depends on "
            "PYTHONHASHSEED (set() iteration order interacting with float "
            "non-associativity). This would only ever "
            "surface on a genuine profile_hash cache-miss recompute."
        )
