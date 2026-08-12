# curatarr
# Copyright (C) 2026 OrchestratedChaos
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Secret redaction - masks anything that looks like a token/key/password
before it's written or displayed anywhere.

Lives here (a neutral utils module, no web/Flask dependency) rather than
in web/security.py, where this used to live: web/job_runner.py's log
pump and utils/display.py's log_warning/log_error/TeeLogger (all under
utils/, not web/) need to redact at WRITE time too, not just when the
web UI later reads a log back - importing web.* from utils.* would be a
layering violation (the web layer depends on utils, never the other way
around). web/security.py re-exports everything from here so every
existing `from web.security import redact` (and similar) import keeps
working unchanged.

Everything logged/streamed/rendered - recommender subprocess output,
log files, the web UI's live log stream and log tails - is passed
through :func:`redact` first, since a recommender/client error message
could in principle echo a token (e.g. a stray ``X-Plex-Token`` query
parameter or an API key in a stack trace argument).
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
_SECRET_PATTERN = re.compile(r"(?i)\b(" + _KEY_ALTERNATION + r')\b\s*[:=]\s*["\']?([^\s"\']{4,})["\']?')

# "Authorization: Bearer <token>" style headers.
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._\-]{8,})")

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
    "github_pat_",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "sk-live-",
    "sk-test-",
    "sk_live_",
    "sk_test_",
    "sk-",
    "rk_live_",
    "rk_test_",
    "xoxb-",
    "xoxp-",
    "xoxa-",
    "xoxr-",
    "glpat-",
    "npm_",
    "AIza",
    "AKIA",
    "ASIA",
)
_PREFIX_ALTERNATION = "|".join(re.escape(prefix) for prefix in _KNOWN_TOKEN_PREFIXES)
_BARE_TOKEN_PATTERN = re.compile(r"\b(" + _PREFIX_ALTERNATION + r")[A-Za-z0-9_\-]{8,}")

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
