# sgx Skill for Agents

This directory contains the official skill for teaching agents how to use the `sgx` CLI
and MCP server effectively. It follows the Agent Skills format (`SKILL.md` +
frontmatter), so it works across Claude Code, Pi, and other compatible hosts.

## Installation (as a local skill)

Copy or symlink this folder (as `sgx`, matching the skill `name`) into one of:

- Claude Code personal: `~/.claude/skills/sgx/`
- Claude Code project: `.claude/skills/sgx/`
- Pi / Agent Skills personal: `~/.agents/skills/sgx/`
- Pi / Agent Skills project: `.agents/skills/sgx/`

This repo already symlinks `.agents/skills/sgx` → `skills/sgx-for-agents`, so Pi and
other Agent Skills hosts discover it automatically when working in the sgx repo.
(If you edit the skill mid-session in Pi, run `/reload`; force-load with `/skill:sgx`.)

## Vending / Distribution

This skill is designed to be published as an npm package so agents can install it with:

```bash
npx -y @claude-skills/sgx
```

(or a future `claude-skill` installer)

When packaged, the contents of this directory (especially `SKILL.md`) become the distributable unit.

## Structure

- `SKILL.md` — The actual skill definition (frontmatter + guidance)
- `README.md` — This file (how to install and distribute)

## Triggering

The skill activates automatically when a task involves:
- Privileged X search
- Multi-agent research
- Persistent threads / state across sessions
- Calling `sgx` from an agent, via CLI or the `sgx mcp` server

## Versioning

Bump the `version` in `SKILL.md` when making meaningful improvements.