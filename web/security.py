"""Defense-in-depth secret redaction for anything the web UI renders,
plus the Host/Origin and token-auth guards protecting every route.

Recommender subprocess output (streamed live and written to logs) could
in principle echo a URL or header containing a token (e.g. a stray
``X-Plex-Token`` query parameter in an error message). Everything the UI
displays - streamed job output and log tails - is passed through
:func:`redact` first.

redact()/redact_lines()/REDACTED actually live in utils.redact now (a
neutral module with no web/Flask dependency - see that module's
docstring for why: utils/display.py and web/job_runner.py both need to
redact at WRITE time, not just when the web UI later reads a log back,
and utils/display.py importing from web.* would be a layering
violation). Re-exported here unchanged so every existing
``from web.security import redact`` (and similar) import keeps working.
"""

import hmac
import os
import re

from utils.redact import REDACTED, redact, redact_lines

__all__ = [
    "REDACTED",
    "redact",
    "redact_lines",
    "safe_join",
    "is_allowed_host",
    "register_origin_host_guard",
    "AUTH_TOKEN_ENV_VAR",
    "AUTH_TOKEN_COOKIE_NAME",
    "MIN_AUTH_TOKEN_LENGTH",
    "register_token_auth",
]


def safe_join(base_dir: str, filename: str) -> str:
    """Join *filename* onto *base_dir*, refusing path traversal.

    Raises FileNotFoundError if the resolved path would escape base_dir
    (e.g. via ``..`` segments, an absolute path, or a symlink inside
    base_dir that points back out of it). Uses realpath (not just
    abspath) specifically so a symlink can't be used to escape the
    containment check - abspath only normalizes ``..``/``.`` segments
    textually, it doesn't follow symlinks, so a symlink placed inside
    base_dir pointing outside of it would otherwise sail through.
    """
    import os

    base_dir = os.path.realpath(base_dir)
    candidate = os.path.realpath(os.path.join(base_dir, filename))
    if os.path.commonpath([base_dir, candidate]) != base_dir:
        raise FileNotFoundError(filename)
    return candidate


# Hosts the web UI's own origin is allowed to be - 127.0.0.1/localhost,
# with or without a port. app.run() only ever binds 127.0.0.1 (see
# web/app.py), so anything else in the Host header means either a
# misconfigured reverse proxy or a DNS-rebinding attempt.
_ALLOWED_HOST_RE = re.compile(r"^(127\.0\.0\.1|localhost)(:\d+)?$", re.IGNORECASE)

# Methods that mutate server state - every route using one of these is
# a save/trigger/test-connection endpoint, never a plain page view.
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# Additional exact-match hosts to allow, on top of the hardcoded
# 127.0.0.1/localhost above - opt-in via CURATARR_ALLOWED_HOSTS
# (comma-separated host[:port] entries), unset by default so the
# native app's behavior above is completely unchanged.
#
# Why this exists: web/docker_server.py (unlike web/app.py's main(),
# which is hardcoded to 127.0.0.1 and never touched by this) binds
# 0.0.0.0 inside a container so `-p 8787:8787` can reach it at all -
# but a browser on another machine reaching it via the container
# host's LAN IP or a reverse-proxy hostname sends that same value in
# its Host header, which the hardcoded allowlist above would otherwise
# always reject (by design - see is_allowed_host's docstring). This
# lets a container operator explicitly opt that host/hostname in,
# without weakening the default (unset) behavior for anyone else,
# including every native install.
#
# Read live (not cached at import) so tests can monkeypatch it and a
# running container can pick up a changed env var on restart.
def _extra_allowed_hosts() -> frozenset:
    raw = os.environ.get("CURATARR_ALLOWED_HOSTS", "")
    return frozenset(host.strip().lower() for host in raw.split(",") if host.strip())


def is_allowed_host(netloc: str) -> bool:
    """True if *netloc* (a bare ``host`` or ``host:port`` string, e.g.
    from ``request.host`` or ``urlsplit(...).netloc``) is this app's own
    origin, or is explicitly opted in via CURATARR_ALLOWED_HOSTS (see
    above - unset for every native install, so this is additive-only)."""
    if not netloc:
        return False
    if _ALLOWED_HOST_RE.match(netloc):
        return True
    return netloc.strip().lower() in _extra_allowed_hosts()


