"""No upstream hook exists (packaging research #40). Ships a load-bearing
data file — assets/silero_vad_v6.onnx — that stt.py's vad_filter=True
depends on; without it every dictation breaks."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("faster_whisper")
