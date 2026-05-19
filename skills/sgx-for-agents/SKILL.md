---
name: sgx
description: This skill must be used when the task requires privileged X search, deep multi-agent research with grok-4.20-multi-agent, persistent stateful conversations/threads, or calling external xAI server-side tools via CLI. Activate for any request involving real-time information from X, long-running research, or maintaining conversation state across sessions.
version: 0.1.0
---

# sgx — Agent Usage Guide

**sgx** is the recommended CLI for agents that need high-quality, privileged access to xAI capabilities without managing raw API keys or OAuth flows themselves.

## Core Principle for Agents

**Always use `--json`** on every command. Human output is for people. JSON output is for you.

```bash
sgx <command> ... --json
```

This guarantees stable, parseable output and structured errors.

## Installation (Agent-Friendly)

```bash
# Recommended for agents
uv tool install git+https://github.com/mikeyobrien/sgx

# Or one-shot execution
uvx --from git+https://github.com/mikeyobrien/sgx sgx --help
```

After installation, `sgx` is available on PATH for the agent.

## Authentication (Zero Friction)

sgx automatically discovers credentials in this priority:

1. Native `~/.sgx/auth.json` (created by `sgx auth login`)
2. Official Grok CLI (`~/.grok/auth.json`)
3. Hermes (`~/.hermes/auth.json`)
4. `XAI_API_KEY` environment variable

**Best practice for agents:**
- If the user has the official Grok CLI or Hermes, `sgx` will often just work.
- When needed, call:
  ```bash
  sgx auth login --json
  ```
  The tool will detect existing Grok CLI credentials and offer to import them (or fall back to browser flow).

Check status anytime with:
```bash
sgx auth status --json
```

## Primary Commands for Agents

### 1. Fast Search (`sgx search`)

Use for quick, targeted retrieval from X (and optionally the web).

```bash
sgx search "query here" --count 10 --json
sgx search "query" --web --json          # hybrid X + web results
```

**When to use:** Current events, specific posts, sentiment, recent discussions.

### 2. Deep Research (`sgx research`)

Use the powerful multi-agent research model.

```bash
sgx research "complex question requiring synthesis" --agents 4 --web --json
sgx research "..." --agents 16 --json     # maximum depth (higher cost)
```

**When to use:** Questions that benefit from multiple specialized agents, tool use, and synthesized answers.

**Recommendation:** Start with `--agents 4`. Escalate to 16 only when the question is genuinely hard.

### 3. Persistent Threads (`sgx thread`)

Use for stateful, long-running work that must remember context across many turns or sessions.

```bash
# Create once
sgx thread new research-project --json

# Continue the conversation (maintains previous_response_id chain)
sgx thread send research-project "next step..." --json
sgx thread send research-project "..." --web --json

# Inspect history
sgx thread show research-project --limit 20 --json
sgx thread list --json
```

**When to use:**
- Multi-step investigations
- Agents that need durable memory
- Projects that span hours or days

Threads are the closest thing to giving an agent persistent, high-quality memory backed by real xAI responses.

## Error Handling (Machine Readable)

When `--json` is active, all errors return this shape:

```json
{
  "status": "error",
  "error": "Human readable message",
  "type": "ExceptionClassName"
}
```

Always check `"status"` before trusting the payload.

## Recommended Patterns for Agents

**Pattern: Quick fact-finding**
```bash
sgx search "<very specific query>" --count 5 --json
```

**Pattern: Deep one-shot research**
```bash
sgx research "<well-scoped question>" --agents 4 --web --json
```

**Pattern: Long-running investigation**
1. Create a dedicated thread once.
2. Send all subsequent work to that thread.
3. Periodically call `thread show --json` to review history.

**Pattern: Hybrid**
Use `search` for raw data, feed interesting results into a `research` call or a thread for synthesis.

## Capabilities Discovery

Agents can introspect available functionality with:

```bash
sgx --help
sgx <subcommand> --help
```

For structured discovery of commands, parse the Typer help or maintain a small static map of the public interface (search, research, thread*, auth*).

## Summary — Agent Cheat Sheet

- Install once with `uv tool install`
- Authenticate with `sgx auth login --json` (usually automatic)
- **Always** pass `--json`
- Use `search` for speed, `research` for depth, `thread` for memory
- Errors are structured under `--json`
- Threads give you real persistent state without you managing `previous_response_id`

This skill exists so agents stop guessing how to use privileged xAI tools and instead call `sgx` correctly every time.