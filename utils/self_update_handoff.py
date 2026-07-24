"""
External-script hand-off for the web UI's binary self-update swap+relaunch.

Why this exists - the in-frozen-process relaunch was the root fragility
------------------------------------------------------------------
Earlier v2.8.29 iterations had the self-update worker (itself a frozen
curatarr.exe instance, running as `--self-update-worker`) swap the
binary in-process and then directly `subprocess.Popen()` the new exe as
the relaunched UI - see git history for
`utils.self_update.relaunch_binary` (removed). Extensive real
end-to-end testing on Windows kept reproducing intermittent crashes in
that relaunched process - a hard PyInstaller bootloader error
("Failed to execute script" / `pyi_rth_multiprocessing` failing to find
`base_library.zip`) or a plain Python exception
(`importlib.metadata.PackageNotFoundError: No package metadata was
found for werkzeug`) - both consistent with the relaunched process's
PyInstaller onefile extraction directory (`_MEIPASS`) being in some
inconsistent state: inherited/shared with the process tree it was
spawned from, raced against that tree's own teardown, or otherwise not
a clean, from-scratch extraction. Multiple targeted fixes (stripping
_MEIPASS2, giving the worker and the relaunch each their own fresh
TEMP/TMP, bundling missing package metadata via curatarr.spec's
copy_metadata) each closed off a real, confirmed issue, but the
relaunch remained unreliable - because the fundamental problem is
architectural: a frozen PyInstaller process spawning ANOTHER instance
of itself is not a reliably clean operation to begin with, regardless
of how much its environment is sanitized.

What IS reliable, confirmed repeatedly via real end-to-end testing: a
completely FRESH, TOP-LEVEL launch of curatarr.exe - started by
something that is NOT itself a frozen PyInstaller process - always
boots cleanly. So that's the only thing this module ever does.

The design
------------------------------------------------------------------
The self-update worker (web/update_apply.py's _run_worker, frozen
branch) does the CRYPTOGRAPHICALLY SENSITIVE work itself (needs Python
+ the `cryptography` package - see utils/self_update.py's
download_and_verify_update): download the platform asset plus
SHA256SUMS.txt/.sig, verify the pinned-key signature and the resulting
hash. It does NOT do the swap or the relaunch. Instead, once a
verified binary is sitting on disk, this module:

1. Writes a small, PLAIN script - PowerShell (`.ps1`) on Windows,
   POSIX `sh` everywhere else - to a brand-new temp directory that has
   NO relationship whatsoever to any PyInstaller onefile extraction
   directory (this process's own or anyone else's).
2. Launches that script as a DETACHED, TOP-LEVEL process with a
   sanitized environment (utils.self_update.sanitize_frozen_relaunch_env -
   strips PyInstaller's _MEIPASS2 hand-off variable, same defense as
   before, just applied to a process that has even less reason to need
   it: a plain shell interpreter, not another frozen Python).
3. Returns immediately. The worker process (and, shortly after, the
   web server process that spawned it) exits normally - see
   web/update_apply.py's _run_worker.

The script itself, running completely independently of anything
PyInstaller/frozen, then does the parts that actually touch the
filesystem and the process tree:
  - Poll until the OLD server's PID has fully exited (a second,
    defensive check - _run_worker already signals + waits for this
    itself before handing off, via the existing _shut_down_old_server).
  - Rename the current exe aside (`<name>.old`) and move the verified
    new binary into its place - same atomic-rename-then-move mechanics
    utils.self_update.swap_binary already used, just implemented in
    the script's own language since Python is no longer in the loop at
    this point.
  - Launch whatever's now at that path as a brand-new, independent,
    TOP-LEVEL process (this is the one launch that's actually reliable
    - see above).
  - Poll that new process's /healthz for the TARGET version. If it
    answers correctly within the timeout: delete the `.old` backup and
    self-delete the script - done. If not: kill it, restore `.old`
    back into place, relaunch THAT (the original, known-good binary)
    fresh, and self-delete - the user is never left without a working
    app, exactly the same fail-safe guarantee as every other layer of
    this feature.

Nothing here ever decides WHAT to trust - by the time this module is
involved, utils.self_update has already verified the binary's
signature and hash. This module's only job is HOW the already-trusted
bytes end up running, safely.
"""

import logging
import os
import subprocess
import sys
import tempfile
from typing import Optional

from .self_update import sanitize_frozen_relaunch_env

logger = logging.getLogger('curatarr')

