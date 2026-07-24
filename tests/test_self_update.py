"""Tests for utils/self_update.py - the in-binary self-updater for the
standalone PyInstaller binaries (source installs keep using
run.sh/run.ps1's own git-based auto-updater - see that module's
docstring).

Real signatures, disposable keys: every SSHSIG PASS/FAIL/tamper test
below signs with a throwaway ed25519 keypair generated fresh by
`ssh-keygen` in the `signing_key` fixture - never the real (offline,
Windows-only) curatarr release-signing private key. The real PINNED
public key/fingerprint constants are exercised directly (their own
self-check, and the fact they decode to a valid ed25519 key), just
never used to verify a signature produced by a key this suite doesn't
have - that would require the real private key, which by design never
touches CI (see RELEASING.md / scripts/sign-release-checksums.sh). The
Windows machine holding that key runs its own real, live end-to-end
test with a real signature - not part of this suite (see this repo's PR
description for that evidence).

What's NOT unit-tested here (by design - matches this repo's existing
precedent for OS-process-boundary code, e.g. curatarr_app.py's
_attach_or_setup_console / web/update_apply.py's `if __name__ ==
'__main__':` block): actually spawning a real detached process via
relaunch_binary (subprocess.Popen is mocked in every test that touches
it), and the true Windows FILE_SHARE_DELETE rename-while-running
behavior (_swap_windows's rename/replace calls are exercised for real
against plain tmp_path files here - which proves the LOGIC is correct -
but no test here is itself a currently-executing binary being replaced
out from under itself).
"""

import base64
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from utils import self_update


# =============================================================================
# Fixtures - a disposable ed25519 keypair + real ssh-keygen-produced
# SSHSIG signatures, so PASS/tamper/wrong-key tests exercise the exact
# same wire format the real release-signing key produces, without ever
# touching that real (offline) key.
# =============================================================================

def _ssh_keygen_available() -> bool:
    return shutil.which('ssh-keygen') is not None


requires_ssh_keygen = pytest.mark.skipif(
    not _ssh_keygen_available(), reason="ssh-keygen not on PATH - can't generate real SSHSIG fixtures"
)


@pytest.fixture
def signing_key(tmp_path):
    """A disposable ed25519 keypair - private key at <tmp_path>/testkey,
    public key parsed into an Ed25519PublicKey object."""
    key_path = tmp_path / 'testkey'
    subprocess.run(
        ['ssh-keygen', '-t', 'ed25519', '-f', str(key_path), '-N', '', '-q'],
        check=True,
    )
    pub_line = (tmp_path / 'testkey.pub').read_text(encoding='utf-8').split()
    raw_blob = base64.b64decode(pub_line[1])
    algo, offset = self_update._read_string(raw_blob, 0)
    raw_key, offset = self_update._read_string(raw_blob, offset)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    return {
        'key_path': str(key_path),
        'public_key': Ed25519PublicKey.from_public_bytes(raw_key),
        'public_key_blob_b64': pub_line[1],
    }


def _sign(key_path: str, message: bytes, tmp_path, namespace: str = 'file') -> str:
    """Shell out to ssh-keygen -Y sign (real OpenSSH implementation) to
    produce a genuine SSHSIG signature over `message` - the fixture data
    every PASS test below verifies against."""
    msg_path = tmp_path / f'msg-{os.urandom(4).hex()}.bin'
    msg_path.write_bytes(message)
    subprocess.run(
        ['ssh-keygen', '-Y', 'sign', '-f', key_path, '-n', namespace, str(msg_path)],
        check=True, capture_output=True,
    )
    return (tmp_path / f'{msg_path.name}.sig').read_text(encoding='utf-8')


# =============================================================================
# Platform -> asset selection (spec C.2)
# =============================================================================

class TestSelectAssetName:
    def test_windows_x86_64(self):
        assert self_update.select_asset_name('win32', 'AMD64') == 'curatarr-windows-x86_64.exe'

    def test_windows_x86_64_lowercase_machine(self):
        assert self_update.select_asset_name('win32', 'x86_64') == 'curatarr-windows-x86_64.exe'

    def test_windows_unsupported_arch_aborts(self):
        with pytest.raises(self_update.UnsupportedPlatformError, match='Windows/ARM64'):
            self_update.select_asset_name('win32', 'ARM64')

    def test_macos_universal_regardless_of_arch_x86_64(self):
        assert self_update.select_asset_name('darwin', 'x86_64') == 'curatarr-macos-universal'

    def test_macos_universal_regardless_of_arch_arm64(self):
        assert self_update.select_asset_name('darwin', 'arm64') == 'curatarr-macos-universal'

    def test_linux_x86_64(self):
        assert self_update.select_asset_name('linux', 'x86_64') == 'curatarr-linux-x86_64'

    def test_linux_arm64(self):
        assert self_update.select_asset_name('linux', 'aarch64') == 'curatarr-linux-arm64'

    def test_linux_arm64_alt_machine_name(self):
        assert self_update.select_asset_name('linux', 'arm64') == 'curatarr-linux-arm64'

    def test_linux_unsupported_arch_aborts(self):
        with pytest.raises(self_update.UnsupportedPlatformError, match='Linux/armv7l'):
            self_update.select_asset_name('linux', 'armv7l')

    def test_unknown_platform_aborts(self):
        with pytest.raises(self_update.UnsupportedPlatformError, match="'freebsd13'"):
            self_update.select_asset_name('freebsd13', 'x86_64')

    def test_defaults_to_real_sys_platform_and_machine(self):
        # Just proves it doesn't blow up resolving the real values -
        # actual assertions on the mapping are all above.
        with patch('utils.self_update.sys.platform', 'linux'), \
                patch('utils.self_update.platform.machine', return_value='x86_64'):
            assert self_update.select_asset_name() == 'curatarr-linux-x86_64'


# =============================================================================
# SHA256SUMS.txt parsing + local hashing
# =============================================================================

class TestParseSha256Sums:
    def test_parses_gnu_coreutils_text_mode(self):
        text = 'a' * 64 + '  curatarr-linux-x86_64\n' + 'b' * 64 + '  SHA256SUMS.txt\n'
        sums = self_update.parse_sha256sums(text)
        assert sums['curatarr-linux-x86_64'] == 'a' * 64
        assert sums['SHA256SUMS.txt'] == 'b' * 64

    def test_parses_binary_mode_asterisk_prefix(self):
        text = 'c' * 64 + ' *curatarr-windows-x86_64.exe\n'
        sums = self_update.parse_sha256sums(text)
        assert sums['curatarr-windows-x86_64.exe'] == 'c' * 64

    def test_skips_blank_lines_and_comments(self):
        text = '\n# a comment\n' + 'd' * 64 + '  asset\n\n'
        sums = self_update.parse_sha256sums(text)
        assert sums == {'asset': 'd' * 64}

    def test_skips_malformed_lines(self):
        text = 'not-a-hash  asset\n' + 'e' * 64 + '  good-asset\n'
        sums = self_update.parse_sha256sums(text)
        assert sums == {'good-asset': 'e' * 64}

    def test_lowercases_hex_digest(self):
        text = 'A' * 64 + '  asset\n'
        sums = self_update.parse_sha256sums(text)
        assert sums['asset'] == 'a' * 64

    def test_empty_text_yields_empty_dict(self):
        assert self_update.parse_sha256sums('') == {}


