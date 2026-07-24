#!/bin/bash
# Curatarr Docker entrypoint - dispatches the container's CMD to one of
# two modes sharing this same image (see Dockerfile, docs/DOCKER.md):
#
#   web (default)          - runs the web UI, bound to 0.0.0.0:8787 via
#                             web/docker_server.py (NOT web/app.py's own
#                             main(), which stays hardcoded to 127.0.0.1
#                             for the native desktop app - see that
#                             module's docstring).
#   recommend [engine]     - one-shot recommender run for scheduled
#                             invocations (cron/Task Scheduler on the
#                             host, or a compose `--profile schedule`
#                             service - see docker-compose.yml). engine
#                             is movie, tv, external, or full (default);
#                             extra args are passed straight through to
#                             the underlying recommender script (e.g.
#                             --debug, a specific username).
#
# Anything else is exec'd as-is, so `docker run curatarr <cmd>` (a
# shell, a one-off python invocation, etc.) still works normally.
#
# RUNNING_IN_DOCKER=true and CURATARR_CONFIG_DIR are set by the
# Dockerfile itself (not here) - they need to hold for the life of the
# container, not just this script's own duration.

set -e

RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# CURATARR_CONFIG_DIR is the app's project root (see
# utils.helpers.get_project_root) - config.yml itself lives one level
# down, at $CONFIG_DIR/config/config.yml, same as every other install
# type (source checkout, frozen binary's ~/.curatarr).
CONFIG_DIR="${CURATARR_CONFIG_DIR:-/data}"
CONFIG_YML="${CONFIG_DIR}/config/config.yml"

_require_config() {
    if [ ! -f "$CONFIG_YML" ]; then
        echo -e "${RED}ERROR: ${CONFIG_YML} not found${NC}"
        echo ""
        echo "Mount your config directory to ${CONFIG_DIR}/config with a"
        echo "config.yml inside it - see docs/DOCKER.md for setup instructions."
        exit 1
    fi

    if grep -qE "YOUR_TMDB_API_KEY|YOUR_PLEX_TOKEN|your.*api.*key.*here|your.*token.*here" "$CONFIG_YML" 2>/dev/null; then
        echo -e "${RED}ERROR: ${CONFIG_YML} contains placeholder values${NC}"
        echo ""
        echo "Edit your config.yml before running:"
        echo "  1. Add your TMDB API key (free from themoviedb.org)"
        echo "  2. Add your Plex URL and token"
        echo "  3. Add your Plex usernames"
        echo ""
        echo "See docs/DOCKER.md for configuration instructions."
        exit 1
    fi
}

MODE="${1:-web}"

case "$MODE" in
    web)
        # Config doesn't need to pre-exist for web mode: the UI's own
        # /config/connections screen creates config.yml on first save
        # (see web/config_io.py) - that's the intended first-run flow
        # for anyone who didn't already run setup.sh on the host.
        exec python3 -m web.docker_server
        ;;

    recommend)
        _require_config
        shift
        ENGINE="${1:-full}"
        [ $# -gt 0 ] && shift
        case "$ENGINE" in
            full)
                echo -e "${YELLOW}=== Movie recommendations ===${NC}"
                python3 recommenders/movie.py "$@"
                echo -e "${YELLOW}=== TV recommendations ===${NC}"
                python3 recommenders/tv.py "$@"
                echo -e "${YELLOW}=== External watchlists ===${NC}"
                exec python3 recommenders/external.py "$@"
                ;;
            movie|tv|external)
                exec python3 "recommenders/${ENGINE}.py" "$@"
                ;;
            *)
                echo -e "${RED}Unknown recommend engine: ${ENGINE}${NC} (expected movie, tv, external, or full)" >&2
                exit 2
                ;;
        esac
        ;;

    *)
        exec "$@"
        ;;
esac
