"""Production entrypoint for the web UI when running inside the Docker
container - see Dockerfile / docker-entrypoint.sh / docs/DOCKER.md.

Deliberately separate from web/app.py's own main() (used by
run-ui.sh/run-ui.ps1 for the native desktop app), which is hardcoded to
bind 127.0.0.1 ONLY and must stay that way - see that module's
docstring and tests/test_web_routes.py's source-level assertion that
main() never binds 0.0.0.0. This file exists specifically so that
guarantee never has to be touched or weakened to make Docker work:

A container has no "localhost on the user's own machine" concept - the
whole point of `-p 8787:8787` is to reach the UI from outside the
container's own network namespace, so this binds 0.0.0.0 instead (or
CURATARR_UI_HOST, if a caller wants to be more restrictive - e.g. bind
only a specific interface). That's an explicit, opt-in decision made
only when THIS file (not web/app.py's main()) is what's actually
running - i.e. only inside the container, via docker-entrypoint.sh's
`web` mode, never for a native install.

Reachability from another machine also needs a Host-header allowlist
entry - see web/security.py's CURATARR_ALLOWED_HOSTS (opt-in, additive,
unset by default even here) and docs/DOCKER.md for why binding 0.0.0.0
alone isn't sufficient for LAN/reverse-proxy access.
"""

import os

from .app import create_app

DEFAULT_PORT = 8787


def main() -> None:
    port = int(os.environ.get('CURATARR_UI_PORT', DEFAULT_PORT))
    host = os.environ.get('CURATARR_UI_HOST', '0.0.0.0')
    app = create_app()
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
