# Handoff to author claude — clio torch/transformers trim

**From:** mnemos-ic-runtime claude (owns the v4.0 OCI container + bridge)
**To:** ic-engine + clio author claude (owns the upstream Python + Apache-2.0 substrate)
**Date:** 2026-05-01
**Cross-references:** `sbom/ic-engine-4.0-cpu.bom.md`, `sbom/ic-engine-4.0-cpu.spdx.json`,
`bridge/frozendict_shim/__init__.py`

---

## TL;DR

`clio` declares `sentence-transformers` as a hard dependency, which transitively
pulls **torch + transformers + safetensors + tokenizers + huggingface-hub +
nvidia-cu13/* + triton**. On the v4.0 ic-engine container that's about
**3.5–4 GB of binary weight**. We've trimmed CUDA at the runtime layer (full
GPU torch + nvidia/* + triton uninstalled, replaced with CPU-only torch — see
`Dockerfile` lines 70–81). That got the image from 6.20 GB → 2.21 GB.

But the trim should ideally happen upstream in clio, because:

1. The actual usage of torch in clio is **two functions, both replaceable** —
   one is hardware probing (`torch.cuda.is_available()`) that already has a
   CPU fallback via `HardwareProfile`, the other is sentence-transformers-based
   semantic column matching that has cheaper alternatives (rapidfuzz / fastembed).
2. **No host in our deployment topology actually has CUDA** that the engine
   targets — see fleet survey below. The GPU code path is dead-ended for
   ~100% of installations.
3. **Prod mnemos at PYTHIA :5002 has the same dead torch in its venv** and
   it doesn't use it either. The actual prod stack uses `fastembed` (ONNX,
   Apache-2.0, ~10–20 MB) for embeddings and `openvino_genai` (Apache-2.0)
   for Phi-3.5 inference. **Neither imports torch.** That's our blueprint.

---

## 1. Where torch is actually used in clio

Two import sites, both in `try/except ImportError` already or trivially
replaceable.

### 1a. `clio/runtime/hardware.py:816` — GPU detection

```python
def detect_device(...) -> str:
    """Returns: 'cuda' | 'mps' | 'cpu'."""
    override = os.environ.get("CLIO_DEVICE")
    if override:
        return override.strip().lower()

    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass

    profile = hardware if hardware is not None else HardwareProfile()
    if profile.gpu.available:
        for dev in profile.gpu.devices:
            if dev.vendor == "apple":
                return "mps"
            if dev.vendor in {"nvidia", "amd", "intel"} and not dev.integrated:
                return "cuda"
    return "cpu"
```

**Five lines of torch usage, all just to ask "is there a GPU."** Already wraps
the import in `try/except ImportError` with `HardwareProfile`-based fallback
that returns `"cuda"`/`"mps"`/`"cpu"` correctly without touching torch.

**Recommended action:** delete the torch branch. The `HardwareProfile`
fallback already handles this. Or replace torch with a 3-line stdlib check:

```python
try:
    import subprocess
    has_cuda = subprocess.run(["nvidia-smi"], capture_output=True).returncode == 0
except (FileNotFoundError, OSError):
    has_cuda = False
```

### 1b. `clio/extract/schema_map.py:170` — semantic column matching

```python
def map_columns(self, source_columns, target_columns) -> dict[str, MappingResult]:
    # ...
    if self._model is None:
        self.warm_up()

    import torch
    from sentence_transformers import util

    embeddings_source = self._model.encode(source_list, convert_to_tensor=True, device=self._device)
    embeddings_target = self._model.encode(target_descriptions, convert_to_tensor=True, device=self._device)
    cosine_scores = util.cos_sim(embeddings_source, embeddings_target)

    for i, source_col in enumerate(source_list):
        best_idx = int(torch.argmax(cosine_scores[i]).item())
        # ...
```

This is the real use: when a user uploads a broker CSV with non-canonical
column names ("Sym" / "Pos Qty" / "Acct #"), this maps them to the CDM 5.x
schema using a sentence-transformers embedding model.

This is **NOT guarded** — it's a hard `import torch` inside the method body
that crashes if torch isn't installed.

**Recommended replacement:** swap sentence-transformers for `fastembed`. See
section 3.

---

## 2. Fleet GPU reality check

GPU ≠ CUDA. Several fleet hosts have GPUs, but the shape of those GPUs
matters. Surveyed via `lspci`:

| Host | IP | GPU | CUDA-capable? | Used via | Runs ic-engine? |
|---|---|---|---|---|---|
| **TYPHON** | 192.168.207.61 | NVIDIA RTX 5060 | yes | dev/barrage only | no (agent-dev rig) |
| **CERBERUS** | 192.168.207.96 | NVIDIA RTX 4500 Ada | yes | vLLM, Ollama (LLM serving) | no |
| **PYTHIA** | 192.168.207.67 | Intel Raptor Lake-P iGPU | **no** | **OpenVINO** (live: Phi-3.5 + fastembed) | mnemos-prod yes |
| **cixmini** | (Yocto edge) | NVIDIA Sky1 / CP8180 (Tegra) | yes (Tegra) | TensorRT / TRT-LLM | edge device, possibly |
| **PROTEUS** | 192.168.207.25 | Intel HD 530 (integrated) | no | OpenVINO-capable but unused | yes (batch CPU) |
| **ARGOS** | 192.168.207.22 | Intel Arrow Lake-S iGPU | no | not used | no (infra) |
| **bigpi** (Pi 5) | 192.168.207.65 | VideoCore VII | no | not for ML | yes (CPU only) |
| **clawpi** (Pi 4) | 192.168.207.54 | none | no | n/a | yes (CPU only) |
| **zeropi** (Pi 4 2GB) | 192.168.207.56 | none | no | n/a | client only (memory-tier) |
| Laptops / desktops | various | Apple M-series | no | MPS or CPU | yes (CPU + MPS) |

**The argument isn't "no fleet hosts have GPUs."** The argument is **none of
them are the GPU torch+CUDA targets.** The breakdown:

- The two NVIDIA-CUDA boxes (TYPHON, CERBERUS) are dev/serving rigs, not
  engine hosts.
- The Intel iGPU boxes (PYTHIA, PROTEUS, ARGOS) are usable via **OpenVINO**,
  not torch. Prod mnemos at PYTHIA already does this.
- The Tegra box (cixmini) wants **TensorRT / TRT-LLM**, also not the same
  torch CUDA wheel as a desktop NVIDIA card.
- Apple Silicon dev boxes can use **MPS** (which torch supports), but the
  whole CUDA wheel chain is wasted there too.
- The Pis use **CPU only**.

**The shipped GPU torch stack only works on TYPHON's RTX 5060** — a host that
doesn't run the engine. For literally every host that runs the engine, the
GPU code path hits `torch.cuda.is_available() → False` and falls through to
CPU. Carrying ~700 MB of torch (CPU build) + historically ~3 GB of CUDA libs
just to make that call is the wrong trade.

The right pattern, demonstrated by prod mnemos: pick the runtime that
matches the GPU shape on the box.

| GPU shape | Runtime that fits |
|---|---|
| Intel iGPU | OpenVINO (Apache-2.0) |
| Apple M-series | MPS via PyTorch (or Metal via mlx) |
| NVIDIA Tegra | TensorRT / TRT-LLM |
| NVIDIA desktop CUDA | torch+CUDA (only useful on the few dev rigs) |
| ARM CPU (Pis) | ONNX runtime or pure CPU |

`fastembed` happens to use ONNX runtime, which has providers for all of the
above (CPU is universal; CUDA EP, OpenVINO EP, CoreML EP are pluggable).
That's why it generalizes where torch + CUDA wheels don't.

---

## 3. The prod mnemos blueprint

**`/opt/mnemos/phi_server.py` at PYTHIA :5002 — the production memory service:**

```python
import openvino_genai as ov_genai          # Phi-3.5 inference, Intel iGPU + CPU
from fastembed import TextEmbedding         # 768-dim embeddings, ONNX runtime

# bootstrap
_embed_model = TextEmbedding("nomic-ai/nomic-embed-text-v1.5")
# ... use:
vec = _embed_model.embed(["query string"])
```

**No `import torch` anywhere in the mnemos source.** torch is in the venv
(transitively from `optimum-intel` + `huggingface_hub` extras) but it's dead
weight. The actual hot path uses fastembed + OpenVINO.

### Why fastembed (vs sentence-transformers)

| Property | sentence-transformers | fastembed |
|---|---|---|
| Backend | torch | ONNX runtime |
| License | Apache-2.0 (but pulls torch BSD + nvidia stack) | Apache-2.0, no torch needed |
| Size | ~1 GB stack | ~10–20 MB |
| Models | Same model family | Same model family (`nomic-embed-text-v1.5`, `BAAI/bge-small`, MiniLM, etc.) |
| API | `model.encode(strs) -> ndarray` | `model.embed(strs) -> generator[ndarray]` (or `.embed(strs, return_type='numpy')`) |
| Speed (CPU) | baseline | typically 1.3–2× faster |
| GPU | optional via CUDA torch | optional via ONNX CUDA EP, but doesn't bloat install |

The API surface for our use case is one-line different:

```python
# Before
from sentence_transformers import SentenceTransformer, util
model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5")
embeddings_source = model.encode(source_list, convert_to_tensor=True)
embeddings_target = model.encode(target_list, convert_to_tensor=True)
cosine_scores = util.cos_sim(embeddings_source, embeddings_target)

# After (fastembed)
from fastembed import TextEmbedding
import numpy as np
model = TextEmbedding("nomic-ai/nomic-embed-text-v1.5")
emb_src = np.array(list(model.embed(source_list)))
emb_tgt = np.array(list(model.embed(target_list)))
# normalize and cosine via numpy
emb_src_n = emb_src / np.linalg.norm(emb_src, axis=1, keepdims=True)
emb_tgt_n = emb_tgt / np.linalg.norm(emb_tgt, axis=1, keepdims=True)
cosine_scores = emb_src_n @ emb_tgt_n.T  # shape (n_src, n_tgt)
```

Same model, same vectors, same cosine similarity, no torch. ~5 lines.

---

## 4. Recommended changes to `clio` pyproject.toml

```diff
 dependencies = [
     "pymupdf>=1.24.0",      # consider replacing — see separate note re: AGPL-3.0
     "pillow>=10.0.0",
     "litellm>=1.48.0",
-    "sentence-transformers>=2.2.0",
+    "fastembed>=0.3.0",
     "rapidfuzz>=3.0.0",
     "polars>=0.20.0",
     "pyarrow>=12.0.0",
     "pydantic>=2.0.0",
     "pyyaml>=6.0",
     "python-dotenv>=1.0.0",
 ]
```

And in `clio/runtime/hardware.py:detect_device`, drop the `import torch`
branch. The `HardwareProfile` path already returns the correct device string.

In `clio/extract/schema_map.py:map_columns`, replace the
sentence-transformers + torch path with fastembed + numpy as shown above.

---

## 5. Optional — add a rapidfuzz fallback

For the case where `fastembed` itself isn't installed (e.g. someone strips
ML deps entirely for a deployment that just imports known broker formats),
clio could degrade gracefully:

```python
try:
    from fastembed import TextEmbedding
    USE_EMBEDDINGS = True
except ImportError:
    USE_EMBEDDINGS = False

def map_columns(self, source_columns, target_columns):
    if USE_EMBEDDINGS and self._model is not None:
        # ... fastembed cosine similarity path
    else:
        # rapidfuzz fallback
        from rapidfuzz import process, fuzz
        results = {}
        for source in source_columns:
            best = process.extractOne(source, target_columns, scorer=fuzz.WRatio)
            if best and best[1] > 60:
                results[source] = MappingResult(target=best[0], confidence=best[1] / 100)
        return results
```

`rapidfuzz` is already a clio dependency (line 9 of current pyproject.toml).
For ~80% of users with known brokers, the fuzzy fallback is plenty
("Sym" → "ticker", "Pos Qty" → "quantity"). The embeddings path is the
nice-to-have for unfamiliar formats.

---

## 6. Expected size impact

Cumulative trim if clio adopts the changes above:

| Stage | Image size | Δ |
|---|---|---|
| As-shipped v4.0 build (sept 2025-era torch+CUDA) | 6.20 GB | baseline |
| Strip CUDA + use CPU torch (already done in mnemos-ic-runtime Dockerfile) | 2.27 GB | -3.93 GB |
| Strip GPL/LGPL deps + frozendict shim (also done) | 2.21 GB | -60 MB |
| Replace sentence-transformers→fastembed in clio (proposed) | ~1.20 GB | -1.0 GB |
| Drop torch entirely (if fastembed lands) | ~0.95 GB | -250 MB |

**Final target: ~1 GB image.** Laptop-friendly even on 8 GB Pis. Still big
because it's a real scientific-Python service (pandas + scipy + polars +
pyarrow + the PDF stack), but ~6× smaller than today.

---

## 7. Other separate concerns flagged in the BOM

These are not part of this trim recommendation but the BOM surfaced them
and the author claude should be aware:

### License posture (separate handoff document already produced)

- `PyMuPDF` AGPL-3.0 — currently pulled by clio. We've stripped it from the
  runtime container; clio still declares it. Recommended replacement: drop
  to `pdfplumber` (already in ic-engine deps, MIT) + `pypdf` (BSD-3-Clause)
  for whatever clio.extract.vision actually does. SBOM: `sbom/ic-engine-4.0-cpu.bom.md`
- `premailer + cssutils + encutils` LGPL-3.0 — already gracefully optional
  in `ic_engine.rendering.template_engine`. Could just remove from the dep
  list since the code path tolerates it absent.
- `frozendict` LGPL-3.0 — pulled by `yfinance`. We replaced with an Apache-2.0
  shim (`bridge/frozendict_shim/__init__.py`). yfinance ≥ 0.2.40 dropped
  this dep; bumping yfinance fixes it without a shim.

### Other heavy unused-or-replaceable deps

- `tabula-py` 2.x — pulls Java JRE if invoked. Do we actually use it, or do
  camelot + pdfplumber cover the same job? If yes, drop tabula-py.
- `matplotlib` — currently imported at module level in
  `ic_engine/commands/optimize.py:41`. If the dashboard renders plots
  client-side (the v4.0 plan), can this become lazy/optional?
- `litellm` 62 MB — only used in `rendering/stonkmode.py` for narrative
  synthesis. Could split into a `narrative` extra so headless deployments
  skip it.

---

## 8. Verification snippets

If the author claude wants to verify any of this against the running prod
mnemos:

```bash
# Confirm fastembed + openvino path on prod mnemos
ssh jasonperlow@192.168.207.67 'grep -E "fastembed|openvino" /opt/mnemos/phi_server.py | head -5'

# Confirm torch is dead weight on prod mnemos (no actual import)
ssh jasonperlow@192.168.207.67 'grep -rn "import torch\|from torch" /opt/mnemos/*.py /opt/mnemos/mnemos/ 2>/dev/null | grep -v __pycache__'
# expected: empty output

# Confirm clio's torch usage in our trimmed runtime container
ssh jasonperlow@192.168.207.61 'docker exec ic-engine-smoke grep -rn "import torch\|from torch" /opt/ic-engine/.venv/lib/python3.12/site-packages/clio/'
# expected: hardware.py:816 + schema_map.py:170, both as discussed above
```

---

## 9. What I (mnemos-ic-runtime claude) am NOT doing

- I'm not patching clio directly — that's your repo, your call.
- I'm not patching ic-engine pyproject.toml — that's also your call.
- I'm continuing to apply runtime-container-layer fixes (CUDA strip,
  GPL/LGPL strip, frozendict shim, env-var translation) as defensive
  trims. These will keep working whether or not the upstream changes
  land.
- When upstream lands the trim, the runtime Dockerfile becomes simpler
  (just the env-var translation remains).

If you adopt the fastembed swap, ping back and I'll regenerate the SBOM
+ rebuild the container against the new clio.
