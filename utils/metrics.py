"""Local-first Prometheus-text-format metrics for Curatarr.

Deliberately NOT built on the `prometheus_client` package: this app
already had a bug where a UI-only import broke CLI-only installs (see
curatarr_app.py's module docstring), and every added runtime dependency
means regenerating four hash-pinned lockfiles. Prometheus's text
exposition format (https://prometheus.io/docs/instrumenting/exposition_formats/)
is simple enough to render directly with the stdlib, so that's what this
module does - a small, dependency-free counter/histogram registry plus a
renderer, nothing more.

Lives in utils/ (no web/Flask import, same reasoning as utils/redact.py)
so every recorder function below can be called from anywhere that might
observe a countable event - a recommender subprocess (utils/cli.py's
run_recommender_main, recommenders/external.py), an *arr/TMDB/Trakt/
Simkl/MDBList/Tautulli client (utils/api_client.py, utils/tmdb.py,
utils/trakt.py, utils/simkl.py), a cache read (utils/cache.py), a
self-update attempt (utils/self_update.py) - without any of those
needing Flask installed. web/app.py's own /metrics route is the only
thing that ever calls render_prometheus_text(); everything else in this
module just records.

Cross-process aggregation
--------------------------
Recommender runs happen in short-lived SUBPROCESSES (see
web/job_runner.py's module docstring: movie.py/tv.py/external.py hijack
sys.stdout and call sys.exit(), so they're never run in-process inside
the long-lived web server) - a cron-triggered run is a wholly separate
process again. For /metrics (served only by the long-lived web process)
to reflect any of that activity, state can't just live in this module's
own process memory. Instead it's persisted to a small JSON file under
project_root/cache/ (metrics_state.json) - the SAME file every process
reads-modifies-writes, mirroring utils/update_dismissal.py's own
project_root-relative state file. Same fail-open, best-effort contract
as that module: a read/write failure here never breaks the caller's
actual work (a recommender run, an API call, ...), it just means that
one data point is lost - never raises.

No cross-process file locking: this app runs at most one recommender
subprocess at a time (see web/job_runner.py's JobManager single-run
lock), so genuinely concurrent writers to metrics_state.json are rare in
practice. A lost update under a rare race is an acceptable tradeoff for
a personal/home-server metrics endpoint - the same tradeoff every other
JSON cache file in this codebase already makes (see utils/cache.py),
none of which lock either.

Cheap to scrape: render_prometheus_text() only ever reads this one local
file - never a network call, never a Plex/TMDB/etc. request - so /metrics
can be scraped as often as a Prometheus server likes without adding load
to anything this app talks to.
"""

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterator

from .config import __version__
from .helpers import get_project_root

logger = logging.getLogger("curatarr")

_METRICS_FILENAME = "metrics_state.json"

# In-process only - guards read-modify-write of the on-disk state
# against concurrent callers *within this same process* (e.g. two
# threads in the web server). See module docstring for why
# cross-process locking isn't attempted.
_lock = threading.Lock()

# Histogram bucket upper bounds, in seconds - shared by every duration
# metric below. Wide enough to span a sub-second API call and a
# multi-minute recommender run without per-metric tuning.
DURATION_BUCKETS = (0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800)

# name -> (HELP text, label names) - counters
_COUNTERS = {
    "curatarr_recommender_runs_total": (
        "Total recommender runs, by engine and outcome.",
        ("engine", "outcome"),
    ),
    "curatarr_api_requests_total": (
        "Total outbound API requests, by service and outcome.",
        ("service", "outcome"),
    ),
    "curatarr_cache_lookups_total": (
        "Total local cache lookups, by result.",
        ("result",),
    ),
    "curatarr_self_update_attempts_total": (
        "Total self-update attempts, by outcome.",
        ("outcome",),
    ),
    "curatarr_unhandled_errors_total": (
        "Total unhandled errors, by component.",
        ("component",),
    ),
}

