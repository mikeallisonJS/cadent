"""Optional GPU support pack: cuBLAS DLLs for CUDA-when-present (M3 #55).

The packaged app is CPU-safe; ctranslate2 delay-loads cuBLAS through the
Windows DLL search order (see #40). When an NVIDIA driver is present but the
STT probe fell back to CPU, the app offers a one-time disclosed download of
the two cuBLAS DLLs into %LOCALAPPDATA%/Cadent/cuda — PATH-prepended at
startup (see __main__) and again right here after an in-session install.
"""

from __future__ import annotations

import logging
import os
import zipfile
from collections.abc import Callable
from pathlib import Path

from .config import CUDA_DIR

log = logging.getLogger(__name__)

# The pack's entire payload. cuDNN is not needed at ctranslate2 >= 4.6.3 (#40).
PACK_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")

# Pinned nvidia-cublas-cu12 release; ~412 MB wheel, ~550 MB extracted (#40).
PACK_VERSION = "12.9.2.10"
_PYPI_JSON = f"https://pypi.org/pypi/nvidia-cublas-cu12/{PACK_VERSION}/json"
DOWNLOAD_SIZE = "~550 MB"


def pack_installed(cuda_dir: Path = CUDA_DIR) -> bool:
    return all((cuda_dir / dll).exists() for dll in PACK_DLLS)


def should_offer(configured_device: str, engine_device: str,
                 cuda_dir: Path = CUDA_DIR,
                 stt_engine: str = "faster-whisper",
                 driver_present: bool | None = None) -> bool:
    """Offer only when the pack would change something: the running engine is
    the one these DLLs serve, the user wants CUDA (auto counts), the engine
    landed on CPU anyway, the hardware is there, and the pack isn't already
    installed.

    The engine test is not redundant. These are ctranslate2's cuBLAS DLLs;
    Parakeet runs on ONNX Runtime's DirectML provider and would not load one
    of them (#72), so offering the pack there is offering a placebo.
    """
    if driver_present is None:
        from . import platform

        driver_present = platform.current().hardware.nvidia_driver_present()
    return (stt_engine == "faster-whisper"
            and configured_device in ("auto", "cuda")
            and engine_device == "cpu"
            and not pack_installed(cuda_dir)
            and driver_present)


def _wheel_url(pypi_data: dict) -> str:
    """Pick the win_amd64 wheel out of PyPI's per-version file list
    (the /pypi/<pkg>/<version>/json response's "urls" array)."""
    for release in pypi_data.get("urls", []):
        if release.get("filename", "").endswith("win_amd64.whl"):
            return release["url"]
    raise RuntimeError(
        f"no win_amd64 wheel for nvidia-cublas-cu12 {PACK_VERSION} on PyPI")


def _fetch_wheel(dest: Path) -> None:
    """Download the pinned cuBLAS wheel from PyPI to dest (streamed)."""
    import json
    import ssl
    import urllib.request

    import certifi  # bundled by the PyInstaller certifi hook (#40)

    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(_PYPI_JSON, context=ctx, timeout=30) as resp:
        url = _wheel_url(json.load(resp))
    log.info("downloading GPU support pack: %s", url)
    with urllib.request.urlopen(url, context=ctx, timeout=60) as resp, \
            open(dest, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)


def install_pack(cuda_dir: Path = CUDA_DIR,
                 fetch: Callable[[Path], None] = _fetch_wheel) -> None:
    """Download the wheel, extract the two DLLs into cuda_dir, and prepend it
    to PATH so a same-session STT restart finds them. Raises on any failure;
    never leaves a partial pack behind (pack_installed stays False)."""
    cuda_dir.mkdir(parents=True, exist_ok=True)
    wheel = cuda_dir / "nvidia-cublas-cu12.whl"
    try:
        fetch(wheel)
        with zipfile.ZipFile(wheel) as zf:
            members = {Path(n).name: n for n in zf.namelist()}
            missing = [dll for dll in PACK_DLLS if dll not in members]
            if missing:
                raise RuntimeError(f"wheel is missing {', '.join(missing)}")
            for dll in PACK_DLLS:
                with zf.open(members[dll]) as src, open(cuda_dir / dll, "wb") as out:
                    while chunk := src.read(1 << 20):
                        out.write(chunk)
    finally:
        wheel.unlink(missing_ok=True)
    os.environ["PATH"] = str(cuda_dir) + os.pathsep + os.environ.get("PATH", "")
