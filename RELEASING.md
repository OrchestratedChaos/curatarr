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

The workflow has a commented extension point for Phase 2: per-OS
PyInstaller binary builds. That is **not implemented yet** - releases
today ship a source archive + checksums only.

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
