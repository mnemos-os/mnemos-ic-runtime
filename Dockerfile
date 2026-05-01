# SPDX-License-Identifier: Apache-2.0
# InvestorClaw 4.0 ic-engine container — bridge image for the v4.0 application service
#
# Builds: mnemos-os/ic-engine:4.0
# Pairs with: mnemos-os/mnemos-rs:4.2 (over compose bridge network)
#
# What's in this container:
#   - Python 3.12 + uv-managed venv
#   - perlowja/InvestorClaw ic-engine pinned to a specific SHA (set via build arg)
#   - FastMCP server at :8090
#   - Dashboard static files served at :8092
#   - MnemosClient (HTTP client to mnemos-rs at $MNEMOS_BASE)
#
# What's NOT in this container:
#   - Any agent runtime code
#   - Any user data (mounts /data volume from compose)
#   - Any raw API keys (mounts /data/keys.env at runtime)

# ============================================================================
# Stage 1: builder — fetch ic-engine source, install deps via uv
# ============================================================================
FROM python:3.12-slim AS builder

# Pinning ic-engine to a specific SHA (set via --build-arg).
# Default fills in at build time; production builds should pin explicitly.
ARG IC_ENGINE_REF=v2.6.3
ARG IC_ENGINE_REPO=https://github.com/perlowja/InvestorClaw.git

# uv install (canonical Python toolchain per project policy)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

# UV_INSTALL_DIR=/usr/local/bin tells the installer to drop uv directly there;
# no follow-up symlink needed (and creating one fails because uv already exists).
ENV UV_INSTALL_DIR=/usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && uv --version

# Clone ic-engine source at the pinned ref
WORKDIR /build
RUN git clone --depth 1 --branch ${IC_ENGINE_REF} ${IC_ENGINE_REPO} /build/ic-engine \
 || git clone ${IC_ENGINE_REPO} /build/ic-engine && cd /build/ic-engine && git checkout ${IC_ENGINE_REF}

# uv sync — produces a self-contained venv at /build/.venv
WORKDIR /build/ic-engine
RUN UV_PROJECT_ENVIRONMENT=/build/.venv uv sync --python 3.12 --frozen \
 || UV_PROJECT_ENVIRONMENT=/build/.venv uv sync --python 3.12

# uv sync installs the local project (`investorclaw`) editable by default,
# which writes a __editable___investorclaw_finder.py with MAPPING pointing
# at the build-stage path (/build/ic-engine/investorclaw). After we COPY
# the venv into the runtime stage, that path is gone, so `import investorclaw`
# fails with ModuleNotFoundError. Force a non-editable reinstall so the
# investorclaw module lands in site-packages and survives the stage hop.
RUN UV_PROJECT_ENVIRONMENT=/build/.venv uv pip install \
        --python /build/.venv/bin/python \
        --reinstall --no-deps /build/ic-engine

# Drop CUDA stack and replace with CPU-only torch.
# clio (transitive dep of ic-engine) pulls full GPU torch by default,
# which drags 2.7 GB of nvidia/* + 639 MB triton + 1.1 GB GPU torch.
# ic-engine does not use CUDA at runtime, so we strip the whole stack
# and reinstall CPU-only torch (~200 MB).
# Expected image-size win: ~4 GB.
#
# Note: uv-built venvs do not include pip, so we use `uv pip` (uv's
# pip-compatible CLI) for both list and uninstall. The earlier attempt
# using `python -m pip` failed silently inside xargs because pip isn't
# in the venv.
RUN set -ex; \
    PKGS=$(UV_PROJECT_ENVIRONMENT=/build/.venv uv pip list --python /build/.venv/bin/python --format=json \
       | /usr/local/bin/python3 -c "import json, sys; print(' '.join(p['name'] for p in json.load(sys.stdin) if p['name'].lower().startswith('nvidia') or p['name'].lower() in ('triton','torch')))"); \
    echo "uninstalling: $PKGS"; \
    UV_PROJECT_ENVIRONMENT=/build/.venv uv pip uninstall --python /build/.venv/bin/python $PKGS; \
    UV_PROJECT_ENVIRONMENT=/build/.venv uv pip install \
        --python /build/.venv/bin/python \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        --index-strategy unsafe-best-match \
        torch; \
    echo "verifying torch is CPU-only..."; \
    /build/.venv/bin/python -c "import torch; print('torch:', torch.__version__, 'cuda:', torch.cuda.is_available())"

