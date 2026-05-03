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

    Each successful response is auto-persisted to MNEMOS (best-effort) so
    the user can search history, retrieve by serial number (run_id), flag
    bad ones, and delete failures — see portfolio_response_* tools. The
    persisted mem_id is attached to the response under `mnemos_mem_id`
    when storage succeeds.
    """
    result = await _run_ic_engine(["ask", question])
    # Best-effort MNEMOS persistence; never raises, never blocks the response.
    try:
        from .responses import persist_response
        stored = persist_response(
            question=question,
            narrative=result.get("narrative") or "",
            ic_result=result.get("ic_result"),
            duration_ms=None,
        )
        if stored and stored.get("id"):
            result["mnemos_mem_id"] = stored["id"]
    except Exception:
        # MNEMOS optional — silent failure, response still goes back to caller.
        pass
    return result


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


async def portfolio_initialize(seed_question: str | None = None) -> dict[str, Any]:
    """One-shot bootstrap: discover portfolio files, refresh all sections,
    optionally fire a seed ask to warm the LLM-narrative cache. After this
    returns success, every subsequent portfolio_ask within the section TTLs
    hits the warm envelope cache and returns in 1-3 seconds.

    Designed for first-run install paths (the agent or container boot sequence
    can call this once) and after manual portfolio file uploads.

    Returns dict with per-stage status, total elapsed time, and the run_ids
    of each underlying call (so the agent can retrieve any of them via
    portfolio_response_get for diagnostics).

    Args:
        seed_question: optional natural-language question to fire after
            refresh. Defaults to a holdings-flavored prompt that exercises
            the narrator path. Pass empty string to skip the seed ask.
    """
    import time

    overall_start = time.monotonic()
    stages: list[dict[str, Any]] = []

    # Stage 1: setup — auto-discover any portfolio files in /data/portfolios.
    t0 = time.monotonic()
    setup_result = await _run_ic_engine(["setup"])
    stages.append({
        "stage": "setup",
        "exit_code": setup_result.get("exit_code", -1),
        "duration_ms": int((time.monotonic() - t0) * 1000),
        "ic_result": (setup_result.get("ic_result") or {}).get("ic_result", {}),
    })

    # Stage 2: refresh — populate envelope.sections.* (holdings, performance,
    # bonds, analyst, news, synthesize, optimize, cashflow, peer). 600s
    # subprocess timeout; large portfolios may take 5-15min cold.
    t0 = time.monotonic()
    refresh_result = await _run_ic_engine(["refresh"], timeout_sec=1800.0)
    stages.append({
        "stage": "refresh",
        "exit_code": refresh_result.get("exit_code", -1),
        "duration_ms": int((time.monotonic() - t0) * 1000),
        "ic_result": (refresh_result.get("ic_result") or {}).get("ic_result", {}),
    })

    # Stage 3: seed ask — warms the narrator path so the very first user
    # ask doesn't hit a cold LLM connection. Skip if caller passed empty
    # string. Default seed exercises holdings + analyst sections, which
    # are the most likely first-question targets.
    if seed_question != "":
        prompt = seed_question or "What is in my portfolio? Top 3 positions."
        t0 = time.monotonic()
        ask_result = await _run_ic_engine(["ask", prompt], timeout_sec=600.0)
        stages.append({
            "stage": "seed_ask",
            "exit_code": ask_result.get("exit_code", -1),
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "ic_result": (ask_result.get("ic_result") or {}).get("ic_result", {}),
            "narrative_chars": len(ask_result.get("narrative") or ""),
        })

    return {
        "initialized": all(s.get("exit_code") == 0 for s in stages),
        "total_duration_ms": int((time.monotonic() - overall_start) * 1000),
        "stages": stages,
        "ready": all(s.get("exit_code") == 0 for s in stages),
        "next_step": (
            "Subsequent portfolio_ask calls will hit the warm envelope cache "
            "(1-3s) until the per-section TTL expires (5-10 min depending "
            "on section)."
        ),
    }


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
            "PRIMARY TOOL — call this for ANY portfolio question. The "
            "container auto-initialized at boot, so the data cache is "
            "already warm: holdings, performance, bonds, analyst ratings, "
            "news (per-symbol + general/forex/crypto/merger categories), "
            "synthesis, optimization, cashflow projections, peer analysis, "
            "and Treasury yield curve are ALL pre-loaded into the envelope. "
            "Just call portfolio_ask with the user's question verbatim — "
            "you do NOT need to call portfolio_setup, portfolio_refresh, or "
            "portfolio_initialize first. The engine routes deterministically "
            "and the narrator returns a verified natural-language answer "
            "with envelope-quoted numbers (no hallucination)."
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
            "ADVANCED — force a fresh data pull. The container already auto-"
            "refreshes stale sections (TTLs 30s-300s) on every portfolio_ask, "
            "so you almost never need this. Call only when the user "
            "explicitly asks for a manual refresh, OR after they upload a "
            "new portfolio file via /api/portfolio/setup."
        ),
        parameters={},
        required=[],
        handler=portfolio_refresh,
    ),
    "portfolio_setup": _tool(
        description=(
            "ADVANCED — auto-discover portfolio files in /data/portfolios/. "
            "Already runs at container boot. Call only after the user uploads "
            "a NEW portfolio file mid-session. For everyday use just call "
            "portfolio_ask."
        ),
        parameters={},
        required=[],
        handler=portfolio_setup,
    ),
    "portfolio_initialize": _tool(
        description=(
            "One-shot bootstrap: setup + refresh + optional seed ask. Returns "
            "after the envelope cache is fully populated, so every subsequent "
            "portfolio_ask hits the warm cache and answers in 1-3 seconds. "
            "Call this once after install, after uploading new portfolio "
            "files, or whenever you want to force a fresh data pull. The "
            "container can also auto-initialize at boot via env flag "
            "IC_INITIALIZE_ON_BOOT=1."
        ),
        parameters={
            "seed_question": {
                "type": "string",
                "description": (
                    "Optional natural-language question to fire after refresh "
                    "to warm the narrator/LLM cache. Defaults to a holdings "
                    "prompt; pass empty string to skip."
                ),
            },
        },
        required=[],
        handler=portfolio_initialize,
    ),
}
