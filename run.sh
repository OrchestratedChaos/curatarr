#!/bin/bash

# Curatarr - Unified Run Script
# This script handles everything: dependencies, setup, recommendations, collections, cron

set -e  # Exit on error

# Parse arguments
DEBUG_FLAG=""
for arg in "$@"; do
    case $arg in
        --debug)
            DEBUG_FLAG="--debug"
            ;;
    esac
done

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Get script directory (absolute path)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ------------------------------------------------------------------------
# DEPENDENCY CHECKING AND INSTALLATION
# ------------------------------------------------------------------------
check_and_install_dependencies() {
    echo -e "${CYAN}Checking dependencies...${NC}"
    echo ""

    # Check Python 3
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}❌ Python 3 not found${NC}"
        echo ""
        echo "Please install Python 3.8+ from:"
        echo "  - macOS: https://www.python.org/downloads/ or 'brew install python3'"
        echo "  - Linux: sudo apt install python3 python3-pip"
        echo ""
        exit 1
    fi

    PYTHON_VERSION=$(python3 --version | awk '{print $2}')
    echo -e "${GREEN}✓ Python $PYTHON_VERSION found${NC}"

    # Check pip3
    if ! command -v pip3 &> /dev/null; then
        echo -e "${YELLOW}pip3 not found, attempting to install...${NC}"
        python3 -m ensurepip --upgrade || {
            echo -e "${RED}❌ Failed to install pip3${NC}"
            echo "Please install pip3 manually:"
            echo "  - macOS: python3 -m ensurepip --upgrade"
            echo "  - Linux: sudo apt install python3-pip"
            exit 1
        }
    fi
    echo -e "${GREEN}✓ pip3 found${NC}"

    # Install/update Python requirements
    if [ -f "requirements.txt" ]; then
        echo -e "${CYAN}Installing Python dependencies...${NC}"
        pip3 install -r requirements.txt --quiet --upgrade || {
            echo -e "${RED}❌ Failed to install Python dependencies${NC}"
            echo "Try running manually: pip3 install -r requirements.txt"
            exit 1
        }
        echo -e "${GREEN}✓ All dependencies installed${NC}"
    fi

    echo ""
}

# ------------------------------------------------------------------------
# AUTO-UPDATE FROM GITHUB
# ------------------------------------------------------------------------
check_for_updates() {
    # Skip update check in Docker (users should rebuild to update)
    if [ "$RUNNING_IN_DOCKER" = "true" ]; then
        return
    fi

    # Check if auto_update is enabled in config
    if [ -f "config/config.yml" ]; then
        AUTO_UPDATE=$(python3 -c "import yaml; c=yaml.safe_load(open('config/config.yml')); print(c.get('general', {}).get('auto_update', False))" 2>/dev/null)

        if [ "$AUTO_UPDATE" = "True" ]; then
            echo -e "${CYAN}Checking for updates...${NC}"

            # Check if we're in a git repo
            if [ -d ".git" ]; then
                # Fetch latest from remote
                git fetch origin main --quiet 2>/dev/null || {
                    echo -e "${YELLOW}Could not check for updates (network error)${NC}"
                    return
                }

                # Compare local and remote
                LOCAL=$(git rev-parse HEAD 2>/dev/null)
                REMOTE=$(git rev-parse origin/main 2>/dev/null)

                if [ "$LOCAL" != "$REMOTE" ]; then
                    echo -e "${YELLOW}Update available! Pulling latest changes...${NC}"

                    # Stash any local changes
                    git stash --quiet 2>/dev/null || true

                    # Pull updates
                    if git pull origin main --quiet 2>/dev/null; then
                        echo -e "${GREEN}✓ Updated successfully!${NC}"

                        # Re-apply stashed changes if any
                        git stash pop --quiet 2>/dev/null || true

                        echo -e "${YELLOW}Restarting with updated code...${NC}"
                        echo ""
                        exec "$0" "$@"  # Restart script with same arguments
                    else
                        echo -e "${RED}Update failed, continuing with current version${NC}"
                        git stash pop --quiet 2>/dev/null || true
                    fi
                else
                    echo -e "${GREEN}✓ Already up to date${NC}"
                fi
            else
                echo -e "${YELLOW}Not a git repository, skipping update check${NC}"
            fi
            echo ""
        fi
    fi
}

# ------------------------------------------------------------------------
# FIRST RUN DETECTION
# ------------------------------------------------------------------------
is_first_run() {
    # Check if config/config.yml exists and is configured
    if [ ! -f "config/config.yml" ]; then
        return 0  # true (first run - no config)
    fi

    # Check if TMDB key is configured
    if grep -q "YOUR_TMDB_API_KEY\|your.*api.*key.*here" config/config.yml 2>/dev/null; then
        return 0  # true (first run - placeholder values)
    fi

    # Check if Plex token is configured
    if grep -q "YOUR_PLEX_TOKEN\|your.*token.*here" config/config.yml 2>/dev/null; then
        return 0  # true (first run - placeholder values)
    fi

    return 1  # false (not first run)
}

