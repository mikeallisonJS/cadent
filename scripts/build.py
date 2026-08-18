"""Build the frozen Cadent app: uv-driven PyInstaller onedir build (#47).

Usage: python scripts/build.py [--skip-sync]

Produces dist/Cadent/ on Windows and Linux and dist/Cadent.app on macOS
(#171), per packaging/cadent.spec, then verifies the load-bearing files the
M3 packaging research (#40) identified are present (and, on Linux, that the
excluded-on-purpose ones are absent — M6 §8.5) — a build that passes here can
still fail at runtime, so follow with scripts/smoke_frozen.py. On Linux,
scripts/build_linux.py then wraps the onedir into the tarball and AppImage.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IS_DARWIN = sys.platform == "darwin"

# The .app is the artefact on macOS; the dist/Cadent/ onedir PyInstaller
# assembles it from is scaffolding, and checking it instead would pass on a
# build whose BUNDLE step never ran.
DIST = REPO / "dist" / ("Cadent.app" if IS_DARWIN else "Cadent")

# The files whose absence the research flagged as silent build breakage —
# glob patterns against paths relative to DIST, because the versioned dylib
# names and PyInstaller's Contents/Frameworks-vs-Resources split are its
# implementation details, not facts worth pinning.
LOAD_BEARING = {
    "win32": [
        "Cadent.exe",
        "_internal/ctranslate2/ctranslate2.dll",       # custom hook: STT engine
        "_internal/llama_cpp/lib/llama.dll",           # custom hook: ctypes-loaded
        # The Vulkan backend of that same wheel (#116). Exactly the silent
        # breakage this list is for: llama.cpp loads its ggml backends by scanning
        # the lib/ directory, so a build that dropped this one starts perfectly
        # and runs every cleanup pass on the processor, ~20x slower, saying nothing.
        "_internal/llama_cpp/lib/ggml-vulkan.dll",
        "_internal/faster_whisper/assets/silero_vad_v6.onnx",  # vad_filter=True
        # The DirectML flavour of onnxruntime, not the CPU one (#72). Its absence
        # is the silent breakage this list exists for: the CPU wheel provides the
        # same onnxruntime.dll, so a build that picked it up looks complete and
        # leaves the Parakeet engine with nowhere to run but the CPU.
        "_internal/onnxruntime/capi/DirectML.dll",
        # onnx_asr's mel front end: pure-Python package, so PyInstaller archives
        # the code and drops the assets unless the custom hook collects them.
        "_internal/onnx_asr/preprocessors/data/nemo128.onnx",
        "_internal/PySide6/plugins/platforms/qwindows.dll",    # Qt can't start without
        "_internal/_sounddevice_data/portaudio-binaries/libportaudio64bit.dll",
    ],
    "darwin": [
        "Contents/MacOS/Cadent",
        "*/libctranslate2*.dylib",                     # custom hook: STT engine
        "*/llama_cpp/lib/libllama*.dylib",             # custom hook: ctypes-loaded
        # The Metal backend, and the macOS twin of the ggml-vulkan check above:
        # llama.cpp finds its backends by scanning lib/, so losing this one is
        # silent — cleanup still answers, on the CPU, many times slower. It is
        # the whole reason macOS builds llama-cpp-python from source
        # (docs/research/macos-dev-environment.md §2b).
        "*/llama_cpp/lib/libggml-metal*.dylib",
        "*/faster_whisper/assets/silero_vad_v6.onnx",  # vad_filter=True
        "*/onnx_asr/preprocessors/data/nemo128.onnx",
        "*/platforms/libqcocoa.dylib",                 # Qt can't start without
        "*/_sounddevice_data/portaudio-binaries/libportaudio*.dylib",
    ],
    # The Linux onedir (M6 §8.5, ADR 0011): eleven rows, glob patterns against
    # the dist/Cadent/ tree scripts/build_linux.py tars and wraps.
    "linux": [
        "Cadent",
        "*/libctranslate2*.so*",                       # custom hook: STT engine
        "*/llama_cpp/lib/libllama.so",                 # custom hook: ctypes-loaded
        # The Vulkan backend of the same wheel — the ggml-vulkan.dll story
        # again: lost, cleanup runs on the CPU ~20x slower and says nothing.
        "*/llama_cpp/lib/libggml-vulkan.so",
        "*/faster_whisper/assets/silero_vad_v6.onnx",  # vad_filter=True
        "*/onnx_asr/preprocessors/data/nemo128.onnx",
        # The GPU wheel's CUDA provider, not the CPU wheel: same
        # libonnxruntime.so either way, so a build that picked the CPU one up
        # looks complete and leaves Parakeet nowhere to run but the CPU.
        "*/onnxruntime/capi/libonnxruntime_providers_cuda.so",
        # Either platform plugin missing is a dead session type; without the
        # xdg-shell integration Qt connects to Wayland and shows no window.
        "*/platforms/libqxcb.so",
        "*/platforms/libqwayland-generic.so",
        "*/wayland-shell-integration/libxdg-shell.so",
        "*/libportaudio.so*",                          # staged by packaging/cadent.spec
    ],
}

# Excluded on purpose (M6 §8.5) — recorded beside LOAD_BEARING so a refactor
# cannot re-add it: alsa-lib dlopens host plugins (`pcm_pipewire` among them)
# against the host's own config, so a *bundled* libasound.so.2 breaks audio
# routing silently. The host's libasound2 is a requirement, not a payload.
# libjack.so.0 rides along instead — inert without a JACK server, and
# excluding it makes libportaudio fail to load.
EXCLUDED_ON_PURPOSE = {
    "linux": ["*/libasound.so*"],
}


def run(cmd: list[str]) -> None:
    print(f"+ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=REPO, check=True)


def collected(root: Path) -> dict[str, int]:
    """Every file in the build, as DIST-relative posix paths and sizes.

    os.walk with followlinks=False, not rglob: the .app bundle is stitched
    together with symlinks between Contents/Frameworks and Contents/Resources,
    and following them would both double-count the size and risk walking a
    cycle. Symlinks are measured as links, so each payload is counted once.
    """
    files: dict[str, int] = {}
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            files[path.relative_to(root).as_posix()] = \
                os.stat(path, follow_symlinks=False).st_size
    return files


def check_tree(files, platform: str) -> list[str]:
    """The load-bearing rows that are missing and the excluded-on-purpose
    files that crept in, as human lines; empty means the tree is right."""
    key = "linux" if platform.startswith("linux") else platform
    problems = [f"missing load-bearing file: {pattern}"
                for pattern in LOAD_BEARING.get(key, [])
                if not any(fnmatch(rel, pattern) for rel in files)]
    problems += [f"bundled a file excluded on purpose: {rel} (matches {pattern})"
                 for pattern in EXCLUDED_ON_PURPOSE.get(key, [])
                 for rel in files if fnmatch(rel, pattern)]
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-sync", action="store_true",
                        help="reuse the current environment (CI installs separately)")
    args = parser.parse_args()

    if not args.skip_sync:
        # all-extras: cleanup puts the llama-cpp-python build in the env (the
        # Vulkan wheel on Windows, a from-source Metal build on macOS) so it
        # gets collected, build brings PyInstaller — and syncing anything less
        # would strip dev tools from a local environment.
        run(["uv", "sync", "--all-extras"])
    run(["uv", "run", "pyinstaller", "--noconfirm",
         str(REPO / "packaging" / "cadent.spec"),
         "--distpath", str(REPO / "dist"), "--workpath", str(REPO / "build")])

    if not DIST.exists():
        print(f"BUILD INCOMPLETE — {DIST} was never produced", file=sys.stderr)
        return 1

    files = collected(DIST)
    problems = check_tree(files, sys.platform)
    if problems:
        print("BUILD INCOMPLETE:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1

    total = sum(files.values())
    print(f"OK: {DIST} ({total / 1_000_000:.0f} MB)")
    print("Next: python scripts/smoke_frozen.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
