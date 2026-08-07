#!/usr/bin/env bash
# Signs a published release's aggregate SHA256SUMS.txt with the offline
# release-signing key, for the in-binary self-updater (utils/self_update.py)
# to verify before trusting any downloaded binary's hash. See that
# module's docstring and RELEASING.md's "Signing a release's checksums"
# section for the full authenticity model.
#
# Usage: ./scripts/sign-release-checksums.sh [--dry-run] <version>
#        (e.g. ./scripts/sign-release-checksums.sh 2.8.29)
#
# Run this AFTER scripts/release.sh has cut the release AND
# .github/workflows/release.yml's build-binaries / finalize-checksums
# jobs have all finished (check with
# `gh run list --workflow=release.yml` or the Actions tab) - this script
# downloads the FINAL aggregate SHA256SUMS.txt (source archive + every
# binary), not the source-archive-only one the `release` job publishes
# first.
#
# Must be run on a machine that has:
#   - the release-signing PRIVATE key, which lives ONLY here - never in
#     CI, never in this repo, and it is never copied off this machine by
#     this script. Default path: ~/.ssh/curatarr_release_signing (override
#     with CURATARR_SIGNING_KEY=/path/to/key). See RELEASING.md's
#     "One-time setup" for how that key was generated.
#
# `gh` (GitHub CLI) does the actual publish/download/upload calls, but it
# does NOT need to be authenticated on this machine: if `gh auth status`
# fails locally, this script delegates every `gh` call over SSH to
# CURATARR_GH_SSH_HOST (a machine where `gh auth status` succeeds), and
# only ever transfers the public SHA256SUMS.txt / SHA256SUMS.txt.sig files
# over that connection - the private signing key never leaves this
# machine. CURATARR_GH_SSH_HOST is required (no default hardcoded here)
# whenever local gh isn't authenticated.
#
# What it does:
#   1. Verifies prerequisites (ssh-keygen, the signing key, its
#      fingerprint, a usable `gh` - local or delegated) and that the
#      target release + its SHA256SUMS.txt asset actually exist.
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
#   6. Dispatches .github/workflows/post-release-smoke-test.yml for this
#      version - this is the FIRST moment SHA256SUMS.txt.sig actually
#      exists, which is exactly what that workflow's client-real
#      signature-verification step needs (see its own "Trigger design"
#      comment for why it isn't triggered on `release: published`
#      instead). Not fatal on its own if it fails to fire (network
#      hiccup, etc.) - steps 1-5 above already completed the thing this
#      script exists for; a failure here just prints the equivalent
#      manual `gh workflow run` command instead.
#
# --dry-run runs every prerequisite check (including the gh-delegation
# detection) and prints what it would do, without downloading, signing,
# uploading, or dispatching anything.

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
GITHUB_REPO="OrchestratedChaos/curatarr"
SUMS_FILENAME="SHA256SUMS.txt"
SIG_FILENAME="SHA256SUMS.txt.sig"

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
DRY_RUN=0
VERSION=""
for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=1
      ;;
    -*)
      echo "ERROR: unknown flag: $arg" >&2
      exit 1
      ;;
    *)
      if [ -n "$VERSION" ]; then
        echo "ERROR: unexpected extra argument: $arg" >&2
        exit 1
      fi
      VERSION="$arg"
      ;;
  esac
done

if [ -z "$VERSION" ]; then
  echo "Usage: $0 [--dry-run] <version>   (e.g. $0 2.8.29)" >&2
  exit 1
fi

TAG="v${VERSION}"

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "ERROR: version must look like X.Y.Z (got: $VERSION)" >&2
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "==> Signing checksums for ${TAG} from $(pwd)$([ "$DRY_RUN" -eq 1 ] && echo ' [dry-run]')"

# ---------------------------------------------------------------------------
# gh delegation: run `gh` locally if authenticated here, else over SSH on
# CURATARR_GH_SSH_HOST. The signing key never leaves this machine either
# way - only these wrapper functions ever call `gh`, and only to
# view/download/upload plain, public release assets.
# ---------------------------------------------------------------------------
GH_DELEGATE=0
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  echo "==> gh is authenticated locally - running gh commands directly"
else
  echo "==> gh is not authenticated locally - delegating gh calls over SSH"
  if [ -z "${CURATARR_GH_SSH_HOST:-}" ]; then
    echo "ERROR: gh is not authenticated locally (or not installed), and CURATARR_GH_SSH_HOST is" >&2
    echo "       unset. Set CURATARR_GH_SSH_HOST to the SSH alias of a machine where" >&2
    echo "       'gh auth status' succeeds." >&2
    exit 1
  fi
  if ! ssh "$CURATARR_GH_SSH_HOST" "command -v gh >/dev/null 2>&1 && gh auth status" >/dev/null 2>&1; then
    echo "ERROR: gh is not authenticated on \$CURATARR_GH_SSH_HOST ($CURATARR_GH_SSH_HOST) either." >&2
    exit 1
  fi
  GH_DELEGATE=1
  echo "==> Will delegate gh calls to $CURATARR_GH_SSH_HOST"
