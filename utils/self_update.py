"""
In-binary self-update for the PyInstaller-packaged curatarr executable.

Source installs already have a real, signature-verified one-click update
path: run.sh's/run.ps1's own select_verified_release() (git tag, pinned
signer fingerprint, verified BEFORE checkout - see RELEASING.md), driven
from the CLI's interactive prompt or the web UI's "Update now" button
(web/update_apply.py). A downloaded standalone binary has no git
checkout to `git pull` - this module is the equivalent trust chain for
that case: download the new binary over HTTPS, cryptographically prove
it's authentic, and only then replace the running executable on disk.

Only ever meaningful when `sys.frozen` (see curatarr_app.py / curatarr.spec) -
every public entry point below either no-ops or raises NotFrozenError
for a source install, which keeps using run.sh/run.ps1 exclusively.

Authenticity model (fail-closed at every step - a broken/tampered/
unreachable ANYTHING here means "keep running the current binary",
never "run something unverified")
------------------------------------------------------------------
1. utils.update_check (already unauthenticated/advisory - see that
   module's docstring) says a newer version NUMBER exists. This is
   NEVER trusted for anything beyond "is it worth trying" - it decides
   nothing about what bytes end up on disk.
2. The asset for this platform, `SHA256SUMS.txt`, and
   `SHA256SUMS.txt.sig` are downloaded over HTTPS from GitHub Releases.
   All three are, at this point, still just as untrusted as step 1 - a
   compromised CDN edge or a MITM'd connection could serve anything.
3. `SHA256SUMS.txt.sig` is a detached OpenSSH SSH-SIGNATURE (see
   OpenSSH's PROTOCOL.sshsig) over `SHA256SUMS.txt`, produced offline
   by `ssh-keygen -Y sign` with the maintainer's release-signing
   private key (see scripts/sign-release-checksums.sh) - the exact
   same key (same pinned fingerprint, `PINNED_SIGNING_KEY_FINGERPRINT`
   below) already used to sign every release git tag (RELEASING.md).
   That signature is verified here in pure Python (see "Why pure
   Python, not `ssh-keygen -Y verify`" below) against
   `PINNED_SIGNING_PUBLIC_KEY_B64`, a constant HARDCODED in this file -
   never read from the downloaded files, argv, environment, or any
   other attacker-reachable input. Verification failure (missing sig,
   tampered sums file, wrong key, corrupt armor, anything) raises
   SignatureVerificationError - fail closed, no swap, ever.
4. Only once that signature verifies is `SHA256SUMS.txt`'s CONTENT
   trusted as the source of truth for what the downloaded asset's
   SHA256 should be. The asset's actual SHA256 is computed locally and
   compared - any mismatch raises HashMismatchError, again fail closed.
5. Only after BOTH of the above succeed does anything touch the
   filesystem at the target path. Everything above (steps 1-4) lives
   entirely in this module and is IDENTICAL regardless of caller. What
   happens with the resulting verified binary differs by caller:
     - CLI (`curatarr --self-update`): perform_self_update() swaps it
       into place IN-PROCESS (swap_binary()) and returns - safe here
       specifically because the CLI never relaunches anything
       afterward (see that function's docstring).
     - Web UI "Update now" (frozen binaries): download_and_verify_update()
       is called directly, and the resulting VerifiedUpdate is handed
       off to a plain EXTERNAL script (utils/self_update_handoff.py)
       that does the actual swap+relaunch AFTER this process exits -
       see that module's docstring for why the swap+relaunch could not
       safely stay in-process for this caller (PyInstaller onefile
       extraction-directory state racing/inheriting across a
       spawned-from-within-a-frozen-process relaunch, confirmed via
       real end-to-end testing - see the v2.8.29 PR history).

Why pure Python (the `cryptography` package), not shelling out to
`ssh-keygen -Y verify`
------------------------------------------------------------------
docs/BINARIES.md's entire pitch for these binaries is "no Python
install, no git clone, no pip install" - a self-updater that then turns
around and requires a system `ssh-keygen` binary on PATH would silently
break that promise for exactly the users most likely to hit it: a
Windows machine with no Git/OpenSSH installed, or a minimal/scratch
Linux container image. Beyond portability, shelling out to a
PATH-resolved external binary for a step this security-critical is
itself an attacker surface (PATH manipulation, a wrong/old ssh-keygen
build, a missing binary that some future refactor mishandles as
fail-open instead of fail-closed) that a self-contained, PyInstaller-
bundled pure-Python implementation avoids entirely - the verification
code and its only trusted input (the hardcoded key constant below) ship
inside the same signed-off binary as everything else, with no runtime
dependency on what else happens to be installed on the machine it's
running on. The `cryptography` package is added to requirements.txt (a
core runtime dependency, not build-only) precisely so it's bundled into
every platform's binary by PyInstaller - see curatarr.spec.

Never touches the per-user data directory (utils.helpers.get_project_root() -
config/cache/logs) - only the running executable's own file and a
throwaway temp file next to it. See swap_binary()'s docstring for why
the temp download has to live in that same directory rather than the
system temp dir.
"""

import base64
import binascii
import hashlib
import logging
import os
import platform
import re
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Dict, Mapping, NamedTuple, Optional

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .config import __version__
from .metrics import record_self_update_attempt
from .update_check import GITHUB_RELEASES_PAGE, parse_version, update_available

logger = logging.getLogger("curatarr")

# =============================================================================
# Errors - every failure mode below is one of these. Callers (curatarr_app.py's
# --self-update dispatch, web/update_apply.py's frozen worker branch) treat
# all of them identically: log it, swap nothing, keep/relaunch whatever
# binary is currently on disk. See module docstring.
# =============================================================================


class SelfUpdateError(Exception):
    """Base class for every self-update failure."""


class NotFrozenError(SelfUpdateError):
    """Called from a source (non-frozen) install - use run.sh/run.ps1."""


class UnsupportedPlatformError(SelfUpdateError):
    """No published binary asset matches this OS/architecture."""


class NoUpdateAvailableError(SelfUpdateError):
    """No verified-newer version is currently known/reachable."""


class DownloadError(SelfUpdateError):
    """A required release asset could not be downloaded."""


class SignatureVerificationError(SelfUpdateError):
    """SHA256SUMS.txt.sig failed to verify against the pinned key."""


class HashMismatchError(SelfUpdateError):
    """The downloaded binary's SHA256 didn't match the verified sums file."""


class SwapError(SelfUpdateError):
    """The verified binary could not be swapped into place."""


class VersionMismatchError(SelfUpdateError):
    """SHA256SUMS.txt's signed `# curatarr-version:` line (see
    .github/workflows/release.yml's finalize-checksums job) didn't match
    the version this download was actually for - a signal something is
    wrong (e.g. a stale or substituted SHA256SUMS.txt) even though every
    individual asset hash inside it still verified against the pinned
    signature. Only ever raised when the line IS present but WRONG - an
    absent line (a release published before this field existed) is not
    an error; see download_and_verify_update's docstring."""


