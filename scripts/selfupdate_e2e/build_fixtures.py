"""Builds everything the self-update real end-to-end CI workflow
(.github/workflows/selfupdate-e2e.yml) needs: two real PyInstaller
binaries (the current version, "old", and a synthetic higher version,
"new"), a non-functional "broken" placeholder asset for the
new-binary-fails-to-boot rollback scenario, and five signed release
fixture directories (good / bad_hash / bad_sig / rollback /
missing_asset) - each a SHA256SUMS.txt + SHA256SUMS.txt.sig pair plus
(for every scenario except missing_asset - see
build_missing_asset_release_dir's own docstring for why that one is
different) the asset they describe, exactly what
utils.self_update.download_and_verify_update() expects to find on a
real GitHub release.

CI-only, never used by the real release pipeline (scripts/release.sh,
.github/workflows/release.yml) and never touches the real signing key:
generates its own throwaway ed25519 keypair with `ssh-keygen` and
temporarily patches THIS EPHEMERAL CHECKOUT's own copy of
utils/self_update.py's PINNED_SIGNING_PUBLIC_KEY_B64/
PINNED_SIGNING_KEY_FINGERPRINT to match it before building the
binaries, restoring the real constants immediately after - the
resulting "old"/"new" binaries trust ONLY the throwaway key baked in at
that moment, so this workflow run's fake release server can sign
fixtures for them without ever touching (or needing) the real
maintainer-only private key described in RELEASING.md.

Usage:
  python build_fixtures.py --repo-root <path> --work-dir <path> [--pyinstaller <path>]
"""

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys


