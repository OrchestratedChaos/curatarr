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
# Two-hop push: if `origin` isn't directly reachable/credentialed from this
# machine (probed with `git ls-remote`, not guessed from the URL - two
# machines can share one .git dir, e.g. mounted over SMB, with `origin`
# genuinely pointing at github.com on both, yet only one of them holding
# working credentials for it), set:
#   CURATARR_GH_SSH_HOST     - SSH alias/host of a machine that CAN reach
#                               `origin` (a checkout of this same repo)
#   CURATARR_GH_SSH_REPO_DIR - absolute path to the curatarr checkout there
# This script probes `origin` first; if it's unreachable from here, every
# `origin`-touching read/write (the tag-existence check and the tag push)
# is delegated over SSH to that host instead, then confirms the ref actually
# landed on GitHub via a direct, credential-independent `git ls-remote`
# before declaring success. No default host is hardcoded here - both vars
# are required whenever `origin` isn't reachable from here.
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
# Lists $REMOTE's tags, either directly or - if $ORIGIN_USABLE_LOCALLY is 0 -
# delegated over SSH to a checkout of the SAME repo on CURATARR_GH_SSH_HOST
# (see the reachability probe below for why this is keyed on credentials,
# not on what URL $REMOTE happens to be).
remote_tags() {
  if [ "$ORIGIN_USABLE_LOCALLY" -eq 1 ]; then
    git ls-remote --tags "$REMOTE"
  else
    ssh "$CURATARR_GH_SSH_HOST" "cd '${CURATARR_GH_SSH_REPO_DIR}' && git ls-remote --tags origin"
  fi
}

# Pushes ref $2 (kind $1: "tags" or "heads") to $REMOTE - directly if
# $ORIGIN_USABLE_LOCALLY is 1, otherwise delegated over SSH to a checkout of
# the SAME repo on CURATARR_GH_SSH_HOST - then confirms, via a direct,
# host-independent `git ls-remote` against github.com, that the ref actually
# landed there. Fails loudly (not silently) if it never does.
push_ref_and_confirm_on_github() {
  local ref_kind="$1" ref_name="$2"

  if [ "$ORIGIN_USABLE_LOCALLY" -eq 1 ]; then
    echo "==> Pushing ${ref_name} to ${REMOTE}"
    git push "$REMOTE" "$ref_name"
  else
    echo "==> ${REMOTE} isn't directly reachable from here - pushing ${ref_name} via ${CURATARR_GH_SSH_HOST}"
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

# Content-aware: this repo's .git is shared (SMB-mounted) between machines
# with different filemode semantics, so a raw `git status --porcelain` here
# can false-positive on pure exec-bit mode deltas with zero content
# difference (e.g. Windows Git Bash misreading the bit over SMB) while the
# tree is otherwise clean. `-c core.fileMode=false` is scoped to these two
# invocations only - it is never written to .git/config, so no other git
# command anywhere in this repo (or run by anyone else against it) treats
# the mode bit any differently.
if [ -n "$(git -c core.fileMode=false status --porcelain)" ]; then
  echo "ERROR: working tree is not clean" >&2
  git -c core.fileMode=false status --short
  exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "ERROR: tag $TAG already exists locally" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Determine how to reach $REMOTE: directly, or delegated over SSH to a
# checkout of the SAME repo on CURATARR_GH_SSH_HOST. Two-hop used to be
# gated on "$REMOTE's URL isn't github.com", but that's the wrong test: two
# machines can share one .git directory (e.g. mounted over SMB) and both
# have `origin` genuinely pointing at github.com, yet only one of them has
# working credentials for it. What actually matters is whether $REMOTE is
# usable from HERE, so probe that directly instead of pattern-matching the
# URL.
# ---------------------------------------------------------------------------
echo "==> Checking whether $REMOTE is directly reachable from here"
ORIGIN_USABLE_LOCALLY=1
if ! git ls-remote --exit-code "$REMOTE" >/dev/null 2>&1; then
  ORIGIN_USABLE_LOCALLY=0
  echo "==> $REMOTE is not directly reachable from here (missing/invalid credentials) - will delegate"
  if [ -z "${CURATARR_GH_SSH_HOST:-}" ]; then
    echo "ERROR: cannot reach $REMOTE directly, and CURATARR_GH_SSH_HOST is unset. Set it to the SSH" >&2
    echo "       alias of a machine whose checkout of this repo CAN reach $REMOTE (see RELEASING.md)." >&2
    exit 1
  fi
  if [ -z "${CURATARR_GH_SSH_REPO_DIR:-}" ]; then
    echo "ERROR: cannot reach $REMOTE directly, and CURATARR_GH_SSH_REPO_DIR is unset. Set it to the" >&2
    echo "       absolute path of the curatarr checkout on \$CURATARR_GH_SSH_HOST ($CURATARR_GH_SSH_HOST)." >&2
    exit 1
  fi
  if ! ssh "$CURATARR_GH_SSH_HOST" "cd '${CURATARR_GH_SSH_REPO_DIR}' && git ls-remote --exit-code origin" >/dev/null 2>&1; then
    echo "ERROR: \$CURATARR_GH_SSH_HOST ($CURATARR_GH_SSH_HOST) can't reach its own origin either - check its credentials." >&2
    exit 1
  fi
  echo "==> Confirmed $CURATARR_GH_SSH_HOST can reach $REMOTE - delegating $REMOTE operations there"
else
  echo "==> $REMOTE is directly reachable from here - no delegation needed"
fi

echo "==> Checking $TAG doesn't already exist on $REMOTE or GitHub"
if ! REMOTE_TAGS="$(remote_tags)"; then
  echo "ERROR: could not reach $REMOTE (or its delegate on \$CURATARR_GH_SSH_HOST) to check existing tags" >&2
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

# ---------------------------------------------------------------------------
# Dry run: stop here, print the plan
# ---------------------------------------------------------------------------
if [ "$DRY_RUN" -eq 1 ]; then
  echo "==> [dry-run] All preconditions passed for ${TAG} (on $(git rev-parse --short HEAD))."
  echo "[dry-run] A real run would execute:"
  echo "  git -c user.email=\"$RELEASE_TAG_EMAIL\" tag -s \"$TAG\" -m \"$TAG\""
  echo "  git config gpg.ssh.allowedSignersFile \"$ALLOWED_SIGNERS_FILE\""
  echo "  git verify-tag \"$TAG\"   # must verify against pinned fingerprint $RELEASE_SIGNER_FINGERPRINT, else abort"
  if [ "$ORIGIN_USABLE_LOCALLY" -eq 1 ]; then
    echo "  git push \"$REMOTE\" \"$TAG\""
  else
    echo "  ssh \"$CURATARR_GH_SSH_HOST\" \"cd '$CURATARR_GH_SSH_REPO_DIR' && git push origin '$TAG'\"   # delegated - $REMOTE unreachable from here"
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
