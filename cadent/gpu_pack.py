"""Optional GPU support pack: the CUDA libraries a speech engine needs (M3 #55).

The packaged app is CPU-safe. When an NVIDIA driver is present but the STT
probe fell back to CPU, the app offers a one-time disclosed download of the
CUDA libraries into the data dir's `cuda/` folder.

The pack is one surface with engine-keyed **editions** (ADR 0010): which one
a platform offers for an engine is a `Capabilities` fact. Windows has the
cuBLAS-12 DLL pair for faster-whisper, PATH-prepended (see below). Linux adds
a CUDA-13 stack for Parakeet, offered only on an R580+ driver, and activates
both by an `RTLD_LOCAL` ctypes preload — `LD_LIBRARY_PATH` is fixed at exec,
and a GLOBAL preload of cuBLAS 12 would let its symbols shadow cuBLAS 13's.
"""

from __future__ import annotations

import ctypes
import fnmatch
import logging
import os
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import CUDA_DIR

log = logging.getLogger(__name__)

# The pack's Windows payload. cuDNN is not needed at ctranslate2 >= 4.6.3 (#40).
PACK_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")

# Pinned nvidia-cublas-cu12 release; ~412 MB wheel, ~550 MB extracted (#40).
PACK_VERSION = "12.9.2.10"

# What `cuDriverGetVersion` reports for an R580 driver (CUDA 13.0).
CUDA_13_DRIVER = 13000
DRIVER_UPDATE_NOTE = ("Update your NVIDIA driver to 580 or newer to run "
                      "Parakeet on the GPU.")


@dataclass(frozen=True)
class Edition:
    key: str
    engine: str
    size: str                                 # disclosed before any download
    what: str                                 # "NVIDIA cuBLAS" — disclosure copy
    wheels: tuple[tuple[str, str], ...]       # (PyPI project, pinned version)
    wheel_tags: tuple[str, ...]               # substrings picking the wheel
    required: tuple[str, ...]                 # present on disk == installed
    extract: tuple[str, ...]                  # fnmatch globs for wheel members
    preload: tuple[str, ...] = ()             # Linux: dlopen order; () = PATH
    min_driver_cuda: int = 0                  # cuDriverGetVersion floor


_LINUX_TAGS = ("manylinux", "x86_64")

EDITIONS: dict[str, Edition] = {e.key: e for e in (
    Edition(
        key="cublas-win", engine="faster-whisper", size="~550 MB",
        what="NVIDIA cuBLAS",
        wheels=(("nvidia-cublas-cu12", PACK_VERSION),),
        wheel_tags=("win_amd64",), required=PACK_DLLS, extract=PACK_DLLS),
    Edition(
        key="cublas-linux", engine="faster-whisper", size="~600 MB",
        what="NVIDIA cuBLAS",
        wheels=(("nvidia-cublas-cu12", PACK_VERSION),),
        wheel_tags=_LINUX_TAGS,
        required=("libcublas.so.12", "libcublasLt.so.12"),
        extract=("libcublas.so.12", "libcublasLt.so.12"),
        preload=("libcublasLt.so.12", "libcublas.so.12")),
    Edition(
        key="cuda13-linux", engine="parakeet", size="~1.3 GB",
        what="NVIDIA CUDA 13 and cuDNN",
        wheels=(("nvidia-cuda-runtime", "13.5.37"),
                ("nvidia-cublas", "13.10.0.20"),
                ("nvidia-cufft", "12.5.0.14"),
                ("nvidia-curand", "10.4.5.25"),
                ("nvidia-cuda-nvrtc", "13.5.37"),
                ("nvidia-nvjitlink", "13.5.37"),
                ("nvidia-cudnn-cu13", "9.28.1.4")),
        wheel_tags=_LINUX_TAGS,
        required=("libcudart.so.13", "libcublas.so.13", "libcublasLt.so.13",
                  "libnvrtc.so.13", "libcufft.so.12", "libnvJitLink.so.13",
                  "libcurand.so.10", "libcudnn.so.9"),
        extract=("libcudart.so.13", "libcublas.so.13", "libcublasLt.so.13",
                 "libnvrtc.so.13", "libnvrtc-builtins.so*", "libcufft.so.12",
                 "libnvJitLink.so.13", "libcurand.so.10", "libcudnn*.so.9"),
        preload=("libcudart.so.13", "libnvJitLink.so.13", "libcublasLt.so.13",
                 "libcublas.so.13", "libnvrtc.so.13", "libcufft.so.12",
                 "libcurand.so.10", "libcudnn.so.9"),
        min_driver_cuda=CUDA_13_DRIVER),
)}


def edition_for(engine: str) -> Edition | None:
    """The edition this platform serves an engine, or None where the pack
    would be a placebo for it (Parakeet on Windows runs on DirectML, #72)."""
    from . import platform

    key = platform.current().capabilities.gpu_pack_editions.get(engine)
    return EDITIONS.get(key) if key else None


def download_size(engine: str) -> str:
    edition = edition_for(engine)
    return edition.size if edition else ""


def pack_installed(edition: Edition, cuda_dir: Path = CUDA_DIR) -> bool:
    return all((cuda_dir / name).exists() for name in edition.required)


def driver_supports(edition: Edition, driver_cuda: int | None) -> bool:
    """Does the driver's CUDA version meet the edition's floor? An edition
    with no floor needs only that a driver exists."""
    return edition.min_driver_cuda == 0 or (driver_cuda or 0) >= edition.min_driver_cuda


