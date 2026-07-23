"""Defense-in-depth secret redaction for anything the web UI renders.

The MVP has no config/settings forms, so it never renders config values
directly. But recommender subprocess output (streamed live and written
to logs) could in principle echo a URL or header containing a token
(e.g. a stray ``X-Plex-Token`` query parameter in an error message).
Everything the UI displays - streamed job output and log tails - is
passed through :func:`redact` first.
"""

import re
from typing import Iterable, List

# Common secret-ish key names, matched case-insensitively.
_SECRET_KEY_NAMES = (
    "x-plex-token",
    "access_token",
    "refresh_token",
    "client_secret",
    "api_key",
    "apikey",
    "password",
    "token",
    "secret",
)

_KEY_ALTERNATION = "|".join(re.escape(name) for name in _SECRET_KEY_NAMES)

# Matches key=value / key: value / key="value" style occurrences. The
# value class deliberately allows any non-whitespace, non-quote
# character (not just alnum/._-+/) so a value that happens to start
# with (or contain) a special char - `token: "$ecretValue"`,
# `api_key=#deadbeef!`, a base64 value with a leading `/` or `+`, etc. -
# still gets masked instead of silently passing through because the
# old, narrower character class didn't match at that position at all.
# The key name is kept (so redaction is still informative); only the
# value is masked.
_SECRET_PATTERN = re.compile(
    r'(?i)\b(' + _KEY_ALTERNATION + r')\b\s*[:=]\s*["\']?([^\s"\']{4,})["\']?'
)

# "Authorization: Bearer <token>" style headers.
_BEARER_PATTERN = re.compile(r'(?i)\bBearer\s+([A-Za-z0-9._\-]{8,})')

# Bare high-entropy tokens that don't follow a recognizable key=value
# shape but are still unambiguously a secret by their vendor-specific
# prefix (GitHub PATs, Stripe/OpenAI-style sk- keys, Slack tokens, AWS
# access key IDs, GitLab PATs, npm tokens, Google API keys, etc.) - a
# recommender/client error message that echoes one of these raw (e.g.
# in a stack trace argument) wouldn't otherwise be caught by
# _SECRET_PATTERN since there's no "key: " / "key=" prefix at all.
# Deliberately prefix-anchored rather than a generic
# "any long mixed-case alnum run" heuristic, which would also catch
# harmless things like git commit SHAs and cache/session IDs.
_KNOWN_TOKEN_PREFIXES = (
    "github_pat_", "ghp_", "gho_", "ghu_", "ghs_", "ghr_",
    "sk-live-", "sk-test-", "sk_live_", "sk_test_", "sk-",
    "rk_live_", "rk_test_",
    "xoxb-", "xoxp-", "xoxa-", "xoxr-",
    "glpat-", "npm_", "AIza", "AKIA", "ASIA",
)
_PREFIX_ALTERNATION = "|".join(re.escape(prefix) for prefix in _KNOWN_TOKEN_PREFIXES)
_BARE_TOKEN_PATTERN = re.compile(r'\b(' + _PREFIX_ALTERNATION + r')[A-Za-z0-9_\-]{8,}')

REDACTED = "***REDACTED***"


def redact(text: str) -> str:
    """Return *text* with anything that looks like a secret masked out."""
    if not text:
        return text
    text = _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    text = _BEARER_PATTERN.sub(lambda m: f"Bearer {REDACTED}", text)
    text = _BARE_TOKEN_PATTERN.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    return text


def redact_lines(lines: Iterable[str]) -> List[str]:
    """Apply :func:`redact` to every line in *lines*."""
    return [redact(line) for line in lines]


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
_ALLOWED_HOST_RE = re.compile(r'^(127\.0\.0\.1|localhost)(:\d+)?$', re.IGNORECASE)

# Methods that mutate server state - every route using one of these is
# a save/trigger/test-connection endpoint, never a plain page view.
STATE_CHANGING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})


def is_allowed_host(netloc: str) -> bool:
    """True if *netloc* (a bare ``host`` or ``host:port`` string, e.g.
    from ``request.host`` or ``urlsplit(...).netloc``) is this app's own
    origin."""
    return bool(netloc) and bool(_ALLOWED_HOST_RE.match(netloc))


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
            source = request.headers.get('Origin') or request.headers.get('Referer')
            if not source:
                abort(403)
            if not is_allowed_host(urlsplit(source).netloc):
                abort(403)
