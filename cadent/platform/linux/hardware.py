"""The Linux `HardwareProbe` (spec M6 §6, ADR 0010).

Ports one-for-one from Windows: `libcuda.so.1` (the driver's, not the
toolkit's) loadable means an NVIDIA driver; the same driver-API calls read
total VRAM; `/proc/cpuinfo` names the processor. New here: the
driver-CUDA-version fill (`cuDriverGetVersion()`), which gates the CUDA-13
Parakeet pack edition on R580+ drivers. No Direct3D, no Metal, and no Vulkan
probe — cleanup's Vulkan wheel finds the distro loader itself and lands `cpu`
silently without it.
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

LIBCUDA = "libcuda.so.1"


def _load_cuda(loader: Callable[[str], object] = ctypes.CDLL):
    return loader(LIBCUDA)


class LinuxHardware:
    def __init__(self, loader: Callable[[str], object] = ctypes.CDLL,
                 cpuinfo: Path = Path("/proc/cpuinfo")) -> None:
        self._loader = loader
        self._cpuinfo = cpuinfo

    def cuda_total_memory(self) -> float | None:
        """Total VRAM in GB via the CUDA driver API, or None. `cuInit` can
        block — callers run this off the UI thread (hardware.detect)."""
        try:
            cuda = _load_cuda(self._loader)
            if cuda.cuInit(0) != 0:
                return None
            device = ctypes.c_int()
            if cuda.cuDeviceGet(ctypes.byref(device), 0) != 0:
                return None
            total = ctypes.c_size_t()
            get_mem = getattr(cuda, "cuDeviceTotalMem_v2", None) or cuda.cuDeviceTotalMem
            if get_mem(ctypes.byref(total), device) != 0:
                return None
            return total.value / (1024 ** 3)
        except Exception:
            log.debug("CUDA driver probe failed", exc_info=True)
            return None

    def dx12_gpu_present(self) -> bool:
        return False

    def processor_name(self) -> str:
        try:
            for line in self._cpuinfo.read_text(encoding="utf-8",
                                                errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            log.debug("/proc/cpuinfo unreadable", exc_info=True)
        return ""

    def nvidia_driver_present(self) -> bool:
        try:
            _load_cuda(self._loader)
            return True
        except OSError:
            return False

    def metal_gpu_present(self) -> bool:
        return False

    def cuda_driver_version(self) -> int | None:
        try:
            cuda = _load_cuda(self._loader)
            version = ctypes.c_int()
            if cuda.cuDriverGetVersion(ctypes.byref(version)) != 0:
                return None
            return int(version.value)
        except Exception:
            log.debug("cuDriverGetVersion failed", exc_info=True)
            return None
