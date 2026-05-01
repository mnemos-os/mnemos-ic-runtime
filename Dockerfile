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

# Copy v4.0 bridge code (MnemosClient, MCP server wrappers, dashboard static files)
# These live in this repo (mnemos-os/mnemos-ic-runtime) at /bridge
COPY bridge/ /build/bridge/
COPY dashboard/ /build/dashboard/
# Non-editable install: bridge code lands in venv site-packages, survives
# the COPY --from=builder /build/.venv → /opt/ic-engine/.venv hop. Editable
# (-e) would leave a venv .pth pointing at /build/bridge, which doesn't
# exist in the runtime stage.
# WITH deps: fastapi, uvicorn, mcp, httpx, sqlalchemy, aiosqlite, pydantic,
# structlog from the bridge's pyproject.toml. Without these, serve.py
# import-fails.
RUN UV_PROJECT_ENVIRONMENT=/build/.venv uv pip install --python /build/.venv/bin/python /build/bridge

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
