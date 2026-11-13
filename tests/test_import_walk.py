"""The import rule, made real (spec §1.4, #143).

OS-specific imports — `winreg`, `ctypes.windll`, pyobjc's `AppKit`/`Quartz` —
may live only in per-OS platform modules, and only the factory imports those.
Two enforcements, because each catches what the other cannot:

- The walk imports every non-adapter module and asserts success. On the OS
  that *lacks* the import, a stray `winreg` fails here — but on the OS that
  has it, the import quietly works. That is why CI runs this on both.
- The source scan reads the same modules' ASTs for the banned names, so a
  violation fails on every OS, including the developer's own.
"""

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import cadent

# The per-OS adapter modules: the only legal homes for OS imports. Only the
# factory (cadent/platform/__init__.py) may import them.
# linux is a subpackage (ADR 0013): everything under it is adapter code.
ADAPTERS = {"cadent.platform.win32", "cadent.platform.darwin",
            "cadent.platform.linux"}
FACTORY = "cadent.platform"

# Top-level distributions that exist on one OS only. `ctypes.windll` is an
# attribute, not a module, so the scan checks for it separately.
OS_ONLY_MODULES = {
    "winreg", "winsound", "msvcrt", "_winapi",             # Windows
    "AppKit", "Quartz", "Foundation", "Cocoa", "objc",     # macOS (pyobjc)
    "ApplicationServices",                                 # macOS (pyobjc, #144)
    "fcntl",                                               # Unix only
    "jeepney", "Xlib",                                     # Linux (ADR 0013)
}

PACKAGE_DIR = Path(cadent.__file__).parent


def is_adapter(name: str) -> bool:
    return any(name == a or name.startswith(a + ".") for a in ADAPTERS)


def walkable_modules():
    """Every module outside an adapter, found without importing any package
    on the way down (`pkgutil.walk_packages` imports each package it
    descends into, which would import the linux adapter on an OS without
    jeepney)."""
    yield "cadent"

    def descend(path: Path, prefix: str):
        for info in pkgutil.iter_modules([str(path)]):
            name = prefix + info.name
            if is_adapter(name):
                continue
            yield name
            if info.ispkg:
                yield from descend(path / info.name, name + ".")

    yield from descend(PACKAGE_DIR, "cadent.")


def module_path(name: str) -> Path:
    parts = name.split(".")[1:]
    as_package = PACKAGE_DIR.joinpath(*parts, "__init__.py")
    return as_package if as_package.exists() else \
        PACKAGE_DIR.joinpath(*parts).with_suffix(".py")


@pytest.mark.parametrize("name", sorted(walkable_modules()))
def test_every_non_adapter_module_imports_on_this_os(name):
    importlib.import_module(name)


def imported_names(tree: ast.AST, package: list[str]):
    """Every module name a piece of source imports, top-level or nested —
    nested matters because `def f(): import winreg` still dies at call time
    on macOS, just later and harder to find. Relative imports are resolved
    against `package`, since `from . import win32` names an adapter just as
    surely as its absolute spelling."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                parts = package[:len(package) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            yield base
            yield from (f"{base}.{a.name}" for a in node.names)


def violations_in(name: str) -> list[str]:
    path = module_path(name)
    package = name.split(".") if path.name == "__init__.py" \
        else name.split(".")[:-1]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for imported in imported_names(tree, package):
        top = imported.split(".")[0]
        if top in OS_ONLY_MODULES:
            found.append(f"imports OS-only module {imported!r}")
        if is_adapter(imported) and name != FACTORY:
            found.append(f"imports adapter {imported!r} (only the factory may)")
        if imported == "ctypes.windll":
            found.append("imports ctypes.windll")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "windll":
            found.append("touches ctypes.windll")
    return found


def test_os_imports_live_only_in_adapters():
    report = {
        name: found
        for name in walkable_modules()
        if (found := violations_in(name))
    }
    assert report == {}


def test_the_scan_would_catch_a_real_adapter():
    """The scan proves it has teeth on the one file known to be full of OS
    imports — otherwise a scan that silently matched nothing would pass."""
    assert violations_in("cadent.platform.win32")


def test_the_platform_package_never_imports_the_gui_toolkit():
    """Adapters run off the GUI thread and some will load beside libdbus;
    Qt belongs to the UI layer alone (spec §1.4)."""
    found = {}
    for path in sorted((PACKAGE_DIR / "platform").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = [n for n in imported_names(tree, ["cadent", "platform"])
                if n.split(".")[0] == "PySide6"]
        if hits:
            found[path.name] = hits
    assert found == {}
