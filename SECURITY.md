# Security Policy

## Reporting a Vulnerability

Please report suspected vulnerabilities privately by email to
&lt;jperlow@gmail.com&gt;. Do not open a public issue for security-sensitive
reports.

Include:

- The affected version of `mnemos-os/mnemos-ic-runtime` and the
  `ghcr.io/argonautsystems/ic-engine:*` image tag in use.
- A concise description of the issue.
- Reproduction steps or proof-of-concept where possible.
- Logs, configuration, or `docker inspect` output needed to validate
  the issue.

We will acknowledge reports as quickly as practical, investigate in
good faith, and coordinate disclosure timing with the reporter when a
fix or mitigation is available.

## Scope

In scope:

- The `mnemos-ic-runtime` Docker image (bridge code, dashboard,
  Dockerfile)
- The `ghcr.io/argonautsystems/ic-engine:*` image
- The bundled `compose.yml`, `install.yaml`, and `SKILL.md`
- Per-runtime install paths under `agent-skills/**`

Out of scope (report to upstream maintainers):

- The `argonautsystems/ic-engine` Python source — file at
  https://github.com/argonautsystems/ic-engine/issues
- Third-party providers the engine talks to (Together AI, Finnhub,
  Polygon, etc.) — see their respective security pages

## Coordinated disclosure

For high-severity issues we will:

1. Acknowledge within 5 business days.
2. Confirm reproduction within 10 business days where possible.
3. Coordinate a fix timeline with the reporter.
4. Credit the reporter in `CHANGELOG.md` (with their permission) when
   the fix ships.

## Known security considerations (by design)

These are documented design choices, not vulnerabilities, but worth
flagging for your threat model:

- **MCP server is unauthenticated on `127.0.0.1:18090`.** The security
  model is localhost binding. If you expose the port to a network,
  put it behind your own auth layer (Tailscale, nginx + mTLS, etc.).
- **`portfolio_keys_set` accepts API keys over the unauthenticated
  loopback endpoint.** Same threat model — keys persist to
  `/data/keys.env` (mode 0600) inside the named volume.
- **Auto-init runs at container boot** (`IC_INITIALIZE_ON_BOOT=1`).
  The container performs `setup → refresh → seed_ask` against any
  configured providers automatically. Disable by setting
  `IC_INITIALIZE_ON_BOOT=0` if you want manual control.
- **Container runs as `uid=1000(ic)`**, not root. Bind-mount targets
  must be writable by uid 1000 on the host.
