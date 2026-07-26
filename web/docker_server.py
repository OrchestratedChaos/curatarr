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
alone isn't sufficient for LAN/reverse-proxy access. That allowlist is
NOT authentication, though (it only stops browsers - see
web/security.py's register_token_auth docstring), which is why
_require_auth_token_or_exit() below refuses to even start this server
against a non-loopback host without a strong CURATARR_AUTH_TOKEN
configured - real per-request auth for anything reachable from off this
machine, on top of (never instead of) the Host/Origin guard.

Also unlike web/app.py's main() (Flask's own single-threaded dev
server - fine for a single local user), this serves via waitress
(waitress.serve()) - a production-grade, multi-threaded WSGI server,
appropriate for a long-running container that may see more than one
concurrent request (e.g. the dashboard polling /healthz while a run's
live-log SSE stream is open). waitress is a Docker-only dependency -
see requirements-docker.txt/.lock and the Dockerfile - never installed
for a native source/binary install, which has no use for it.
"""

import os
import sys

import waitress

from .app import create_app
from .security import (
    AUTH_TOKEN_ENV_VAR,
    MIN_AUTH_TOKEN_LENGTH,
    TRUSTED_NETWORK_ENV_VAR,
    _is_loopback_bind,
    _trusted_network_ack,
)

DEFAULT_PORT = 8787

# Comfortably covers a dashboard poll or two alongside one open
# SSE live-log stream (see web/job_runner.py) without either starving
# the other - generous for this app's expected personal/home-server
# scale, not tuned for high concurrency.
THREADS = 8


def _require_auth_token_or_exit(host: str) -> None:
    """Fail closed at startup, before ever binding a socket: a
    non-loopback *host* (this module's whole reason for existing - see
    the module docstring) with no strong CURATARR_AUTH_TOKEN configured
    would otherwise start a server whose only protection is the
    Host/Origin guard in web/security.py, which is NOT authentication -
    it stops browsers, not a non-browser client that sets those headers
    itself (confirmed empirically - see the 2026-07 security audit this
    fixes). Deliberately does NOT auto-generate a token: a secret this
    process invents and never shows the operator isn't meaningfully
    different from no auth at all, and would change on every restart.

    Exactly one explicit escape hatch: CURATARR_TRUSTED_NETWORK=true (see
    web/security.py's TRUSTED_NETWORK_ENV_VAR) lets an operator who has
    decided the published port is genuinely unreachable by anything
    untrusted start anyway, unauthenticated - never auto-detected, never
    the default; the operator has to type it out. That path still boots,
    but prints a prominent warning on every boot so it's never silently
    running unauthenticated without the operator being reminded. See
    web/security.py's register_token_auth for the matching change that
    keeps the per-request guard from then 401-ing every single request
    once this has let the process start in that state.
    """
    if _is_loopback_bind(host):
        return
    token = os.environ.get(AUTH_TOKEN_ENV_VAR, "")
    if len(token) >= MIN_AUTH_TOKEN_LENGTH:
        return
    if _trusted_network_ack():
        print(
            "=" * 70 + "\n"
            f"curatarr: WARNING - starting UNAUTHENTICATED.\n"
            f"Bound to host '{host}' (not loopback) with no "
            f"{AUTH_TOKEN_ENV_VAR} configured. {TRUSTED_NETWORK_ENV_VAR}=true "
            f"is set, so this is intentional - but it means ANYONE who can "
            f"reach the published port can read/write this instance's "
            f"config (Plex token, Sonarr/Radarr/Trakt/etc. API keys) with "
            f"no login at all. The Host/Origin check (web/security.py) "
            f"alone is NOT authentication, it only stops browsers.\n"
            f"Only acceptable if the published port is genuinely "
            f"unreachable by anything untrusted (e.g. no host port "
            f"published, or a fully isolated network).\n" + "=" * 70,
            file=sys.stderr,
        )
        return
    print(
        f"curatarr: refusing to start - bound to host '{host}' (not "
        f"loopback) with no authentication configured.\n"
        f"Binding anywhere other than 127.0.0.1/::1/localhost means "
        f"anyone who can reach this port can reach the UI - the "
        f"Host/Origin check (web/security.py) alone is NOT "
        f"authentication, it only stops browsers.\n"
        f"Fix - one of:\n"
        f"  1. Set {AUTH_TOKEN_ENV_VAR} to a strong random value of at "
        f"least {MIN_AUTH_TOKEN_LENGTH} characters (e.g. "
        f"`openssl rand -hex 32`).\n"
        f"  2. Set {TRUSTED_NETWORK_ENV_VAR}=true if the published port "
        f"is genuinely unreachable by anything untrusted - acknowledges "
        f"starting UNAUTHENTICATED.\n"
        f"  3. Bind to loopback instead (CURATARR_UI_HOST=127.0.0.1).",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    port = int(os.environ.get("CURATARR_UI_PORT", DEFAULT_PORT))
    host = os.environ.get("CURATARR_UI_HOST", "0.0.0.0")
    _require_auth_token_or_exit(host)
    app = create_app(bind_host=host)
    waitress.serve(app, host=host, port=port, threads=THREADS)


if __name__ == "__main__":
    main()