def needs_driver_update(engine: str, driver_present: bool,
                        driver_cuda: int | None) -> bool:
    """A readable driver that is too old for the engine's edition — the one
    case where the row says to update the driver instead of offering a pack."""
    edition = edition_for(engine)
    return (edition is not None and driver_present and driver_cuda is not None
            and not driver_supports(edition, driver_cuda))


def should_offer(configured_device: str, engine_device: str,
                 cuda_dir: Path = CUDA_DIR,
                 stt_engine: str = "faster-whisper",
                 driver_present: bool | None = None,
                 driver_cuda: int | None = None) -> bool:
    """Offer only when the pack would change something: the running engine has
    an edition on this platform, the user wants CUDA (auto counts), the engine
    landed on CPU anyway, the hardware (and for Parakeet, the driver's CUDA
    version) is there, and that edition isn't already installed.

    The engine test is a selector, not a formality: an edition only helps the
    engine whose runtime reads it (#72).
    """
    edition = edition_for(stt_engine)
    if edition is None:
        return False
    if driver_present is None:
        from . import platform

        probe = platform.current().hardware
        driver_present = probe.nvidia_driver_present()
        driver_cuda = probe.cuda_driver_version()
    return (configured_device in ("auto", "cuda")
            and engine_device == "cpu"
            and not pack_installed(edition, cuda_dir)
            and driver_present
            and driver_supports(edition, driver_cuda))


def _wheel_url(pypi_data: dict, tags: tuple[str, ...] = ("win_amd64",),
               project: str = "nvidia-cublas-cu12") -> str:
    """Pick the matching wheel out of PyPI's per-version file list
    (the /pypi/<pkg>/<version>/json response's "urls" array)."""
    for release in pypi_data.get("urls", []):
        name = release.get("filename", "")
        if name.endswith(".whl") and all(tag in name for tag in tags):
            return release["url"]
    raise RuntimeError(f"no {'/'.join(tags)} wheel for {project} on PyPI")


def _fetch_wheel(project: str, version: str, tags: tuple[str, ...],
                 dest: Path) -> None:
    """Download one pinned wheel from PyPI to dest (streamed)."""
    import json
    import ssl
    import urllib.request

    import certifi  # bundled by the PyInstaller certifi hook (#40)

    ctx = ssl.create_default_context(cafile=certifi.where())
    meta = f"https://pypi.org/pypi/{project}/{version}/json"
    with urllib.request.urlopen(meta, context=ctx, timeout=30) as resp:
        url = _wheel_url(json.load(resp), tags, project)
    log.info("downloading GPU support pack: %s", url)
    with urllib.request.urlopen(url, context=ctx, timeout=60) as resp, \
            open(dest, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)


def install_pack(edition: Edition, cuda_dir: Path = CUDA_DIR,
                 fetch: Callable[[str, str, tuple[str, ...], Path], None] = _fetch_wheel
                 ) -> None:
    """Download the edition's wheels, extract its libraries into cuda_dir, and
    activate them so a same-session STT restart finds them. Raises on any
    failure; never leaves a partial pack behind (pack_installed stays False)."""
    cuda_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        for project, version in edition.wheels:
            wheel = cuda_dir / f"{project}.whl"
            try:
                fetch(project, version, edition.wheel_tags, wheel)
                with zipfile.ZipFile(wheel) as zf:
                    for member in zf.namelist():
                        name = Path(member).name
                        if not any(fnmatch.fnmatchcase(name, glob)
                                   for glob in edition.extract):
                            continue
                        target = cuda_dir / name
                        written.append(target)
                        with zf.open(member) as src, open(target, "wb") as out:
                            while chunk := src.read(1 << 20):
                                out.write(chunk)
            finally:
                wheel.unlink(missing_ok=True)
        missing = [n for n in edition.required if not (cuda_dir / n).exists()]
        if missing:
            raise RuntimeError(f"wheel is missing {', '.join(missing)}")
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    activate(edition, cuda_dir)


def activate(edition: Edition, cuda_dir: Path = CUDA_DIR) -> None:
    """Make an installed edition findable by the engine's runtime.

    Windows prepends the dir to PATH. Linux preloads each library with
    `RTLD_LOCAL` before the engine first touches CUDA: a later `dlopen` by
    soname (ctranslate2's, ONNX Runtime's) then resolves to the loaded copy,
    and cuBLAS 12 and 13 coexist in one process without either's unversioned
    symbols shadowing the other's. A library that will not load is logged,
    not raised — the engine's own probe then lands on CPU."""
    if not edition.preload:
        os.environ["PATH"] = str(cuda_dir) + os.pathsep + os.environ.get("PATH", "")
        return
    for name in edition.preload:
        try:
            ctypes.CDLL(str(cuda_dir / name), mode=ctypes.RTLD_LOCAL)
        except OSError:
            log.warning("could not preload %s", name, exc_info=True)


def activate_installed(cuda_dir: Path | None = None) -> None:
    """Startup: activate every installed edition this platform offers."""
    from . import config, platform

    cuda_dir = cuda_dir or config.CUDA_DIR

    for key in set(platform.current().capabilities.gpu_pack_editions.values()):
        edition = EDITIONS.get(key)
        if edition is not None and pack_installed(edition, cuda_dir):
            activate(edition, cuda_dir)
