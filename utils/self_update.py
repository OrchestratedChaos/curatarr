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
5. Only after BOTH of the above succeed does swap_binary() touch the
   filesystem at all. See that function's docstring for the atomic,
   per-OS swap mechanics and their own fail-safe/rollback guarantees.

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
from typing import Dict, NamedTuple, Optional

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .update_check import GITHUB_RELEASES_PAGE, update_available

logger = logging.getLogger('curatarr')

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


# =============================================================================
# Platform -> release asset selection (section C.2)
# =============================================================================

# Filenames exactly as published by .github/workflows/release.yml - see
# docs/BINARIES.md's asset table.
ASSET_WINDOWS_X86_64 = 'curatarr-windows-x86_64.exe'
ASSET_LINUX_X86_64 = 'curatarr-linux-x86_64'
ASSET_LINUX_ARM64 = 'curatarr-linux-arm64'
ASSET_MACOS_UNIVERSAL = 'curatarr-macos-universal'

_X86_64_MACHINE_NAMES = ('x86_64', 'amd64')
_ARM64_MACHINE_NAMES = ('aarch64', 'arm64')


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
    machine_l = (machine or '').lower()

    if sys_platform == 'win32':
        if machine_l in _X86_64_MACHINE_NAMES:
            return ASSET_WINDOWS_X86_64
        raise UnsupportedPlatformError(
            f"No self-update binary published for Windows/{machine} - only x86_64 is "
            f"built. Download manually: {GITHUB_RELEASES_PAGE}"
        )

    if sys_platform == 'darwin':
        # One universal2 binary covers both Intel and Apple Silicon - no
        # architecture branch needed, unlike Windows/Linux.
        return ASSET_MACOS_UNIVERSAL

    if sys_platform.startswith('linux'):
        if machine_l in _X86_64_MACHINE_NAMES:
            return ASSET_LINUX_X86_64
        if machine_l in _ARM64_MACHINE_NAMES:
            return ASSET_LINUX_ARM64
        raise UnsupportedPlatformError(
            f"No self-update binary published for Linux/{machine} - only x86_64 and "
            f"arm64 are built. Download manually: {GITHUB_RELEASES_PAGE}"
        )

    raise UnsupportedPlatformError(
        f"No self-update binary published for platform {sys_platform!r}. "
        f"Download manually: {GITHUB_RELEASES_PAGE}"
    )


# =============================================================================
# SSH SIGNATURE (SSHSIG) verification - pure Python, see module docstring's
# "Why pure Python" section. Implements the wire format from OpenSSH's
# PROTOCOL.sshsig: an armored blob wrapping (public key, namespace,
# reserved, hash algorithm, signature), where the signature itself
# covers a small wrapper structure containing a hash of the actual
# signed file - never the raw file bytes directly.
# =============================================================================

SIGNATURE_NAMESPACE = 'file'  # matches `ssh-keygen -Y sign -n file` (scripts/sign-release-checksums.sh)

_SSHSIG_MAGIC = b'SSHSIG'
_SSHSIG_VERSION = 1
_SSHSIG_ARMOR_BEGIN = '-----BEGIN SSH SIGNATURE-----'
_SSHSIG_ARMOR_END = '-----END SSH SIGNATURE-----'
_SUPPORTED_HASH_ALGORITHMS = ('sha256', 'sha512')
_SUPPORTED_SIGNATURE_ALGORITHM = b'ssh-ed25519'


class _ParsedSshsig(NamedTuple):
    public_key_blob: bytes
    namespace: str
    hash_algorithm: str
    signature_algorithm: bytes
    signature_raw: bytes


def _read_uint32(buf: bytes, offset: int) -> tuple:
    if offset + 4 > len(buf):
        raise SignatureVerificationError("Truncated SSH signature (expected a length prefix)")
    return struct.unpack('>I', buf[offset:offset + 4])[0], offset + 4


def _read_string(buf: bytes, offset: int) -> tuple:
    length, offset = _read_uint32(buf, offset)
    if length < 0 or offset + length > len(buf):
        raise SignatureVerificationError("Truncated SSH signature (expected a length-prefixed field)")
    return buf[offset:offset + length], offset + length


def _pack_string(data: bytes) -> bytes:
    return struct.pack('>I', len(data)) + data


def _decode_armor(armored_signature: str) -> bytes:
    """Strip the '-----BEGIN/END SSH SIGNATURE-----' PEM-style armor and
    base64-decode the body. Raises SignatureVerificationError (never a
    bare exception) on anything malformed - a caller catching only this
    module's own exception types must never see a raw ValueError/
    binascii.Error escape from here."""
    text = (armored_signature or '').strip()
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
        return base64.b64decode(''.join(body_lines))
    except (binascii.Error, ValueError) as e:
        raise SignatureVerificationError(f"Malformed base64 in SSH signature: {e}") from e