# How long the hand-off script itself waits for things, in seconds -
# module-level constants (not buried in the script templates) so tests
# can assert on them without magic numbers.
HANDOFF_OLD_EXIT_TIMEOUT_SECONDS = 20
HANDOFF_HEALTH_TIMEOUT_SECONDS = 60
HANDOFF_POLL_INTERVAL_SECONDS = 0.5


def _windows_script_content() -> str:
    """PowerShell hand-off script content - see module docstring for
    the full sequence. Dynamic values (PIDs, paths, port, version) are
    NEVER interpolated into this template; they're passed as real
    PowerShell parameters (argv, via `-File script.ps1 -OldPid ... `)
    so subprocess.Popen's own argv-list quoting handles anything odd in
    a path (spaces, etc.) correctly - the same reasoning
    web/update_apply.py's existing run.ps1 invocations already rely on.
    Only the module-level timeout/poll-interval constants above (always
    literal ints/floats this code controls, never attacker-reachable)
    are substituted into the template text itself.
    """
    poll_ms = int(HANDOFF_POLL_INTERVAL_SECONDS * 1000)
    return f"""
param(
    [Parameter(Mandatory=$true)][int]$OldPid,
    [Parameter(Mandatory=$true)][string]$CurrentExePath,
    [Parameter(Mandatory=$true)][string]$NewAssetPath,
    [Parameter(Mandatory=$true)][int]$Port,
    [Parameter(Mandatory=$true)][string]$TargetVersion
)

$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

function Test-CuratarrHealthz {{
    param([int]$Port, [string]$Version)
    try {{
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2 -ErrorAction Stop
        return $resp.version -eq $Version
    }} catch {{
        return $false
    }}
}}

function Start-CuratarrDetached {{
    param([string]$ExePath, [int]$Port)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $ExePath
    $psi.UseShellExecute = $false
    $psi.EnvironmentVariables['CURATARR_UI_PORT'] = "$Port"
    $psi.EnvironmentVariables['CURATARR_SKIP_BROWSER_OPEN'] = '1'
    $psi.EnvironmentVariables.Remove('_MEIPASS2') | Out-Null
    return [System.Diagnostics.Process]::Start($psi)
}}

Write-Host "[handoff] starting - old pid=$OldPid target=127.0.0.1:$Port"

# 1) Defensive re-check: wait for the old server process to fully exit
#    (the worker already signaled + waited for this itself before
#    handing off - see web/update_apply.py's _shut_down_old_server).
$deadline = (Get-Date).AddSeconds({HANDOFF_OLD_EXIT_TIMEOUT_SECONDS})
while ((Get-Date) -lt $deadline) {{
    if (-not (Get-Process -Id $OldPid -ErrorAction SilentlyContinue)) {{ break }}
    Start-Sleep -Milliseconds {poll_ms}
}}
if (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) {{
    Write-Host "[handoff] old server never exited - leaving everything untouched"
    Remove-Item -Path $NewAssetPath -Force -ErrorAction SilentlyContinue
    exit 0
}}

# 2) Swap: current -> .old, verified new asset -> current.
$bakPath = "$CurrentExePath.old"
Remove-Item -Path $bakPath -Force -ErrorAction SilentlyContinue
$swapped = $false
try {{
    Rename-Item -Path $CurrentExePath -NewName (Split-Path -Leaf $bakPath) -ErrorAction Stop
    Move-Item -Path $NewAssetPath -Destination $CurrentExePath -Force -ErrorAction Stop
    $swapped = $true
    Write-Host "[handoff] swap complete: $CurrentExePath is now v$TargetVersion"
}} catch {{
    Write-Host "[handoff] swap FAILED: $_"
    if (-not (Test-Path $CurrentExePath) -and (Test-Path $bakPath)) {{
        Move-Item -Path $bakPath -Destination $CurrentExePath -Force -ErrorAction SilentlyContinue
    }}
    Remove-Item -Path $NewAssetPath -Force -ErrorAction SilentlyContinue
}}

# 3) Launch whatever's now at CurrentExePath - the new binary if the
#    swap succeeded, the untouched/restored original if it didn't - as
#    a brand-new, fully independent TOP-LEVEL process. This is the one
#    launch this whole design exists to make possible: a fresh
#    top-level start always does its own clean extraction, nothing
#    inherited from any frozen parent.
Write-Host "[handoff] launching $CurrentExePath fresh on port $Port..."
$newProc = Start-CuratarrDetached -ExePath $CurrentExePath -Port $Port

# 4) Only meaningful if the swap actually happened - poll for the NEW
#    version to answer healthz, and roll back if it never does.
if ($swapped) {{
    $healthy = $false
    $deadline2 = (Get-Date).AddSeconds({HANDOFF_HEALTH_TIMEOUT_SECONDS})
    while ((Get-Date) -lt $deadline2) {{
        if (Test-CuratarrHealthz -Port $Port -Version $TargetVersion) {{ $healthy = $true; break }}
        Start-Sleep -Milliseconds {poll_ms}
    }}

    if ($healthy) {{
        Write-Host "[handoff] v$TargetVersion confirmed healthy on port $Port"
        Remove-Item -Path $bakPath -Force -ErrorAction SilentlyContinue
    }} else {{
        Write-Host "[handoff] new binary never became healthy - rolling back to the previous binary"
        if ($newProc) {{ Stop-Process -Id $newProc.Id -Force -ErrorAction SilentlyContinue }}
        Start-Sleep -Milliseconds 500
        Remove-Item -Path $CurrentExePath -Force -ErrorAction SilentlyContinue
        Move-Item -Path $bakPath -Destination $CurrentExePath -Force -ErrorAction SilentlyContinue
        Start-CuratarrDetached -ExePath $CurrentExePath -Port $Port | Out-Null
        Write-Host "[handoff] rolled back and relaunched the previous binary"
    }}
}}

Write-Host "[handoff] done"
Remove-Item -Path $PSCommandPath -Force -ErrorAction SilentlyContinue
"""


