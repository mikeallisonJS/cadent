"""Run-at-login via the HKCU Run key (M3 ticket 46) — Win32 adapter internals,
platform-skipped per ADR 0005; they run on the real Windows machines.

Tests write to a throwaway subkey so they never touch the real Run key.
"""

import uuid

import pytest

winreg = pytest.importorskip("winreg", reason="Win32 autostart adapter internals")

from cadent.platform.win32 import Win32Autostart  # noqa: E402


@pytest.fixture
def run_key():
    path = rf"Software\CadentTests\Run-{uuid.uuid4().hex}"
    yield path
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def _registered_command(path):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
        value, _ = winreg.QueryValueEx(key, "Cadent")
    return value


def test_enable_registers_the_command(run_key):
    autostart = Win32Autostart(key_path=run_key)
    autostart.set_enabled(True, command='"C:\\Cadent\\Cadent.exe"')
    assert autostart.is_enabled()
    assert _registered_command(run_key) == '"C:\\Cadent\\Cadent.exe"'


def test_disable_removes_the_registration(run_key):
    autostart = Win32Autostart(key_path=run_key)
    autostart.set_enabled(True, command='"C:\\Cadent\\Cadent.exe"')
    autostart.set_enabled(False)
    assert not autostart.is_enabled()


def test_disable_when_never_enabled_is_quiet(run_key):
    autostart = Win32Autostart(key_path=run_key)
    autostart.set_enabled(False)
    assert not autostart.is_enabled()


def test_frozen_build_registers_the_installed_exe(monkeypatch):
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", r"C:\Program Files\Cadent\Cadent.exe")
    assert Win32Autostart().run_command() == r'"C:\Program Files\Cadent\Cadent.exe"'


def test_dev_registers_pythonw_module_entrypoint(monkeypatch, tmp_path):
    (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr("sys.executable", str(tmp_path / "python.exe"))
    assert Win32Autostart().run_command() == f'"{tmp_path}\\pythonw.exe" -m cadent'


def test_dev_without_pythonw_falls_back_to_the_interpreter(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.executable", str(tmp_path / "python.exe"))
    assert Win32Autostart().run_command() == f'"{tmp_path}\\python.exe" -m cadent'


def test_enable_defaults_to_run_command(run_key, monkeypatch):
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", r"C:\Program Files\Cadent\Cadent.exe")
    Win32Autostart(key_path=run_key).set_enabled(True)
    assert _registered_command(run_key) == r'"C:\Program Files\Cadent\Cadent.exe"'
