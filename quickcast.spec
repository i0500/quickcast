# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['cv2']
hiddenimports += collect_submodules('PySide6')


a = Analysis(
    ['quickcast\\__main__.py'],
    pathex=['.'],
    binaries=[],
    datas=[('quickcast\\data\\targets', 'quickcast\\data\\targets'), ('quickcast\\data\\icon.ico', 'quickcast\\data')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
splash = Splash(
    'quickcast\\data\\splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    text_size=12,
    minify_script=True,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    splash,
    splash.binaries,
    [],
    name='quickcast',
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
    version='quickcast\\data\\version.txt',
    uac_admin=True,
    icon=['quickcast\\data\\icon.ico'],
)
