# sgx — SuperGrok X CLI

**A CLI for high-quality X search, deep multi-agent research, and persistent research threads — with native login and great ergonomics for both humans and agents.**

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

---

## The Problem

You already have powerful access to Grok via Hermes and your SuperGrok subscription (OAuth). But every time you want to use the *privileged* server-side tools (`x_search`, multi-agent research on `grok-4.20-multi-agent`, etc.) from the terminal or another agent, you either:

- Duplicate OAuth flows
- Fall back to weaker public APIs
- Write one-off scripts that don't integrate with your existing tools and workflows

## The Solution

`sgx` is a tiny, auditable CLI that gives you first-class access to xAI's privileged server-side tools.

You can either:
- Run `sgx auth login` once (browser OAuth against your SuperGrok subscription), or
- Reuse your existing Hermes credentials automatically.

No duplicated OAuth flows. No separate API keys required for the good experience. Just `sgx` in your terminal.

### Why Use sgx?

| Feature                    | sgx                                   | Plain xAI SDK / curl          | Hermes alone                  |
|---------------------------|---------------------------------------|-------------------------------|-------------------------------|
| Native login (`sgx auth login`) | Yes (first-class)                  | No                            | N/A                           |
| Reuses Hermes OAuth       | Yes (automatic fallback)              | No (API key only)             | Yes (inside Hermes)           |
| Privileged `x_search`     | Yes                                   | Limited                       | Yes (inside Hermes)           |
| Multi-agent research      | First-class (`--agents 16`)           | Manual                        | Not exposed                   |
| Persistent threads        | Yes (`sgx thread`)                    | No                            | No                            |
| Agent-first design        | Yes (full `--json`, structured errors, official skill) | Basic | Limited |
| No duplicated auth        | Yes                                   | No                            | N/A                           |

---

## Quick Example

```bash
# Install once
uv tool install git+https://github.com/mikeyobrien/sgx   # or from local clone

# First run (pick one)
sgx auth login                # one-time browser login (recommended for standalone use)
# or just run any command and it will fall back to your existing Hermes credentials automatically

sgx doctor                    # shows exactly which credential source is active

# Fast X search (privileged)
sgx search "grok 4.3 reactions" --count 5

# Deep multi-agent research (4 or 16 agents)
sgx research "Compare approaches to long-running personal AI agents" --agents 4 --web

# Beautiful visual HTML output + auto-open in browser (new!)
sgx research "State of the art techniques for persistent memory in agents" --agents 4 --web --html --open

# Persistent research thread (remembers across days)
sgx thread new research-2026
sgx thread send research-2026 "Start by researching memory architectures..."
sgx thread show research-2026 --limit 8
```

---

## Design Philosophy

- **Native when you want it, seamless reuse when you don't** — `sgx auth login` gives you a first-class credential store. If you already use Hermes, `sgx` automatically falls back to it with zero configuration.
- **Agent-first design** — Built for other agents. Every command has stable `--json` + structured errors. New `--html --open` flag turns research into beautiful visual HTML explainers that can be automatically opened in the browser. Includes an official Claude skill for agents.
- **Tiny and auditable** — TDD-built, minimal dependencies, no magic.
- **Progressive power** — Start with fast `search`, graduate to `research` and persistent `thread` when you need depth.
- **Honest about cost** — Multi-agent research and privileged tools cost real money. `sgx` makes the trade-offs visible.

---

## Installation

**Recommended (uv tool):**

```bash
uv tool install git+https://github.com/mikeyobrien/sgx
```

**From source (development):**

```bash
git clone https://github.com/mikeyobrien/sgx ~/projects/sgx
cd ~/projects/sgx
uv sync
uv run sgx doctor
```

**One-liner (recommended for agents & scripts):**

```bash
uvx --from git+https://github.com/mikeyobrien/sgx sgx --help
```

This runs the latest version without permanent installation. Perfect for agents.

---

## Quick Start

1. Install `sgx` (see Installation above).
2. Authenticate once:
   ```bash
   sgx auth login
   ```
   (It will automatically detect if you already have the official Grok CLI or Hermes credentials.)
3. Verify everything is working:
   ```bash
   sgx doctor
   ```
4. Start using it:
   ```bash
   sgx search "grok 4.3" --count 5
   sgx research "your question here" --agents 4 --web
   ```

**If you're building an agent or using Claude Code:** See the "For Agents & Claude Code" section below to install the official skill that teaches agents the best ways to use `sgx`.

---

## Authentication

`sgx` supports four credential sources, tried in this order:

