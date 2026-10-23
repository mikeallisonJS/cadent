"""The GPU support pack: CUDA userspace for CUDA-when-present (M3 #55, ADR 0010).

The packaged app is CPU-safe; the speech engines reach CUDA only once the
libraries they dlopen are there. When an NVIDIA driver is present but the
engine fell back to CPU, the app offers a one-time disclosed download of
those libraries into the app's data dir. **What it fetches is an edition**,
keyed by speech engine and carried by the platform (`Capabilities.
gpu_pack_editions`): on Windows the two cuBLAS DLLs for faster-whisper,
switched on by a PATH prepend; on Linux the same cuBLAS pair *and* the
CUDA-13 stack for Parakeet, switched on by a `ctypes` preload with the
default `RTLD_LOCAL` before the engine first touches CUDA — not a PATH
prepend (`LD_LIBRARY_PATH` is fixed at exec) and not `RTLD_GLOBAL` (a
GLOBAL cuBLAS 12 preload lets unversioned symbols shadow cuBLAS 13's).
"""

from __future__ import annotations

import ctypes
import logging
import os
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

from .config import CUDA_DIR
from .platform.base import GpuPackEdition

log = logging.getLogger(__name__)


# The Windows edition's names, kept for the call sites that predate editions
# (the tray item's size, the install toast) and the tests that pin them.
PACK_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")
PACK_VERSION = "12.9.2.10"
DOWNLOAD_SIZE = "~550 MB"


def editions() -> Mapping[str, GpuPackEdition]:
    from . import platform

    return platform.current().capabilities.gpu_pack_editions


def edition_for(stt_engine: str) -> GpuPackEdition | None:
    """The edition the running speech engine would read, or None where
    this platform has none for it."""
    return editions().get(stt_engine)


def _cuda_dir(cuda_dir: Path | None) -> Path:
    """Read at call time, so a test (or a future setting) can redirect it."""
    return cuda_dir if cuda_dir is not None else CUDA_DIR


def pack_dir(edition: GpuPackEdition, cuda_dir: Path | None = None) -> Path:
    """Each edition keeps its own directory: two CUDA majors share one
    process by soname but not one folder, and "delete the directory to go
    back to CPU" should mean one edition at a time."""
    return _cuda_dir(cuda_dir) / edition.engine


def pack_installed(cuda_dir: Path | None = None,
                   edition: GpuPackEdition | None = None) -> bool:
    edition = edition or _default_edition()
    if edition is None:
        return False
    directory = pack_dir(edition, cuda_dir)
    return all((directory / name).exists() for name in edition.files)


def _default_edition() -> GpuPackEdition | None:
    return edition_for("faster-whisper")


def driver_can_use(edition: GpuPackEdition, driver_cuda_version: int | None) -> bool:
    """Does this driver speak the CUDA the edition needs? An edition with no
    floor is fine on any driver; one with a floor needs the probe to have
    answered at or above it."""
    if edition.min_driver_cuda is None:
        return True
    return driver_cuda_version is not None and driver_cuda_version >= edition.min_driver_cuda


DRIVER_TOO_OLD = ("Update the NVIDIA driver to 580 or newer to run this model on "
                  "the GPU.")


def should_offer(configured_device: str, engine_device: str,
                 cuda_dir: Path | None = None,
                 stt_engine: str = "faster-whisper",
                 driver_present: bool | None = None,
                 driver_cuda_version: int | None = None,
                 edition: GpuPackEdition | None = None) -> bool:
    """Offer only when the pack would change something: this platform has an
    edition for the running engine (the engine test is a real selector —
    Windows has none for Parakeet, Linux has both), the user wants CUDA
    (auto counts), the engine landed on CPU anyway, the driver is there and
    new enough, and the edition isn't already installed."""
    edition = edition or edition_for(stt_engine)
    if edition is None or edition.engine != stt_engine:
        return False
    if driver_present is None:
        from . import platform

        driver_present = platform.current().hardware.nvidia_driver_present()
    if edition.min_driver_cuda is not None and driver_cuda_version is None and driver_present:
        from . import platform

        driver_cuda_version = platform.current().hardware.driver_cuda_version()
    return (configured_device in ("auto", "cuda")
            and engine_device == "cpu"
            and driver_present
            and driver_can_use(edition, driver_cuda_version)
            and not pack_installed(cuda_dir, edition))


