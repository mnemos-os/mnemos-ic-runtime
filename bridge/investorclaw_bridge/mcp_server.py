# SPDX-License-Identifier: Apache-2.0
"""FastMCP server wrapping ic-engine commands as MCP-HTTP tools.

This is the v4.0 beta-pilot surface — minimum viable set of tools needed
to demonstrate "agent connects + answers portfolio questions" end-to-end.
Additional tools (bonds, news, optimize, peer, etc.) wire in once the
beta is live and we know the contract works.

Design:
  - Each MCP tool is a thin wrapper around an existing ic-engine CLI verb.
  - Tools subprocess into the ic-engine venv and capture structured
    `ic_result` envelopes from stdout, returning them as MCP tool results.
  - No LLM call in the parse/extract path
    (per feedback_v4_0_engine_deterministic_no_llm_in_parse).
  - Tool names use snake_case to satisfy upstream MCP / OpenAI tool-name
    validation (no dots — that's a known issue on some agent runtimes).

For beta pilot: 4 tools — enough to answer "what's in my portfolio?",
"how is it performing?", "refresh the data", and "scan the portfolios
folder for files."
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("investorclaw_bridge.mcp")


# ──────────────────────────────────────────────────────────────────────
# Configuration — env-driven so the container can rewire without rebuild
# ──────────────────────────────────────────────────────────────────────


IC_ENGINE_BIN = os.environ.get(
    "IC_ENGINE_BIN", "/opt/ic-engine/.venv/bin/investorclaw"
)
PORTFOLIO_DIR = Path(os.environ.get("IC_PORTFOLIO_DIR", "/data/portfolios"))
REPORTS_DIR = Path(os.environ.get("IC_REPORTS_DIR", "/data/reports"))
KEYS_FILE = Path(os.environ.get("IC_KEYS_FILE", "/data/keys.env"))

# yfinance writes session cookies + ticker-tz cache to ~/.cache/py-yfinance/
# inside the container (Docker user `ic`, uid 1000). Once Yahoo's anti-bot
# rate-limits a session, the cookie carries the backoff state, and *every*
# subsequent ic-engine subprocess (which re-imports yfinance fresh) inherits
# the lockout — even though `proc.terminate()` killed the orphan. Result is
# a hard cascade: every prompt past the first yfinance-rate-limit hit times
# out at 240s for the rest of the session. Clearing this dir on timeout
# makes the next subprocess look like a brand-new yfinance session.
# Discovered during 2026-05-02 v4 30-prompt barrage on 4.0.2-cpu (10/10
# cascade hit on p21-p30 after p21-deflect-market triggered the rate limit).
YF_CACHE_DIR = Path(os.environ.get("YF_CACHE_DIR", "/home/ic/.cache/py-yfinance"))


# Provider key map — translates per-provider key env vars (set by the
# operator in /data/keys.env) into the ic-engine narrative env contract.
# Default endpoint family is set in the Dockerfile (Together AI); the
# operator only needs to set ONE of these in keys.env to enable narratives.
#
# Cost policy: Together MiniMax is the fleet default (cheap, Anthropic-free).
# If Google is configured, the operator should pin a Flash model — Pro
# blew $70/night in the 2026-04-30 incident.
_PROVIDER_KEY_FALLBACKS = (
    "INVESTORCLAW_NARRATIVE_API_KEY",  # explicit override wins
    "TOGETHER_API_KEY",                # fleet default
    "GOOGLE_API_KEY",                  # only if INVESTORCLAW_NARRATIVE_MODEL is a -flash variant
    "GEMINI_API_KEY",                  # alias used by some keys.env files
    "OPENAI_API_KEY",                  # last resort
)


def _resolve_narrative_api_key() -> str | None:
    """Return the first non-empty narrative API key from the fallback chain.

    Reads from process env (which has been pre-loaded from /data/keys.env
    by the bridge entrypoint via key_resolver). Returns None if no key is
    configured — narrative synthesis is degraded but the deterministic
    engine still runs.
    """
    for var in _PROVIDER_KEY_FALLBACKS:
        v = os.environ.get(var, "").strip()
        if v:
            return v
    return None


# ──────────────────────────────────────────────────────────────────────
# ic-engine subprocess invocation
# ──────────────────────────────────────────────────────────────────────


class IcEngineError(Exception):
    """Raised when the ic-engine subprocess exits non-zero."""


def _clear_yfinance_cache() -> None:
    """Delete the yfinance per-user cache directory, if it exists.

    Called from the subprocess-reap path on TimeoutError to break the
    rate-limit-cookie cascade (see YF_CACHE_DIR comment above). Safe to
    call when the dir does not exist or is partially written — `rmtree`
    with ignore_errors=True swallows ENOENT and EACCES.
    """
    try:
        shutil.rmtree(YF_CACHE_DIR, ignore_errors=True)
        logger.info("mcp.yf_cache.cleared", path=str(YF_CACHE_DIR))
    except Exception as exc:
        # Should never fire (ignore_errors=True), but log if it does.
        logger.warning("mcp.yf_cache.clear_failed", path=str(YF_CACHE_DIR), error=str(exc))


async def _run_ic_engine(
    args: list[str],
    *,
    timeout_sec: float = 240.0,
) -> dict[str, Any]:
    """Run `investorclaw <args>` in a subprocess; return parsed result.

    Resolves ic-engine binary path via $IC_ENGINE_BIN, falling back to
    PATH lookup if the explicit path doesn't exist (useful for local
    dev outside the container).

    Returns a dict with keys:
        stdout: raw stdout (str)
        stderr: raw stderr (str)
        exit_code: int
        ic_result: parsed `{"ic_result": {...}}` JSON envelope if present, else None
        narrative: the human-readable text body before the envelope (str)

    Raises IcEngineError on non-zero exit unless the result includes
    a structured `ic_result` envelope (in which case the caller decides
    how to handle the error semantically).
    """
    bin_path = IC_ENGINE_BIN if Path(IC_ENGINE_BIN).exists() else "investorclaw"
    if not shutil.which(bin_path) and not Path(bin_path).exists():
        raise IcEngineError(
            f"ic-engine binary not found at {IC_ENGINE_BIN!r} or on PATH. "
            f"Container build must install it at /opt/ic-engine/.venv/bin/investorclaw."
        )

    full_cmd = [bin_path, *args]
    logger.info("mcp.ic_engine.invoke", cmd=full_cmd)

    # Subprocess env: inherit parent + inject the narrative API key so
    # ic_engine.rendering.stonkmode can call Together (or whichever
    # endpoint INVESTORCLAW_NARRATIVE_ENDPOINT points to). Without this,
    # narrative synthesis silently degrades to stub text.
    sub_env = dict(os.environ)
    api_key = _resolve_narrative_api_key()
    if api_key:
        sub_env.setdefault("INVESTORCLAW_NARRATIVE_API_KEY", api_key)
        sub_env.setdefault("INVESTORCLAW_STONKMODE_API_KEY", api_key)
        sub_env.setdefault("INVESTORCLAW_CONSULTATION_API_KEY", api_key)

    proc = await asyncio.create_subprocess_exec(
        *full_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(PORTFOLIO_DIR.parent),
        env=sub_env,
    )
    # Bug fix (2026-05-01): if the FastMCP request handler is cancelled mid-call
    # (e.g., the agent's HTTP client times out and aborts), `proc.communicate()`
    # raises CancelledError and the subprocess is orphaned, holding yfinance/FRED
    # connections + file handles in /data/reports/. Subsequent calls then queue
    # behind the orphans and time out themselves, cascading. Wrap in try/finally
    # that always reaps the subprocess on any exit path. Discovered during
    # 2026-05-01 v4.0 cobol barrage: 3 passes / 43 trials before this fix.
    timed_out = False
    try:
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except asyncio.TimeoutError:
            timed_out = True
            raise IcEngineError(
                f"ic-engine command timed out after {timeout_sec}s: {full_cmd}"
            )
    except (asyncio.CancelledError, asyncio.TimeoutError, IcEngineError):
        # On timeout, on outer-task cancellation, or on any error: reap.
        # SIGTERM first (gives engine a chance to clean up), then SIGKILL.
        if proc.returncode is None:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except ProcessLookupError:
                pass
        if timed_out:
            # Yahoo Finance rate-limit cookies survive subprocess reap.
            # Clear them so the next ic-engine subprocess starts fresh and
            # doesn't immediately hit the same backoff window.
            _clear_yfinance_cache()
        raise
    finally:
        # Belt-and-suspenders: catch the case where we returned successfully
        # but the subprocess somehow didn't reap (shouldn't happen post-communicate
        # but harmless if it does).
        if proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    exit_code = proc.returncode or 0

    # Parse the structured `{"ic_result": {...}}` envelope if present.
    # ic-engine emits it on the LAST line of stdout for tools the harness can verify.
    ic_result = None
    narrative = stdout
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith('{"ic_result"'):
            try:
                ic_result = json.loads(line)
                # Strip the envelope from the narrative
                narrative = stdout.rsplit(line, 1)[0].rstrip()
                break
            except json.JSONDecodeError:
                pass

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "ic_result": ic_result,
        "narrative": narrative,
    }


# ──────────────────────────────────────────────────────────────────────
# MCP tool registry — beta MVP set
# ──────────────────────────────────────────────────────────────────────


# We use FastMCP's decorator API. Imports inside register_tools so the
# module is importable for tests without the mcp package installed.


def register_tools(app) -> None:
    """Register all v4.0 MCP tools on a FastMCP application.

    `app` is an mcp.server.fastmcp.FastMCP instance. The caller wires
    transport (HTTP at :8090) + auth.
    """

    @app.tool()
    async def portfolio_ask(question: str) -> dict[str, Any]:
        """Ask a natural-language portfolio question.

        Routes through ic-engine's deterministic pipeline. Returns the
        structured `ic_result` envelope plus the narrative text body.
        Use this for any portfolio question the user asks in plain
        English — InvestorClaw will figure out which analyzer to run.

        Args:
            question: The user's portfolio question, e.g.
                "What is in my portfolio?", "How is performance?",
                "What are my biggest tech holdings?"

        Returns:
            dict with `ic_result` (envelope), `narrative` (text body),
            `exit_code`. ic_result may be None if the engine returned
            a deflection.
        """
        result = await _run_ic_engine(["ask", question])
        return result

    @app.tool()
    async def portfolio_holdings() -> dict[str, Any]:
        """Get current portfolio holdings snapshot.

        Returns positions, values, weights, and account hierarchy as
        structured data. Equivalent to `investorclaw ask "what is in
        my portfolio?"` but with a more focused contract.

        Returns:
            dict with structured holdings data + narrative body.
        """
        result = await _run_ic_engine(
            ["ask", "What is in my portfolio? Show me holdings, values, and weights."]
        )
        return result

    @app.tool()
    async def portfolio_refresh() -> dict[str, Any]:
        """Refresh market data without re-uploading portfolio files.

        Re-runs the ic-engine refresh pipeline against current portfolio
        files in /data/portfolios/. Pulls fresh prices via yfinance / FRED
        / Finnhub (depending on which keys are configured in /data/keys.env).

        Returns:
            dict with refresh status + counts.
        """
        result = await _run_ic_engine(["refresh"], timeout_sec=180.0)
        return result

    @app.tool()
    async def portfolio_setup() -> dict[str, Any]:
        """Auto-discover portfolio files in /data/portfolios/.

        Use this on first run, or after the user uploads a new portfolio
        file. Returns a summary of detected files (pdfs / xls / csv) and
        whether the engine is ready for analysis.

        Returns:
            dict with detected file counts + ready_for_analysis bool.
        """
        result = await _run_ic_engine(["setup"])
        return result

    logger.info(
        "mcp.tools.registered",
        tools=["portfolio_ask", "portfolio_holdings", "portfolio_refresh", "portfolio_setup"],
    )


# ──────────────────────────────────────────────────────────────────────
# Health endpoints (lifted into the FastAPI app by serve.py)
# ──────────────────────────────────────────────────────────────────────


def health_check() -> dict[str, Any]:
    """Basic health check — returns liveness + ic-engine binary presence.

    Exposed at /healthz on both :8090 (MCP) and :8092 (dashboard).
    """
    bin_present = Path(IC_ENGINE_BIN).exists() or bool(shutil.which("investorclaw"))
    return {
        "status": "ok" if bin_present else "degraded",
        "ic_engine_bin_found": bin_present,
        "portfolio_dir": str(PORTFOLIO_DIR),
        "portfolio_dir_exists": PORTFOLIO_DIR.exists(),
        "reports_dir": str(REPORTS_DIR),
    }
