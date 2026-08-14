# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec per the M3 packaging research (#40): onedir + windowed
# (Inno wraps it; onefile costs per-launch extraction and AV rescans), stock
# hooks everywhere except the three custom ones in hooks/. CPU-safe by
# construction — CUDA support is delay-loaded at runtime, never bundled.

import os

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    # The project is installed editable (uv sync), which modulegraph can't
    # follow — resolve the cadent package from the repo root instead.
    pathex=[os.path.dirname(SPECPATH)],
    binaries=[],
    # The mark (#73): rasterised PNGs loaded by QIcon.addFile at runtime, so
    # Qt's SVG image-format plugin never has to survive the freeze.
    datas=[(os.path.join(SPECPATH, "icons"), "icons")],
    hiddenimports=[],
    hookspath=[os.path.join(SPECPATH, "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Cadent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # tray app; stdout/stderr go nowhere by design
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Without this the exe, the Alt-Tab entry and the taskbar are on
    # PyInstaller's default icon while the tray shows the real mark.
    icon=os.path.join(SPECPATH, "icons", "cadent.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Cadent",
)