def _posix_script_content() -> str:
    """POSIX `sh` hand-off script content - see module docstring and
    _windows_script_content's docstring (same "never interpolate
    dynamic values into the template" reasoning applies here; they
    arrive as $1/$2/... positional argv, not text substitution)."""
    old_exit_iterations = max(1, round(HANDOFF_OLD_EXIT_TIMEOUT_SECONDS / HANDOFF_POLL_INTERVAL_SECONDS))
    health_iterations = max(1, round(HANDOFF_HEALTH_TIMEOUT_SECONDS / HANDOFF_POLL_INTERVAL_SECONDS))
    return f"""#!/bin/sh
# curatarr self-update swap+relaunch hand-off script - see
# utils/self_update_handoff.py's module docstring. Deliberately plain
# POSIX sh: this process has no relationship to any PyInstaller onefile
# runtime at all.
set -u

OLD_PID="$1"
CURRENT_EXE="$2"
NEW_ASSET="$3"
PORT="$4"
TARGET_VERSION="$5"

BAK_PATH="${{CURRENT_EXE}}.old"

echo "[handoff] starting - old pid=$OLD_PID target=127.0.0.1:$PORT"

launch_detached() {{
    exe="$1"
    port="$2"
    env -u _MEIPASS2 CURATARR_UI_PORT="$port" CURATARR_SKIP_BROWSER_OPEN=1 "$exe" >/dev/null 2>&1 &
    echo $!
}}

# 1) Defensive re-check: wait for the old server process to fully exit
#    (the worker already signaled + waited for this itself before
#    handing off - see web/update_apply.py's _shut_down_old_server).
i=0
while [ "$i" -lt {old_exit_iterations} ]; do
    if ! kill -0 "$OLD_PID" 2>/dev/null; then
        break
    fi
    sleep {HANDOFF_POLL_INTERVAL_SECONDS}
    i=$((i + 1))
done

if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[handoff] old server never exited - leaving everything untouched"
    rm -f "$NEW_ASSET"
    exit 0
fi

# 2) Swap: current -> .old, verified new asset -> current.
SWAPPED=0
rm -f "$BAK_PATH"
if mv "$CURRENT_EXE" "$BAK_PATH" 2>/dev/null; then
    if mv "$NEW_ASSET" "$CURRENT_EXE" 2>/dev/null; then
        chmod +x "$CURRENT_EXE" 2>/dev/null
        SWAPPED=1
        echo "[handoff] swap complete: $CURRENT_EXE is now v$TARGET_VERSION"
    else
        echo "[handoff] swap FAILED (could not move verified asset into place) - rolling back rename"
        mv "$BAK_PATH" "$CURRENT_EXE" 2>/dev/null
        rm -f "$NEW_ASSET"
    fi
else
    echo "[handoff] swap FAILED (could not rename current exe aside)"
    rm -f "$NEW_ASSET"
fi

# 3) Launch whatever's now at CURRENT_EXE - the new binary if the swap
#    succeeded, the untouched/restored original if it didn't - as a
#    brand-new, fully independent TOP-LEVEL process.
echo "[handoff] launching $CURRENT_EXE fresh on port $PORT..."
NEW_PID=$(launch_detached "$CURRENT_EXE" "$PORT")

# 4) Only meaningful if the swap actually happened - poll for the NEW
#    version to answer healthz, and roll back if it never does.
if [ "$SWAPPED" -eq 1 ]; then
    HEALTHY=0
    i=0
    while [ "$i" -lt {health_iterations} ]; do
        RESP=$(curl -s -m 2 "http://127.0.0.1:${{PORT}}/healthz" 2>/dev/null)
        case "$RESP" in
            *"\\"${{TARGET_VERSION}}\\""*)
                HEALTHY=1
                break
                ;;
        esac
        sleep {HANDOFF_POLL_INTERVAL_SECONDS}
        i=$((i + 1))
    done

    if [ "$HEALTHY" -eq 1 ]; then
        echo "[handoff] v$TARGET_VERSION confirmed healthy on port $PORT"
        rm -f "$BAK_PATH"
    else
        echo "[handoff] new binary never became healthy - rolling back to the previous binary"
        kill "$NEW_PID" 2>/dev/null
        sleep 0.5
        rm -f "$CURRENT_EXE"
        mv "$BAK_PATH" "$CURRENT_EXE" 2>/dev/null
        launch_detached "$CURRENT_EXE" "$PORT" >/dev/null
        echo "[handoff] rolled back and relaunched the previous binary"
    fi
fi

echo "[handoff] done"
rm -f "$0"
"""


