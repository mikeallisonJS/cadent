"""The Linux HardwareProbe: the NVIDIA driver through `libcuda.so.1`, the CPU
name off `/proc/cpuinfo` (spec §6.2, ADR 0010).

No Direct3D, no Metal, and no Vulkan probe: cleanup enumerates its own
accelerator, so nothing here needs to know whether one exists.
"""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path

from .. import fallback

log = logging.getLogger(__name__)

CPUINFO = Path("/proc/cpuinfo")


class ProcHardware(fallback.NullHardware):
    def __init__(self, cpuinfo: Path = CPUINFO) -> None:
        self._cpuinfo = cpuinfo

    @staticmethod
    def _libcuda() -> ctypes.CDLL:
        return ctypes.CDLL("libcuda.so.1")

    def cuda_total_memory(self) -> float | None:
        """Total VRAM in GB via the CUDA driver API, or None (total, not
        free — stt's encode probe self-corrects an over-optimistic pick)."""
        try:
            cuda = self._libcuda()
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

    def processor_name(self) -> str:
        try:
            for line in self._cpuinfo.read_text(errors="replace").splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "model name":
                    return value.strip()
        except OSError:
            log.debug("cpuinfo read failed", exc_info=True)
        return ""

    def nvidia_driver_present(self) -> bool:
        """`libcuda.so.1` ships with every NVIDIA driver — loadable means GPU
        hardware."""
        try:
            self._libcuda()
            return True
        except OSError:
            return False

    def cuda_driver_version(self) -> int | None:
        """`cuDriverGetVersion` off the driver: 13000 and up is R580+, the
        floor of the Parakeet CUDA edition."""
        try:
            version = ctypes.c_int()
            if self._libcuda().cuDriverGetVersion(ctypes.byref(version)) != 0:
                return None
            return version.value
        except Exception:
            log.debug("CUDA driver version probe failed", exc_info=True)
            return None
