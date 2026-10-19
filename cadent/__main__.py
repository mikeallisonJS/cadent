"""Entry point: `python -m cadent` or the `cadent` script."""

from __future__ import annotations

import logging
import sys


def _enable_cuda_support_pack() -> None:
    """GPU-when-present (M3 packaging research, #40; ADR 0010): switch on
    every installed GPU support pack edition before anything can touch CUDA
    — a PATH prepend on Windows, where ctranslate2 delay-loads cuBLAS through
    the DLL search order, an RTLD_LOCAL preload on Linux, where
    LD_LIBRARY_PATH is fixed at exec. Absent packs leave the stt.py probes
    to fall back to CPU as usual."""
    from . import gpu_pack

    gpu_pack.activate_installed()


def _setup_logging() -> None:
    from .config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=DATA_DIR / "cadent.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> int:
    _enable_cuda_support_pack()
    from .app import CadentApp
    from .platform import current

    instance = current().single_instance
    if not instance.acquire():
        # On a desktop with no tray icon the second launch is the way to
        # Settings (ADR 0014); elsewhere the holder ignores the knock.
        instance.wake_other()
        print("Cadent is already running.", file=sys.stderr)
        return 1
    _setup_logging()
    logging.getLogger(__name__).info("Cadent starting (frozen=%s)",
                                     getattr(sys, "frozen", False))
    app = CadentApp()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
