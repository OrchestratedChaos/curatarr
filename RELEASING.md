# Releasing Curatarr

The auto-updater (`run.sh` / `run.ps1`) only applies a new version if the
release's git tag is a **signed annotated tag** verified against the
maintainer's release-signing key. Unsigned tags, tags signed by any other
key, or a client that can't verify signatures all fail closed: nothing
gets applied and the client stays on its current version. GitHub Releases
are cut the same way: `.github/workflows/release.yml` only publishes a
release for tags that pass the same signature + fingerprint check.

## Trust anchor

- Public key lives at `.github/allowed_signers` in this repo. It has two
  principal lines for the **same key** (same fingerprint):
  - `jasonbsmith1568@gmail.com` - the maintainer's real address, used by
    older tags (e.g. `v2.8.21`).
  - `<id>+OrchestratedChaos@users.noreply.github.com` - GitHub's noreply
    address, used by `scripts/release.sh` so `git push` of a signed tag
    doesn't get blocked by GitHub's GH007 "push would publish a private
    email address" protection. Git's SSH tag verification checks "was
    this signed by a key listed here", not which principal line matched,
    so adding a second principal for the same key doesn't widen trust.
- Fingerprint is additionally pinned as a literal constant in `run.sh`,
  `run.ps1`, and `.github/workflows/release.yml`
  (`SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU`), so a tampered
  `allowed_signers` file alone can't widen trust to a new key.
- The **private** signing key never touches the server or this repo. It
  lives only on the maintainer's machine.

## One-time setup (maintainer's machine)

Generate the release-signing keypair once, keep the private half offline:

```
ssh-keygen -t ed25519 -f ~/.ssh/curatarr_release_signing -C "curatarr-release-signing"
```

Confirm the fingerprint matches what's pinned in `.github/allowed_signers`
and in `run.sh`/`run.ps1`/`release.yml`:

```
ssh-keygen -lf ~/.ssh/curatarr_release_signing.pub
```

Configure git to sign tags with this key using SSH-format signatures:

```
git config user.signingkey ~/.ssh/curatarr_release_signing
git config gpg.format ssh
```

(`scripts/release.sh` overrides `user.email` to the noreply address only
for the tag-signing command itself - it doesn't need to be your default
`user.email`.)

Also make sure `gh auth status` is logged in with repo write access
(needed for the bump PR below; `scripts/release.sh` itself no longer
calls `gh` at all - see "Two-hop push" and "Cutting a release" below).

### Machine setup for `scripts/release.sh` / `scripts/sign-release-checksums.sh`

Two things this project's actual machine layout requires that neither
script can assume by default (no personal hostnames or paths are
hardcoded into either - both fail with a clear message instead of
guessing):

- **Two-hop push.** If the `origin` remote on the machine you run
  `scripts/release.sh` from does **not** point at
  `github.com/OrchestratedChaos/curatarr` (e.g. it points at another one
  of your own machines, which in turn has a GitHub-connected `origin`),
  set:
  ```
  export CURATARR_GH_SSH_HOST=<ssh-alias-of-the-github-connected-machine>
  export CURATARR_GH_SSH_REPO_DIR=<absolute-path-to-its-curatarr-checkout>
  ```
  `scripts/release.sh` pushes the tag to `origin` first, then - only if
  `origin` isn't GitHub - SSHes to `$CURATARR_GH_SSH_HOST`, `cd`s to
  `$CURATARR_GH_SSH_REPO_DIR`, and runs `git push origin <tag>` there,
  then confirms (via a direct `git ls-remote` against
  `https://github.com/OrchestratedChaos/curatarr.git`, independent of
  either host) that the tag actually landed before declaring success. If
  it never lands, the script fails loudly rather than silently leaving
  `.github/workflows/release.yml` un-triggered.
- **gh delegation.** `scripts/sign-release-checksums.sh` must run on the
  machine holding the signing **private** key, which isn't necessarily
  the machine where `gh` is authenticated. It checks `gh auth status`
  locally first; if that fails, it requires `CURATARR_GH_SSH_HOST` (same
  var as above) and delegates every `gh release view/download/upload`
  call to that host over SSH. Only the public `SHA256SUMS.txt` /
  `SHA256SUMS.txt.sig` files ever cross that connection - the private
  key is read only locally and never transferred.

Set both env vars in your shell profile on any machine where they apply;
neither script has (or should have) a working default for them.

## Cutting a release

The version bump and the tag are two separate steps on purpose: `main` only
ever moves via a reviewed PR (nothing pushes to `main` directly), so the
bump has to land **before** `scripts/release.sh` runs - the script's own
precondition check enforces this instead of racing it.

