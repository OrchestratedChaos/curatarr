"""Compiles tiny C# stub 'binaries' for fast, safe local iteration on
the self-update hand-off script logic (utils/self_update_handoff.py).
NOT PyInstaller onefile - a plain compiled console app has no
bootloader/extraction stage at all, so it categorically cannot produce
the DLL-missing/_MEI dialog a real curatarr.exe onefile build can.

Variants:
  old.exe / old        - serves /healthz with OLD_VERSION
  new.exe / new         - serves /healthz with NEW_VERSION
  crash.exe / crash     - exits immediately (simulates a build that
                           fails to even start)
  hang.exe / hang        - starts but never binds the port (simulates a
                           build that hangs during startup)

Windows: compiled via csc.exe (bundled with every .NET Framework
install, i.e. every real Windows machine and GitHub's windows-latest).
POSIX: no compilation needed - the shell-script versions here are
plain /bin/sh + a background `nc`-less pure-python http.server one-
liner is overkill; instead this reuses the SAME tiny python stub
server script for macOS/Linux, since there's no PyInstaller
onefile/bootloader concern on those platforms in the first place (only
Windows relaunch reliability was ever in question here).
"""
import os
import platform
import subprocess
import sys

STUB_DIR = os.path.dirname(os.path.abspath(__file__))
CSC_PATH = r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
TEMPLATE_PATH = os.path.join(STUB_DIR, 'stub_template.cs')


def _compile_windows_stub(mode, version, out_path):
    with open(TEMPLATE_PATH, encoding='utf-8') as f:
        src = f.read()
    src = src.replace('__MODE__', mode).replace('__VERSION__', version)
    cs_path = out_path + '.cs'
    with open(cs_path, 'w', encoding='utf-8') as f:
        f.write(src)
    # Invoked via PowerShell, not directly - Git Bash/MSYS mangles
    # csc.exe's /flag style args and Windows paths unpredictably.
    ps_cmd = (
        f"& '{CSC_PATH}' /nologo /out:'{out_path}' '{cs_path}'"
    )
    result = subprocess.run(
        ['powershell', '-NoProfile', '-Command', ps_cmd],
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0 or not os.path.isfile(out_path):
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"csc.exe failed to build {out_path}")


def _write_posix_stub_wrapper(mode, version, out_path):
    """A plain /bin/sh script (no compilation, no PyInstaller) that
    execs the shared stub_server.py with the right args - directly
    executable, exactly like a real binary would be launched by
    self_update_handoff.py's posix script."""
    server_script = os.path.join(STUB_DIR, 'stub_server.py')
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{server_script}" "{mode}" "{version}"\n')
    os.chmod(out_path, 0o755)


def build_stub(mode, version, out_path):
    if os.name == 'nt':
        _compile_windows_stub(mode, version, out_path)
    else:
        _write_posix_stub_wrapper(mode, version, out_path)


def main():
    out_dir = os.path.join(STUB_DIR, 'bin')
    os.makedirs(out_dir, exist_ok=True)
    suffix = '.exe' if os.name == 'nt' else ''

    build_stub('normal', '1.0.0', os.path.join(out_dir, f'old{suffix}'))
    build_stub('normal', '2.0.0', os.path.join(out_dir, f'new{suffix}'))
    build_stub('crash', '0.0.0', os.path.join(out_dir, f'crash{suffix}'))
    build_stub('hang', '0.0.0', os.path.join(out_dir, f'hang{suffix}'))
    print(f"stubs built in {out_dir}")


if __name__ == '__main__':
    main()
