"""Wrap dist/Cadent/ as the two Linux artefacts (spec §8.1, ADR 0011).

Usage: python scripts/build_linux.py [--version X.Y.Z] [--appimagetool PATH]

Emits dist/installer/cadent-<version>-x86_64.tar.zst — the plain onedir, which
the AUR `cadent-bin` package repacks — and dist/installer/Cadent-<version>-
x86_64.AppImage, built from an AppDir that adds the desktop entry, the
hicolor icons and an AppRun symlink to that same onedir. The AppImage runtime
sets $APPIMAGE, which is what the autostart entry reads (spec §8.4).

Requires: dist/Cadent from scripts/build.py, `zstd` and `tar`, and
appimagetool on PATH (or --appimagetool); the workflow downloads the latter.
No signing on Linux.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "dist" / "Cadent"
STAGE = REPO / "build" / "appdir"
OUT_DIR = REPO / "dist" / "installer"
ICONS = REPO / "packaging" / "icons" / "png"
APP_ID = "com.mikeallisonjs.cadent"
ARCH = "x86_64"

DESKTOP_ENTRY = f"""[Desktop Entry]
Type=Application
Name=Cadent
Comment=Push-to-talk dictation that runs entirely on this PC
Exec=Cadent
Icon={APP_ID}
Terminal=false
Categories=Utility;Accessibility;
StartupWMClass={APP_ID}
"""


def run(cmd: list[str], **kwargs) -> None:
    print(f"+ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def tarball(version: str) -> Path:
    out = OUT_DIR / f"cadent-{version}-{ARCH}.tar.zst"
    out.unlink(missing_ok=True)
    # The onedir under a versioned top-level directory, so an unpack never
    # spills files into the current directory.
    run(["tar", "--zstd", "-cf", str(out), "-C", str(DIST.parent),
         f"--transform=s,^Cadent,cadent-{version},", "Cadent"])
    return out


def appdir() -> Path:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    shutil.copytree(DIST, STAGE / "usr" / "bin", symlinks=True)
    os.symlink("usr/bin/Cadent", STAGE / "AppRun")
    (STAGE / f"{APP_ID}.desktop").write_text(DESKTOP_ENTRY, encoding="utf-8")
    for size in (48, 128, 256):
        source = ICONS / f"app-{size}.png"
        if not source.exists():
            continue
        dest = STAGE / "usr" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest / f"{APP_ID}.png")
        if size == 256:
            shutil.copy2(source, STAGE / f"{APP_ID}.png")
            os.symlink(f"{APP_ID}.png", STAGE / ".DirIcon")
    return STAGE


def appimage(version: str, tool: str) -> Path:
    out = OUT_DIR / f"Cadent-{version}-{ARCH}.AppImage"
    out.unlink(missing_ok=True)
    env = dict(os.environ, ARCH=ARCH)
    run([tool, str(appdir()), str(out)], env=env)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=os.environ.get("CADENT_VERSION", "0.0.0"))
    parser.add_argument("--appimagetool", default=shutil.which("appimagetool"))
    parser.add_argument("--skip-appimage", action="store_true")
    args = parser.parse_args()

    if not sys.platform.startswith("linux"):
        print("build_linux.py needs Linux", file=sys.stderr)
        return 1
    if not DIST.exists():
        print(f"{DIST} not found — run scripts/build.py first", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    artefacts = [tarball(args.version)]
    if not args.skip_appimage:
        if not args.appimagetool:
            print("appimagetool not found — pass --appimagetool", file=sys.stderr)
            return 1
        artefacts.append(appimage(args.version, args.appimagetool))
    for path in artefacts:
        print(f"OK: {path} ({path.stat().st_size / 1_000_000:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