def _parse_sshsig_blob(blob: bytes) -> _ParsedSshsig:
    offset = 0
    magic = blob[offset:offset + 6]
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
        namespace=namespace_raw.decode('utf-8', 'replace'),
        hash_algorithm=hash_algorithm_raw.decode('ascii', 'replace'),
        signature_algorithm=sig_algorithm,
        signature_raw=signature_raw,
    )


def _encode_ed25519_public_key_blob(public_key: Ed25519PublicKey) -> bytes:
    """Rebuild the SSH wire-format public key blob (string "ssh-ed25519"
    + string raw-key) FROM the cryptography key object - i.e. from
    something this module itself decoded from PINNED_SIGNING_PUBLIC_KEY_B64,
    never from the signature file being verified."""
    raw = public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return _pack_string(b'ssh-ed25519') + _pack_string(raw)


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
        + _pack_string(namespace.encode('utf-8'))
        + _pack_string(b'')  # reserved
        + _pack_string(parsed.hash_algorithm.encode('ascii'))
        + _pack_string(digest)
    )

    try:
        public_key.verify(parsed.signature_raw, to_be_signed)
    except InvalidSignature as e:
        raise SignatureVerificationError(
            "SSH signature does not verify against the pinned release-signing key"
        ) from e


# =============================================================================
# Pinned key - the ONE trust anchor everything above ultimately resolves
# to. Same key (same fingerprint) as .github/allowed_signers, which
# verifies signed release TAGS; this is the same key's public half,
# hardcoded here (not read from that file, which isn't bundled into the
# binary at all) to verify signed CHECKSUM FILES instead. The private
# half never leaves the maintainer's Windows machine - see
# scripts/sign-release-checksums.sh and RELEASING.md.
# =============================================================================

PINNED_SIGNING_PUBLIC_KEY_B64 = (
    'AAAAC3NzaC1lZDI1NTE5AAAAIINUnyyTuXRhMU7XEpgBwm3dKrkv0D3U7mz+21piPb8q'
)
PINNED_SIGNING_KEY_FINGERPRINT = 'SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU'


def compute_key_fingerprint(public_key_blob: bytes) -> str:
    """SHA256 SSH key fingerprint, matching `ssh-keygen -lf`'s own
    format exactly (SHA256 of the wire-format public key blob,
    base64-encoded, unpadded, "SHA256:" prefixed) - verified against a
    real `ssh-keygen -lf` output while this module was written."""
    digest = hashlib.sha256(public_key_blob).digest()
    return 'SHA256:' + base64.b64encode(digest).decode('ascii').rstrip('=')


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

SUMS_FILENAME = 'SHA256SUMS.txt'
SUMS_SIG_FILENAME = 'SHA256SUMS.txt.sig'

# Matches both GNU coreutils' `sha256sum` output ("<hex>  <name>" or
# "<hex> *<name>" for binary mode) and `shasum -a 256`'s identical
# format - see scripts/release.sh / .github/workflows/release.yml, both
# of which use one or the other depending on OS.
_SUMS_LINE_RE = re.compile(r'^([0-9a-fA-F]{64})\s+[* ]?(.+)$')


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
        if not line or line.startswith('#'):
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
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_downloaded_asset(asset_path: str, sums_path: str, sig_path: str, asset_name: str) -> None:
    """
    The full authenticity chain (module docstring steps 3-4) applied to
    one already-downloaded asset. Raises SignatureVerificationError or
    HashMismatchError on any failure - never returns anything, silent
    success is the only non-raising outcome.
    """
    try:
        with open(sums_path, 'r', encoding='utf-8') as f:
            sums_text = f.read()
        with open(sig_path, 'r', encoding='utf-8') as f:
            sig_text = f.read()
    except OSError as e:
        # Missing/unreadable either file (e.g. the .sig never got
        # downloaded/published at all) is exactly as fail-closed as a
        # signature that doesn't verify - never fall through and treat
        # "we don't have a signature" as "unsigned is fine".
        raise SignatureVerificationError(f"Could not read checksum/signature files: {e}") from e

    # Authenticity FIRST: SHA256SUMS.txt was just downloaded over
    # anonymous HTTPS and is no more trustworthy than the asset itself
    # until ITS signature verifies - only then does its content become a
    # trusted source of truth for what the asset's hash should be. An
    # attacker able to substitute the asset could trivially also
    # substitute a matching-but-unsigned (or unsigned-differently) sums
    # file, so checking the hash before the signature would verify
    # nothing.
    verify_pinned_signature(sums_text.encode('utf-8'), sig_text)

    sums = parse_sha256sums(sums_text)
    expected = sums.get(asset_name)
    if not expected:
        raise HashMismatchError(f"{asset_name!r} is not listed in the verified {SUMS_FILENAME}")

    actual = sha256_file(asset_path)
    if actual.lower() != expected.lower():
        raise HashMismatchError(f"{asset_name} SHA256 mismatch: expected {expected}, got {actual}")


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
    os.environ.get('CURATARR_RELEASES_DOWNLOAD_BASE_OVERRIDE')
    or 'https://github.com/OrchestratedChaos/curatarr/releases/download'
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
    latest, current, is_newer = update_available(update_mode='notify', force_refresh=force_refresh)
    if not is_newer:
        raise NoUpdateAvailableError(
            f"No newer verified release available (current v{current}, latest known v{latest or 'unknown'})"
        )
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
            with open(dest_path, 'wb') as f:
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
    return path + '.old'


