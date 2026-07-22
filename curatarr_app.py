"""PyInstaller entry point for the curatarr standalone binary.

Packaged via `pyinstaller curatarr.spec` (see docs/BINARIES.md and
RELEASING.md). This file is intentionally thin: it just calls the same
web.app.main() that run-ui.sh / run-ui.ps1 already use for a source
install, so the binary and the source-install web UI stay identical in
behavior. All real logic lives in web/app.py - keep it that way rather
than adding anything here.

Where a packaged binary reads/writes config/cache/logs: see
utils.helpers.get_project_root() - when running frozen (this file,
built by PyInstaller), that resolves to a per-user data directory
instead of a repo checkout, since a downloaded binary has no repo
checkout to anchor to.
"""

from web.app import main

if __name__ == '__main__':
    main()
