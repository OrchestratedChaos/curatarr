"""Drives one real end-to-end self-update scenario against a REAL
built curatarr binary, entirely inside CI (see
.github/workflows/selfupdate-e2e.yml's module-level comment for why
this only ever runs there, never on a maintainer's own machine).

Scenarios:
  swap      - a genuinely newer, correctly-signed release is available:
              expect the running binary to swap and relaunch as the
              new version.
  bad_sig   - SHA256SUMS.txt is signed by the WRONG key: expect the
              update to be refused, old binary/version unchanged.
  bad_hash  - SHA256SUMS.txt is correctly signed but records the wrong
              hash for the asset: expect refusal, old binary/version
              unchanged.
  rollback  - the "new" asset passes signature+hash verification (it
              IS exactly what SHA256SUMS.txt describes) but can never
              actually serve /healthz (see build_fixtures.py's
              "broken" binary) - expect the hand-off script's own
              fail-safe to detect that, restore the old binary, and
              relaunch it successfully.

Prints clear PASS/FAIL evidence; exits 1 on any unexpected outcome or
timeout (a stuck/hung process must fail the CI job, never hang it
forever).
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

try:
    import psutil
except ImportError:
    print("ERROR: this script requires psutil (pip install psutil)", file=sys.stderr)
    raise


def http_get_json(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def http_post(url, timeout=5):
    # web/security.py's register_origin_host_guard requires a same-origin
    # Origin header on every state-changing request.
    origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
    req = urllib.request.Request(url, method='POST', data=b'', headers={'Origin': origin})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace')
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {'raw_body': raw}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


class Outcome:
    def __init__(self, version, elapsed):
        self.version = version
        self.elapsed = elapsed


def wait_for_version(port, timeout, acceptable, forbidden=(), label=''):
    """Polls /healthz until it reports a version in `acceptable`
    (success) or `forbidden` (immediate hard failure - e.g. a rollback
    scenario must never actually end up serving the broken "new"
    version). Tolerates the port being transiently unreachable (the old
    server going down before the new/relaunched one comes up)."""
    deadline = time.time() + timeout
    last_seen = None
    while time.time() < deadline:
        try:
            data = http_get_json(f'http://127.0.0.1:{port}/healthz', timeout=2)
            v = data.get('version')
            if v != last_seen:
                print(f"    [{label}] healthz version = {v!r} at t={time.time():.1f}", flush=True)
                last_seen = v
            if v in forbidden:
                raise SystemExit(f"[{label}] FAIL: /healthz reported forbidden version {v!r}")
            if v in acceptable:
                return Outcome(v, timeout - (deadline - time.time()))
        except SystemExit:
            raise
        except Exception:
            if last_seen != '<unreachable>':
                print(f"    [{label}] healthz unreachable at t={time.time():.1f}", flush=True)
                last_seen = '<unreachable>'
        time.sleep(0.5)
    raise TimeoutError(f"[{label}] /healthz on port {port} never reported one of {acceptable} within {timeout}s")


def kill_tree(pid):
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    children = proc.children(recursive=True)
    for child in children:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        proc.kill()
    except psutil.NoSuchProcess:
        pass
    psutil.wait_procs(children + [proc], timeout=5)


def kill_by_install_dir(install_dir):
    """Kills any process whose executable path is inside install_dir -
    NOT psutil.net_connections()/port-based lookup: confirmed via real
    CI runs that psutil.net_connections() raises AccessDenied without
    root on macOS (and, separately, misattributes loopback listeners to
    PID 4/"System" (a protected process) without elevation on Windows -
    both netstat and psutil misattribute to it there too - see this
    repo's v2.8.29 PR description). Process iteration + matching each
    process's own exe/cmdline against install_dir needs no elevation on
    any platform and is scoped tightly enough (a fresh, unique work dir
    per scenario/cycle) to never touch anything unrelated - including
    the swap-relaunched process this script never has a PID for
    directly."""
    install_dir_norm = os.path.normcase(os.path.abspath(install_dir))
    for proc in psutil.process_iter(['pid', 'exe', 'cmdline']):
        try:
            exe = proc.info.get('exe') or ''
            cmdline = proc.info.get('cmdline') or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        haystacks = [exe] + cmdline
        if any(install_dir_norm in os.path.normcase(h) for h in haystacks if h):
            kill_tree(proc.info['pid'])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--scenario', required=True, choices=['swap', 'bad_sig', 'bad_hash', 'rollback'])
    p.add_argument('--old-binary', required=True)
    p.add_argument('--release-dir', required=True)
    p.add_argument('--old-version', required=True)
    p.add_argument('--target-version', required=True)
    p.add_argument('--ui-port', type=int, required=True)
    p.add_argument('--server-port', type=int, required=True)
    p.add_argument('--work-dir', required=True)
    p.add_argument('--debug-log', default=None)
    args = p.parse_args()

    if os.path.isdir(args.work_dir):
        shutil.rmtree(args.work_dir)
    install_dir = os.path.join(args.work_dir, 'install')
    home_dir = os.path.join(args.work_dir, 'home')
    os.makedirs(install_dir)
    os.makedirs(home_dir)

    exe_name = os.path.basename(args.old_binary)
    exe_path = os.path.join(install_dir, exe_name)
    shutil.copy2(args.old_binary, exe_path)
    if os.name != 'nt':
        os.chmod(exe_path, 0o755)
    original_hash = sha256_file(exe_path)

    print(f"=== [{args.scenario}] release_dir={args.release_dir} target={args.target_version} ===", flush=True)
    print(f"[{args.scenario}] old binary sha256: {original_hash}", flush=True)

    server_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fake_release_server.py')
    server_proc = subprocess.Popen(
        [sys.executable, server_script, args.release_dir, args.target_version, str(args.server_port)],
    )
    time.sleep(1.0)

    env = dict(os.environ)
    env['HOME'] = home_dir
    env['APPDATA'] = home_dir  # harmless no-op on POSIX, correct override on Windows
    env['CURATARR_UI_PORT'] = str(args.ui_port)
    env['CURATARR_SKIP_BROWSER_OPEN'] = '1'
    env['CURATARR_RELEASES_API_OVERRIDE'] = f'http://127.0.0.1:{args.server_port}/api/latest'
    env['CURATARR_RELEASES_DOWNLOAD_BASE_OVERRIDE'] = f'http://127.0.0.1:{args.server_port}/download'
    # GitHub-hosted runners can have HTTP(S)_PROXY set for outbound
    # traffic control - confirmed via a real CI run that without an
    # explicit NO_PROXY, `requests` (used by utils/update_check.py and
    # utils/self_update.py) tries to route even 127.0.0.1 through it,
    # timing out after REQUEST_TIMEOUT_SECONDS and making the
    # precondition check fail-open to "no update available" - never a
    # real bug in the self-update logic itself, just this harness
    # needing to force loopback traffic to bypass any proxy.
    env['NO_PROXY'] = '127.0.0.1,localhost'
    env['no_proxy'] = '127.0.0.1,localhost'
    if args.debug_log:
        env['CURATARR_HANDOFF_DEBUG_LOG'] = args.debug_log

    exe_proc = subprocess.Popen([exe_path], cwd=install_dir, env=env)

    try:
        print(f"[{args.scenario}] waiting for old server on port {args.ui_port}...", flush=True)
        old_up = wait_for_version(args.ui_port, timeout=30, acceptable={args.old_version}, label='startup')
        print(f"[{args.scenario}] old server up: version={old_up.version}", flush=True)

        print(f"[{args.scenario}] POST /update/apply ...", flush=True)
        status, body = http_post(f'http://127.0.0.1:{args.ui_port}/update/apply')
        print(f"[{args.scenario}] /update/apply -> {status} {body}", flush=True)
        if status != 202:
            raise SystemExit(f"[{args.scenario}] FAIL: expected 202, got {status}: {body}")

        appdata_curatarr = os.path.join(home_dir, 'curatarr') if os.name == 'nt' else os.path.join(home_dir, '.curatarr')
        update_log_path = os.path.join(appdata_curatarr, 'logs', 'update_apply.log')

        def read_update_log():
            if os.path.isfile(update_log_path):
                with open(update_log_path, encoding='utf-8', errors='replace') as f:
                    return f.read()
            return ''

        if args.scenario == 'swap':
            outcome = wait_for_version(
                args.ui_port, timeout=110,
                acceptable={args.target_version}, forbidden=set(), label='swap',
            )
            final_hash = sha256_file(exe_path)
            log = read_update_log()
            expected_new_hash = sha256_file(os.path.join(args.release_dir, exe_name))
            assert final_hash == expected_new_hash, (
                f"on-disk binary hash {final_hash} does not match the release asset's hash "
                f"{expected_new_hash} after a reported swap"
            )
            assert 'handing off swap+relaunch' in log, f"update_apply.log missing hand-off line: {log!r}"
            print(f"[{args.scenario}] PASS: swapped to v{outcome.version}, "
                  f"binary hash {original_hash[:12]} -> {final_hash[:12]}", flush=True)

        elif args.scenario in ('bad_sig', 'bad_hash'):
            time.sleep(20)
            data = http_get_json(f'http://127.0.0.1:{args.ui_port}/healthz', timeout=3)
            assert data.get('version') == args.old_version, (
                f"expected version to stay {args.old_version}, got {data}"
            )
            final_hash = sha256_file(exe_path)
            assert final_hash == original_hash, "binary hash CHANGED - a bad update was applied!"
            log = read_update_log()
            assert 'verify failed' in log, f"update_apply.log does not show a verify failure: {log!r}"
            print(f"[{args.scenario}] PASS: update refused, binary hash unchanged "
                  f"({final_hash[:12]}), still serving v{args.old_version}", flush=True)

        elif args.scenario == 'rollback':
            outcome = wait_for_version(
                args.ui_port, timeout=110,
                acceptable={args.old_version}, forbidden={args.target_version}, label='rollback',
            )
            final_hash = sha256_file(exe_path)
            assert final_hash == original_hash, (
                f"binary hash did not revert to the original after rollback "
                f"(expected {original_hash}, got {final_hash})"
            )
            print(f"[{args.scenario}] PASS: rolled back to v{outcome.version}, "
                  f"binary hash restored ({final_hash[:12]})", flush=True)

        return 0
    finally:
        kill_tree(exe_proc.pid)
        kill_by_install_dir(install_dir)
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except Exception:
            server_proc.kill()
        try:
            exe_proc.wait(timeout=5)
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())
