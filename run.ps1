# Plex Recommendation System - Windows Run Script
# This script handles everything: dependencies, setup, recommendations, scheduled tasks

param(
    [switch]$Debug
)

$ErrorActionPreference = "Stop"

# Color functions
function Write-Cyan { param($msg) Write-Host $msg -ForegroundColor Cyan }
function Write-Green { param($msg) Write-Host $msg -ForegroundColor Green }
function Write-Yellow { param($msg) Write-Host $msg -ForegroundColor Yellow }
function Write-Red { param($msg) Write-Host $msg -ForegroundColor Red }

# Get script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$DebugFlag = if ($Debug) { "--debug" } else { "" }

# ------------------------------------------------------------------------
# DEPENDENCY CHECKING AND INSTALLATION
# ------------------------------------------------------------------------
function Check-Dependencies {
    Write-Cyan "Checking dependencies..."
    Write-Host ""

    # Check Python 3
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        $python = Get-Command python3 -ErrorAction SilentlyContinue
    }

    if (-not $python) {
        Write-Red "X Python 3 not found"
        Write-Host ""
        Write-Host "Please install Python 3.8+ from:"
        Write-Host "  https://www.python.org/downloads/"
        Write-Host ""
        Write-Host "IMPORTANT: Check 'Add Python to PATH' during installation"
        Write-Host ""
        exit 1
    }

    $pythonCmd = $python.Source
    $pythonVersion = & $pythonCmd --version 2>&1
    Write-Green "OK Python $pythonVersion found"

    # Check pip
    $pipCheck = & $pythonCmd -m pip --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Yellow "pip not found, attempting to install..."
        & $pythonCmd -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) {
            Write-Red "X Failed to install pip"
            Write-Host "Please install pip manually"
            exit 1
        }
    }
    Write-Green "OK pip found"

    # Install/update Python requirements
    if (Test-Path "requirements.txt") {
        Write-Cyan "Installing Python dependencies..."
        & $pythonCmd -m pip install -r requirements.txt --quiet --upgrade 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Red "X Failed to install Python dependencies"
            Write-Host "Try running manually: python -m pip install -r requirements.txt"
            exit 1
        }
        Write-Green "OK All dependencies installed"
    }

    Write-Host ""
    return $pythonCmd
}

# ------------------------------------------------------------------------
# AUTO-UPDATE FROM GITHUB
# ------------------------------------------------------------------------
function Check-ForUpdates {
    param($pythonCmd)

    if (Test-Path "config.yml") {
        $autoUpdate = & $pythonCmd -c "import yaml; c=yaml.safe_load(open('config.yml')); print(c.get('general', {}).get('auto_update', False))" 2>$null

        if ($autoUpdate -eq "True") {
            Write-Cyan "Checking for updates..."

            if (Test-Path ".git") {
                # Fetch latest from remote
                git fetch origin main --quiet 2>$null
                if ($LASTEXITCODE -ne 0) {
                    Write-Yellow "Could not check for updates (network error)"
                    return
                }

                $local = git rev-parse HEAD 2>$null
                $remote = git rev-parse origin/main 2>$null

                if ($local -ne $remote) {
                    Write-Yellow "Update available! Pulling latest changes..."

                    git stash --quiet 2>$null
                    $pullResult = git pull origin main --quiet 2>&1

                    if ($LASTEXITCODE -eq 0) {
                        Write-Green "OK Updated successfully!"
                        git stash pop --quiet 2>$null
                        Write-Yellow "Restarting with updated code..."
                        Write-Host ""
                        & $MyInvocation.MyCommand.Path @PSBoundParameters
                        exit
                    } else {
                        Write-Red "Update failed, continuing with current version"
                        git stash pop --quiet 2>$null
                    }
                } else {
                    Write-Green "OK Already up to date"
                }
            } else {
                Write-Yellow "Not a git repository, skipping update check"
            }
            Write-Host ""
        }
    }
}

