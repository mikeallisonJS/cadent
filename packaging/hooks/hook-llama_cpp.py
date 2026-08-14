"""No upstream hook exists (packaging research #40). The wheel is py3-none:
llama_cpp/lib/*.dll are loaded via ctypes, nothing links them, so PyInstaller
collects nothing without this. collect_dynamic_libs preserves the lib/
subpath the loader resolves relative to __file__."""

from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs("llama_cpp")