class VersionReadbackError(SelfUpdateError):
    """After swapping in a verified binary, running it with --version
    either failed outright or printed something other than the expected
    version - see perform_self_update's docstring. The previous binary
    is restored before this is raised."""


# =============================================================================
# Platform -> release asset selection (section C.2)
# =============================================================================

# Filenames exactly as published by .github/workflows/release.yml - see
# docs/BINARIES.md's asset table.
ASSET_WINDOWS_X86_64 = "curatarr-windows-x86_64.exe"
ASSET_LINUX_X86_64 = "curatarr-linux-x86_64"
ASSET_LINUX_ARM64 = "curatarr-linux-arm64"
# Canonical name as of the release that dropped Intel macOS support
# (cryptography 49.0.0 removed x86_64 macOS wheels entirely, and
# GitHub's last Intel macOS runner is retired) - macOS binaries are now
# arm64-only, same one-name-per-platform shape as every other OS. The
# old 'curatarr-macos-universal' name was published as an
# identical-bytes duplicate for one transitional release so pre-2.10.0
# installs could still self-update at least once more (see CHANGELOG.md)
# - that duplicate has since been dropped, and this function has never
# had a fallback to it: it only ever runs INSIDE the binary requesting
# its OWN next asset, so it always requests the canonical name.
ASSET_MACOS_ARM64 = "curatarr-macos-arm64"

_X86_64_MACHINE_NAMES = ("x86_64", "amd64")
_ARM64_MACHINE_NAMES = ("aarch64", "arm64")


def select_asset_name(sys_platform: Optional[str] = None, machine: Optional[str] = None) -> str:
    """
    Pick the release asset filename for the platform this process is
    running on, mirroring docs/BINARIES.md's asset table exactly.

    Args:
        sys_platform: defaults to sys.platform - overridable for tests.
        machine: defaults to platform.machine() - overridable for tests.

    Returns:
        The exact asset filename as published on GitHub Releases.

    Raises:
        UnsupportedPlatformError: no published binary matches - abort
            clearly (never guess/fall back to a wrong asset).
    """
    sys_platform = sys.platform if sys_platform is None else sys_platform
    machine = platform.machine() if machine is None else machine
    machine_l = (machine or "").lower()

    if sys_platform == "win32":
        if machine_l in _X86_64_MACHINE_NAMES:
            return ASSET_WINDOWS_X86_64
        raise UnsupportedPlatformError(
            f"No self-update binary published for Windows/{machine} - only x86_64 is "
            f"built. Download manually: {GITHUB_RELEASES_PAGE}"
        )

    if sys_platform == "darwin":
        # Only an arm64 binary is published as of the release that
        # dropped Intel macOS support (see ASSET_MACOS_ARM64's own
        # comment) - no architecture branch here, same as before, but
        # now because there's only one build rather than because one
        # build covers both architectures.
        return ASSET_MACOS_ARM64

    if sys_platform.startswith("linux"):
        if machine_l in _X86_64_MACHINE_NAMES:
            return ASSET_LINUX_X86_64
        if machine_l in _ARM64_MACHINE_NAMES:
            return ASSET_LINUX_ARM64
        raise UnsupportedPlatformError(
            f"No self-update binary published for Linux/{machine} - only x86_64 and "
            f"arm64 are built. Download manually: {GITHUB_RELEASES_PAGE}"
        )

    raise UnsupportedPlatformError(
        f"No self-update binary published for platform {sys_platform!r}. Download manually: {GITHUB_RELEASES_PAGE}"
    )


# =============================================================================
# SSH SIGNATURE (SSHSIG) verification - pure Python, see module docstring's
# "Why pure Python" section. Implements the wire format from OpenSSH's
# PROTOCOL.sshsig: an armored blob wrapping (public key, namespace,
# reserved, hash algorithm, signature), where the signature itself
# covers a small wrapper structure containing a hash of the actual
# signed file - never the raw file bytes directly.
# =============================================================================

SIGNATURE_NAMESPACE = "file"  # matches `ssh-keygen -Y sign -n file` (scripts/sign-release-checksums.sh)

_SSHSIG_MAGIC = b"SSHSIG"
_SSHSIG_VERSION = 1
_SSHSIG_ARMOR_BEGIN = "-----BEGIN SSH SIGNATURE-----"
_SSHSIG_ARMOR_END = "-----END SSH SIGNATURE-----"
_SUPPORTED_HASH_ALGORITHMS = ("sha256", "sha512")
_SUPPORTED_SIGNATURE_ALGORITHM = b"ssh-ed25519"


class _ParsedSshsig(NamedTuple):
    public_key_blob: bytes
    namespace: str
    hash_algorithm: str
    signature_algorithm: bytes
    signature_raw: bytes


def _read_uint32(buf: bytes, offset: int) -> tuple:
    if offset + 4 > len(buf):
        raise SignatureVerificationError("Truncated SSH signature (expected a length prefix)")
    return struct.unpack(">I", buf[offset : offset + 4])[0], offset + 4


def _read_string(buf: bytes, offset: int) -> tuple:
    length, offset = _read_uint32(buf, offset)
    if length < 0 or offset + length > len(buf):
        raise SignatureVerificationError("Truncated SSH signature (expected a length-prefixed field)")
    return buf[offset : offset + length], offset + length


