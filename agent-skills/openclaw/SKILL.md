<!--
SPDX-License-Identifier: MIT
Copyright 2026 InvestorClaw Contributors

This SKILL.md is MIT-licensed. The InvestorClaw service it connects to
is Apache 2.0. See the InvestorClaw repository for that license.
-->

# InvestorClaw — Skill (openclaw runtime)

> Powered by [InvestorClaw](https://investorclaw.app) (Apache 2.0).
> This skill file is MIT-licensed; the underlying service is Apache 2.0.

## What this is

InvestorClaw is a containerized portfolio-analysis service exposed to
openclaw as **two MCP-HTTP servers**:

- `investorclaw` — portfolio analysis tools at `http://127.0.0.1:8090/mcp`
- `mnemos` — memory + knowledge graph at `http://127.0.0.1:5002/mcp`

Both run inside a Docker compose stack on the user's machine
(`docker compose up -d` is the entire service install). openclaw connects
to them as native MCP servers via its `mcp.servers` config block —
no plugin manifest, no `dist/index.js`, no npm install, no skill
bootstrap files.

If openclaw runs in a container itself, the two MCP URLs reach the host's
loopback through the compose bridge network or `host.docker.internal`,
depending on how the openclaw container is launched. See `INSTALL.md`.

## Tool surface

When the service is running, openclaw's tool catalog gains:

### Portfolio analysis (`investorclaw.*`)

- `investorclaw.portfolio_ask` — natural-language portfolio question
  routed through the deterministic engine
- `investorclaw.portfolio_holdings` — current snapshot of positions,
  values, weights
- `investorclaw.portfolio_performance` — Sharpe, volatility, top/bottom
  performers, max drawdown
- `investorclaw.portfolio_bonds` — bond analytics (YTM, duration, FRED
  yield curve)
- `investorclaw.portfolio_analyst` — analyst ratings per holding
- `investorclaw.portfolio_news` — news correlation for held positions
- `investorclaw.portfolio_lookup` — ticker / account lookup
- `investorclaw.portfolio_optimize` — Sharpe / min-vol optimization
- `investorclaw.portfolio_rebalance` — current vs target with tax impact
- `investorclaw.portfolio_scenario` — what-if scenarios on holdings
- `investorclaw.portfolio_cashflow` — projected cashflow from bonds
- `investorclaw.portfolio_peer` — peer comparison vs benchmark
- `investorclaw.portfolio_setup` — auto-discover portfolio files in
  `/data/portfolios/`
- `investorclaw.portfolio_refresh` — refresh market data without
  re-uploading files
- `investorclaw.portfolio_guardrails` — view educational-only guardrails

### Memory (`mnemos.*`)

- `mnemos.search_memories` — full-text + semantic search across
  remembered observations
- `mnemos.create_memory` — record an observation about the user's
  preferences, prior questions, or current investing context
- `mnemos.list_memories` — browse by category / date

## How users interact with it

Users ask portfolio questions in openclaw chat. The LLM sees the MCP
tools in its function-calling schema and routes the question to the
right tool automatically. Examples:

- "What's in my portfolio?" → `investorclaw.portfolio_holdings`
- "How am I doing this year?" → `investorclaw.portfolio_performance`
- "What did I tell you about BABA last month?" → `mnemos.search_memories`

A typical flow:

1. User asks: *"What changed since last review?"*
2. openclaw's LLM calls `mnemos.search_memories` for prior portfolio
   context.
3. LLM calls `investorclaw.portfolio_holdings` for the current snapshot.
4. LLM compares the two and synthesizes a narrative.
5. LLM calls `mnemos.create_memory` to record salient observations from
   the review.

After delivering analysis, the LLM should record only non-obvious
observations the user might want next time — not every detail, just the
ones that would be hard to recover from re-reading the data.

## Important behaviors

- **The investorclaw tools are deterministic at the data layer.** Each
  response includes a structured `ic_result` envelope plus a narrative
  text body. Trust the structured envelope — it is the source of truth.
  The narrative is decoration. If a portfolio file format isn't
  recognized, the tool returns a structured error with detected columns;
  surface that error and direct the user to the dashboard's column-mapping
  wizard at `http://localhost:8092/portfolios/map`.

- **Educational only — never investment advice.** All outputs include
  the disclaimer envelope. Echo it when summarizing for the user. Do not
  recommend buying, selling, or holding specific securities.

- **The MCP servers run on loopback by default.** `127.0.0.1:8090` and
  `127.0.0.1:5002`. If the user deploys remotely (Tailscale VM, cloud
  host), the URLs change but the tool surface is identical.

- **openclaw's own LLM provider config is separate.** openclaw routes
  *its* chat completions through `models.providers.<name>` in
  `~/.openclaw/openclaw.json` (Together, OpenAI, Ollama, etc.). That is
  unrelated to InvestorClaw's optional narrative tier, which is configured
  inside the InvestorClaw dashboard at `http://localhost:8092/`.

## v4.0 vs v2.x — what's different on openclaw

v4.0 eliminates the v2.x openclaw install friction:

- **No** `openclaw.plugin.json` manifest (there is no plugin)
- **No** `dist/index.js` (there is no plugin shim to compile)
- **No** `npm install` / `uv sync` step inside an openclaw container
- **No** workspace bootstrap files (`BOOTSTRAP.md` / `IDENTITY.md` /
  `USER.md`) to seed
- **No** schema-validation daemon to fight when writing provider config
  for the plugin

The integration is just two MCP server URLs. openclaw's existing native
MCP support handles the rest.

## What this skill does NOT do

- Does not manage money or execute trades
- Does not give investment advice
- Does not access user accounts or move funds
- Educational outputs only

## Reporting issues

This SKILL.md describes how openclaw connects to the InvestorClaw
service. If a tool returns an unexpected result, the issue is in the
service (Apache 2.0 — `mnemos-os/mnemos-ic-runtime` and
`perlowja/InvestorClaw`), not in this file. If openclaw fails to register
the MCP servers, see `INSTALL.md` in this directory — in particular the
note about always using the validated `openclaw mcp set` /
`openclaw config patch` CLI rather than editing `openclaw.json` by hand.
