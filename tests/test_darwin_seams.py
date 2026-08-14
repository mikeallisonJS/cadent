"""The darwin small seams (#147, spec §6) — adapter internals, platform-skipped
per ADR 0005; they run on real Macs (the M1 Max, CI's macos-14 leg).

Tests write plists and lock files under tmp_path so they never touch the real
LaunchAgents directory or the app's data dir.
"""

import plistlib
import shlex
import subprocess
import sys

import pytest

if sys.platform != "darwin":
    pytest.skip("darwin adapter internals", allow_module_level=True)

from cadent.platform.darwin import (  # noqa: E402
    LAUNCH_AGENT_LABEL,
    DarwinAutostart,
    DarwinDesktop,
    DarwinHardware,
    DarwinSingleInstance,
)

# ---- autostart: the LaunchAgent plist (spec §6.1) -----------------------------


def test_enable_writes_the_launch_agent(tmp_path):
    autostart = DarwinAutostart(agents_dir=tmp_path)
    autostart.set_enabled(True)
    assert autostart.is_enabled()

    payload = plistlib.loads((tmp_path / f"{LAUNCH_AGENT_LABEL}.plist").read_bytes())
    assert payload["Label"] == LAUNCH_AGENT_LABEL
    assert payload["ProgramArguments"] == [sys.executable, "-m", "cadent"]
    assert payload["RunAtLoad"] is True
    # No KeepAlive, ever: a crash-relaunch loop fighting single-instance is
    # worse than staying down.
    assert "KeepAlive" not in payload


def test_disable_deletes_the_plist(tmp_path):
    autostart = DarwinAutostart(agents_dir=tmp_path)
    autostart.set_enabled(True)
    autostart.set_enabled(False)
    assert not autostart.is_enabled()
    assert not (tmp_path / f"{LAUNCH_AGENT_LABEL}.plist").exists()


def test_disable_when_never_enabled_is_quiet(tmp_path):
    DarwinAutostart(agents_dir=tmp_path).set_enabled(False)


def test_enable_creates_the_agents_dir(tmp_path):
    autostart = DarwinAutostart(agents_dir=tmp_path / "Library" / "LaunchAgents")
    autostart.set_enabled(True)
    assert autostart.is_enabled()


def test_run_command_is_the_venv_module_entrypoint():
    # shlex-joined, so an interpreter path with a space stays one argument.
    assert DarwinAutostart().run_command() == \
        shlex.join([sys.executable, "-m", "cadent"])


def test_default_plist_lives_in_launch_agents():
    path = DarwinAutostart()._plist_path
    assert path.parent.name == "LaunchAgents"
    assert path.name == f"{LAUNCH_AGENT_LABEL}.plist"


# ---- single instance: fcntl.flock (spec §6.2) ---------------------------------


def test_first_acquire_wins(tmp_path):
    assert DarwinSingleInstance(lock_path=tmp_path / "cadent.lock").acquire() is True


def _flock_in_child(lock) -> int:
    """Run a real second process that tries the lock non-blocking.
    Exit 0 = it acquired, 1 = it was refused."""
    program = (
        "import fcntl, os, sys\n"
        f"fd = os.open({str(lock)!r}, os.O_RDWR | os.O_CREAT)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except OSError:\n"
        "    sys.exit(1)\n"
        "sys.exit(0)\n"
    )
    return subprocess.run([sys.executable, "-c", program]).returncode


def test_second_acquire_in_another_process_loses(tmp_path):
    lock = tmp_path / "cadent.lock"
    assert DarwinSingleInstance(lock_path=lock).acquire() is True
    assert _flock_in_child(lock) == 1


def test_lock_dies_with_the_holder(tmp_path):
    lock = tmp_path / "cadent.lock"
    assert _flock_in_child(lock) == 0
    # The holder exited; the kernel released its lock — no stale state.
    assert DarwinSingleInstance(lock_path=lock).acquire() is True


def test_acquire_creates_the_data_dir(tmp_path):
    lock = tmp_path / "Cadent" / "cadent.lock"
    assert DarwinSingleInstance(lock_path=lock).acquire() is True


# ---- desktop environment (spec §6.3) ------------------------------------------


def test_desktop_facts_are_sane():
    desktop = DarwinDesktop()
    # macOS exposes no readable global text scale; 1.0 is the honest no-op.
    assert desktop.text_scale_factor() == 1.0
    # Qt's contrastPreference() upstream is the sole darwin detector; the
    # seam's fallback answer is False.
    assert desktop.high_contrast() is False
    assert desktop.animations_enabled() in (True, False)


def test_open_path_never_raises(tmp_path):
    DarwinDesktop().open_path(tmp_path / "does-not-exist.txt")


# ---- hardware probes (spec §4, #146) ------------------------------------------


def test_hardware_facts_are_the_apple_silicon_column():
    probe = DarwinHardware()
    # No CUDA, no Direct3D, no NVIDIA driver — and a Metal GPU always: every
    # Apple Silicon Mac ships one, and Intel Macs are out of scope.
    assert probe.cuda_total_memory() is None
    assert probe.dx12_gpu_present() is False
    assert probe.nvidia_driver_present() is False
    assert probe.metal_gpu_present() is True


def test_processor_name_reads_the_real_chip():
    # sysctl machdep.cpu.brand_string — "Apple M1 Max" on the target machine,
    # "Apple <something>" on any supported Mac.
    assert DarwinHardware().processor_name().startswith("Apple")
