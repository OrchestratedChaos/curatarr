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

    if (Test-Path "config/config.yml") {
        $autoUpdate = & $pythonCmd -c "import yaml; c=yaml.safe_load(open('config/config.yml')); print(c.get('general', {}).get('auto_update', False))" 2>$null

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
    param($pythonCmd)

    Write-Cyan "=== Curatarr Setup Wizard ==="
    Write-Host ""
    Write-Host "Let's get you set up! I'll walk you through the configuration."
    Write-Host ""

    # Ensure config directory exists
    if (-not (Test-Path "config")) {
        New-Item -ItemType Directory -Path "config" | Out-Null
    }

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

    # Try to detect admin username from Plex
    $adminUser = ""
    try {
        $adminUser = & $pythonCmd -c @"
import sys
sys.path.insert(0, '.')
try:
    from plexapi.myplex import MyPlexAccount
    account = MyPlexAccount(token='$plexToken')
    print(account.username)
except:
    print('')
"@ 2>$null
    } catch {}

    # --- Huntarr: Missing Collection Movies ---
    Write-Yellow "Huntarr: Missing Collection Movies"
    Write-Host ""
    Write-Host "Huntarr finds missing movies from collections you've started."
    Write-Host "Example: You have 2 of 3 John Wick movies - it'll show the missing one."
    Write-Host ""
    $enableHuntarr = Read-Host "Enable Huntarr? (Y/n)"
    if ($enableHuntarr -match "^[Nn]$") {
        $huntarrEnabled = "false"
        Write-Yellow "Huntarr disabled (can use --huntarr flag to run manually)"
    } else {
        $huntarrEnabled = "true"
        Write-Green "OK Huntarr enabled"
    }
    Write-Host ""

    # --- Optional: Trakt Integration ---
    Write-Yellow "Step 6: Trakt Integration (Optional)"
    Write-Host ""
    Write-Host "Trakt syncs your recommendations to Trakt.tv lists"
    Write-Host "and can exclude items already on your Trakt watchlist."
    Write-Host ""
    $enableTrakt = Read-Host "Enable Trakt integration? (y/N)"

    $traktEnabled = "false"
    $traktClientId = ""
    $traktClientSecret = ""
    $traktAccessToken = ""
    $traktRefreshToken = ""
    $traktAutoSync = "false"
    $traktUserMode = "mapping"
    $traktPlexUsers = "[]"

    if ($enableTrakt -match "^[Yy]") {
        Write-Host ""
        Write-Cyan "Creating a Trakt API application:"
        Write-Host "1. Go to: https://trakt.tv/oauth/applications/new"
        Write-Host "2. Name: Curatarr"
        Write-Host "3. Redirect URI: urn:ietf:wg:oauth:2.0:oob"
        Write-Host "4. Check all permissions"
        Write-Host "5. Save and copy the Client ID and Client Secret"
        Write-Host ""
        $traktClientId = Read-Host "Enter your Trakt Client ID"
        $traktClientSecret = Read-Host "Enter your Trakt Client Secret"

        if ($traktClientId -and $traktClientSecret) {
            $traktEnabled = "true"
            Write-Green "OK Trakt credentials received"
            Write-Host ""
            Write-Cyan "Authenticating with Trakt..."

            # Get device code
            $traktAuthResult = & $pythonCmd -c @"
import sys
sys.path.insert(0, '.')
from utils.trakt import TraktClient
try:
    client = TraktClient('$traktClientId', '$traktClientSecret')
    device_info = client.get_device_code()
    print('URL:' + device_info['verification_url'])
    print('CODE:' + device_info['user_code'])
    print('DEVICE:' + device_info['device_code'])
except Exception as e:
    print('ERROR:' + str(e))
"@ 2>$null

            $traktUrl = ($traktAuthResult | Where-Object { $_ -match "^URL:" }) -replace "^URL:", ""
            $traktCode = ($traktAuthResult | Where-Object { $_ -match "^CODE:" }) -replace "^CODE:", ""
            $traktDevice = ($traktAuthResult | Where-Object { $_ -match "^DEVICE:" }) -replace "^DEVICE:", ""
            $traktError = ($traktAuthResult | Where-Object { $_ -match "^ERROR:" }) -replace "^ERROR:", ""

            if ($traktError) {
                Write-Red "Failed to get device code: $traktError"
                Write-Yellow "You can authenticate later with: python utils/trakt_auth.py"
            } elseif ($traktCode) {
                Write-Host ""
                Write-Host "1. Go to: " -NoNewline; Write-Cyan $traktUrl
                Write-Host "2. Enter code: " -NoNewline; Write-Yellow $traktCode
                Write-Host ""
                Read-Host "Press Enter after you've approved on Trakt..."

                # Poll for token
                $traktTokens = & $pythonCmd -c @"
import sys
sys.path.insert(0, '.')
from utils.trakt import TraktClient
client = TraktClient('$traktClientId', '$traktClientSecret')
success = client.poll_for_token('$traktDevice', interval=1, expires_in=30)
if success:
    print('ACCESS:' + client.access_token)
    print('REFRESH:' + client.refresh_token)
else:
    print('FAILED')
"@ 2>$null

                $traktAccessToken = ($traktTokens | Where-Object { $_ -match "^ACCESS:" }) -replace "^ACCESS:", ""
                $traktRefreshToken = ($traktTokens | Where-Object { $_ -match "^REFRESH:" }) -replace "^REFRESH:", ""

                if ($traktAccessToken) {
                    Write-Green "OK Trakt authenticated!"
                } else {
                    Write-Yellow "Authentication not completed. You can retry later with: python utils/trakt_auth.py"
                }
            }

            # Trakt export configuration
            Write-Host ""
            Write-Yellow "Trakt Export Configuration"
            Write-Host ""
            Write-Red "IMPORTANT: " -NoNewline
            Write-Host "Trakt export syncs recommendations to YOUR personal Trakt account."
            Write-Host ""
            Write-Host "Which Plex users' recommendations should be exported to YOUR Trakt?"
            Write-Host ""
            if ($adminUser) {
                Write-Host "  1) Just me (admin: $adminUser) - RECOMMENDED"
            } else {
                Write-Host "  1) Just me (admin) - enter username manually"
            }
            Write-Host "  2) All users - exports everyone's recommendations to your Trakt"
            Write-Host "  3) Skip - I'll configure manually in config/trakt.yml"
            Write-Host ""
            $traktExportChoice = Read-Host "Choose [1/2/3]"

            switch ($traktExportChoice) {
                "1" {
                    if ($adminUser) {
                        $traktPlexUser = $adminUser
                    } else {
                        $traktPlexUser = Read-Host "Enter your Plex username"
                    }
                    if ($traktPlexUser) {
                        $traktPlexUsers = "[`"$traktPlexUser`"]"
                        $traktUserMode = "mapping"
                        $enableAutoSync = Read-Host "Auto-sync to Trakt on each run? (y/N)"
                        if ($enableAutoSync -match "^[Yy]") {
                            $traktAutoSync = "true"
                            Write-Green "OK Auto-sync enabled for: $traktPlexUser"
                        } else {
                            Write-Yellow "Auto-sync disabled. Use HTML export button instead."
                        }
                    }
                }
                "2" {
                    $traktUserMode = "per_user"
                    $traktPlexUsers = "[]"
                    Write-Yellow "Warning: This exports ALL Plex users' data to your Trakt account."
                    $enableAutoSync = Read-Host "Auto-sync to Trakt on each run? (y/N)"
                    if ($enableAutoSync -match "^[Yy]") {
                        $traktAutoSync = "true"
                        Write-Green "OK Auto-sync enabled for all users"
                    }
                }
                default {
                    Write-Yellow "Skipping. Configure trakt.export in config/trakt.yml later."
                }
            }
        } else {
            Write-Yellow "Skipping Trakt (credentials not provided)"
        }
    } else {
        Write-Yellow "Skipping Trakt (can be enabled later in config/trakt.yml)"
    }
    Write-Host ""

    # --- Optional: Sonarr Integration ---
    Write-Yellow "Step 7: Sonarr Integration (Optional)"
    Write-Host ""
    Write-Host "Sonarr can auto-add recommended TV shows to your download queue."
    Write-Host ""
    $enableSonarr = Read-Host "Enable Sonarr integration? (y/N)"

    $sonarrEnabled = "false"
    $sonarrUrl = ""
    $sonarrApiKey = ""
    $sonarrRootFolder = ""
    $sonarrQualityProfile = ""
    $sonarrAutoSync = "false"
    $sonarrUserMode = "mapping"
    $sonarrPlexUsers = "[]"

    if ($enableSonarr -match "^[Yy]") {
        Write-Host ""
        Write-Host "Enter your Sonarr connection details:"
        Write-Host "(Find API key in Sonarr: Settings -> General -> API Key)"
        Write-Host ""
        $sonarrUrl = Read-Host "Sonarr URL (e.g., http://localhost:8989)"
        $sonarrApiKey = Read-Host "Sonarr API Key"

        if ($sonarrUrl -and $sonarrApiKey) {
            Write-Host ""
            Write-Cyan "Testing Sonarr connection..."

            $sonarrTest = & $pythonCmd -c @"
import sys
sys.path.insert(0, '.')
try:
    from utils.sonarr import SonarrClient
    client = SonarrClient('$sonarrUrl', '$sonarrApiKey')
    client.test_connection()
    print('OK')
    profiles = client.get_quality_profiles()
    for p in profiles:
        print(f'PROFILE:{p["id"]}:{p["name"]}')
    folders = client.get_root_folders()
    for f in folders:
        print(f'FOLDER:{f["path"]}')
except Exception as e:
    print(f'ERROR:{e}')
"@ 2>$null

            if ($sonarrTest -match "^OK") {
                Write-Green "OK Connected to Sonarr!"
                $sonarrEnabled = "true"
                Write-Host ""

                # Get profiles and folders
                $profiles = @()
                $folders = @()
                foreach ($line in $sonarrTest) {
                    if ($line -match "^PROFILE:\d+:(.+)$") { $profiles += $Matches[1] }
                    if ($line -match "^FOLDER:(.+)$") { $folders += $Matches[1] }
                }

                if ($profiles.Count -gt 0) {
                    Write-Host "Available quality profiles:"
                    for ($i = 0; $i -lt $profiles.Count; $i++) {
                        Write-Host "  $($i+1)) $($profiles[$i])"
                    }
                    $profileChoice = Read-Host "Choose quality profile [1]"
                    if ([string]::IsNullOrWhiteSpace($profileChoice)) { $profileChoice = "1" }
                    $sonarrQualityProfile = $profiles[[int]$profileChoice - 1]
                    Write-Green "OK Using: $sonarrQualityProfile"
                }

                if ($folders.Count -gt 0) {
                    Write-Host ""
                    Write-Host "Available root folders:"
                    for ($i = 0; $i -lt $folders.Count; $i++) {
                        Write-Host "  $($i+1)) $($folders[$i])"
                    }
                    $folderChoice = Read-Host "Choose root folder [1]"
                    if ([string]::IsNullOrWhiteSpace($folderChoice)) { $folderChoice = "1" }
                    $sonarrRootFolder = $folders[[int]$folderChoice - 1]
                    Write-Green "OK Using: $sonarrRootFolder"
                }

                Write-Host ""
                Write-Host "Which Plex user's TV recommendations should go to Sonarr?"
                Write-Host "  1) Just mine - only YOUR recommendations (recommended)"
                Write-Host "  2) All users - everyone's recommendations"
                Write-Host "  3) Skip for now - configure later"
                Write-Host ""
                $sonarrUserChoice = Read-Host "Choose [1/2/3]"

                switch ($sonarrUserChoice) {
                    "1" {
                        if ($adminUser) { $sonarrPlexUser = $adminUser }
                        else { $sonarrPlexUser = Read-Host "Enter your Plex username" }
                        if ($sonarrPlexUser) {
                            $sonarrPlexUsers = "[`"$sonarrPlexUser`"]"
                            $sonarrUserMode = "mapping"
                            $enableAuto = Read-Host "Auto-add to Sonarr on each run? (y/N)"
                            if ($enableAuto -match "^[Yy]") {
                                $sonarrAutoSync = "true"
                                Write-Green "OK Auto-sync enabled for: $sonarrPlexUser"
                            }
                        }
                    }
                    "2" {
                        $sonarrUserMode = "combined"
                        $sonarrPlexUsers = "[]"
                        Write-Yellow "Warning: This adds ALL Plex users' recommendations to Sonarr."
                        $enableAuto = Read-Host "Auto-add to Sonarr on each run? (y/N)"
                        if ($enableAuto -match "^[Yy]") { $sonarrAutoSync = "true" }
                    }
                    default { Write-Yellow "Skipping. Configure sonarr.yml later." }
                }
            } else {
                $sonarrError = ($sonarrTest | Where-Object { $_ -match "^ERROR:" }) -replace "^ERROR:", ""
                Write-Red "Could not connect to Sonarr: $sonarrError"
                Write-Yellow "Check your URL and API key, then configure sonarr.yml manually."
            }
        } else {
            Write-Yellow "Skipping Sonarr (credentials not provided)"
        }
    } else {
        Write-Yellow "Skipping Sonarr (can be enabled later in config/sonarr.yml)"
    }
    Write-Host ""

    # --- Optional: Radarr Integration ---
    Write-Yellow "Step 8: Radarr Integration (Optional)"
    Write-Host ""
    Write-Host "Radarr can auto-add recommended movies to your download queue."
    Write-Host ""
    $enableRadarr = Read-Host "Enable Radarr integration? (y/N)"

    $radarrEnabled = "false"
    $radarrUrl = ""
    $radarrApiKey = ""
    $radarrRootFolder = ""
    $radarrQualityProfile = ""
    $radarrAutoSync = "false"
    $radarrUserMode = "mapping"
    $radarrPlexUsers = "[]"

    if ($enableRadarr -match "^[Yy]") {
        Write-Host ""
        Write-Host "Enter your Radarr connection details:"
        Write-Host "(Find API key in Radarr: Settings -> General -> API Key)"
        Write-Host ""
        $radarrUrl = Read-Host "Radarr URL (e.g., http://localhost:7878)"
        $radarrApiKey = Read-Host "Radarr API Key"

        if ($radarrUrl -and $radarrApiKey) {
            Write-Host ""
            Write-Cyan "Testing Radarr connection..."

            $radarrTest = & $pythonCmd -c @"
import sys
sys.path.insert(0, '.')
try:
    from utils.radarr import RadarrClient
    client = RadarrClient('$radarrUrl', '$radarrApiKey')
    client.test_connection()
    print('OK')
    profiles = client.get_quality_profiles()
    for p in profiles:
        print(f'PROFILE:{p["id"]}:{p["name"]}')
    folders = client.get_root_folders()
    for f in folders:
        print(f'FOLDER:{f["path"]}')
except Exception as e:
    print(f'ERROR:{e}')
"@ 2>$null

            if ($radarrTest -match "^OK") {
                Write-Green "OK Connected to Radarr!"
                $radarrEnabled = "true"
                Write-Host ""

                $profiles = @()
                $folders = @()
                foreach ($line in $radarrTest) {
                    if ($line -match "^PROFILE:\d+:(.+)$") { $profiles += $Matches[1] }
                    if ($line -match "^FOLDER:(.+)$") { $folders += $Matches[1] }
                }

                if ($profiles.Count -gt 0) {
                    Write-Host "Available quality profiles:"
                    for ($i = 0; $i -lt $profiles.Count; $i++) {
                        Write-Host "  $($i+1)) $($profiles[$i])"
                    }
                    $profileChoice = Read-Host "Choose quality profile [1]"
                    if ([string]::IsNullOrWhiteSpace($profileChoice)) { $profileChoice = "1" }
                    $radarrQualityProfile = $profiles[[int]$profileChoice - 1]
                    Write-Green "OK Using: $radarrQualityProfile"
                }

                if ($folders.Count -gt 0) {
                    Write-Host ""
                    Write-Host "Available root folders:"
                    for ($i = 0; $i -lt $folders.Count; $i++) {
                        Write-Host "  $($i+1)) $($folders[$i])"
                    }
                    $folderChoice = Read-Host "Choose root folder [1]"
                    if ([string]::IsNullOrWhiteSpace($folderChoice)) { $folderChoice = "1" }
                    $radarrRootFolder = $folders[[int]$folderChoice - 1]
                    Write-Green "OK Using: $radarrRootFolder"
                }

                Write-Host ""
                Write-Host "Which Plex user's movie recommendations should go to Radarr?"
                Write-Host "  1) Just mine - only YOUR recommendations (recommended)"
                Write-Host "  2) All users - everyone's recommendations"
                Write-Host "  3) Skip for now - configure later"
                Write-Host ""
                $radarrUserChoice = Read-Host "Choose [1/2/3]"

                switch ($radarrUserChoice) {
                    "1" {
                        if ($adminUser) { $radarrPlexUser = $adminUser }
                        else { $radarrPlexUser = Read-Host "Enter your Plex username" }
                        if ($radarrPlexUser) {
                            $radarrPlexUsers = "[`"$radarrPlexUser`"]"
                            $radarrUserMode = "mapping"
                            $enableAuto = Read-Host "Auto-add to Radarr on each run? (y/N)"
                            if ($enableAuto -match "^[Yy]") {
                                $radarrAutoSync = "true"
                                Write-Green "OK Auto-sync enabled for: $radarrPlexUser"
                            }
                        }
                    }
                    "2" {
                        $radarrUserMode = "combined"
                        $radarrPlexUsers = "[]"
                        Write-Yellow "Warning: This adds ALL Plex users' recommendations to Radarr."
                        $enableAuto = Read-Host "Auto-add to Radarr on each run? (y/N)"
                        if ($enableAuto -match "^[Yy]") { $radarrAutoSync = "true" }
                    }
                    default { Write-Yellow "Skipping. Configure radarr.yml later." }
                }
            } else {
                $radarrError = ($radarrTest | Where-Object { $_ -match "^ERROR:" }) -replace "^ERROR:", ""
                Write-Red "Could not connect to Radarr: $radarrError"
                Write-Yellow "Check your URL and API key, then configure radarr.yml manually."
            }
        } else {
            Write-Yellow "Skipping Radarr (credentials not provided)"
        }
    } else {
        Write-Yellow "Skipping Radarr (can be enabled later in config/radarr.yml)"
    }
    Write-Host ""

    # --- Optional: MDBList Integration ---
    Write-Yellow "Step 9: MDBList Integration (Optional)"
    Write-Host ""
    Write-Host "MDBList can export recommendations to shareable lists."
    Write-Host "Lists can be imported into other apps like Kometa/PMM."
    Write-Host ""
    $enableMdblist = Read-Host "Enable MDBList integration? (y/N)"

    $mdblistEnabled = "false"
    $mdblistApiKey = ""
    $mdblistAutoSync = "false"
    $mdblistUserMode = "mapping"
    $mdblistPlexUsers = "[]"

    if ($enableMdblist -match "^[Yy]") {
        Write-Host ""
        Write-Host "Enter your MDBList API key:"
        Write-Cyan "Get it from: https://mdblist.com/preferences/"
        Write-Host ""
        $mdblistApiKey = Read-Host "MDBList API Key"

        if ($mdblistApiKey) {
            Write-Host ""
            Write-Cyan "Testing MDBList connection..."

            $mdblistTest = & $pythonCmd -c @"
import sys
sys.path.insert(0, '.')
try:
    from utils.mdblist import MDBListClient
    client = MDBListClient('$mdblistApiKey')
    client.test_connection()
    print('OK')
except Exception as e:
    print(f'ERROR:{e}')
"@ 2>$null

            if ($mdblistTest -match "^OK") {
                Write-Green "OK Connected to MDBList!"
                $mdblistEnabled = "true"
                Write-Host ""

                Write-Host "Which Plex user's recommendations should go to MDBList?"
                Write-Host "  1) Just mine - only YOUR recommendations (recommended)"
                Write-Host "  2) All users - everyone's recommendations (combined list)"
                Write-Host "  3) Per-user - separate list for each user"
                Write-Host "  4) Skip for now - configure later"
                Write-Host ""
                $mdblistUserChoice = Read-Host "Choose [1/2/3/4]"

                switch ($mdblistUserChoice) {
                    "1" {
                        if ($adminUser) { $mdblistPlexUser = $adminUser }
                        else { $mdblistPlexUser = Read-Host "Enter your Plex username" }
                        if ($mdblistPlexUser) {
                            $mdblistPlexUsers = "[`"$mdblistPlexUser`"]"
                            $mdblistUserMode = "mapping"
                            $enableAuto = Read-Host "Auto-export to MDBList on each run? (y/N)"
                            if ($enableAuto -match "^[Yy]") {
                                $mdblistAutoSync = "true"
                                Write-Green "OK Auto-sync enabled for: $mdblistPlexUser"
                            }
                        }
                    }
                    "2" {
                        $mdblistUserMode = "combined"
                        $mdblistPlexUsers = "[]"
                        $enableAuto = Read-Host "Auto-export to MDBList on each run? (y/N)"
                        if ($enableAuto -match "^[Yy]") { $mdblistAutoSync = "true" }
                    }
                    "3" {
                        $mdblistUserMode = "per_user"
                        $mdblistPlexUsers = "[]"
                        $enableAuto = Read-Host "Auto-export to MDBList on each run? (y/N)"
                        if ($enableAuto -match "^[Yy]") { $mdblistAutoSync = "true" }
                    }
                    default { Write-Yellow "Skipping. Configure mdblist.yml later." }
                }
            } else {
                $mdblistError = ($mdblistTest | Where-Object { $_ -match "^ERROR:" }) -replace "^ERROR:", ""
                Write-Red "Could not connect to MDBList: $mdblistError"
                Write-Yellow "Check your API key, then configure mdblist.yml manually."
            }
        } else {
            Write-Yellow "Skipping MDBList (API key not provided)"
        }
    } else {
        Write-Yellow "Skipping MDBList (can be enabled later in config/mdblist.yml)"
    }
    Write-Host ""

    # --- Optional: Simkl Integration ---
    Write-Yellow "Step 10: Simkl Integration (Optional)"
    Write-Host ""
    Write-Host "Simkl tracks anime/TV/movies with excellent anime database."
    Write-Host "Great for anime fans - enhances recommendations with Simkl data."
    Write-Host ""
    $enableSimkl = Read-Host "Enable Simkl integration? (y/N)"

    $simklEnabled = "false"
    $simklClientId = ""
    $simklAccessToken = ""
    $simklAutoSync = "false"
    $simklUserMode = "mapping"
    $simklPlexUsers = "[]"

    if ($enableSimkl -match "^[Yy]") {
        Write-Host ""
        Write-Host "To use Simkl, you need to create an app:"
        Write-Host "1. Go to https://simkl.com/settings/developer/"
        Write-Host "2. Click 'New Application'"
        Write-Host "3. Enter any name (e.g., 'Curatarr')"
        Write-Host "4. Copy the Client ID"
        Write-Host ""
        $simklClientId = Read-Host "Simkl Client ID"

        if ($simklClientId) {
            Write-Host ""
            Write-Cyan "Getting Simkl PIN code..."

            $simklPin = & $pythonCmd -c @"
import sys
sys.path.insert(0, '.')
try:
    from utils.simkl import SimklClient
    client = SimklClient('$simklClientId')
    pin_data = client.get_pin_code()
    print(f"CODE:{pin_data['user_code']}")
    print(f"URL:{pin_data['verification_url']}")
except Exception as e:
    print(f'ERROR:{e}')
"@ 2>$null

            $pinCode = ($simklPin | Where-Object { $_ -match "^CODE:" }) -replace "^CODE:", ""
            $pinUrl = ($simklPin | Where-Object { $_ -match "^URL:" }) -replace "^URL:", ""
            $pinError = ($simklPin | Where-Object { $_ -match "^ERROR:" }) -replace "^ERROR:", ""

            if ($pinCode) {
                Write-Host ""
                Write-Cyan "To authorize Curatarr:"
                Write-Host "1. Go to: $pinUrl"
                Write-Host "2. Enter this code: " -NoNewline; Write-Yellow $pinCode
                Write-Host ""
                Read-Host "Press Enter after you've authorized the app..."

                Write-Cyan "Checking authorization..."
                $simklAuth = & $pythonCmd -c @"
import sys
sys.path.insert(0, '.')
try:
    from utils.simkl import SimklClient
    client = SimklClient('$simklClientId')
    if client.poll_for_token('$pinCode', interval=2, expires_in=30):
        print(f'TOKEN:{client.access_token}')
    else:
        print('ERROR:Authorization timed out or was denied')
except Exception as e:
    print(f'ERROR:{e}')
"@ 2>$null

                $simklAccessToken = ($simklAuth | Where-Object { $_ -match "^TOKEN:" }) -replace "^TOKEN:", ""

                if ($simklAccessToken) {
                    $simklEnabled = "true"
                    Write-Green "OK Connected to Simkl!"
                    Write-Host ""

                    Write-Host "Which Plex user's recommendations should go to Simkl?"
                    Write-Host "  1) Just mine - only YOUR recommendations (recommended)"
                    Write-Host "  2) All users - everyone's recommendations"
                    Write-Host "  3) Skip for now - configure later"
                    Write-Host ""
                    $simklUserChoice = Read-Host "Choose [1/2/3]"

                    switch ($simklUserChoice) {
                        "1" {
                            if ($adminUser) { $simklPlexUser = $adminUser }
                            else { $simklPlexUser = Read-Host "Enter your Plex username" }
                            if ($simklPlexUser) {
                                $simklPlexUsers = "[`"$simklPlexUser`"]"
                                $simklUserMode = "mapping"
                                $enableAuto = Read-Host "Auto-export to Simkl on each run? (y/N)"
                                if ($enableAuto -match "^[Yy]") {
                                    $simklAutoSync = "true"
                                    Write-Green "OK Auto-sync enabled for: $simklPlexUser"
                                }
                            }
                        }
                        "2" {
                            $simklUserMode = "combined"
                            $simklPlexUsers = "[]"
                            $enableAuto = Read-Host "Auto-export to Simkl on each run? (y/N)"
                            if ($enableAuto -match "^[Yy]") { $simklAutoSync = "true" }
                        }
                        default { Write-Yellow "Skipping. Configure simkl.yml later." }
                    }
                } else {
                    $authError = ($simklAuth | Where-Object { $_ -match "^ERROR:" }) -replace "^ERROR:", ""
                    Write-Red "Authorization failed: $authError"
                    Write-Yellow "You can configure Simkl later in config/simkl.yml"
                }
            } else {
                Write-Red "Could not get PIN code: $pinError"
                Write-Yellow "Check your Client ID, then configure simkl.yml manually."
            }
        } else {
            Write-Yellow "Skipping Simkl (Client ID not provided)"
        }
    } else {
        Write-Yellow "Skipping Simkl (can be enabled later in config/simkl.yml)"
    }
    Write-Host ""

    # --- Write config/config.yml ---
    Write-Cyan "Creating config/config.yml..."

    $configContent = @"
# Curatarr Configuration
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
  plex_only: true
  auto_update: true
  log_retention_days: 7

# Huntarr: Find missing movies from your collections
huntarr: $huntarrEnabled
"@

    $configContent | Out-File -FilePath "config/config.yml" -Encoding UTF8
    Write-Green "OK config/config.yml created!"

    # --- Write trakt.yml if enabled ---
    if ($traktEnabled -eq "true") {
        Write-Cyan "Creating config/trakt.yml..."
        $traktConfig = @"
# Curatarr Trakt Configuration

enabled: true
client_id: $traktClientId
client_secret: $traktClientSecret
access_token: $traktAccessToken
refresh_token: $traktRefreshToken

export:
  enabled: true
  auto_sync: $traktAutoSync
  list_prefix: "Curatarr"
  user_mode: "$traktUserMode"
  plex_users: $traktPlexUsers

import:
  enabled: true
  exclude_watchlist: true
"@
        $traktConfig | Out-File -FilePath "config/trakt.yml" -Encoding UTF8
        Write-Green "OK config/trakt.yml created!"
    }

    # --- Write sonarr.yml if enabled ---
    if ($sonarrEnabled -eq "true") {
        Write-Cyan "Creating config/sonarr.yml..."
        $sonarrConfig = @"
# Curatarr Sonarr Configuration

enabled: true
url: $sonarrUrl
api_key: $sonarrApiKey

auto_sync: $sonarrAutoSync
user_mode: "$sonarrUserMode"
plex_users: $sonarrPlexUsers

root_folder: $sonarrRootFolder
quality_profile: $sonarrQualityProfile
series_type: standard
season_folder: true

tag: Curatarr
append_usernames: false

monitor: false
monitor_option: none
search_missing: false
"@
        $sonarrConfig | Out-File -FilePath "config/sonarr.yml" -Encoding UTF8
        Write-Green "OK config/sonarr.yml created!"
    }

    # --- Write radarr.yml if enabled ---
    if ($radarrEnabled -eq "true") {
        Write-Cyan "Creating config/radarr.yml..."
        $radarrConfig = @"
# Curatarr Radarr Configuration

enabled: true
url: $radarrUrl
api_key: $radarrApiKey

auto_sync: $radarrAutoSync
user_mode: "$radarrUserMode"
plex_users: $radarrPlexUsers

root_folder: $radarrRootFolder
quality_profile: $radarrQualityProfile
minimum_availability: released

tag: Curatarr
append_usernames: false

monitor: false
search_for_movie: false
"@
        $radarrConfig | Out-File -FilePath "config/radarr.yml" -Encoding UTF8
        Write-Green "OK config/radarr.yml created!"
    }

    # --- Write mdblist.yml if enabled ---
    if ($mdblistEnabled -eq "true") {
        Write-Cyan "Creating config/mdblist.yml..."
        $mdblistConfig = @"
# Curatarr MDBList Configuration

enabled: true
api_key: $mdblistApiKey

auto_sync: $mdblistAutoSync
user_mode: "$mdblistUserMode"
plex_users: $mdblistPlexUsers

list_prefix: "Curatarr"
replace_existing: true
"@
        $mdblistConfig | Out-File -FilePath "config/mdblist.yml" -Encoding UTF8
        Write-Green "OK config/mdblist.yml created!"
    }

    # --- Write simkl.yml if enabled ---
    if ($simklEnabled -eq "true") {
        Write-Cyan "Creating config/simkl.yml..."
        $simklConfig = @"
# Curatarr Simkl Configuration

enabled: true
client_id: $simklClientId
access_token: $simklAccessToken

import:
  enabled: true
  include_anime: true

discovery:
  enabled: true
  anime_focus: true
  include_tv: true
  include_movies: false

export:
  enabled: true
  auto_sync: $simklAutoSync
  user_mode: "$simklUserMode"
  plex_users: $simklPlexUsers
"@
        $simklConfig | Out-File -FilePath "config/simkl.yml" -Encoding UTF8
        Write-Green "OK config/simkl.yml created!"
    }

    Write-Host ""
}

# ------------------------------------------------------------------------
# FIRST RUN DETECTION
# ------------------------------------------------------------------------
function Test-FirstRun {
    if (-not (Test-Path "config/config.yml")) {
        return $true
    }

    $configContent = Get-Content "config/config.yml" -Raw
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
    $taskName = "Curatarr"

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

        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Daily Curatarr Recommendations" | Out-Null

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
    Write-Cyan "              Curatarr"
    Write-Cyan "==============================================="
    Write-Host ""

    # Step 1: Check/install dependencies
    $pythonCmd = Check-Dependencies

    # Step 2: Check for updates (if enabled)
    Check-ForUpdates -pythonCmd $pythonCmd

    # Step 3: First run setup
    $isFirstRun = Test-FirstRun
    if ($isFirstRun) {
        Start-SetupWizard -pythonCmd $pythonCmd
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
    Write-Cyan "=== Generating External Watchlists ==="
    & $pythonCmd recommenders/external.py
    if ($LASTEXITCODE -eq 0) {
        Write-Green "OK External watchlists generated"
    } else {
        Write-Yellow "! External watchlist generation had issues (non-fatal)"
    }
    Write-Host ""

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

    # Show link to external watchlist HTML if it exists
    $watchlistFile = Join-Path $ScriptRoot "recommendations\external\watchlist.html"
    if (Test-Path $watchlistFile) {
        Write-Host ""
        Write-Host "View external watchlist: " -NoNewline
        Write-Cyan "file:///$($watchlistFile -replace '\\', '/')"
    }

    Write-Host ""
    Write-Host "Check your Plex library for updated collections."
    Write-Host ""
}

# Run main function
Main
