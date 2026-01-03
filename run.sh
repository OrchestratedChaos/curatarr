#!/bin/bash

# Plex Recommendation System - Unified Run Script
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
    echo -e "${CYAN}=== Plex Recommender Setup Wizard ===${NC}"
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

    # --- Write config.yml ---
    echo -e "${CYAN}Creating config.yml...${NC}"

    cat > config.yml << CONFIGEOF
# Plex Recommender Configuration
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
  auto_update: true
  log_retention_days: 7

movies:
  limit_results: 50

tv:
  limit_results: 20

collections:
  add_label: true
  stale_removal_days: 7
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

    # Step 4: Create logs directory
    mkdir -p logs

    # Step 5: Run recommendations
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

    # Step 6: Generate external recommendations (watchlist)
    if grep -A 2 "external_recommendations:" config.yml | grep -q "enabled: true" 2>/dev/null; then
        echo -e "${CYAN}=== Generating External Watchlists ===${NC}"
        if python3 recommenders/external.py; then
            echo -e "${GREEN}✓ External watchlists generated${NC}"
        else
            echo -e "${YELLOW}⚠ External watchlist generation failed (non-fatal)${NC}"
        fi
        echo ""
    fi

    # Step 7: Cron setup (first run only)
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