def cleanup_stale_old_binary(current_path: Optional[str] = None) -> None:
    """
    Best-effort removal of a leftover `<exe>.old` from a previous
    Windows swap (see _swap_windows below) - call unconditionally at
    every frozen startup (curatarr_app.py), not just after an update.

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
    try:
        # Belt-and-suspenders on top of _preserve_permissions: always
        # guarantee the exec bit regardless of what mode the freshly
        # downloaded temp file started with.
        mode = os.stat(new_binary_path).st_mode
        os.chmod(new_binary_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        # A single rename(2) syscall - atomic by POSIX definition: either
        # fully succeeds or leaves current_path completely untouched.
        # The already-running process (if this IS the running binary -
        # true for the detached self-update worker, see
        # web/update_apply.py) keeps executing its old, already-mapped
        # inode either way; replacing the file underneath a running
        # process is well-defined on POSIX, unlike Windows.
        os.replace(new_binary_path, current_path)
    except OSError as e:
        raise SwapError(f"Could not swap binary at {current_path}: {e}") from e


def _swap_windows(current_path: str, new_binary_path: str) -> None:
    """Windows won't let you overwrite a running .exe in place, but the
    OS loader opens a running image with FILE_SHARE_DELETE, which DOES
    permit renaming it - the same mechanism self-updating apps like VS
    Code/Chrome rely on. Sequence: rename current -> current+'.old',
    then move the verified new binary into current's place. If the
    second step fails after the first succeeded, the rename is undone
    (current_path restored to the ORIGINAL binary) before raising - a
    failed swap must never leave current_path missing. See
    relaunch_binary()'s _binary_to_relaunch for the very-last-resort
    fallback if even that rollback fails."""
    old_path = _old_sidecar_path(current_path)
    try:
        if os.path.isfile(old_path):
            os.remove(old_path)  # clear out any leftover before reusing the name
    except OSError:
        pass  # non-fatal - the rename below will just fail loudly if this is actually a problem

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
        raise SwapError(
            f"Could not move verified binary into place ({e}) - rolled back to the original binary"
        ) from e


def swap_binary(current_path: str, new_binary_path: str) -> None:
    """Atomically replace the executable at current_path with the
    already hash+signature verified file at new_binary_path (see
    verify_downloaded_asset - swap_binary() must NEVER be called on an
    unverified file). Dispatches to the POSIX or Windows mechanics
    above - see each for the platform-specific guarantees. Raises
    SwapError on failure; callers (perform_self_update) never catch
    this to retry with something unverified, only to abort and keep
    running the current binary."""
    _preserve_permissions(current_path, new_binary_path)
    if os.name == 'nt':
        _swap_windows(current_path, new_binary_path)
    else:
        _swap_posix(current_path, new_binary_path)


def _binary_to_relaunch(current_path: str) -> str:
    """Whatever's actually a working file right now: current_path after
    a successful swap (or when no swap was even attempted, e.g.
    verification failed first), or its `.old` sidecar in the
    last-resort case where a Windows swap's own rollback also failed
    (see _swap_windows) - the true "never leave the user without a
    working app" fallback."""
    if os.path.isfile(current_path):
        return current_path
    old_path = _old_sidecar_path(current_path)
    if os.path.isfile(old_path):
        return old_path
    raise SwapError(f"No working binary found at {current_path} or {old_path} - cannot relaunch")


