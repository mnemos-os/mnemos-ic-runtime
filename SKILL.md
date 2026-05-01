<!--
SPDX-License-Identifier: MIT
Copyright 2026 InvestorClaw Contributors

This SKILL.md is MIT-licensed. The InvestorClaw service it connects to is
Apache 2.0. See LICENSE-MIT in this directory.
-->

# InvestorClaw — Skill

> Powered by [InvestorClaw](https://investorclaw.app) (Apache 2.0).
> This skill file is MIT-licensed; the underlying service is Apache 2.0.

## What this is

InvestorClaw is a containerized portfolio analysis service that exposes
its analytical capabilities via two MCP-HTTP servers:

- `investorclaw` (port 8090) — portfolio analysis tools (holdings,
  performance, bonds, news, optimization, etc.)
- `mnemos` (port 5002) — memory + knowledge graph (remember user
  preferences, prior observations, conversation context)

The user is the orchestrator. The service is the substrate. Your agent
runtime is the interface. **Your job: connect to the MCP servers, call
the tools, interpret the structured results.**

## Tool surface (after install)

When InvestorClaw is running, your tool catalog gains:

### Portfolio analysis (`investorclaw.*`)

- `investorclaw.portfolio_ask` — natural-language portfolio question
  routed through the deterministic engine
- `investorclaw.portfolio_holdings` — current snapshot of positions /
  values / weights
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
- `investorclaw.portfolio_guardrails` — view/configure educational-only
  guardrails

### Memory (`mnemos.*`)

- `mnemos.search_memories` — full-text + semantic search across
  remembered observations
- `mnemos.create_memory` — record an observation about the user's
  preferences, prior questions, or current investing context
- `mnemos.list_memories` — browse by category / date

## How to use it

1. **For portfolio questions:** call `investorclaw.portfolio_ask` with the
   user's natural-language question. The deterministic engine routes it
   to the right analyzer and returns a structured `ic_result` envelope
   plus a narrative text body. **Trust the structured output** — it's
   deterministic. **Decorate the narrative** if the user wants more
   context.

2. **For follow-up questions:** call `mnemos.search_memories` first to
   pull relevant prior observations (e.g., user's risk tolerance, prior
   discussions about specific holdings). Then call the appropriate
   `investorclaw.*` tool with that context in mind.

3. **For "what changed" questions:** call `mnemos.search_memories` for
   prior portfolio summaries; the LLM can compare against the current
   `investorclaw.portfolio_holdings` output.

4. **After delivering an analysis:** call `mnemos.create_memory` to
   record any salient observations the user might want to remember
   (e.g., "User flagged BABA as a never-sell sentimental position
   during the 2026-04-30 review"). Don't over-record — only what's
   non-obvious from re-reading the data.

## Important behaviors

- **The investorclaw tools are deterministic at the data layer.** If a
  format isn't recognized, you'll get a structured error with detected
  columns and supported formats. Don't ask the LLM to disambiguate —
  surface the error to the user and direct them to the dashboard's
  column-mapping wizard at http://localhost:8092/portfolios/map.

- **Educational only — never investment advice.** All outputs include
  the disclaimer envelope. Echo it when summarizing for the user.

- **The MCP server runs locally by default.** It's at
  http://127.0.0.1:8090/mcp and http://127.0.0.1:5002/mcp. If the user
  deploys remotely (Tailscale VM, cloud), the URLs change but the tool
  surface is identical.

## How to install (if not yet running)

If `investorclaw.*` tools aren't responding, the service isn't running.
You can install it:

1. Verify Docker is available: `docker --version` (or Podman)
2. Stage the compose file:
   ```
   mkdir -p ~/.investorclaw
   curl -sSL https://get.investorclaw.app/v4.0/compose.yml > ~/.investorclaw/compose.yml
   ```
3. Start the service:
   ```
   cd ~/.investorclaw && docker compose up -d
   ```
4. Wait for health: poll `http://127.0.0.1:8090/healthz` until 200.
5. Add the MCP servers to your config (instructions vary per agent
   runtime; see `install.yaml` for ordered steps per agent).
6. Reload config / restart your agent so it picks up the new MCP
   servers.
7. Open the dashboard at http://127.0.0.1:8092/ to upload the user's
   portfolio file.

For zeroclaw on master: a single command does all of the above:

```
zeroclaw services install https://get.investorclaw.app/v4.0/compose.yml
```

(zeroclaw `services` subcommand is in upstream PR — check master
availability before assuming it's there.)

## Connection settings

Your MCP server config should have:

```json
{
  "mcpServers": {
    "investorclaw": {
      "transport": "http",
      "url": "http://127.0.0.1:8090/mcp"
    },
    "mnemos": {
      "transport": "http",
      "url": "http://127.0.0.1:5002/mcp"
    }
  }
}
```

(zeroclaw uses TOML; openclaw uses `mcp.servers` block; hermes uses YAML
`mcp_servers:`. The dashboard's "Connect an agent" wizard generates the
right format per detected agent.)

## What this skill does NOT do

- Does not manage money or execute trades
- Does not give investment advice
- Does not access user accounts or move funds
- Educational outputs only

## Reporting issues

This skill describes the InvestorClaw service. If a tool returns an
unexpected result, the issue is in the service (Apache 2.0,
`perlowja/InvestorClaw` + `mnemos-os/mnemos-ic-runtime`), not in this
SKILL.md.
