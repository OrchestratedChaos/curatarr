# Curatarr Web UI launcher (Windows).
# Starts the local-only (127.0.0.1) Flask dashboard and opens it in
# your browser once it's listening. See web/app.py for the app itself.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found. Install Python 3.10+ first."
    exit 1
}

# Python floor gate - same rationale as run.ps1's Check-Dependencies:
# read the floor back out of requirements.lock's own header instead of a
# second hardcoded copy, so a version bump there can't silently drift
# out of sync with this script.
if (Test-Path "requirements.lock") {
    $pythonVersionRaw = & python --version 2>&1
    $pythonVersionNumber = [regex]::Match("$pythonVersionRaw", '(\d+\.\d+\.\d+)').Groups[1].Value
    $lockContent = Get-Content "requirements.lock" -Raw
    $floorMatch = [regex]::Match($lockContent, '--python-version (\d+\.\d+)')
    if ($floorMatch.Success -and $pythonVersionNumber) {
        $requiredPython = [version]$floorMatch.Groups[1].Value
        $currentPython = [version]$pythonVersionNumber
        if ($currentPython -lt $requiredPython) {
            Write-Error "Python $pythonVersionNumber found, but curatarr's web UI requires Python $requiredPython+. Upgrade Python, or use a standalone curatarr binary instead (bundles its own Python + UI deps): https://github.com/OrchestratedChaos/curatarr/releases"
            exit 1
        }
    }
}

# Core deps (plexapi/requests/pyyaml - requirements.txt) plus the web
# UI's own deps (flask/ruamel.yaml - requirements-ui.txt). Prefer the
# hashed locks when present, same rationale as run.ps1; fall back to
# the plain pinned files (still reproducible, just unverified) otherwise.
function Install-UiDeps {
    if ((Test-Path "requirements.lock") -and (Test-Path "requirements-ui.lock")) {
        pip install --require-hashes -r requirements.lock -r requirements-ui.lock --quiet
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warning "Hash-verified install failed (hash/platform mismatch?) - falling back to a normal pinned install (no hash verification) for this run."
    }
    pip install -r requirements.txt -r requirements-ui.txt --quiet
}

python -c "import flask, ruamel.yaml" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing web UI dependencies..."
    Install-UiDeps
}

if (-not $env:CURATARR_UI_PORT) {
    $env:CURATARR_UI_PORT = "8787"
}

Write-Host "Starting Curatarr web UI on http://127.0.0.1:$($env:CURATARR_UI_PORT) (Ctrl+C to stop) ..."
python -m web.app
