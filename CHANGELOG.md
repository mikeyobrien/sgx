# Changelog

All notable changes to sgx will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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