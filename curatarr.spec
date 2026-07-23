# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the curatarr standalone (--onefile) binary.

Build: pyinstaller curatarr.spec  (see docs/BINARIES.md)

Notes:
- datas bundles web/templates and web/static so Flask can find them at
  runtime - PyInstaller's onefile mode extracts `datas` entries next to
  where the frozen `web` package itself lands (sys._MEIPASS/web/...),
  which is where Flask(__name__) resolves its template/static folders
  from (see web/app.py). Confirmed by an actual local build+run - see
  docs/BINARIES.md.
- hiddenimports covers packages PyInstaller's static import analysis
  doesn't always resolve on its own: ruamel.yaml and plexapi both do
  a fair amount of lazy/conditional importing internally.
- target_arch is read from the PYINSTALLER_TARGET_ARCH env var (macOS
  only - PyInstaller ignores it elsewhere) so the same spec produces
  the normal single-arch macOS binary by default, and a universal2
  (Intel + Apple Silicon) binary when PYINSTALLER_TARGET_ARCH=universal2
  is set - see the macOS job in .github/workflows/release.yml and
  docs/BINARIES.md's "Building it yourself" section for the universal2
  prerequisites (a universal2 Python + universal2 wheels/fused wheels
  for pyyaml, ruamel.yaml.clib and markupsafe).
- console is False only on Windows (windowed/no-console double-click
  launch - curatarr_app.py handles attaching to a parent console when
  run from cmd/PowerShell, allocating one for --debug, and file logging
  otherwise - see that module's docstring). macOS/Linux keep console=True,
  unchanged from before: the console flag mainly governs the Windows EXE
  subsystem, and this spec never wraps macOS in a windowed .app BUNDLE,
  so flipping it there wouldn't suppress a Terminal launch anyway.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

hidden_imports = (
    collect_submodules('ruamel.yaml')
    + collect_submodules('plexapi')
    + [
        'flask',
        'jinja2',
        'werkzeug',
        'yaml',
        'requests',
    ]
)

a = Analysis(
    ['curatarr_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('web/templates', 'web/templates'),
        ('web/static', 'web/static'),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='curatarr',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=sys.platform != 'win32',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=os.environ.get('PYINSTALLER_TARGET_ARCH') or None,
    codesign_identity=None,
    entitlements_file=None,
)