# ------------------------------------------------------------------------
# INTERACTIVE SETUP WIZARD
# ------------------------------------------------------------------------
function Start-SetupWizard {
    Write-Cyan "=== Plex Recommender Setup Wizard ==="
    Write-Host ""
    Write-Host "Let's get you set up! I'll walk you through the configuration."
    Write-Host ""

    # --- TMDB API Key ---
    Write-Yellow "Step 1: TMDB API Key"
    Write-Host ""
    Write-Host "You need a free TMDB API key for movie/show metadata."
    Write-Cyan "Get one here: https://www.themoviedb.org/settings/api"
    Write-Host "(Create account -> Settings -> API -> Create -> Copy 'API Key')"
    Write-Host ""
    $tmdbKey = Read-Host "Enter your TMDB API key"
    if ([string]::IsNullOrWhiteSpace($tmdbKey)) {
        Write-Red "TMDB API key is required. Exiting."
        exit 1
    }
    Write-Green "OK Got it"
    Write-Host ""

    # --- Plex URL ---
    Write-Yellow "Step 2: Plex Server URL"
    Write-Host ""
    Write-Host "Your Plex server URL (usually http://IP:32400)"
    Write-Host "Example: http://192.168.1.100:32400"
    Write-Host ""
    $plexUrl = Read-Host "Enter your Plex URL"
    if ([string]::IsNullOrWhiteSpace($plexUrl)) {
        Write-Red "Plex URL is required. Exiting."
        exit 1
    }
    Write-Green "OK Got it"
    Write-Host ""

    # --- Plex Token ---
    Write-Yellow "Step 3: Plex Token"
    Write-Host ""
    Write-Host "Your Plex authentication token."
    Write-Cyan "How to find it: https://support.plex.tv/articles/204059436"
    Write-Host "(Open any media -> Get Info -> View XML -> copy 'X-Plex-Token' from URL)"
    Write-Host ""
    $plexToken = Read-Host "Enter your Plex token"
    if ([string]::IsNullOrWhiteSpace($plexToken)) {
        Write-Red "Plex token is required. Exiting."
        exit 1
    }
    Write-Green "OK Got it"
    Write-Host ""

    # --- Users ---
    Write-Yellow "Step 4: Plex Users"
    Write-Host ""
    Write-Host "Which Plex users should get recommendations?"
    Write-Host "(Comma-separated list of usernames)"
    Write-Host "Example: john, sarah, kids"
    Write-Host ""
    $usersList = Read-Host "Enter usernames"
    if ([string]::IsNullOrWhiteSpace($usersList)) {
        Write-Red "At least one user is required. Exiting."
        exit 1
    }
    Write-Green "OK Got it"
    Write-Host ""

    # --- Library Names ---
    Write-Yellow "Step 5: Library Names"
    Write-Host ""
    $movieLib = Read-Host "Movie library name [Movies]"
    if ([string]::IsNullOrWhiteSpace($movieLib)) { $movieLib = "Movies" }
    $tvLib = Read-Host "TV library name [TV Shows]"
    if ([string]::IsNullOrWhiteSpace($tvLib)) { $tvLib = "TV Shows" }
    Write-Green "OK Got it"
    Write-Host ""

    # --- Write config.yml ---
    Write-Cyan "Creating config.yml..."

    $configContent = @"
# Plex Recommender Configuration
# Generated by setup wizard

plex:
  url: $plexUrl
  token: $plexToken
  movie_library: $movieLib
  tv_library: $tvLib

tmdb:
  api_key: $tmdbKey

users:
  list: $usersList

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
"@

    $configContent | Out-File -FilePath "config.yml" -Encoding UTF8
    Write-Green "OK config.yml created!"
    Write-Host ""
}

# ------------------------------------------------------------------------
# FIRST RUN DETECTION
# ------------------------------------------------------------------------
function Test-FirstRun {
    if (-not (Test-Path "config.yml")) {
        return $true
    }

    $configContent = Get-Content "config.yml" -Raw
    if ($configContent -match "YOUR_TMDB_API_KEY|YOUR_PLEX_TOKEN|your.*api.*key.*here|your.*token.*here") {
        return $true
    }

    return $false
}

# ------------------------------------------------------------------------
# SCHEDULED TASK SETUP (Windows equivalent of cron)
# ------------------------------------------------------------------------
function Show-ScheduledTaskInfo {
    Write-Host ""
    Write-Cyan "=== What is a Scheduled Task? ==="
    Write-Host ""
    Write-Host "A scheduled task runs automatically on your system."
    Write-Host "For this project, it would:"
    Write-Host "  - Run once per day (default: 3 AM)"
    Write-Host "  - Analyze everyone's watch history"
    Write-Host "  - Update recommendations automatically"
    Write-Host "  - You never have to remember to run it"
    Write-Host ""
    Write-Host "It's completely optional - you can always run .\run.ps1 manually instead."
    Write-Host ""
    Read-Host "Press Enter to continue"
}