def register_origin_host_guard(app) -> None:
    """Register a before_request hook that:

    1. Rejects (400) ANY request whose Host header isn't 127.0.0.1[:port]
       or localhost[:port] - blocks DNS-rebinding attacks, where a
       victim's browser is tricked into resolving an attacker-controlled
       domain to 127.0.0.1 and then sending it real requests (the
       Origin/Referer check below can't catch this on its own, since the
       attacker page's Origin genuinely differs, but a rebinding attack
       specifically relies on the Host header looking legitimate to a
       naive server that only binds to localhost and assumes that's
       enough).
    2. Rejects (403) any state-changing request (POST/PUT/PATCH/DELETE)
       whose Origin (falling back to Referer) doesn't also resolve to
       127.0.0.1[:port]/localhost[:port] - blocks a page on any other
       origin from driving /run, /config/*, or /config/test/<service>
       via a cross-site form POST or fetch() (this app has no other
       session/auth boundary to rely on, since it's designed to run
       trusted-user-only on localhost).

    A request with neither Origin nor Referer set is rejected too - a
    real browser always sends at least one of these on a state-changing
    request; their total absence is what a simple script/curl call
    (or a non-browser attacker) looks like, not a legitimate UI
    interaction.
    """
    from urllib.parse import urlsplit

    from flask import abort, request

    @app.before_request
    def _origin_host_guard():
        if not is_allowed_host(request.host):
            abort(400)
        if request.method in STATE_CHANGING_METHODS:
            source = request.headers.get("Origin") or request.headers.get("Referer")
            if not source:
                abort(403)
            if not is_allowed_host(urlsplit(source).netloc):
                abort(403)


# ---------------------------------------------------------------------------
# Token authentication - required whenever the server isn't bound to
# loopback (see register_token_auth's docstring for the full rationale;
# short version: the Host/Origin guard above only ever proves something
# about a *browser*, since it's the browser that enforces same-origin
# policy on Host/Origin/Referer in the first place - a non-browser
# client, e.g. curl, can set all three to whatever it wants and walk
# straight through it. That's an acceptable trust model only when this
# process is bound to loopback, where "who can even reach this port" is
# already restricted to this same machine. See web/docker_server.py,
# which binds 0.0.0.0 for container use and fails closed at startup if
# this isn't configured.
# ---------------------------------------------------------------------------

# Shared secret a caller must present on every request once the server is
# bound to something other than loopback. Read live (not cached at
# import), same as CURATARR_ALLOWED_HOSTS above, so a running process
# picks up a changed value on restart and tests can monkeypatch it.
AUTH_TOKEN_ENV_VAR = "CURATARR_AUTH_TOKEN"

# Cookie name POST /login sets on success, so a browser session doesn't
# have to hand-attach an Authorization/X-Curatarr-Token header on every
# request - see register_token_auth's login routes.
AUTH_TOKEN_COOKIE_NAME = "curatarr_token"

# Floor CURATARR_AUTH_TOKEN must meet to be treated as "set" at all -
# below this, it's short enough to be brute-forceable and is treated the
# same as unset (rejects every request). web/docker_server.py enforces
# this same floor at startup, before the server even binds, so this
# request-time check should never actually be what catches a weak token
# in practice - it's the defensive backstop, not the primary gate.
MIN_AUTH_TOKEN_LENGTH = 16

# Explicit opt-out for an operator who has decided a non-loopback bind
# with NO token is acceptable - e.g. the published port is only reachable
# on a network they already fully trust (a private LAN with no other
# untrusted devices, a container network with no host port published at
# all, etc). Without this, web/docker_server.py's startup check refuses
# to start at all rather than silently running unauthenticated - this is
# the explicit, opt-in way to say "I understand the risk, start anyway."
# Never auto-detected, never defaulted to true.
TRUSTED_NETWORK_ENV_VAR = "CURATARR_TRUSTED_NETWORK"


def _trusted_network_ack() -> bool:
    """True if the operator has explicitly set CURATARR_TRUSTED_NETWORK
    to opt out of token auth on a non-loopback bind (see
    TRUSTED_NETWORK_ENV_VAR above and web/docker_server.py's
    _require_auth_token_or_exit, which is what actually gates startup on
    this - this helper is also consulted here in register_token_auth so
    the per-request guard doesn't reject with 401 forever once that
    startup check has already let the operator's explicit choice
    through)."""
    return os.environ.get(TRUSTED_NETWORK_ENV_VAR, "").strip().lower() == "true"


