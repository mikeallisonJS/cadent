"""Package the frozen Linux build as a tarball and an AppImage (spec m6 §8).

Usage: python scripts/build_linux.py --version X.Y.Z [--appimagetool PATH]

Reads dist/Cadent/ (from scripts/build.py) and writes, into dist/installer/:

    Cadent-<version>-x86_64.AppImage
    cadent-<version>-x86_64.tar.zst

The tarball holds one top-level `cadent-<version>/` directory with the onedir
inside, which is what packaging/aur/PKGBUILD repacks. No signing.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "dist" / "Cadent"
OUT = REPO / "dist" / "installer"
APP_ID = "com.mikeallisonjs.cadent"
ARCH = "x86_64"

APPRUN = """#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/cadent/Cadent" "$@"
"""

DESKTOP = f"""[Desktop Entry]
Type=Application
Name=Cadent
Comment=Local push-to-talk dictation
Exec=Cadent
Icon={APP_ID}
Terminal=false
Categories=Utility;AudioVideo;
StartupWMClass={APP_ID}
"""


def tarball_name(version: str) -> str:
    return f"cadent-{version}-{ARCH}.tar.zst"


def appimage_name(version: str) -> str:
    return f"Cadent-{version}-{ARCH}.AppImage"


def build_tarball(dist: Path, version: str, out: Path) -> Path:
    """`tar --zstd` rather than a Python zstd binding: the runner has both,
    and the AUR build repacking the result needs only tar."""
    out.mkdir(parents=True, exist_ok=True)
    target = out / tarball_name(version)
    plain = out / f"cadent-{version}-{ARCH}.tar"
    root = f"cadent-{version}"
    with tarfile.open(plain, "w") as tar:
        tar.add(dist, arcname=root)
    subprocess.run(["zstd", "-19", "-T0", "--rm", "-f", "-q", str(plain),
                    "-o", str(target)], check=True)
    return target


def build_appdir(dist: Path, appdir: Path) -> None:
    if appdir.exists():
        shutil.rmtree(appdir)
    payload = appdir / "usr" / "lib" / "cadent"
    shutil.copytree(dist, payload, symlinks=True)
    apprun = appdir / "AppRun"
    apprun.write_text(APPRUN, encoding="utf-8")
    apprun.chmod(apprun.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (appdir / f"{APP_ID}.desktop").write_text(DESKTOP, encoding="utf-8")
    icon = REPO / "packaging" / "icons" / "png" / "cadent-256.png"
    shutil.copyfile(icon, appdir / f"{APP_ID}.png")
    shutil.copyfile(icon, appdir / ".DirIcon")


def build_appimage(dist: Path, version: str, out: Path, appimagetool: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    appdir = REPO / "build" / "Cadent.AppDir"
    build_appdir(dist, appdir)
    target = out / appimage_name(version)
    # --appimage-extract-and-run: CI runners have no FUSE.
    env = {**os.environ, "ARCH": ARCH, "APPIMAGE_EXTRACT_AND_RUN": "1"}
    subprocess.run([appimagetool, str(appdir), str(target)], check=True, env=env)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--appimagetool", default="appimagetool")
    args = parser.parse_args()

    if not DIST.is_dir():
        print(f"{DIST} is missing — run scripts/build.py first", file=sys.stderr)
        return 1
    tarball = build_tarball(DIST, args.version, OUT)
    appimage = build_appimage(DIST, args.version, OUT, args.appimagetool)
    for artefact in (tarball, appimage):
        print(f"OK: {artefact} ({artefact.stat().st_size / 1_000_000:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
