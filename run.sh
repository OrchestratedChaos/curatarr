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

    # Step 2: First run setup
    if is_first_run; then
        echo -e "${CYAN}=== First Run Setup ===${NC}"
        echo ""
        echo "Before continuing, please edit config.yml and set:"
        echo "  • tmdb.api_key (get from https://www.themoviedb.org/settings/api)"
        echo "  • plex.url (your Plex server URL)"
        echo "  • plex.token (see: https://support.plex.tv/articles/204059436)"
        echo "  • users.list (your Plex usernames)"
        echo ""
        read -p "Press Enter once you've configured config.yml..."
        echo ""

        # Validate config was actually updated
        if grep -q "YOUR_TMDB_API_KEY\|YOUR_PLEX_TOKEN\|your.*api.*key.*here\|your.*token.*here" config.yml 2>/dev/null; then
            echo -e "${RED}❌ Config not updated. Please edit config.yml first.${NC}"
            exit 1
        fi
        echo -e "${GREEN}✓ Config validated${NC}"
        echo ""
    fi

    # Step 3: Create logs directory
    mkdir -p logs

    # Step 4: Run recommendations
    echo -e "${CYAN}=== Running Recommendations ===${NC}"
    echo ""

    echo -e "${YELLOW}Step 1/2: Movie recommendations...${NC}"
    cd "$SCRIPT_DIR/scripts/Movie-Recommendations-for-Plex"
    if python3 MRFP.py $DEBUG_FLAG; then
        echo -e "${GREEN}✓ Movie recommendations complete${NC}"
    else
        echo -e "${RED}❌ Movie recommendations failed${NC}"
        exit 1
    fi
    cd "$SCRIPT_DIR"
    echo ""

    echo -e "${YELLOW}Step 2/2: TV recommendations...${NC}"
    cd "$SCRIPT_DIR/scripts/TV-Show-Recommendations-for-Plex"
    if python3 TRFP.py $DEBUG_FLAG; then
        echo -e "${GREEN}✓ TV recommendations complete${NC}"
    else
        echo -e "${RED}❌ TV recommendations failed${NC}"
        exit 1
    fi
    cd "$SCRIPT_DIR"
    echo ""

    # Step 5: Generate external recommendations (watchlist)
    # Note: Collections are now created/updated directly by MRFP.py and TRFP.py
    if grep -A 2 "external_recommendations:" config.yml | grep -q "enabled: true" 2>/dev/null; then
        echo -e "${CYAN}=== Generating External Watchlists ===${NC}"
        if python3 scripts/generate-external-recommendations.py; then
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
