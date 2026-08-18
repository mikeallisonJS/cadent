# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec per the M3 packaging research (#40): onedir + windowed
# (Inno wraps it; onefile costs per-launch extraction and AV rescans), stock
# hooks everywhere except the three custom ones in hooks/. CPU-safe by
# construction — CUDA support is delay-loaded at runtime, never bundled.
#
# One spec, two OSes (#171). Analysis is identical on both — same entry script,
# same hooks, same collected icons — so the platform branches sit at the edges
# only: which icon container the OS reads, and the BUNDLE step that turns the
# onedir output into Cadent.app. A second spec file would have meant two places
# to add the next hidden import.

import os
import sys

IS_DARWIN = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# The identity macOS files the app under: TCC grants (Accessibility, the
# microphone), the LaunchAgent, Finder, `open -b`. It must stay equal to
# `cadent.platform.darwin.LAUNCH_AGENT_LABEL` — the agent labels *this* app, so
# a drifted pair would leave a login item pointing at a bundle id nothing has.
# tests/test_packaging.py asserts the two literals match.
BUNDLE_ID = "com.mikeallisonjs.cadent"

# CFBundleVersion is what macOS compares across updates, and pyproject's
# version never reaches a frozen build (no package metadata is collected — see
# cadent/__init__.py), so the tag supplies it. Digits and dots only, which is
# why this default is a bare 0.0.0 where the Windows leg's dev marker is
# "0.0.0-dev" — macOS rejects the suffix rather than ignoring it.
VERSION = os.environ.get("CADENT_VERSION", "0.0.0")

# Kept a plain literal so the packaging test can `ast.literal_eval` it on any
# OS — the version is grafted on below rather than interpolated in.
INFO_PLIST = {
    "CFBundleIdentifier": BUNDLE_ID,
    "CFBundleName": "Cadent",
    "CFBundleDisplayName": "Cadent",
    # A menu-bar app, not a Dock app. Two reasons beyond taste: injection
    # pastes into whatever is frontmost (ADR 0001), and app-override identity
    # is `NSWorkspace.frontmostApplication()` (ADR 0004) — an accessory app
    # can raise its own windows without ever becoming the frontmost app, so
    # neither the paste target nor the override key can end up being Cadent
    # itself.
    "LSUIElement": True,
    # PySide6 6.8's floor, and above every pyobjc/onnxruntime arm64 wheel's.
    "LSMinimumSystemVersion": "12.0",
    "NSHighResolutionCapable": True,
    # Load-bearing, not boilerplate: a hardened-runtime app that touches the
    # mic with no usage string is killed by the OS rather than prompted for.
    "NSMicrophoneUsageDescription":
        "Cadent transcribes what you dictate while you hold the hotkey. "
        "Audio is processed on this Mac and never leaves it.",
}
INFO_PLIST["CFBundleShortVersionString"] = VERSION
INFO_PLIST["CFBundleVersion"] = VERSION

ICON = os.path.join(SPECPATH, "icons",
                    "cadent.icns" if IS_DARWIN else "cadent.ico")

# Linux (M6 §8.5, ADR 0011): sounddevice's manylinux wheel carries no
# PortAudio, so the distro's libportaudio.so.2 is staged into the bundle
# (`apt-get install libportaudio2` on the runner). Its ALSA dependency is
# deliberately *not*: alsa-lib dlopens the host's plugins against the host's
# config, so a bundled copy breaks routing silently — libasound.so.2 stays a
# host requirement, and Analysis is stripped of it below. libjack rides along.
STAGED_BINARIES = []
if IS_LINUX:
    import ctypes.util

    _portaudio = ctypes.util.find_library("portaudio")
    if _portaudio:
        for candidate in ("/usr/lib/x86_64-linux-gnu/" + _portaudio,
                          "/usr/lib64/" + _portaudio, "/usr/lib/" + _portaudio):
            if os.path.exists(candidate):
                STAGED_BINARIES.append((candidate, "."))
                break
EXCLUDED_ON_PURPOSE = ("libasound.so",) if IS_LINUX else ()

# Signing, when there is an identity to sign with. Read from the environment
# rather than written here: the value names a certificate in the runner's
# keychain, and only the workflow knows whether one was imported. Unset — the
# default, and the whole story on a dev machine — leaves PyInstaller ad-hoc
# signing, which is the minimum arm64 will load at all.
#
# Letting PyInstaller do it beats re-signing afterwards: it signs each collected
# binary as it places it, innermost first, which is exactly the order codesign
# requires and exactly what `--deep` gets wrong.
CODESIGN_IDENTITY = os.environ.get("CADENT_CODESIGN_IDENTITY") or None
ENTITLEMENTS = (os.path.join(SPECPATH, "cadent.entitlements")
                if CODESIGN_IDENTITY else None)

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    # The project is installed editable (uv sync), which modulegraph can't
    # follow — resolve the cadent package from the repo root instead.
    pathex=[os.path.dirname(SPECPATH)],
    binaries=STAGED_BINARIES,
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
if EXCLUDED_ON_PURPOSE:
    a.binaries = [entry for entry in a.binaries
                  if not any(os.path.basename(entry[0]).startswith(name)
                             for name in EXCLUDED_ON_PURPOSE)]
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
    # Native arch. macOS builds are Apple-silicon only by construction: the
    # runtime stack (onnxruntime, ctranslate2, the from-source llama.cpp) has
    # no universal2 wheel to build against, so asking for one would only fail
    # later and less clearly.
    target_arch=None,
    codesign_identity=CODESIGN_IDENTITY,
    entitlements_file=ENTITLEMENTS,
    # Without this the exe, the Alt-Tab entry and the taskbar are on
    # PyInstaller's default icon while the tray shows the real mark. macOS
    # reads the bundle's icon instead (set on BUNDLE below), and warns if
    # handed an .ico.
    icon=ICON,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Cadent",
)

if IS_DARWIN:
    # dist/Cadent.app alongside the dist/Cadent/ onedir it is assembled from.
    # scripts/build_dmg.py ships the .app; the onedir is scaffolding.
    app = BUNDLE(
        coll,
        name="Cadent.app",
        icon=ICON,
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist=INFO_PLIST,
        codesign_identity=CODESIGN_IDENTITY,
        entitlements_file=ENTITLEMENTS,
    )
