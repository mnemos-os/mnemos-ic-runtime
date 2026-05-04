---
name: investorclaw
description: Deterministic-first portfolio analyzer for Claude Code via MCP-HTTP at localhost:18090. Holdings, performance, Sharpe + Sortino, FRED yields, bond duration, scenario rebalancing.
homepage: https://github.com/argonautsystems/InvestorClaw
user-invocable: true
metadata: {"license":"MIT-0","version":"4.1.20","runtime":"claude-code","image":"ghcr.io/argonautsystems/ic-engine:4.1.20-cpu","mcp-endpoint":"http://localhost:18090/mcp"}
---

<!--
SPDX-License-Identifier: MIT-0
Copyright 2026 InvestorClaw Contributors

This SKILL.md is MIT-0-licensed. It describes the Claude Code marketplace
plugin that connects to InvestorClaw v4.0 (Apache 2.0). The plugin
ships only this file, INSTALL.md, and a manifest — no Python, no bundle.
-->

# InvestorClaw — Claude Code Plugin (v4.0)

> Powered by [InvestorClaw](https://investorclaw.app) (Apache 2.0).
> This plugin is MIT-0-licensed.

## What this is

InvestorClaw is a containerized portfolio analysis service. The user
runs it locally as two Docker containers via `docker compose up -d`;
this Claude Code plugin is the thin client that registers the service's
MCP servers with your agent and exposes two convenience slash commands.

The plugin is a manifest + this SKILL.md + a one-time config write
that points Claude Code at two HTTP MCP endpoints on localhost. All
analysis happens inside the user's Docker containers — the agent
never installs or imports anything.

This is the **v4.0 thin-client variant**. A separate listing
("InvestorClaude", v2.6.x) carries the older skill-bundle install path
for users who prefer running the engine in-process.

## Slash commands the plugin exposes

The plugin registers two slash commands so portfolio questions don't
depend on the LLM spontaneously deciding to call MCP tools:

- **`/ask <question>`** — routes to the `investorclaw.portfolio_ask`
  tool. Pass the user's natural-language portfolio question (e.g.,
  `/ask what are my top 5 dividend payers?`). The deterministic engine
  picks the right analyzer and returns a structured `ic_result` plus
  narrative text.

- **`/refresh`** — routes to `investorclaw.portfolio_refresh`. Pulls
  fresh market data without re-uploading portfolio files. Use this when
  the user has been chatting for a while and quotes may be stale.

These commands are deterministic entry points: the user types the slash
command, Claude Code dispatches it to the named MCP tool, the engine
returns structured output. **No LLM routing decision is involved at the
slash-command boundary.** This is by design — slash commands are
load-bearing for reliability.

## MCP tools available (after install)

When InvestorClaw is running and the plugin is loaded, your tool
catalog gains:

### Portfolio analysis (`investorclaw.*`)

- `investorclaw.portfolio_ask` — natural-language question router (also
  invoked by `/ask`)
- `investorclaw.portfolio_refresh` — market-data refresh (also invoked
  by `/refresh`)
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
  `/data/portfolios/` inside the container
- `investorclaw.portfolio_guardrails` — view/configure educational-only
  guardrails

### Memory (`mnemos.*`)

- `mnemos.search_memories` — full-text + semantic search across
  remembered observations
- `mnemos.create_memory` — record an observation about the user's
  preferences, prior questions, or current investing context
- `mnemos.list_memories` — browse by category / date

## How to use it

1. **For portfolio questions:** prefer `/ask "<question>"`. It's
   deterministic and surfaces the structured `ic_result` envelope
   directly. Decorate the narrative if the user wants more context, but
   trust the engine's numbers — they are computed in code, not inferred.

2. **For follow-up questions:** call `mnemos.search_memories` first to
   pull relevant prior observations (e.g., user's risk tolerance, prior
   discussions about specific holdings). Then call the appropriate
   `investorclaw.*` tool with that context in mind.

3. **For "what changed" questions:** call `mnemos.search_memories` for
   prior portfolio summaries; compare against the current
   `investorclaw.portfolio_holdings` output.

4. **After delivering an analysis:** call `mnemos.create_memory` to
   record any salient observations the user might want to remember
   (e.g., "User flagged BABA as a never-sell sentimental position
   during the 2026-04-30 review"). Don't over-record — only record what
   wouldn't be obvious from re-reading the data later.

5. **When the user uploads a portfolio file:** direct them to the
   dashboard at http://localhost:8092 (the file goes into the container
   directly — Claude Code does not need to handle the upload).

## Important behaviors

- **Deterministic at the data layer.** The investorclaw tools compute
  numbers in code. If a portfolio format isn't recognized, you'll get a
  structured error with detected columns and supported formats. Don't
  ask the LLM to disambiguate — surface the error and direct the user
  to the dashboard's column-mapping wizard at
  http://localhost:8092/portfolios/map.

- **Educational only — never investment advice.** All outputs include
  a disclaimer envelope. Echo it when summarizing for the user. Do not
  recommend specific buys or sells.

- **No money movement, no trades.** This plugin cannot execute trades,
  move money, place orders, or access brokerage accounts. If the user
  asks for any of those, decline and direct them to a licensed advisor.

- **Local by default.** MCP endpoints are on `127.0.0.1` (8090 +
  5002). If the user has deployed the service to a remote host
  (Tailscale VM, cloud), the URLs change but the tool surface is
  identical — the user edits the manifest's MCP server URLs.

## When the plugin can't reach the service

If `investorclaw.*` calls fail with connection errors, the user's
Docker containers aren't running. Tell the user:

1. Open a terminal and run `docker compose ps` in `~/.investorclaw/`
2. If containers aren't listed: `cd ~/.investorclaw && docker compose up -d`
3. If Docker itself isn't installed: see INSTALL.md for the prereq link
4. If the MCP servers still don't respond after `docker compose up -d`:
   wait ~10 seconds for health checks, then retry

See INSTALL.md for the full bring-up sequence.

## What this plugin does NOT do

- Does not install or execute any code on the agent side — it's a
  manifest, this SKILL.md, and an MCP-server config write
- Does not download or execute portfolio data on the agent side
- Does not manage credentials (the engine reads broker CSVs the user
  drops in the dashboard)
- Does not execute trades or move money
- Does not give investment advice

## License + attribution

- This plugin (manifest, SKILL.md, INSTALL.md) is **MIT-0-licensed**.
- The InvestorClaw service it connects to is **Apache 2.0**, hosted at
  `github.com/perlowja/InvestorClaw` and the runtime container at
  `mnemos-os/ic-engine`.
- The MNEMOS memory service is **Apache 2.0**, hosted at
  `mnemos-os/mnemos-rs`.

> Powered by [InvestorClaw](https://investorclaw.app) (Apache 2.0).
> This plugin is MIT-0-licensed.
