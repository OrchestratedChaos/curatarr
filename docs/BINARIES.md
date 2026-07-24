# Standalone Binaries

Every [release](https://github.com/OrchestratedChaos/curatarr/releases)
ships a self-contained, single-file executable for each major OS, in
addition to the source archive. Download the one for your platform, run
it, and it opens the web UI in your browser - no Python install, no
`git clone`, no `pip install`.

| Platform | Asset |
|---|---|
| Windows (x86_64) | `curatarr-windows-x86_64.exe` |
| macOS (Intel + Apple Silicon) | `curatarr-macos-universal` |
| Linux (x86_64) | `curatarr-linux-x86_64` |
| Linux (arm64) | `curatarr-linux-arm64` |

Each asset has a matching `<asset>.sha256` file with its checksum, and the
release also publishes an aggregate `SHA256SUMS.txt` (every asset's checksum
in one file) plus a detached signature `SHA256SUMS.txt.sig` over it - see
[Self-updating](#self-updating) below for what verifies that signature and
why.

## Running it

### Windows

1. Download `curatarr-windows-x86_64.exe` and put it in a folder of its
   own (see [Where data lives](#where-data-lives) below).
2. Double-click it. Windows SmartScreen will likely say "Windows
   protected your PC" because the binary isn't code-signed yet (see
   [Unsigned binaries](#unsigned-binaries)) - click **More info**, then
   **Run anyway**.
3. No console window opens - it launches straight into your browser at
   `http://127.0.0.1:8787`. The server log instead goes to
   `%APPDATA%\curatarr\logs\curatarr.log` (see
   [Where data lives](#where-data-lives) below). Running it from an
   existing Command Prompt/PowerShell window still prints there as
   normal, and `curatarr.exe --debug` (or setting `CURATARR_DEBUG=1`)
   opens a console too, for troubleshooting.

### macOS

1. Download `curatarr-macos-universal` and make it executable:
   `chmod +x curatarr-macos-universal`. It's a single **universal2**
   binary that runs natively on both Intel and Apple Silicon
   (M1/M2/M3/M4) Macs - no need to pick one.
2. Gatekeeper will refuse to open an unsigned binary downloaded from
   the internet the normal way - see
   [Unsigned binaries](#unsigned-binaries) for the two ways around
   that.
3. Run it (double-click in Finder after clearing Gatekeeper, or
   `./curatarr-macos-universal` in Terminal). Your browser opens to
   `http://127.0.0.1:8787`.

### Linux

1. Download `curatarr-linux-x86_64` (Intel/AMD) or `curatarr-linux-arm64`
   (arm64, e.g. Raspberry Pi 4/5, AWS Graviton) and `chmod +x` it.
2. Run it: `./curatarr-linux-x86_64` (or `./curatarr-linux-arm64`). It
   opens `http://127.0.0.1:8787` in your default browser (via
   `xdg-open`/`webbrowser`); if nothing opens (e.g. no desktop
   environment), the terminal output shows the URL to open manually.

## Verifying the checksum

```bash
# macOS/Linux
shasum -a 256 -c curatarr-macos-universal.sha256

# Windows (PowerShell)
(Get-FileHash .\curatarr-windows-x86_64.exe -Algorithm SHA256).Hash.ToLower()
# compare against the contents of curatarr-windows-x86_64.exe.sha256
```

This confirms the file wasn't corrupted or tampered with in transit -
it is **not** a substitute for code signing (see below); the checksum
file itself is just another asset on the same GitHub Release.

## Unsigned binaries

These binaries are **not code-signed**. Code-signing certificates
(a Windows EV/OV cert, an Apple Developer ID + notarization) are a
future option, not implemented yet - until then, expect your OS to
warn you the first time you run a downloaded binary:

- **Windows SmartScreen**: "Windows protected your PC" -> click
  **More info** -> **Run anyway**.
- **macOS Gatekeeper**: right-click (or Control-click) the binary in
  Finder -> **Open** -> confirm **Open** in the dialog. (A plain
  double-click on a freshly-downloaded unsigned binary will just say
  it "cannot be opened" and won't offer this option - it has to be the
  right-click path the first time.) Alternatively, clear the quarantine
  attribute from a terminal: `xattr -d com.apple.quarantine curatarr-macos-universal`.
- **Linux**: no equivalent gate; just needs `chmod +x`.

Only download binaries from the official
[GitHub Releases page](https://github.com/OrchestratedChaos/curatarr/releases) -
each release is only published by CI for a tag independently verified
against the maintainer's signed-tag key (see `RELEASING.md`), and each
binary is built by that same CI run from that same verified tag.

## Self-updating

As of v2.8.29, binaries can update themselves in place - no manual
download required. Two ways to trigger it:

- **Web UI**: the dismissible update banner's **Update now** button
  (same one-click flow source installs already had via
  `run.sh`/`run.ps1`'s signed-tag updater).
- **CLI**: `curatarr --self-update` (or `curatarr.exe --self-update` on
  Windows) - downloads, verifies, swaps, and exits; run curatarr again
  normally afterward.

Either way, the sequence is: check whether a newer version is published
(the same advisory GitHub Releases API lookup the CLI notice/web banner
already do - see below), download the platform asset plus
`SHA256SUMS.txt`/`SHA256SUMS.txt.sig`, **cryptographically verify**
before trusting anything, then atomically swap the running executable
and relaunch on the same port (web UI) or just exit (CLI).

### Authenticity model

A downloaded binary is verified for **authenticity**, not just
integrity - a checksum alone only proves a file wasn't corrupted in
transit, not that it came from the maintainer. The actual chain:

1. `SHA256SUMS.txt.sig` is a detached SSH signature (`ssh-keygen -Y
   sign`) over `SHA256SUMS.txt`, produced **offline** with the same
   release-signing private key that signs every release git tag (see
   `RELEASING.md`) - that key never touches CI or this repo.
2. The updater (`utils/self_update.py`) verifies that signature in pure
   Python (the `cryptography` package, bundled into the binary itself)
   against a public key hardcoded in the binary, before trusting
   anything else. Missing, tampered, or wrong-key signatures fail
   closed - no swap, current binary keeps running.
3. Only once that signature verifies is `SHA256SUMS.txt`'s content
   trusted as the source of truth for the downloaded asset's expected
   SHA256. The asset's actual hash is computed locally and compared;
   any mismatch also fails closed.
4. Only after both checks pass does the binary get swapped - see
   `utils/self_update.py`'s module docstring for the full chain and
   the per-OS swap mechanics (Windows: rename-while-running, since a
   running .exe can't be overwritten directly; macOS/Linux: an atomic
   `os.replace()`). Any failure at any step, including during the swap
   itself, leaves the **current** binary running - a self-updater must
   never be able to brick the install it's updating.

### Advisory version check (unchanged, still non-authenticating on its own)

The CLI prints a one-line notice after the version banner, and the web
UI shows a dismissible banner, whenever `general.update_mode` (default
`notify`) isn't set to `off`. This is the same unauthenticated,
advisory-only version lookup against the GitHub Releases API as before
(`utils/update_check.py`) - it only decides *whether a newer version
number is known*, never anything about trusting or applying it; the
authenticity model above is what actually gates the self-update itself.
Set `general.update_mode: off` in `config/config.yml` to disable the
check entirely (this also disables the "Update now" button's advisory
precondition, though `curatarr --self-update` on the CLI still performs
its own fresh check regardless of `update_mode`).

## Where data lives

A downloaded binary has no `git clone` checkout to anchor `config/`,
`cache/`, and `logs/` to, so it uses a per-user data directory instead,
created automatically on first run:

- **Windows**: `%APPDATA%\curatarr`
- **macOS/Linux**: `~/.curatarr`

This is separate from (and does not affect) a `config/`/`cache/`/`logs/`
directory belonging to a source-install checkout - the two never
collide, since a git checkout keeps using its own repo-relative paths
(see `utils.helpers.get_project_root()`). First run works with no
config at all: open the **Connections** / **Users** / **Settings**
screens in the web UI to set everything up from the browser instead of
hand-editing YAML.

## Triggering a run from the binary

The web UI's **Run** button normally launches `recommenders/movie.py`
etc. as a subprocess (see `web/job_runner.py`) using the current Python
interpreter and an on-disk script path - neither of which exists in the
expected form inside a frozen binary (`sys.executable` is the binary
itself, not a Python interpreter, and there's no `recommenders/`
directory on disk next to the data dir). Instead, when running frozen,
`web/job_runner.py` re-invokes the packaged exe itself as
`curatarr --run-recommender <engine> [user]`; `curatarr_app.py`
recognizes that flag and runs the requested recommender's own `main()`
in that fresh subprocess (never inside the long-lived server process -
see `curatarr_app.py`'s module docstring for why that distinction
matters). The Movie/TV/External/Full Pipeline buttons all work from the
binary the same as a source install.

## Building it yourself

```bash
pip install -r requirements.txt -r requirements-ui.txt -r build-requirements.txt
pyinstaller --clean --noconfirm curatarr.spec
```

The binary bundles the web UI too, so `requirements-ui.txt` (flask,
ruamel.yaml) has to be installed alongside the core deps before running
PyInstaller - see `requirements.txt`'s header for why those live in a
separate file for source installs.

Produces `dist/curatarr` (`dist/curatarr.exe` on Windows). See
`curatarr.spec` for what's bundled and why, and
`.github/workflows/release.yml` for the CI matrix that does this for
every tagged release.

### Building the macOS universal2 binary yourself

`curatarr-macos-universal` needs a **universal2** Python (Intel + Apple
Silicon in one interpreter) - the regular python.org/Homebrew installer
for your own Mac's architecture only produces a single-arch build, which
PyInstaller can't turn into a universal2 binary on its own. You also
need universal2 wheels for every compiled dependency; PyPI doesn't
publish those for `pyyaml`, `ruamel.yaml.clib`, or `markupsafe` (only
separate x86_64/arm64 wheels), so those three have to be fused into
universal2 wheels first. See the `build-macos-universal` job in
`.github/workflows/release.yml` for the exact, working recipe
(install python.org's universal2 `.pkg`, then
`delocate-merge` the three thin wheels, then
`PYINSTALLER_TARGET_ARCH=universal2 pyinstaller --clean --noconfirm curatarr.spec`).
Verify the result with `lipo -archs dist/curatarr` - it must list both
`x86_64` and `arm64`.
