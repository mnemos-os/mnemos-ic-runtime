# SPDX-License-Identifier: Apache-2.0
"""Entry point for the InvestorClaw v4.0 bridge.

Serves on two ports:
  :8090  — MCP-HTTP server (FastMCP) — the agent-facing tool surface
  :8092  — Dashboard web UI (FastAPI + static files) — the user-facing config UI

One uvicorn process, one Python interpreter. Both endpoints share the same
ic-engine session, sqlite db, and MnemosClient instance.

This file is intentionally minimal — it wires up the components defined
in sibling modules (mcp_server, dashboard, bundle, auth) and starts
uvicorn. Real implementation lands incrementally as each module fills in.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import structlog

logger = structlog.get_logger("investorclaw_bridge")


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


def main() -> int:
    """Start the bridge.

    Two listeners, one process:
      - MCP-HTTP at IC_MCP_BIND (default 0.0.0.0:8090)
      - Dashboard + REST at IC_DASHBOARD_BIND (default 0.0.0.0:8092)

    TODO[v4.0-impl]:
      1. Build the FastAPI app with both MCP-HTTP and dashboard routes
         mounted (via mcp_server + dashboard modules).
      2. Initialize ic-engine session + sqlite at /data/ic-engine.db
      3. Initialize MnemosClient pointing at $MNEMOS_BASE
      4. Load /data/bundle.json if present (resumes prior config)
      5. Start uvicorn with the app, both binds.
      6. Health endpoints at /healthz on both ports.

    For now: a placeholder stub that logs the intended startup sequence
    and exits 0. Replace with real implementation when codex orientation
    pass returns and mnemos-rs API surface is locked.
    """
    _configure_logging()

    logger.info(
        "bridge.start",
        mcp_bind=os.environ.get("IC_MCP_BIND", "0.0.0.0:8090"),
        dashboard_bind=os.environ.get("IC_DASHBOARD_BIND", "0.0.0.0:8092"),
        mnemos_base=os.environ.get("MNEMOS_BASE", "http://mnemos:5002"),
        ic_engine_db=os.environ.get("IC_ENGINE_DB", "/data/ic-engine.db"),
        portfolio_dir=os.environ.get("IC_PORTFOLIO_DIR", "/data/portfolios"),
    )
    logger.warning(
        "bridge.placeholder",
        message=(
            "v4.0.0a1 placeholder — bridge implementation pending. "
            "Real startup lands when codex orientation pass completes "
            "and mnemos-rs MCP-HTTP surface is locked."
        ),
    )

    # Placeholder: keep process alive for compose health checks during dev
    if os.environ.get("BRIDGE_DEV_LOOP") == "1":
        logger.info("bridge.dev_loop", interval_sec=60)
        try:
            while True:
                # In real impl, uvicorn's loop runs here.
                asyncio.run(asyncio.sleep(60))
                logger.info("bridge.heartbeat")
        except KeyboardInterrupt:
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
