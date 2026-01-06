#!/bin/bash
# Curatarr Setup Wizard - Standalone config generator
# Run this BEFORE starting Docker to create your config files
#
# Usage:
#   ./setup.sh           # Interactive setup
#   docker compose up    # Run after setup

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${CYAN}===============================================${NC}"
echo -e "${CYAN}        Curatarr Setup Wizard${NC}"
echo -e "${CYAN}===============================================${NC}"
echo ""
echo "This wizard creates config files for Curatarr."
echo "Run this before 'docker compose up' or './run.sh'"
echo ""

# Check for Python (needed for OAuth flows)
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${YELLOW}Note: Python not found. OAuth authentication will be skipped.${NC}"
    echo "You can authenticate services later by editing config files."
    echo ""
    PYTHON_CMD=""
fi

# Ensure config directory exists
mkdir -p config

# --- TMDB API Key ---
echo -e "${YELLOW}Step 1: TMDB API Key${NC}"
echo ""
echo "You need a free TMDB API key for movie/show metadata."
echo -e "${CYAN}Get one here: https://www.themoviedb.org/settings/api${NC}"
echo "(Create account -> Settings -> API -> Create -> Copy 'API Key')"
echo ""
read -p "Enter your TMDB API key: " TMDB_KEY
if [ -z "$TMDB_KEY" ]; then
    echo -e "${RED}TMDB API key is required. Exiting.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Got it${NC}"
echo ""

# --- Plex URL ---
echo -e "${YELLOW}Step 2: Plex Server URL${NC}"
echo ""
echo "Your Plex server URL (usually http://IP:32400)"
echo "Example: http://192.168.1.100:32400"
echo ""
read -p "Enter your Plex URL: " PLEX_URL
if [ -z "$PLEX_URL" ]; then
    echo -e "${RED}Plex URL is required. Exiting.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Got it${NC}"
echo ""

# --- Plex Token ---
echo -e "${YELLOW}Step 3: Plex Token${NC}"
echo ""
echo "Your Plex authentication token."
echo -e "${CYAN}How to find it: https://support.plex.tv/articles/204059436${NC}"
echo "(Open any media -> Get Info -> View XML -> copy 'X-Plex-Token' from URL)"
echo ""
read -p "Enter your Plex token: " PLEX_TOKEN
if [ -z "$PLEX_TOKEN" ]; then
    echo -e "${RED}Plex token is required. Exiting.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Got it${NC}"
echo ""

# --- Users ---
echo -e "${YELLOW}Step 4: Plex Users${NC}"
echo ""
echo "Which Plex users should get recommendations?"
echo "(Comma-separated list of usernames)"
echo "Example: john, sarah, kids"
echo ""
read -p "Enter usernames: " USERS_LIST
if [ -z "$USERS_LIST" ]; then
    echo -e "${RED}At least one user is required. Exiting.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Got it${NC}"
echo ""

# --- Library Names ---
echo -e "${YELLOW}Step 5: Library Names${NC}"
echo ""
read -p "Movie library name [Movies]: " MOVIE_LIB
MOVIE_LIB=${MOVIE_LIB:-Movies}
read -p "TV library name [TV Shows]: " TV_LIB
TV_LIB=${TV_LIB:-TV Shows}
echo -e "${GREEN}✓ Got it${NC}"
echo ""

