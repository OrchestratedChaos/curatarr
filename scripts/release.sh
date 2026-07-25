#!/usr/bin/env bash
# One-command release helper for Curatarr: tags, verifies, and publishes a
# release commit that has ALREADY landed on main via its own version-bump PR.
#
# Usage: ./scripts/release.sh [--dry-run] <version>   (e.g. ./scripts/release.sh 2.8.22)
#
# This script does NOT bump __version__, open a PR, or merge anything - main
# only ever moves via a reviewed PR, and this script never pushes to main.
# The version-bump PR is a separate, prior step (see RELEASING.md): merge it
# first, THEN run this script to tag the commit it produced.
#
# Two-hop push: if `origin` here is not github.com/OrchestratedChaos/curatarr
# (e.g. a Windows checkout whose `origin` is actually another machine that
# itself pushes on to GitHub), set:
#   CURATARR_GH_SSH_HOST     - SSH alias/host of a machine whose own `origin`
#                               IS github.com/OrchestratedChaos/curatarr
#   CURATARR_GH_SSH_REPO_DIR - absolute path to the curatarr checkout there
# This script pushes to `origin` first, then (only if `origin` isn't GitHub)
# pushes onward from that host, then confirms the ref actually landed on
# GitHub via `git ls-remote` before declaring success. No default host is
# hardcoded here - both vars are required whenever `origin` isn't GitHub.
#
# Run this from a machine that has:
#   - the release-signing key configured for git (see RELEASING.md):
#       git config user.signingkey ~/.ssh/curatarr_release_signing
#       git config gpg.format ssh
#   - network access to github.com (plain git/https - `gh` is NOT required
#     by this script; only sign-release-checksums.sh needs `gh`, and it can
#     delegate that over SSH too - see that script and RELEASING.md)
#
# What it does:
#   1. Verifies you're on a clean, up-to-date main whose __version__ already
#      equals <version> (i.e. the bump PR has merged) and whose CHANGELOG.md
#      has an entry for <version>.
#   2. Creates a signed annotated tag vX.Y.Z on that commit.
#   3. Verifies the tag locally against .github/allowed_signers before
#      pushing anything (fail closed if the signature/fingerprint is wrong).
#   4. Pushes the tag (two-hop if needed, see above) and confirms it landed
#      on GitHub, which triggers .github/workflows/release.yml.
#
# --dry-run runs every precondition and prints the exact commands a real
# run would execute, without creating a tag, pushing, signing, or uploading
# anything - use it to sanity-check the path before a real release.
#
# GH007 ("push would publish a private email address"): the tag is signed
# with user.email set to the maintainer's GitHub noreply address
# (see RELEASE_TAG_EMAIL below), which is also listed as a principal in
# .github/allowed_signers for the same signing key. This avoids GitHub's
# push-protection rejection while still verifying under the pinned key
# fingerprint.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REMOTE="origin"
MAIN_BRANCH="main"
ALLOWED_SIGNERS_FILE=".github/allowed_signers"
RELEASE_SIGNER_FINGERPRINT="SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU"
RELEASE_TAG_EMAIL="252325559+OrchestratedChaos@users.noreply.github.com"
GITHUB_REPO="OrchestratedChaos/curatarr"
GITHUB_REMOTE_URL="https://github.com/${GITHUB_REPO}.git"
CONFIG_FILE="utils/config.py"
CHANGELOG_FILE="CHANGELOG.md"

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
  echo "Usage: $0 [--dry-run] <version>   (e.g. $0 2.8.22)" >&2
  exit 1
fi

TAG="v${VERSION}"

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "ERROR: version must look like X.Y.Z (got: $VERSION)" >&2
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "==> Releasing ${TAG} from $(pwd)$([ "$DRY_RUN" -eq 1 ] && echo ' [dry-run]')"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# True if $1 is a remote name whose URL points at github.com. Covers
# git@github.com:owner/repo.git, https://github.com/owner/repo.git, and
# ssh://git@github.com/owner/repo.git forms.
remote_is_github() {
  local url
  url="$(git remote get-url "$1" 2>/dev/null)" || return 1
  case "$url" in
    *github.com*) return 0 ;;
    *) return 1 ;;
  esac
}

