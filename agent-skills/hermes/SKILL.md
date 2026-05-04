---
name: investorclaw
description: Deterministic-first portfolio analyzer for Hermes via MCP-HTTP at localhost:18090. Holdings, performance, Sharpe + Sortino, FRED yields, bond duration, scenario rebalancing.
homepage: https://github.com/argonautsystems/InvestorClaw
user-invocable: true
metadata: {"license":"MIT-0","version":"4.1.18","runtime":"hermes","image":"ghcr.io/perlowja/ic-engine:4.1.18-cpu","mcp-endpoint":"http://localhost:18090/mcp"}
---

<!--
SPDX-License-Identifier: MIT-0
Copyright 2026 InvestorClaw Contributors

This SKILL.md is MIT-0-licensed and tailored for the NousResearch Hermes
Agent (v0.12+). The InvestorClaw service it connects to is Apache 2.0.
See LICENSE-MIT-0 in this directory.
-->

# InvestorClaw — Hermes Skill (v4.0)

> Powered by [InvestorClaw](https://investorclaw.app) (Apache 2.0).
> This skill file is MIT-0-licensed; the underlying service is Apache 2.0.

## TL;DR for hermes operators

InvestorClaw v4.0 turns hermes into a **first-class portfolio-analysis
agent**. The service runs as two local Docker containers and exposes
its capabilities over MCP-HTTP. Hermes 0.12+ registers the MCP servers
as native function-callable tool sources — the LLM sees
`investorclaw.portfolio_ask`, `mnemos.search_memories`, and friends in
the same tool catalog as `browser_*`, `terminal`, and `skill_view`.

**Headline upgrade — HER-1 is gone.** Read the next section if you
remember v2.x.

## What changed since v2.x — HER-1 elimination

If you ran InvestorClaw v2.x against hermes, you hit **HER-1**: the
"skill-as-doc-hint" caveat. In v2.x, InvestorClaw shipped as a hermes
skill bundle injected into the system prompt. The LLM had to use
hermes meta-tools (`skill_view`, `terminal`) to *read* the skill
documentation and then *imitate* the analyst commands by shelling out.
That indirection layer was lossy and slow, and the Linux baseline
empirical reliability landed around **8% (2.3/30)** on the standard
prompt barrage — vs **77%** on zeroclaw, which had real tool
registration.

**v4.0 ends that.** There is no skill bundle to inject. The
deterministic engine runs as a containerized service and publishes its
analytical surface over MCP-HTTP. Hermes 0.12+ registers MCP servers
declaratively in `~/.hermes/config.yaml` and exposes their tools to
the LLM directly — same dispatch path as any other built-in tool.

What this means in practice for hermes users:

- **No more meta-tool indirection.** The LLM calls
  `investorclaw.portfolio_ask` directly, not via `skill_view` →
  `terminal` → fragile shell parsing.
- **Reliability now matches other agent runtimes.** Expect the same
  routing accuracy as zeroclaw / openclaw — roughly an order of
  magnitude better than v2.x on the same prompts.
- **Memory is built in.** The `mnemos.*` tool family gives hermes a
  persistent memory layer it never had before, scoped to InvestorClaw
  observations and user preferences.
- **No skill bundle to keep in sync.** Bumping the service to a newer
  ic-engine version is `docker compose pull && docker compose up -d`.
  The tool catalog hermes sees is whatever the running service
  publishes.

## Architecture (hermes ⇄ InvestorClaw)

```
hermes (host)
  │
  │  config.yaml mcp_servers:
  │     investorclaw → http://127.0.0.1:18090/mcp
  │     mnemos       → http://127.0.0.1:5002/mcp
  ▼
Docker compose (~/.investorclaw/compose.yml)
  ├── mnemos-os/ic-engine:4.1.18-cpu       :8090   portfolio analysis MCP
  └── mnemos-os/mnemos-rs:4.2       :5002   memory + KG MCP
       (dashboard at :8092 for portfolio upload + key config)
```

The user runs `docker compose up -d` to install the service. Hermes
discovers tools at startup by handshaking with each MCP server.

## Tool surface

When InvestorClaw is running and hermes has reloaded its config,
the tool catalog gains:

### Portfolio analysis (`investorclaw.*`)

- `investorclaw.portfolio_ask` — natural-language portfolio question
  routed through the deterministic engine
- `investorclaw.portfolio_holdings` — current snapshot of positions,
  values, weights
- `investorclaw.portfolio_performance` — Sharpe, volatility, top /
  bottom performers, max drawdown
- `investorclaw.portfolio_bonds` — bond analytics (YTM, duration,
  FRED yield curve)
- `investorclaw.portfolio_analyst` — analyst ratings per holding
- `investorclaw.portfolio_news` — news correlation for held positions
- `investorclaw.portfolio_lookup` — ticker / account lookup
- `investorclaw.portfolio_optimize` — Sharpe / min-vol optimization
- `investorclaw.portfolio_rebalance` — current vs target with tax
  impact
- `investorclaw.portfolio_scenario` — what-if scenarios on holdings
- `investorclaw.portfolio_cashflow` — projected cashflow from bonds
- `investorclaw.portfolio_peer` — peer comparison vs benchmark
- `investorclaw.portfolio_setup` — auto-discover portfolio files in
  `/data/portfolios/`
- `investorclaw.portfolio_refresh` — refresh market data without
  re-uploading files
- `investorclaw.portfolio_guardrails` — view / configure
  educational-only guardrails

### Memory (`mnemos.*`)

- `mnemos.search_memories` — full-text + semantic search across
  remembered observations
- `mnemos.create_memory` — record an observation about user
  preferences, prior questions, or current investing context
- `mnemos.list_memories` — browse by category / date

## Usage idioms

Just ask portfolio questions in chat. Hermes' LLM picks the right
MCP tool from the catalog automatically:

```bash
hermes chat -q "What's in my portfolio?" \
  --provider gemini -m gemini-2.5-flash --yolo

hermes chat -q "What changed since last week?" \
  --provider together -m MiniMaxAI/MiniMax-M2.7 --yolo

hermes chat -q "Refresh my market data and show me the worst
                performer." \
  --provider gemini -m gemini-2.5-flash --yolo
```

Recommended LLM behavior (the model already does this on its own,
but worth knowing):

1. **Portfolio questions →** call `investorclaw.portfolio_ask` with
   the user's natural-language question. The deterministic engine
   routes it to the correct analyzer and returns a structured
   `ic_result` envelope plus a narrative body.
2. **Follow-up questions →** call `mnemos.search_memories` first to
   pull relevant prior observations (risk tolerance, prior holdings
   discussions). Then call the appropriate `investorclaw.*` tool with
   that context.
3. **What-changed questions →** combine `mnemos.search_memories` for
   prior portfolio summaries with `investorclaw.portfolio_holdings`
   for the current snapshot; let the LLM diff them.
4. **After delivering an analysis →** call `mnemos.create_memory` to
   record salient observations the user might want to remember
   (e.g., "User flagged BABA as a never-sell sentimental position").
   Don't over-record.

## Important behaviors

- **Deterministic at the data layer.** If a portfolio file format
  isn't recognized, the tool returns a structured error with detected
  columns and supported formats. Surface that to the user — point
  them at the dashboard's column-mapping wizard at
  http://localhost:8092/portfolios/map.
- **Educational only — never investment advice.** All outputs include
  the disclaimer envelope. Echo it when summarizing.
- **MCP servers are local by default** at `http://127.0.0.1:18090/mcp`
  and `http://127.0.0.1:5002/mcp`. Remote deployments (Tailscale,
  cloud) just change the URLs — the tool surface is identical.
- **No portfolio? No problem.** The LLM can talk about generic
  market questions via `investorclaw.portfolio_ask` even before a
  portfolio file is uploaded; it'll guide the user to the dashboard.

## Install pointer

See `INSTALL.md` next to this file for the full hermes 0.12+ install
flow (skill drop, compose up, config.yaml MCP block, restart).
`config-snippet.yaml` has the exact YAML to paste into your
`~/.hermes/config.yaml`.

## What this skill does NOT do

- Does not manage money or execute trades
- Does not give investment advice
- Does not access user accounts or move funds
- Educational outputs only

## Reporting issues

This skill describes the InvestorClaw service from a hermes operator's
perspective. If a tool returns an unexpected result, the issue is in
the upstream service (Apache 2.0,
`mnemos-os/mnemos-ic-runtime` + `perlowja/InvestorClaw`), not in this
SKILL.md. If hermes can't see the tools at all, that's an install
issue — work through `INSTALL.md` and the troubleshooting section
there.
