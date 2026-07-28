"""Field-level validation for the config screens.

This is a companion to, not a replacement for, utils.migrate_config -
that module validates config.yml's overall *shape* (monolithic vs.
modular layout). Nothing in the existing codebase validates individual
field values (weights summing to 1.0, well-formed URLs, etc.) before
writing them out, so each of web/config_connections.py, config_users.py,
config_libraries.py, and config_settings.py uses these helpers to reject
bad input before it ever reaches config_io.save_module.

Every validate_* function appends to a shared `errors` dict of
field_name -> message instead of raising immediately, so a form
submission can report every problem at once instead of one at a time.
"""

from typing import Dict, Optional
from urllib.parse import urlparse

from utils import WEIGHT_SUM_TOLERANCE


class ValidationError(Exception):
    """Raised with a field -> message dict when a submitted form is invalid."""

    def __init__(self, errors: Dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


def validate_url(value: str, field: str, errors: Dict[str, str], required: bool = False) -> None:
    value = (value or "").strip()
    if not value:
        if required:
            errors[field] = "URL is required"
        return
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        errors[field] = "Must be a valid http:// or https:// URL"


def validate_required(value: Optional[str], field: str, errors: Dict[str, str], label: Optional[str] = None) -> None:
    if not (value or "").strip():
        errors[field] = f"{label or field} is required"


def validate_choice(value: str, field: str, errors: Dict[str, str], choices) -> None:
    if value not in choices:
        errors[field] = f"Must be one of: {', '.join(choices)}"


def validate_media_type(value: str, field: str, errors: Dict[str, str]) -> None:
    """Thin wrapper over validate_choice for the Libraries screen's
    media_type field (#157 Phase 4) - kept separate from the generic
    helper so the ('movie', 'tv') choice set has one place to change."""
    validate_choice(value, field, errors, ("movie", "tv"))


def validate_float(
    value,
    field: str,
    errors: Dict[str, str],
    lo: Optional[float] = None,
    hi: Optional[float] = None,
    label: Optional[str] = None,
) -> Optional[float]:
    """Parse *value* as a float, recording an error and returning None on
    failure so callers can skip using an invalid value downstream."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        errors[field] = f"{label or field} must be a number"
        return None
    if lo is not None and parsed < lo:
        errors[field] = f"{label or field} must be >= {lo}"
    elif hi is not None and parsed > hi:
        errors[field] = f"{label or field} must be <= {hi}"
    return parsed


def validate_int(
    value,
    field: str,
    errors: Dict[str, str],
    lo: Optional[int] = None,
    hi: Optional[int] = None,
    label: Optional[str] = None,
) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        errors[field] = f"{label or field} must be a whole number"
        return None
    if lo is not None and parsed < lo:
        errors[field] = f"{label or field} must be >= {lo}"
    elif hi is not None and parsed > hi:
        errors[field] = f"{label or field} must be <= {hi}"
    return parsed


def validate_weights_sum(weights: Dict[str, float], field: str, errors: Dict[str, str]) -> None:
    """Mirrors recommenders/base.py's own sum-to-1.0 check (WEIGHT_SUM_TOLERANCE),
    but rejects the save outright instead of just logging a warning - a
    UI-driven typo shouldn't silently skew every recommendation."""
    total = sum(float(v) for v in weights.values())
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        errors[field] = f"Weights must sum to 1.0 (currently {total:.4f})"


def validate_collection_template(value: str, field: str, errors: Dict[str, str], media_type: str) -> None:
    """Validates a collections.movie_name_template/tv_name_template
    value (#267/#286) against the exact rules utils.labels.
    render_collection_name applies at run time: str.format() with only
    {user}/{media_type} available.

    render_collection_name() itself never raises - an invalid template
    there falls back to the matching DEFAULT_*_NAME_TEMPLATE and logs a
    warning, so a bad tuning.yml edit can never crash a recommender run.
    This validator deliberately does NOT reuse that lenient behavior:
    same as validate_weights_sum above, a UI-driven typo/malformed
    template is rejected outright here rather than silently accepted
    only to fall back at every actual run with nothing but a log line
    to explain why.
    """
    media_type_label = "Movie" if media_type == "movie" else "TV"
    try:
        value.format(user="Test User", media_type=media_type_label)
    except (KeyError, IndexError, ValueError) as e:
        errors[field] = f"Invalid template ({e}) - only {{user}} and {{media_type}} placeholders are supported"
