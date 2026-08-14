"""Cadent — fully-local push-to-talk AI dictation for Windows."""

from importlib.metadata import PackageNotFoundError, version

# pyproject.toml is the single source of truth for the version (#168); this
# just surfaces it. The frozen PyInstaller build doesn't collect package
# metadata, so it falls through — there the installer carries the version.
try:
    __version__ = version("cadent")
except PackageNotFoundError:
    __version__ = "0.0.0"