# name -> (HELP text, label names) - histograms
_HISTOGRAMS = {
    "curatarr_recommender_run_duration_seconds": (
        "Recommender run duration in seconds, by engine and outcome.",
        ("engine", "outcome"),
    ),
    "curatarr_api_request_duration_seconds": (
        "Outbound API request duration in seconds, by service.",
        ("service",),
    ),
}


def _state_path() -> str:
    cache_dir = os.path.join(get_project_root(), "cache")
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except Exception as e:
        logger.debug(f"Could not create metrics cache dir: {e}")
    return os.path.join(cache_dir, _METRICS_FILENAME)


def _load_state() -> dict:
    path = _state_path()
    if not os.path.isfile(path):
        return {"counters": {}, "histograms": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"counters": {}, "histograms": {}}
        data.setdefault("counters", {})
        data.setdefault("histograms", {})
        return data
    except Exception as e:
        logger.debug(f"Could not read metrics state ({path}): {e}")
        return {"counters": {}, "histograms": {}}


def _atomic_write(path: str, data: dict) -> None:
    """Write-to-temp-then-os.replace() so a reader (render_prometheus_text,
    possibly in a different process) never sees a partially-written file -
    same pattern used throughout this codebase's own binary-swap code
    (see utils/self_update.py)."""
    tmp_path = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def _label_key(labels: Dict[str, str]) -> str:
    """Stable, internal-only string key for a label dict - never rendered
    directly (see _format_labels for the quoted Prometheus form)."""
    return ",".join(f"{k}={v}" for k, v in labels.items())


def _format_labels(key: str) -> str:
    """Render an internal `_label_key()` string as a Prometheus label
    list, e.g. 'engine=movie,outcome=success' -> 'engine="movie",
    outcome="success"'. Every label value curatarr ever records is one of
    its own fixed, enum-like strings (an engine name, an outcome, a
    service name, ...) - never free-form user input - so no
    quote/backslash escaping of the value is needed here."""
    if not key:
        return ""
    pairs = []
    for part in key.split(","):
        name, _, value = part.partition("=")
        pairs.append(f'{name}="{value}"')
    return ",".join(pairs)


def _increment_counter(name: str, labels: Dict[str, str], amount: float = 1.0) -> None:
    key = _label_key(labels)
    with _lock:
        try:
            state = _load_state()
            series = state["counters"].setdefault(name, {})
            series[key] = series.get(key, 0.0) + amount
            _atomic_write(_state_path(), state)
        except Exception as e:
            logger.debug(f"Could not persist metric {name}: {e}")


def _observe_histogram(name: str, labels: Dict[str, str], value: float) -> None:
    key = _label_key(labels)
    with _lock:
        try:
            state = _load_state()
            series = state["histograms"].setdefault(name, {})
            entry = series.setdefault(key, {"sum": 0.0, "count": 0, "buckets": {}})
            entry["sum"] += value
            entry["count"] += 1
            for bound in DURATION_BUCKETS:
                if value <= bound:
                    bucket_key = str(bound)
                    entry["buckets"][bucket_key] = entry["buckets"].get(bucket_key, 0) + 1
            _atomic_write(_state_path(), state)
        except Exception as e:
            logger.debug(f"Could not persist metric {name}: {e}")


# ---------------------------------------------------------------------------
# Public recorder API - one function per observable event. Every one of
# these is fire-and-forget: a persistence failure is logged at debug
# level (see _increment_counter/_observe_histogram) and otherwise
# swallowed, never raised - recording a metric must never be the reason a
# recommender run, API call, or cache lookup fails.
# ---------------------------------------------------------------------------


def record_recommender_run(engine: str, outcome: str, duration_seconds: float) -> None:
    """One completed recommender run. `engine` is 'movie', 'tv', or
    'external' (matches web/job_runner.py's ENGINES, minus 'full' - a
    'full' run is just those three run in sequence, each already
    recording its own entry here). `outcome` is 'success' or 'failure'.

    Called from utils.cli.run_recommender_main (movie.py/tv.py) and
    recommenders/external.py's main() directly - i.e. by whichever
    process actually ran the recommender, so a cron-triggered run is
    counted exactly the same as one triggered from the web UI's Run
    button (which just launches that same entry point as a subprocess -
    see web/job_runner.py)."""
    labels = {"engine": engine, "outcome": outcome}
    _increment_counter("curatarr_recommender_runs_total", labels)
    _observe_histogram("curatarr_recommender_run_duration_seconds", labels, duration_seconds)