def sh(cmd, **kwargs):
    print(f"+ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def asset_name_for_this_platform():
    if sys.platform == "win32":
        return "curatarr-windows-x86_64.exe", "curatarr.exe"
    if sys.platform == "darwin":
        return "curatarr-macos-arm64", "curatarr"
    if sys.platform.startswith("linux"):
        import platform as _platform

        machine = _platform.machine().lower()
        if machine in ("aarch64", "arm64"):
            return "curatarr-linux-arm64", "curatarr"
        return "curatarr-linux-x86_64", "curatarr"
    raise SystemExit(f"unsupported platform for this E2E harness: {sys.platform}")


def generate_test_signing_key(work_dir):
    key_path = os.path.join(work_dir, "test_signing_key")
    sh(["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", "", "-C", "selfupdate-e2e-test-key"])
    with open(key_path + ".pub") as f:
        pub_line = f.read().strip()
    # "ssh-ed25519 <base64-blob> comment" - PINNED_SIGNING_PUBLIC_KEY_B64
    # is exactly that middle base64 field, no prefix/comment.
    pub_b64 = pub_line.split()[1]
    fpr_out = subprocess.run(
        ["ssh-keygen", "-lf", key_path + ".pub"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    fingerprint = fpr_out.split()[1]
    assert fingerprint.startswith("SHA256:"), f"unexpected ssh-keygen -lf output: {fpr_out!r}"
    allowed_signers_path = os.path.join(work_dir, "test_allowed_signers")
    with open(allowed_signers_path, "w") as f:
        f.write(f"e2e-test ssh-ed25519 {pub_b64}\n")
    return {
        "key_path": key_path,
        "pub_b64": pub_b64,
        "fingerprint": fingerprint,
        "allowed_signers_path": allowed_signers_path,
    }


def generate_attacker_signing_key(work_dir):
    """A second, unrelated throwaway key - never the one baked into the
    test binaries - used to produce a signature that's well-formed but
    must be REJECTED (wrong-key case for the bad_sig fixture)."""
    key_path = os.path.join(work_dir, "attacker_signing_key")
    sh(["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", "", "-C", "selfupdate-e2e-attacker-key"])
    return key_path


SELF_UPDATE_PY_RELATIVE = os.path.join("utils", "self_update.py")
CONFIG_PY_RELATIVE = os.path.join("utils", "config.py")


# =============================================================================
# Source patching - by AST shape, never by source text shape.
#
# This file has to rewrite two throwaway constants inside a REAL source
# file before building a binary from it (see module docstring), then put
# the original text back. A previous version of this did that via a
# regex matched against the constant declaration's exact text layout
# (quote style/line-wrapping/parenthesization) - a routine `ruff format`
# reformat (2.10.14) changed that layout, the regex silently matched
# zero times, and patch_pinned_key() never ran, so every fixture build
# quietly built binaries that still trusted the REAL pinned key instead
# of the throwaway one, and the whole workflow failed downstream instead
# (see 2.10.16 CHANGELOG). Fixed by locating the assignment by parsing
# the file into an AST and matching on the target's NAME - which is
# identical however the assignment happens to be formatted - and
# rewriting only that node's exact span. Still fails loudly (raises
# SystemExit) if it can't find exactly one matching assignment, rather
# than silently skipping the patch - that fail-loud design is what
# surfaced the original regex break in the first place.
# =============================================================================


def _line_start_offsets(source):
    """offsets[i] is the absolute character offset of the start of line
    i + 1 (ast line numbers are 1-indexed) - lets an ast node's
    (lineno, col_offset)/(end_lineno, end_col_offset) be converted into
    plain string slice indices."""
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _find_top_level_string_assignments(tree, name):
    """Every top-level (module body) `<name> = "<string literal>"`
    Assign node in `tree` - matched on AST shape (a single Name target
    with this exact id, assigned a string constant), never on source
    text shape, so quote style/line-wrapping/parenthesization can't
    hide or duplicate a match."""
    matches = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            matches.append(node)
    return matches


def _require_one(tree, name):
    matches = _find_top_level_string_assignments(tree, name)
    if len(matches) != 1:
        raise SystemExit(
            f'expected exactly one top-level `{name} = "..."` assignment, found {len(matches)} '
            "- has that constant's declaration changed shape (renamed, moved into a class/function, "
            "no longer a plain string literal)?"
        )
    return matches[0]


def read_top_level_string_constant(source, name):
    """Returns the string value of the sole top-level `name = "..."`
    assignment in `source`. Fails loudly if it can't find exactly one."""
    node = _require_one(ast.parse(source), name)
    return node.value.value


def replace_top_level_string_constant(source, name, new_value):
    """Rewrites the sole top-level `name = "..."` assignment in `source`
    to `name = <repr(new_value)>`, in place of whatever the original
    assignment's exact text looked like. Fails loudly if it can't find
    exactly one such assignment."""
    tree = ast.parse(source)
    node = _require_one(tree, name)
    offsets = _line_start_offsets(source)
    start = offsets[node.lineno - 1] + node.col_offset
    end = offsets[node.end_lineno - 1] + node.end_col_offset
    return source[:start] + f"{name} = {new_value!r}" + source[end:]


def patch_pinned_key(repo_root, pub_b64, fingerprint):
    path = os.path.join(repo_root, SELF_UPDATE_PY_RELATIVE)
    with open(path, encoding="utf-8") as f:
        original = f.read()
    patched = replace_top_level_string_constant(original, "PINNED_SIGNING_PUBLIC_KEY_B64", pub_b64)
    patched = replace_top_level_string_constant(patched, "PINNED_SIGNING_KEY_FINGERPRINT", fingerprint)
    with open(path, "w", encoding="utf-8") as f:
        f.write(patched)
    return original


def restore_file(repo_root, relative_path, original_content):
    path = os.path.join(repo_root, relative_path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(original_content)


def read_version(repo_root):
    path = os.path.join(repo_root, CONFIG_PY_RELATIVE)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    version = read_top_level_string_constant(content, "__version__")
    return content, version


def bump_version(repo_root, new_version):
    path = os.path.join(repo_root, CONFIG_PY_RELATIVE)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    patched = replace_top_level_string_constant(content, "__version__", new_version)
    with open(path, "w", encoding="utf-8") as f:
        f.write(patched)


def synthetic_higher_version(current):
    major, minor, patch = (int(x) for x in current.split("."))
    return f"{major}.{minor}.{patch + 70}"


def build_binary(repo_root, pyinstaller_bin, dist_name, out_path):
    dist_dir = os.path.join(repo_root, "dist")
    build_dir = os.path.join(repo_root, "build")
    if os.path.isdir(dist_dir):
        shutil.rmtree(dist_dir)
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir)
    sh([pyinstaller_bin, "--clean", "--noconfirm", "curatarr.spec"], cwd=repo_root)
    built = os.path.join(dist_dir, dist_name)
    if not os.path.isfile(built):
        raise SystemExit(f"PyInstaller did not produce expected output at {built}")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    shutil.copy2(built, out_path)
    if os.name != "nt":
        os.chmod(out_path, 0o755)
    return out_path


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sign_sums_file(sums_path, signing_key_path):
    sig_path = sums_path + ".sig"
    if os.path.isfile(sig_path):
        os.remove(sig_path)
    sh(["ssh-keygen", "-Y", "sign", "-f", signing_key_path, "-n", "file", sums_path])
    if not os.path.isfile(sig_path):
        raise SystemExit(f"ssh-keygen did not produce {sig_path}")
    return sig_path


def build_release_dir(release_dir, asset_name, asset_bytes_source_path, signing_key_path, corrupt_hash=False):
    os.makedirs(release_dir, exist_ok=True)
    asset_dest = os.path.join(release_dir, asset_name)
    shutil.copy2(asset_bytes_source_path, asset_dest)
    real_hash = sha256_file(asset_dest)
    recorded_hash = ("0" * 64) if corrupt_hash else real_hash
    sums_path = os.path.join(release_dir, "SHA256SUMS.txt")
    with open(sums_path, "w", newline="\n") as f:
        f.write(f"{recorded_hash}  {asset_name}\n")
    sign_sums_file(sums_path, signing_key_path)
    return {"dir": release_dir, "asset": asset_dest, "real_hash": real_hash, "recorded_hash": recorded_hash}


def build_missing_asset_release_dir(release_dir, requested_asset_name, signing_key_path):
    """Models discussion #207's exact real-world situation: a release
    that genuinely exists and publishes a validly-signed SHA256SUMS.txt
    - it just never shipped the specific asset filename an OLDER
    binary's self-updater still requests (e.g. the retired
    'curatarr-macos-universal' name dropped once macOS binaries went
    arm64-only - see utils/self_update.py's ASSET_MACOS_ARM64 comment).

    Deliberately does NOT write `requested_asset_name` into this
    directory at all, so fake_release_server.py's existing "file not
    on disk -> 404" branch fires for it exactly as it would against a
    real GitHub release missing that one asset - no changes needed to
    that server. SHA256SUMS.txt/.sig are both present and correctly
    signed (the release/tag itself is real) but list a DIFFERENT,
    unrelated filename instead of requested_asset_name, mirroring a
    real SHA256SUMS.txt regenerated for a release that simply never
    published that name.

    This deliberately never even reaches verify_downloaded_asset()'s
    own "asset not listed in the verified sums file" check
    (HashMismatchError) - the real failure this models happens one
    step EARLIER, at the asset download itself (DownloadError), which
    is what utils.self_update._download_and_verify_update_impl
    actually hits first: it downloads SHA256SUMS.txt/.sig, THEN the
    platform asset - see that function's docstring."""
    os.makedirs(release_dir, exist_ok=True)
    unrelated_name = "curatarr-some-other-platform-asset"
    unrelated_bytes = b"stands in for a different release asset entirely - never downloaded by this scenario"
    unrelated_hash = hashlib.sha256(unrelated_bytes).hexdigest()
    sums_path = os.path.join(release_dir, "SHA256SUMS.txt")
    with open(sums_path, "w", newline="\n") as f:
        f.write(f"{unrelated_hash}  {unrelated_name}\n")
    sign_sums_file(sums_path, signing_key_path)
    return {"dir": release_dir}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", required=True)
    p.add_argument("--work-dir", required=True)
    p.add_argument("--pyinstaller", default="pyinstaller")
    args = p.parse_args()

    repo_root = os.path.abspath(args.repo_root)
    work_dir = os.path.abspath(args.work_dir)
    os.makedirs(work_dir, exist_ok=True)

    asset_name, dist_name = asset_name_for_this_platform()
    print(f"== platform asset name: {asset_name} (dist: {dist_name})")

    test_key = generate_test_signing_key(work_dir)
    attacker_key_path = generate_attacker_signing_key(work_dir)
    print(f"== test signing key fingerprint: {test_key['fingerprint']}")

    original_self_update_py = patch_pinned_key(repo_root, test_key["pub_b64"], test_key["fingerprint"])
    original_config_content, current_version = read_version(repo_root)
    new_version = synthetic_higher_version(current_version)
    print(f"== old version: {current_version}  new (synthetic) version: {new_version}")

    # Build order was temporarily swapped (new-then-old) as a
    # diagnostic to rule out "second build in the same job is
    # unreliable regardless of version" as the cause of a real relaunch
    # failure investigated in this repo's v2.8.29 PR description -
    # ruled out (the failure stayed with the newer version either way),
    # so this reverts to the semantically clearer old-then-new order.
    try:
        old_binary_path = build_binary(
            repo_root,
            args.pyinstaller,
            dist_name,
            os.path.join(work_dir, "binaries", "old", asset_name),
        )

        bump_version(repo_root, new_version)
        new_binary_path = build_binary(
            repo_root,
            args.pyinstaller,
            dist_name,
            os.path.join(work_dir, "binaries", "new", asset_name),
        )
    finally:
        restore_file(repo_root, CONFIG_PY_RELATIVE, original_config_content)
        restore_file(repo_root, SELF_UPDATE_PY_RELATIVE, original_self_update_py)

    # A plausible-looking but entirely non-functional "binary" - passes
    # signature+hash verification (it IS exactly the bytes SHA256SUMS.txt
    # describes) but can never actually serve /healthz, simulating a
    # build that's corrupt/broken in some way verification can't catch -
    # exactly the case the hand-off script's rollback exists for.
    broken_binary_path = os.path.join(work_dir, "binaries", "broken", asset_name)
    os.makedirs(os.path.dirname(broken_binary_path), exist_ok=True)
    with open(broken_binary_path, "wb") as f:
        f.write(b"#!/bin/sh\nexit 1\n" if os.name != "nt" else b"not a real PE executable\r\n")
    if os.name != "nt":
        os.chmod(broken_binary_path, 0o755)

    releases_dir = os.path.join(work_dir, "releases")
    manifest = {
        "asset_name": asset_name,
        "old_version": current_version,
        "new_version": new_version,
        "old_binary": old_binary_path,
        "releases": {},
    }

    manifest["releases"]["good"] = build_release_dir(
        os.path.join(releases_dir, "good"),
        asset_name,
        new_binary_path,
        test_key["key_path"],
    )
    manifest["releases"]["bad_hash"] = build_release_dir(
        os.path.join(releases_dir, "bad_hash"),
        asset_name,
        new_binary_path,
        test_key["key_path"],
        corrupt_hash=True,
    )
    manifest["releases"]["bad_sig"] = build_release_dir(
        os.path.join(releases_dir, "bad_sig"),
        asset_name,
        new_binary_path,
        attacker_key_path,
    )
    manifest["releases"]["rollback"] = build_release_dir(
        os.path.join(releases_dir, "rollback"),
        asset_name,
        broken_binary_path,
        test_key["key_path"],
    )
    manifest["releases"]["missing_asset"] = build_missing_asset_release_dir(
        os.path.join(releases_dir, "missing_asset"),
        asset_name,
        test_key["key_path"],
    )

    manifest_path = os.path.join(work_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"== manifest written to {manifest_path}")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
