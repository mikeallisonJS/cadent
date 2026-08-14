"""No upstream hook exists. Ships load-bearing data files —
preprocessors/data/*.onnx, the mel-filterbank front ends every model runs its
audio through — that nothing imports, so PyInstaller archives the pure Python
and drops them. Parakeet (#72) needs nemo128.onnx; without it the engine
raises at load and the app falls back to Whisper with no visible reason.

Same shape as hook-faster_whisper.py, and for the same reason.
"""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("onnx_asr")
