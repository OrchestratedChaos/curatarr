"""Runs the 'swap' scenario N times in a row via run_scenario.py, to
prove the real onefile swap -> external hand-off -> fresh relaunch
cycle is NOT flaky (see .github/workflows/selfupdate-e2e.yml). Each
cycle is fully isolated (fresh install dir + fresh HOME/APPDATA,
recreated by run_scenario.py itself) and always goes old-version ->
new-version fresh - repeat cycles are testing "does the exact same
kill -> verify -> hand-off -> swap -> relaunch sequence succeed
reliably, run after run", which is what matters for proving it's not
flaky.

Usage: python run_repeated.py <n> --manifest <path> --work-dir <path> [--debug-log-dir <path>]
"""
import argparse
import json
import subprocess
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument('n', type=int)
    p.add_argument('--manifest', required=True)
    p.add_argument('--work-dir', required=True)
    p.add_argument('--ui-port', type=int, default=18787)
    p.add_argument('--server-port', type=int, default=18800)
    p.add_argument('--debug-log-dir', default=None)
    args = p.parse_args()

    with open(args.manifest) as f:
        manifest = json.load(f)

    good = manifest['releases']['good']
    run_scenario = __file__.replace('run_repeated.py', 'run_scenario.py')

    results = []
    for i in range(1, args.n + 1):
        print(f"\n{'=' * 20} CYCLE {i}/{args.n} {'=' * 20}", flush=True)
        cmd = [
            sys.executable, run_scenario,
            '--scenario', 'swap',
            '--old-binary', manifest['old_binary'],
            '--release-dir', good['dir'],
            '--old-version', manifest['old_version'],
            '--target-version', manifest['new_version'],
            '--ui-port', str(args.ui_port),
            '--server-port', str(args.server_port),
            '--work-dir', f"{args.work_dir}/cycle_{i}",
        ]
        if args.debug_log_dir:
            cmd += ['--debug-log', f"{args.debug_log_dir}/cycle_{i}_handoff.log"]

        proc = subprocess.run(cmd, timeout=180)
        passed = proc.returncode == 0
        results.append(passed)
        print(f"--- cycle {i}: {'PASS' if passed else 'FAIL'} ---", flush=True)

    n_passed = sum(results)
    print(f"\n{n_passed}/{args.n} cycles passed")
    sys.exit(0 if n_passed == args.n else 1)


if __name__ == '__main__':
    main()