def _pack_string(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def _decode_armor(armored_signature: str) -> bytes:
    """Strip the '-----BEGIN/END SSH SIGNATURE-----' PEM-style armor and
    base64-decode the body. Raises SignatureVerificationError (never a
    bare exception) on anything malformed - a caller catching only this
    module's own exception types must never see a raw ValueError/
    binascii.Error escape from here."""
    text = (armored_signature or "").strip()
    if _SSHSIG_ARMOR_BEGIN not in text or _SSHSIG_ARMOR_END not in text:
        raise SignatureVerificationError("Not a valid SSH SIGNATURE block (missing PEM-style armor)")

    body_lines = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == _SSHSIG_ARMOR_BEGIN:
            inside = True
            continue
        if stripped == _SSHSIG_ARMOR_END:
            break
        if inside and stripped:
            body_lines.append(stripped)

    try:
        return base64.b64decode("".join(body_lines))
    except (binascii.Error, ValueError) as e:
        raise SignatureVerificationError(f"Malformed base64 in SSH signature: {e}") from e


def _parse_sshsig_blob(blob: bytes) -> _ParsedSshsig:
    offset = 0
    magic = blob[offset : offset + 6]
    offset += 6
    if magic != _SSHSIG_MAGIC:
        raise SignatureVerificationError("Bad SSH signature: missing SSHSIG magic preamble")

    version, offset = _read_uint32(blob, offset)
    if version != _SSHSIG_VERSION:
        raise SignatureVerificationError(f"Unsupported SSH signature version: {version}")

    public_key_blob, offset = _read_string(blob, offset)
    namespace_raw, offset = _read_string(blob, offset)
    _reserved, offset = _read_string(blob, offset)
    hash_algorithm_raw, offset = _read_string(blob, offset)
    signature_blob, offset = _read_string(blob, offset)

    sig_algorithm, sig_offset = _read_string(signature_blob, 0)
    signature_raw, sig_offset = _read_string(signature_blob, sig_offset)

    return _ParsedSshsig(
        public_key_blob=public_key_blob,
        namespace=namespace_raw.decode("utf-8", "replace"),
        hash_algorithm=hash_algorithm_raw.decode("ascii", "replace"),
        signature_algorithm=sig_algorithm,
        signature_raw=signature_raw,
    )


def _encode_ed25519_public_key_blob(public_key: Ed25519PublicKey) -> bytes:
    """Rebuild the SSH wire-format public key blob (string "ssh-ed25519"
    + string raw-key) FROM the cryptography key object - i.e. from
    something this module itself decoded from PINNED_SIGNING_PUBLIC_KEY_B64,
    never from the signature file being verified."""
    raw = public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return _pack_string(b"ssh-ed25519") + _pack_string(raw)


def verify_sshsig(
    message: bytes,
    armored_signature: str,
    public_key: Ed25519PublicKey,
    namespace: str = SIGNATURE_NAMESPACE,
) -> None:
    """
    Verify an OpenSSH SSHSIG detached signature over `message`, using an
    EXPLICITLY supplied Ed25519 public key - never a key read out of the
    signature file itself. That's what makes verification "not depend on
    anything an attacker controls" (module docstring): at the one real
    call site (verify_pinned_signature below), `public_key` always comes
    from THIS module's own hardcoded PINNED_SIGNING_PUBLIC_KEY_B64. This
    function takes it as a parameter purely so its PASS/FAIL/tamper
    logic is unit-testable against disposable test keypairs, without
    needing the real (offline, Windows-only) release-signing private
    key - see tests/test_self_update.py.

    Raises SignatureVerificationError (never returns False, never lets a
    bare exception escape) on ANY failure: malformed armor/blob, wrong
    namespace, unsupported hash/signature algorithm, a public key in the
    file that doesn't match `public_key`, or a cryptographically invalid
    signature. Returns None (silently) on success - fail-closed by
    construction, there is no path that returns normally without proof
    the signature was produced by the matching private key over exactly
    this message.
    """
    blob = _decode_armor(armored_signature)
    parsed = _parse_sshsig_blob(blob)

    if parsed.namespace != namespace:
        raise SignatureVerificationError(
            f"Unexpected SSH signature namespace: {parsed.namespace!r} (expected {namespace!r})"
        )

    if parsed.hash_algorithm not in _SUPPORTED_HASH_ALGORITHMS:
        raise SignatureVerificationError(f"Unsupported SSH signature hash algorithm: {parsed.hash_algorithm!r}")

    if parsed.signature_algorithm != _SUPPORTED_SIGNATURE_ALGORITHM:
        raise SignatureVerificationError(
            f"Unsupported SSH signature algorithm: {parsed.signature_algorithm!r} "
            f"(only {_SUPPORTED_SIGNATURE_ALGORITHM.decode()} is trusted)"
        )

    expected_public_key_blob = _encode_ed25519_public_key_blob(public_key)
    if parsed.public_key_blob != expected_public_key_blob:
        # Defense in depth, not the actual trust boundary: this can
        # never be the sole reason a forged signature is rejected (the
        # cryptographic verify() call below is what really enforces
        # that - a signature blob claiming OUR key but actually produced
        # by a different one still fails verify()). Failing fast here
        # just gives a clearer error than letting a key mismatch fall
        # through to a generic "invalid signature".
        raise SignatureVerificationError(
            "SSH signature's embedded public key does not match the pinned release-signing key"
        )

    digest = hashlib.new(parsed.hash_algorithm, message).digest()
    to_be_signed = (
        _SSHSIG_MAGIC
        + _pack_string(namespace.encode("utf-8"))
        + _pack_string(b"")  # reserved
        + _pack_string(parsed.hash_algorithm.encode("ascii"))
        + _pack_string(digest)
    )

    try:
        public_key.verify(parsed.signature_raw, to_be_signed)
    except InvalidSignature as e:
        raise SignatureVerificationError("SSH signature does not verify against the pinned release-signing key") from e


# =============================================================================
# Pinned key - the ONE trust anchor everything above ultimately resolves
# to. Same key (same fingerprint) as .github/allowed_signers, which
# verifies signed release TAGS; this is the same key's public half,
# hardcoded here (not read from that file, which isn't bundled into the
# binary at all) to verify signed CHECKSUM FILES instead. The private
# half never leaves the maintainer's Windows machine - see
# scripts/sign-release-checksums.sh and RELEASING.md.
# =============================================================================

PINNED_SIGNING_PUBLIC_KEY_B64 = "AAAAC3NzaC1lZDI1NTE5AAAAIINUnyyTuXRhMU7XEpgBwm3dKrkv0D3U7mz+21piPb8q"
PINNED_SIGNING_KEY_FINGERPRINT = "SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU"


def compute_key_fingerprint(public_key_blob: bytes) -> str:
    """SHA256 SSH key fingerprint, matching `ssh-keygen -lf`'s own
    format exactly (SHA256 of the wire-format public key blob,
    base64-encoded, unpadded, "SHA256:" prefixed) - verified against a
    real `ssh-keygen -lf` output while this module was written."""
    digest = hashlib.sha256(public_key_blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _pinned_public_key_blob() -> bytes:
    try:
        blob = base64.b64decode(PINNED_SIGNING_PUBLIC_KEY_B64)
    except (binascii.Error, ValueError) as e:
        raise SignatureVerificationError(f"Corrupt pinned signing key constant: {e}") from e

    # Self-check: an edit to PINNED_SIGNING_PUBLIC_KEY_B64 (accidental or
    # malicious) must be caught here rather than silently starting to
    # trust a different key - same "pin the fingerprint independently of
    # the key material" defense already used for git tag verification
    # (scripts/release.sh, run.sh/run.ps1, .github/workflows/release.yml
    # all check this exact fingerprint too).
    actual_fingerprint = compute_key_fingerprint(blob)
    if actual_fingerprint != PINNED_SIGNING_KEY_FINGERPRINT:
        raise SignatureVerificationError(
            f"Pinned signing key integrity check failed: computed {actual_fingerprint}, "
            f"expected {PINNED_SIGNING_KEY_FINGERPRINT} - refusing to trust it"
        )
    return blob


def _pinned_public_key() -> Ed25519PublicKey:
    blob = _pinned_public_key_blob()
    algo, offset = _read_string(blob, 0)
    if algo != _SUPPORTED_SIGNATURE_ALGORITHM:
        raise SignatureVerificationError(f"Pinned signing key is not ssh-ed25519: {algo!r}")
    raw_key, offset = _read_string(blob, offset)
    return Ed25519PublicKey.from_public_bytes(raw_key)


def verify_pinned_signature(message: bytes, armored_signature: str) -> None:
    """The only verification entry point actually used by the real
    download/verify/swap flow below - always checks against this
    module's own hardcoded pinned key. See verify_sshsig()'s docstring
    for why that function itself takes the key as a parameter."""
    verify_sshsig(message, armored_signature, _pinned_public_key(), namespace=SIGNATURE_NAMESPACE)


# =============================================================================
# SHA256SUMS.txt parsing + local hashing
# =============================================================================

SUMS_FILENAME = "SHA256SUMS.txt"
SUMS_SIG_FILENAME = "SHA256SUMS.txt.sig"

# Matches both GNU coreutils' `sha256sum` output ("<hex>  <name>" or
# "<hex> *<name>" for binary mode) and `shasum -a 256`'s identical
# format - see scripts/release.sh / .github/workflows/release.yml, both
# of which use one or the other depending on OS.
_SUMS_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})\s+[* ]?(.+)$")