1. **Native sgx store** (`~/.sgx/auth.json`) — created by `sgx auth login`
2. **Hermes SuperGrok OAuth** (`~/.hermes/auth.json`) — automatic fallback for existing Hermes users
3. **Official Grok CLI** (`~/.grok/auth.json`) — automatic fallback if you use the official `grok` CLI
4. **`XAI_API_KEY`** — from the environment or `~/.hermes/.env`

This means:
- If you run `sgx auth login` once, `sgx` will always prefer those credentials.
- If you never run `sgx auth login`, it will transparently use Hermes (or your API key) exactly like before.

### Auth Commands

| Command              | Description |
|----------------------|-------------|
| `sgx auth login`     | Open your browser and log in with your SuperGrok subscription (PKCE OAuth). If it detects existing credentials from the official Grok CLI (`~/.grok/auth.json`), it will offer to import them instead of forcing a new browser flow. Stores the token in `~/.sgx/auth.json`. Supports `--no-browser`. |
| `sgx auth status`    | Show which source is currently active and a masked view of the token. |
| `sgx auth logout`    | Clear only the native sgx credentials (Hermes and `XAI_API_KEY` are untouched). |

**Example first-time flow (standalone):**

```bash
sgx auth login
sgx auth status          # should now show "xai-oauth" from native store
sgx search "grok 4.3" --count 3
```

---

## Commands

### Core

**`sgx search <query>`**  
Fast privileged X search.

```bash
sgx search "grok 4.3" --count 8 --json
sgx search "topic" --web --json
```

**`sgx research "<question>"`**  
Deep multi-agent research using `grok-4.20-multi-agent`.

```bash
sgx research "your question" --agents 4 --web --json
sgx research "..." --agents 16 --json

# Generate a beautiful standalone visual HTML explainer
sgx research "your question" --agents 4 --web --html

# Generate + automatically open in browser
sgx research "your question" --agents 4 --web --html --open
```

### Threads

- `sgx thread new <name>`
- `sgx thread send <name> "message" [--web]`
- `sgx thread list`
- `sgx thread show <name> [--limit N]`

Example:
```bash
sgx thread new project --json
sgx thread send project "next step..." --json
```

All commands support `--json`.

---

## For Agents & Claude Code

`sgx` was built with agents in mind. It offers:

- Stable `--json` output on every command
- Structured error responses
- `--html --open` to turn research into beautiful, self-contained visual HTML explainers (powered by a Nico Preme-style visual explainer prompt)

### Recommended: Load the official skill

```bash
cp -r skills/sgx-for-agents ~/.claude/skills/sgx
```

This teaches agents the best patterns for using `sgx` (auth, when to use threads vs research, `--html` usage, etc.).

Future one-command distribution:
```bash
npx -y @claude-skills/sgx
```

See `skills/sgx-for-agents/SKILL.md` for the full skill definition.

---

## Architecture

```
Your Terminal / Other Agents
        │
        ▼
   sgx CLI (this repo)
        │
        ├─► ThreadStorage (local ~/.sgx/threads/)
        │
        ▼
   get_xai_auth()  (native ~/.sgx/auth.json → Hermes fallback → XAI_API_KEY)
        │
        ▼
   xAI Responses API (privileged server-side tools)
        │
        ├─► x_search
        ├─► web_search
        ├─► multi-agent research (grok-4.20-multi-agent)
        └─► stateful conversation (previous_response_id)
```

---

## Troubleshooting

**"No xAI credentials found"**
- Run `sgx auth login`, or ensure you have either Hermes SuperGrok OAuth or `XAI_API_KEY` set.

**Multi-agent is expensive / slow**
- Use `--agents 4` (or `--effort low`) for most work. Reserve 16 agents for truly hard questions.

**Thread history feels truncated**
- Use `sgx thread show <name> --limit 20` (default is 10).

---

## Limitations

- Only supports the privileged Responses API surface (not the full public OpenAI-compatible chat completions).
- Multi-agent model (`grok-4.20-multi-agent`) and some advanced tools require appropriate xAI tier.
- Persistent threads are local + server-side (30-day server retention for response objects).
- No image/video/TTS support yet (planned).

---

## FAQ

**Q: Is this just a wrapper around the official xAI SDK?**  
No. It deliberately uses the raw Responses API + the exact server-side tools (`x_search`, multi-agent orchestration) that are not fully exposed in the public SDKs.

**Q: Can other agents call sgx?**  
Yes — this is a primary design goal. All commands support stable `--json` output and structured errors. For best results, load the official **sgx skill** (see the "For Agents & Claude Code" section above). It teaches agents the correct patterns for authentication, search vs research vs threads, and error handling.

**Q: Does it work without Hermes?**  
Yes — just set `XAI_API_KEY`.

---

## License

MIT — see [LICENSE](LICENSE).
