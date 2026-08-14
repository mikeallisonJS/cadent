"""Smoke-check the frozen build (#47): the exe starts, stays up, and holds
the single-instance mutex. Dictating into a real app stays a human step —
this proves the bundle launches, not that the mic works.

Usage: python scripts/smoke_frozen.py  (after scripts/build.py; quit any
running Cadent first — the mutex is global to the session)
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

EXE = Path(__file__).resolve().parent.parent / "dist" / "Cadent" / "Cadent.exe"
STARTUP_GRACE_S = 20    # imports + Qt + tray + background STT preload kickoff


def fail(msg: str) -> int:
    print(f"SMOKE FAIL: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    if not EXE.exists():
        return fail(f"{EXE} not found — run scripts/build.py first")

    proc = subprocess.Popen([str(EXE)])
    deadline = time.monotonic() + STARTUP_GRACE_S
    while time.monotonic() < deadline:
        code = proc.poll()
        if code == 1:
            return fail("exe exited 1 immediately — another Cadent is "
                        "already running; quit it and retry")
        if code is not None:
            return fail(f"exe died during startup (exit code {code}) — "
                        "see %LOCALAPPDATA%/Cadent/cadent.log")
        time.sleep(0.5)

    # A second copy must bounce off the mutex — proves the first one really
    # is Cadent up and running, not a zombie process object.
    second = subprocess.run([str(EXE)], timeout=30)
    if second.returncode != 1:
        proc.terminate()
        return fail(f"second instance exited {second.returncode}, expected 1 "
                    "(single-instance mutex not held)")

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    print(f"SMOKE OK: launched, alive after {STARTUP_GRACE_S}s, mutex held.")
    print("Manual step: run the exe and dictate once (raw + flow).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
