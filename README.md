# mnemos-ic-runtime

Container bridge for **InvestorClaw 4.1.22** — the InvestorClaw application
service that pairs `ic-engine` (Python, FINOS CDM 5.x portfolio analysis)
with `mnemos-rs` (Rust, sqlite-backed memory).

This repo owns:
- The `ic-engine:4.1.22-cpu` container Dockerfile (Python 3.12 + uv +
  `argonautsystems/ic-engine` ic-engine pinned by SHA + bridge code)
- The `docker-compose.yml` wiring `mnemos-rs` + `ic-engine` together with
  `x-mcp-services` extension keys describing the MCP server manifest
- The `MnemosClient` bridge code (Python: HTTP client to mnemos-rs; matches
  the Rust trait in `mnemos-os/mnemos-rs`)
- The dashboard web UI static files served at `:8092` by the ic-engine
  container
- The bundle.json schema + import/export logic
- The MIT-0-licensed distribution-edge artifacts: `compose.yml`, `install.yaml`,
  `SKILL.md` (published to ClawHub at https://clawhub.ai and served raw via
  https://raw.githubusercontent.com/mnemos-os/mnemos-ic-runtime/main/compose.yml)

## What this is NOT

- Not the ic-engine source code (that's `argonautsystems/ic-engine`, hand-authored
  by the maintainer)
- Not the mnemos-rs source code (that's `mnemos-os/mnemos-rs`, in flight)
- Not the mnemos Python server (that's `mnemos-os/mnemos`)

## Quick start

```bash
# One-shot install (after Docker / Podman is available):
docker compose -f https://raw.githubusercontent.com/mnemos-os/mnemos-ic-runtime/main/compose.yml up -d

# Open the dashboard:
open http://127.0.0.1:8092/

# Connect your agent: see SKILL.md for per-agent config blocks
```

For zeroclaw on master:

```bash
zeroclaw services install https://raw.githubusercontent.com/mnemos-os/mnemos-ic-runtime/main/compose.yml
```

## Architecture

- **[docs/INSTALL_MODELS.md](docs/INSTALL_MODELS.md)** — *why* the v4.x
  architecture splits along two install models (dockerized-skill for the
  claw runtimes today; native-workspace SKILL.md for Claude Code once the
  marketplace path is live) and what the contract between them is.
- **[docs/COBOL_TESTING.md](docs/COBOL_TESTING.md)** — *why* we built the
  Agentic COBOL 250-prompt regression suite, what the strict verdict
  catches that no other test layer can, and how it's the v4.x ship gate.
  Long-form rationale published at
  [techbroiler.net/all-our-tests-passed-the-agent-was-still-broken](https://techbroiler.net/all-our-tests-passed-the-agent-was-still-broken/).
- **[RFC-v0.1.md](RFC-v0.1.md)** — full v4.x architecture specification.

```
HOST
┌──────────────────────────────────────────────┐
│  docker compose:                             │
│  ┌──────────┐         ┌──────────────────┐  │
│  │ mnemos   │ ◀───────│   ic-engine       │  │
│  │ (rust)   │  HTTP   │  (python 3.12)   │  │
│  │  :5002   │         │   :8090 (MCP)    │  │
│  └──────────┘         │   :8092 (dash)   │  │
│                        └──────────────────┘  │
│                              ▲                │
└──────────────────────────────┼────────────────┘
                               │ MCP-HTTP
                  ┌────────────┴────────────────┐
                  │  Claude / zeroclaw / openclaw│
                  │  / hermes — any MCP client   │
                  └──────────────────────────────┘
```

## Repo layout

```
mnemos-ic-runtime/
├── RFC-v0.1.md           # v4.0 architecture document
├── compose.yml            # MIT-0 — canonical docker-compose with x-mcp-services
├── install.yaml           # MIT-0 — derived view for shell-tool agents
├── SKILL.md               # MIT-0 — agent-readable installer instructions
├── Dockerfile             # Apache 2.0 — ic-engine:4.1.22-cpu image build
├── bridge/                # Apache 2.0 — Python bridge code (MnemosClient,
│                          # MCP server wrappers around ic-engine commands,
│                          # bundle import/export)
├── dashboard/             # Apache 2.0 — single-page dashboard (vanilla JS
│                          # or preact, served by bridge at :8092)
├── tests/                 # Apache 2.0 — bridge tests + compat-test harness
├── LICENSE                # Apache 2.0 (full text)
├── LICENSE-MIT-0          # MIT-0 (for distribution-edge files)
└── README.md              # this file
```

## Licensing

| Artifact | License |
|---|---|
| Container code, bridge, dashboard, tests | Apache 2.0 |
| `SKILL.md`, `install.yaml`, `compose.yml`, `agent-skills/**` (distribution edge) | MIT-0 |

Per-file `SPDX-License-Identifier` headers indicate the applicable
license. The `compose.yml` + `install.yaml` + `SKILL.md` are intentionally
MIT-0 (MIT No Attribution) — required by ClawHub schema and a deliberate
choice to permit frictionless redistribution through agent skill marketplaces
(ClawHub, Claude Code marketplace) without an Apache NOTICE-file overhead.

The substantive code (bridge, dashboard, ic-engine source it pulls in) is
Apache 2.0. The patent grant and NOTICE requirements apply there as
normal.

## Contributing

- Bug fixes / CI / docs: PRs welcome
- Architectural changes: file an RFC under `docs/RFC-v<n>.md` first; use
  the v0.1 RFC as a template
- ic-engine source changes: contribute upstream at
  `argonautsystems/ic-engine`; this repo just packages it
- mnemos-rs changes: contribute upstream at `mnemos-os/mnemos-rs`

## Related repos

| Repo | Owner | Scope |
|---|---|---|
| `argonautsystems/ic-engine` | upstream maintainer | ic-engine analytical Python source |
| `mnemos-os/mnemos` | mnemos-claude | Python server (qdrant, federation, NATS, GRAEAE) |
| `mnemos-os/mnemos-rs` | this Claude session | Rust desktop port + sqlite substrate |
| `mnemos-os/mnemosctl` | this Claude session | CLI tool |
| **`mnemos-os/mnemos-ic-runtime`** (this repo) | this Claude session | bridge + Dockerfile + compose + dashboard |