1. **Bump the version** (separate PR, merged like any other change): bump
   `__version__` in `utils/config.py`, add a `## [X.Y.Z] - YYYY-MM-DD`
   entry to `CHANGELOG.md`, open a PR, get the `test` check green, squash-merge
   it, and sync local `main` on whichever machine you'll run
   `scripts/release.sh` from.
2. **Dry-run it** (recommended - catches a stale local `main`, a missing
   `CURATARR_GH_SSH_HOST`/`CURATARR_GH_SSH_REPO_DIR`, a missing
   `CHANGELOG.md` entry, etc. before touching anything):
   ```
   ./scripts/release.sh --dry-run 2.8.22
   ```
   This runs every precondition and prints the exact `git tag` / `git
   verify-tag` / `git push` (and, if needed, the two-hop `ssh ... git push`)
   commands a real run would execute, without running any of them.
3. **Cut it for real:**
   ```
   ./scripts/release.sh 2.8.22
   ```

This:

1. Checks you're on a clean `main` whose `__version__` already equals
   `2.8.22` (i.e. step 1's PR has merged) and whose `CHANGELOG.md` has a
   `[2.8.22]` entry, and that local `main` matches
   `github.com/OrchestratedChaos/curatarr`'s `main` exactly.
2. Creates a **signed annotated tag** `vX.Y.Z` (`git tag -s`), signed with
   the noreply principal so the push won't hit GH007.
3. Verifies the tag locally against `.github/allowed_signers` and the
   pinned fingerprint - if that fails, it aborts and does **not** push.
4. Pushes the tag (two-hop if configured, see above) and confirms it
   actually reached GitHub before finishing.

Note: pushing a `v*` tag is itself subject to the repo's tag protection
ruleset (see "Tag protection ruleset" below) - the account/token doing the
push (locally, or on `$CURATARR_GH_SSH_HOST` in the two-hop case) needs to
be on that ruleset's bypass list (Admin role), or the push is rejected by
GitHub regardless of how correctly the script itself ran.

Pushing the tag triggers `.github/workflows/release.yml`, which:

1. Checks out full history/tags (`fetch-depth: 0`).
2. Re-verifies the tag's signature against `.github/allowed_signers` and
   asserts the pinned key fingerprint - fails the job (no release) if
   either check fails.
3. Asserts the tag version matches `__version__` in `utils/config.py`.
4. Generates release notes from `git log <prev-tag>..<tag>`.
5. Builds a versioned source archive (`git archive` tar.gz) and a
   `SHA256SUMS.txt`.
6. Publishes the GitHub Release via `gh release create` (GitHub CLI only
   - no third-party marketplace actions), attaching both files.
7. Once that job succeeds, the `build-binaries` matrix job (Windows,
   Linux x64/arm64, macOS arm64) builds a standalone PyInstaller binary
   per platform and uploads it - plus a matching `.sha256` checksum
   file - to the same release. It depends on `release` (`needs:
   release`) as its only gate: it never runs for a tag that failed the
   signature/fingerprint/version checks above, and it does not
   re-verify them independently on top - one gate, not two that could
   drift out of sync. (The macOS matrix entry additionally published an
   identical-bytes `curatarr-macos-universal` duplicate for one
   transitional release, so pre-2.10.0 installs whose self-updater still
   requested that old asset name could self-update once more - that
   duplicate-publish step has since been removed; see CHANGELOG.md and
   the job's own comment in `release.yml`.)
8. Once ALL of those finish, `finalize-checksums` downloads every
   per-binary `.sha256` plus the source-archive-only `SHA256SUMS.txt`
   from step 5, combines them into one aggregate `SHA256SUMS.txt`
   covering every published asset, and re-uploads it (`--clobber`).
   This is the file the in-binary self-updater (`utils/self_update.py`)
   actually checks a downloaded binary's hash against - see
   `docs/BINARIES.md`'s "Self-updating" section.

See `curatarr.spec` and `docs/BINARIES.md` for what's bundled, where a
binary's config/cache/logs live, and how the self-update flow itself
works (web UI "Update now" button, or `curatarr --self-update`).

## Signing a release's checksums (binary self-update trust anchor)

CI publishes `SHA256SUMS.txt` (step 8 above) but never signs it - the
release-signing **private** key stays off CI entirely, same as tag
signing. Signing `SHA256SUMS.txt` is therefore a separate, manual,
offline step, run on whichever machine actually holds
`~/.ssh/curatarr_release_signing` (this project's convention: a
Windows machine, via Git Bash), **after** `scripts/release.sh` has cut
the release and CI's `build-binaries` / `finalize-checksums` jobs have
all finished:

```
./scripts/sign-release-checksums.sh 2.8.29
```

(`--dry-run 2.8.29` first is recommended - it runs every prerequisite
check, including whether `gh` needs to be delegated over SSH, and prints
the exact commands a real run would execute without downloading, signing,
or uploading anything.)

The signing machine does not need `gh` authenticated on it - see "gh
delegation" above. If it isn't, this delegates `gh release
view/download/upload` over SSH to `$CURATARR_GH_SSH_HOST`; only the
public `SHA256SUMS.txt`/`SHA256SUMS.txt.sig` ever cross that connection,
never `~/.ssh/curatarr_release_signing` itself.

This downloads the tag's aggregate `SHA256SUMS.txt`, signs it with
`ssh-keygen -Y sign -f ~/.ssh/curatarr_release_signing -n file
SHA256SUMS.txt` (namespace `file` - matches
`utils.self_update.SIGNATURE_NAMESPACE`, what actually gets checked at
update time), **self-verifies** the resulting signature locally against
`.github/allowed_signers` and the pinned key fingerprint before uploading
anything (fail closed, same discipline as `scripts/release.sh`'s own
tag verify-before-push), then uploads `SHA256SUMS.txt.sig` to the
release.

Until this step runs for a given tag, that tag's binaries are still
published and manually downloadable/verifiable (see
`docs/BINARIES.md`'s "Verifying the checksum"), but the in-binary
self-updater can't yet treat that tag as a verified self-update
target - `utils.self_update.verify_pinned_signature()` fails closed
(no `SHA256SUMS.txt.sig` published yet = no signature to verify = no
swap), which is exactly the intended behavior, not a bug.

## Manual sanity-check

You can always independently re-verify any published tag yourself:

```
git -c gpg.ssh.allowedSignersFile=.github/allowed_signers verify-tag vX.Y.Z
```

Expected output includes: `... with ED25519 key
SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU`.

Once the tag is pushed, clients with `auto_update: true` will pick it up
on their next run - but only after independently re-verifying the
signature themselves against their own local `.github/allowed_signers`.
Version numbers are monotonic: a client will never downgrade, and will
never apply a tag whose version isn't strictly greater than its current
`__version__`.

## Repo hygiene this depends on

- Branch protection on `main` (required PR + required status checks, no direct pushes) - see "Branch protection on `main`" below for exactly what's enforced.
- 2FA enforced on all maintainer GitHub accounts.
- The release-signing private key stays off any server; only the
  maintainer's own machine(s) hold it.
- A repository ruleset restricts creating/updating/deleting `v*` tags to
  the repository Admin role - see "Tag protection ruleset" below. This
  closes the "attacker with mere push/write access crafts a `vX.Y.Z` tag
  whose tree has a tampered `release.yml`" gap: SHA-pinning the actions
  used by the workflows (below) stops that tampered tree from pulling in
  a different version of a third-party action, and the ruleset stops
  that tag from being pushable in the first place by anyone who isn't
  already a trusted maintainer.

## CI/CD supply-chain hardening

- **Actions are pinned to full commit SHAs**, not floating major tags, in
  every workflow (`release.yml`, `tests.yml`, `auto-close-prs.yml`) -
  e.g. `uses: actions/checkout@<sha> # v7`. A floating `@v7` tag can be
  moved to point at different code by the action's maintainer (or by
  whoever compromises their account); a commit SHA can't be silently
  repointed. The trailing `# vN` comment is just a human-readable label -
  bump both together when intentionally upgrading an action, by
  resolving the new tag to its commit SHA (e.g. `gh api
  repos/actions/checkout/git/refs/tags/v8 --jq .object.sha`, following
  one more level via `gh api repos/<owner>/<repo>/git/tags/<sha>` if that
  returns an annotated tag object instead of a commit).
- **Runtime Python dependencies are hash-pinned, and split core vs. UI.**
  `requirements.txt` (core: plexapi/requests/pyyaml) stays the
  human-edited `==`-pinned source of truth for the CLI/cron
  recommendation engine; `requirements.lock` is its generated, fully-hashed
  (direct + transitive, macOS/Linux/Windows) lock that `run.sh`/`run.ps1`
  install with `pip install --require-hashes`, so a compromised package
  index or a MITM'd download can't silently substitute a different build
  of a dependency during the auto-updater's install step. If that hashed
  install itself fails (hash/platform mismatch), `run.sh`/`run.ps1` fall
  back to a plain pinned install from `requirements.txt` with a warning
  rather than hard-failing the update - hashed stays the primary path.
  `requirements-ui.txt`/`requirements-ui.lock` are the same thing for the
  web UI's own deps (flask, ruamel.yaml), installed only by
  `run-ui.sh`/`run-ui.ps1` (and the binary build) - kept out of the core
  files so a plain `./run.sh` update never pulls in the UI stack. Both
  `run.sh`/`run.ps1` and `run-ui.sh`/`run-ui.ps1` also gate on the Python
  floor declared in the `--python-version X.Y` comment in the relevant
  lock file's header *before* attempting any install (and, for the
  auto-updater, *before* checking out a candidate release tag at all -
  see `check_for_updates`/`Check-ForUpdates`), so an interpreter below the
  floor gets one clear, actionable message and its working installation
  is left untouched instead of a broken half-update. Regenerate a lock
  after any change to its `requirements*.txt` - see the comment at the
  top of `requirements.lock`/`requirements-ui.lock` for the exact command
  (uses [`uv`](https://docs.astral.sh/uv/)). Build-only dependencies
  (`build-requirements.txt`, PyInstaller, CI-only) are not hash-pinned -
  they never run on an end user's machine.
- **Fingerprint parsing is anchored, not "first match anywhere".** Both
  `run.sh`/`run.ps1`'s `select_verified_release`/`Select-VerifiedRelease`
  and `release.yml`'s tag-verification step capture only what `git
  verify-tag` writes to **stderr** (its own signature-status line;
  `-v`/verbose tag-body output, which would go to stdout, is never
  requested) and then extract the fingerprint anchored to git's own
  `with <algo> key SHA256:...` phrase - not just the first `SHA256:`
  token anywhere in the captured text. This means a fingerprint that
  ended up elsewhere in that text (e.g. injected into a tag message)
  can never be picked up in place of the actually-verified signing key.

### Branch protection on `main`

Applied via the API (`gh api --method PUT
repos/OrchestratedChaos/curatarr/branches/main/protection`) as classic
branch protection - this repo's tag protection uses the newer rulesets
API instead (see "Tag protection ruleset" below), but branch protection
on `main` predates that and there's no functional reason to migrate it.
Enforces:

- **Require a pull request before merging**, required approving review
  count **0**. This is a single-maintainer repo with no second reviewer,
  so requiring approvals would permanently block every merge - the
  required status checks below are the actual gate, not a human review.
- **Required status checks**, using the exact context names a real
  `tests.yml` run reports rather than a guess: `test` and
  `secret-scan` (both jobs in `.github/workflows/tests.yml`, which runs
  on every PR targeting `main`). `strict: true` - the branch must be up
  to date with `main` before merging, which prevents two independently-
  green PRs from landing a combination that's actually broken.
- Force pushes to `main` and deletion of `main` are both blocked.
- **Admins are not enforced (`enforce_admins: false`)** - the maintainer
  can bypass this protection if genuinely necessary. This is intentional:
  there's no second maintainer who could unblock an emergency lockout, so
  admin bypass is the escape hatch, not an oversight. The required checks
  still gate the normal `gh pr merge` flow.
- **Signed commits: evaluated, not enabled.** GitHub signs its own
  squash-merge commits, so this would likely work for every commit that
  currently reaches `main` (all of them arrive via squash merge), but it
  adds lockout risk if a direct push to `main` is ever genuinely needed.
  Left off unless that tradeoff is revisited.

To recreate or inspect it by hand: repo Settings -> Branches -> Branch
protection rules -> `main`. Verify with `gh api
repos/OrchestratedChaos/curatarr/branches/main/protection`.

### Tag protection ruleset

Applied via the API (`gh api --method POST
repos/OrchestratedChaos/curatarr/rulesets`) targeting `tag` refs matching
`refs/tags/v*`, with `creation`/`update`/`deletion` rules and a
`RepositoryRole` (Admin, `actor_id: 5`) bypass so the maintainer can still
cut releases. To recreate or inspect it by hand instead:

1. Repo Settings -> Rules -> Rulesets -> New ruleset -> New tag ruleset.
2. Target: `Include by pattern` -> `refs/tags/v*`.
3. Enforcement status: Active.
4. Rules: check "Restrict creations", "Restrict updates", "Restrict
   deletions".
5. Bypass list: add the "Admin" repository role (or the specific
   maintainer account) so releases can still be cut; leave it empty and
   *nobody* - including the maintainer - could push a release tag.
6. Save. Verify with `gh api repos/OrchestratedChaos/curatarr/rulesets`.

**Follow-up option (not implemented):** move the signature/fingerprint
verification step itself into a separate reusable workflow
(`.github/workflows/verify-release-tag.yml`) called with
`uses: OrchestratedChaos/curatarr/.github/workflows/verify-release-tag.yml@main`
from `release.yml`. Because the `@main` reference always resolves to the
version of that file on the **default branch** (protected, PR-reviewed),
not whatever's in the triggering tag's own tree, this would remove even
the need to trust that a given tag's tree hasn't tampered with the
verification logic itself - on top of, not instead of, the tag ruleset
above.
