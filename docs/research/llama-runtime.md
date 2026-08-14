# llama.cpp runtime & model sourcing on Windows CPU (research, 2026-07-28)

Ticket: #28 (M2 ticket 01 — llama.cpp runtime research). Feeds the benchmark ticket #29.

> **Superseded for the wheel choice, 2026-08-10 (#116).** Everything below about models,
> settings and download mechanics still holds; §1's *CPU* wheel index does not. The app now
> installs the **Vulkan** build. See §6.

## 1. Install: llama-cpp-python, prebuilt CPU wheels — yes, no compiler needed

**Prebuilt CPU wheels exist for Windows and cover our Python.** The abetlen project publishes an
official CPU wheel index; as of the latest release, `cp311-win_amd64` and `cp312-win_amd64`
wheels are present for v0.3.34 (and back through 0.3.x). No MSVC/CMake needed.

- Wheel index (verified win_amd64 cp311/cp312 wheels up to 0.3.34):
  https://abetlen.github.io/llama-cpp-python/whl/cpu/llama-cpp-python/
- Install docs: https://github.com/abetlen/llama-cpp-python (README "Supported Backends")
- PyPI (source dist only on PyPI itself; latest 0.3.34, released 2026-07-12):
  https://pypi.org/project/llama-cpp-python/

**Install command (pip semantics):**

```
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

**For this repo's uv-managed `.venv`**, add the index in `pyproject.toml` so the wheel (not the
sdist) is chosen:

```toml
[[tool.uv.index]]
name = "llama-cpp-python-cpu"
url = "https://abetlen.github.io/llama-cpp-python/whl/cpu"
explicit = true

[tool.uv.sources]
llama-cpp-python = { index = "llama-cpp-python-cpu" }
```

then `uv add llama-cpp-python`. (One-off equivalent:
`uv pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu`.)

### Maintenance status & alternatives

- **abetlen/llama-cpp-python is alive**: 10.5k stars, latest release 0.3.34 on 2026-07-12,
  wheels published per release. Caveat: the bundled OpenAI-compatible `llama_cpp.server`
  component is semi-deprecated — irrelevant to us, we use the in-process Python API.
  (https://github.com/abetlen/llama-cpp-python, https://pypi.org/project/llama-cpp-python/)
- **Fallback A — JamePeng fork**: tracks llama.cpp more aggressively; its 2026 Windows wheels do
  dynamic CPU-backend loading (GGML picks the best CPU backend at runtime).
  https://github.com/JamePeng/llama-cpp-python
- **Fallback B — llama.cpp `llama-server` binary over HTTP**: ggml-org publishes official
  Windows CPU zips on every release (https://github.com/ggml-org/llama.cpp/releases); talk to it
  with plain HTTP/OpenAI-style requests. More moving parts (child process lifecycle) but zero
  Python-binding risk. Not needed unless the binding stalls.

**Recommendation**: abetlen llama-cpp-python from the official CPU wheel index. It is
maintained, wheel-installable on our exact platform, and in-process (no server process to manage
for load-on-toggle/unload semantics from charter decision 6).

## 2. Models: GGUF repos, Q4 sizes, licenses

Note: Qwen does not publish an official GGUF for the **2507 instruct** refresh of Qwen3-4B
(official `Qwen/Qwen3-4B-GGUF` is the older hybrid-thinking model, limited quants; official
`Qwen/Qwen3-1.7B-GGUF` carries only Q8_0 at 1.83 GB). The trusted community sources are
**unsloth** and **bartowski** (and ggml-org). Sizes below verified on the HF file trees.

| Rung | Repo id | Q4 file | Size | License |
|---|---|---|---|---|
| ~4B | `unsloth/Qwen3-4B-Instruct-2507-GGUF` | `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` | 2.5 GB | Apache-2.0 |
| ~3B | `bartowski/Llama-3.2-3B-Instruct-GGUF` | `Llama-3.2-3B-Instruct-Q4_K_M.gguf` | 2.02 GB | Llama 3.2 Community |
| ~1.7B | `unsloth/Qwen3-1.7B-GGUF` | `Qwen3-1.7B-Q4_K_M.gguf` | 1.11 GB | Apache-2.0 |
| ~1B | `bartowski/Llama-3.2-1B-Instruct-GGUF` | `Llama-3.2-1B-Instruct-Q4_K_M.gguf` | 808 MB | Llama 3.2 Community |

Sources:
- https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/tree/main (also Q4_0 2.38 GB, Q4_K_S 2.38 GB)
- https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF (alternate 4B source)
- https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/tree/main (Q4_K_S 1.93 GB, Q4_0 1.92 GB)
- https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/tree/main (Q4_0 1.06 GB)
- https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/tree/main (Q8_0 1.32 GB if Q4 quality disappoints at 1B)
- Official Qwen GGUFs: https://huggingface.co/Qwen/Qwen3-1.7B-GGUF (Q8_0 only), https://huggingface.co/Qwen/Qwen3-4B-GGUF

License notes:
- **Apache-2.0** (Qwen3): unrestricted commercial use, no attribution gate — cleanest for shipping.
- **Llama 3.2 Community License**: fine for our scale (restrictions kick in at >700M MAU) but
  requires "Built with Llama" attribution and license passthrough; the bartowski/hugging-quants
  GGUF repos are not gated (no access request), unlike `meta-llama/*` originals.
- Preference implication: Qwen3 rungs are license-simplest; Llama rungs are ladder alternates.

> **Reversed 2026-08-08 (#112).** All four rungs ship, Llama ones included: the ladder needs a
> 1B floor and a 3B middle, and Qwen3 publishes no GGUF at either. The attribution and
> passthrough that buys is recorded in the repo's `NOTICE`.

Qwen3-specific: the 2507 "Instruct" variant is non-thinking by design (good — no `<think>`
tokens to strip, no wasted latency). The older `Qwen3-1.7B` is a hybrid model whose chat
template defaults to thinking **ON**.

> **Corrected 2026-08-08 (#112).** This paragraph originally said the template's default was
> off and that `/no_think` was one of two equivalent switches. Both were wrong. There is no
> "default off switch": with thinking enabled the template emits a `<think>` block **even when
> it is empty**, so `/no_think` suppresses the reasoning but not the tags — the app strips
> `<think>...</think>` from every reply before the diff-guard sees it. The tidier
> `enable_thinking=False` cannot be passed at all from `llama_cpp`:
> `Llama.create_chat_completion` has an explicit signature with no `**kwargs` and no
> `chat_template_kwargs` (verified against source).

## 3. Runtime settings for the short-prompt cleanup workload

Workload shape: static short system prompt + ≤120 s transcript (≈300–500 tokens in), cleaned
text out (≈ same length). Latency budget: flow ≤3.5 s for a 10 s utterance.

- **`n_threads`**: number of **physical performance cores**, not logical CPUs. Oversubscribing
  threads hurts CPU inference. Leave `n_threads_batch` default (same value is fine at our sizes).
  (https://github.com/ggml-org/llama.cpp/discussions/21112)
- **`n_ctx`**: 4096. A ≤120 s transcript plus system prompt and output fits comfortably; small
  contexts keep KV-cache RAM negligible at these model sizes. Batch size defaults (`n_batch=512`)
  are fine — at short prompt lengths batch tuning is noise.
- **Flash attention on CPU**: llama.cpp now defaults to `--flash-attn auto`; in llama-cpp-python
  leave `flash_attn` at its default. CPU FA gains are minor at 4k ctx — do not hand-force.
- **Prompt caching**: the system prompt is static, so KV reuse matters. In-process, a single
  long-lived `Llama` instance reuses the KV prefix automatically when consecutive calls share a
  prefix (`Llama` keeps state between `create_chat_completion` calls); additionally
  `llama_cpp.LlamaRAMCache` can be attached via `llm.set_cache(...)` to cache across divergent
  prompts. Keep one instance resident while flow mode is on (matches charter decision 6:
  load-on-toggle, unload on switch/pause).
- **Determinism/quality knobs for cleanup**: `temperature=0` (or very low), modest
  `max_tokens` cap tied to input length, and a stop condition — cleanup is a rewrite task, not
  generation.
- **Construction**: `Llama(model_path=..., n_ctx=4096, n_threads=<phys cores>, verbose=False)`.
  Use `model_path` pointing into `MODELS_DIR` (below) rather than `Llama.from_pretrained`, so the
  M1 storage pattern is kept.

**Ballpark throughput**: published CPU-only numbers for these exact models are thin; community
data points put ~3–4B Q4 models around 6–15 tok/s generation on mid-range desktop CPUs and
~1.5–2B Q4 around 20–35 tok/s, with prompt processing several times faster than generation
(e.g. Qwen2.5-3B ≈ 8 tok/s on a mid-range CPU: https://singhajit.com/llm-inference-speed-comparison/).
Treat these as ladder priors only — ticket 02 measures on the actual machine with
`llama-bench`-equivalent runs; the pre-agreed ladder (4B → 1.5–2B → flow off by default) absorbs
the uncertainty.

## 4. Download mechanics into `%LOCALAPPDATA%\Cadent\models`

Yes — `huggingface_hub.hf_hub_download` supports `local_dir`, matching the M1 `MODELS_DIR`
pattern from `cadent/stt.py` / `cadent/config.py`:

```python
from huggingface_hub import hf_hub_download
from .config import MODELS_DIR

path = hf_hub_download(
    repo_id="unsloth/Qwen3-4B-Instruct-2507-GGUF",
    filename="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    local_dir=MODELS_DIR / "llm",
)
```

Resume/integrity story (https://huggingface.co/docs/huggingface_hub/en/guides/download):

- With `local_dir`, the hub writes a `.cache/huggingface/` metadata folder inside that directory;
  re-runs skip files that are already up to date and re-fetch only changed ones.
- Downloads go through **Xet** (default since huggingface_hub 0.32; `hf_xet` installed
  automatically): files are fetched as content-addressed chunks resolved from the file's LFS
  SHA256, which gives chunk-level retry/resume and hash-anchored integrity. The old
  `resume_download` parameter is gone in v1.x — resuming is automatic
  (https://huggingface.co/docs/huggingface_hub/concepts/migration).
- Pin `revision=<commit sha>` in the download call if we want immutable model identity for the
  benchmark record.

## 6. GPU: the Vulkan wheel (#116, 2026-08-10)

Cleanup was CPU-only twice over — the CPU wheel index in §1, and a `Llama(...)` call that never
passed `n_gpu_layers`. Both are fixed; this section is the record of why Vulkan and not CUDA.

### The wheel

abetlen publishes per-backend indexes at `https://abetlen.github.io/llama-cpp-python/whl/<backend>`.
Live-checked 2026-08-10: `vulkan` serves **0.3.34 `py3-none-win_amd64`** — the same version the CPU
pin held — so the swap is a backend change, not a version bump.

| | CPU | Vulkan | cu124 |
|---|---|---|---|
| Wheel size | 6.6 MB | **42 MB** | 536 MB |
| Extra runtime DLLs to stage | none | **none** (`vulkan-1.dll` ships with every Windows GPU driver) | `cudart64_12.dll` — ggml links CUDA dynamically and the Windows wheels have no `delvewheel` repair step |
| Vendors covered | — | **AMD, Intel, NVIDIA** | NVIDIA |
| Tracks current releases | yes | yes | yes (cu124 and cu132 only) |

Vulkan also sidesteps a pin conflict CUDA would create with the existing GPU support pack. ROCm was
rejected outright — abetlen publishes no `rocm` index.

**The CUDA path stays deferred, not cancelled.** The research behind it — wheel sizes, which
`cu*` indexes exist, the missing-DLL analysis and its unrun `dumpbin /dependents` check — lives in
#116's issue thread and is not restated here; the one thing worth knowing from outside that thread
is that it was deferred for want of NVIDIA-only hardware to test on, not on merit.

Platform note: the Vulkan index publishes `win_amd64` and manylinux x86_64 only, where the CPU
index also carried macOS and aarch64. Every platform this Windows-only app is developed on is
covered; a macOS `uv sync --extra cleanup` is not, and that is the trade.

### Measured, on an RTX 4090 dev box

Not the Strix Halo machine #116 was reframed on, and not the CPU-only primary the charter binds
latency to — a third, **secondary** box. Read these as a ratio, not as a pass mark, and note that
the Vulkan path is unmeasured on AMD and Intel: it is chosen there on vendor-neutrality, not on
evidence.

Qwen3-4B-Instruct-2507-Q4_K_M, `n_ctx=4096`, a ~30-word transcript, `max_tokens=128`,
temperature 0.

| | load | 1st generation | warm generation |
|---|---|---|---|
| `n_gpu_layers=0` (CPU) | 0.71 s | 4.44 s | **3.1–3.3 s** |
| `n_gpu_layers=-1` (Vulkan) | 1.6–2.5 s | **12.1 s, once per machine** | **0.16 s** |

Two findings, both load-bearing:

1. **~20x faster warm.** CPU cleanup at 3.1 s sits right against `cleanup_timeout(30) = 5.0 s`;
   Vulkan at 0.16 s is not in the same conversation.
2. **The first-ever generation costs 12.1 s** while the driver compiles llama.cpp's shader
   pipelines. It is *not* per load — a second process on the same machine measured 0.15 s, because
   this driver caches compiled pipelines on disk. Whether every vendor's does is unverified, which
   only strengthens the case for the warm-up. 12.1 s is 2.4x the cleanup deadline, so
   without a warm-up the first dictation after install silently returns raw. `Cleaner._warm_up`
   spends it inside the disclosed load window instead, and doubles as the #38 proof that the rung
   works at all — a `Llama` constructs happily on a GPU it cannot compute on, because llama.cpp
   builds its graph lazily.

## Bottom line for ticket 02

1. `uv add llama-cpp-python` with the abetlen CPU wheel index (no toolchain).
2. Ladder models: `unsloth/Qwen3-4B-Instruct-2507-GGUF` Q4_K_M (2.5 GB) →
   `unsloth/Qwen3-1.7B-GGUF` Q4_K_M (1.11 GB); Llama-3.2 3B/1B bartowski Q4_K_M as alternates.
3. One resident `Llama(n_ctx=4096, n_threads=<physical cores>)`, temperature 0, static system
   prompt for KV reuse.
4. `hf_hub_download(..., local_dir=MODELS_DIR / "llm")` — resume and integrity handled by Xet.
