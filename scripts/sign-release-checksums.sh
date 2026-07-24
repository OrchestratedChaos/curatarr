#!/usr/bin/env bash
# Signs a published release's aggregate SHA256SUMS.txt with the offline
# release-signing key, for the in-binary self-updater (utils/self_update.py)
# to verify before trusting any downloaded binary's hash. See that
# module's docstring and RELEASING.md's "Signing a release's checksums"
# section for the full authenticity model.
#
# Usage: ./scripts/sign-release-checksums.sh <version>   (e.g. ./scripts/sign-release-checksums.sh 2.8.29)
#
# Run this AFTER scripts/release.sh has cut the release AND
# .github/workflows/release.yml's build-binaries / build-macos-universal /
# finalize-checksums jobs have all finished (check with
# `gh run list --workflow=release.yml` or the Actions tab) - this script
# downloads the FINAL aggregate SHA256SUMS.txt (source archive + every
# binary), not the source-archive-only one the `release` job publishes
# first.
#
# Must be run on a machine that has:
#   - `gh` authenticated (`gh auth status`) with repo write access
#   - the release-signing PRIVATE key, which lives ONLY here - never in
#     CI, never in this repo. Default path: ~/.ssh/curatarr_release_signing
#     (override with CURATARR_SIGNING_KEY=/path/to/key). See
#     RELEASING.md's "One-time setup" for how that key was generated.
#
# What it does:
#   1. Verifies prerequisites (gh, ssh-keygen, the signing key, its
#      fingerprint) and that the target release + its SHA256SUMS.txt
#      asset actually exist.
#   2. Downloads that SHA256SUMS.txt.
#   3. Signs it: `ssh-keygen -Y sign -f <key> -n file SHA256SUMS.txt`
#      (namespace "file" - matches SIGNATURE_NAMESPACE in
#      utils/self_update.py, which is what actually gets verified at
#      update time).
#   4. Self-verifies the signature locally against
#      .github/allowed_signers and the pinned fingerprint BEFORE
#      uploading anything - fail closed if that doesn't pass, same
#      "never publish something that doesn't even verify against our
#      own key" discipline as scripts/release.sh's tag signing.
#   5. Uploads SHA256SUMS.txt.sig to the release.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SIGNING_KEY="${CURATARR_SIGNING_KEY:-$HOME/.ssh/curatarr_release_signing}"
ALLOWED_SIGNERS_FILE=".github/allowed_signers"
RELEASE_SIGNER_FINGERPRINT="SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU"
# Any principal line in .github/allowed_signers that points at the
# signing key works for local self-verification (see that file's own
# comment on why the two principal lines are the same key) - the real
# defense here is the fingerprint check below, not which principal
# string is used.
VERIFY_PRINCIPAL="jasonbsmith1568@gmail.com"
SUMS_FILENAME="SHA256SUMS.txt"
SIG_FILENAME="SHA256SUMS.txt.sig"

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
if [ $# -ne 1 ]; then
  echo "Usage: $0 <version>   (e.g. $0 2.8.29)" >&2
  exit 1
fi

VERSION="$1"
TAG="v${VERSION}"

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "ERROR: version must look like X.Y.Z (got: $VERSION)" >&2
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "==> Signing checksums for ${TAG} from $(pwd)"

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
echo "==> Checking prerequisites"

command -v gh >/dev/null 2>&1 || { echo "ERROR: gh CLI not found" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: gh is not authenticated (run: gh auth login)" >&2; exit 1; }
command -v ssh-keygen >/dev/null 2>&1 || { echo "ERROR: ssh-keygen not found" >&2; exit 1; }

if [ ! -f "$SIGNING_KEY" ]; then
  echo "ERROR: signing key not found at $SIGNING_KEY (override with CURATARR_SIGNING_KEY)" >&2
  exit 1
fi

if [ ! -f "$ALLOWED_SIGNERS_FILE" ]; then
  echo "ERROR: $ALLOWED_SIGNERS_FILE not found - run this from the curatarr checkout" >&2
  exit 1
fi

KEY_FPR="$(ssh-keygen -lf "$SIGNING_KEY" | awk '{print $2}')"
if [ "$KEY_FPR" != "$RELEASE_SIGNER_FINGERPRINT" ]; then
  echo "ERROR: $SIGNING_KEY does not match the pinned fingerprint" >&2
  echo "  got:      $KEY_FPR" >&2
  echo "  expected: $RELEASE_SIGNER_FINGERPRINT" >&2
  exit 1
fi
echo "==> Signing key fingerprint OK: $KEY_FPR"

if ! gh release view "$TAG" >/dev/null 2>&1; then
  echo "ERROR: release $TAG not found - has scripts/release.sh been run for this version?" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Download, sign, self-verify, upload
# ---------------------------------------------------------------------------
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

echo "==> Downloading ${SUMS_FILENAME} from ${TAG}"
if ! gh release download "$TAG" -p "$SUMS_FILENAME" -O "$SUMS_FILENAME" -R OrchestratedChaos/curatarr; then
  echo "ERROR: could not download ${SUMS_FILENAME} from ${TAG} - has the release.yml workflow's" >&2
  echo "       finalize-checksums job finished yet? Check: gh run list --workflow=release.yml" >&2
  exit 1
fi
echo "==> Downloaded (contents):"
cat "$SUMS_FILENAME"

echo "==> Signing ${SUMS_FILENAME} (namespace: file)"
ssh-keygen -Y sign -f "$SIGNING_KEY" -n file "$SUMS_FILENAME"

if [ ! -f "$SIG_FILENAME" ]; then
  echo "ERROR: ssh-keygen did not produce ${SIG_FILENAME}" >&2
  exit 1
fi

echo "==> Self-verifying the signature before publishing it"
cd "$REPO_ROOT"
if ! ssh-keygen -Y verify \
    -f "$ALLOWED_SIGNERS_FILE" \
    -I "$VERIFY_PRINCIPAL" \
    -n file \
    -s "$WORKDIR/$SIG_FILENAME" \
    < "$WORKDIR/$SUMS_FILENAME"; then
  echo "ERROR: local self-verification of ${SIG_FILENAME} failed - NOT uploading" >&2
  exit 1
fi
echo "==> Self-verification OK"

echo "==> Uploading ${SIG_FILENAME} to ${TAG}"
gh release upload "$TAG" "$WORKDIR/$SIG_FILENAME" --clobber -R OrchestratedChaos/curatarr

echo "==> Done. ${TAG}'s SHA256SUMS.txt is now signed - self-update targeting ${TAG} will verify."
