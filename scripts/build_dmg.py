"""Wrap dist/Cadent.app in a drag-to-Applications disk image (#171).

Usage: python scripts/build_dmg.py [--version X.Y.Z]

The macOS counterpart of packaging/cadent.iss: where Windows gets a per-user
Inno installer, a Mac gets the convention it already knows — a disk image
holding the app and a symlink to /Applications, dragged across by hand. No
admin prompt either way, and nothing to uninstall but the bundle.

Requires: dist/Cadent.app from scripts/build.py, and macOS (hdiutil, ditto).
Signing is the workflow's job — this only packages what it is given.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "dist" / "Cadent.app"
STAGE = REPO / "build" / "dmg"
OUT_DIR = REPO / "dist" / "installer"

# Apple silicon only, and named so nobody has to open it to find out: the
# runtime stack has no universal2 build to freeze (see packaging/cadent.spec).
ARCH = "arm64"


def run(cmd: list[str]) -> None:
    print(f"+ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=os.environ.get("CADENT_VERSION", "0.0.0"),
                        help="version for the filename (default: $CADENT_VERSION)")
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("build_dmg.py needs macOS — hdiutil and ditto are the tools "
              "that make a correct image", file=sys.stderr)
        return 1
    if not APP.exists():
        print(f"{APP} not found — run scripts/build.py first", file=sys.stderr)
        return 1

    # ditto, not copytree: it is the one copy that preserves symlinks, resource
    # forks and the extended attributes a code signature lives in. A copy that
    # loses those ships an app that fails `codesign --verify`.
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    run(["ditto", str(APP), str(STAGE / "Cadent.app")])
    os.symlink("/Applications", STAGE / "Applications")

    # The mounted volume carries the mark too, instead of the generic disk
    # Finder draws otherwise — the same tile the .app inside it wears. Finder
    # only honours `.VolumeIcon.icns` when the folder it sits in is flagged as
    # having a custom icon, and `SetFile` is the only tool that sets that bit;
    # it ships with the Xcode command line tools, which this build already
    # needs to compile llama-cpp-python. A missing SetFile costs the icon, not
    # the release.
    icns = REPO / "packaging" / "icons" / "cadent.icns"
    if icns.exists():
        shutil.copy2(icns, STAGE / ".VolumeIcon.icns")
        if shutil.which("SetFile"):
            run(["SetFile", "-a", "C", str(STAGE)])
        else:
            print("note: SetFile not found — the volume ships without its "
                  "custom icon", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dmg = OUT_DIR / f"Cadent-{args.version}-{ARCH}.dmg"
    dmg.unlink(missing_ok=True)
    # UDZO: read-only and compressed, the format Finder mounts fastest and the
    # only one worth signing. HFS+ because a signed, notarized image has to
    # mount on every macOS the app supports, including ones that predate APFS
    # on this path.
    run(["hdiutil", "create", "-volname", "Cadent", "-srcfolder", str(STAGE),
         "-ov", "-format", "UDZO", "-fs", "HFS+", str(dmg)])

    print(f"OK: {dmg} ({dmg.stat().st_size / 1_000_000:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
