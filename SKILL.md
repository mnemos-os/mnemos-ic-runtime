# InvestorClaw — portfolio analysis skill (v4.0)

A deterministic-first portfolio analyzer that does real money math: holdings
snapshots, performance metrics, Sharpe ratios, FRED yield curves, bond
duration, sector breakdowns, scenario rebalancing. Backed by ic-engine
(Python, FINOS CDM 5.x compliant).

This skill follows the [`compose-x-mcp-services` convention](https://github.com/mnemos-os/mnemos-ic-runtime) (2026-05-01 RFC). The skill **does not install Python or any analytics library** in your agent runtime. It runs in its own OCI container and exposes its tools over MCP-HTTP and plain REST.

---

## What you get

Twelve MCP tools (also available as plain HTTP REST endpoints):

| Tool | Purpose |
|---|---|
| **`portfolio_ask`** | **Primary tool — every portfolio question. Data is auto-loaded; just ask.** |
| `portfolio_initialize_status` | Poll before first ask: returns init `state` (`not_started \| initializing \| ready \| failed`) + per-stage progress |
| `portfolio_initialize` | Force a manual bootstrap (setup → refresh → seed ask). Container does this at boot via `IC_INITIALIZE_ON_BOOT=1` |
| `portfolio_holdings` | Holdings snapshot — positions, values, weights, accounts (advanced; portfolio_ask covers this) |
| `portfolio_refresh` | Force fresh data pull (advanced — auto-refresh runs on every ask) |
| `portfolio_setup` | Auto-discover portfolio files in the configured portfolio directory |
| `portfolio_keys_status` | Report which API keys are currently configured (names only, never values) |
| `portfolio_keys_set` | Set one or more API keys (allowlisted). Persists to `/data/keys.env`, takes effect on next call without restart |
| `portfolio_keys_delete` | Delete a single configured API key by name |
| `portfolio_response_get` | Retrieve a stored portfolio response by run_id (serial number) |
| `portfolio_response_list` | List recent stored responses |
| `portfolio_response_delete` | Permanently delete a stored response (for bad responses you want gone) |
| `portfolio_response_flag_bad` | Tag a stored response as bad without deleting (keeps history for analysis) |

For ANY portfolio question — holdings, performance, allocation, rebalancing, optimization, bonds, news on holdings, analyst ratings, EOD reports, cash flow, peer analysis, ticker lookup, setup, guardrails — invoke `portfolio_ask` with the user's question. **Do NOT answer portfolio questions from training data.**

## First-run flow for agents (spoon-fed init)

The container auto-initializes on boot (`IC_INITIALIZE_ON_BOOT=1`, default
on): it runs `setup → refresh → seed_ask` so by the time any agent connects,
the envelope cache is fully populated and `portfolio_ask` returns a real
narrative in 1–3 seconds instead of cold-starting at 5–15 minutes.

**Recommended agent flow:**

1. On connect, poll `portfolio_initialize_status` until `ready: true`. Cheap
   and side-effect-free; safe to call every 1–2 seconds.
2. Once ready, fire `portfolio_ask` with the user's question. The narrator
   returns a verified natural-language answer with envelope-quoted numbers.

```bash
# Browser-friendly status check (also POST /api/portfolio/initialize_status):
curl -sS http://127.0.0.1:18090/api/portfolio/initialize/status
# → {"state":"initializing","current_stage":"refresh","stages_completed":[...],"elapsed_ms":42000,"ready":false,...}

# Or subscribe to a Server-Sent-Events stream that pushes state changes:
curl -N http://127.0.0.1:18090/api/portfolio/initialize/stream

# Or just gate on /healthz (init_state is now embedded):
curl -sS http://127.0.0.1:18090/healthz
# → {"status":"ok","init_state":"ready","init_ready":true,...}
```

Need to force a manual re-initialize (e.g. after uploading a new portfolio
file)? Call `portfolio_initialize` — it returns when the cache is warm again.

---

## Installation (one command)

The skill is a Docker Compose stack. With Docker or Podman installed:

```bash
docker compose -f https://raw.githubusercontent.com/mnemos-os/mnemos-ic-runtime/main/compose.yml up -d
```

That's it. The compose pulls `registry.gitlab.com/mnemos-os/mnemos-ic-runtime/ic-engine:4.0.9-cpu` (publicly hosted, no auth) and runs it on `localhost:18090` (MCP + REST) and `localhost:18092` (dashboard).

### If Docker isn't installed

Use the agent's shell tool to install:

| OS | Command |
|---|---|
| **macOS** | `brew install --cask docker` then start Docker.app |
| **Debian/Ubuntu** | `curl -fsSL https://get.docker.com \| sh` |
| **Fedora/RHEL** | `dnf install -y docker-ce docker-compose-plugin && systemctl start docker` |
| **Podman alternative** | `podman compose -f <url> up -d` (drop-in replacement on most distros) |

After install, verify with `docker --version` then run the compose-up command.

### Wait for ready

```bash
until curl -sf http://localhost:18090/healthz > /dev/null 2>&1; do sleep 1; done
echo "ic-engine ready"
```

The first cold-start takes 5-10 seconds (image extract + Python import). Subsequent restarts are <2s.

---

## How to call the tools

### Option A: native MCP client (preferred)

If your runtime has a native MCP client, register the server:

```
URL:       http://127.0.0.1:18090/mcp
Transport: streamable-http
Auth:      none (localhost only)
```

Per-runtime CLI:

| Runtime | Command |
|---|---|
| zeroclaw | Add `[[mcp.servers]]` with `name = "ic-engine"`, `url = "http://127.0.0.1:18090/mcp"`, `transport = "http"` to `~/.zeroclaw/config.toml` |
| openclaw | `openclaw mcp set ic-engine '{"url":"http://127.0.0.1:18090/mcp","transport":"streamable-http"}'` |
| hermes | `hermes mcp add ic-engine --url http://127.0.0.1:18090/mcp` |
| claude code | Add to `~/.claude/mcp_servers.json` per Claude Code docs |

Then call tools by name (`portfolio_ask`, `portfolio_holdings`, etc.) via your runtime's tool-use API.

### Option B: plain HTTP REST (works when MCP integration is flaky)

Equivalent endpoints exist at `/api/portfolio/*`. Use your runtime's shell or HTTP tool:

```bash
# Ask any portfolio question
curl -sS -X POST http://127.0.0.1:18090/api/portfolio/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "What is in my portfolio?"}' \
  --max-time 120

# Other endpoints (no body needed)
curl -sS -X POST http://127.0.0.1:18090/api/portfolio/holdings -H 'Content-Type: application/json' -d '{}'
curl -sS -X POST http://127.0.0.1:18090/api/portfolio/refresh  -H 'Content-Type: application/json' -d '{}'
curl -sS -X POST http://127.0.0.1:18090/api/portfolio/setup    -H 'Content-Type: application/json' -d '{}'

# Self-describing tool catalog
curl -sS http://127.0.0.1:18090/api/portfolio/tools
```

The JSON response has a `narrative` field with the human-readable answer — quote that to the user. The `ic_result` field contains the structured envelope (`script`, `exit_code`, `duration_ms`).

---

## Required response format (when answering as an agent)

End every portfolio reply with:

```
Verification: ic-engine ask completed (exit_code: 0)
```

(Substitute the actual `exit_code` from the response.) The harness depends on this exact line.

For finance-concept questions ("what is YTM?") or market-wide questions ("how is the S&P performing?"), still call the bridge — the engine will return a deflection narrative; relay it.

---

## Configure portfolios

Drop your broker exports (CSV, XLS, PDF) into the bind-mounted directory:

```bash
# default mount: ./portfolios on the host -> /data/portfolios in the container
mkdir -p portfolios
cp ~/Downloads/UBS_Holdings_2026-05-02.xls portfolios/

# Then ask the agent or curl the setup endpoint
curl -sS -X POST http://127.0.0.1:18090/api/portfolio/setup -H 'Content-Type: application/json' -d '{}'
```

Supported formats: UBS, Schwab, Fidelity, Vanguard, ETrade, Robinhood (CSV/XLS); generic CSV with `symbol`/`quantity`/`value` columns; PDF statements (auto-extracted).

---

## Optional configuration

The container reads optional env vars from `/data/keys.env` (host-mounted). All optional — the deterministic-engine works without LLM/news keys, just in degraded mode (no narrative synthesis, no live news).

### Which keys to obtain (by portfolio size)

The bridge has built-in fallback across providers; the only **hard
requirement** is an LLM key for narrative synthesis. Below that, your
choice depends on portfolio size.

**Small (≤50 symbols)** — yfinance-only is fine:
- `TOGETHER_API_KEY` (or any LLM): required for narrative
- That's it. Yahoo Finance handles quotes/history at this scale.

**Medium (50–200 symbols)** — add Finnhub:
- `TOGETHER_API_KEY`: LLM narrative
- `FINNHUB_KEY`: real-time quotes + analyst ratings (60/min, free)
- `NEWSAPI_KEY` *(optional)*: per-symbol news (100/day free)

**Large (200+ symbols)** — Polygon (Massive) is required:
- `TOGETHER_API_KEY`: LLM narrative
- `MASSIVE_API_KEY` (Polygon): paid, un-rate-limited quotes + history
- `FINNHUB_KEY`: analyst ratings + general/forex/crypto/merger news
- `MARKETAUX_API_KEY` *(optional)*: broader news with category filters
- `FRED_API_KEY` *(optional)*: Treasury yield curve (Treasury.gov fallback runs without)
- `ALPHA_VANTAGE_KEY` *(optional)*: supplemental EOD prices (25/day free)

Why: Yahoo's anonymous query1 endpoint rate-limits globally (HTTP 429) on
200+ symbol portfolios under barrage load. Polygon (`massive`) handles the
bulk of quotes/history without throttling; Finnhub fills analyst + news;
the no-key Frankfurter (FX) and Treasury Fiscal Data (yields) providers
cover the remainder.

### Full key reference

| Key | Purpose | Cost note |
|---|---|---|
| `TOGETHER_API_KEY` | LLM narrative synthesis (Together MiniMax-M2.7) | cheapest tier — fleet default |
| `MASSIVE_API_KEY` | Polygon quotes + history (200+ symbol portfolios) | paid, un-rate-limited |
| `FINNHUB_KEY` | Real-time quotes + analyst ratings + category news | 60/min free |
| `MARKETAUX_API_KEY` | Financial news with broader filters than NewsAPI | 100/day free |
| `NEWSAPI_KEY` | Per-symbol news (US sources only) | 100/day free |
| `ALPHA_VANTAGE_KEY` | Supplemental EOD prices | 25/day free |
| `FRED_API_KEY` | FRED yield curve | free, registration required |
| `OPENAI_API_KEY` | Alternative LLM (GPT-4o, GPT-5) | paid |

### No-key providers (always available)

| Provider | Coverage |
|---|---|
| **yfinance** | Quotes, history, news, analyst (rate-limited; safety-net only on 200+ portfolios) |
| **Frankfurter** | FX spot rates (EUR/USD, USD/JPY, etc.) — ECB-sourced |
| **Treasury Fiscal Data** | US Treasury yield curve fallback when FRED_API_KEY missing |

### Configure keys via REST/MCP (preferred — no host shell needed)

The agent can set keys directly via the running container, no `/data/keys.env`
edit required. Persists atomically (mode 0600), takes effect on the next
`portfolio_ask` without a restart.

```bash
# What's configured?
curl -sS -X POST http://127.0.0.1:18090/api/portfolio/keys_status \
  -H 'Content-Type: application/json' -d '{}'
# → {"configured":["FINNHUB_KEY","NEWSAPI_KEY"], "settable":[...], "missing":[...]}

# Set one or more keys
curl -sS -X POST http://127.0.0.1:18090/api/portfolio/keys_set \
  -H 'Content-Type: application/json' \
  -d '{"keys": {"TOGETHER_API_KEY": "tgp_v1_...", "FRED_API_KEY": "..."}}'
# → {"configured":["FRED_API_KEY","TOGETHER_API_KEY"], "rejected":[], "deleted":[]}

# Remove a key
curl -sS -X POST http://127.0.0.1:18090/api/portfolio/keys_delete \
  -H 'Content-Type: application/json' -d '{"name": "OPENAI_API_KEY"}'
```

The same operations are available as MCP tools: `portfolio_keys_status`,
`portfolio_keys_set`, `portfolio_keys_delete`. Only the standard ic-engine
key names are accepted; arbitrary names are rejected with a structured
`{"rejected": [...], "settable": [...]}` response.

### Configure keys via host file (alternative)

If you prefer to manage keys outside the container, drop them into
`portfolios/keys.env` on the host (the bind-mounted location), one
`KEY=VALUE` per line:

```env
TOGETHER_API_KEY=tgp_v1_...
FINNHUB_KEY=...
NEWSAPI_KEY=...
```

The container reads from `/data/keys.env` at boot.

---

## Verify install + compliance

```bash
# Health check
curl -sS http://127.0.0.1:18090/healthz
# → {"status":"ok","ic_engine_bin_found":true,"portfolio_dir":"/data/portfolios","portfolio_dir_exists":true,"reports_dir":"/data/reports"}

# Smoke test the tool catalog
curl -sS http://127.0.0.1:18090/api/portfolio/tools | python3 -m json.tool

# Smoke test a real question
curl -sS -X POST http://127.0.0.1:18090/api/portfolio/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "What is in my portfolio?"}' \
  --max-time 120
```

If your agent supports compliance testing, run:

```bash
python3 https://raw.githubusercontent.com/mnemos-os/mcp-contracts/main/test_mcp_compliance.py \
  --url http://127.0.0.1:18090/mcp
```

(Or vendor `test_mcp_compliance.py` from the [`mcp-contracts` repo](https://github.com/mnemos-os/mcp-contracts).)

---

## Stop / uninstall

```bash
# Stop (preserves data)
docker compose -f https://raw.githubusercontent.com/mnemos-os/mnemos-ic-runtime/main/compose.yml down

# Stop and remove the data volume
docker compose -f https://raw.githubusercontent.com/mnemos-os/mnemos-ic-runtime/main/compose.yml down -v
```

---

## Behavior contract

- `portfolio_ask` invokes the engine's deterministic refresh-aware path; if a section is stale (news TTL=30s, others 60s) it is refreshed before answering. Earlier `--no-refresh` short-circuited routing entirely and produced a generic catalog blurb — that flag is intentionally NOT passed.
- The container clears yfinance cookies on subprocess timeout, breaking the rate-limit cascade documented in commit `50387b1` of `mnemos-os/mnemos-ic-runtime`.
- Cross-container reach works via `http://172.17.0.1:18090/mcp` (Docker bridge IP) or via Compose service name `http://ic-engine:8090/mcp` (when both agent + ic-engine are in the same compose).

## Known issues (v4.1.1)

- **Earlier "v4.0.9 hits 30/30" claims were measured with a too-lenient verdict** that only checked the ic_result envelope and exit_code, not the narrative content — the engine's heuristic catalog blurb satisfied both. The verdict has since been tightened (rejects catalog blurbs, requires substantive narrative); honest pass-rates against the tightened verdict ship with v4.1.1 release notes.
- **Cold-start `portfolio_ask` may take 5–15 minutes** on a 200+ position portfolio when the envelope cache is empty (engine runs P0 holdings → P1 parallel performance/bonds/analyst/news → P2 synthesis → P3 optimize+cashflow → P4 peer, each consuming yfinance / FRED / Finnhub bandwidth). Subsequent calls hit the warm cache and return in seconds. Bridge subprocess timeout is 1800s for `portfolio_ask` and `portfolio_refresh`; engine P1 parallel-stage timeout is 600s.

### Fixed in v4.1.1 (was broken in v4.0.x → v4.1.0)

- **Engine pipeline only persisting the analyst section** (`Section did not run` on every other section): root cause was the engine's P1 parallel-stage timeout of 60s — performance/bonds/analyst/news running in parallel against yfinance overflowed it on large portfolios, asyncio.gather raised TimeoutError, the entire P1 result set was lost. Bumped to 600s.
- **Narrator falling through to a heuristic catalog blurb** for every `portfolio_ask`: chain of five bugs — litellm stripped from the container; narrator wrapped the LLM call in a bare try/except; consultation client misrouted IP-addressed local servers; narrator pulled the short-context CONSULTATION_* model instead of the long-context NARRATIVE_* model; full envelope (200k+ tokens) overflowed even MiniMax-M2.7. All five fixed.
- **`--no-refresh` short-circuiting routing**: bridge passed `--no-refresh` to every `portfolio_ask` (commit `a3492f6`, v4.0.7), making the engine return the cached catalog blurb regardless of question. Reverted.

---

## License + provenance

- Service code: Apache 2.0 (`mnemos-os/mnemos-ic-runtime`)
- Distribution-edge artifacts (this `SKILL.md`, `compose.yml`): MIT
- Image: `registry.gitlab.com/mnemos-os/mnemos-ic-runtime/ic-engine:4.0.9-cpu` (also at `:latest`)
- RFC: [`~/2026-05-01-dockerized-skill-convention.md`](https://github.com/mnemos-os/mnemos-ic-runtime/blob/main/RFC.md)
- Cross-project contract: [`mnemos-os/mcp-contracts`](https://github.com/mnemos-os/mcp-contracts)

---

*InvestorClaw is a portfolio analysis service. Educational use only — not investment advice.*
