# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None
project_root = Path.cwd()
cn_datas, cn_binaries, cn_hiddenimports = collect_all("charset_normalizer")

datas = [
    ("desktop_app/resources/styles.qss", "desktop_app/resources"),
    ("desktop_app/resources/icon.ico", "desktop_app/resources"),
] + cn_datas

binaries = cn_binaries
hiddenimports = ["pypdfium2"] + cn_hiddenimports

a = Analysis(
    ["desktop_app/__main__.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BahiAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="desktop_app/resources/icon.ico",
    version="file_version_info.txt",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BahiAI",
)