# PyInstaller onefile internals: on first launch, the bootloader
# extracts the bundled archive to a temp dir, sets _MEIPASS2 (pointing
# at it) in ITS OWN environment, then re-execs itself - the resulting
# CHILD process (the one that actually runs this Python code) inherits
# that _MEIPASS2, uses it as sys._MEIPASS, and skips re-extracting.
# That's fine for the process it was set for, but if THIS process (the
# self-update worker or CLI, both already running with _MEIPASS2 in
# os.environ) spawns ANOTHER, INDEPENDENT curatarr.exe instance while
# blindly inheriting os.environ, the new instance's bootloader sees
# _MEIPASS2 ALREADY set and skips ITS OWN extraction too - reusing a
# temp directory that may belong to a DIFFERENT build (post-swap) or
# may already be gone (cleaned up when the just-killed old server's own
# parent bootloader process exited) - confirmed via a real end-to-end
# test: without stripping this, the relaunched process crashed inside
# werkzeug's own package-metadata lookup because it was running against
# the wrong/missing extraction directory. Every spawn of a fresh,
# independent curatarr.exe instance (this module's relaunch_binary, and
# web/update_apply.py's _spawn_worker) must sanitize this out so the
# new process always does its own clean extraction.
_PYINSTALLER_CHILD_ENV_VARS_TO_STRIP = ('_MEIPASS2',)


def sanitize_frozen_relaunch_env(env: dict) -> dict:
    """Returns a copy of `env` with PyInstaller onefile's internal
    bootloader hand-off variables removed - see the module-level
    comment above. Safe to call on a non-frozen/non-Windows env too
    (no-op if the vars aren't present)."""
    return {k: v for k, v in env.items() if k not in _PYINSTALLER_CHILD_ENV_VARS_TO_STRIP}


def fresh_extraction_temp_dir() -> str:
    """A guaranteed-unique directory under the system temp root, meant
    to be pointed at by TEMP/TMP for any freshly-spawned, independent
    curatarr.exe instance involved in a self-update - the worker
    (web/update_apply.py's _spawn_worker) AND the final relaunch (this
    module's relaunch_binary) both use this, and for the SAME
    underlying reason.

    Confirmed via a real end-to-end test on Windows that stripping
    _MEIPASS2 alone (sanitize_frozen_relaunch_env above) is NOT
    sufficient: PyInstaller onefile's bootloader can still resolve
    MULTIPLE independent processes to the SAME extraction-directory
    identity if it derives that identity from something tied to the
    executable's PATH rather than random per-launch entropy. The
    specific failure chain observed: the worker (spawned from the OLD
    server, at that point still the same on-disk exe/path) shared its
    _MEIPASS extraction with the old server. When the old server was
    then force-killed (see _shut_down_old_server), its own parent
    bootloader process's exit-time cleanup tore apart that SHARED
    extraction directory - files disappearing out from under the
    worker WHILE IT WAS STILL RUNNING AND USING THEM. The result was a
    hard PyInstaller bootloader error dialog ("Failed to execute
    script" / `pyi_rth_multiprocessing` failing with `[Errno 2] No such
    file or directory: ...\\_MEI*\\base_library.zip...`) - a modal
    MessageBox the bootloader itself shows directly, NOT a standard
    Windows Error Reporting crash that
    curatarr_app.py's_suppress_windows_crash_dialogs() (SetErrorMode)
    can suppress, since it isn't routed through the OS's WER path at
    all.

    Giving the worker its OWN extraction directory from the moment it's
    spawned - decoupled from the old server's before that old server is
    ever killed - closes this off entirely: nothing the old server's
    death does to ITS OWN extraction directory can ever affect the
    worker's. The final relaunch gets the same treatment for the
    earlier-observed, related reason (a relaunch immediately after a
    binary swap could otherwise still inherit a stale/wrong extraction
    tied to the pre-swap build). Both sidestep whatever the exact reuse
    mechanism is, rather than relying on understanding (or trusting the
    stability of) PyInstaller's internals across versions.

    PyInstaller's bootloader cleans up its own extracted subdirectory
    within this on normal process exit; this function's directory
    itself is deliberately left for the OS's own temp-cleanup policies
    rather than actively removed here (there is no reliable point in a
    detached/relaunched process's lifecycle to clean it up from the
    OUTSIDE).
    """
    # tempfile.mkdtemp() itself already guarantees a fresh, collision-free
    # name (unlike a hand-rolled pid+timestamp scheme, which two calls
    # within the same millisecond could collide on).
    return tempfile.mkdtemp(prefix=f'curatarr-relaunch-{os.getpid()}-')


