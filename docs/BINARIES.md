# Standalone Binaries

Every [release](https://github.com/OrchestratedChaos/curatarr/releases)
ships a self-contained, single-file executable for each major OS, in
addition to the source archive. Download the one for your platform, run
it, and it opens the web UI in your browser - no Python install, no
`git clone`, no `pip install`.

| Platform | Asset |
|---|---|
| Windows (x86_64) | `curatarr-windows-x86_64.exe` |
| macOS (Apple Silicon) | `curatarr-macos-arm64` |
| macOS (Intel) | `curatarr-macos-x86_64` |
| Linux (x86_64) | `curatarr-linux-x86_64` |

Each asset has a matching `<asset>.sha256` file with its checksum.

## Running it

### Windows

1. Download `curatarr-windows-x86_64.exe` and put it in a folder of its
   own (see [Where data lives](#where-data-lives) below).
2. Double-click it. Windows SmartScreen will likely say "Windows
   protected your PC" because the binary isn't code-signed yet (see
   [Unsigned binaries](#unsigned-binaries)) - click **More info**, then
   **Run anyway**.
3. A console window opens (that's the server log) and your browser
   opens to `http://127.0.0.1:8787`.

### macOS

1. Download `curatarr-macos-arm64` (Apple Silicon: M1/M2/M3/M4) or
   `curatarr-macos-x86_64` (Intel), and make it executable:
   `chmod +x curatarr-macos-*`.
2. Gatekeeper will refuse to open an unsigned binary downloaded from
   the internet the normal way - see
   [Unsigned binaries](#unsigned-binaries) for the two ways around
   that.
3. Run it (double-click in Finder after clearing Gatekeeper, or
   `./curatarr-macos-arm64` in Terminal). Your browser opens to
   `http://127.0.0.1:8787`.

### Linux

1. Download `curatarr-linux-x86_64` and `chmod +x` it.
2. Run it: `./curatarr-linux-x86_64`. It opens `http://127.0.0.1:8787`
   in your default browser (via `xdg-open`/`webbrowser`); if nothing
   opens (e.g. no desktop environment), the terminal output shows the
   URL to open manually.

## Verifying the checksum

```bash
# macOS/Linux
shasum -a 256 -c curatarr-macos-arm64.sha256

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
  attribute from a terminal: `xattr -d com.apple.quarantine curatarr-macos-arm64`.
- **Linux**: no equivalent gate; just needs `chmod +x`.

Only download binaries from the official
[GitHub Releases page](https://github.com/OrchestratedChaos/curatarr/releases) -
each release is only published by CI for a tag independently verified
against the maintainer's signed-tag key (see `RELEASING.md`), and each
binary is built by that same CI run from that same verified tag.

## No auto-update

The auto-updater in `run.sh` / `run.ps1` (which checks for and applies
new signed tags) is for **source/git installs only** - it re-`git pull`s
and re-verifies a signed tag, which a standalone binary has no
equivalent of. Binaries are **manual-download, no auto-update**: to
upgrade, download the newer binary from Releases and replace the old
one. A self-updating binary (checking Releases, downloading, and
replacing itself) is a possible future item, not implemented yet.

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
pip install -r requirements.txt -r build-requirements.txt
pyinstaller --clean --noconfirm curatarr.spec
```

Produces `dist/curatarr` (`dist/curatarr.exe` on Windows). See
`curatarr.spec` for what's bundled and why, and
`.github/workflows/release.yml` for the CI matrix that does this for
every tagged release.
