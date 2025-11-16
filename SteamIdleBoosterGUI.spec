import os
from pathlib import Path

icon_data = []
icon_file = None
if os.path.exists('icon.ico'):
    icon_data = [('icon.ico', '.')]
    icon_file = str(Path('icon.ico').resolve())

a = Analysis(
    ['gui_main.py'],
    pathex=[],
    binaries=[],
    datas=icon_data,
    hiddenimports=[],
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
    name='SteamIdleBooster-win64',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)