# Hosts this process is considered loopback-only when bound to - the
# same trust boundary web/app.py's main() has always hardcoded itself to
# (127.0.0.1 only). ::1 (IPv6 loopback) and the bare hostname
# 'localhost' are included too since a caller could plausibly set
# CURATARR_UI_HOST to either and mean the same thing.
_LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_loopback_bind(host: str) -> bool:
    """True if *host* - a server BIND address (e.g. CURATARR_UI_HOST),
    NOT a request's Host header (see is_allowed_host for that) - is
    loopback-only. Anything else (0.0.0.0, a specific LAN interface,
    ...) is reachable from other machines and needs real authentication,
    not just the Host/Origin guard above."""
    return (host or "").strip().lower() in _LOOPBACK_BIND_HOSTS


# Exempt from the token requirement even on a non-loopback bind:
# - /login itself (a browser can't already have the cookie the first
#   time it visits a token-protected instance - see register_token_auth).
# - /healthz (polled by container orchestration/liveness probes that
#   have no way to carry a token; see web/app.py's healthz() docstring -
#   it discloses nothing but a version string, same risk profile either
#   way).
_TOKEN_EXEMPT_PATHS = frozenset({"/login", "/healthz"})


def _request_token(request) -> str:
    """Pull the caller-supplied token out of *request*, checking (in
    order) the Authorization: Bearer header, the X-Curatarr-Token
    header, then the curatarr_token cookie a successful POST /login
    leaves behind - whichever a given caller finds easiest: a
    script/reverse proxy for the header forms, a browser for the
    cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer ") :].strip()
    header_token = request.headers.get("X-Curatarr-Token", "")
    if header_token:
        return header_token
    return request.cookies.get(AUTH_TOKEN_COOKIE_NAME, "")


def _valid_token(supplied: str) -> bool:
    """hmac.compare_digest against the configured CURATARR_AUTH_TOKEN -
    constant-time so a caller can't use response timing to guess the
    token one character at a time. False (never an exception, never a
    "trivially true") if the token isn't configured or nothing was
    supplied."""
    expected = os.environ.get(AUTH_TOKEN_ENV_VAR, "")
    if len(expected) < MIN_AUTH_TOKEN_LENGTH or not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def register_token_auth(app, bind_host: str) -> None:
    """Register real token authentication, required on EVERY request
    (not just state-changing ones - a GET can disclose saved config
    values, see /config/connections) whenever *bind_host* is not
    loopback-only (see _is_loopback_bind).

    This runs IN ADDITION to register_origin_host_guard's Host/Origin
    check above, never instead of it - that guard still blocks DNS
    rebinding and cross-site form submissions from a browser regardless
    of whether token auth is also active.

    When *bind_host* IS loopback (the default for every native install -
    see web/app.py's main(), always 127.0.0.1), this registers /login
    (harmless either way - the cookie it sets is simply never checked)
    but skips the before_request guard entirely, so a loopback-bound
    server's request handling is byte-for-byte unchanged from before
    this existed.

    Also skips the guard when *bind_host* is non-loopback but no valid
    token is configured AND the operator has explicitly set
    CURATARR_TRUSTED_NETWORK=true (see _trusted_network_ack) - that env
    var is what let web/docker_server.py's startup check let the process
    boot at all in this state instead of refusing to start; without this
    same check here, every single request would otherwise immediately
    401 (there being no token that could ever satisfy _valid_token), which
    would defeat the whole point of that explicit opt-out. If a valid
    token IS configured, it's always enforced regardless of this env var -
    CURATARR_TRUSTED_NETWORK only ever loosens the "no token configured"
    case, never overrides an actually-configured one.
    """
    from flask import abort, make_response, redirect, render_template, request

    @app.get("/login")
    def login_form():
        return render_template("login.html", error=request.args.get("error"))

    @app.post("/login")
    def login_submit():
        supplied = request.form.get("token", "")
        if not _valid_token(supplied):
            return redirect("/login?error=1", code=303)
        response = make_response(redirect("/", code=303))
        response.set_cookie(
            AUTH_TOKEN_COOKIE_NAME,
            supplied,
            httponly=True,
            samesite="Strict",
            path="/",
            # No secure=True: this app is served over plain HTTP on a
            # LAN by design (see docs/DOCKER.md) - Secure would make the
            # browser silently refuse to ever send the cookie back on
            # any request, with no HTTPS path to switch to instead.
        )
        return response

    if _is_loopback_bind(bind_host):
        return

    token_configured = len(os.environ.get(AUTH_TOKEN_ENV_VAR, "")) >= MIN_AUTH_TOKEN_LENGTH
    if not token_configured and _trusted_network_ack():
        return

    @app.before_request
    def _token_auth_guard():
        if request.path in _TOKEN_EXEMPT_PATHS:
            return
        if not _valid_token(_request_token(request)):
            abort(401)
