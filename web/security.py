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

# Matches key=value / key: value / key="value" style occurrences.
# The key name is kept (so redaction is still informative); only the
# value is masked.
_SECRET_PATTERN = re.compile(
    r'(?i)\b(' + _KEY_ALTERNATION + r')\b\s*[:=]\s*["\']?([A-Za-z0-9._\-+/]{4,})["\']?'
)

# "Authorization: Bearer <token>" style headers.
_BEARER_PATTERN = re.compile(r'(?i)\bBearer\s+([A-Za-z0-9._\-]{8,})')

REDACTED = "***REDACTED***"


def redact(text: str) -> str:
    """Return *text* with anything that looks like a secret masked out."""
    if not text:
        return text
    text = _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    text = _BEARER_PATTERN.sub(lambda m: f"Bearer {REDACTED}", text)
    return text


def redact_lines(lines: Iterable[str]) -> List[str]:
    """Apply :func:`redact` to every line in *lines*."""
    return [redact(line) for line in lines]


def safe_join(base_dir: str, filename: str) -> str:
    """Join *filename* onto *base_dir*, refusing path traversal.

    Raises FileNotFoundError if the resolved path would escape base_dir
    (e.g. via ``..`` segments or an absolute path).
    """
    import os

    base_dir = os.path.abspath(base_dir)
    candidate = os.path.abspath(os.path.join(base_dir, filename))
    if os.path.commonpath([base_dir, candidate]) != base_dir:
        raise FileNotFoundError(filename)
    return candidate
