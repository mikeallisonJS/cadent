"""No upstream hook exists (packaging research #40). Collect the package DLLs
deterministically — ctranslate2.dll, libiomp5md.dll and the vestigial cudnn
shim sit loose in the package dir — instead of relying on import-table
walking from the extension module."""

from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs("ctranslate2")