# Pushes ref $2 (kind $1: "tags" or "heads") to $REMOTE, pushes it onward to
# GitHub via CURATARR_GH_SSH_HOST/CURATARR_GH_SSH_REPO_DIR if $REMOTE itself
# isn't GitHub, then confirms - via a direct, host-independent `git
# ls-remote` against github.com - that the ref actually landed there.
# Fails loudly (not silently) if it never does.
push_ref_and_confirm_on_github() {
  local ref_kind="$1" ref_name="$2"

  echo "==> Pushing ${ref_name} to ${REMOTE}"
  git push "$REMOTE" "$ref_name"

  if [ "$IS_GITHUB_REMOTE" -eq 0 ]; then
    echo "==> ${REMOTE} is not GitHub - pushing ${ref_name} onward from ${CURATARR_GH_SSH_HOST}"
    ssh "$CURATARR_GH_SSH_HOST" "cd '${CURATARR_GH_SSH_REPO_DIR}' && git push origin '${ref_name}'"
  fi

  echo "==> Confirming refs/${ref_kind}/${ref_name} exists on github.com/${GITHUB_REPO}"
  local ls_output
  if ! ls_output="$(git ls-remote "$GITHUB_REMOTE_URL" "refs/${ref_kind}/${ref_name}")"; then
    echo "ERROR: could not reach ${GITHUB_REMOTE_URL} to confirm ${ref_name}" >&2
    exit 1
  fi
  if [ -z "$ls_output" ]; then
    echo "ERROR: ${ref_name} was pushed but never appeared on github.com/${GITHUB_REPO} - aborting." >&2
    exit 1
  fi
  echo "==> Confirmed: $ls_output"
}

# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------
echo "==> Checking prerequisites"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT_BRANCH" != "$MAIN_BRANCH" ]; then
  echo "ERROR: must be on '$MAIN_BRANCH' (currently on '$CURRENT_BRANCH')" >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: working tree is not clean" >&2
  git status --short
  exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "ERROR: tag $TAG already exists locally" >&2
  exit 1
fi

echo "==> Checking $TAG doesn't already exist on $REMOTE or GitHub"
if ! REMOTE_TAGS="$(git ls-remote --tags "$REMOTE")"; then
  echo "ERROR: could not reach $REMOTE to check existing tags" >&2
  exit 1
fi
if printf '%s\n' "$REMOTE_TAGS" | grep -qF "refs/tags/${TAG}"; then
  echo "ERROR: tag $TAG already exists on $REMOTE" >&2
  exit 1
fi
if ! GITHUB_TAGS="$(git ls-remote --tags "$GITHUB_REMOTE_URL")"; then
  echo "ERROR: could not reach $GITHUB_REMOTE_URL to check existing tags" >&2
  exit 1
fi
if printf '%s\n' "$GITHUB_TAGS" | grep -qF "refs/tags/${TAG}"; then
  echo "ERROR: tag $TAG already exists on github.com/${GITHUB_REPO}" >&2
  exit 1
fi

echo "==> Checking __version__ in $CONFIG_FILE matches $VERSION (bump PR must already be merged)"
CURRENT_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$CONFIG_FILE")"
if [ "$CURRENT_VERSION" != "$VERSION" ]; then
  echo "ERROR: $CONFIG_FILE has __version__ = \"${CURRENT_VERSION:-<unknown>}\", expected \"$VERSION\"." >&2
  echo "       The version-bump PR (bumping __version__ to \"$VERSION\") must be merged to" >&2
  echo "       $MAIN_BRANCH before running this script - see RELEASING.md." >&2
  exit 1
fi

echo "==> Checking $CHANGELOG_FILE has an entry for [$VERSION]"
if ! grep -qF "## [${VERSION}]" "$CHANGELOG_FILE"; then
  echo "ERROR: $CHANGELOG_FILE has no '## [$VERSION]' entry - add one (as part of the bump PR) before releasing." >&2
  exit 1
fi

echo "==> Checking local $MAIN_BRANCH matches github.com/${GITHUB_REPO}'s $MAIN_BRANCH"
LOCAL_MAIN_SHA="$(git rev-parse HEAD)"
if ! GITHUB_MAIN_SHA="$(git ls-remote "$GITHUB_REMOTE_URL" "refs/heads/$MAIN_BRANCH" | awk '{print $1}')"; then
  echo "ERROR: could not reach $GITHUB_REMOTE_URL to read $MAIN_BRANCH" >&2
  exit 1
fi
if [ -z "$GITHUB_MAIN_SHA" ]; then
  echo "ERROR: $GITHUB_REMOTE_URL returned no SHA for refs/heads/$MAIN_BRANCH" >&2
  exit 1
fi
if [ "$LOCAL_MAIN_SHA" != "$GITHUB_MAIN_SHA" ]; then
  echo "ERROR: local $MAIN_BRANCH ($LOCAL_MAIN_SHA) does not match github.com/${GITHUB_REPO}'s $MAIN_BRANCH ($GITHUB_MAIN_SHA)." >&2
  echo "       Sync local $MAIN_BRANCH (via $REMOTE, making sure $REMOTE is itself synced with GitHub first) and re-run." >&2
  exit 1
fi

IS_GITHUB_REMOTE=0
if remote_is_github "$REMOTE"; then
  IS_GITHUB_REMOTE=1
  echo "==> $REMOTE points directly at GitHub - no two-hop push needed"
