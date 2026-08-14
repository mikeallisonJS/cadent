"""Entry point: `python -m cadent` or the `cadent` script."""

from __future__ import annotations

import logging
import os
import sys


def _enable_cuda_support_pack() -> None:
    """GPU-when-present (M3 packaging research, #40): ctranslate2 delay-loads
    cuBLAS through the plain Windows DLL search order, which consults the
    process PATH — os.add_dll_directory does not apply. Prepend the optional
    support-pack dir before anything can touch CUDA; if it's absent, the
    stt.py probe falls back to CPU as usual."""
    from .config import CUDA_DIR

    if CUDA_DIR.is_dir():
        os.environ["PATH"] = str(CUDA_DIR) + os.pathsep + os.environ.get("PATH", "")


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