def _wheel_url(pypi_data: dict, tag: str = "win_amd64") -> str:
    """Pick the wheel for a platform tag out of PyPI's per-version file list
    (the /pypi/<pkg>/<version>/json response's "urls" array)."""
    for release in pypi_data.get("urls", []):
        name = release.get("filename", "")
        if name.endswith(".whl") and tag in name and "x86_64" in name or \
                name.endswith(f"{tag}.whl"):
            return release["url"]
    raise RuntimeError(f"no {tag} wheel in {pypi_data.get('info', {}).get('name', '?')}")


def _fetch_wheel(dest: Path, distribution: str, version: str, tag: str) -> None:
    """Download one pinned wheel from PyPI to dest (streamed)."""
    import json
    import ssl
    import urllib.request

    import certifi  # bundled by the PyInstaller certifi hook (#40)

    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(f"https://pypi.org/pypi/{distribution}/{version}/json",
                                context=ctx, timeout=30) as resp:
        url = _wheel_url(json.load(resp), tag)
    log.info("downloading GPU support pack: %s", url)
    with urllib.request.urlopen(url, context=ctx, timeout=60) as resp, \
            open(dest, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)


def install_pack(cuda_dir: Path | None = None,
                 fetch: Callable[..., None] = _fetch_wheel,
                 edition: GpuPackEdition | None = None) -> None:
    """Download each wheel, extract exactly the edition's files into the
    pack dir, and activate them so a same-session STT restart finds them.
    Raises on any failure; never leaves a partial pack behind
    (`pack_installed` stays False). `fetch(dest, distribution, version, tag)`
    is the download; tests hand in a stand-in that writes a wheel-shaped zip."""
    edition = edition or _default_edition()
    if edition is None:
        raise RuntimeError("no GPU support pack exists on this platform")
    directory = pack_dir(edition, cuda_dir)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        for distribution, version, tag, names in edition.wheels:
            wheel = directory / f"{distribution}.whl"
            try:
                _call_fetch(fetch, wheel, distribution, version, tag)
                with zipfile.ZipFile(wheel) as zf:
                    members = {Path(n).name: n for n in zf.namelist()}
                    missing = [name for name in names if name not in members]
                    if missing:
                        raise RuntimeError(f"{distribution} wheel is missing "
                                           f"{', '.join(missing)}")
                    for name in names:
                        with zf.open(members[name]) as src, \
                                open(directory / name, "wb") as out:
                            while chunk := src.read(1 << 20):
                                out.write(chunk)
            finally:
                wheel.unlink(missing_ok=True)
    except Exception:
        for name in edition.files:
            (directory / name).unlink(missing_ok=True)
        raise
    activate(edition, cuda_dir)


def _call_fetch(fetch, dest: Path, distribution: str, version: str, tag: str) -> None:
    """Older stand-ins take only the destination; the real fetch takes all."""
    import inspect

    try:
        required = sum(1 for param in inspect.signature(fetch).parameters.values()
                       if param.default is param.empty
                       and param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD))
    except (TypeError, ValueError):
        required = 4
    if required <= 1:
        fetch(dest)
    else:
        fetch(dest, distribution, version, tag)


_preloaded: dict[str, ctypes.CDLL] = {}


def activate(edition: GpuPackEdition, cuda_dir: Path | None = None) -> bool:
    """Switch an installed edition on for this process: PATH prepend or
    RTLD_LOCAL preload, per the edition. False when it is not installed."""
    if not pack_installed(cuda_dir, edition):
        return False
    directory = pack_dir(edition, cuda_dir)
    if edition.activation == "path":
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
        return True
    for name in edition.files:
        path = directory / name
        if str(path) in _preloaded:
            continue
        try:
            # Default mode is RTLD_LOCAL on purpose (ADR 0010); the dlopen by
            # soname the engine does later finds the already-loaded object.
            _preloaded[str(path)] = ctypes.CDLL(str(path))
        except OSError:
            log.warning("could not preload %s", path, exc_info=True)
            return False
    return True


def activate_installed(cuda_dir: Path | None = None) -> list[str]:
    """At startup: switch on every edition that is already on disk, before
    anything can touch CUDA. Returns the engines activated."""
    activated = []
    for engine, edition in editions().items():
        if activate(edition, cuda_dir):
            activated.append(engine)
    return activated