def record_api_call(service: str, outcome: str, duration_seconds: float) -> None:
    """One outbound API call to `service` (plex, radarr, sonarr, tmdb,
    trakt, simkl, mdblist, or tautulli). `outcome` is 'success' or
    'error'."""
    _increment_counter("curatarr_api_requests_total", {"service": service, "outcome": outcome})
    _observe_histogram("curatarr_api_request_duration_seconds", {"service": service}, duration_seconds)


@contextmanager
def track_api_call(service: str) -> Iterator[None]:
    """Context manager form of record_api_call: times the wrapped block
    and records it against `service` on exit. outcome is 'error' if the
    block raised, 'success' otherwise - the exception (if any) still
    propagates unchanged; this only observes, it never suppresses."""
    start = time.monotonic()
    outcome = "success"
    try:
        yield
    except Exception:
        outcome = "error"
        raise
    finally:
        record_api_call(service, outcome, time.monotonic() - start)


def record_cache_lookup(result: str) -> None:
    """One local on-disk cache read. `result` is 'hit' or 'miss'."""
    _increment_counter("curatarr_cache_lookups_total", {"result": result})


def record_self_update_attempt(outcome: str) -> None:
    """One self-update attempt (CLI `--self-update` or the web UI's
    "Update now" for a frozen binary - see utils/self_update.py's
    download_and_verify_update, the single choke point both paths call
    through). `outcome` is 'success' or 'failure'."""
    _increment_counter("curatarr_self_update_attempts_total", {"outcome": outcome})


def record_unhandled_error(component: str = "unknown") -> None:
    """One unhandled exception - a recommender run crashing outside its
    own per-item error handling, or a Flask request handler raising past
    web/app.py's own error handler."""
    _increment_counter("curatarr_unhandled_errors_total", {"component": component})


# ---------------------------------------------------------------------------
# Rendering - the only thing web/app.py's /metrics route calls.
# ---------------------------------------------------------------------------


def render_prometheus_text() -> str:
    """Render every metric as Prometheus text exposition format. Cheap:
    exactly one local JSON file read (see module docstring) - never a
    network call, never a Plex/TMDB/etc. request - safe to scrape as
    often as desired."""
    state = _load_state()
    lines = [
        "# HELP curatarr_build_info Curatarr build/version info.",
        "# TYPE curatarr_build_info gauge",
        f'curatarr_build_info{{version="{__version__}"}} 1',
    ]

    counters = state.get("counters", {})
    for name, (help_text, _label_names) in _COUNTERS.items():
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        for key, value in sorted(counters.get(name, {}).items()):
            label_str = _format_labels(key)
            labels = f"{{{label_str}}}" if label_str else ""
            lines.append(f"{name}{labels} {value}")

    histograms = state.get("histograms", {})
    for name, (help_text, _label_names) in _HISTOGRAMS.items():
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} histogram")
        for key, entry in sorted(histograms.get(name, {}).items()):
            label_str = _format_labels(key)
            bucket_prefix = f"{label_str}," if label_str else ""
            base_labels = f"{{{label_str}}}" if label_str else ""
            for bound in DURATION_BUCKETS:
                bucket_count = entry.get("buckets", {}).get(str(bound), 0)
                lines.append(f'{name}_bucket{{{bucket_prefix}le="{bound}"}} {bucket_count}')
            lines.append(f'{name}_bucket{{{bucket_prefix}le="+Inf"}} {entry.get("count", 0)}')
            lines.append(f"{name}_sum{base_labels} {entry.get('sum', 0)}")
            lines.append(f"{name}_count{base_labels} {entry.get('count', 0)}")

    return "\n".join(lines) + "\n"