function Setup-ScheduledTask {
    $taskName = "PlexRecommender"

    # Check if task already exists
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Write-Green "OK Scheduled task already configured"
        return
    }

    try {
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$ScriptDir\run.ps1`"" -WorkingDirectory $ScriptDir
        $trigger = New-ScheduledTaskTrigger -Daily -At 3am
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Daily Plex Recommendations" | Out-Null

        Write-Green "OK Scheduled task configured - recommendations will run daily at 3 AM"
    } catch {
        Write-Red "X Failed to set up scheduled task"
        Write-Host ""
        Write-Host "You may need to run PowerShell as Administrator, or set it up manually:"
        Write-Host "  1. Open Task Scheduler"
        Write-Host "  2. Create a new task to run: powershell.exe -File `"$ScriptDir\run.ps1`""
        Write-Host ""
    }
}

function Setup-Schedule {
    Write-Host ""
    Write-Cyan "=== Scheduled Task Setup ==="
    Write-Host ""
    Write-Host "Would you like to set up automatic daily recommendations?"
    Write-Host ""
    Write-Host "  1) Yes - run daily at 3 AM"
    Write-Host "  2) No - I'll run manually"
    Write-Host "  3) More info - what is a scheduled task?"
    Write-Host ""
    $choice = Read-Host "Enter choice (1/2/3)"

    switch ($choice) {
        "1" { Setup-ScheduledTask }
        "2" { Write-Host "Skipping scheduled task setup. Run .\run.ps1 manually whenever you want to update." }
        "3" { Show-ScheduledTaskInfo; Setup-Schedule }
        default { Write-Host "Invalid choice. Skipping scheduled task setup." }
    }
}

# ------------------------------------------------------------------------
# MAIN EXECUTION
# ------------------------------------------------------------------------
function Main {
    Write-Cyan "==============================================="
    Write-Cyan "    Plex Recommendation System"
    Write-Cyan "==============================================="
    Write-Host ""

    # Step 1: Check/install dependencies
    $pythonCmd = Check-Dependencies

    # Step 2: Check for updates (if enabled)
    Check-ForUpdates -pythonCmd $pythonCmd

    # Step 3: First run setup
    $isFirstRun = Test-FirstRun
    if ($isFirstRun) {
        Start-SetupWizard
    }

    # Step 4: Create logs directory
    if (-not (Test-Path "logs")) {
        New-Item -ItemType Directory -Path "logs" | Out-Null
    }

    # Step 5: Run recommendations
    Write-Cyan "=== Running Recommendations ==="
    Write-Host ""

    Write-Yellow "Step 1/2: Movie recommendations..."
    & $pythonCmd recommenders/movie.py $DebugFlag
    if ($LASTEXITCODE -eq 0) {
        Write-Green "OK Movie recommendations complete"
    } else {
        Write-Red "X Movie recommendations failed"
        exit 1
    }
    Write-Host ""

    Write-Yellow "Step 2/2: TV recommendations..."
    & $pythonCmd recommenders/tv.py $DebugFlag
    if ($LASTEXITCODE -eq 0) {
        Write-Green "OK TV recommendations complete"
    } else {
        Write-Red "X TV recommendations failed"
        exit 1
    }
    Write-Host ""

    # Step 6: Generate external recommendations (watchlist)
    $configContent = Get-Content "config.yml" -Raw
    if ($configContent -match "external_recommendations:[\s\S]*?enabled:\s*true") {
        Write-Cyan "=== Generating External Watchlists ==="
        & $pythonCmd recommenders/external.py
        if ($LASTEXITCODE -eq 0) {
            Write-Green "OK External watchlists generated"
        } else {
            Write-Yellow "! External watchlist generation failed (non-fatal)"
        }
        Write-Host ""
    }

    # Step 7: Scheduled task setup (first run only)
    if ($isFirstRun) {
        Setup-Schedule
    }

    Write-Host ""
    Write-Green "==============================================="
    Write-Green "           All Done!"
    Write-Green "==============================================="
    Write-Host ""
    Write-Host "Your recommendations are ready!"
    Write-Host "Check your Plex library for updated collections."
    Write-Host ""
}

# Run main function
Main
