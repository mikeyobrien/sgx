# Changelog

All notable changes to sgx will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-07-01

### Added
- `--html` / `--open` (`-o`) on `search` and `research` — generate a premium, self-contained visual HTML explainer (Nico Preme style) and optionally open it in the browser. **Note:** this makes an additional billed `grok-4.3` LLM call on top of the search/research request.
- `sgx auth device-login` (also `sgx device-login`) — OAuth device authorization flow for remote/headless terminals that copies a verification URL and user code/key, then polls until authorization completes.
- Natural-language search via `-p` / `--prompt` — an LLM preparser turns free-form intent into structured `search` arguments.
- `--full` / `-f` flag on `sgx search` to return complete post text instead of truncated summaries.
- Official Claude Code skill for agents (`skills/sgx-for-agents`).
- GitHub Actions CI workflow, `CONTRIBUTING.md`, and MIT `LICENSE`.

### Changed
- `sgx auth device-login --json` emits newline-delimited JSON: an immediate `authorization_required` record with the safe verification URL/code, followed by terminal success or error.
- Reduced default `search` truncation so more post text is visible without `--full`.

### Fixed
- OAuth endpoints are pinned to xAI's trusted origin, verification links are restricted to xAI-owned HTTPS hosts, and internal device codes never reach display callbacks.
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