# ------------------------------------------------------------------------
# INTERACTIVE SETUP WIZARD
# ------------------------------------------------------------------------
run_setup_wizard() {
    echo -e "${CYAN}=== Curatarr Setup Wizard ===${NC}"
    echo ""
    echo "Let's get you set up! I'll walk you through the configuration."
    echo ""

    # --- TMDB API Key ---
    echo -e "${YELLOW}Step 1: TMDB API Key${NC}"
    echo ""
    echo "You need a free TMDB API key for movie/show metadata."
    echo "Get one here: ${CYAN}https://www.themoviedb.org/settings/api${NC}"
    echo "(Create account → Settings → API → Create → Copy 'API Key')"
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
    echo "How to find it: ${CYAN}https://support.plex.tv/articles/204059436${NC}"
    echo "(Open any media → Get Info → View XML → copy 'X-Plex-Token' from URL)"
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
    echo "Detecting Plex users..."

    # Auto-detect users from Plex
    PLEX_USERS_DATA=$(python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from plexapi.myplex import MyPlexAccount
    account = MyPlexAccount(token='$PLEX_TOKEN')
    # Admin first
    print(f'{account.username}|{account.title}')
    # Then managed users
    for user in account.users():
        print(f'{user.username}|{user.title}')
except Exception as e:
    print(f'ERROR|{e}')
" 2>/dev/null)

    # Check for errors
    if echo "$PLEX_USERS_DATA" | grep -q "^ERROR|"; then
        echo -e "${YELLOW}Could not auto-detect users. Enter manually.${NC}"
        echo "(Comma-separated list of usernames)"
        read -p "Enter usernames: " USERS_LIST
        if [ -z "$USERS_LIST" ]; then
            echo -e "${RED}At least one user is required. Exiting.${NC}"
            exit 1
        fi
        USER_PREFS=""
    else
        echo ""
        echo "Found these Plex users:"
        echo ""

        # Parse users into arrays
        declare -a USERNAMES
        declare -a DISPLAY_NAMES
        i=1
        while IFS='|' read -r username display; do
            USERNAMES+=("$username")
            DISPLAY_NAMES+=("$display")
            echo "  $i) $username ($display)"
            ((i++))
        done <<< "$PLEX_USERS_DATA"

        echo ""
        echo "Which users should get recommendations?"
        echo "  Enter 'all' for everyone, or comma-separated numbers (e.g., 1,2,4)"
        echo ""
        read -p "Choose: " USER_SELECTION

        # Build selected users list
        declare -a SELECTED_USERNAMES
        declare -a SELECTED_DISPLAYS
        if [ "$USER_SELECTION" = "all" ] || [ "$USER_SELECTION" = "ALL" ]; then
            SELECTED_USERNAMES=("${USERNAMES[@]}")
            SELECTED_DISPLAYS=("${DISPLAY_NAMES[@]}")
        else
            IFS=',' read -ra SELECTIONS <<< "$USER_SELECTION"
            for sel in "${SELECTIONS[@]}"; do
                sel=$(echo "$sel" | tr -d ' ')
                idx=$((sel - 1))
                if [ $idx -ge 0 ] && [ $idx -lt ${#USERNAMES[@]} ]; then
                    SELECTED_USERNAMES+=("${USERNAMES[$idx]}")
                    SELECTED_DISPLAYS+=("${DISPLAY_NAMES[$idx]}")
                fi
            done
        fi

        if [ ${#SELECTED_USERNAMES[@]} -eq 0 ]; then
            echo -e "${RED}At least one user is required. Exiting.${NC}"
            exit 1
        fi

        # Build USERS_LIST
        USERS_LIST=$(IFS=', '; echo "${SELECTED_USERNAMES[*]}")

        # Ask about display name mapping
        echo ""
        read -p "Map usernames to display names? (Recommended for collections) (Y/n): " MAP_NAMES
        USER_PREFS=""

        if [[ ! "$MAP_NAMES" =~ ^[Nn]$ ]]; then
            echo ""
            echo "Enter display name for each user (press Enter to use default):"
            echo ""
            for i in "${!SELECTED_USERNAMES[@]}"; do
                username="${SELECTED_USERNAMES[$i]}"
                default_display="${SELECTED_DISPLAYS[$i]}"
                # Extract first name as simpler default
                first_name=$(echo "$default_display" | awk '{print $1}')
                read -p "  $username [$first_name]: " custom_name
                display_name="${custom_name:-$first_name}"

                # Build YAML for user preferences
                USER_PREFS="${USER_PREFS}
    ${username}:
      display_name: ${display_name}"
            done
            echo ""
            echo -e "${GREEN}✓ Display names configured${NC}"
        fi
    fi
    echo -e "${GREEN}✓ Users configured${NC}"
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

    # --- Optional: Trakt Integration ---
    echo -e "${YELLOW}Step 6: Trakt Integration (Optional)${NC}"
    echo ""
    echo "Trakt syncs your recommendations to Trakt.tv lists"
    echo "and can exclude items already on your Trakt watchlist."
    echo ""
    read -p "Enable Trakt integration? (y/N): " ENABLE_TRAKT
    TRAKT_ENABLED="false"
    TRAKT_CLIENT_ID=""
    TRAKT_CLIENT_SECRET=""

    if [[ "$ENABLE_TRAKT" =~ ^[Yy]$ ]]; then
        echo ""
        echo -e "${CYAN}Creating a Trakt API application:${NC}"
        echo "1. Go to: ${CYAN}https://trakt.tv/oauth/applications/new${NC}"
        echo "2. Name: Curatarr"
        echo "3. Redirect URI: urn:ietf:wg:oauth:2.0:oob"
        echo "4. Check all permissions"
        echo "5. Save and copy the Client ID and Client Secret"
        echo ""
        read -p "Enter your Trakt Client ID: " TRAKT_CLIENT_ID
        read -p "Enter your Trakt Client Secret: " TRAKT_CLIENT_SECRET

        if [ -n "$TRAKT_CLIENT_ID" ] && [ -n "$TRAKT_CLIENT_SECRET" ]; then
            TRAKT_ENABLED="true"
            echo -e "${GREEN}✓ Trakt credentials received${NC}"
            echo ""
            echo -e "${CYAN}Authenticating with Trakt...${NC}"

            # Run device auth flow inline
            TRAKT_AUTH_RESULT=$(python3 -c "
import sys
sys.path.insert(0, '.')
from utils.trakt import TraktClient

client = TraktClient('$TRAKT_CLIENT_ID', '$TRAKT_CLIENT_SECRET')
try:
    device_info = client.get_device_code()
    print('URL:' + device_info['verification_url'])
    print('CODE:' + device_info['user_code'])
    print('DEVICE:' + device_info['device_code'])
except Exception as e:
    print('ERROR:' + str(e))
" 2>/dev/null)

            TRAKT_URL=$(echo "$TRAKT_AUTH_RESULT" | grep "^URL:" | cut -d: -f2-)
            TRAKT_CODE=$(echo "$TRAKT_AUTH_RESULT" | grep "^CODE:" | cut -d: -f2-)
            TRAKT_DEVICE=$(echo "$TRAKT_AUTH_RESULT" | grep "^DEVICE:" | cut -d: -f2-)
            TRAKT_ERROR=$(echo "$TRAKT_AUTH_RESULT" | grep "^ERROR:" | cut -d: -f2-)

            if [ -n "$TRAKT_ERROR" ]; then
                echo -e "${RED}Failed to get device code: $TRAKT_ERROR${NC}"
                echo -e "${YELLOW}You can authenticate later with: python3 utils/trakt_auth.py${NC}"
            elif [ -n "$TRAKT_CODE" ]; then
                echo ""
                echo -e "1. Go to: ${CYAN}$TRAKT_URL${NC}"
                echo -e "2. Enter code: ${YELLOW}$TRAKT_CODE${NC}"
                echo ""
                read -p "Press Enter after you've approved on Trakt..."

                # Poll for token
                TRAKT_TOKENS=$(python3 -c "
import sys
sys.path.insert(0, '.')
from utils.trakt import TraktClient

client = TraktClient('$TRAKT_CLIENT_ID', '$TRAKT_CLIENT_SECRET')
success = client.poll_for_token('$TRAKT_DEVICE', interval=1, expires_in=30)
if success:
    print('ACCESS:' + client.access_token)
    print('REFRESH:' + client.refresh_token)
else:
    print('FAILED')
" 2>/dev/null)

                TRAKT_ACCESS=$(echo "$TRAKT_TOKENS" | grep "^ACCESS:" | cut -d: -f2-)
                TRAKT_REFRESH=$(echo "$TRAKT_TOKENS" | grep "^REFRESH:" | cut -d: -f2-)

                if [ -n "$TRAKT_ACCESS" ]; then
                    echo -e "${GREEN}✓ Trakt authenticated!${NC}"
                else
                    echo -e "${YELLOW}Authentication not completed. You can retry later with: python3 utils/trakt_auth.py${NC}"
                    TRAKT_ACCESS=""
                    TRAKT_REFRESH=""
                fi
            fi

            # --- Trakt Export Configuration ---
            echo ""
            echo -e "${YELLOW}Trakt Export Configuration${NC}"
            echo ""
            echo -e "${RED}IMPORTANT:${NC} Trakt export syncs recommendations to YOUR personal Trakt account."
            echo ""

            # Auto-detect admin username from Plex
            ADMIN_USER=$(python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from plexapi.myplex import MyPlexAccount
    account = MyPlexAccount(token='$PLEX_TOKEN')
    print(account.username)
except Exception as e:
    print('')
" 2>/dev/null)

            TRAKT_AUTO_SYNC="false"
            TRAKT_USER_MODE="mapping"
            TRAKT_PLEX_USERS="[]"

            echo "Which Plex users' recommendations should be exported to YOUR Trakt?"
            echo ""
            if [ -n "$ADMIN_USER" ]; then
                echo "  1) Just me (admin: $ADMIN_USER) - RECOMMENDED"
            else
                echo "  1) Just me (admin) - enter username manually"
            fi
            echo "  2) All users - exports everyone's recommendations to your Trakt"
            echo "  3) Skip - I'll configure manually in config/config.yml"
            echo ""
            read -p "Choose [1/2/3]: " TRAKT_EXPORT_CHOICE

            case "$TRAKT_EXPORT_CHOICE" in
                1)
                    if [ -n "$ADMIN_USER" ]; then
                        TRAKT_PLEX_USER="$ADMIN_USER"
                    else
                        read -p "Enter your Plex username: " TRAKT_PLEX_USER
                    fi
                    if [ -n "$TRAKT_PLEX_USER" ]; then
                        TRAKT_PLEX_USERS="[\"$TRAKT_PLEX_USER\"]"
                        TRAKT_USER_MODE="mapping"
                        echo ""
                        read -p "Auto-sync to Trakt on each run? (y/N): " ENABLE_AUTO_SYNC
                        if [[ "$ENABLE_AUTO_SYNC" =~ ^[Yy]$ ]]; then
                            TRAKT_AUTO_SYNC="true"
                            echo -e "${GREEN}✓ Auto-sync enabled for: $TRAKT_PLEX_USER${NC}"
                        else
                            echo -e "${YELLOW}Auto-sync disabled. Use HTML export button instead.${NC}"
                        fi
                    fi
                    ;;
                2)
                    TRAKT_USER_MODE="per_user"
                    TRAKT_PLEX_USERS="[]"
                    echo ""
                    echo -e "${YELLOW}Warning: This exports ALL Plex users' data to your Trakt account.${NC}"
                    read -p "Auto-sync to Trakt on each run? (y/N): " ENABLE_AUTO_SYNC
                    if [[ "$ENABLE_AUTO_SYNC" =~ ^[Yy]$ ]]; then
                        TRAKT_AUTO_SYNC="true"
                        echo -e "${GREEN}✓ Auto-sync enabled for all users${NC}"
                    else
                        echo -e "${YELLOW}Auto-sync disabled. Use HTML export button instead.${NC}"
                    fi
                    ;;
                *)
                    echo -e "${YELLOW}Skipping. Configure trakt.export in config/config.yml later.${NC}"
                    ;;
            esac
        else
            echo -e "${YELLOW}Skipping Trakt (credentials not provided)${NC}"
        fi
    else
        echo -e "${YELLOW}Skipping Trakt (can be enabled later in config/config.yml)${NC}"
    fi
    echo ""

    # --- Optional: Sonarr Integration ---
    echo -e "${YELLOW}Step 7: Sonarr Integration (Optional)${NC}"
    echo ""
    echo "Sonarr can auto-add recommended TV shows to your download queue."
    echo ""
    read -p "Enable Sonarr integration? (y/N): " ENABLE_SONARR
    SONARR_ENABLED="false"
    SONARR_URL=""
    SONARR_API_KEY=""
    SONARR_ROOT_FOLDER=""
    SONARR_QUALITY_PROFILE=""
    SONARR_AUTO_SYNC="false"
    SONARR_USER_MODE="mapping"
    SONARR_PLEX_USERS="[]"

    if [[ "$ENABLE_SONARR" =~ ^[Yy]$ ]]; then
        echo ""
        echo "Enter your Sonarr connection details:"
        echo "(Find API key in Sonarr: Settings -> General -> API Key)"
        echo ""
        read -p "Sonarr URL (e.g., http://localhost:8989): " SONARR_URL
        read -p "Sonarr API Key: " SONARR_API_KEY

        if [ -n "$SONARR_URL" ] && [ -n "$SONARR_API_KEY" ]; then
            # Test connection and get profiles/folders
            echo ""
            echo -e "${CYAN}Testing Sonarr connection...${NC}"

            SONARR_TEST=$(python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from utils.sonarr import SonarrClient
    client = SonarrClient('$SONARR_URL', '$SONARR_API_KEY')
    client.test_connection()
    print('OK')
    # Get quality profiles
    profiles = client.get_quality_profiles()
    for p in profiles:
        print(f'PROFILE:{p[\"id\"]}:{p[\"name\"]}')
    # Get root folders
    folders = client.get_root_folders()
    for f in folders:
        print(f'FOLDER:{f[\"path\"]}')
except Exception as e:
    print(f'ERROR:{e}')
" 2>/dev/null)

            if echo "$SONARR_TEST" | grep -q "^OK"; then
                echo -e "${GREEN}✓ Connected to Sonarr!${NC}"
                SONARR_ENABLED="true"
                echo ""

                # Show quality profiles
                echo "Available quality profiles:"
                i=1
                declare -a PROFILE_NAMES
                while IFS=':' read -r prefix id name; do
                    if [ "$prefix" = "PROFILE" ]; then
                        PROFILE_NAMES+=("$name")
                        echo "  $i) $name"
                        ((i++))
                    fi
                done <<< "$SONARR_TEST"

                read -p "Choose quality profile [1]: " PROFILE_CHOICE
                PROFILE_CHOICE=${PROFILE_CHOICE:-1}
                PROFILE_IDX=$((PROFILE_CHOICE - 1))
                if [ $PROFILE_IDX -ge 0 ] && [ $PROFILE_IDX -lt ${#PROFILE_NAMES[@]} ]; then
                    SONARR_QUALITY_PROFILE="${PROFILE_NAMES[$PROFILE_IDX]}"
                else
                    SONARR_QUALITY_PROFILE="${PROFILE_NAMES[0]}"
                fi
                echo -e "${GREEN}✓ Using: $SONARR_QUALITY_PROFILE${NC}"
                echo ""

                # Show root folders
                echo "Available root folders:"
                i=1
                declare -a FOLDER_PATHS
                while IFS=':' read -r prefix path; do
                    if [ "$prefix" = "FOLDER" ]; then
                        FOLDER_PATHS+=("$path")
                        echo "  $i) $path"
                        ((i++))
                    fi
                done <<< "$SONARR_TEST"

                read -p "Choose root folder [1]: " FOLDER_CHOICE
                FOLDER_CHOICE=${FOLDER_CHOICE:-1}
                FOLDER_IDX=$((FOLDER_CHOICE - 1))
                if [ $FOLDER_IDX -ge 0 ] && [ $FOLDER_IDX -lt ${#FOLDER_PATHS[@]} ]; then
                    SONARR_ROOT_FOLDER="${FOLDER_PATHS[$FOLDER_IDX]}"
                else
                    SONARR_ROOT_FOLDER="${FOLDER_PATHS[0]}"
                fi
                echo -e "${GREEN}✓ Using: $SONARR_ROOT_FOLDER${NC}"
                echo ""

                # Ask about which user's recommendations to sync
                echo "Which Plex user's TV recommendations should go to Sonarr?"
                echo "  1) Just mine - only YOUR recommendations (recommended)"
                echo "  2) All users - everyone's recommendations"
                echo "  3) Skip for now - configure later"
                echo ""
                read -p "Choose [1/2/3]: " SONARR_USER_CHOICE

                case "$SONARR_USER_CHOICE" in
                    1)
                        # Get first user from USERS_LIST as default
                        if [ -n "$ADMIN_USER" ]; then
                            SONARR_PLEX_USER="$ADMIN_USER"
                        else
                            read -p "Enter your Plex username: " SONARR_PLEX_USER
                        fi
                        if [ -n "$SONARR_PLEX_USER" ]; then
                            SONARR_PLEX_USERS="[\"$SONARR_PLEX_USER\"]"
                            SONARR_USER_MODE="mapping"

                            read -p "Auto-add to Sonarr on each run? (y/N): " ENABLE_SONARR_AUTO
                            if [[ "$ENABLE_SONARR_AUTO" =~ ^[Yy]$ ]]; then
                                SONARR_AUTO_SYNC="true"
                                echo -e "${GREEN}✓ Auto-sync enabled for: $SONARR_PLEX_USER${NC}"
                            else
                                echo -e "${YELLOW}Manual mode - enable auto_sync in sonarr.yml when ready${NC}"
                            fi
                        fi
                        ;;
                    2)
                        SONARR_USER_MODE="combined"
                        SONARR_PLEX_USERS="[]"
                        echo -e "${YELLOW}Warning: This adds ALL Plex users' recommendations to Sonarr.${NC}"
                        read -p "Auto-add to Sonarr on each run? (y/N): " ENABLE_SONARR_AUTO
                        if [[ "$ENABLE_SONARR_AUTO" =~ ^[Yy]$ ]]; then
                            SONARR_AUTO_SYNC="true"
                        fi
                        ;;
                    *)
                        echo -e "${YELLOW}Skipping. Configure sonarr.yml later.${NC}"
                        ;;
                esac
            else
                SONARR_ERROR=$(echo "$SONARR_TEST" | grep "^ERROR:" | cut -d: -f2-)
                echo -e "${RED}Could not connect to Sonarr: $SONARR_ERROR${NC}"
                echo -e "${YELLOW}Check your URL and API key, then configure sonarr.yml manually.${NC}"
            fi
        else
            echo -e "${YELLOW}Skipping Sonarr (credentials not provided)${NC}"
        fi
    else
        echo -e "${YELLOW}Skipping Sonarr (can be enabled later in config/sonarr.yml)${NC}"
    fi
    echo ""

    # --- Optional: Radarr Integration ---
    echo -e "${YELLOW}Step 8: Radarr Integration (Optional)${NC}"
    echo ""
    echo "Radarr can auto-add recommended movies to your download queue."
    echo ""
    read -p "Enable Radarr integration? (y/N): " ENABLE_RADARR
    RADARR_ENABLED="false"
    RADARR_URL=""
    RADARR_API_KEY=""
    RADARR_ROOT_FOLDER=""
    RADARR_QUALITY_PROFILE=""
    RADARR_AUTO_SYNC="false"
    RADARR_USER_MODE="mapping"
    RADARR_PLEX_USERS="[]"

    if [[ "$ENABLE_RADARR" =~ ^[Yy]$ ]]; then
        echo ""
        echo "Enter your Radarr connection details:"
        echo "(Find API key in Radarr: Settings -> General -> API Key)"
        echo ""
        read -p "Radarr URL (e.g., http://localhost:7878): " RADARR_URL
        read -p "Radarr API Key: " RADARR_API_KEY

        if [ -n "$RADARR_URL" ] && [ -n "$RADARR_API_KEY" ]; then
            # Test connection and get profiles/folders
            echo ""
            echo -e "${CYAN}Testing Radarr connection...${NC}"

            RADARR_TEST=$(python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from utils.radarr import RadarrClient
    client = RadarrClient('$RADARR_URL', '$RADARR_API_KEY')
    client.test_connection()
    print('OK')
    # Get quality profiles
    profiles = client.get_quality_profiles()
    for p in profiles:
        print(f'PROFILE:{p[\"id\"]}:{p[\"name\"]}')
    # Get root folders
    folders = client.get_root_folders()
    for f in folders:
        print(f'FOLDER:{f[\"path\"]}')
except Exception as e:
    print(f'ERROR:{e}')
" 2>/dev/null)

            if echo "$RADARR_TEST" | grep -q "^OK"; then
                echo -e "${GREEN}✓ Connected to Radarr!${NC}"
                RADARR_ENABLED="true"
                echo ""

                # Show quality profiles
                echo "Available quality profiles:"
                i=1
                declare -a RADARR_PROFILE_NAMES
                while IFS=':' read -r prefix id name; do
                    if [ "$prefix" = "PROFILE" ]; then
                        RADARR_PROFILE_NAMES+=("$name")
                        echo "  $i) $name"
                        ((i++))
                    fi
                done <<< "$RADARR_TEST"

                read -p "Choose quality profile [1]: " RADARR_PROFILE_CHOICE
                RADARR_PROFILE_CHOICE=${RADARR_PROFILE_CHOICE:-1}
                RADARR_PROFILE_IDX=$((RADARR_PROFILE_CHOICE - 1))
                if [ $RADARR_PROFILE_IDX -ge 0 ] && [ $RADARR_PROFILE_IDX -lt ${#RADARR_PROFILE_NAMES[@]} ]; then
                    RADARR_QUALITY_PROFILE="${RADARR_PROFILE_NAMES[$RADARR_PROFILE_IDX]}"
                else
                    RADARR_QUALITY_PROFILE="${RADARR_PROFILE_NAMES[0]}"
                fi
                echo -e "${GREEN}✓ Using: $RADARR_QUALITY_PROFILE${NC}"
                echo ""

                # Show root folders
                echo "Available root folders:"
                i=1
                declare -a RADARR_FOLDER_PATHS
                while IFS=':' read -r prefix path; do
                    if [ "$prefix" = "FOLDER" ]; then
                        RADARR_FOLDER_PATHS+=("$path")
                        echo "  $i) $path"
                        ((i++))
                    fi
                done <<< "$RADARR_TEST"

                read -p "Choose root folder [1]: " RADARR_FOLDER_CHOICE
                RADARR_FOLDER_CHOICE=${RADARR_FOLDER_CHOICE:-1}
                RADARR_FOLDER_IDX=$((RADARR_FOLDER_CHOICE - 1))
                if [ $RADARR_FOLDER_IDX -ge 0 ] && [ $RADARR_FOLDER_IDX -lt ${#RADARR_FOLDER_PATHS[@]} ]; then
                    RADARR_ROOT_FOLDER="${RADARR_FOLDER_PATHS[$RADARR_FOLDER_IDX]}"
                else
                    RADARR_ROOT_FOLDER="${RADARR_FOLDER_PATHS[0]}"
                fi
                echo -e "${GREEN}✓ Using: $RADARR_ROOT_FOLDER${NC}"
                echo ""

                # Ask about which user's recommendations to sync
                echo "Which Plex user's movie recommendations should go to Radarr?"
                echo "  1) Just mine - only YOUR recommendations (recommended)"
                echo "  2) All users - everyone's recommendations"
                echo "  3) Skip for now - configure later"
                echo ""
                read -p "Choose [1/2/3]: " RADARR_USER_CHOICE

                case "$RADARR_USER_CHOICE" in
                    1)
                        # Get first user from USERS_LIST as default
                        if [ -n "$ADMIN_USER" ]; then
                            RADARR_PLEX_USER="$ADMIN_USER"
                        else
                            read -p "Enter your Plex username: " RADARR_PLEX_USER
                        fi
                        if [ -n "$RADARR_PLEX_USER" ]; then
                            RADARR_PLEX_USERS="[\"$RADARR_PLEX_USER\"]"
                            RADARR_USER_MODE="mapping"

                            read -p "Auto-add to Radarr on each run? (y/N): " ENABLE_RADARR_AUTO
                            if [[ "$ENABLE_RADARR_AUTO" =~ ^[Yy]$ ]]; then
                                RADARR_AUTO_SYNC="true"
                                echo -e "${GREEN}✓ Auto-sync enabled for: $RADARR_PLEX_USER${NC}"
                            else
                                echo -e "${YELLOW}Manual mode - enable auto_sync in radarr.yml when ready${NC}"
                            fi
                        fi
                        ;;
                    2)
                        RADARR_USER_MODE="combined"
                        RADARR_PLEX_USERS="[]"
                        echo -e "${YELLOW}Warning: This adds ALL Plex users' recommendations to Radarr.${NC}"
                        read -p "Auto-add to Radarr on each run? (y/N): " ENABLE_RADARR_AUTO
                        if [[ "$ENABLE_RADARR_AUTO" =~ ^[Yy]$ ]]; then
                            RADARR_AUTO_SYNC="true"
                        fi
                        ;;
                    *)
                        echo -e "${YELLOW}Skipping. Configure radarr.yml later.${NC}"
                        ;;
                esac
            else
                RADARR_ERROR=$(echo "$RADARR_TEST" | grep "^ERROR:" | cut -d: -f2-)
                echo -e "${RED}Could not connect to Radarr: $RADARR_ERROR${NC}"
                echo -e "${YELLOW}Check your URL and API key, then configure radarr.yml manually.${NC}"
            fi
        else
            echo -e "${YELLOW}Skipping Radarr (credentials not provided)${NC}"
        fi
    else
        echo -e "${YELLOW}Skipping Radarr (can be enabled later in config/radarr.yml)${NC}"
    fi
    echo ""

    # --- Optional: MDBList Integration ---
    echo -e "${YELLOW}Step 9: MDBList Integration (Optional)${NC}"
    echo ""
    echo "MDBList can export recommendations to shareable lists."
    echo "Lists can be imported into other apps like Kometa/PMM."
    echo ""
    read -p "Enable MDBList integration? (y/N): " ENABLE_MDBLIST
    MDBLIST_ENABLED="false"
    MDBLIST_API_KEY=""
    MDBLIST_AUTO_SYNC="false"
    MDBLIST_USER_MODE="mapping"
    MDBLIST_PLEX_USERS="[]"
    MDBLIST_LIST_PREFIX="Curatarr"
    MDBLIST_REPLACE_EXISTING="true"

    if [[ "$ENABLE_MDBLIST" =~ ^[Yy]$ ]]; then
        echo ""
        echo "Enter your MDBList API key:"
        echo "(Get it from https://mdblist.com/preferences/)"
        echo ""
        read -p "MDBList API Key: " MDBLIST_API_KEY

        if [ -n "$MDBLIST_API_KEY" ]; then
            # Test connection
            echo ""
            echo -e "${CYAN}Testing MDBList connection...${NC}"

            MDBLIST_TEST=$(python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from utils.mdblist import MDBListClient
    client = MDBListClient('$MDBLIST_API_KEY')
    client.test_connection()
    print('OK')
except Exception as e:
    print(f'ERROR:{e}')
" 2>/dev/null)

            if echo "$MDBLIST_TEST" | grep -q "^OK"; then
                echo -e "${GREEN}✓ Connected to MDBList!${NC}"
                MDBLIST_ENABLED="true"
                echo ""

                # Ask about which user's recommendations to export
                echo "Which Plex user's recommendations should go to MDBList?"
                echo "  1) Just mine - only YOUR recommendations (recommended)"
                echo "  2) All users - everyone's recommendations (combined list)"
                echo "  3) Per-user - separate list for each user"
                echo "  4) Skip for now - configure later"
                echo ""
                read -p "Choose [1/2/3/4]: " MDBLIST_USER_CHOICE

                case "$MDBLIST_USER_CHOICE" in
                    1)
                        # Get first user from USERS_LIST as default
                        if [ -n "$ADMIN_USER" ]; then
                            MDBLIST_PLEX_USER="$ADMIN_USER"
                        else
                            read -p "Enter your Plex username: " MDBLIST_PLEX_USER
                        fi
                        if [ -n "$MDBLIST_PLEX_USER" ]; then
                            MDBLIST_PLEX_USERS="[\"$MDBLIST_PLEX_USER\"]"
                            MDBLIST_USER_MODE="mapping"

                            read -p "Auto-export to MDBList on each run? (y/N): " ENABLE_MDBLIST_AUTO
                            if [[ "$ENABLE_MDBLIST_AUTO" =~ ^[Yy]$ ]]; then
                                MDBLIST_AUTO_SYNC="true"
                                echo -e "${GREEN}✓ Auto-sync enabled for: $MDBLIST_PLEX_USER${NC}"
                            else
                                echo -e "${YELLOW}Manual mode - enable auto_sync in mdblist.yml when ready${NC}"
                            fi
                        fi
                        ;;
                    2)
                        MDBLIST_USER_MODE="combined"
                        MDBLIST_PLEX_USERS="[]"
                        read -p "Auto-export to MDBList on each run? (y/N): " ENABLE_MDBLIST_AUTO
                        if [[ "$ENABLE_MDBLIST_AUTO" =~ ^[Yy]$ ]]; then
                            MDBLIST_AUTO_SYNC="true"
                        fi
                        ;;
                    3)
                        MDBLIST_USER_MODE="per_user"
                        MDBLIST_PLEX_USERS="[]"
                        read -p "Auto-export to MDBList on each run? (y/N): " ENABLE_MDBLIST_AUTO
                        if [[ "$ENABLE_MDBLIST_AUTO" =~ ^[Yy]$ ]]; then
                            MDBLIST_AUTO_SYNC="true"
                        fi
                        ;;
                    *)
                        echo -e "${YELLOW}Skipping. Configure mdblist.yml later.${NC}"
                        ;;
                esac
            else
                MDBLIST_ERROR=$(echo "$MDBLIST_TEST" | grep "^ERROR:" | cut -d: -f2-)
                echo -e "${RED}Could not connect to MDBList: $MDBLIST_ERROR${NC}"
                echo -e "${YELLOW}Check your API key, then configure mdblist.yml manually.${NC}"
            fi
        else
            echo -e "${YELLOW}Skipping MDBList (API key not provided)${NC}"
        fi
    else
        echo -e "${YELLOW}Skipping MDBList (can be enabled later in config/mdblist.yml)${NC}"
    fi
    echo ""

    # --- Optional: Simkl Integration ---
    echo -e "${YELLOW}Step 10: Simkl Integration (Optional)${NC}"
    echo ""
    echo "Simkl tracks anime/TV/movies with excellent anime database."
    echo "Great for anime fans - enhances recommendations with Simkl data."
    echo ""
    read -p "Enable Simkl integration? (y/N): " ENABLE_SIMKL
    SIMKL_ENABLED="false"
    SIMKL_CLIENT_ID=""
    SIMKL_ACCESS_TOKEN=""
    SIMKL_AUTO_SYNC="false"
    SIMKL_USER_MODE="mapping"
    SIMKL_PLEX_USERS="[]"

    if [[ "$ENABLE_SIMKL" =~ ^[Yy]$ ]]; then
        echo ""
        echo "To use Simkl, you need to create an app:"
        echo "1. Go to https://simkl.com/settings/developer/"
        echo "2. Click 'New Application'"
        echo "3. Enter any name (e.g., 'Curatarr')"
        echo "4. Copy the Client ID"
        echo ""
        read -p "Simkl Client ID: " SIMKL_CLIENT_ID

        if [ -n "$SIMKL_CLIENT_ID" ]; then
            # Get PIN code for auth
            echo ""
            echo -e "${CYAN}Getting Simkl PIN code...${NC}"

            SIMKL_PIN=$(python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from utils.simkl import SimklClient
    client = SimklClient('$SIMKL_CLIENT_ID')
    pin_data = client.get_pin_code()
    print(f\"CODE:{pin_data['user_code']}\")
    print(f\"URL:{pin_data['verification_url']}\")
except Exception as e:
    print(f'ERROR:{e}')
" 2>/dev/null)

            if echo "$SIMKL_PIN" | grep -q "^CODE:"; then
                PIN_CODE=$(echo "$SIMKL_PIN" | grep "^CODE:" | cut -d: -f2)
                PIN_URL=$(echo "$SIMKL_PIN" | grep "^URL:" | cut -d: -f2-)

                echo ""
                echo -e "${CYAN}To authorize Curatarr:${NC}"
                echo "1. Go to: ${PIN_URL}"
                echo "2. Enter this code: ${YELLOW}${PIN_CODE}${NC}"
                echo ""
                read -p "Press Enter after you've authorized the app..."

                # Poll for token
                echo -e "${CYAN}Checking authorization...${NC}"
                SIMKL_AUTH=$(python3 -c "
import sys
sys.path.insert(0, '.')
try:
    from utils.simkl import SimklClient
    client = SimklClient('$SIMKL_CLIENT_ID')
    if client.poll_for_token('$PIN_CODE', interval=2, expires_in=30):
        print(f'TOKEN:{client.access_token}')
    else:
        print('ERROR:Authorization timed out or was denied')
except Exception as e:
    print(f'ERROR:{e}')
" 2>/dev/null)

                if echo "$SIMKL_AUTH" | grep -q "^TOKEN:"; then
                    SIMKL_ACCESS_TOKEN=$(echo "$SIMKL_AUTH" | grep "^TOKEN:" | cut -d: -f2-)
                    SIMKL_ENABLED="true"
                    echo -e "${GREEN}✓ Connected to Simkl!${NC}"
                    echo ""

                    # Ask about export settings
                    echo "Which Plex user's recommendations should go to Simkl?"
                    echo "  1) Just mine - only YOUR recommendations (recommended)"
                    echo "  2) All users - everyone's recommendations"
                    echo "  3) Skip for now - configure later"
                    echo ""
                    read -p "Choose [1/2/3]: " SIMKL_USER_CHOICE

                    case "$SIMKL_USER_CHOICE" in
                        1)
                            if [ -n "$ADMIN_USER" ]; then
                                SIMKL_PLEX_USER="$ADMIN_USER"
                            else
                                read -p "Enter your Plex username: " SIMKL_PLEX_USER
                            fi
                            if [ -n "$SIMKL_PLEX_USER" ]; then
                                SIMKL_PLEX_USERS="[\"$SIMKL_PLEX_USER\"]"
                                SIMKL_USER_MODE="mapping"

                                read -p "Auto-export to Simkl on each run? (y/N): " ENABLE_SIMKL_AUTO
                                if [[ "$ENABLE_SIMKL_AUTO" =~ ^[Yy]$ ]]; then
                                    SIMKL_AUTO_SYNC="true"
                                    echo -e "${GREEN}✓ Auto-sync enabled for: $SIMKL_PLEX_USER${NC}"
                                else
                                    echo -e "${YELLOW}Manual mode - enable auto_sync in simkl.yml when ready${NC}"
                                fi
                            fi
                            ;;
                        2)
                            SIMKL_USER_MODE="combined"
                            SIMKL_PLEX_USERS="[]"
                            read -p "Auto-export to Simkl on each run? (y/N): " ENABLE_SIMKL_AUTO
                            if [[ "$ENABLE_SIMKL_AUTO" =~ ^[Yy]$ ]]; then
                                SIMKL_AUTO_SYNC="true"
                            fi
                            ;;
                        *)
                            echo -e "${YELLOW}Skipping. Configure simkl.yml later.${NC}"
                            ;;
                    esac
                else
                    SIMKL_ERROR=$(echo "$SIMKL_AUTH" | grep "^ERROR:" | cut -d: -f2-)
                    echo -e "${RED}Authorization failed: $SIMKL_ERROR${NC}"
                    echo -e "${YELLOW}You can configure Simkl later in config/simkl.yml${NC}"
                fi
            else
                SIMKL_ERROR=$(echo "$SIMKL_PIN" | grep "^ERROR:" | cut -d: -f2-)
                echo -e "${RED}Could not get PIN code: $SIMKL_ERROR${NC}"
                echo -e "${YELLOW}Check your Client ID, then configure simkl.yml manually.${NC}"
            fi
        else
            echo -e "${YELLOW}Skipping Simkl (Client ID not provided)${NC}"
        fi
    else
        echo -e "${YELLOW}Skipping Simkl (can be enabled later in config/simkl.yml)${NC}"
    fi
    echo ""

    # --- Write config/config.yml (essentials only) ---
    mkdir -p config
    echo -e "${CYAN}Creating config/config.yml...${NC}"

    cat > config/config.yml << CONFIGEOF
# Curatarr Configuration
# Generated by setup wizard
# See tuning.yml for display/scoring options

plex:
  url: $PLEX_URL
  token: $PLEX_TOKEN
  movie_library: $MOVIE_LIB
  tv_library: $TV_LIB

tmdb:
  api_key: $TMDB_KEY

users:
  list: $USERS_LIST
  preferences:${USER_PREFS:-}

general:
  plex_only: true
  auto_update: true
  log_retention_days: 7
CONFIGEOF

    echo -e "${GREEN}✓ config/config.yml created!${NC}"

    # --- Write trakt.yml if enabled ---
    if [ "$TRAKT_ENABLED" = "true" ]; then
        echo -e "${CYAN}Creating config/trakt.yml...${NC}"

        cat > config/trakt.yml << TRAKTEOF
# Curatarr Trakt Configuration

enabled: true
client_id: ${TRAKT_CLIENT_ID}
client_secret: ${TRAKT_CLIENT_SECRET}
access_token: ${TRAKT_ACCESS:-null}
refresh_token: ${TRAKT_REFRESH:-null}

export:
  enabled: true
  auto_sync: ${TRAKT_AUTO_SYNC:-false}
  list_prefix: "Curatarr"
  user_mode: "${TRAKT_USER_MODE:-mapping}"
  plex_users: ${TRAKT_PLEX_USERS:-[]}

import:
  enabled: true
  exclude_watchlist: true
TRAKTEOF

        echo -e "${GREEN}✓ config/trakt.yml created!${NC}"
    fi

    # --- Write sonarr.yml if enabled ---
    if [ "$SONARR_ENABLED" = "true" ]; then
        echo -e "${CYAN}Creating config/sonarr.yml...${NC}"

        cat > config/sonarr.yml << SONARREOF
# Curatarr Sonarr Configuration

enabled: true
url: ${SONARR_URL}
api_key: ${SONARR_API_KEY}

# Sync behavior
auto_sync: ${SONARR_AUTO_SYNC:-false}
user_mode: "${SONARR_USER_MODE:-mapping}"
plex_users: ${SONARR_PLEX_USERS:-[]}

# Import settings
root_folder: ${SONARR_ROOT_FOLDER}
quality_profile: ${SONARR_QUALITY_PROFILE}
series_type: standard
season_folder: true

# Tagging
tag: Curatarr
append_usernames: false

# Download behavior (safe defaults)
monitor: false
monitor_option: none
search_missing: false
SONARREOF

        echo -e "${GREEN}✓ config/sonarr.yml created!${NC}"
    fi

    # --- Write radarr.yml if enabled ---
    if [ "$RADARR_ENABLED" = "true" ]; then
        echo -e "${CYAN}Creating config/radarr.yml...${NC}"

        cat > config/radarr.yml << RADARREOF
# Curatarr Radarr Configuration

enabled: true
url: ${RADARR_URL}
api_key: ${RADARR_API_KEY}

# Sync behavior
auto_sync: ${RADARR_AUTO_SYNC:-false}
user_mode: "${RADARR_USER_MODE:-mapping}"
plex_users: ${RADARR_PLEX_USERS:-[]}

# Import settings
root_folder: ${RADARR_ROOT_FOLDER}
quality_profile: ${RADARR_QUALITY_PROFILE}
minimum_availability: released

# Tagging
tag: Curatarr
append_usernames: false

# Download behavior (safe defaults)
monitor: false
search_for_movie: false
RADARREOF

        echo -e "${GREEN}✓ config/radarr.yml created!${NC}"
    fi

    # --- Write mdblist.yml if enabled ---
    if [ "$MDBLIST_ENABLED" = "true" ]; then
        echo -e "${CYAN}Creating config/mdblist.yml...${NC}"

        cat > config/mdblist.yml << MDBLISTEOF
# Curatarr MDBList Configuration

enabled: true
api_key: ${MDBLIST_API_KEY}

# Sync behavior
auto_sync: ${MDBLIST_AUTO_SYNC:-false}
user_mode: "${MDBLIST_USER_MODE:-mapping}"
plex_users: ${MDBLIST_PLEX_USERS:-[]}

# List naming
list_prefix: "${MDBLIST_LIST_PREFIX:-Curatarr}"

# Replace list contents on each run (true) or append new items (false)
replace_existing: ${MDBLIST_REPLACE_EXISTING:-true}
MDBLISTEOF

        echo -e "${GREEN}✓ config/mdblist.yml created!${NC}"
    fi

    # --- Write simkl.yml if enabled ---
    if [ "$SIMKL_ENABLED" = "true" ]; then
        echo -e "${CYAN}Creating config/simkl.yml...${NC}"

        cat > config/simkl.yml << SIMKLEOF
# Curatarr Simkl Configuration

enabled: true
client_id: ${SIMKL_CLIENT_ID}
access_token: ${SIMKL_ACCESS_TOKEN}

# Import settings
import:
  enabled: true
  include_anime: true

# Discovery settings
discovery:
  enabled: true
  anime_focus: true
  include_tv: true
  include_movies: false

# Export settings
export:
  enabled: true
  auto_sync: ${SIMKL_AUTO_SYNC:-false}
  user_mode: "${SIMKL_USER_MODE:-mapping}"
  plex_users: ${SIMKL_PLEX_USERS:-[]}
SIMKLEOF

        echo -e "${GREEN}✓ config/simkl.yml created!${NC}"
    fi
    echo ""
}

# ------------------------------------------------------------------------
# CRON SETUP
# ------------------------------------------------------------------------
show_cron_info() {
    echo ""
    echo -e "${CYAN}=== What is a Cron Job? ===${NC}"
    echo ""
    echo "A cron job is a scheduled task that runs automatically on your system."
    echo "For this project, it would:"
    echo "  • Run once per day (default: 3 AM)"
    echo "  • Analyze everyone's watch history"
    echo "  • Update recommendations automatically"
    echo "  • You never have to remember to run it"
    echo ""
    echo "It's completely optional - you can always run ./run.sh manually instead."
    echo ""
    read -p "Press Enter to continue..."
}

setup_cron_job() {
    CRON_CMD="0 3 * * * cd $SCRIPT_DIR && ./run.sh >> logs/daily-run.log 2>&1"

    # Check if cron entry already exists
    if crontab -l 2>/dev/null | grep -q "$SCRIPT_DIR/run.sh"; then
        echo -e "${GREEN}✓ Cron job already configured${NC}"
        return
    fi

    # Add to crontab
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab - || {
        echo -e "${RED}❌ Failed to set up cron job${NC}"
        echo ""
        echo "You can add it manually:"
        echo "  1. Run: crontab -e"
        echo "  2. Add this line: $CRON_CMD"
        echo ""
        return 1
    }

    echo -e "${GREEN}✓ Cron job configured - recommendations will run daily at 3 AM${NC}"
}

setup_cron() {
    echo ""
    echo -e "${CYAN}=== Cron Job Setup ===${NC}"
    echo ""
    echo "Would you like to set up automatic daily recommendations?"
    echo ""
    echo "  1) Yes - run daily at 3 AM"
    echo "  2) No - I'll run manually"
    echo "  3) More info - what is a cron job?"
    echo ""
    read -p "Enter choice (1/2/3): " choice

    case $choice in
        1)
            setup_cron_job
            ;;
        2)
            echo "Skipping cron setup. Run ./run.sh manually whenever you want to update."
            ;;
        3)
            show_cron_info
            setup_cron  # Ask again after showing info
            ;;
        *)
            echo "Invalid choice. Skipping cron setup."
            ;;
    esac
}

# ------------------------------------------------------------------------
# SHOW INTEGRATION STATUS
# ------------------------------------------------------------------------
show_integration_status() {
    if [ ! -f "config/config.yml" ]; then
        return
    fi

    echo -e "${CYAN}Integrations:${NC}"

    # Plex - always required
    PLEX_URL=$(python3 -c "import yaml; c=yaml.safe_load(open('config/config.yml')); print(c.get('plex', {}).get('url', ''))" 2>/dev/null)
    if [ -n "$PLEX_URL" ] && [ "$PLEX_URL" != "None" ]; then
        echo -e "  ${GREEN}✓${NC} Plex"
    else
        echo -e "  ${RED}✗${NC} Plex (not configured)"
    fi

    # TMDB - always required
    TMDB_KEY=$(python3 -c "import yaml; c=yaml.safe_load(open('config/config.yml')); print(c.get('tmdb', {}).get('api_key', ''))" 2>/dev/null)
    if [ -n "$TMDB_KEY" ] && [ "$TMDB_KEY" != "None" ]; then
        echo -e "  ${GREEN}✓${NC} TMDB"
    else
        echo -e "  ${RED}✗${NC} TMDB (not configured)"
    fi

    # Trakt - optional (check config/trakt.yml)
    if [ -f "config/trakt.yml" ]; then
        TRAKT_STATUS=$(python3 -c "
import yaml
import os
trakt = yaml.safe_load(open('config/trakt.yml'))
enabled = trakt.get('enabled', False)
has_token = bool(trakt.get('access_token'))
if enabled and has_token:
    print('authenticated')
elif enabled:
    print('enabled_no_auth')
else:
    print('disabled')
" 2>/dev/null)
    else
        TRAKT_STATUS="disabled"
    fi

    case "$TRAKT_STATUS" in
        "authenticated")
            echo -e "  ${GREEN}✓${NC} Trakt"
            ;;
        "enabled_no_auth")
            echo -e "  ${YELLOW}○${NC} Trakt (needs authentication)"
            ;;
        *)
            echo -e "  ${YELLOW}○${NC} Trakt (disabled)"
            ;;
    esac

    # External Recommendations - optional (check config/tuning.yml or defaults to enabled)
    if [ -f "config/tuning.yml" ]; then
        EXT_ENABLED=$(python3 -c "import yaml; c=yaml.safe_load(open('config/tuning.yml')); print(c.get('external_recommendations', {}).get('enabled', True))" 2>/dev/null)
    else
        EXT_ENABLED="True"  # Defaults to enabled
    fi
    if [ "$EXT_ENABLED" = "True" ]; then
        echo -e "  ${GREEN}✓${NC} External Recommendations"
    else
        echo -e "  ${YELLOW}○${NC} External Recommendations (disabled)"
    fi

    echo ""
}

# ------------------------------------------------------------------------
# MAIN EXECUTION
# ------------------------------------------------------------------------
main() {
    echo -e "${CYAN}===============================================${NC}"
    echo -e "${CYAN}    Plex Recommendation System${NC}"
    echo -e "${CYAN}===============================================${NC}"
    echo ""

    # Step 1: Check/install dependencies
    check_and_install_dependencies

    # Step 2: Check for updates (if enabled)
    check_for_updates

    # Step 3: First run setup
    if is_first_run; then
        run_setup_wizard
    fi

    # Step 4: Show integration status
    show_integration_status

    # Step 5: Sync Plex watch history to Trakt (if enabled)
    # This runs FIRST so both internal and external recommenders benefit
    if [ -f "config/trakt.yml" ] && grep -q "auto_sync: true" config/trakt.yml 2>/dev/null; then
        echo -e "${CYAN}=== Syncing Watch History to Trakt ===${NC}"
        python3 utils/trakt_sync.py || echo -e "${YELLOW}⚠ Trakt sync skipped${NC}"
        echo ""
    fi

    # Step 6: Create logs directory
    mkdir -p logs

    # Step 7: Run recommendations
    echo -e "${CYAN}=== Running Recommendations ===${NC}"
    echo ""

    echo -e "${YELLOW}Step 1/2: Movie recommendations...${NC}"
    if python3 recommenders/movie.py $DEBUG_FLAG; then
        echo -e "${GREEN}✓ Movie recommendations complete${NC}"
    else
        echo -e "${RED}❌ Movie recommendations failed${NC}"
        exit 1
    fi
    echo ""

    echo -e "${YELLOW}Step 2/2: TV recommendations...${NC}"
    if python3 recommenders/tv.py $DEBUG_FLAG; then
        echo -e "${GREEN}✓ TV recommendations complete${NC}"
    else
        echo -e "${RED}❌ TV recommendations failed${NC}"
        exit 1
    fi
    echo ""

    # Step 7: Generate external recommendations (watchlist)
    # Check tuning.yml for external_recommendations setting, default to enabled
    EXT_CHECK="true"
    if [ -f "config/tuning.yml" ] && grep -A 2 "external_recommendations:" config/tuning.yml | grep -q "enabled: false" 2>/dev/null; then
        EXT_CHECK="false"
    fi
    if [ "$EXT_CHECK" = "true" ]; then
        echo -e "${CYAN}=== Generating External Watchlists ===${NC}"
        if python3 recommenders/external.py; then
            echo -e "${GREEN}✓ External watchlists generated${NC}"
        else
            echo -e "${YELLOW}⚠ External watchlist generation failed (non-fatal)${NC}"
        fi
        echo ""
    fi

    # Step 8: Cron setup (first run only)
    if is_first_run; then
        setup_cron
    fi

    echo ""
    echo -e "${GREEN}===============================================${NC}"
    echo -e "${GREEN}           All Done!${NC}"
    echo -e "${GREEN}===============================================${NC}"
    echo ""
    echo "Your recommendations are ready!"
    echo "Check your Plex library for updated collections."
    echo ""
}

# Run main function
main "$@"