else
  echo "==> $REMOTE ($(git remote get-url "$REMOTE")) is not GitHub - two-hop push required"
  if [ -z "${CURATARR_GH_SSH_HOST:-}" ]; then
    echo "ERROR: CURATARR_GH_SSH_HOST is unset. Set it to the SSH alias of a machine whose own" >&2
    echo "       'origin' remote IS github.com/${GITHUB_REPO} (see RELEASING.md)." >&2
    exit 1
  fi
  if [ -z "${CURATARR_GH_SSH_REPO_DIR:-}" ]; then
    echo "ERROR: CURATARR_GH_SSH_REPO_DIR is unset. Set it to the absolute path of the curatarr" >&2
    echo "       checkout on \$CURATARR_GH_SSH_HOST ($CURATARR_GH_SSH_HOST)." >&2
    exit 1
  fi
  echo "==> Will push onward via CURATARR_GH_SSH_HOST=$CURATARR_GH_SSH_HOST CURATARR_GH_SSH_REPO_DIR=$CURATARR_GH_SSH_REPO_DIR"
fi

# ---------------------------------------------------------------------------
# Dry run: stop here, print the plan
# ---------------------------------------------------------------------------
if [ "$DRY_RUN" -eq 1 ]; then
  echo "==> [dry-run] All preconditions passed for ${TAG} (on $(git rev-parse --short HEAD))."
  echo "[dry-run] A real run would execute:"
  echo "  git -c user.email=\"$RELEASE_TAG_EMAIL\" tag -s \"$TAG\" -m \"$TAG\""
  echo "  git config gpg.ssh.allowedSignersFile \"$ALLOWED_SIGNERS_FILE\""
  echo "  git verify-tag \"$TAG\"   # must verify against pinned fingerprint $RELEASE_SIGNER_FINGERPRINT, else abort"
  echo "  git push \"$REMOTE\" \"$TAG\""
  if [ "$IS_GITHUB_REMOTE" -eq 0 ]; then
    echo "  ssh \"$CURATARR_GH_SSH_HOST\" \"cd '$CURATARR_GH_SSH_REPO_DIR' && git push origin '$TAG'\"   # two-hop"
  fi
  echo "  git ls-remote \"$GITHUB_REMOTE_URL\" \"refs/tags/$TAG\"   # confirm it landed on GitHub"
  echo "[dry-run] No tag was created; nothing was pushed, signed, or uploaded."
  exit 0
fi

# ---------------------------------------------------------------------------
# Tag, verify, push
# ---------------------------------------------------------------------------
echo "==> Creating signed tag ${TAG} (signer email: ${RELEASE_TAG_EMAIL})"
git -c user.email="$RELEASE_TAG_EMAIL" tag -s "$TAG" -m "$TAG"

echo "==> Verifying ${TAG} locally against ${ALLOWED_SIGNERS_FILE} before pushing"
git config gpg.ssh.allowedSignersFile "$ALLOWED_SIGNERS_FILE"

# `2>&1 1>/dev/null` captures ONLY stderr (where git writes its own
# signature-status line) and discards stdout, so a tag body/message
# (which `-v` would print to stdout — we never pass `-v`) can never end
# up in VERIFY_OUTPUT regardless of git version. Same hardening as
# run.sh/run.ps1/release.yml - see RELEASING.md.
VERIFY_OUTPUT="$(git verify-tag "$TAG" 2>&1 1>/dev/null)" || {
  echo "ERROR: local verify-tag failed for $TAG - not pushing" >&2
  echo "$VERIFY_OUTPUT" >&2
  git tag -d "$TAG"
  exit 1
}
echo "$VERIFY_OUTPUT"

# Anchored to git's own "with <algo> key SHA256:..." phrase instead of a
# plain substring search, so a fingerprint injected elsewhere (e.g. a
# crafted tag message) can never be picked up in place of the
# actually-verified key.
TAG_FPR="$(printf '%s\n' "$VERIFY_OUTPUT" | grep -oE 'with [A-Za-z0-9-]+ key SHA256:[A-Za-z0-9+/=]+' | grep -oE 'SHA256:[A-Za-z0-9+/=]+' | head -1)" || true

if [ -z "$TAG_FPR" ] || [ "$TAG_FPR" != "$RELEASE_SIGNER_FINGERPRINT" ]; then
  echo "ERROR: $TAG did not verify against the pinned fingerprint ($RELEASE_SIGNER_FINGERPRINT) - not pushing" >&2
  git tag -d "$TAG"
  exit 1
fi

echo "==> Verified. Pushing ${TAG}"
push_ref_and_confirm_on_github "tags" "$TAG"

echo "==> Done. .github/workflows/release.yml will publish the GitHub Release for ${TAG}."
echo "==> Once its build-binaries / finalize-checksums jobs finish"
echo "    (watch: gh run list --workflow=release.yml - delegate over SSH via CURATARR_GH_SSH_HOST"
echo "    if gh isn't authenticated on this machine), sign the aggregate checksums on a machine"
echo "    holding the release-signing PRIVATE key:"
echo "      ./scripts/sign-release-checksums.sh ${VERSION}"
echo "    Until that runs, ${TAG}'s binaries publish fine but the in-binary self-updater"
echo "    (utils/self_update.py) can't yet treat ${TAG} as a verified self-update target."
