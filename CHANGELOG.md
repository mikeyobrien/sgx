# Changelog

All notable changes to sgx will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-15

### Added
- `sgx mcp` — run sgx as an MCP server (optional `mcp` extra). Stdio for local clients; Streamable HTTP for remote use, with an embedded single-user OAuth authorization server (Dynamic Client Registration + PKCE + rotating refresh tokens, gated by `SGX_MCP_PASSWORD`) so it can be onboarded in Muse as a custom connector. Tools: `x_search`, `research`, `thread_new`/`thread_send`/`thread_list`/`thread_show`, `sgx_status`. See `docs/remote-mcp.md`; `scripts/mcp_smoke.py` verifies a deployment the way Muse connects; `deploy/` has a systemd unit + env template.
- `sgx --version` and shell completion (Typer `--install-completion`).
- `--raw` on `sgx search`/`sgx research` to opt back into duplicative debug payloads (`raw_text`/`raw_response` are now dropped by default, halving agent-facing output).
- `--force` on `sgx thread new` (and `force` arg on MCP `thread_new`) to deliberately recreate a thread.

### Changed
- `sgx doctor --json` now runs the same `/models` connectivity probe as human mode; `ready` reflects the probe and a `connectivity` object is included. MCP `sgx_status` reports `connectivity_checked: false` to make clear it only resolves credentials.
- `search`/`research` JSON successes now include `"status": "success"`, and their runtime/unexpected failures emit the standard JSON error shape under `--json` (exit codes 1/2 preserved) instead of human text.
- The `--prompt` preparser now receives today's date, so relative ranges ("last week") normalize correctly.

### Fixed
- `sgx thread show` / MCP `thread_show` returning empty turns: assistant text is now extracted from the real Responses API `output[]` shape via one shared helper, and turn text (user + Grok) is persisted locally at send time (the retrieve endpoint never returns user input). Legacy turns recover Grok's text; their user side stays blank.
- `sgx thread new` silently wiping an existing thread of the same name; it now errors unless `--force` is given.
- HTML explainers starting/ending with stray markdown code fences.
- `--html`/`--open` silently ignored when combined with `--json` (now a structured error); `thread show --limit 0` returning every turn; stale `sgx/0.1.0` User-Agent, "(once Phase 2 is done)" help text, and an error message advising a nonexistent `XAI_X_SEARCH_MODEL` env var.

## [0.2.0] - 2026-07-17

### Added
- `--html` / `--open` (`-o`) on `search` and `research` — generate a premium, self-contained visual HTML explainer (Nico Preme style) and optionally open it in the browser. **Note:** this makes an additional billed `grok-4.3` LLM call on top of the search/research request.
- `sgx auth device-login` (also `sgx device-login`) — OAuth device authorization flow for remote/headless terminals that copies a verification URL and user code/key, then polls until authorization completes.
- Natural-language search via `-p` / `--prompt` — an LLM preparser turns free-form intent into structured `search` arguments.
- `--full` / `-f` flag on `sgx search` to return complete post text instead of truncated summaries.
- Redacted credential diagnostics in `sgx doctor --json`, including skipped-source reasons and the selected credential source.
- Official Claude Code skill for agents (`skills/sgx-for-agents`).
- GitHub Actions CI workflow, `CONTRIBUTING.md`, and MIT `LICENSE`.

### Changed
- `sgx auth device-login --json` emits newline-delimited JSON: an immediate `authorization_required` record with the safe verification URL/code, followed by terminal success or error.
- Current Hermes runtime OAuth credentials are resolved when usable before falling back to Grok CLI or environment credentials.
- Incomplete or unusable Hermes OAuth credential candidates are skipped instead of blocking fallback to another authenticated source.
- Reduced default `search` truncation so more post text is visible without `--full`.

### Fixed
- OAuth endpoints are pinned to xAI's trusted origin, verification links are restricted to xAI-owned HTTPS hosts, and internal device codes never reach display callbacks.
- Blank search queries are rejected consistently.
- Exit cleanly (exit code 0) after successful `--json` output.
- CI hardening: ruff + mypy now pass for the open-source release.

## [0.1.0] - 2026-05-19

### Added
- `sgx auth login` — native browser OAuth PKCE flow against xAI accounts
- Automatic detection and import prompt for existing official Grok CLI credentials (`~/.grok/auth.json`)
- `sgx auth status` and `sgx auth logout`
- Native credential storage at `~/.sgx/auth.json` with fallback to Hermes and Grok CLI
- Full support for persistent research threads (`sgx thread`)
- Deep multi-agent research via `grok-4.20-multi-agent`
- Privileged server-side `x_search` and hybrid web + X search
- Comprehensive test suite with hermetic credential testing

### Changed
- Decoupled from hard Hermes dependency while preserving seamless fallback

Initial public release.