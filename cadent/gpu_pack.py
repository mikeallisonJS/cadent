"""Optional GPU support pack: CUDA userspace for CUDA-when-present (M3 #55,
M6 ADR 0010).

The packaged app is CPU-safe. When an NVIDIA driver is present but the STT
probe fell back to CPU, the app offers a one-time disclosed download of the
libraries the *running engine* dlopens, extracted from PyPI `nvidia-*`
wheels into `CUDA_DIR`. One surface, engine-keyed **editions** — the
platform's `Capabilities.gpu_pack_editions` says which exist here:

- Windows: the cuBLAS-12 DLL pair for faster-whisper (ctranslate2 delay-loads
  cuBLAS through the DLL search order → PATH-prepended, see __main__).
- Linux: the same pair as `.so`s, plus the CUDA-13 stack for Parakeet
  (`onnxruntime-gpu`'s CUDA provider; needs an R580+ driver). Activated by a
  `ctypes.CDLL` preload with the default `RTLD_LOCAL` before the engine
  first touches CUDA — `LD_LIBRARY_PATH` is fixed at exec, and RTLD_GLOBAL
  would let cuBLAS 12's unversioned symbols shadow cuBLAS 13's.

`should_offer` keeps its engine test as a real selector: an edition is only
offered for the engine that would load it.
"""

from __future__ import annotations

import ctypes
import fnmatch
import logging
import os
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

from .config import CUDA_DIR
from .platform.base import GpuPackEdition

log = logging.getLogger(__name__)

# The historical Windows constants, kept for the surfaces and tests that
# still name them; the edition table is the source of truth.
PACK_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")
PACK_VERSION = "12.9.2.10"
DOWNLOAD_SIZE = "~550 MB"


# ---- which edition ----------------------------------------------------------

def _editions(caps=None) -> Mapping[str, GpuPackEdition]:
    if caps is None:
        from . import platform

        caps = platform.current().capabilities
    return caps.gpu_pack_editions


def edition_for(engine: str, caps=None) -> GpuPackEdition | None:
    """The edition serving `engine` on this platform, or None."""
    return _editions(caps).get(engine)


def download_size(engine: str, caps=None) -> str:
    edition = edition_for(engine, caps)
    return edition.size if edition is not None else DOWNLOAD_SIZE


def pack_dir(edition: GpuPackEdition, cuda_dir: Path = CUDA_DIR) -> Path:
    return cuda_dir / edition.subdir if edition.subdir else cuda_dir


def _is_glob(pattern: str) -> bool:
    return any(ch in pattern for ch in "*?[")


def _has(dest: Path, pattern: str) -> bool:
    if _is_glob(pattern):
        return next(dest.glob(pattern), None) is not None
    return (dest / pattern).exists()


def _present(edition: GpuPackEdition, dest: Path) -> bool:
    return all(_has(dest, pattern) for pattern in edition.files)


def pack_installed(cuda_dir: Path = CUDA_DIR, engine: str = "faster-whisper",
                   caps=None) -> bool:
    edition = edition_for(engine, caps)
    return edition is not None and _present(edition, pack_dir(edition, cuda_dir))


def driver_supports(edition: GpuPackEdition, cuda_driver_version: int | None) -> bool:
    """A pre-580 driver cannot run the CUDA-13 edition; the row says to
    update instead of offering a download that would not load."""
    if edition.min_driver_cuda is None:
        return True
    return cuda_driver_version is not None and cuda_driver_version >= edition.min_driver_cuda


def driver_hint(engine: str, cuda_driver_version: int | None, caps=None) -> str | None:
    """The update-driver line for the current engine's edition, or None
    when the driver is fine (or there is no edition)."""
    edition = edition_for(engine, caps)
    if edition is None or driver_supports(edition, cuda_driver_version):
        return None
    return edition.driver_hint


def should_offer(configured_device: str, engine_device: str,
                 cuda_dir: Path = CUDA_DIR,
                 stt_engine: str = "faster-whisper",
                 driver_present: bool | None = None,
                 cuda_driver_version: int | None = None,
                 caps=None) -> bool:
    """Offer only when the pack would change something: an edition exists
    for the running engine, the user wants CUDA (auto counts), the engine
    landed on CPU anyway, the hardware (and driver version) is there, and
    the edition isn't already installed."""
    edition = edition_for(stt_engine, caps)
    if edition is None:
        return False
    if driver_present is None:
        from . import platform

        driver_present = platform.current().hardware.nvidia_driver_present()
        if cuda_driver_version is None:
            cuda_driver_version = platform.current().hardware.cuda_driver_version()
    return (configured_device in ("auto", "cuda")
            and engine_device == "cpu"
            and not _present(edition, pack_dir(edition, cuda_dir))
            and driver_present
            and driver_supports(edition, cuda_driver_version))


# ---- PyPI --------------------------------------------------------------------

def _wheel_url(pypi_data: dict, tag: str = "win_amd64") -> str:
    """Pick the wheel for `tag` out of PyPI's per-version file list (the
    /pypi/<pkg>/<version>/json response's "urls" array). "manylinux" means
    any manylinux x86_64 build."""
    for release in pypi_data.get("urls", []):
        name = release.get("filename", "")
        if not name.endswith(".whl"):
            continue
        if tag == "manylinux":
            if "manylinux" in name and "x86_64" in name:
                return release["url"]
        elif name.endswith(f"{tag}.whl"):
            return release["url"]
    raise RuntimeError(f"no {tag} wheel in {pypi_data.get('info', {}).get('name', '?')} "
                       f"{pypi_data.get('info', {}).get('version', '')} on PyPI")