# Try to detect admin user
ADMIN_USER=""
if [ -n "$PYTHON_CMD" ]; then
    ADMIN_USER=$($PYTHON_CMD -c "
import sys
sys.path.insert(0, '.')
try:
    from plexapi.myplex import MyPlexAccount
    account = MyPlexAccount(token='$PLEX_TOKEN')
    print(account.username)
except:
    print('')
" 2>/dev/null) || true
fi

# --- Optional: Trakt Integration ---
echo -e "${YELLOW}Step 6: Trakt Integration (Optional)${NC}"
echo ""
echo "Trakt syncs your recommendations to Trakt.tv lists."
echo ""
read -p "Enable Trakt integration? (y/N): " ENABLE_TRAKT
TRAKT_ENABLED="false"

if [[ "$ENABLE_TRAKT" =~ ^[Yy]$ ]]; then
    echo ""
    echo -e "${CYAN}Creating a Trakt API application:${NC}"
    echo "1. Go to: https://trakt.tv/oauth/applications/new"
    echo "2. Name: Curatarr"
    echo "3. Redirect URI: urn:ietf:wg:oauth:2.0:oob"
    echo "4. Save and copy the Client ID and Client Secret"
    echo ""
    read -p "Enter your Trakt Client ID: " TRAKT_CLIENT_ID
    read -p "Enter your Trakt Client Secret: " TRAKT_CLIENT_SECRET

    if [ -n "$TRAKT_CLIENT_ID" ] && [ -n "$TRAKT_CLIENT_SECRET" ]; then
        TRAKT_ENABLED="true"
        TRAKT_ACCESS=""
        TRAKT_REFRESH=""
        TRAKT_AUTO_SYNC="false"
        TRAKT_USER_MODE="mapping"
        TRAKT_PLEX_USERS="[]"

        # Try OAuth if Python available
        if [ -n "$PYTHON_CMD" ]; then
            echo ""
            echo -e "${CYAN}Authenticating with Trakt...${NC}"

            TRAKT_AUTH=$($PYTHON_CMD -c "
import sys
sys.path.insert(0, '.')
from utils.trakt import TraktClient
try:
    client = TraktClient('$TRAKT_CLIENT_ID', '$TRAKT_CLIENT_SECRET')
    device_info = client.get_device_code()
    print('URL:' + device_info['verification_url'])
    print('CODE:' + device_info['user_code'])
    print('DEVICE:' + device_info['device_code'])
except Exception as e:
    print('ERROR:' + str(e))
" 2>/dev/null) || true

            TRAKT_URL=$(echo "$TRAKT_AUTH" | grep "^URL:" | cut -d: -f2-)
            TRAKT_CODE=$(echo "$TRAKT_AUTH" | grep "^CODE:" | cut -d: -f2-)
            TRAKT_DEVICE=$(echo "$TRAKT_AUTH" | grep "^DEVICE:" | cut -d: -f2-)

            if [ -n "$TRAKT_CODE" ]; then
                echo ""
                echo -e "1. Go to: ${CYAN}$TRAKT_URL${NC}"
                echo -e "2. Enter code: ${YELLOW}$TRAKT_CODE${NC}"
                echo ""
                read -p "Press Enter after you've approved on Trakt..."

                TRAKT_TOKENS=$($PYTHON_CMD -c "
import sys
sys.path.insert(0, '.')
from utils.trakt import TraktClient
client = TraktClient('$TRAKT_CLIENT_ID', '$TRAKT_CLIENT_SECRET')
if client.poll_for_token('$TRAKT_DEVICE', interval=1, expires_in=30):
    print('ACCESS:' + client.access_token)
    print('REFRESH:' + client.refresh_token)
" 2>/dev/null) || true

                TRAKT_ACCESS=$(echo "$TRAKT_TOKENS" | grep "^ACCESS:" | cut -d: -f2-)
                TRAKT_REFRESH=$(echo "$TRAKT_TOKENS" | grep "^REFRESH:" | cut -d: -f2-)

                if [ -n "$TRAKT_ACCESS" ]; then
                    echo -e "${GREEN}✓ Trakt authenticated!${NC}"
                fi
            fi
        fi

        # Export config
        echo ""
        echo "Which Plex user's recommendations should go to YOUR Trakt?"
        if [ -n "$ADMIN_USER" ]; then
            echo "  1) Just me ($ADMIN_USER) - RECOMMENDED"
        else
            echo "  1) Just me - enter username"
        fi
        echo "  2) All users"
        echo "  3) Skip"
        read -p "Choose [1/2/3]: " TRAKT_CHOICE

        case "$TRAKT_CHOICE" in
            1)
                if [ -n "$ADMIN_USER" ]; then
                    TRAKT_PLEX_USER="$ADMIN_USER"
                else
                    read -p "Enter your Plex username: " TRAKT_PLEX_USER
                fi
                TRAKT_PLEX_USERS="[\"$TRAKT_PLEX_USER\"]"
                read -p "Auto-sync on each run? (y/N): " AUTO
                [[ "$AUTO" =~ ^[Yy]$ ]] && TRAKT_AUTO_SYNC="true"
                ;;
            2)
                TRAKT_USER_MODE="per_user"
                read -p "Auto-sync on each run? (y/N): " AUTO
                [[ "$AUTO" =~ ^[Yy]$ ]] && TRAKT_AUTO_SYNC="true"
                ;;
        esac

        # Write trakt.yml
        cat > config/trakt.yml << EOF
# Curatarr Trakt Configuration

enabled: true
client_id: ${TRAKT_CLIENT_ID}
client_secret: ${TRAKT_CLIENT_SECRET}
access_token: ${TRAKT_ACCESS:-null}
refresh_token: ${TRAKT_REFRESH:-null}

export:
  enabled: true
  auto_sync: ${TRAKT_AUTO_SYNC}
  list_prefix: "Curatarr"
  user_mode: "${TRAKT_USER_MODE}"
  plex_users: ${TRAKT_PLEX_USERS}

import:
  enabled: true
  exclude_watchlist: true
EOF
        echo -e "${GREEN}✓ config/trakt.yml created${NC}"
    fi
fi
echo ""

# --- Optional: Sonarr ---
echo -e "${YELLOW}Step 7: Sonarr Integration (Optional)${NC}"
echo ""
read -p "Enable Sonarr integration? (y/N): " ENABLE_SONARR

if [[ "$ENABLE_SONARR" =~ ^[Yy]$ ]]; then
    read -p "Sonarr URL (e.g., http://localhost:8989): " SONARR_URL
    read -p "Sonarr API Key: " SONARR_API_KEY

    if [ -n "$SONARR_URL" ] && [ -n "$SONARR_API_KEY" ]; then
        read -p "Root folder path (e.g., /tv): " SONARR_ROOT
        read -p "Quality profile name (e.g., HD-1080p): " SONARR_PROFILE

        cat > config/sonarr.yml << EOF
# Curatarr Sonarr Configuration

enabled: true
url: ${SONARR_URL}
api_key: ${SONARR_API_KEY}

auto_sync: false
user_mode: "mapping"
plex_users: []

root_folder: ${SONARR_ROOT:-/tv}
quality_profile: ${SONARR_PROFILE:-HD-1080p}
series_type: standard
season_folder: true

tag: Curatarr
monitor: false
search_missing: false
EOF
        echo -e "${GREEN}✓ config/sonarr.yml created${NC}"
    fi
fi
echo ""

# --- Optional: Radarr ---
echo -e "${YELLOW}Step 8: Radarr Integration (Optional)${NC}"
echo ""
read -p "Enable Radarr integration? (y/N): " ENABLE_RADARR

if [[ "$ENABLE_RADARR" =~ ^[Yy]$ ]]; then
    read -p "Radarr URL (e.g., http://localhost:7878): " RADARR_URL
    read -p "Radarr API Key: " RADARR_API_KEY

    if [ -n "$RADARR_URL" ] && [ -n "$RADARR_API_KEY" ]; then
        read -p "Root folder path (e.g., /movies): " RADARR_ROOT
        read -p "Quality profile name (e.g., HD-1080p): " RADARR_PROFILE

        cat > config/radarr.yml << EOF
# Curatarr Radarr Configuration

enabled: true
url: ${RADARR_URL}
api_key: ${RADARR_API_KEY}

auto_sync: false
user_mode: "mapping"
plex_users: []

root_folder: ${RADARR_ROOT:-/movies}
quality_profile: ${RADARR_PROFILE:-HD-1080p}
minimum_availability: released

tag: Curatarr
monitor: false
search_for_movie: false
EOF
        echo -e "${GREEN}✓ config/radarr.yml created${NC}"
    fi
fi
echo ""

# --- Optional: MDBList ---
echo -e "${YELLOW}Step 9: MDBList Integration (Optional)${NC}"
echo ""
read -p "Enable MDBList integration? (y/N): " ENABLE_MDBLIST

if [[ "$ENABLE_MDBLIST" =~ ^[Yy]$ ]]; then
    echo "Get your API key from: https://mdblist.com/preferences/"
    read -p "MDBList API Key: " MDBLIST_API_KEY

    if [ -n "$MDBLIST_API_KEY" ]; then
        cat > config/mdblist.yml << EOF
# Curatarr MDBList Configuration

enabled: true
api_key: ${MDBLIST_API_KEY}

auto_sync: false
user_mode: "mapping"
plex_users: []

list_prefix: "Curatarr"
replace_existing: true
EOF
        echo -e "${GREEN}✓ config/mdblist.yml created${NC}"
    fi
fi
echo ""

# --- Optional: Simkl ---
echo -e "${YELLOW}Step 10: Simkl Integration (Optional)${NC}"
echo ""
read -p "Enable Simkl integration? (y/N): " ENABLE_SIMKL

if [[ "$ENABLE_SIMKL" =~ ^[Yy]$ ]]; then
    echo "Create an app at: https://simkl.com/settings/developer/"
    read -p "Simkl Client ID: " SIMKL_CLIENT_ID

    if [ -n "$SIMKL_CLIENT_ID" ]; then
        SIMKL_TOKEN=""

        if [ -n "$PYTHON_CMD" ]; then
            SIMKL_PIN=$($PYTHON_CMD -c "
import sys
sys.path.insert(0, '.')
try:
    from utils.simkl import SimklClient
    client = SimklClient('$SIMKL_CLIENT_ID')
    pin = client.get_pin_code()
    print('CODE:' + pin['user_code'])
    print('URL:' + pin['verification_url'])
except Exception as e:
    print('ERROR:' + str(e))
" 2>/dev/null) || true

            PIN_CODE=$(echo "$SIMKL_PIN" | grep "^CODE:" | cut -d: -f2)
            PIN_URL=$(echo "$SIMKL_PIN" | grep "^URL:" | cut -d: -f2-)

            if [ -n "$PIN_CODE" ]; then
                echo ""
                echo -e "1. Go to: ${CYAN}$PIN_URL${NC}"
                echo -e "2. Enter code: ${YELLOW}$PIN_CODE${NC}"
                read -p "Press Enter after authorizing..."

                SIMKL_AUTH=$($PYTHON_CMD -c "
import sys
sys.path.insert(0, '.')
from utils.simkl import SimklClient
client = SimklClient('$SIMKL_CLIENT_ID')
if client.poll_for_token('$PIN_CODE', interval=2, expires_in=30):
    print('TOKEN:' + client.access_token)
" 2>/dev/null) || true

                SIMKL_TOKEN=$(echo "$SIMKL_AUTH" | grep "^TOKEN:" | cut -d: -f2-)
                [ -n "$SIMKL_TOKEN" ] && echo -e "${GREEN}✓ Simkl authenticated!${NC}"
            fi
        fi

        cat > config/simkl.yml << EOF
# Curatarr Simkl Configuration

enabled: true
client_id: ${SIMKL_CLIENT_ID}
access_token: ${SIMKL_TOKEN:-null}

import:
  enabled: true
  include_anime: true

discovery:
  enabled: true
  anime_focus: true

export:
  enabled: true
  auto_sync: false
  user_mode: "mapping"
  plex_users: []
EOF
        echo -e "${GREEN}✓ config/simkl.yml created${NC}"
    fi
fi
echo ""

# --- Write main config.yml ---
echo -e "${CYAN}Creating config/config.yml...${NC}"

cat > config/config.yml << EOF
# Curatarr Configuration
# Generated by setup wizard

plex:
  url: $PLEX_URL
  token: $PLEX_TOKEN
  movie_library: $MOVIE_LIB
  tv_library: $TV_LIB

tmdb:
  api_key: $TMDB_KEY

users:
  list: $USERS_LIST

general:
  plex_only: true
  auto_update: false
  log_retention_days: 7
EOF

echo -e "${GREEN}✓ config/config.yml created!${NC}"
echo ""

echo -e "${GREEN}===============================================${NC}"
echo -e "${GREEN}           Setup Complete!${NC}"
echo -e "${GREEN}===============================================${NC}"
echo ""
echo "Your config files are ready in the config/ directory."
echo ""
echo "Next steps:"
echo "  Docker:  docker compose up"
echo "  Local:   ./run.sh"
echo ""
