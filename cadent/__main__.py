"""Entry point: `python -m cadent` or the `cadent` script."""

from __future__ import annotations

import logging
import sys


def _enable_cuda_support_pack() -> None:
    """GPU-when-present (M3 packaging research, #40; M6 ADR 0010): activate
    every installed support-pack edition before anything can touch CUDA —
    a PATH prepend on Windows (ctranslate2 delay-loads cuBLAS through the
    DLL search order; os.add_dll_directory does not apply), an RTLD_LOCAL
    preload on Linux. Absent, the stt.py probes fall back to CPU as usual."""
    from . import config, gpu_pack

    gpu_pack.activate_installed(config.CUDA_DIR)


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

    if not current().single_instance.acquire():
        print("Cadent is already running.", file=sys.stderr)
        return 1
    _setup_logging()
    logging.getLogger(__name__).info("Cadent starting (frozen=%s)",
                                     getattr(sys, "frozen", False))
    app = CadentApp()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
