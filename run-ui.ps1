# Curatarr Web UI launcher (Windows).
# Starts the local-only (127.0.0.1) Flask dashboard and opens it in
# your browser once it's listening. See web/app.py for the app itself.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found. Install Python 3.8+ first."
    exit 1
}

python -c "import flask" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing web UI dependencies..."
    pip install -r requirements.txt --quiet
}

if (-not $env:CURATARR_UI_PORT) {
    $env:CURATARR_UI_PORT = "8787"
}

Write-Host "Starting Curatarr web UI on http://127.0.0.1:$($env:CURATARR_UI_PORT) (Ctrl+C to stop) ..."
python -m web.app
