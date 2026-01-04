#!/bin/bash
# Docker entrypoint - validates config before running

set -e

RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if config.yml exists
if [ ! -f "/app/config.yml" ]; then
    echo -e "${RED}ERROR: config.yml not found${NC}"
    echo ""
    echo "You need to mount your config file. Example:"
    echo "  docker compose up"
    echo ""
    echo "Make sure config.yml exists in your curatarr directory."
    echo "See README.md for configuration instructions."
    exit 1
fi

# Check for placeholder values
if grep -qE "YOUR_TMDB_API_KEY|YOUR_PLEX_TOKEN|your.*api.*key.*here|your.*token.*here" /app/config.yml 2>/dev/null; then
    echo -e "${RED}ERROR: config.yml contains placeholder values${NC}"
    echo ""
    echo "Edit your config.yml before running Docker:"
    echo "  1. Add your TMDB API key (free from themoviedb.org)"
    echo "  2. Add your Plex URL and token"
    echo "  3. Add your Plex usernames"
    echo ""
    echo "See README.md for configuration instructions."
    exit 1
fi

# Check required fields exist
if ! grep -q "api_key:" /app/config.yml || ! grep -q "token:" /app/config.yml; then
    echo -e "${YELLOW}WARNING: config.yml may be incomplete${NC}"
    echo "Make sure you have configured: tmdb.api_key, plex.url, plex.token, users.list"
    echo ""
fi

# All good, run the main script
exec ./run.sh "$@"
