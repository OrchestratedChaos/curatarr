#!/usr/bin/env bash
# One-command release helper for Curatarr.
#
# Usage: ./scripts/release.sh <version>   (e.g. ./scripts/release.sh 2.8.22)
#
# Run this from a machine that has:
#   - `gh` authenticated (`gh auth status`) with repo write access
#   - the release-signing key configured for git (see RELEASING.md):
#       git config user.signingkey ~/.ssh/curatarr_release_signing
#       git config gpg.format ssh
#
# What it does:
#   1. Verifies you're on a clean, up-to-date main.
#   2. Bumps __version__ in utils/config.py.
#   3. Opens a PR with the bump, waits for the `test` check, squash-merges it.
#   4. Pulls the merged commit into main.
#   5. Creates a signed annotated tag vX.Y.Z on that commit.
#   6. Verifies the tag locally against .github/allowed_signers before
#      pushing anything (fail closed if the signature/fingerprint is wrong).
#   7. Pushes the tag, which triggers .github/workflows/release.yml.
#
# GH007 ("push would publish a private email address"): the tag is signed
# with user.email set to the maintainer's GitHub noreply address
# (see RELEASE_TAG_EMAIL below), which is also listed as a principal in
# .github/allowed_signers for the same signing key. This avoids GitHub's
# push-protection rejection while still verifying under the pinned key
# fingerprint. The version-bump commit itself uses your normal git identity.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REMOTE="origin"
MAIN_BRANCH="main"
REQUIRED_CHECK="test"
ALLOWED_SIGNERS_FILE=".github/allowed_signers"
RELEASE_SIGNER_FINGERPRINT="SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU"
RELEASE_TAG_EMAIL="252325559+OrchestratedChaos@users.noreply.github.com"

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
if [ $# -ne 1 ]; then
  echo "Usage: $0 <version>   (e.g. $0 2.8.22)" >&2
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

echo "==> Releasing ${TAG} from $(pwd)"

# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------
echo "==> Checking prerequisites"

command -v gh >/dev/null 2>&1 || { echo "ERROR: gh CLI not found" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: gh is not authenticated (run: gh auth login)" >&2; exit 1; }

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

if git ls-remote --tags "$REMOTE" | grep -qF "refs/tags/${TAG}"; then
  echo "ERROR: tag $TAG already exists on $REMOTE" >&2
  exit 1
fi

echo "==> Pulling latest $MAIN_BRANCH"
git fetch "$REMOTE" "$MAIN_BRANCH"
if [ "$(git rev-parse HEAD)" != "$(git rev-parse "$REMOTE/$MAIN_BRANCH")" ]; then
  git pull --ff-only "$REMOTE" "$MAIN_BRANCH"
fi

# ---------------------------------------------------------------------------
# Bump __version__ and open the PR
# ---------------------------------------------------------------------------
CONFIG_FILE="utils/config.py"
CURRENT_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$CONFIG_FILE")"
echo "==> Current version: ${CURRENT_VERSION:-<unknown>}, bumping to ${VERSION}"

if [ "$CURRENT_VERSION" = "$VERSION" ]; then
  echo "ERROR: $CONFIG_FILE already has __version__ = \"$VERSION\"" >&2
  exit 1
fi

BUMP_BRANCH="release/${TAG}"
git checkout -b "$BUMP_BRANCH"

# Portable in-place sed (GNU/BSD)
sed -i.bak "s/^__version__ = \".*\"/__version__ = \"${VERSION}\"/" "$CONFIG_FILE"
rm -f "${CONFIG_FILE}.bak"

if [ -z "$(git status --porcelain "$CONFIG_FILE")" ]; then
  echo "ERROR: version bump produced no change in $CONFIG_FILE" >&2
  git checkout "$MAIN_BRANCH"
  git branch -D "$BUMP_BRANCH"
  exit 1
fi

git add "$CONFIG_FILE"
git commit -m "${VERSION}"

echo "==> Pushing ${BUMP_BRANCH} and opening PR"
git push -u "$REMOTE" "$BUMP_BRANCH"

PR_URL="$(gh pr create \
  --base "$MAIN_BRANCH" \
  --head "$BUMP_BRANCH" \
  --title "${VERSION}" \
  --body "Version bump for release ${TAG}.")"
echo "==> PR: $PR_URL"
PR_NUMBER="$(basename "$PR_URL")"

echo "==> Waiting for required check '${REQUIRED_CHECK}' on PR #${PR_NUMBER}"
gh pr checks "$PR_NUMBER" --watch --fail-fast

echo "==> Squash-merging PR #${PR_NUMBER}"
gh pr merge "$PR_NUMBER" --squash --delete-branch

# ---------------------------------------------------------------------------
# Sync main, tag, verify, push
# ---------------------------------------------------------------------------
echo "==> Syncing local $MAIN_BRANCH"
git checkout "$MAIN_BRANCH"
git pull --ff-only "$REMOTE" "$MAIN_BRANCH"

MERGED_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$CONFIG_FILE")"
if [ "$MERGED_VERSION" != "$VERSION" ]; then
  echo "ERROR: after merge, $CONFIG_FILE has __version__ = \"$MERGED_VERSION\", expected \"$VERSION\"" >&2
  exit 1
fi

echo "==> Creating signed tag ${TAG} (signer email: ${RELEASE_TAG_EMAIL})"
git -c user.email="$RELEASE_TAG_EMAIL" tag -s "$TAG" -m "$TAG"

echo "==> Verifying ${TAG} locally against ${ALLOWED_SIGNERS_FILE} before pushing"
git config gpg.ssh.allowedSignersFile "$ALLOWED_SIGNERS_FILE"

VERIFY_OUTPUT="$(git verify-tag "$TAG" 2>&1)" || {
  echo "ERROR: local verify-tag failed for $TAG - not pushing" >&2
  echo "$VERIFY_OUTPUT" >&2
  git tag -d "$TAG"
  exit 1
}
echo "$VERIFY_OUTPUT"

if ! echo "$VERIFY_OUTPUT" | grep -qF "$RELEASE_SIGNER_FINGERPRINT"; then
  echo "ERROR: $TAG did not verify against the pinned fingerprint ($RELEASE_SIGNER_FINGERPRINT) - not pushing" >&2
  git tag -d "$TAG"
  exit 1
fi

echo "==> Verified. Pushing ${TAG}"
git push "$REMOTE" "$TAG"

echo "==> Done. .github/workflows/release.yml will publish the GitHub Release for ${TAG}."
