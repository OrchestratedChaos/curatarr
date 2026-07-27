"""
Tests for tests/golden_external_harness.py - the golden-output
equivalence harness backing the recommenders/external.py architecture
decomposition (PR2 - see tests/golden_external_harness.py's own
docstring for exactly what it exercises and what it deliberately
doesn't).

This is the actual regression gate: every PR in the external.py
decomposition sequence must leave `test_current_run_matches_golden_files`
passing. A failure here means the moved/refactored code produced
different output than current main did - per that PR's own instructions,
that's a STOP-and-report condition, not something to reconcile by
updating the golden files (updating them is only correct when a PR's own
described change is an intentional, separately-reviewed behavior change,
never as a side effect of a "pure relocation" commit).
"""

from tests.golden_external_harness import load_golden, run


class TestGoldenOutputMatchesCommittedFixtures:
    """The core regression gate - see module docstring."""

    def test_current_run_matches_golden_files(self):
        current = run()
        golden = load_golden()

        # Compare the rendered artifacts first (the actual deliverable
        # this harness exists to protect) with clear, specific failure
        # messages rather than one opaque dict-equality assertion.
        assert current["watchlist_md"] == golden["watchlist_md"], "generate_markdown() output drifted from golden"
        assert current["watchlist_html"] == golden["watchlist_html"], (
            "generate_combined_html() output drifted from golden"
        )
        assert current["missing_sequels"] == golden["missing_sequels"], (
            "find_missing_sequels() (Sequel Huntarr) output drifted from golden"
        )
        assert current["horizon_movies"] == golden["horizon_movies"], (
            "find_horizon_movies() (Horizon Huntarr) output drifted from golden"
        )
        assert current["movies_categorized"] == golden["movies_categorized"], (
            "categorize_by_streaming_service() (movies) output drifted from golden"
        )
        assert current["shows_categorized"] == golden["shows_categorized"], (
            "categorize_by_streaming_service() (shows) output drifted from golden"
        )


class TestHarnessSelfConsistency:
    """Sanity checks on the harness itself, independent of the committed
    golden files - catches the harness becoming flaky/nondeterministic on
    its own, which would make the golden comparison above meaningless."""

    def test_two_consecutive_runs_are_byte_identical(self):
        first = run()
        second = run()
        assert first == second

    def test_produces_non_trivial_output(self):
        """A byte-identical-to-golden empty/degenerate result would
        trivially pass the test above without proving anything."""
        result = run()
        assert len(result["missing_sequels"]) == 1
        assert len(result["horizon_movies"]) == 1
        assert len(result["movies_categorized"]["all_items"]) == 3
        assert len(result["shows_categorized"]["all_items"]) == 1
        assert "Alice" in result["watchlist_md"]
        assert "Alice" in result["watchlist_html"]