# Matches the `# curatarr-version: X.Y.Z` comment line
# .github/workflows/release.yml's finalize-checksums job writes into
# SHA256SUMS.txt (see that job's own comment for why - binding the
# version number itself to the same signature that already covers every
# asset's hash). Present on every release from v2.9.0 onward; absent on
# anything published before that - see download_and_verify_update's
# docstring for how an absent line is handled.
_VERSION_LINE_RE = re.compile(r"^#\s*curatarr-version:\s*(\S+)\s*$", re.IGNORECASE)


def parse_sha256sums_version(text: str) -> Optional[str]:
    """Extract the signed `# curatarr-version:` comment line from a
    SHA256SUMS.txt-style file's text, or None if it isn't present (an
    older release, published before this field existed - see
    _VERSION_LINE_RE's comment). A SEPARATE function from
    parse_sha256sums() below rather than folded into its return value,
    so that function's existing {filename: digest} contract (and every
    existing caller/test relying on exactly that shape) stays
    unchanged."""
    for line in text.splitlines():
        match = _VERSION_LINE_RE.match(line.strip())
        if match:
            return match.group(1)
    return None


def parse_sha256sums(text: str) -> Dict[str, str]:
    """Parse a SHA256SUMS.txt-style file into {filename: lowercase hex
    digest}. Silently skips blank lines, comments, and any line that
    doesn't match the expected format - malformed lines are never fatal
    here, since the real trust decision (verify_downloaded_asset) is
    "is the SPECIFIC asset we care about listed with a hash that
    matches", not "is the whole file well-formed"."""
    sums: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SUMS_LINE_RE.match(line)
        if not match:
            continue
        digest, filename = match.groups()
        sums[filename.strip()] = digest.lower()
    return sums