# Strip GPL/LGPL packages to keep the runtime image redistribution-clean
# under Apache-2.0. The four targets we care about:
#
#   PyMuPDF (AGPL-3.0)  — pulled by clio for vision PDF extraction.
#                         clio guards `import fitz` in a try/except inside
#                         clio.extract.vision; uninstalling produces a
#                         graceful runtime error on vision-only paths.
#                         Non-vision paths are unaffected.
#   premailer (LGPL via cssutils) — used in ic_engine.rendering.template_engine
#                         for inlining CSS into HTML reports. The module
#                         already wraps `from premailer import Premailer` in
#                         try/except and sets PREMAILER_AVAILABLE=False on
#                         missing import; reports render without inlined CSS.
#   cssutils (LGPL-3.0)  — only used as premailer's dep.
#   encutils (LGPL-3.0)  — transitive of cssutils.
#   frozendict (LGPL-3.0) — yfinance imports `from frozendict import frozendict`
#                         unconditionally, so we replace with a tiny pure-Python
#                         Apache-2.0 shim. yfinance's use is hash-stability for
#                         dict cache keys; a subclass-of-dict shim suffices.
# Copy v4.0 bridge code (MnemosClient, MCP server wrappers, dashboard
# static files, frozendict shim) before any post-uv-sync surgery that
# references files in /build/bridge/.
COPY bridge/ /build/bridge/
COPY dashboard/ /build/dashboard/
# Non-editable install: bridge code lands in venv site-packages, survives
# the COPY --from=builder /build/.venv → /opt/ic-engine/.venv hop. Editable
# (-e) would leave a venv .pth pointing at /build/bridge, which doesn't
# exist in the runtime stage.
RUN UV_PROJECT_ENVIRONMENT=/build/.venv uv pip install --python /build/.venv/bin/python /build/bridge

RUN set -ex; \
    UV_PROJECT_ENVIRONMENT=/build/.venv uv pip uninstall \
        --python /build/.venv/bin/python \
        pymupdf premailer cssutils encutils frozendict || true; \
    cp -r /build/bridge/frozendict_shim /build/.venv/lib/python3.12/site-packages/frozendict; \
    /build/.venv/bin/python -c "from frozendict import frozendict; d=frozendict(a=1); h=hash(d); assert d['a']==1; print('frozendict shim ok, hash:', h)"; \
    echo "verifying GPL/LGPL strip..."; \
    /build/.venv/bin/python -c "import importlib.metadata as md; banned={'pymupdf','premailer','cssutils','encutils'}; found=[d.metadata['Name'] for d in md.distributions() if (d.metadata['Name'] or '').lower() in banned]; assert not found, f'still installed: {found}'; print('GPL/LGPL packages absent: pymupdf premailer cssutils encutils')"

# Strip the torch / transformers / OpenCV stack.
#
# clio declares sentence-transformers as a hard dep, which transitively
# pulls torch + transformers + safetensors + tokenizers + huggingface-hub +
# sympy + (in some configs) opencv-python-headless. After our CUDA strip
# above, that's still ~1 GB of binary weight in the venv. None of it is
# load-bearing for the v4.0 beta deterministic-engine path:
#
#   - clio/runtime/hardware.py:816 wraps `import torch` in try/except;
#     falls through to HardwareProfile-based "cuda"/"mps"/"cpu" detection.
#   - clio/extract/schema_map.py:170 has an unguarded `import torch`, but
#     that method (.map_columns) only fires when a user uploads a broker
#     CSV with non-canonical column names. For known broker formats
#     (UBS xls, Schwab CSV, etc.) the path never executes.
#   - The full author-claude fix is in
#     docs/handoff-2026-05-01-clio-torch-trim-for-author-claude.md
#     (swap sentence-transformers -> fastembed, ONNX-based, ~10-20 MB,
#     proven in prod mnemos at PYTHIA :5002).
#
# Until the upstream clio swap lands we strip the stack at the runtime
# layer. Unknown-broker schema mapping fails loudly on use — that's the
# documented v4.0 beta limitation.
RUN set -ex; \
    PKGS=$(UV_PROJECT_ENVIRONMENT=/build/.venv uv pip list --python /build/.venv/bin/python --format=json \
       | /usr/local/bin/python3 -c "import json, sys; ml={'torch','torchvision','torchaudio','functorch','torchgen','transformers','sentence-transformers','safetensors','tokenizers','huggingface-hub','accelerate','sympy','opencv-python','opencv-python-headless','onnxruntime-gpu'}; print(' '.join(p['name'] for p in json.load(sys.stdin) if p['name'].lower() in ml))"); \
    echo "stripping ML stack: $PKGS"; \
    UV_PROJECT_ENVIRONMENT=/build/.venv uv pip uninstall --python /build/.venv/bin/python $PKGS || true; \
    echo "verifying ML stack strip..."; \
    /build/.venv/bin/python -c "import importlib.metadata as md; banned={'torch','transformers','sentence-transformers','safetensors','tokenizers','huggingface-hub','sympy','opencv-python-headless'}; found=[d.metadata['Name'] for d in md.distributions() if (d.metadata['Name'] or '').lower() in banned]; assert not found, f'still installed: {found}'; print('ML stack absent: torch transformers sentence-transformers safetensors tokenizers huggingface-hub sympy opencv-python-headless')"; \
    echo "verifying clio still importable (its torch usage is lazy)..."; \
    /build/.venv/bin/python -c "import clio; print('clio import ok:', getattr(clio, '__version__', 'unknown'))"; \
    /build/.venv/bin/python -c "from clio.runtime.hardware import detect_device; print('clio device detection (no torch):', detect_device())"

