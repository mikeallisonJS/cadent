# macOS: one speech rung, and cleanup inherits llm_device with Metal underneath

On macOS both speech engines run on the CPU and nothing else: ctranslate2 ships no
Metal/MPS backend on arm64, and the CoreML execution provider crashes on Parakeet at
first inference (microsoft/onnxruntime#26355) — so `STT_RUNTIMES` on darwin is
`("auto", "cpu")` for both engines, `auto` stays the stored default (a one-rung
ladder is still a ladder), stray `cuda`/`directml` values sanitize back to `auto`,
and the Settings runtime combo hides because it would offer one choice twice.
Parakeet keeps probe-then-discard even on CPU — the first-inference execution-plan
pathology is ONNX-Runtime-level, not DirectML-level. Parakeet stays in the picker;
whether it earns the Recommended chip on Apple Silicon waits on a real M-series
bench (structure decided in #132: v2 Recommended at RAM ≥ 16 GB if the bench shows
roughly ≤ 1 s median insert on varied-length utterances, else the existing
RAM/cores Whisper branch), and `GPU_ONLY_ENGINES` is empty on darwin — no row is
ever disabled and "Needs a graphics card" never renders.

Cleanup introduces nothing macOS-specific: it inherits `llm_device` (#116, the
Vulkan ladder) unchanged — `auto` offloads every layer to the GPU the platform
build carries, Metal here rather than Vulkan, and commits only after a real
one-token generation; `cpu` remains the config-only escape hatch. Nothing replaces
the GPU support pack: Metal ships inside the llama.cpp build, so there is nothing
to download and the pack's wizard page and tray items are Windows-only surfaces.
The cleanup Recommended chip on darwin keeps the RAM-headroom test (unified memory
makes it more honest, not less — the GPU can't page its way out of a too-big model)
and drops `MAY_BE_SLOW`, whose physical-core gate mispredicts once the GPU does the
work.
