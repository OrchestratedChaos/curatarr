"""Tests for scripts/selfupdate_e2e/build_fixtures.py's source-patching
helpers (replace_top_level_string_constant / read_top_level_string_constant
/ patch_pinned_key / bump_version) - the ones that temporarily rewrite
real constants in utils/self_update.py and utils/config.py before a
throwaway CI binary is built from them (see that module's docstring).

Regression coverage for the 2.10.16 fix: a routine `ruff format` reformat
(2.10.14) changed PINNED_SIGNING_PUBLIC_KEY_B64/
PINNED_SIGNING_KEY_FINGERPRINT's declaration from a multi-line
parenthesized single-quoted form to a single-line double-quoted form,
which silently broke the old regex-based patcher (0 matches instead of
1) and made every selfupdate-e2e.yml run fail downstream instead. These
tests feed the AST-based replacement BOTH shapes directly (plus a
couple of other plausible reformattings) and assert all of them patch
correctly - the whole point being that this must survive quote-style,
line-wrapping, and parenthesization changes without being touched again.
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "selfupdate_e2e")
)

import build_fixtures as bf

OLD_MULTILINE_PARENTHESIZED_FORM = (
    "PINNED_SIGNING_PUBLIC_KEY_B64 = (\n"
    "    'AAAAC3NzaC1lZDI1NTE5AAAAIINUnyyTuXRhMU7XEpgBwm3dKrkv0D3U7mz+21piPb8q'\n"
    ")\n"
    "PINNED_SIGNING_KEY_FINGERPRINT = 'SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU'\n"
)

NEW_SINGLE_LINE_DOUBLE_QUOTED_FORM = (
    'PINNED_SIGNING_PUBLIC_KEY_B64 = "AAAAC3NzaC1lZDI1NTE5AAAAIINUnyyTuXRhMU7XEpgBwm3dKrkv0D3U7mz+21piPb8q"\n'
    'PINNED_SIGNING_KEY_FINGERPRINT = "SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU"\n'
)

# A third, deliberately different-again shape (single-line, single
# quotes, no surrounding blank line) - proving this isn't just special
# -cased to the two known-observed forms above.
THIRD_PLAUSIBLE_FORM = (
    "PINNED_SIGNING_PUBLIC_KEY_B64 = 'AAAAC3NzaC1lZDI1NTE5AAAAIINUnyyTuXRhMU7XEpgBwm3dKrkv0D3U7mz+21piPb8q'\n"
    "PINNED_SIGNING_KEY_FINGERPRINT = 'SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU'\n"
)


class TestReplaceTopLevelStringConstant:
    """The regression: this must patch identically no matter how the
    two PINNED_SIGNING_* assignments happen to be formatted."""

    @pytest.mark.parametrize(
        "source",
        [OLD_MULTILINE_PARENTHESIZED_FORM, NEW_SINGLE_LINE_DOUBLE_QUOTED_FORM, THIRD_PLAUSIBLE_FORM],
        ids=["old_multiline_parenthesized", "new_single_line_double_quoted", "third_plausible_single_quoted"],
    )
    def test_patches_pinned_key_and_fingerprint_regardless_of_source_shape(self, source):
        patched = bf.replace_top_level_string_constant(source, "PINNED_SIGNING_PUBLIC_KEY_B64", "TEST_PUB_B64")
        patched = bf.replace_top_level_string_constant(patched, "PINNED_SIGNING_KEY_FINGERPRINT", "SHA256:testfpr")

        assert bf.read_top_level_string_constant(patched, "PINNED_SIGNING_PUBLIC_KEY_B64") == "TEST_PUB_B64"
        assert bf.read_top_level_string_constant(patched, "PINNED_SIGNING_KEY_FINGERPRINT") == "SHA256:testfpr"
        # Must still be valid, parseable Python afterward.
        compile(patched, "<patched>", "exec")

    def test_leaves_everything_else_in_the_file_untouched(self):
        source = f"BEFORE = 1\n{NEW_SINGLE_LINE_DOUBLE_QUOTED_FORM}AFTER = 2\n"
        patched = bf.replace_top_level_string_constant(source, "PINNED_SIGNING_PUBLIC_KEY_B64", "X")
        assert "BEFORE = 1\n" in patched
        assert "AFTER = 2\n" in patched
        assert 'PINNED_SIGNING_KEY_FINGERPRINT = "SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU"' in patched

    def test_new_value_containing_quotes_is_still_a_safe_literal(self):
        # repr()-based, not naive string interpolation - a value with a
        # single quote in it must not produce broken/unparseable source.
        patched = bf.replace_top_level_string_constant("X = 'a'\n", "X", "it's-fine")
        assert bf.read_top_level_string_constant(patched, "X") == "it's-fine"
        compile(patched, "<patched>", "exec")

    def test_fails_loudly_when_the_constant_cannot_be_found(self):
        with pytest.raises(SystemExit):
            bf.replace_top_level_string_constant("SOMETHING_ELSE = '1'\n", "PINNED_SIGNING_PUBLIC_KEY_B64", "x")

    def test_fails_loudly_on_more_than_one_match(self):
        source = "A = '1'\nA = '2'\n"
        with pytest.raises(SystemExit):
            bf.replace_top_level_string_constant(source, "A", "x")

    def test_fails_loudly_when_the_constant_is_not_a_plain_string_literal(self):
        # e.g. moved inside a function/class, or built from an
        # expression - still must not silently no-op.
        source = "def f():\n    PINNED_SIGNING_PUBLIC_KEY_B64 = '1'\n"
        with pytest.raises(SystemExit):
            bf.replace_top_level_string_constant(source, "PINNED_SIGNING_PUBLIC_KEY_B64", "x")


class TestReadTopLevelStringConstant:
    def test_reads_value_from_either_known_shape(self):
        for source in (OLD_MULTILINE_PARENTHESIZED_FORM, NEW_SINGLE_LINE_DOUBLE_QUOTED_FORM):
            value = bf.read_top_level_string_constant(source, "PINNED_SIGNING_PUBLIC_KEY_B64")
            assert value == "AAAAC3NzaC1lZDI1NTE5AAAAIINUnyyTuXRhMU7XEpgBwm3dKrkv0D3U7mz+21piPb8q"

    def test_fails_loudly_when_missing(self):
        with pytest.raises(SystemExit):
            bf.read_top_level_string_constant("X = '1'\n", "MISSING_NAME")


class TestPatchPinnedKeyAndBumpVersion:
    """End-to-end against real file-on-disk helpers (patch_pinned_key /
    restore_file / read_version / bump_version), against a throwaway
    copy of the repo's real utils/self_update.py and utils/config.py -
    proves the on-disk round trip (patch -> build -> restore) that
    build_fixtures.py's main() relies on, without mutating this actual
    checkout."""

    @pytest.fixture
    def fake_repo(self, tmp_path):
        repo_root = tmp_path / "repo"
        (repo_root / "utils").mkdir(parents=True)
        for relative, content in (
            (bf.SELF_UPDATE_PY_RELATIVE, NEW_SINGLE_LINE_DOUBLE_QUOTED_FORM),
            (bf.CONFIG_PY_RELATIVE, '__version__ = "1.2.3"\n'),
        ):
            (repo_root / relative).write_text(content, encoding="utf-8")
        return str(repo_root)

    def test_patch_pinned_key_round_trips(self, fake_repo):
        path = os.path.join(fake_repo, bf.SELF_UPDATE_PY_RELATIVE)
        original = bf.patch_pinned_key(fake_repo, "NEWPUBKEY", "SHA256:newfpr")

        with open(path, encoding="utf-8") as f:
            patched = f.read()
        assert "NEWPUBKEY" in patched
        assert "SHA256:newfpr" in patched

        bf.restore_file(fake_repo, bf.SELF_UPDATE_PY_RELATIVE, original)
        with open(path, encoding="utf-8") as f:
            restored = f.read()
        assert restored == original == NEW_SINGLE_LINE_DOUBLE_QUOTED_FORM

    def test_patch_pinned_key_round_trips_against_the_old_multiline_shape(self, fake_repo):
        path = os.path.join(fake_repo, bf.SELF_UPDATE_PY_RELATIVE)
        with open(path, "w", encoding="utf-8") as f:
            f.write(OLD_MULTILINE_PARENTHESIZED_FORM)

        original = bf.patch_pinned_key(fake_repo, "NEWPUBKEY", "SHA256:newfpr")
        with open(path, encoding="utf-8") as f:
            patched = f.read()
        assert "NEWPUBKEY" in patched
        assert "SHA256:newfpr" in patched

        bf.restore_file(fake_repo, bf.SELF_UPDATE_PY_RELATIVE, original)
        with open(path, encoding="utf-8") as f:
            restored = f.read()
        assert restored == OLD_MULTILINE_PARENTHESIZED_FORM

    def test_bump_version_round_trips(self, fake_repo):
        content, version = bf.read_version(fake_repo)
        assert version == "1.2.3"

        new_version = bf.synthetic_higher_version(version)
        assert new_version == "1.2.73"
        bf.bump_version(fake_repo, new_version)

        _, bumped_version = bf.read_version(fake_repo)
        assert bumped_version == new_version

        bf.restore_file(fake_repo, bf.CONFIG_PY_RELATIVE, content)
        restored_content, restored_version = bf.read_version(fake_repo)
        assert restored_version == "1.2.3"
        assert restored_content == content


class TestAgainstThisRepoRealSelfUpdatePy:
    """The actual regression check: run the real patch_pinned_key /
    restore_file against a throwaway copy of THIS repo's real, current
    utils/self_update.py (whatever shape it's in today) - not a
    hand-written fixture string - so a future reformat that this test
    suite hasn't been updated for yet is still exercised for real."""

    def test_round_trips_against_this_repos_actual_self_update_py(self, tmp_path):
        real_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        real_path = os.path.join(real_repo_root, bf.SELF_UPDATE_PY_RELATIVE)
        with open(real_path, encoding="utf-8") as f:
            real_content = f.read()

        fake_repo = tmp_path / "repo"
        (fake_repo / "utils").mkdir(parents=True)
        target = fake_repo / bf.SELF_UPDATE_PY_RELATIVE
        target.write_text(real_content, encoding="utf-8")

        original = bf.patch_pinned_key(str(fake_repo), "NEWPUBKEY", "SHA256:newfpr")
        assert original == real_content
        with open(target, encoding="utf-8") as f:
            patched = f.read()
        assert "NEWPUBKEY" in patched
        assert "SHA256:newfpr" in patched
        compile(patched, str(target), "exec")

        bf.restore_file(str(fake_repo), bf.SELF_UPDATE_PY_RELATIVE, original)
        with open(target, encoding="utf-8") as f:
            restored = f.read()
        assert restored == real_content