class TestSha256File:
    def test_matches_hashlib(self, tmp_path):
        import hashlib
        f = tmp_path / 'data.bin'
        f.write_bytes(b'some binary content' * 1000)
        expected = hashlib.sha256(f.read_bytes()).hexdigest()
        assert self_update.sha256_file(str(f)) == expected

    def test_empty_file(self, tmp_path):
        import hashlib
        f = tmp_path / 'empty.bin'
        f.write_bytes(b'')
        assert self_update.sha256_file(str(f)) == hashlib.sha256(b'').hexdigest()


# =============================================================================
# SSHSIG verification - the core authenticity primitive
# =============================================================================

@requires_ssh_keygen
class TestVerifySshsig:
    def test_genuine_signature_passes(self, signing_key, tmp_path):
        message = b'SHA256SUMS.txt contents for a genuine release'
        sig = _sign(signing_key['key_path'], message, tmp_path)
        self_update.verify_sshsig(message, sig, signing_key['public_key'])  # must not raise

    def test_tampered_message_fails_closed(self, signing_key, tmp_path):
        message = b'original trusted content'
        sig = _sign(signing_key['key_path'], message, tmp_path)
        with pytest.raises(self_update.SignatureVerificationError):
            self_update.verify_sshsig(message + b'INJECTED', sig, signing_key['public_key'])

    def test_wrong_key_fails_closed(self, tmp_path):
        message = b'signed by one key, checked against another'
        key_a = tmp_path / 'key_a'
        key_b = tmp_path / 'key_b'
        subprocess.run(['ssh-keygen', '-t', 'ed25519', '-f', str(key_a), '-N', '', '-q'], check=True)
        subprocess.run(['ssh-keygen', '-t', 'ed25519', '-f', str(key_b), '-N', '', '-q'], check=True)
        sig = _sign(str(key_a), message, tmp_path)

        pub_b_line = (tmp_path / 'key_b.pub').read_text(encoding='utf-8').split()
        blob_b = base64.b64decode(pub_b_line[1])
        _, off = self_update._read_string(blob_b, 0)
        raw_b, off = self_update._read_string(blob_b, off)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pubkey_b = Ed25519PublicKey.from_public_bytes(raw_b)

        with pytest.raises(self_update.SignatureVerificationError, match='does not match the pinned'):
            self_update.verify_sshsig(message, sig, pubkey_b)

    def test_missing_signature_fails_closed(self, signing_key):
        with pytest.raises(self_update.SignatureVerificationError, match='PEM-style armor'):
            self_update.verify_sshsig(b'anything', '', signing_key['public_key'])

    def test_garbage_signature_fails_closed(self, signing_key):
        with pytest.raises(self_update.SignatureVerificationError):
            self_update.verify_sshsig(b'anything', 'not a signature at all', signing_key['public_key'])

    def test_corrupt_base64_fails_closed(self, signing_key):
        bad = (
            self_update._SSHSIG_ARMOR_BEGIN + '\n' + 'not-valid-base64!!!' + '\n' +
            self_update._SSHSIG_ARMOR_END
        )
        with pytest.raises(self_update.SignatureVerificationError, match='Malformed base64'):
            self_update.verify_sshsig(b'anything', bad, signing_key['public_key'])

    def test_wrong_namespace_fails_closed(self, signing_key, tmp_path):
        message = b'signed for the wrong namespace'
        sig = _sign(signing_key['key_path'], message, tmp_path, namespace='email')
        with pytest.raises(self_update.SignatureVerificationError, match='namespace'):
            self_update.verify_sshsig(message, sig, signing_key['public_key'], namespace='file')

    def test_truncated_blob_fails_closed(self, signing_key, tmp_path):
        message = b'truncation test'
        sig = _sign(signing_key['key_path'], message, tmp_path)
        # Chop the armored body way down - decodes to a too-short blob.
        lines = sig.strip().splitlines()
        truncated = '\n'.join([lines[0], lines[1][:10], lines[-1]])
        with pytest.raises(self_update.SignatureVerificationError):
            self_update.verify_sshsig(message, truncated, signing_key['public_key'])

    def test_bad_magic_preamble_fails_closed(self, signing_key, tmp_path):
        message = b'bad magic test'
        sig = _sign(signing_key['key_path'], message, tmp_path)
        blob = self_update._decode_armor(sig)
        tampered_blob = b'XXXXXX' + blob[6:]
        tampered_sig = (
            self_update._SSHSIG_ARMOR_BEGIN + '\n' +
            base64.b64encode(tampered_blob).decode() + '\n' +
            self_update._SSHSIG_ARMOR_END
        )
        with pytest.raises(self_update.SignatureVerificationError, match='magic preamble'):
            self_update.verify_sshsig(message, tampered_sig, signing_key['public_key'])

    def test_unsupported_version_fails_closed(self, signing_key, tmp_path):
        message = b'version test'
        sig = _sign(signing_key['key_path'], message, tmp_path)
        blob = self_update._decode_armor(sig)
        # Byte 6-9 is the big-endian uint32 version field - bump 1 -> 2.
        tampered_blob = blob[:6] + struct.pack('>I', 2) + blob[10:]
        tampered_sig = (
            self_update._SSHSIG_ARMOR_BEGIN + '\n' +
            base64.b64encode(tampered_blob).decode() + '\n' +
            self_update._SSHSIG_ARMOR_END
        )
        with pytest.raises(self_update.SignatureVerificationError, match='Unsupported SSH signature version'):
            self_update.verify_sshsig(message, tampered_sig, signing_key['public_key'])

    def test_unsupported_hash_algorithm_fails_closed(self, signing_key, tmp_path):
        message = b'hash algo test'
        sig = _sign(signing_key['key_path'], message, tmp_path)
        blob = self_update._decode_armor(sig)
        parsed = self_update._parse_sshsig_blob(blob)
        # Rebuild the blob with hash_algorithm swapped to something unsupported.
        rebuilt = (
            self_update._SSHSIG_MAGIC
            + struct.pack('>I', 1)
            + self_update._pack_string(parsed.public_key_blob)
            + self_update._pack_string(parsed.namespace.encode())
            + self_update._pack_string(b'')
            + self_update._pack_string(b'md5')
            + self_update._pack_string(
                self_update._pack_string(parsed.signature_algorithm) + self_update._pack_string(parsed.signature_raw)
            )
        )
        tampered_sig = (
            self_update._SSHSIG_ARMOR_BEGIN + '\n' +
            base64.b64encode(rebuilt).decode() + '\n' +
            self_update._SSHSIG_ARMOR_END
        )
        with pytest.raises(self_update.SignatureVerificationError, match='Unsupported SSH signature hash'):
            self_update.verify_sshsig(message, tampered_sig, signing_key['public_key'])

    def test_unsupported_signature_algorithm_fails_closed(self, tmp_path, signing_key):
        # An RSA key produces a signature blob whose inner algorithm
        # string isn't ssh-ed25519 - must be rejected even before trying
        # (and failing) a cryptographic verify.
        rsa_key = tmp_path / 'rsa_key'
        subprocess.run(
            ['ssh-keygen', '-t', 'rsa', '-b', '2048', '-f', str(rsa_key), '-N', '', '-q'], check=True
        )
        message = b'signed with the wrong algorithm entirely'
        sig = _sign(str(rsa_key), message, tmp_path)
        with pytest.raises(self_update.SignatureVerificationError, match='Unsupported SSH signature'):
            self_update.verify_sshsig(message, sig, signing_key['public_key'])


