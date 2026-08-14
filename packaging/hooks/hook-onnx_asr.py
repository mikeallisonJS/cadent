"""No upstream hook exists. Ships load-bearing data files —
preprocessors/data/*.onnx, the mel-filterbank front ends every model runs its
audio through — that nothing imports, so PyInstaller archives the pure Python
and drops them. Parakeet (#72) needs nemo128.onnx; without it the engine
raises at load and the app falls back to Whisper with no visible reason.

Same shape as hook-faster_whisper.py, and for the same reason.

The metadata is load-bearing too: onnx_asr/__init__.py reads its own version
out of the installed dist-info at import time, which a freeze doesn't carry
unless asked. Without it the very first `import onnx_asr` raises
PackageNotFoundError ("No package metadata was found for onnx-asr") and
Parakeet is unusable in the installed app.
"""

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

datas = collect_data_files("onnx_asr") + copy_metadata("onnx-asr")
