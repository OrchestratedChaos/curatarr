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

Also make sure `gh auth status` is logged in with repo write access.

## Cutting a release (one command)

```
./scripts/release.sh 2.8.22
```

This does everything end to end:

1. Checks you're on a clean, up-to-date `main`.
2. Bumps `__version__` in `utils/config.py` on a `release/vX.Y.Z` branch.
3. Pushes the branch, opens a PR, waits for the `test` check, and
   squash-merges it.
4. Pulls the merged commit back into local `main`.
5. Creates a **signed annotated tag** `vX.Y.Z` (`git tag -s`), signed with
   the noreply principal so the push won't hit GH007.
6. Verifies the tag locally against `.github/allowed_signers` and the
   pinned fingerprint - if that fails, it aborts and does **not** push.
7. Pushes the tag.

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
   Linux x64/arm64) and the `build-macos-universal` job each build a
   standalone PyInstaller binary and upload it - plus a matching
   `.sha256` checksum file - to the same release. Both depend on
   `release` (`needs: release`) as their only gate: they never run for
   a tag that failed the signature/fingerprint/version checks above,
   and they do not re-verify them independently on top - one gate, not
   two that could drift out of sync.
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
Windows machine, via Git Bash - `ssh-keygen`/`gh` both work fine
there), **after** `scripts/release.sh` has cut the release and CI's
`build-binaries` / `build-macos-universal` / `finalize-checksums` jobs
have all finished:

```
./scripts/sign-release-checksums.sh 2.8.29
```

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

- Branch protection on `main` (required PR review, no direct pushes).
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
