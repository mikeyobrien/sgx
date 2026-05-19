# sgx Skill for Agents

This directory contains the official Claude Code skill for teaching agents how to use the `sgx` CLI effectively.

## Installation (as a local skill)

Copy or symlink this folder into one of these locations:

- Personal: `~/.claude/skills/sgx/`
- Project: `.claude/skills/sgx/` (recommended for agent projects)

Claude Code will automatically discover `SKILL.md`.

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
- Calling `sgx` from an agent

## Versioning

Bump the `version` in `SKILL.md` when making meaningful improvements.