# Post-strip cleanup: orphans + leftovers + optional bits.
#
#   scikit-learn (31 MB)   was a transitive of transformers; now orphaned.
#                          ic-engine doesn't import sklearn directly.
#   cuda-bindings/         NVIDIA's newer CUDA Python packages (cuda_*
#   cuda-pathfinder/       naming, missed by the earlier nvidia-* glob).
#   cuda-toolkit (~23 MB)  Orphaned now that torch is gone.
#   matplotlib + deps      Only used in ic_engine/commands/optimize.py:41
#   (~100 MB)              at module level. We hot-patch optimize.py to
#                          make the matplotlib import lazy at runtime,
#                          then strip the package. Optimize results are
#                          still computable (numpy/scipy/cvxpy do the
#                          math); only the inline chart rendering is lost.
#                          The dashboard renders charts client-side anyway.
#   litellm (61 MB)        Only used in ic_engine/rendering/stonkmode.py
#                          for narrative synthesis. The bridge calls
#                          Together AI / OpenAI / etc directly via httpx
#                          on the rendering path, so litellm is dead in
#                          v4.0. Drop it; if the engine's stonkmode
#                          renderer needs it, the import will fail loud.
RUN set -ex; \
    /usr/local/bin/python3 /build/bridge/patches/lazy_matplotlib_optimize.py \
        /build/.venv/lib/python3.12/site-packages; \
    PKGS=$(UV_PROJECT_ENVIRONMENT=/build/.venv uv pip list --python /build/.venv/bin/python --format=json \
       | /usr/local/bin/python3 -c "import json, sys; orphans={'scikit-learn','cuda-bindings','cuda-pathfinder','cuda-toolkit','litellm','matplotlib','fonttools','contourpy','cycler','kiwisolver','pillow'}; print(' '.join(p['name'] for p in json.load(sys.stdin) if p['name'].lower() in orphans))"); \
    echo "stripping orphans + optional: $PKGS"; \
    UV_PROJECT_ENVIRONMENT=/build/.venv uv pip uninstall --python /build/.venv/bin/python $PKGS || true; \
    echo "verifying ic-engine still importable..."; \
    /build/.venv/bin/python -c "import ic_engine.commands.optimize; print('optimize import ok (matplotlib lazy)')"; \
    /build/.venv/bin/python -c "import ic_engine; print('ic_engine ok')"

# ============================================================================
# Stage 2: runtime — minimal image with venv + bridge + dashboard
# ============================================================================
FROM python:3.12-slim AS runtime

# Runtime dependencies (libgomp for numpy/scipy on Debian slim, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
ARG USER_UID=1000
ARG USER_GID=1000
RUN groupadd -g ${USER_GID} ic && \
    useradd -u ${USER_UID} -g ${USER_GID} -m -s /bin/bash ic

# Copy venv + bridge + ic-engine + dashboard from builder
COPY --from=builder --chown=ic:ic /build/.venv /opt/ic-engine/.venv
COPY --from=builder --chown=ic:ic /build/ic-engine /opt/ic-engine/source
COPY --from=builder --chown=ic:ic /build/bridge /opt/ic-engine/bridge
COPY --from=builder --chown=ic:ic /build/dashboard /opt/ic-engine/dashboard

# Rewrite venv shebangs from /build/.venv → /opt/ic-engine/.venv so console
# scripts (investorclaw, investorclaw-bridge, etc.) execve cleanly. uv-built
# venvs hardcode absolute shebangs at install time; the COPY across stages
# leaves them pointing at the build-stage path that no longer exists in the
# runtime image. Without this, `exec investorclaw` fails with ENOENT despite
# the binary itself being present.
RUN find /opt/ic-engine/.venv/bin -type f -exec \
        sed -i '1s|^#!/build/.venv/bin/python.*|#!/opt/ic-engine/.venv/bin/python|' {} \; \
 && /opt/ic-engine/.venv/bin/python -c "import sys; print('venv ok:', sys.executable)"

