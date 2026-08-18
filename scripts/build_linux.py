"""Wrap dist/Cadent/ into the two Linux artefacts (M6 §8, ADR 0011).

Usage: python scripts/build_linux.py [--version X.Y.Z] [--appimagetool PATH]

Emits, into dist/installer/:

- `cadent-<version>-x86_64.tar.zst` — the onedir, unpacked anywhere and run
  as `Cadent`; the AUR `cadent-bin` package repacks exactly this.
- `Cadent-<version>-x86_64.AppImage` — the same tree in an AppDir with an
  AppRun, the desktop entry and the tile, squashed by `appimagetool` (a
  static AppImage the workflow downloads; skipped with a note when it is not
  on PATH and not passed in). No signing.

The Linux twin of build_dmg.py: it only packages what scripts/build.py made
and already checked (LOAD_BEARING plus the excluded-on-purpose row).
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "dist" / "Cadent"
OUT_DIR = REPO / "dist" / "installer"
STAGE = REPO / "build" / "linux"
ARCH = "x86_64"
APP_ID = "com.mikeallisonjs.cadent"

APPRUN = """#!/bin/sh
# AppRun: hand off to the frozen entry point next to this script. $APPIMAGE
# is set by the runtime; cadent's autostart entry reads it (M6 §8.4).
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/cadent/Cadent" "$@"
"""

DESKTOP_ENTRY = f"""[Desktop Entry]
Type=Application
Name=Cadent
GenericName=Dictation
Comment=Push-to-talk dictation that types where you are
Exec=Cadent
Icon={APP_ID}
Terminal=false
Categories=Utility;Accessibility;
StartupWMClass={APP_ID}
"""


def run(cmd: list[str], **kw) -> None:
    print(f"+ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True, **kw)


def build_tarball(version: str) -> Path:
    """`cadent-<version>-x86_64.tar.zst` with one top-level `cadent/` dir."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"cadent-{version}-{ARCH}.tar.zst"
    out.unlink(missing_ok=True)
    staged = STAGE / "tar" / "cadent"
    if staged.parent.exists():
        shutil.rmtree(staged.parent)
    shutil.copytree(DIST, staged, symlinks=True)
    run(["tar", "--zstd", "-cf", str(out), "-C", str(staged.parent), "cadent"])
    return out


def build_appdir(version: str) -> Path:
    appdir = STAGE / "Cadent.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    target = appdir / "usr" / "lib" / "cadent"
    shutil.copytree(DIST, target, symlinks=True)
    (appdir / "usr" / "bin").mkdir(parents=True)
    os.symlink("../lib/cadent/Cadent", appdir / "usr" / "bin" / "Cadent")
    apprun = appdir / "AppRun"
    apprun.write_text(APPRUN, encoding="utf-8")
    apprun.chmod(apprun.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (appdir / f"{APP_ID}.desktop").write_text(DESKTOP_ENTRY, encoding="utf-8")
    tile = REPO / "packaging" / "icons" / "png" / "app-256.png"
    shutil.copyfile(tile, appdir / f"{APP_ID}.png")
    shutil.copyfile(tile, appdir / ".DirIcon")
    # The hicolor tiles for launchers that read the AppDir directly.
    for size in (48, 128, 256):
        src = REPO / "packaging" / "icons" / "png" / f"app-{size}.png"
        dest = appdir / "usr" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest / f"{APP_ID}.png")
    return appdir


def build_appimage(version: str, appimagetool: str | None) -> Path | None:
    tool = appimagetool or shutil.which("appimagetool")
    appdir = build_appdir(version)
    if tool is None:
        print("appimagetool not found — AppDir staged at "
              f"{appdir}, AppImage skipped", file=sys.stderr)
        return None
    out = OUT_DIR / f"Cadent-{version}-{ARCH}.AppImage"
    out.unlink(missing_ok=True)
    env = dict(os.environ, ARCH=ARCH)
    # --no-appstream: no AppStream metadata ships (Discover is not a target).
    run([tool, "--no-appstream", str(appdir), str(out)], env=env)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=os.environ.get("CADENT_VERSION", "0.0.0"))
    parser.add_argument("--appimagetool", default=None,
                        help="path to appimagetool (default: PATH lookup)")
    args = parser.parse_args()

    if not sys.platform.startswith("linux"):
        print("build_linux.py needs Linux — tar --zstd and appimagetool are the tools",
              file=sys.stderr)
        return 1
    if not DIST.exists():
        print(f"{DIST} not found — run scripts/build.py first", file=sys.stderr)
        return 1

    tarball = build_tarball(args.version)
    print(f"OK: {tarball} ({tarball.stat().st_size / 1_000_000:.0f} MB)")
    appimage = build_appimage(args.version, args.appimagetool)
    if appimage is not None:
        print(f"OK: {appimage} ({appimage.stat().st_size / 1_000_000:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