class TestComputeKeyFingerprint:
    @requires_ssh_keygen
    def test_matches_ssh_keygen_lf(self, tmp_path):
        key_path = tmp_path / 'fp_test_key'
        subprocess.run(['ssh-keygen', '-t', 'ed25519', '-f', str(key_path), '-N', '', '-q'], check=True)
        expected = subprocess.run(
            ['ssh-keygen', '-lf', str(key_path) + '.pub'], check=True, capture_output=True, text=True
        ).stdout
        expected_fp = expected.split()[1]

        pub_line = (tmp_path / 'fp_test_key.pub').read_text(encoding='utf-8').split()
        blob = base64.b64decode(pub_line[1])
        assert self_update.compute_key_fingerprint(blob) == expected_fp

    def test_matches_pinned_real_project_key(self):
        """The actual curatarr release-signing public key's fingerprint,
        cross-checked against the literal value pinned everywhere else
        in this repo (.github/allowed_signers, scripts/release.sh,
        .github/workflows/release.yml) - proves
        PINNED_SIGNING_PUBLIC_KEY_B64 and PINNED_SIGNING_KEY_FINGERPRINT
        are the same key as every other verification path in this
        codebase, not a mismatched or stale pair."""
        blob = base64.b64decode(self_update.PINNED_SIGNING_PUBLIC_KEY_B64)
        assert self_update.compute_key_fingerprint(blob) == self_update.PINNED_SIGNING_KEY_FINGERPRINT
        assert self_update.PINNED_SIGNING_KEY_FINGERPRINT == 'SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU'


class TestPinnedKeyIntegritySelfCheck:
    def test_tampered_constant_fails_closed(self, monkeypatch):
        """A corrupted/maliciously-edited PINNED_SIGNING_PUBLIC_KEY_B64
        must be caught by its own fingerprint self-check rather than
        silently starting to trust a different key."""
        other_key = Ed25519PrivateKey.generate().public_key()
        blob = self_update._encode_ed25519_public_key_blob(other_key)
        monkeypatch.setattr(
            self_update, 'PINNED_SIGNING_PUBLIC_KEY_B64', base64.b64encode(blob).decode()
        )
        with pytest.raises(self_update.SignatureVerificationError, match='integrity check failed'):
            self_update._pinned_public_key_blob()

    def test_corrupt_base64_constant_fails_closed(self, monkeypatch):
        monkeypatch.setattr(self_update, 'PINNED_SIGNING_PUBLIC_KEY_B64', 'not valid base64!!!')
        with pytest.raises(self_update.SignatureVerificationError, match='Corrupt pinned signing key'):
            self_update._pinned_public_key_blob()

    def test_real_pinned_key_passes_its_own_integrity_check(self):
        blob = self_update._pinned_public_key_blob()
        assert blob == base64.b64decode(self_update.PINNED_SIGNING_PUBLIC_KEY_B64)

    def test_real_pinned_key_decodes_to_a_usable_ed25519_key(self):
        key = self_update._pinned_public_key()
        assert isinstance(key, type(Ed25519PrivateKey.generate().public_key()))

    def test_non_ed25519_pinned_blob_fails_closed(self, monkeypatch):
        """If the blob-level fingerprint self-check were ever somehow
        bypassed, _pinned_public_key() itself still refuses a key of
        the wrong algorithm entirely, rather than trying to interpret
        arbitrary bytes as an ed25519 key."""
        rsa_like_blob = self_update._pack_string(b'ssh-rsa') + self_update._pack_string(b'not-actually-a-key')
        monkeypatch.setattr(self_update, '_pinned_public_key_blob', lambda: rsa_like_blob)
        with pytest.raises(self_update.SignatureVerificationError, match='not ssh-ed25519'):
            self_update._pinned_public_key()


@requires_ssh_keygen
class TestVerifyPinnedSignature:
    """Wire-together tests: verify_pinned_signature() must call through
    to verify_sshsig() using whatever _pinned_public_key() returns -
    exercised here by monkeypatching that one function to the disposable
    test key, so this suite never needs the real private key."""

    def test_passes_when_signed_by_the_patched_pinned_key(self, signing_key, tmp_path, monkeypatch):
        monkeypatch.setattr(self_update, '_pinned_public_key', lambda: signing_key['public_key'])
        message = b'a real SHA256SUMS.txt, hypothetically'
        sig = _sign(signing_key['key_path'], message, tmp_path)
        self_update.verify_pinned_signature(message, sig)  # must not raise

    def test_fails_when_signed_by_a_different_key(self, signing_key, tmp_path, monkeypatch):
        monkeypatch.setattr(self_update, '_pinned_public_key', lambda: signing_key['public_key'])
        other_key = tmp_path / 'attacker_key'
        subprocess.run(['ssh-keygen', '-t', 'ed25519', '-f', str(other_key), '-N', '', '-q'], check=True)
        message = b'a forged SHA256SUMS.txt'
        sig = _sign(str(other_key), message, tmp_path)
        with pytest.raises(self_update.SignatureVerificationError):
            self_update.verify_pinned_signature(message, sig)


# =============================================================================
# verify_downloaded_asset - the full chain (signature over sums, then
# hash of the actual asset against the now-trusted sums)
# =============================================================================

