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
ADAPTERS = {"cadent.platform.win32", "cadent.platform.darwin"}
FACTORY = "cadent.platform"

# Top-level distributions that exist on one OS only. `ctypes.windll` is an
# attribute, not a module, so the scan checks for it separately.
OS_ONLY_MODULES = {
    "winreg", "winsound", "msvcrt", "_winapi",             # Windows
    "AppKit", "Quartz", "Foundation", "Cocoa", "objc",     # macOS (pyobjc)
    "ApplicationServices",                                 # macOS (pyobjc, #144)
    "fcntl",                                               # Unix only
}

PACKAGE_DIR = Path(cadent.__file__).parent


def _walk_error(name):
    # Without this, walk_packages swallows an ImportError raised while
    # descending into a subpackage and its submodules silently never get
    # parametrized — a shrunken walk that still looks green.
    raise RuntimeError(f"import walk could not descend into {name}")


def walkable_modules():
    yield "cadent"
    for info in pkgutil.walk_packages([str(PACKAGE_DIR)], prefix="cadent.",
                                      onerror=_walk_error):
        if info.name not in ADAPTERS:
            yield info.name


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
        if imported in ADAPTERS and name != FACTORY:
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