def sha256_file(path: str, chunk_size: int = 1 << 20) -> str:
    """Streaming SHA256 of a local file - never loads the whole file
    into memory (these are onefile binaries, tens of MB each)."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_downloaded_asset(
    asset_path: str, sums_path: str, sig_path: str, asset_name: str, target_version: Optional[str] = None
) -> str:
    """
    The full authenticity chain (module docstring steps 3-4) applied to
    one already-downloaded asset. Raises SignatureVerificationError or
    HashMismatchError on any failure. On success, returns the verified
    (lowercase hex) SHA256 digest - the SAME value already checked
    against the signed sums file here, handed back so a caller can
    re-check the file against THIS exact value again immediately before
    actually using it (see perform_self_update's TOCTOU re-hash and
    utils/self_update_handoff.py's own pre-swap re-hash), without
    needing to re-derive or re-trust anything.

    If target_version is given, also binds the version NUMBER itself to
    the same signature that already covers every asset hash (see
    _VERSION_LINE_RE's comment): a signed `# curatarr-version:` line
    present in sums_text but not equal to target_version raises
    VersionMismatchError - fail closed, since that combination can only
    mean a genuinely-signed-but-wrong-release SHA256SUMS.txt was served
    (e.g. a stale cache, a mix-up, or a downgrade/version-confusion
    attempt) even though the individual asset hash inside it still
    matches byte-for-byte. A MISSING version line is not an error (an
    older release, published before this field existed) - see this
    module's docstring and download_and_verify_update.
    """
    try:
        # 'rb', not 'r' - the signature is over the EXACT bytes that were
        # signed, and text-mode reads apply Python's universal-newline
        # translation (any \r\n or bare \r silently becomes \n) before we
        # ever see the content. A published SHA256SUMS.txt containing so
        # much as one CRLF line ending (observed in practice on the
        # Windows binary's checksum line - the sidecar that job writes
        # is CRLF, and finalize-checksums' aggregation carries that
        # through - see .github/workflows/release.yml) would then get
        # hashed as a DIFFERENT byte sequence than what
        # scripts/sign-release-checksums.sh actually signed, and
        # signature verification would fail for every self-update - not
        # a corrupted download, not a bad key, just this file being
        # decoded before its signature was checked. Decoding to text
        # (below) only happens AFTER verify_pinned_signature() has
        # already proven these exact bytes are the ones that were
        # signed - line-ending differences in the now-trusted text are
        # harmless from here on, since parse_sha256sums()/
        # parse_sha256sums_version() both use str.strip()/splitlines(),
        # which already treat \r\n, \r, and \n as equivalent.
        with open(sums_path, "rb") as f:
            sums_bytes = f.read()
        with open(sig_path, "rb") as f:
            sig_bytes = f.read()
    except OSError as e:
        # Missing/unreadable either file (e.g. the .sig never got
        # downloaded/published at all) is exactly as fail-closed as a
        # signature that doesn't verify - never fall through and treat
        # "we don't have a signature" as "unsigned is fine".
        raise SignatureVerificationError(f"Could not read checksum/signature files: {e}") from e

    # The armor is plain ASCII (BEGIN/END markers + base64 body) - _decode_armor
    # strips whitespace per line regardless, so decoding this one to text
    # (unlike sums_bytes above) has no bytes-vs-signature implication; it's
    # only ever parsed, never itself hashed or compared byte-for-byte.
    sig_text = sig_bytes.decode("utf-8")

    # Authenticity FIRST: SHA256SUMS.txt was just downloaded over
    # anonymous HTTPS and is no more trustworthy than the asset itself
    # until ITS signature verifies - only then does its content become a
    # trusted source of truth for what the asset's hash should be. An
    # attacker able to substitute the asset could trivially also
    # substitute a matching-but-unsigned (or unsigned-differently) sums
    # file, so checking the hash before the signature would verify
    # nothing. Verified against the RAW bytes read above - never a
    # decode()/encode() round trip, so there is no representation of
    # this content that could differ from what was actually signed.
    verify_pinned_signature(sums_bytes, sig_text)

    sums_text = sums_bytes.decode("utf-8")
    sums = parse_sha256sums(sums_text)
    expected = sums.get(asset_name)
    if not expected:
        raise HashMismatchError(f"{asset_name!r} is not listed in the verified {SUMS_FILENAME}")

    actual = sha256_file(asset_path)
    if actual.lower() != expected.lower():
        raise HashMismatchError(f"{asset_name} SHA256 mismatch: expected {expected}, got {actual}")

    if target_version is not None:
        signed_version = parse_sha256sums_version(sums_text)
        if signed_version is None:
            logger.info(
                f"{SUMS_FILENAME} has no signed version line (published before this "
                f"field existed) - skipping the version-binding check for v{target_version}"
            )
        elif signed_version != target_version:
            raise VersionMismatchError(
                f"{SUMS_FILENAME}'s signed version ({signed_version}) does not match "
                f"the requested release (v{target_version}) - refusing to trust it"
            )

    return actual.lower()


# =============================================================================
# Target version + download (section C.1, C.3)
# =============================================================================

# Overridable via CURATARR_RELEASES_DOWNLOAD_BASE_OVERRIDE for testing/
# staging - same reasoning as utils.update_check's
# CURATARR_RELEASES_API_OVERRIDE. IMPORTANT: this changes only WHERE the
# asset/SHA256SUMS.txt/.sig bytes are fetched from, never WHAT gets
# trusted - PINNED_SIGNING_PUBLIC_KEY_B64 below is a literal constant,
# never overridable by anything. An attacker who redirects this to a
# malicious server still cannot produce a signature that verifies
# against the pinned key, so this cannot weaken the authenticity model
# (module docstring) - only ever "which server", never "trust it
# anyway". Used by this repo's own real end-to-end self-update test
# (see the v2.8.29 PR description) to point a real built binary at a
# local HTTP server instead of github.com.
GITHUB_RELEASES_DOWNLOAD_BASE = (
    os.environ.get("CURATARR_RELEASES_DOWNLOAD_BASE_OVERRIDE")
    or "https://github.com/OrchestratedChaos/curatarr/releases/download"
)
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK_SIZE = 1 << 20


def determine_update_target(force_refresh: bool = True) -> str:
    """
    Ask utils.update_check whether a strictly-newer version is
    published (advisory-only, unauthenticated - module docstring step
    1). Returns the version string (no leading 'v') if so.

    Raises NoUpdateAvailableError otherwise - including when the check
    itself is unreachable/unknown, since "we don't know" must never be
    treated as "yes there's an update" (same contract as
    utils.update_check.update_available itself).
    """
    latest, current, is_newer = update_available(update_mode="notify", force_refresh=force_refresh)
    if not is_newer:
        raise NoUpdateAvailableError(
            f"No newer verified release available (current v{current}, latest known v{latest or 'unknown'})"
        )
    # update_available()'s own contract: is_newer is only ever True when
    # latest is a known, parseable version - never on "unknown" (see its
    # docstring), so this is never actually None once is_newer is True.
    assert latest is not None
    return latest


def release_asset_url(version: str, filename: str) -> str:
    return f"{GITHUB_RELEASES_DOWNLOAD_BASE}/v{version}/{filename}"


def _download_to_file(url: str, dest_path: str, timeout: float = DOWNLOAD_TIMEOUT_SECONDS) -> None:
    if not url.startswith(GITHUB_RELEASES_DOWNLOAD_BASE):
        # Defense in depth: every URL this module ever builds comes from
        # release_asset_url() above, which always prefixes
        # GITHUB_RELEASES_DOWNLOAD_BASE - this just guarantees a future
        # refactor can never accidentally fetch from anywhere else.
        # GITHUB_RELEASES_DOWNLOAD_BASE itself is hardcoded to
        # https://github.com/... in production, so this is normally
        # equivalent to (and strictly stronger than) an https://-only
        # check; it's only ever http:// when
        # CURATARR_RELEASES_DOWNLOAD_BASE_OVERRIDE is explicitly set for
        # local testing/staging (see that constant's own comment) - a
        # deliberate, narrow relaxation of transport security for a seam
        # that already can't weaken the actual authenticity check (the
        # pinned-key signature verification below is what's actually
        # trusted, never transport).
        raise DownloadError(f"Refusing download URL outside the configured release host: {url}")
    try:
        with requests.get(url, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
    except requests.RequestException as e:
        raise DownloadError(f"Could not download {url}: {e}") from e


# =============================================================================
# Atomic, per-OS binary swap (section C.4) + relaunch (section C.5)
# =============================================================================


def current_binary_path() -> str:
    """The running executable's own path - only meaningful when frozen
    (sys.executable IS the packaged exe itself, not a python.exe)."""
    return os.path.realpath(sys.executable)


def _old_sidecar_path(path: str) -> str:
    return path + ".old"


def cleanup_stale_old_binary(current_path: Optional[str] = None) -> None:
    """
    Best-effort removal of a leftover `<exe>.old` from a previous swap
    (see _swap_windows and _swap_posix below - both platforms leave one
    behind on a successful swap now, so a post-swap readback failure
    has something to restore from) - call unconditionally at every
    frozen startup (curatarr_app.py), not just after an update.

    A missing/undeletable `.old` is never fatal (already gone, or still
    locked by a lingering old process that hasn't exited yet - it'll
    get cleaned up on the NEXT startup instead): this is pure
    housekeeping, never a correctness or security boundary, so it never
    raises.
    """
    current_path = current_path or current_binary_path()
    old_path = _old_sidecar_path(current_path)
    try:
        if os.path.isfile(old_path):
            os.remove(old_path)
    except OSError as e:
        logger.debug(f"Could not remove stale {old_path}: {e}")


def _preserve_permissions(source_path: str, dest_path: str) -> None:
    """Best-effort - copies source_path's mode bits onto dest_path
    before it gets swapped into place, so a downloaded temp file (which
    starts world-unreadable-execute on POSIX) ends up with the same
    permissions the binary it's replacing had. Never fatal: a failure
    here just means the swap proceeds with whatever mode the new file
    already has (see _swap_posix, which independently ensures the exec
    bit is set regardless)."""
    try:
        mode = os.stat(source_path).st_mode
        os.chmod(dest_path, stat.S_IMODE(mode))
    except OSError as e:
        logger.debug(f"Could not copy permissions from {source_path} to {dest_path}: {e}")


def _swap_posix(current_path: str, new_binary_path: str) -> None:
    """POSIX alone would allow a single atomic os.replace() straight
    onto current_path (a single rename(2) syscall - either fully
    succeeds or leaves current_path completely untouched; replacing the
    file underneath an already-running process, if this IS the running
    binary, is well-defined on POSIX, unlike Windows). This deliberately
    does NOT do that anymore: it backs up current_path to
    current_path+'.old' FIRST, same sequence _swap_windows already uses
    below and for the same reason - a post-swap readback failure (see
    perform_self_update) needs something on disk to restore from, and
    once the original bytes were overwritten there was no way back.
    Matches what the web UI's own hand-off script
    (utils/self_update_handoff.py's _posix_script_content) already does
    independently, for the identical reason."""
    old_path = _old_sidecar_path(current_path)
    try:
        if os.path.isfile(old_path):
            os.remove(old_path)  # clear out any leftover before reusing the name
    except OSError:
        pass  # non-fatal - the rename below will just fail loudly if this is actually a problem

    try:
        # Belt-and-suspenders on top of _preserve_permissions: always
        # guarantee the exec bit regardless of what mode the freshly
        # downloaded temp file started with.
        mode = os.stat(new_binary_path).st_mode
        os.chmod(new_binary_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as e:
        raise SwapError(f"Could not set executable permissions on {new_binary_path}: {e}") from e

    try:
        os.rename(current_path, old_path)
    except OSError as e:
        raise SwapError(f"Could not rename running exe {current_path} -> {old_path}: {e}") from e

    try:
        os.replace(new_binary_path, current_path)
    except OSError as e:
        try:
            os.replace(old_path, current_path)
        except OSError as rollback_error:
            raise SwapError(
                f"Could not move verified binary into place ({e}), AND rollback failed "
                f"({rollback_error}) - the original binary may still be recoverable at {old_path}"
            ) from e
        raise SwapError(f"Could not move verified binary into place ({e}) - rolled back to the original binary") from e


def _swap_windows(current_path: str, new_binary_path: str) -> None:
    """Windows won't let you overwrite a running .exe in place, but the
    OS loader opens a running image with FILE_SHARE_DELETE, which DOES
    permit renaming it - the same mechanism self-updating apps like VS
    Code/Chrome rely on. Sequence: rename current -> current+'.old',
    then move the verified new binary into current's place. If the
    second step fails after the first succeeded, the rename is undone
    (current_path restored to the ORIGINAL binary) before raising - a
    failed swap must never leave current_path missing. In the
    extremely unlikely case that the rollback ITSELF also fails,
    SwapError's message points at where the original binary can still
    be recovered from (`<current_path>.old`) - see cleanup_stale_old_binary
    for the normal (successful-swap) cleanup of that same sidecar path.

    Uses os.replace(), not os.rename(), for the current -> old_path
    step: the best-effort cleanup right above is exactly that - best
    effort - so old_path can still be there (e.g. still locked by a
    slow-to-exit previous process, per that cleanup's own comment)
    when this runs. os.rename() refuses to overwrite an existing
    destination on Windows (WinError 183) - it would turn a merely
    stale sidecar into a hard failure of every subsequent update
    attempt, forever, until a human manually deletes it. os.replace()
    is the cross-platform atomic form and overwrites unconditionally,
    same as this same rename already relies on further down for the
    rollback path."""
    old_path = _old_sidecar_path(current_path)
    try:
        if os.path.isfile(old_path):
            os.remove(old_path)  # clear out any leftover before reusing the name
    except OSError:
        pass  # non-fatal - the os.replace() below tolerates old_path still being there

    try:
        os.replace(current_path, old_path)
    except OSError as e:
        raise SwapError(f"Could not rename running exe {current_path} -> {old_path}: {e}") from e

    try:
        os.replace(new_binary_path, current_path)
    except OSError as e:
        try:
            os.replace(old_path, current_path)
        except OSError as rollback_error:
            raise SwapError(
                f"Could not move verified binary into place ({e}), AND rollback failed "
                f"({rollback_error}) - the original binary may still be recoverable at {old_path}"
            ) from e
        raise SwapError(f"Could not move verified binary into place ({e}) - rolled back to the original binary") from e


def swap_binary(current_path: str, new_binary_path: str) -> None:
    """Replace the executable at current_path with the already
    hash+signature verified file at new_binary_path (see
    verify_downloaded_asset - swap_binary() must NEVER be called on an
    unverified file). Dispatches to the POSIX or Windows mechanics
    above - see each for the platform-specific guarantees; both now
    leave the original binary behind at current_path+'.old' on success
    (see cleanup_stale_old_binary), specifically so a caller can restore
    it if a post-swap check (perform_self_update's readback) finds
    something wrong with what's now on disk. Raises SwapError on
    failure; callers never catch this to retry with something
    unverified, only to abort and keep running the current binary."""
    _preserve_permissions(current_path, new_binary_path)
    if os.name == "nt":
        _swap_windows(current_path, new_binary_path)
    else:
        _swap_posix(current_path, new_binary_path)


# PyInstaller onefile internals: on first launch, the bootloader
# extracts the bundled archive to a temp dir, sets _MEIPASS2 (pointing
# at it) in ITS OWN environment, then re-execs itself - the resulting
# CHILD process (the one that actually runs this Python code) inherits
# that _MEIPASS2, uses it as sys._MEIPASS, and skips re-extracting.
# That's fine for the process it was set for, but if THIS process
# spawns ANOTHER, INDEPENDENT curatarr.exe instance while blindly
# inheriting os.environ, the new instance's bootloader sees _MEIPASS2
# ALREADY set and skips ITS OWN extraction too - reusing a temp
# directory that may belong to a DIFFERENT build or may already be torn
# apart by another process's cleanup.
#
# This was investigated extensively (see the v2.8.29 PR history) as the
# suspected cause of a relaunched-process crash inside werkzeug's own
# package-metadata lookup - stripping _MEIPASS2 alone, and later also
# giving spawned processes their own fresh TEMP/TMP, did NOT fully
# resolve it, which is what motivated the bigger architectural fix: the
# web UI's "Update now" flow no longer launches the new binary from
# within any frozen process at all - see utils/self_update_handoff.py's
# module docstring for the full replacement design (a plain external
# script, fully decoupled from any PyInstaller onefile runtime, does
# the actual swap+relaunch after the frozen worker has exited).
#
# CONFIRMED (real end-to-end testing on windows-latest/macos-latest/
# ubuntu-latest CI, see this repo's v2.8.29 PR description) that
# _MEIPASS2 alone was never the whole story: PyInstaller 6's bootloader
# sets several MORE hand-off variables beyond _MEIPASS2 -
# _PYI_ARCHIVE_FILE, _PYI_PARENT_PROCESS_LEVEL, _PYI_APPLICATION_HOME_DIR
# (and, only if a splash screen is used, which curatarr's build never
# does, _PYI_SPLASH_IPC) - and this module never stripped any of them.
# Directly observed in a real worker process's own inherited
# environment: _PYI_ARCHIVE_FILE pointing at the OLD binary's path and
# _PYI_APPLICATION_HOME_DIR pointing at the OLD binary's OWN _MEI
# extraction directory. A binary that inherits these (the external
# hand-off script's launch of the NEW binary never stripped them
# either, since it only ever removed _MEIPASS2 too) sees what looks
# like "I am a re-exec of an already-extracted parent", skips its own
# fresh extraction, and ends up with PYTHONHOME pointing at a
# directory that either belongs to a completely different build or no
# longer exists - Python's own interpreter then fails to bootstrap
# (observed directly: "stdlib dir = ''" in the crashed process's own
# diagnostic dump) before any of this codebase's Python even runs.
# Every `_PYI_` and `_PYINSTALLER_` prefixed variable is stripped
# below, not just the specific ones observed, since PyInstaller can add
# more across versions and none of them have any legitimate reason to
# be inherited by an unrelated, independent process either way.
_PYINSTALLER_CHILD_ENV_VAR_PREFIXES = ("_PYI_", "_PYINSTALLER_")
_PYINSTALLER_CHILD_ENV_VARS_TO_STRIP = ("_MEIPASS2",)


def sanitize_frozen_relaunch_env(env: Mapping[str, str]) -> Dict[str, str]:
    """Returns a copy of `env` with PyInstaller onefile's internal
    bootloader hand-off variables removed - see the module-level
    comment above. Safe to call on a non-frozen/non-Windows env too
    (no-op if the vars aren't present)."""
    return {
        k: v
        for k, v in env.items()
        if k not in _PYINSTALLER_CHILD_ENV_VARS_TO_STRIP and not k.startswith(_PYINSTALLER_CHILD_ENV_VAR_PREFIXES)
    }


# =============================================================================
# Orchestration
# =============================================================================


class VerifiedUpdate(NamedTuple):
    """A downloaded, signature+hash-verified (but not yet applied)
    update, as returned by download_and_verify_update(). `asset_path`
    is a real file sitting in the SAME directory as the running
    executable (see that function's docstring for why) - the caller
    owns it from this point on: either swap it in directly
    (perform_self_update, the CLI path) or hand it off to the external
    swap+relaunch script (the web "Update now" path - see
    utils/self_update_handoff.py). Either way, if the caller doesn't
    consume `asset_path` (move/rename it away), IT is responsible for
    removing it - this function never leaves it behind on its own
    success path, only hands over ownership.

    `asset_sha256` is the digest already verified against the signed
    sums file (verify_downloaded_asset's return value) - carried here so
    a caller can cheaply re-hash `asset_path` against this EXACT
    already-trusted value again immediately before actually using it
    (closing the TOCTOU window between verification and use - see
    perform_self_update and utils/self_update_handoff.py), without
    re-deriving or re-trusting anything new.
    """

    version: str
    asset_path: str
    asset_name: str
    asset_sha256: str


def download_and_verify_update(force_refresh: bool = True) -> VerifiedUpdate:
    """Thin wrapper around _download_and_verify_update_impl() that records
    curatarr_self_update_attempts_total (see utils/metrics.py) -
    'success' if a verified update was produced, 'failure' on any of
    this module's *Error subclasses. This is the single choke point both
    self-update entry points call through - the CLI's `--self-update`
    (perform_self_update(), immediately below, calls this first) and the
    web UI's "Update now" for a frozen binary (web/update_apply.py's
    _run_frozen_verify_and_handoff calls this directly) - so instrumenting
    here covers both without either needing its own metrics call.

    Kept separate from _download_and_verify_update_impl() itself so that
    function's own body/control flow needed no re-indentation to add
    this - see recommenders/external.py's main()/_main_impl() split for
    the identical reasoning.
    """
    outcome = "failure"
    try:
        verified = _download_and_verify_update_impl(force_refresh=force_refresh)
        outcome = "success"
        return verified
    finally:
        record_self_update_attempt(outcome)


def _download_and_verify_update_impl(force_refresh: bool = True) -> VerifiedUpdate:
    """
    Steps 1-4 of the self-update chain (module docstring): determine the
    target version, pick this platform's asset, download it plus
    SHA256SUMS.txt/.sig, and verify the pinned-key signature and hash.
    Deliberately stops BEFORE touching the currently-running executable
    at all - what happens next (an in-process swap for the CLI, or a
    hand-off to an external script for the web-triggered relaunch flow)
    is entirely up to the caller.

    Raises one of this module's *Error subclasses on ANY failure - a
    caller must treat every one identically: nothing was touched on
    disk except a now-cleaned-up temp download, keep running the
    current binary.

    The verified asset is downloaded directly into the SAME directory as
    the running executable (never the system temp dir) specifically so
    a subsequent same-volume os.replace()/os.rename() swap is always
    possible - Windows' MoveFileEx (which os.replace/os.rename use
    under the hood) refuses a cross-volume replace outright, and %TEMP%
    is not guaranteed to be on the same drive as wherever the user put
    curatarr.exe (see docs/BINARIES.md: "put it in a folder of its
    own"). SHA256SUMS.txt/.sig never need to survive past this
    function returning, so they stay in an ordinary auto-cleaned temp
    directory.
    """
    if not getattr(sys, "frozen", False):
        raise NotFrozenError(
            "Self-update only applies to a packaged binary (sys.frozen) - source "
            "installs use run.sh/run.ps1's own signed-tag auto-updater instead."
        )

    target_version = determine_update_target(force_refresh=force_refresh)
    asset_name = select_asset_name()

    current_path = current_binary_path()
    asset_dir = os.path.dirname(current_path)

    with tempfile.TemporaryDirectory(prefix="curatarr-self-update-") as tmp_dir:
        sums_path = os.path.join(tmp_dir, SUMS_FILENAME)
        sig_path = os.path.join(tmp_dir, SUMS_SIG_FILENAME)
        _download_to_file(release_asset_url(target_version, SUMS_FILENAME), sums_path)
        _download_to_file(release_asset_url(target_version, SUMS_SIG_FILENAME), sig_path)

        try:
            asset_fd, asset_path = tempfile.mkstemp(dir=asset_dir, prefix=".curatarr-update-", suffix=".tmp")
            os.close(asset_fd)
        except OSError as e:
            raise SwapError(
                f"Cannot write a temp file in {asset_dir} (the folder containing the running binary): {e}"
            ) from e

        try:
            _download_to_file(release_asset_url(target_version, asset_name), asset_path)
            asset_sha256 = verify_downloaded_asset(
                asset_path, sums_path, sig_path, asset_name, target_version=target_version
            )
        except Exception:
            if os.path.isfile(asset_path):
                try:
                    os.remove(asset_path)
                except OSError as cleanup_exc:
                    logger.debug(f"Could not remove partial download {asset_path}: {cleanup_exc}")
            raise

    return VerifiedUpdate(
        version=target_version,
        asset_path=asset_path,
        asset_name=asset_name,
        asset_sha256=asset_sha256,
    )


# How long the post-swap readback (_verify_swapped_binary_or_restore)
# waits for the newly-swapped binary to answer `--version` - generous
# for a PyInstaller onefile's own extraction time (the dominant cost of
# any frozen launch, --version included - see curatarr_app.py's
# dispatcher) without letting a genuinely hung/broken binary stall the
# CLI indefinitely.
VERSION_READBACK_TIMEOUT_SECONDS = 15


def _verify_swapped_binary_or_restore(current_path: str, expected_version: str) -> None:
    """After swap_binary() has replaced current_path, actually RUN what's
    now there (`<exe> --version`) and confirm it reports
    expected_version - mirroring, for this in-process CLI path, the same
    intent as the web UI's own post-swap /healthz confirmation (see
    utils/self_update_handoff.py) - trust the running result, not just
    that the swap syscalls themselves didn't raise.

    On ANY failure (the new binary won't even launch, times out, exits
    non-zero, or prints something other than exactly expected_version),
    restores the `.old` backup swap_binary() left behind (see
    _swap_posix/_swap_windows) back into current_path's place before
    raising VersionReadbackError - a bad swap must never leave the user
    stranded on a binary that doesn't work, or on the wrong version.
    Always raises on failure; there is no return value on success
    (silent return is the only non-raising outcome).
    """
    old_path = _old_sidecar_path(current_path)

    def _restore_and_raise(reason: str) -> None:
        if not os.path.isfile(old_path):
            raise VersionReadbackError(f"{reason} - no backup binary was available to restore.")
        try:
            if os.path.isfile(current_path):
                os.remove(current_path)
            os.replace(old_path, current_path)
        except OSError as restore_error:
            raise VersionReadbackError(
                f"{reason} - AND restoring the previous binary from {old_path} also failed "
                f"({restore_error}). The previous binary may still be recoverable from {old_path}."
            ) from restore_error
        raise VersionReadbackError(f"{reason} - restored the previous binary.")

    try:
        # sanitize_frozen_relaunch_env: this process IS itself a frozen
        # PyInstaller instance (--self-update only ever runs from one -
        # see NotFrozenError above), so spawning ANOTHER instance of the
        # same exe without stripping its PyInstaller bootloader hand-off
        # env vars first is exactly the inherited-_MEIPASS2-class bug
        # this same module already had to fix for the web relaunch path
        # (see the big comment above sanitize_frozen_relaunch_env).
        result = subprocess.run(
            [current_path, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_READBACK_TIMEOUT_SECONDS,
            env=sanitize_frozen_relaunch_env(dict(os.environ)),
        )
    except (OSError, subprocess.SubprocessError) as e:
        _restore_and_raise(f"Could not run the newly-swapped binary to confirm its version: {e}")
        return

    reported = (result.stdout or "").strip()
    if result.returncode != 0 or reported != expected_version:
        _restore_and_raise(
            f"Newly-swapped binary did not confirm v{expected_version} "
            f"(exit code {result.returncode}, printed {reported!r})"
        )


def perform_self_update(force_refresh: bool = True) -> str:
    """
    The CLI's `--self-update` path (curatarr_app.py's
    _run_self_update_cli): download+verify (see
    download_and_verify_update), then swap the verified binary into
    place IN-PROCESS, confirm the swap by actually running the result
    (see _verify_swapped_binary_or_restore), and return the applied
    version string. Safe to do the swap in-process here specifically
    because the CLI never relaunches anything afterward as part of its
    own process tree (the process just exits - see
    _run_self_update_cli's docstring) - there is no LONG-LIVED new
    curatarr.exe instance spawned from within this one to race a
    PyInstaller onefile extraction directory against, which is what made
    the WEB UI's relaunch flow unsafe to do this way (see
    utils/self_update_handoff.py's module docstring). The readback
    subprocess below is short-lived and exits on its own well before
    this process does, so it doesn't reintroduce that hazard.

    Raises one of this module's *Error subclasses on ANY failure. Before
    swap_binary() runs, that means "do NOT swap anything, keep running
    the current binary" exactly as before - nothing on disk changes at
    all. After swap_binary() runs, a failure instead means "the previous
    binary was restored" (see _verify_swapped_binary_or_restore) - there
    is still no state where a broken/unverified/wrong-version binary is
    left in place for the next launch to pick up.

    Defense-in-depth downgrade gate: determine_update_target() (called
    inside download_and_verify_update, via update_available) already
    requires a strictly-newer version before a target is even chosen -
    but that decision happens several steps, and one whole parsed HTTPS
    API response, earlier than the swap actually below. This function
    re-checks verified.version against the currently-installed
    __version__ again immediately before swap_binary() - the same
    strictly-newer-only rule the web "Update now" path's begin_update()
    already enforces - so a future refactor of anything upstream can
    never accidentally let an equal-or-older version reach the swap,
    even one that's genuinely pinned-signature-verified: a real, older
    release existing on GitHub with a valid signature is not
    hypothetical, it's exactly what a spoofed "latest release" API
    tag_name could otherwise be used to redirect this whole chain into
    silently installing (see utils.update_check.parse_version's
    docstring for that exploit).
    """
    verified = download_and_verify_update(force_refresh=force_refresh)
    try:
        target_tuple = parse_version(verified.version)
        current_tuple = parse_version(__version__)
        if not target_tuple or not current_tuple or target_tuple <= current_tuple:
            raise NoUpdateAvailableError(
                f"Refusing to install v{verified.version}: not strictly newer than the running v{__version__}"
            )

        # TOCTOU close: re-hash the already-verified asset one more
        # time, immediately before it's actually used, against the
        # EXACT digest download_and_verify_update already checked
        # against the signed sums file (verified.asset_sha256) - closes
        # the window between that check and this use, however small, on
        # a local filesystem this process doesn't fully control (the
        # temp file lives next to the running binary - see that
        # function's docstring for why - not an unpredictable system
        # temp dir, but still not provably untouchable by anything else
        # running as this same user).
        current_hash = sha256_file(verified.asset_path)
        if current_hash != verified.asset_sha256:
            raise HashMismatchError(
                f"{verified.asset_name} SHA256 changed between verification and swap "
                f"(expected {verified.asset_sha256}, now {current_hash}) - refusing to install"
            )

        current_path = current_binary_path()
        swap_binary(current_path, verified.asset_path)
        _verify_swapped_binary_or_restore(current_path, verified.version)
    finally:
        # swap_binary() (on success) moves asset_path away via
        # os.replace(); if it raised before doing so, it's still
        # sitting here - clean up defensively either way so a failed
        # update never litters the install directory with a stray
        # .tmp file.
        if os.path.isfile(verified.asset_path):
            try:
                os.remove(verified.asset_path)
            except OSError as cleanup_exc:
                logger.debug(f"Could not remove leftover asset {verified.asset_path}: {cleanup_exc}")
    return verified.version