# /data is the canonical mount point for compose volume
RUN mkdir -p /data/portfolios /data/reports && chown -R ic:ic /data

USER ic
WORKDIR /opt/ic-engine

# Environment defaults — overridable in compose env: block
ENV PATH="/opt/ic-engine/.venv/bin:${PATH}"

# Bridge-side env (read by investorclaw_bridge.serve / mcp_server)
ENV IC_PORTFOLIO_DIR=/data/portfolios
ENV IC_REPORTS_DIR=/data/reports
ENV IC_KEYS_FILE=/data/keys.env
ENV IC_MCP_BIND=0.0.0.0:8090
ENV IC_DASHBOARD_BIND=0.0.0.0:8092

# ic-engine reads its own canonical env-var names (INVESTOR_CLAW_*).
# Set them to the same values so subprocess'd analyzers honor /data/.
# Without these, ic-engine path_resolver.get_portfolio_dir() falls back
# to ~/portfolios (then to <skill_dir>/portfolios in site-packages).
ENV INVESTOR_CLAW_PORTFOLIO_DIR=/data/portfolios
ENV INVESTOR_CLAW_REPORTS_DIR=/data/reports
ENV INVESTOR_CLAW_DATED_REPORTS=false
ENV INVESTORCLAW_PORTFOLIO_DIR=/data/portfolios

ENV MNEMOS_BASE=http://mnemos:5002
ENV PYTHONUNBUFFERED=1

# Narrative-synthesis defaults — Together AI MiniMax-M2 / gemma4.
# Together is the fleet default per CLAUDE.md primary directive 5
# ("Anthropic forbidden as LLM provider for the nclawzero/zeroclaw stack;
#  MiniMax-via-Together is fleet default"). Cheaper than Gemini Pro by
# a wide margin. If the operator wants to point at Google instead, they
# should set INVESTORCLAW_NARRATIVE_MODEL to a *Flash* model — Pro is the
# expensive one (Pro: $1.25/M input, $5/M output; Flash: $0.10/$0.40).
# Last cost incident: $70/night API bill from accidental Gemini Pro use,
# 2026-04-30; documented in feedback_llm_provider_cost_policy.md.
#
# The bridge resolves $TOGETHER_API_KEY (read from /data/keys.env) into
# INVESTORCLAW_NARRATIVE_API_KEY at subprocess-spawn time, so operators
# only need to set the per-provider key file once.
ENV INVESTORCLAW_NARRATIVE_PROVIDER=openai_compat
ENV INVESTORCLAW_NARRATIVE_ENDPOINT=https://api.together.xyz/v1
ENV INVESTORCLAW_NARRATIVE_MODEL=MiniMaxAI/MiniMax-M2.7
# Consultation tier (deeper analysis, used by select code paths) — defaults
# to CERBERUS vLLM when running on the fleet network. Falls back to the
# narrative endpoint if unreachable. Operator can override per deployment.
ENV INVESTORCLAW_CONSULTATION_ENABLED=true
ENV INVESTORCLAW_CONSULTATION_ENDPOINT=http://192.168.207.96:8080
ENV INVESTORCLAW_CONSULTATION_MODEL=gemma4-consult
# Hybrid / local-first deployments can override:
#   INVESTORCLAW_NARRATIVE_ENDPOINT=http://192.168.207.96:11434/v1
#   INVESTORCLAW_NARRATIVE_MODEL=gemma4:e4b
# (CERBERUS Ollama on the fleet)

EXPOSE 8090 8092

# Healthcheck — overridden by compose for finer control
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
    CMD curl -sf http://127.0.0.1:8090/healthz || exit 1

# Entry point: bridge serves both MCP-HTTP (port 8090) and the dashboard (8092)
# in one process. Bridge code reads /data/bundle.json + /data/keys.env at start.
ENTRYPOINT ["/opt/ic-engine/.venv/bin/python", "-m", "investorclaw_bridge.serve"]

# Build-time labels (OCI image-spec)
LABEL org.opencontainers.image.title="InvestorClaw ic-engine"
LABEL org.opencontainers.image.description="Portfolio analysis service exposing MCP-HTTP at :8090 and a dashboard at :8092. Pairs with mnemos-os/mnemos-rs over compose."
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/mnemos-os/mnemos-ic-runtime"
LABEL org.opencontainers.image.documentation="https://investorclaw.app"
LABEL org.opencontainers.image.version="4.0"
