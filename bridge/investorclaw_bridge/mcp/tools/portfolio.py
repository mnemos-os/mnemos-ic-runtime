# SPDX-License-Identifier: Apache-2.0
"""Portfolio analysis tools — pure handlers + tool descriptors.

The functions here are transport-agnostic: they take primitive args
(strings/dicts), return primitive dicts, and do not reference FastMCP
or FastAPI directly. transport.py wires them to MCP @app.tool()
decorators (FastMCP) and FastAPI POST routes.

This separation mirrors v5 mnemos's mnemos/mcp/tools/{memory,dag,kg,...}.py
domain split — handlers are testable in isolation and can be re-wired to
a different transport (e.g., SSE) without touching tool logic.

Tool naming: `portfolio_<verb>` follows the canonical v4.0 dockerized-skill
naming convention (`<domain>_<action>`) for cross-runtime compatibility
with the agentic-cobol harness.
"""
from __future__ import annotations

from typing import Any

from .._runtime import _run_ic_engine


# ──────────────────────────────────────────────────────────────────────
# Pure tool handlers (transport-agnostic)
# ──────────────────────────────────────────────────────────────────────


async def portfolio_ask(question: str) -> dict[str, Any]:
    """Natural-language portfolio question. Routes through ic-engine's
    deterministic pipeline, returns the structured ic_result envelope
    plus narrative text body.

    The engine refreshes stale sections (news TTL=30s) opportunistically; the
    cookie-cache-clear in `_runtime._clear_yfinance_cache` (commit 50387b1)
    breaks the rate-limit cascade that previously made multi-prompt sessions
    hang. Earlier `--no-refresh` here suppressed routing entirely (engine
    fell through to the catalog blurb), so it is intentionally NOT passed.
    """
    return await _run_ic_engine(["ask", question])


async def portfolio_holdings() -> dict[str, Any]:
    """Current holdings snapshot — positions, values, weights, account hierarchy.

    Equivalent to portfolio_ask with a fixed holdings-focused prompt; provides
    a more focused contract for callers that always want holdings data.
    """
    return await _run_ic_engine(
        ["ask", "What is in my portfolio? Show me holdings, values, and weights."]
    )


async def portfolio_refresh() -> dict[str, Any]:
    """Refresh market data without re-uploading portfolio files.

    Re-runs the ic-engine refresh pipeline against current portfolio files
    in /data/portfolios/. Pulls fresh prices via yfinance / FRED / Finnhub.
    Large portfolios (200+ positions) need ~3-5min on a cold yfinance cache,
    so timeout matches the broader subprocess default (600s).
    """
    return await _run_ic_engine(["refresh"], timeout_sec=1800.0)


async def portfolio_setup() -> dict[str, Any]:
    """Auto-discover portfolio files in /data/portfolios/.

    Use on first run or after the user uploads a new portfolio file. Returns
    a summary of detected files (pdf/xls/csv) and engine readiness.
    """
    return await _run_ic_engine(["setup"])


# ──────────────────────────────────────────────────────────────────────
# Tool descriptors — registry shape mirrors v5 mnemos
# ──────────────────────────────────────────────────────────────────────


def _tool(description: str, parameters: dict, required: list[str], handler) -> dict:
    """Mirror of v5 mnemos `_tool()` factory — produces the registry entry shape."""
    return {
        "description": description,
        "parameters": parameters,
        "required": required,
        "handler": handler,
    }


TOOLS: dict[str, dict[str, Any]] = {
    "portfolio_ask": _tool(
        description=(
            "Ask a natural-language portfolio question. Routes through "
            "ic-engine's deterministic pipeline. Use this for any portfolio "
            "question the user asks in plain English — InvestorClaw figures "
            "out which analyzer to run."
        ),
        parameters={
            "question": {
                "type": "string",
                "description": (
                    "The user's portfolio question, e.g. 'What is in my "
                    "portfolio?', 'How is performance?', 'What are my biggest "
                    "tech holdings?'"
                ),
            },
        },
        required=["question"],
        handler=portfolio_ask,
    ),
    "portfolio_holdings": _tool(
        description=(
            "Get the current portfolio holdings snapshot — positions, values, "
            "weights, account hierarchy as structured data."
        ),
        parameters={},
        required=[],
        handler=portfolio_holdings,
    ),
    "portfolio_refresh": _tool(
        description=(
            "Refresh market data without re-uploading portfolio files. Re-runs "
            "the ic-engine refresh pipeline against current portfolio files in "
            "/data/portfolios/."
        ),
        parameters={},
        required=[],
        handler=portfolio_refresh,
    ),
    "portfolio_setup": _tool(
        description=(
            "Auto-discover portfolio files in /data/portfolios/. Use on first "
            "run or after the user uploads a new portfolio file."
        ),
        parameters={},
        required=[],
        handler=portfolio_setup,
    ),
}