fi

# $1=tag - true if the release exists (view only, no output kept).
gh_release_view() {
  if [ "$GH_DELEGATE" -eq 1 ]; then
    ssh "$CURATARR_GH_SSH_HOST" "gh release view '$1' -R '$GITHUB_REPO'" >/dev/null
  else
    gh release view "$1" -R "$GITHUB_REPO" >/dev/null
  fi
}

# $1=tag $2=asset pattern $3=local destination path - downloads straight
# to $3, streaming over SSH via `-O -` when delegated so the asset never
# needs to land in an intermediate file on the delegate host.
gh_release_download_to() {
  local tag="$1" pattern="$2" dest="$3"
  if [ "$GH_DELEGATE" -eq 1 ]; then
    ssh "$CURATARR_GH_SSH_HOST" "gh release download '$tag' -p '$pattern' -O - -R '$GITHUB_REPO'" > "$dest"
  else
    gh release download "$tag" -p "$pattern" -O "$dest" -R "$GITHUB_REPO"
  fi
}

# $1=tag - prints the release's asset names, one per line.
gh_release_asset_names() {
  local tag="$1"
  if [ "$GH_DELEGATE" -eq 1 ]; then
    ssh "$CURATARR_GH_SSH_HOST" "gh release view '$tag' -R '$GITHUB_REPO' --json assets -q '.assets[].name'"
  else
    gh release view "$tag" -R "$GITHUB_REPO" --json assets -q '.assets[].name'
  fi
}

# $1=tag $2=local file path - uploads $2 to the release. When delegated,
# `gh release upload` has no stdin/stdout form (unlike download), so the
# file is scp'd to a throwaway remote temp dir, uploaded from there, and
# that temp dir is removed - only ever the already-self-verified public
# .sig file, never the private key.
gh_release_upload() {
  local tag="$1" local_file="$2" base remote_tmp
  base="$(basename "$local_file")"
  if [ "$GH_DELEGATE" -eq 1 ]; then
    remote_tmp="$(ssh "$CURATARR_GH_SSH_HOST" 'mktemp -d')"
    scp -q "$local_file" "$CURATARR_GH_SSH_HOST:$remote_tmp/$base"
    ssh "$CURATARR_GH_SSH_HOST" "gh release upload '$tag' '$remote_tmp/$base' --clobber -R '$GITHUB_REPO' && rm -rf '$remote_tmp'"
  else
    gh release upload "$tag" "$local_file" --clobber -R "$GITHUB_REPO"
  fi
}

# $1=version - fires .github/workflows/post-release-smoke-test.yml's
# workflow_dispatch now that this version's SHA256SUMS.txt.sig has just
# been uploaded (see that workflow's own "Trigger design" comment for
# why THIS moment, not `release: published`, is what it's triggered
# from: the smoke test's client-real signature check needs the .sig to
# already exist, and this is the one point in the whole release flow
# where that's guaranteed true by construction). Never fatal on its own
# - the signing above already succeeded and is the thing this script
# exists for, so a failure here (e.g. a network hiccup, or `gh workflow
# run` needing a scope the delegated/local `gh` login doesn't have)
# only prints a clear fallback instruction rather than making the whole
# script report failure.
gh_trigger_smoke_test() {
  local version="$1"
  if [ "$GH_DELEGATE" -eq 1 ]; then
    ssh "$CURATARR_GH_SSH_HOST" "gh workflow run post-release-smoke-test.yml -R '$GITHUB_REPO' -f version='$version'"
  else
    gh workflow run post-release-smoke-test.yml -R "$GITHUB_REPO" -f version="$version"
  fi
}

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
echo "==> Checking prerequisites"

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

if ! gh_release_view "$TAG"; then
  echo "ERROR: release $TAG not found - has scripts/release.sh been run for this version?" >&2
  exit 1