def relaunch_binary(port: Optional[int] = None) -> None:
    """
    Spawn a fresh, DETACHED process from whatever's the working binary
    right now (see _binary_to_relaunch) and return immediately - mirrors
    web/update_apply.py's _relaunch_ui detachment flags exactly (see
    that module's docstring for why start_new_session / DETACHED_PROCESS
    matter: without them, this process exiting moments later would also
    tear down a still-attached child).

    `port` is only passed by the web "Update now" flow
    (web/update_apply.py's frozen worker branch, via its own
    _relaunch_ui) so the relaunched process re-binds the UI on the SAME
    port the old one used (CURATARR_UI_PORT) and skips auto-opening a
    new browser tab (CURATARR_SKIP_BROWSER_OPEN - see web/app.py's
    main()); the web UI always binds 127.0.0.1 regardless (see that
    same main()), so there's no separate host to pass through. A bare
    CLI `--self-update` run passes neither; the relaunched process just
    starts normally.
    """
    current_path = current_binary_path()
    exe_path = _binary_to_relaunch(current_path)

    env = sanitize_frozen_relaunch_env(os.environ)
    # See fresh_extraction_temp_dir's docstring - without this, a
    # relaunch right after a binary swap can inherit a PyInstaller
    # onefile extraction directory belonging to the PRE-swap build.
    fresh_temp = fresh_extraction_temp_dir()
    env['TEMP'] = fresh_temp
    env['TMP'] = fresh_temp
    if port is not None:
        env['CURATARR_UI_PORT'] = str(port)
        env['CURATARR_SKIP_BROWSER_OPEN'] = '1'

    popen_kwargs = dict(
        env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    if os.name == 'nt':
        popen_kwargs['creationflags'] = (
            getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
            | getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
        )
    else:
        popen_kwargs['start_new_session'] = True

    subprocess.Popen([exe_path], **popen_kwargs)


# =============================================================================
# Orchestration - the one function everything above exists to support.
# =============================================================================

def perform_self_update(force_refresh: bool = True) -> str:
    """
    The full download -> verify -> swap sequence (module docstring).
    Returns the applied version string (no leading 'v') on success.

    Raises one of this module's *Error subclasses on ANY failure -
    callers must treat every one identically: do NOT swap anything,
    keep/relaunch the CURRENT binary (see relaunch_binary, which works
    the same regardless of whether an update was actually applied).
    Either every verification step below succeeds and swap_binary()
    runs, or nothing on disk changes at all - there is no partial-update
    state.

    The verified asset is downloaded directly into the SAME directory as
    the running executable (never the system temp dir) specifically so
    the final swap is always a same-volume os.replace()/os.rename() -
    Windows' MoveFileEx (which os.replace/os.rename use under the hood)
    refuses a cross-volume replace outright, and %TEMP% is not
    guaranteed to be on the same drive as wherever the user put
    curatarr.exe (see docs/BINARIES.md: "put it in a folder of its
    own").
    """
    if not getattr(sys, 'frozen', False):
        raise NotFrozenError(
            "Self-update only applies to a packaged binary (sys.frozen) - source "
            "installs use run.sh/run.ps1's own signed-tag auto-updater instead."
        )

    target_version = determine_update_target(force_refresh=force_refresh)
    asset_name = select_asset_name()

    current_path = current_binary_path()
    asset_dir = os.path.dirname(current_path)

    with tempfile.TemporaryDirectory(prefix='curatarr-self-update-') as tmp_dir:
        sums_path = os.path.join(tmp_dir, SUMS_FILENAME)
        sig_path = os.path.join(tmp_dir, SUMS_SIG_FILENAME)
        _download_to_file(release_asset_url(target_version, SUMS_FILENAME), sums_path)
        _download_to_file(release_asset_url(target_version, SUMS_SIG_FILENAME), sig_path)

        try:
            asset_fd, asset_path = tempfile.mkstemp(
                dir=asset_dir, prefix='.curatarr-update-', suffix='.tmp'
            )
            os.close(asset_fd)
        except OSError as e:
            raise SwapError(
                f"Cannot write a temp file in {asset_dir} (the folder containing the "
                f"running binary): {e}"
            ) from e

        try:
            _download_to_file(release_asset_url(target_version, asset_name), asset_path)
            verify_downloaded_asset(asset_path, sums_path, sig_path, asset_name)
            swap_binary(current_path, asset_path)
        finally:
            # swap_binary() (on success) moves asset_path away via
            # os.replace(); on a verification failure raised BEFORE
            # swap_binary() ever ran, it's still sitting here - clean up
            # defensively either way so a failed update never litters
            # the install directory with a stray .tmp file.
            if os.path.isfile(asset_path):
                try:
                    os.remove(asset_path)
                except OSError:
                    pass

    return target_version
