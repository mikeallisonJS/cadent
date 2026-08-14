"""PyInstaller entry script — the frozen equivalent of `python -m cadent`."""

import sys

from cadent.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
