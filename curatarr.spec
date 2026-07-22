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
"""

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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
