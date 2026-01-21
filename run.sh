#!/bin/bash

# Curatarr - Unified Run Script
# This script handles everything: dependencies, setup, recommendations, collections, cron

set -e  # Exit on error

# Parse arguments
DEBUG_FLAG=""
HUNTARR_ONLY=""
for arg in "$@"; do
    case $arg in
        --debug)
            DEBUG_FLAG="--debug"
            ;;
        --huntarr-only)
            HUNTARR_ONLY="--huntarr-only"
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
# INTERACTIVE SETUP WIZARD (delegates to setup.sh)
# ------------------------------------------------------------------------
run_setup_wizard() {
    # Call the standalone setup script
    if [ -f "$SCRIPT_DIR/setup.sh" ]; then
        "$SCRIPT_DIR/setup.sh"
    else
        echo -e "${RED}ERROR: setup.sh not found${NC}"
        echo "Please run setup.sh manually or create config/config.yml"
        exit 1
    fi
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

    # Step 7: Run recommendations (skip if --huntarr-only)
    if [ -z "$HUNTARR_ONLY" ]; then
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
    fi

    # Generate external recommendations (watchlist) or huntarr-only
    EXT_CHECK="true"
    if [ -f "config/tuning.yml" ] && grep -A 2 "external_recommendations:" config/tuning.yml | grep -q "enabled: false" 2>/dev/null; then
        # Still run if huntarr-only even if external_recommendations disabled
        if [ -z "$HUNTARR_ONLY" ]; then
            EXT_CHECK="false"
        fi
    fi
    if [ "$EXT_CHECK" = "true" ]; then
        if [ -n "$HUNTARR_ONLY" ]; then
            echo -e "${CYAN}=== Running Huntarr Only ===${NC}"
        else
            echo -e "${CYAN}=== Generating External Watchlists ===${NC}"
        fi
        if python3 recommenders/external.py $HUNTARR_ONLY; then
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
    echo -e "${GREEN}           Curatarr Finished${NC}"
    echo -e "${GREEN}===============================================${NC}"
    echo ""
    echo "Check above for any warnings about collection creation."
    echo "If collections were created, they will appear in your Plex library."

    # Show link to external watchlist HTML if it exists
    local watchlist_file="$SCRIPT_DIR/recommendations/external/watchlist.html"
    if [ -f "$watchlist_file" ]; then
        echo ""
        echo -e "View external watchlist: ${CYAN}file://$watchlist_file${NC}"
    fi
    echo ""
}

# Run main function
main "$@"