def _write_script(content: str) -> str:
    """Writes `content` to a brand-new, guaranteed-independent temp
    directory - deliberately NOT anywhere under this (frozen) process's
    own sys._MEIPASS, which gets torn down when this process exits
    (moments after launching the script - see module docstring)."""
    tmp_dir = tempfile.mkdtemp(prefix='curatarr-handoff-')
    suffix = '.ps1' if os.name == 'nt' else '.sh'
    script_path = os.path.join(tmp_dir, f'curatarr-swap{suffix}')
    with open(script_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    if os.name != 'nt':
        os.chmod(script_path, 0o755)
    return script_path


def write_and_launch_handoff_script(
    old_pid: int,
    current_exe_path: str,
    verified_asset_path: str,
    port: int,
    target_version: str,
) -> None:
    """
    Writes the platform-appropriate hand-off script (see module
    docstring) and launches it as a DETACHED, independent process with
    a sanitized environment, then returns immediately - the caller (the
    self-update worker) has nothing further to do; the script now owns
    the swap+relaunch (and its own rollback-on-failure) entirely.

    Raises nothing that the caller needs to distinguish - if writing or
    launching the script itself somehow fails, that's exactly as fatal
    as any other unexpected worker failure (see
    web/update_apply.py's _run_worker, which already wraps its whole
    frozen apply/hand-off path in a catch-all for exactly this reason).
    """
    if os.name == 'nt':
        script_path = _write_script(_windows_script_content())
        cmd = [
            'powershell', '-ExecutionPolicy', 'Bypass', '-File', script_path,
            '-OldPid', str(old_pid),
            '-CurrentExePath', current_exe_path,
            '-NewAssetPath', verified_asset_path,
            '-Port', str(port),
            '-TargetVersion', target_version,
        ]
    else:
        script_path = _write_script(_posix_script_content())
        cmd = [
            'sh', script_path,
            str(old_pid), current_exe_path, verified_asset_path, str(port), target_version,
        ]

    env = sanitize_frozen_relaunch_env(os.environ)

    popen_kwargs = dict(
        env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    if os.name == 'nt':
        # NOT DETACHED_PROCESS - confirmed via real end-to-end testing
        # (see this repo's v2.8.29 PR description) that a
        # powershell.exe launched with DETACHED_PROCESS starts and
        # exits almost immediately WITHOUT running any of the script's
        # content at all (no file it should have written ever
        # appears, no error either - it just silently never executes).
        # A plain python.exe/curatarr.exe child is unaffected by the
        # same flag (confirmed too - that's exactly what spawns this
        # worker process itself, via web/update_apply.py's
        # _spawn_worker, which works fine); this looks specific to
        # PowerShell's own console-host startup apparently requiring
        # SOME console to exist, even hidden, which DETACHED_PROCESS
        # denies it outright. CREATE_NO_WINDOW allocates a console but
        # keeps it invisible - PowerShell runs correctly under it, and
        # nothing becomes visible on the user's desktop either way.
        popen_kwargs['creationflags'] = (
            getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
            | getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        )
    else:
        popen_kwargs['start_new_session'] = True

    subprocess.Popen(cmd, **popen_kwargs)
    logger.info(f"Self-update hand-off script launched (script: {script_path})")