fi
echo "==> Release $TAG exists"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "==> [dry-run] All prerequisites passed for ${TAG}."
  echo "[dry-run] A real run would execute:"
  if [ "$GH_DELEGATE" -eq 1 ]; then
    echo "  ssh \"$CURATARR_GH_SSH_HOST\" \"gh release download '$TAG' -p '$SUMS_FILENAME' -O - -R '$GITHUB_REPO'\" > <tmp>/$SUMS_FILENAME"
  else
    echo "  gh release download \"$TAG\" -p \"$SUMS_FILENAME\" -O <tmp>/$SUMS_FILENAME -R \"$GITHUB_REPO\""
  fi
  echo "  ssh-keygen -Y sign -f \"$SIGNING_KEY\" -n file <tmp>/$SUMS_FILENAME"
  echo "  ssh-keygen -Y verify -f \"$ALLOWED_SIGNERS_FILE\" -I \"$VERIFY_PRINCIPAL\" -n file -s <tmp>/$SIG_FILENAME < <tmp>/$SUMS_FILENAME"
  if [ "$GH_DELEGATE" -eq 1 ]; then
    echo "  scp <tmp>/$SIG_FILENAME \"$CURATARR_GH_SSH_HOST:<remote-tmp>/$SIG_FILENAME\""
    echo "  ssh \"$CURATARR_GH_SSH_HOST\" \"gh release upload '$TAG' '<remote-tmp>/$SIG_FILENAME' --clobber -R '$GITHUB_REPO'\""
    echo "  ssh \"$CURATARR_GH_SSH_HOST\" \"gh workflow run post-release-smoke-test.yml -R '$GITHUB_REPO' -f version='$VERSION'\""
  else
    echo "  gh release upload \"$TAG\" <tmp>/$SIG_FILENAME --clobber -R \"$GITHUB_REPO\""
    echo "  gh workflow run post-release-smoke-test.yml -R \"$GITHUB_REPO\" -f version=\"$VERSION\""
  fi
  echo "[dry-run] Nothing was downloaded, signed, uploaded, or dispatched."
  exit 0
fi

# ---------------------------------------------------------------------------
# Download, sign, self-verify, upload
# ---------------------------------------------------------------------------
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

echo "==> Downloading ${SUMS_FILENAME} from ${TAG}"
if ! gh_release_download_to "$TAG" "$SUMS_FILENAME" "$SUMS_FILENAME"; then
  echo "ERROR: could not download ${SUMS_FILENAME} from ${TAG} - has the release.yml workflow's" >&2
  echo "       finalize-checksums job finished yet? Check: gh run list --workflow=release.yml" >&2
  exit 1
fi
echo "==> Downloaded (contents):"
cat "$SUMS_FILENAME"

# Confirm this is the FINAL aggregate, not the source-archive-only file
# the `release` job publishes first.
#
# release.yml's finalize-checksums job re-uploads SHA256SUMS.txt with
# --clobber once every binary has built. Signing before that produces a
# signature over superseded bytes: the .sig uploads fine, self-verifies
# fine (it matches what was signed), and is then silently invalid against
# the file the release actually serves - which is how v2.16.1 shipped a
# SHA256SUMS.txt.sig that no client could verify. The header tells the
# operator to wait for that job; relying on them to remember is what
# failed, so check it here instead.
echo "==> Verifying this is the final aggregate (every published binary is listed)"
MISSING=""
while IFS= read -r asset; do
  case "$asset" in
    "$SUMS_FILENAME"|"$SIG_FILENAME"|*.sha256) continue ;;
  esac
  if ! grep -qF "  ${asset}" "$SUMS_FILENAME"; then
    MISSING="${MISSING}         ${asset}\n"
  fi
done <<EOF
$(gh_release_asset_names "$TAG")
EOF

if [ -n "$MISSING" ]; then
  echo "ERROR: ${SUMS_FILENAME} does not cover every published asset - this is the" >&2
  echo "       pre-finalize file, and signing it would produce a signature that no" >&2
  echo "       client can verify against what the release serves." >&2
  echo "       Missing:" >&2
  printf "%b" "$MISSING" >&2
  echo "       Wait for release.yml's finalize-checksums job, then re-run:" >&2
  echo "         gh run list --workflow=release.yml" >&2
  exit 1
fi
echo "==> Aggregate is complete"

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
gh_release_upload "$TAG" "$WORKDIR/$SIG_FILENAME"

echo "==> Done. ${TAG}'s SHA256SUMS.txt is now signed - self-update targeting ${TAG} will verify."

echo "==> Triggering the post-release smoke test for ${VERSION}"
if gh_trigger_smoke_test "$VERSION"; then
  echo "==> Smoke test dispatched - see: gh run list --workflow=post-release-smoke-test.yml -R $GITHUB_REPO"
else
  echo "WARNING: could not automatically trigger the post-release smoke test." >&2
  echo "         Run it by hand: gh workflow run post-release-smoke-test.yml -R $GITHUB_REPO -f version=$VERSION" >&2
fi
