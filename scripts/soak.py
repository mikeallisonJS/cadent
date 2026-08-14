"""Repeat a pytest invocation and count the runs that died natively.

For flakes that kill the process rather than fail an assertion (#119): pytest
cannot report those, because it is not alive to report anything — all that is
left is an exit code, `0xC0000005` on Windows and `139` under bash. This counts
them and echoes the faulthandler frame, so "1 in 20" is a number rather than an
impression.

    uv run python scripts/soak.py 20 tests
    uv run python scripts/soak.py 5 tests/test_settings_ui.py tests/test_a11y_coverage.py

Every argument after the run count is passed through to pytest. Exit codes 0-5
are pytest's own (passed, failed, interrupted, internal error, usage, no
tests); anything else is the process being killed, which is what is counted.
"""

from __future__ import annotations

import subprocess
import sys

PYTEST_EXITS = frozenset(range(6))


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    runs, args = int(sys.argv[1]), sys.argv[2:]
    crashes = 0
    for i in range(runs):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no", *args],
            capture_output=True, text=True)
        crashed = result.returncode not in PYTEST_EXITS
        crashes += crashed
        lines = result.stdout.strip().splitlines()
        print(f"run {i + 1}: exit={result.returncode}{' CRASH' if crashed else ''}"
              f" | {lines[-1][:90] if lines else ''}", flush=True)
        if crashed:
            # The faulthandler traceback pytest printed before it died — the
            # only clue the run leaves behind about where it was.
            for line in lines:
                if " in test_" in line or "fatal" in line.lower():
                    print("   ", line.strip(), flush=True)
    print(f"{crashes}/{runs} crashed")
    return 1 if crashes else 0


if __name__ == "__main__":
    raise SystemExit(main())
