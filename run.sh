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
    # Check if auto_update is enabled in config
    if [ -f "config.yml" ]; then
        AUTO_UPDATE=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yml')); print(c.get('general', {}).get('auto_update', False))" 2>/dev/null)

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
    # Check if config.yml exists and is configured
    if [ ! -f "config.yml" ]; then
        return 0  # true (first run - no config)
    fi

    # Check if TMDB key is configured
    if grep -q "YOUR_TMDB_API_KEY\|your.*api.*key.*here" config.yml 2>/dev/null; then
        return 0  # true (first run - placeholder values)
    fi

    # Check if Plex token is configured
    if grep -q "YOUR_PLEX_TOKEN\|your.*token.*here" config.yml 2>/dev/null; then
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
            echo "  3) Skip - I'll configure manually in config.yml"
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
                    echo -e "${YELLOW}Skipping. Configure trakt.export in config.yml later.${NC}"
                    ;;
            esac
        else
            echo -e "${YELLOW}Skipping Trakt (credentials not provided)${NC}"
        fi
    else
        echo -e "${YELLOW}Skipping Trakt (can be enabled later in config.yml)${NC}"
    fi
    echo ""

    # --- Write config.yml ---
    echo -e "${CYAN}Creating config.yml...${NC}"

    cat > config.yml << CONFIGEOF
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
  preferences:${USER_PREFS:-}

general:
  auto_update: true
  log_retention_days: 7

movies:
  limit_results: 50

tv:
  limit_results: 20

collections:
  add_label: true
  stale_removal_days: 7

external_recommendations:
  enabled: true
  movie_limit: 30
  show_limit: 20
  auto_open_html: false

trakt:
  enabled: $TRAKT_ENABLED
  client_id: ${TRAKT_CLIENT_ID:-null}
  client_secret: ${TRAKT_CLIENT_SECRET:-null}
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
CONFIGEOF

    echo -e "${GREEN}✓ config.yml created!${NC}"
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
    if [ ! -f "config.yml" ]; then
        return
    fi

    echo -e "${CYAN}Integrations:${NC}"

    # Plex - always required
    PLEX_URL=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yml')); print(c.get('plex', {}).get('url', ''))" 2>/dev/null)
    if [ -n "$PLEX_URL" ] && [ "$PLEX_URL" != "None" ]; then
        echo -e "  ${GREEN}✓${NC} Plex"
    else
        echo -e "  ${RED}✗${NC} Plex (not configured)"
    fi

    # TMDB - always required
    TMDB_KEY=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yml')); print(c.get('tmdb', {}).get('api_key', ''))" 2>/dev/null)
    if [ -n "$TMDB_KEY" ] && [ "$TMDB_KEY" != "None" ]; then
        echo -e "  ${GREEN}✓${NC} TMDB"
    else
        echo -e "  ${RED}✗${NC} TMDB (not configured)"
    fi

    # Trakt - optional
    TRAKT_STATUS=$(python3 -c "
import yaml
c = yaml.safe_load(open('config.yml'))
trakt = c.get('trakt', {})
enabled = trakt.get('enabled', False)
has_token = bool(trakt.get('access_token'))
if enabled and has_token:
    print('authenticated')
elif enabled:
    print('enabled_no_auth')
else:
    print('disabled')
" 2>/dev/null)

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

    # External Recommendations - optional
    EXT_ENABLED=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yml')); print(c.get('external_recommendations', {}).get('enabled', False))" 2>/dev/null)
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
    if grep -A 10 "trakt:" config.yml | grep -q "auto_sync: true" 2>/dev/null; then
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
    if grep -A 2 "external_recommendations:" config.yml | grep -q "enabled: true" 2>/dev/null; then
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