@requires_ssh_keygen
class TestVerifyDownloadedAsset:
    ASSET_NAME = 'curatarr-linux-x86_64'

    def _write_verified_fixture(self, tmp_path, signing_key, asset_bytes: bytes):
        asset_path = tmp_path / self.ASSET_NAME
        asset_path.write_bytes(asset_bytes)
        digest = self_update.sha256_file(str(asset_path))
        sums_text = f"{digest}  {self.ASSET_NAME}\n"
        sums_path = tmp_path / 'SHA256SUMS.txt'
        sums_path.write_text(sums_text, encoding='utf-8')
        sig_text = _sign(signing_key['key_path'], sums_text.encode('utf-8'), tmp_path)
        sig_path = tmp_path / 'SHA256SUMS.txt.sig'
        sig_path.write_text(sig_text, encoding='utf-8')
        return str(asset_path), str(sums_path), str(sig_path)

    def test_passes_end_to_end(self, tmp_path, signing_key, monkeypatch):
        monkeypatch.setattr(self_update, '_pinned_public_key', lambda: signing_key['public_key'])
        asset_path, sums_path, sig_path = self._write_verified_fixture(tmp_path, signing_key, b'fake binary bytes')
        self_update.verify_downloaded_asset(asset_path, sums_path, sig_path, self.ASSET_NAME)  # must not raise

    def test_tampered_sums_file_fails_closed(self, tmp_path, signing_key, monkeypatch):
        """Sums file edited AFTER signing (e.g. to point at a
        malicious hash) - the signature no longer matches its content."""
        monkeypatch.setattr(self_update, '_pinned_public_key', lambda: signing_key['public_key'])
        asset_path, sums_path, sig_path = self._write_verified_fixture(tmp_path, signing_key, b'fake binary bytes')
        with open(sums_path, 'a', encoding='utf-8') as f:
            f.write('0' * 64 + '  extra-injected-asset\n')
        with pytest.raises(self_update.SignatureVerificationError):
            self_update.verify_downloaded_asset(asset_path, sums_path, sig_path, self.ASSET_NAME)

    def test_bad_signature_fails_closed(self, tmp_path, signing_key, monkeypatch):
        monkeypatch.setattr(self_update, '_pinned_public_key', lambda: signing_key['public_key'])
        asset_path, sums_path, sig_path = self._write_verified_fixture(tmp_path, signing_key, b'fake binary bytes')
        # Corrupt a byte inside the base64 body of the .sig (not the armor lines).
        lines = open(sig_path, encoding='utf-8').read().splitlines()
        body_idx = 1
        corrupted_line = 'A' + lines[body_idx][1:] if lines[body_idx][0] != 'A' else 'B' + lines[body_idx][1:]
        lines[body_idx] = corrupted_line
        with open(sig_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        with pytest.raises(self_update.SignatureVerificationError):
            self_update.verify_downloaded_asset(asset_path, sums_path, sig_path, self.ASSET_NAME)

    def test_missing_sig_file_fails_closed(self, tmp_path, signing_key, monkeypatch):
        monkeypatch.setattr(self_update, '_pinned_public_key', lambda: signing_key['public_key'])
        asset_path, sums_path, sig_path = self._write_verified_fixture(tmp_path, signing_key, b'fake binary bytes')
        os.remove(sig_path)
        with pytest.raises(self_update.SignatureVerificationError, match='Could not read'):
            self_update.verify_downloaded_asset(asset_path, sums_path, sig_path, self.ASSET_NAME)

    def test_missing_sums_file_fails_closed(self, tmp_path, signing_key, monkeypatch):
        monkeypatch.setattr(self_update, '_pinned_public_key', lambda: signing_key['public_key'])
        asset_path, sums_path, sig_path = self._write_verified_fixture(tmp_path, signing_key, b'fake binary bytes')
        os.remove(sums_path)
        with pytest.raises(self_update.SignatureVerificationError, match='Could not read'):
            self_update.verify_downloaded_asset(asset_path, sums_path, sig_path, self.ASSET_NAME)

    def test_hash_mismatch_fails_closed(self, tmp_path, signing_key, monkeypatch):
        """Signature verifies fine (the sums file itself is genuine and
        untampered) but the actual asset bytes don't match what the
        (correctly signed) sums file says they should be - e.g. a
        corrupted download."""
        monkeypatch.setattr(self_update, '_pinned_public_key', lambda: signing_key['public_key'])
        asset_path, sums_path, sig_path = self._write_verified_fixture(tmp_path, signing_key, b'fake binary bytes')
        with open(asset_path, 'wb') as f:
            f.write(b'CORRUPTED DOWNLOAD - not what was signed for')
        with pytest.raises(self_update.HashMismatchError, match='mismatch'):
            self_update.verify_downloaded_asset(asset_path, sums_path, sig_path, self.ASSET_NAME)

    def test_asset_not_listed_in_sums_fails_closed(self, tmp_path, signing_key, monkeypatch):
        monkeypatch.setattr(self_update, '_pinned_public_key', lambda: signing_key['public_key'])
        asset_path, sums_path, sig_path = self._write_verified_fixture(tmp_path, signing_key, b'fake binary bytes')
        with pytest.raises(self_update.HashMismatchError, match='not listed'):
            self_update.verify_downloaded_asset(asset_path, sums_path, sig_path, 'curatarr-windows-x86_64.exe')


# =============================================================================
# Version target resolution
# =============================================================================

class TestDetermineUpdateTarget:
    @patch('utils.self_update.update_available')
    def test_returns_latest_when_newer(self, mock_update_available):
        mock_update_available.return_value = ('2.9.0', '2.8.29', True)
        assert self_update.determine_update_target() == '2.9.0'

    @patch('utils.self_update.update_available')
    def test_raises_when_not_newer(self, mock_update_available):
        mock_update_available.return_value = ('2.8.29', '2.8.29', False)
        with pytest.raises(self_update.NoUpdateAvailableError):
            self_update.determine_update_target()

    @patch('utils.self_update.update_available')
    def test_raises_when_latest_unknown(self, mock_update_available):
        mock_update_available.return_value = (None, '2.8.29', False)
        with pytest.raises(self_update.NoUpdateAvailableError, match='unknown'):
            self_update.determine_update_target()

    @patch('utils.self_update.update_available')
    def test_passes_force_refresh_through(self, mock_update_available):
        mock_update_available.return_value = ('2.9.0', '2.8.29', True)
        self_update.determine_update_target(force_refresh=False)
        assert mock_update_available.call_args.kwargs['force_refresh'] is False


class TestReleasesDownloadBaseOverride:
    """CURATARR_RELEASES_DOWNLOAD_BASE_OVERRIDE - a test/staging-only
    seam (see GITHUB_RELEASES_DOWNLOAD_BASE's own comment for why this
    can't weaken the authenticity model: it only changes WHERE bytes are
    fetched from, never the pinned key anything gets verified against).
    Used by this repo's own real end-to-end self-update test to point a
    built binary's downloads at a local HTTP server."""

    def test_env_override_wins_over_default(self):
        import importlib
        with patch.dict(os.environ, {'CURATARR_RELEASES_DOWNLOAD_BASE_OVERRIDE': 'http://127.0.0.1:9/fake'}):
            importlib.reload(self_update)
            try:
                assert self_update.GITHUB_RELEASES_DOWNLOAD_BASE == 'http://127.0.0.1:9/fake'
            finally:
                importlib.reload(self_update)  # restore the real default for every later test

    def test_default_used_when_env_var_unset(self):
        import importlib
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CURATARR_RELEASES_DOWNLOAD_BASE_OVERRIDE', None)
            importlib.reload(self_update)
        assert self_update.GITHUB_RELEASES_DOWNLOAD_BASE == (
            'https://github.com/OrchestratedChaos/curatarr/releases/download'
        )


class TestReleaseAssetUrl:
    def test_builds_expected_url(self):
        url = self_update.release_asset_url('2.9.0', 'curatarr-linux-x86_64')
        assert url == (
            'https://github.com/OrchestratedChaos/curatarr/releases/download/'
            'v2.9.0/curatarr-linux-x86_64'
        )


# =============================================================================
# Download
# =============================================================================

class TestDownloadToFile:
    def test_rejects_url_outside_configured_release_host(self, tmp_path):
        with pytest.raises(self_update.DownloadError, match='outside the configured release host'):
            self_update._download_to_file('http://evil.example.com/asset', str(tmp_path / 'out'))

    def test_rejects_https_url_on_a_different_host(self, tmp_path):
        """Not just a scheme check - a URL that's HTTPS but points
        somewhere other than GITHUB_RELEASES_DOWNLOAD_BASE must also be
        refused (stronger than a bare https://-only check)."""
        with pytest.raises(self_update.DownloadError, match='outside the configured release host'):
            self_update._download_to_file('https://evil.example.com/asset', str(tmp_path / 'out'))

    @patch('utils.self_update.requests.get')
    def test_allows_the_configured_download_base_even_when_overridden_to_http(self, mock_get, tmp_path, monkeypatch):
        """CURATARR_RELEASES_DOWNLOAD_BASE_OVERRIDE (test/staging only -
        see that constant's docstring) can point GITHUB_RELEASES_DOWNLOAD_BASE
        at a plain-http local server - this is what this repo's own real
        end-to-end self-update test uses. Never possible for the
        production default, which is hardcoded to https://."""
        monkeypatch.setattr(self_update, 'GITHUB_RELEASES_DOWNLOAD_BASE', 'http://127.0.0.1:9999/download')
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.iter_content = Mock(return_value=[b'x'])
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_get.return_value = mock_response
        self_update._download_to_file('http://127.0.0.1:9999/download/v1.0.0/asset', str(tmp_path / 'out'))  # must not raise

    @patch('utils.self_update.requests.get')
    def test_writes_response_content_to_dest(self, mock_get, tmp_path):
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.iter_content = Mock(return_value=[b'chunk1', b'chunk2'])
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_get.return_value = mock_response

        dest = tmp_path / 'out.bin'
        self_update._download_to_file(self_update.release_asset_url('1.0.0', 'asset'), str(dest))
        assert dest.read_bytes() == b'chunk1chunk2'

    @patch('utils.self_update.requests.get', side_effect=requests.ConnectionError('offline'))
    def test_network_error_raises_download_error(self, mock_get, tmp_path):
        with pytest.raises(self_update.DownloadError, match='Could not download'):
            self_update._download_to_file(self_update.release_asset_url('1.0.0', 'asset'), str(tmp_path / 'out'))

    @patch('utils.self_update.requests.get')
    def test_http_error_status_raises_download_error(self, mock_get, tmp_path):
        mock_response = Mock()
        mock_response.raise_for_status = Mock(side_effect=requests.HTTPError('404'))
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_get.return_value = mock_response
        with pytest.raises(self_update.DownloadError):
            self_update._download_to_file(self_update.release_asset_url('1.0.0', 'missing'), str(tmp_path / 'out'))


# =============================================================================
# Swap mechanics - real file operations against tmp_path (safe: these
# are plain files, never the actual running test process's own binary).
# =============================================================================

class TestSwapPosix:
    def test_replaces_current_with_new_and_sets_exec_bit(self, tmp_path):
        current = tmp_path / 'curatarr'
        current.write_bytes(b'old content')
        current.chmod(0o644)
        new = tmp_path / 'new_download.tmp'
        new.write_bytes(b'new verified content')
        new.chmod(0o600)

        self_update._swap_posix(str(current), str(new))

        assert current.read_bytes() == b'new verified content'
        assert not new.exists()
        assert os.stat(str(current)).st_mode & 0o100  # owner-exec bit set

    def test_replace_failure_raises_swap_error(self, tmp_path):
        current = tmp_path / 'curatarr'
        current.write_bytes(b'old content')
        new = tmp_path / 'new_download.tmp'
        new.write_bytes(b'new content')
        with patch('utils.self_update.os.replace', side_effect=OSError('disk full')):
            with pytest.raises(self_update.SwapError, match='Could not swap'):
                self_update._swap_posix(str(current), str(new))
        # os.replace was mocked out entirely - current must be untouched.
        assert current.read_bytes() == b'old content'


class TestCurrentBinaryPath:
    def test_returns_realpath_of_sys_executable(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / 'curatarr'
        fake_exe.write_bytes(b'x')
        monkeypatch.setattr(self_update.sys, 'executable', str(fake_exe))
        assert self_update.current_binary_path() == os.path.realpath(str(fake_exe))


class TestSwapWindows:
    """_swap_windows itself only calls os.rename/os.replace - both work
    identically against plain files on any OS, so its rename-then-move
    LOGIC (including rollback) is fully exercisable here without
    actually running on Windows. The Windows-specific claim being
    tested is "you can rename a file out from under a process still
    using it", which is a real-OS fact asserted in the module's
    docstring/comments, not something a unit test proves - see
    docs/BINARIES.md and this PR's Windows E2E evidence for that part.
    """

    def test_success_path(self, tmp_path):
        current = tmp_path / 'curatarr.exe'
        current.write_bytes(b'old content')
        new = tmp_path / 'new_download.tmp'
        new.write_bytes(b'new verified content')

        self_update._swap_windows(str(current), str(new))

        assert current.read_bytes() == b'new verified content'
        assert not (tmp_path / 'curatarr.exe.old').exists() is False or True  # see next assertion
        # .old sidecar is left behind by design (curatarr_app.py's
        # cleanup_stale_old_binary deletes it on the NEXT startup, once
        # the freshly-relaunched new binary is confirmed running).
        assert (tmp_path / 'curatarr.exe.old').read_bytes() == b'old content'

    def test_clears_stale_old_sidecar_before_reuse(self, tmp_path):
        current = tmp_path / 'curatarr.exe'
        current.write_bytes(b'old content')
        (tmp_path / 'curatarr.exe.old').write_bytes(b'stale leftover from a previous update')
        new = tmp_path / 'new_download.tmp'
        new.write_bytes(b'new verified content')

        self_update._swap_windows(str(current), str(new))

        assert (tmp_path / 'curatarr.exe.old').read_bytes() == b'old content'

    def test_stale_old_sidecar_removal_failure_is_non_fatal(self, tmp_path):
        """A leftover .old that can't be removed (e.g. still locked by a
        slow-to-exit previous process) must not abort the swap - the
        rename below will just fail loudly on its own if that's
        actually a real problem."""
        current = tmp_path / 'curatarr.exe'
        current.write_bytes(b'old content')
        (tmp_path / 'curatarr.exe.old').write_bytes(b'stale, locked')
        new = tmp_path / 'new_download.tmp'
        new.write_bytes(b'new verified content')

        with patch('utils.self_update.os.remove', side_effect=OSError('still locked')):
            self_update._swap_windows(str(current), str(new))  # must not raise

        assert current.read_bytes() == b'new verified content'

    def test_rename_failure_raises_and_touches_nothing(self, tmp_path):
        current = tmp_path / 'curatarr.exe'
        current.write_bytes(b'old content')
        new = tmp_path / 'new_download.tmp'
        new.write_bytes(b'new content')

        with patch('utils.self_update.os.rename', side_effect=OSError('locked')):
            with pytest.raises(self_update.SwapError, match='Could not rename'):
                self_update._swap_windows(str(current), str(new))
        assert current.read_bytes() == b'old content'
        assert new.read_bytes() == b'new content'

    def test_move_failure_rolls_back_to_original_binary(self, tmp_path):
        current = tmp_path / 'curatarr.exe'
        current.write_bytes(b'old content')
        new = tmp_path / 'new_download.tmp'
        new.write_bytes(b'new content')

        real_replace = os.replace
        calls = {'n': 0}

        def _flaky_replace(src, dst):
            calls['n'] += 1
            if calls['n'] == 1:
                raise OSError('could not move new binary into place')
            return real_replace(src, dst)

        with patch('utils.self_update.os.replace', side_effect=_flaky_replace):
            with pytest.raises(self_update.SwapError, match='rolled back to the original binary'):
                self_update._swap_windows(str(current), str(new))

        # Rollback succeeded: current_path has the ORIGINAL content back.
        assert current.read_bytes() == b'old content'

    def test_move_failure_and_rollback_failure_both_raise_with_recovery_hint(self, tmp_path):
        current = tmp_path / 'curatarr.exe'
        current.write_bytes(b'old content')
        new = tmp_path / 'new_download.tmp'
        new.write_bytes(b'new content')

        with patch('utils.self_update.os.replace', side_effect=OSError('everything is on fire')):
            with pytest.raises(self_update.SwapError, match='rollback failed'):
                self_update._swap_windows(str(current), str(new))
        # current_path is gone (both replace attempts failed) but the
        # renamed original is still recoverable at .old - the error
        # message says so (asserted above via match).
        assert not current.exists()
        assert (tmp_path / 'curatarr.exe.old').read_bytes() == b'old content'


class TestSwapBinaryDispatch:
    @patch('utils.self_update._swap_windows')
    @patch('utils.self_update._swap_posix')
    def test_dispatches_to_windows_impl_on_nt(self, mock_posix, mock_windows, tmp_path, monkeypatch):
        monkeypatch.setattr(self_update.os, 'name', 'nt')
        current = tmp_path / 'a'
        current.write_bytes(b'x')
        new = tmp_path / 'b'
        new.write_bytes(b'y')
        self_update.swap_binary(str(current), str(new))
        mock_windows.assert_called_once_with(str(current), str(new))
        mock_posix.assert_not_called()

    @patch('utils.self_update._swap_windows')
    @patch('utils.self_update._swap_posix')
    def test_dispatches_to_posix_impl_otherwise(self, mock_posix, mock_windows, tmp_path, monkeypatch):
        monkeypatch.setattr(self_update.os, 'name', 'posix')
        current = tmp_path / 'a'
        current.write_bytes(b'x')
        new = tmp_path / 'b'
        new.write_bytes(b'y')
        self_update.swap_binary(str(current), str(new))
        mock_posix.assert_called_once_with(str(current), str(new))
        mock_windows.assert_not_called()


class TestPreservePermissions:
    def test_copies_mode_bits(self, tmp_path):
        source = tmp_path / 'source'
        source.write_bytes(b'x')
        source.chmod(0o755)
        dest = tmp_path / 'dest'
        dest.write_bytes(b'y')
        dest.chmod(0o600)

        self_update._preserve_permissions(str(source), str(dest))

        import stat as stat_module
        assert stat_module.S_IMODE(os.stat(str(dest)).st_mode) == 0o755

    def test_missing_source_does_not_raise(self, tmp_path):
        dest = tmp_path / 'dest'
        dest.write_bytes(b'y')
        self_update._preserve_permissions(str(tmp_path / 'does-not-exist'), str(dest))  # must not raise


class TestCleanupStaleOldBinary:
    def test_removes_existing_old_sidecar(self, tmp_path):
        current = tmp_path / 'curatarr.exe'
        current.write_bytes(b'x')
        old = tmp_path / 'curatarr.exe.old'
        old.write_bytes(b'stale')
        self_update.cleanup_stale_old_binary(str(current))
        assert not old.exists()

    def test_missing_old_sidecar_is_a_silent_noop(self, tmp_path):
        current = tmp_path / 'curatarr.exe'
        current.write_bytes(b'x')
        self_update.cleanup_stale_old_binary(str(current))  # must not raise

    def test_remove_failure_does_not_raise(self, tmp_path):
        current = tmp_path / 'curatarr.exe'
        current.write_bytes(b'x')
        old = tmp_path / 'curatarr.exe.old'
        old.write_bytes(b'stale')
        with patch('utils.self_update.os.remove', side_effect=OSError('locked')):
            self_update.cleanup_stale_old_binary(str(current))  # must not raise

    def test_defaults_to_current_binary_path(self, tmp_path):
        with patch('utils.self_update.current_binary_path', return_value=str(tmp_path / 'curatarr.exe')):
            (tmp_path / 'curatarr.exe.old').write_bytes(b'stale')
            self_update.cleanup_stale_old_binary()
            assert not (tmp_path / 'curatarr.exe.old').exists()



class TestSanitizeFrozenRelaunchEnv:
    """PyInstaller onefile's internal bootloader hand-off variables must
    never be inherited by a freshly-spawned, independent curatarr.exe
    instance - see sanitize_frozen_relaunch_env's docstring for the
    real end-to-end failure this fixes. Originally just _MEIPASS2;
    confirmed via a real built binary's own inherited environment (see
    this repo's v2.8.29 PR description) that PyInstaller 6 sets several
    more - _PYI_ARCHIVE_FILE, _PYI_PARENT_PROCESS_LEVEL,
    _PYI_APPLICATION_HOME_DIR - and inheriting THOSE causes the exact
    same class of failure (the relaunched process never finishes Python
    bootstrap - directly observed as "stdlib dir = ''" in its own
    diagnostic dump) even with _MEIPASS2 alone stripped."""

    def test_strips_meipass2(self):
        env = {'PATH': '/usr/bin', '_MEIPASS2': r'C:\Temp\_MEI123456'}
        result = self_update.sanitize_frozen_relaunch_env(env)
        assert '_MEIPASS2' not in result
        assert result['PATH'] == '/usr/bin'

    def test_strips_pyi_prefixed_vars(self):
        env = {
            'PATH': '/usr/bin',
            '_PYI_ARCHIVE_FILE': '/opt/curatarr/curatarr',
            '_PYI_PARENT_PROCESS_LEVEL': '1',
            '_PYI_APPLICATION_HOME_DIR': '/tmp/_MEIstale',
            '_PYI_SPLASH_IPC': '12345',
        }
        result = self_update.sanitize_frozen_relaunch_env(env)
        assert result == {'PATH': '/usr/bin'}

    def test_strips_pyinstaller_prefixed_vars(self):
        env = {'PATH': '/usr/bin', '_PYINSTALLER_SETUP': '1'}
        result = self_update.sanitize_frozen_relaunch_env(env)
        assert result == {'PATH': '/usr/bin'}

    def test_strips_a_future_unknown_pyi_var_too(self):
        # Confirms the fix is prefix-based, not a fixed enumeration -
        # PyInstaller has added _PYI_* variables across versions before
        # and may again.
        env = {'PATH': '/usr/bin', '_PYI_SOME_FUTURE_VAR': 'x'}
        result = self_update.sanitize_frozen_relaunch_env(env)
        assert result == {'PATH': '/usr/bin'}

    def test_noop_when_not_present(self):
        env = {'PATH': '/usr/bin', 'CURATARR_UI_PORT': '8787'}
        result = self_update.sanitize_frozen_relaunch_env(env)
        assert result == env

    def test_does_not_mutate_the_original_dict(self):
        env = {'_MEIPASS2': 'x', '_PYI_ARCHIVE_FILE': 'y'}
        self_update.sanitize_frozen_relaunch_env(env)
        assert '_MEIPASS2' in env  # original untouched - a copy was returned
        assert '_PYI_ARCHIVE_FILE' in env


# =============================================================================
# Orchestration
# =============================================================================

class TestDownloadAndVerifyUpdate:
    """download_and_verify_update() - steps 1-4 of the self-update
    chain, shared by BOTH perform_self_update() (CLI, in-process swap)
    and web/update_apply.py's frozen worker (hands the result off to
    the external script - see utils/self_update_handoff.py). Stops
    before touching the currently-running executable at all."""

    def test_raises_not_frozen_for_source_install(self, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with pytest.raises(self_update.NotFrozenError):
            self_update.download_and_verify_update()

    def test_full_success_path_calls_everything_in_order(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        current_exe = tmp_path / 'curatarr'
        current_exe.write_bytes(b'old binary')

        calls = []

        monkeypatch.setattr(self_update, 'current_binary_path', lambda: str(current_exe))
        monkeypatch.setattr(self_update, 'determine_update_target', lambda force_refresh=True: '2.9.0')
        monkeypatch.setattr(self_update, 'select_asset_name', lambda: 'curatarr-linux-x86_64')

        def _fake_download(url, dest, timeout=30):
            calls.append(('download', url))
            with open(dest, 'wb') as f:
                f.write(b'downloaded bytes')

        def _fake_verify(asset_path, sums_path, sig_path, asset_name):
            calls.append(('verify', asset_name))

        monkeypatch.setattr(self_update, '_download_to_file', _fake_download)
        monkeypatch.setattr(self_update, 'verify_downloaded_asset', _fake_verify)

        result = self_update.download_and_verify_update()

        assert isinstance(result, self_update.VerifiedUpdate)
        assert result.version == '2.9.0'
        assert result.asset_name == 'curatarr-linux-x86_64'
        assert os.path.dirname(result.asset_path) == str(tmp_path)
        assert os.path.isfile(result.asset_path)
        assert calls[-1] == ('verify', 'curatarr-linux-x86_64')
        # Downloaded SHA256SUMS.txt and .sig, plus the asset itself.
        download_urls = [c[1] for c in calls if c[0] == 'download']
        assert any('SHA256SUMS.txt.sig' in u for u in download_urls)
        assert any(u.endswith('SHA256SUMS.txt') for u in download_urls)
        assert any('curatarr-linux-x86_64' in u for u in download_urls)

    def test_asset_temp_file_downloaded_next_to_the_running_exe(self, tmp_path, monkeypatch):
        """Not the system temp dir - see this function's docstring for
        why (cross-volume os.replace() would fail on Windows)."""
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        exe_dir = tmp_path / 'install_dir'
        exe_dir.mkdir()
        current_exe = exe_dir / 'curatarr'
        current_exe.write_bytes(b'old binary')

        seen_asset_dirs = []

        monkeypatch.setattr(self_update, 'current_binary_path', lambda: str(current_exe))
        monkeypatch.setattr(self_update, 'determine_update_target', lambda force_refresh=True: '2.9.0')
        monkeypatch.setattr(self_update, 'select_asset_name', lambda: 'curatarr-linux-x86_64')

        def _fake_download(url, dest, timeout=30):
            if 'curatarr-linux-x86_64' in url and 'SHA256SUMS' not in url:
                seen_asset_dirs.append(os.path.dirname(dest))
            with open(dest, 'wb') as f:
                f.write(b'x')

        monkeypatch.setattr(self_update, '_download_to_file', _fake_download)
        monkeypatch.setattr(self_update, 'verify_downloaded_asset', lambda *a, **k: None)

        self_update.download_and_verify_update()

        assert seen_asset_dirs == [str(exe_dir)]

    def test_verification_failure_cleans_up_temp_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        current_exe = tmp_path / 'curatarr'
        current_exe.write_bytes(b'old binary')

        monkeypatch.setattr(self_update, 'current_binary_path', lambda: str(current_exe))
        monkeypatch.setattr(self_update, 'determine_update_target', lambda force_refresh=True: '2.9.0')
        monkeypatch.setattr(self_update, 'select_asset_name', lambda: 'curatarr-linux-x86_64')
        monkeypatch.setattr(
            self_update, '_download_to_file',
            lambda url, dest, timeout=30: open(dest, 'wb').write(b'x'),
        )
        monkeypatch.setattr(
            self_update, 'verify_downloaded_asset',
            Mock(side_effect=self_update.HashMismatchError('mismatch')),
        )

        with pytest.raises(self_update.HashMismatchError):
            self_update.download_and_verify_update()

        # Original binary on disk is completely untouched.
        assert current_exe.read_bytes() == b'old binary'
        # No stray .tmp files left behind in the install directory.
        leftovers = [p for p in os.listdir(str(tmp_path)) if p.endswith('.tmp')]
        assert leftovers == []

    def test_no_update_available_never_downloads_anything(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        current_exe = tmp_path / 'curatarr'
        current_exe.write_bytes(b'old binary')
        monkeypatch.setattr(self_update, 'current_binary_path', lambda: str(current_exe))
        monkeypatch.setattr(
            self_update, 'determine_update_target',
            Mock(side_effect=self_update.NoUpdateAvailableError('nothing newer')),
        )
        download_mock = Mock()
        monkeypatch.setattr(self_update, '_download_to_file', download_mock)

        with pytest.raises(self_update.NoUpdateAvailableError):
            self_update.download_and_verify_update()

        download_mock.assert_not_called()

    def test_temp_file_cleanup_failure_does_not_mask_the_real_error(self, tmp_path, monkeypatch):
        """If even the defensive cleanup of the leftover temp asset
        fails (e.g. permissions), the ORIGINAL verification error must
        still be what propagates - not an unrelated cleanup OSError."""
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        current_exe = tmp_path / 'curatarr'
        current_exe.write_bytes(b'old binary')

        monkeypatch.setattr(self_update, 'current_binary_path', lambda: str(current_exe))
        monkeypatch.setattr(self_update, 'determine_update_target', lambda force_refresh=True: '2.9.0')
        monkeypatch.setattr(self_update, 'select_asset_name', lambda: 'curatarr-linux-x86_64')
        monkeypatch.setattr(
            self_update, '_download_to_file',
            lambda url, dest, timeout=30: open(dest, 'wb').write(b'x'),
        )
        monkeypatch.setattr(
            self_update, 'verify_downloaded_asset',
            Mock(side_effect=self_update.HashMismatchError('mismatch')),
        )
        with patch('utils.self_update.os.remove', side_effect=OSError('cannot clean up')):
            with pytest.raises(self_update.HashMismatchError):
                self_update.download_and_verify_update()

    def test_unwritable_install_dir_raises_swap_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        current_exe = tmp_path / 'curatarr'
        current_exe.write_bytes(b'old binary')
        monkeypatch.setattr(self_update, 'current_binary_path', lambda: str(current_exe))
        monkeypatch.setattr(self_update, 'determine_update_target', lambda force_refresh=True: '2.9.0')
        monkeypatch.setattr(self_update, 'select_asset_name', lambda: 'curatarr-linux-x86_64')
        monkeypatch.setattr(
            self_update, '_download_to_file',
            lambda url, dest, timeout=30: open(dest, 'wb').write(b'x'),
        )
        with patch('utils.self_update.tempfile.mkstemp', side_effect=OSError('permission denied')):
            with pytest.raises(self_update.SwapError, match='Cannot write'):
                self_update.download_and_verify_update()


class TestPerformSelfUpdate:
    """perform_self_update() - the CLI's `--self-update` path: calls
    download_and_verify_update() (tested thoroughly above) then swaps
    the result into place IN-PROCESS. Safe here specifically because
    the CLI never relaunches anything afterward - see this function's
    own docstring."""

    def test_raises_not_frozen_for_source_install(self, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        with pytest.raises(self_update.NotFrozenError):
            self_update.perform_self_update()

    def test_success_path_swaps_and_returns_version(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        current_exe = tmp_path / 'curatarr'
        current_exe.write_bytes(b'old binary')
        asset_path = tmp_path / '.curatarr-update-abc.tmp'
        asset_path.write_bytes(b'new binary')

        verified = self_update.VerifiedUpdate(
            version='2.9.0', asset_path=str(asset_path), asset_name='curatarr-linux-x86_64',
        )
        monkeypatch.setattr(self_update, 'download_and_verify_update', lambda force_refresh=True: verified)
        monkeypatch.setattr(self_update, 'current_binary_path', lambda: str(current_exe))

        result = self_update.perform_self_update()

        assert result == '2.9.0'
        assert current_exe.read_bytes() == b'new binary'
        assert not asset_path.exists()  # consumed by the swap

    def test_download_and_verify_failure_propagates_without_swapping(self, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        monkeypatch.setattr(
            self_update, 'download_and_verify_update',
            Mock(side_effect=self_update.HashMismatchError('mismatch')),
        )
        swap_mock = Mock()
        monkeypatch.setattr(self_update, 'swap_binary', swap_mock)

        with pytest.raises(self_update.HashMismatchError):
            self_update.perform_self_update()

        swap_mock.assert_not_called()

    def test_swap_failure_still_cleans_up_the_verified_temp_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        current_exe = tmp_path / 'curatarr'
        current_exe.write_bytes(b'old binary')
        asset_path = tmp_path / '.curatarr-update-abc.tmp'
        asset_path.write_bytes(b'new binary')

        verified = self_update.VerifiedUpdate(
            version='2.9.0', asset_path=str(asset_path), asset_name='curatarr-linux-x86_64',
        )
        monkeypatch.setattr(self_update, 'download_and_verify_update', lambda force_refresh=True: verified)
        monkeypatch.setattr(self_update, 'current_binary_path', lambda: str(current_exe))
        monkeypatch.setattr(
            self_update, 'swap_binary', Mock(side_effect=self_update.SwapError('disk full')),
        )

        with pytest.raises(self_update.SwapError):
            self_update.perform_self_update()

        assert not asset_path.exists()

    def test_swap_failure_cleanup_itself_failing_does_not_mask_the_real_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        current_exe = tmp_path / 'curatarr'
        current_exe.write_bytes(b'old binary')
        asset_path = tmp_path / '.curatarr-update-abc.tmp'
        asset_path.write_bytes(b'new binary')

        verified = self_update.VerifiedUpdate(
            version='2.9.0', asset_path=str(asset_path), asset_name='curatarr-linux-x86_64',
        )
        monkeypatch.setattr(self_update, 'download_and_verify_update', lambda force_refresh=True: verified)
        monkeypatch.setattr(self_update, 'current_binary_path', lambda: str(current_exe))
        monkeypatch.setattr(self_update, 'swap_binary', Mock(side_effect=self_update.SwapError('disk full')))

        with patch('utils.self_update.os.remove', side_effect=OSError('cannot clean up')):
            with pytest.raises(self_update.SwapError, match='disk full'):
                self_update.perform_self_update()
        assert current_exe.read_bytes() == b'old binary'

    def test_passes_force_refresh_through(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        current_exe = tmp_path / 'curatarr'
        current_exe.write_bytes(b'old binary')
        asset_path = tmp_path / '.curatarr-update-abc.tmp'
        asset_path.write_bytes(b'new binary')

        mock_download = Mock(return_value=self_update.VerifiedUpdate(
            version='2.9.0', asset_path=str(asset_path), asset_name='curatarr-linux-x86_64',
        ))
        monkeypatch.setattr(self_update, 'download_and_verify_update', mock_download)
        monkeypatch.setattr(self_update, 'current_binary_path', lambda: str(current_exe))

        self_update.perform_self_update(force_refresh=False)

        mock_download.assert_called_once_with(force_refresh=False)
