"""Fast, safe local iteration harness for utils/self_update_handoff.py's
generated hand-off script - drives the REAL script content (imported
directly from that module, not a copy) against tiny stub 'binaries'
(see build_stubs.py) instead of a real PyInstaller onefile curatarr.exe.

Because the stubs are plain compiled/interpreted programs with no
PyInstaller bootloader or onefile extraction stage, there is no path to
a native crash dialog here at all - safe to run repeatedly, directly,
locally, on the interactive desktop, in seconds per cycle.

Usage:
  python build_stubs.py                                  # once, or after editing stub_template.cs
  python run_stub_cycle.py success --work-dir <dir> --port <port> [--repeat N]
  python run_stub_cycle.py rollback-crash --work-dir <dir> --port <port>
  python run_stub_cycle.py rollback-hang --work-dir <dir> --port <port>

--repeat N runs N independent cycles back to back (each on its own
port and work dir) - this is what proves the swap/hand-off/relaunch
sequence is reliable run after run, not just a one-off success.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

# This script lives at <repo_root>/scripts/selfupdate_stub_e2e/ - walk
# up two levels to import utils.self_update_handoff from the checkout
# it's actually running in, never a hardcoded path.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, '..', '..'))
sys.path.insert(0, REPO_ROOT)

from utils import self_update_handoff  # noqa: E402

STUB_BIN = os.path.join(_THIS_DIR, 'bin')
EXE_SUFFIX = '.exe' if os.name == 'nt' else ''


def get_healthz(port, timeout=2):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def wait_for(port, timeout, acceptable_versions):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            v = get_healthz(port).get('version')
            if v != last:
                print(f"    healthz version = {v!r} at t={time.time():.1f}", flush=True)
                last = v
            if v in acceptable_versions:
                return v
        except Exception:
            if last != '<down>':
                print(f"    healthz unreachable at t={time.time():.1f}", flush=True)
                last = '<down>'
        time.sleep(0.3)
    raise TimeoutError(f"/healthz on {port} never reported one of {acceptable_versions} within {timeout}s")


def start_stub(exe_path, port, extra_env=None):
    env = dict(os.environ)
    env['CURATARR_UI_PORT'] = str(port)
    if extra_env:
        env.update(extra_env)
    if os.name == 'nt':
        return subprocess.Popen([exe_path], env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    return subprocess.Popen([exe_path], env=env)


def launch_handoff_script(old_pid, current_exe_path, new_asset_path, port, target_version, debug_log):
    if os.name == 'nt':
        script_path = self_update_handoff._write_script(self_update_handoff._windows_script_content())
        cmd = [
            'powershell', '-ExecutionPolicy', 'Bypass', '-File', script_path,
            '-OldPid', str(old_pid), '-CurrentExePath', current_exe_path,
            '-NewAssetPath', new_asset_path, '-Port', str(port), '-TargetVersion', target_version,
        ]
        creationflags = (
            getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
            | getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
        )
        popen_kwargs = dict(creationflags=creationflags)
    else:
        script_path = self_update_handoff._write_script(self_update_handoff._posix_script_content())
        cmd = ['sh', script_path, str(old_pid), current_exe_path, new_asset_path, str(port), target_version]
        popen_kwargs = dict(start_new_session=True)

    env = dict(os.environ)
    if debug_log:
        with open(debug_log, 'ab') as f:
            pass
        popen_kwargs['stdout'] = open(debug_log, 'ab')
        popen_kwargs['stderr'] = subprocess.STDOUT
    else:
        popen_kwargs['stdout'] = subprocess.DEVNULL
        popen_kwargs['stderr'] = subprocess.DEVNULL
    popen_kwargs['stdin'] = subprocess.DEVNULL
    popen_kwargs['env'] = env
    subprocess.Popen(cmd, **popen_kwargs)
    return script_path


def kill_quiet(pid):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)
    except Exception:
        pass


def kill_by_exe_path(exe_path):
    """Kills any process whose command line matches exe_path - used
    instead of port-based lookup (confirmed via direct testing that
    both netstat and psutil misattribute loopback listeners to PID 4/
    "System" on this machine, almost certainly Bitdefender's network
    filter intercepting them - neither tool can find the real owning
    PID without elevation here). WMI's CommandLine match is unaffected
    and has been reliable throughout this whole investigation."""
    if os.name == 'nt':
        ps_path = exe_path.replace("'", "''")
        cmd = (
            f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{ps_path}*' }} "
            f"| ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
        )
        subprocess.run(['powershell', '-NoProfile', '-Command', cmd], capture_output=True)
    else:
        subprocess.run(['pkill', '-9', '-f', exe_path], capture_output=True)


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# NOT netstat/psutil port->PID lookup - confirmed via direct testing
# that BOTH misattribute this machine's loopback listeners to PID 4
# ("System") without elevation, almost certainly Bitdefender's network
# filter driver intercepting them at the kernel level. kill_by_exe_path
# (WMI CommandLine match) is unaffected and has been reliable
# throughout this whole investigation - this harness's stub root path
# is specific enough to never match anything unrelated.
STUB_E2E_ROOT = os.path.dirname(os.path.abspath(__file__))


def _rmtree_retry(work_dir, attempts=6, delay=0.5):
    last_exc = None
    for _ in range(attempts):
        try:
            shutil.rmtree(work_dir)
            return
        except FileNotFoundError:
            return
        except PermissionError as e:
            last_exc = e
            time.sleep(delay)
    if last_exc:
        raise last_exc


def _wait_port_free(port, timeout=10):
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return True
        time.sleep(0.3)
    return False


def run_cycle(scenario, work_dir, port, debug_log=None):
    # Defensive: a previous cycle's relaunched stub may still be
    # exiting/holding a file handle for a moment - kill anything of
    # ours still alive first, then retry the rmtree instead of failing
    # outright on a transient PermissionError.
    kill_by_exe_path(STUB_E2E_ROOT)
    _wait_port_free(port, timeout=10)
    if os.path.isdir(work_dir):
        _rmtree_retry(work_dir)
    os.makedirs(work_dir)

    current_exe_path = os.path.join(work_dir, f'curatarr{EXE_SUFFIX}')
    shutil.copy2(os.path.join(STUB_BIN, f'old{EXE_SUFFIX}'), current_exe_path)
    if os.name != 'nt':
        os.chmod(current_exe_path, 0o755)
    old_hash = sha256(current_exe_path)

    if scenario == 'success':
        new_asset_source = os.path.join(STUB_BIN, f'new{EXE_SUFFIX}')
        target_version = '2.0.0'
    elif scenario == 'rollback-crash':
        new_asset_source = os.path.join(STUB_BIN, f'crash{EXE_SUFFIX}')
        target_version = '2.0.0'
    elif scenario == 'rollback-hang':
        new_asset_source = os.path.join(STUB_BIN, f'hang{EXE_SUFFIX}')
        target_version = '2.0.0'
    else:
        raise ValueError(scenario)

    new_asset_path = os.path.join(work_dir, f'.pending-update{EXE_SUFFIX}')
    shutil.copy2(new_asset_source, new_asset_path)
    if os.name != 'nt':
        os.chmod(new_asset_path, 0o755)

    print(f"=== [{scenario}] starting old stub on port {port} ===", flush=True)
    old_proc = start_stub(current_exe_path, port)
    try:
        wait_for(port, timeout=10, acceptable_versions={'1.0.0'})
        print(f"[{scenario}] old stub confirmed up (v1.0.0)", flush=True)

        print(f"[{scenario}] launching hand-off script (old_pid={old_proc.pid})...", flush=True)
        launch_handoff_script(old_proc.pid, current_exe_path, new_asset_path, port, target_version, debug_log)

        # The script itself waits for the old pid to exit - kill it now,
        # exactly like web/update_apply.py's _shut_down_old_server does
        # before handing off in production.
        kill_quiet(old_proc.pid)

        if scenario == 'success':
            v = wait_for(port, timeout=30, acceptable_versions={'2.0.0'})
            final_hash = sha256(current_exe_path)
            expected_hash = sha256(new_asset_source)
            assert final_hash == expected_hash, f"binary on disk does not match the new asset after swap"
            print(f"[{scenario}] PASS: swapped and serving v{v}, hash {old_hash[:12]} -> {final_hash[:12]}", flush=True)
        else:
            # HANDOFF_HEALTH_TIMEOUT_SECONDS (60s) alone means the
            # script won't even decide to roll back until close to a
            # minute in - generous margin beyond that for the actual
            # restore+relaunch+old-stub-startup afterward.
            v = wait_for(port, timeout=90, acceptable_versions={'1.0.0'})
            final_hash = sha256(current_exe_path)
            assert final_hash == old_hash, "binary on disk did not revert to the original after rollback"
            print(f"[{scenario}] PASS: rolled back and serving v{v} again, hash restored ({final_hash[:12]})", flush=True)
        return True
    except Exception as e:
        print(f"[{scenario}] FAIL: {e}", flush=True)
        return False
    finally:
        kill_quiet(old_proc.pid)
        # whatever is now on the port (new/relaunched-old stub)
        kill_by_exe_path(STUB_E2E_ROOT)


def main():
    if not os.path.isdir(STUB_BIN):
        raise SystemExit(
            f"{STUB_BIN} doesn't exist yet - run build_stubs.py first "
            f"(see this script's module docstring)."
        )

    p = argparse.ArgumentParser()
    p.add_argument('scenario', choices=['success', 'rollback-crash', 'rollback-hang'])
    p.add_argument('--work-dir', required=True)
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--debug-log', default=None)
    p.add_argument('--repeat', type=int, default=1)
    args = p.parse_args()

    results = []
    for i in range(1, args.repeat + 1):
        if args.repeat > 1:
            print(f"\n----- cycle {i}/{args.repeat} -----", flush=True)
        # A fresh port per cycle - this local stub loop's job is
        # proving the swap/hand-off/rollback SCRIPT LOGIC is reliable
        # run after run, not re-testing "does the OS release a TCP
        # port promptly" (confirmed via direct testing to be an
        # environmental artifact on this machine: Bitdefender's network
        # filter keeps a loopback listener socket appearing bound for
        # some time after the owning process is already dead and gone,
        # entirely outside anything this code controls).
        cycle_port = args.port + i - 1
        ok = run_cycle(args.scenario, f"{args.work_dir}_{i}", cycle_port, args.debug_log)
        results.append(ok)
        time.sleep(1)

    n_ok = sum(results)
    print(f"\n{n_ok}/{len(results)} cycles passed")
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == '__main__':
    main()
