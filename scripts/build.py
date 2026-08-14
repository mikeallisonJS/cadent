"""Build the frozen Cadent app: uv-driven PyInstaller onedir build (#47).

Usage: python scripts/build.py [--skip-sync]

Produces dist/Cadent/ per packaging/cadent.spec, then verifies the
load-bearing files the M3 packaging research (#40) identified are present —
a build that passes here can still fail at runtime, so follow with
scripts/smoke_frozen.py.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "dist" / "Cadent"

# The files whose absence the research flagged as silent build breakage.
LOAD_BEARING = [
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
]


def run(cmd: list[str]) -> None:
    print(f"+ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=REPO, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-sync", action="store_true",
                        help="reuse the current environment (CI installs separately)")
    args = parser.parse_args()

    if not args.skip_sync:
        # all-extras: cleanup puts the Vulkan llama-cpp-python wheel in the env
        # so it gets collected, build brings PyInstaller — and syncing
        # anything less would strip dev tools from a local environment.
        run(["uv", "sync", "--all-extras"])
    run(["uv", "run", "pyinstaller", "--noconfirm",
         str(REPO / "packaging" / "cadent.spec"),
         "--distpath", str(REPO / "dist"), "--workpath", str(REPO / "build")])

    missing = [rel for rel in LOAD_BEARING if not (DIST / rel).exists()]
    if missing:
        print("BUILD INCOMPLETE — missing load-bearing files:", file=sys.stderr)
        for rel in missing:
            print(f"  {rel}", file=sys.stderr)
        return 1

    total = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"OK: {DIST} ({total / 1_000_000:.0f} MB onedir)")
    print("Next: python scripts/smoke_frozen.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
