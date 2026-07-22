# Releasing Curatarr

The auto-updater (`run.sh` / `run.ps1`) only applies a new version if the
release's git tag is a **signed annotated tag** verified against the
maintainer's release-signing key. Unsigned tags, tags signed by any other
key, or a client that can't verify signatures all fail closed: nothing
gets applied and the client stays on its current version.

## Trust anchor

- Public key lives at `.github/allowed_signers` in this repo (principal:
  `jasonbsmith1568@gmail.com`).
- Fingerprint is additionally pinned as a literal constant inside
  `run.sh` and `run.ps1` (`RELEASE_SIGNER_FINGERPRINT` /
  `$script:ReleaseSignerFingerprint`), so a tampered `allowed_signers`
  file alone can't widen trust to a new key.
- The **private** signing key never touches the server or this repo. It
  lives only on the maintainer's machine.

## One-time setup (maintainer's machine)

Generate the release-signing keypair once, keep the private half offline:

```
ssh-keygen -t ed25519 -f ~/.ssh/curatarr_release_signing -C "curatarr-release-signing"
```

Confirm the fingerprint matches what's pinned in `.github/allowed_signers`
and in `run.sh`/`run.ps1`:

```
ssh-keygen -lf ~/.ssh/curatarr_release_signing.pub
```

Configure git to sign tags with this key using SSH-format signatures:

```
git config user.signingkey ~/.ssh/curatarr_release_signing
git config gpg.format ssh
git config user.email jasonbsmith1568@gmail.com
```

(Use `--global` if you want this to apply to all repos on the machine,
or leave it repo-local if you'd rather scope it to `curatarr` only.)

## Cutting a release

1. Bump `__version__` in `utils/config.py`.
2. Commit the version bump and push to `main` (via PR, per normal
   branch protection).
3. Tag the release commit with a **signed annotated tag** — plain
   `git tag vX.Y.Z` (lightweight) or `-a` without `-s` will NOT verify
   and will be ignored by the updater:

   ```
   git tag -s vX.Y.Z -m "vX.Y.Z"
   ```

4. Push the tag:

   ```
   git push origin vX.Y.Z
   ```

5. Sanity-check the signature yourself before announcing the release:

   ```
   git -c gpg.ssh.allowedSignersFile=.github/allowed_signers verify-tag vX.Y.Z
   ```

   Expected output: `Good "git" signature for jasonbsmith1568@gmail.com
   with ED25519 key SHA256:yrqOXw6sWZGPKON9mJJvjhsBKTgMzsn3VTGdNL5mxKU`.

Once the tag is pushed, clients with `auto_update: true` will pick it up
on their next run — but only after independently re-verifying the
signature themselves against their own local `.github/allowed_signers`.
Version numbers are monotonic: a client will never downgrade, and will
never apply a tag whose version isn't strictly greater than its current
`__version__`.

## Repo hygiene this depends on

- Branch protection on `main` (required PR review, no direct pushes).
- 2FA enforced on all maintainer GitHub accounts.
- The release-signing private key stays off any server; only the
  maintainer's own machine(s) hold it.