def _pick_version(pypi_project: dict, wanted: str) -> str:
    """An exact version, or the newest release under a `13.`-style prefix."""
    releases = pypi_project.get("releases", {})
    if wanted in releases:
        return wanted
    if wanted.endswith("."):
        candidates = [v for v, files in releases.items()
                      if v.startswith(wanted) and files
                      and not any(f.get("yanked") for f in files)]
        if candidates:
            return max(candidates, key=_version_key)
    raise RuntimeError(f"no release {wanted!r} of {pypi_project.get('info', {}).get('name')}")


def _version_key(version: str) -> tuple:
    parts = []
    for piece in version.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _fetch_wheel(dest: Path, package: str = "nvidia-cublas-cu12",
                 version: str = PACK_VERSION, tag: str = "win_amd64") -> None:
    """Download one pinned wheel from PyPI to dest (streamed)."""
    import json
    import ssl
    import urllib.request

    import certifi  # bundled by the PyInstaller certifi hook (#40)

    ctx = ssl.create_default_context(cafile=certifi.where())
    if version.endswith("."):
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json",
                                    context=ctx, timeout=30) as resp:
            version = _pick_version(json.load(resp), version)
    with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/{version}/json",
                                context=ctx, timeout=30) as resp:
        url = _wheel_url(json.load(resp), tag)
    log.info("downloading GPU support pack piece: %s", url)
    with urllib.request.urlopen(url, context=ctx, timeout=60) as resp, \
            open(dest, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)


# ---- install and activate ------------------------------------------------------

def install_pack(cuda_dir: Path = CUDA_DIR,
                 fetch: Callable[..., None] = _fetch_wheel,
                 edition: GpuPackEdition | None = None,
                 engine: str = "faster-whisper", caps=None) -> None:
    """Download the edition's wheels, extract exactly its files into the
    edition's dir and activate it for this session. Raises on any failure;
    never leaves a partial pack behind (`pack_installed` stays False).

    `fetch(dest, package, version, tag)` is the PyPI download; tests hand in
    a stand-in that writes a wheel-shaped zip."""
    if edition is None:
        edition = edition_for(engine, caps)
    if edition is None:
        raise RuntimeError(f"no GPU support pack edition for {engine} here")
    dest = pack_dir(edition, cuda_dir)
    dest.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    try:
        for package, version in edition.sources:
            wheel = dest / f"{package}.whl"
            try:
                _call_fetch(fetch, wheel, package, version, edition.wheel_tag)
                extracted += _extract(wheel, edition.files, dest)
            finally:
                wheel.unlink(missing_ok=True)
        missing = [pattern for pattern in edition.files if not _has(dest, pattern)]
        if missing:
            raise RuntimeError(f"wheels are missing {', '.join(missing)}")
    except BaseException:
        for path in extracted:
            path.unlink(missing_ok=True)
        raise
    activate(edition, cuda_dir)


def _call_fetch(fetch, wheel, package, version, tag) -> None:
    """The real fetch takes the PyPI coordinates; a stand-in may take just
    `dest` (the M3 test seam)."""
    import inspect

    try:
        arity = len(inspect.signature(fetch).parameters)
    except (TypeError, ValueError):
        arity = 4
    if arity >= 4:
        fetch(wheel, package, version, tag)
    else:
        fetch(wheel)


def _extract(wheel: Path, patterns: tuple[str, ...], dest: Path) -> list[Path]:
    written: list[Path] = []
    with zipfile.ZipFile(wheel) as zf:
        for member in zf.namelist():
            name = Path(member).name
            if not name or member.endswith("/"):
                continue
            if any(fnmatch.fnmatch(name, p) if _is_glob(p) else name == p for p in patterns):
                target = dest / name
                with zf.open(member) as src, open(target, "wb") as out:
                    while chunk := src.read(1 << 20):
                        out.write(chunk)
                written.append(target)
    return written


_preloaded: dict[str, object] = {}


def activate(edition: GpuPackEdition, cuda_dir: Path = CUDA_DIR) -> None:
    """Make an installed edition reachable for this process: PATH prepend on
    Windows; on Linux a `ctypes.CDLL` preload of each library, in the
    edition's dependency order, with the default `RTLD_LOCAL` — a later
    `dlopen("libcublas.so.12")` by the engine resolves to the already-loaded
    soname without any search path."""
    dest = pack_dir(edition, cuda_dir)
    if edition.activation == "path":
        os.environ["PATH"] = str(dest) + os.pathsep + os.environ.get("PATH", "")
        return
    for pattern in edition.files:
        paths = sorted(dest.glob(pattern)) if _is_glob(pattern) else [dest / pattern]
        for path in paths:
            if not path.exists() or str(path) in _preloaded:
                continue
            try:
                _preloaded[str(path)] = ctypes.CDLL(str(path))   # RTLD_LOCAL
            except OSError:
                log.warning("GPU support pack: could not preload %s", path, exc_info=True)


def activate_installed(cuda_dir: Path = CUDA_DIR, caps=None) -> None:
    """At startup: activate every edition already on disk, before any
    engine touches CUDA. Editions coexist (RTLD_LOCAL), so both are loaded
    where both are installed. Never raises."""
    try:
        editions = _editions(caps)
    except Exception:
        log.debug("gpu pack editions unavailable", exc_info=True)
        return
    for edition in editions.values():
        try:
            if _present(edition, pack_dir(edition, cuda_dir)):
                activate(edition, cuda_dir)
        except Exception:
            log.debug("activating %s failed", edition.key, exc_info=True)
