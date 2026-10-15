---
name: sgx
description: This skill must be used when the task requires privileged X search, deep multi-agent research with grok-4.20-multi-agent, persistent stateful conversations/threads, or calling external xAI server-side tools via the sgx CLI or its MCP server. Activate for any request involving real-time information from X, long-running research, or maintaining conversation state across sessions.
version: 0.3.1
---

# sgx — Agent Usage Guide

**sgx** gives agents privileged access to xAI capabilities (real X search, multi-agent
research, persistent threads) without managing raw API keys or OAuth flows. It is usable
two ways: as a **CLI** (shell + `--json`) and as an **MCP server** (stdio or HTTP).
Prefer MCP when your host supports it — structured tools beat shell parsing.

All behavior below was verified live against sgx 0.2.0 with real credentials.

## Core Principles for Agents

1. **Always use `--json`** on every CLI command. Human output is for people.
2. **Branch on the exit code first**, then parse JSON (see Error Handling — the JSON
   error shape is *not* universal).
3. **Preflight before spending quota**: run `sgx doctor --json` and require
   `"ready": true` (MCP equivalent: `sgx_status`).
4. **Use generous timeouts**: search/thread calls can take ~60s; `research` routinely
   takes minutes. Run research in the background; never fire research calls in parallel.

## Installation (Agent-Friendly)

```bash
# Recommended — include the [mcp] extra so `sgx mcp` works too
uv tool install 'sgx[mcp] @ git+https://github.com/mikeyobrien/sgx'

# From a local clone (development; source edits take effect immediately)
uv tool install --editable '.[mcp]'

# One-shot execution without installing
uvx --from git+https://github.com/mikeyobrien/sgx sgx --help
```

After installation, `sgx` is on PATH (uv puts it in `~/.local/bin`).

## Authentication (Zero Friction)

sgx resolves credentials in this priority order:

1. Native `~/.sgx/auth.json` (created by `sgx auth login`; auto-refreshes when expired)
2. Hermes `~/.hermes/auth.json`
3. Official Grok CLI `~/.grok/auth.json`
4. `XAI_API_KEY` from the environment or `~/.hermes/.env` (honors `XAI_BASE_URL`)

**Best practice for agents** — check first, log in only if needed:

```bash
sgx doctor --json        # probes the API: {"ready": true, "connectivity": {"ok": true, ...}, "credentials": {...}}
sgx auth status --json   # lighter-weight: which source is active (no connectivity probe)
```

Note the asymmetry: `doctor --json` makes a real (free) `GET /models` probe and its
`ready` reflects whether the token is actually accepted. MCP `sgx_status` only
*resolves* credentials (`connectivity_checked: false`) — trust `doctor` for preflight.

If not ready:
- `sgx auth login --json` — detects existing Grok CLI credentials and offers to import
  them, or falls back to the browser (PKCE) flow.
- On remote/headless machines, `sgx auth device-login --json` (also `sgx device-login`):
  read the first newline-delimited JSON record (`status: authorization_required`), show
  its `verification_uri` and `user_code` to the human, then keep the process attached
  for the terminal success/error record. Raw token and device-code secrets are never emitted.

Do **not** run `sgx auth logout` unless the human asks — it destroys working credentials.

## Primary CLI Commands

### 1. Fast Search (`sgx search`)

Quick, targeted retrieval from X (and optionally the web). Default model `grok-4.3`.

```bash
sgx search "query here" --count 10 --json          # --count/-c is 1..20, default 5
sgx search "query" --web --json                    # hybrid X + web
sgx search "query" --full --json                   # full post text (default truncates)
sgx search "query" --after 2026-10-01 --before 2026-10-15 --json
sgx search --prompt "recent posts about X from the last day" --json
```

- `--full`: use it whenever you will quote or closely analyze posts; default output
  truncates long post text.
- `--prompt`: natural-language description; a preparser LLM converts it to structured
  search arguments. Use it when you can't express the query as keywords. No positional
  query is needed with `--prompt`.
- **Output shape**:
  ```json
  {"results": [{"url": "https://x.com/...", "text": "...", "author": "@handle",
                "created_at": "2026-10-15T15:39:20Z"}],
   "citations": [], "model": "grok-4.3", "query": "...", "web": false, "status": "success"}
  ```
  In pure-X mode the duplicative `raw_text` (a copy of the results JSON) is dropped by
  default — pass `--raw` if you need it for debugging. With `--web`, the answer may be
  a synthesized response in `raw_text` rather than structured `results` — handle both
  (in that mode `raw_text` is always kept).

**When to use:** current events, specific posts, sentiment, recent discussions.

### 2. Deep Research (`sgx research`)

Multi-agent research on `grok-4.20-multi-agent` with server-side tools.

```bash
sgx research "complex question requiring synthesis" --agents 4 --json
sgx research "..." --agents 16 --json                       # maximum depth, high cost
sgx research "..." --agents 4 --tools web_search --json     # restrict tool set
sgx research "..." --no-tools --json                        # pure multi-agent reasoning
```

- `--agents` accepts only **4** or **16** and overrides `--effort` (4→low, 16→high).
- **Cost reality check** (measured): even a trivial question at `--agents 4` consumed
  ~110–150k input tokens and 12–14 server-side tool calls over ~30–60s. Real questions
  cost more. Never use `research` for anything `search` can answer; start at 4 agents
  and escalate to 16 only for genuinely hard questions.
- **Timeout:** allow 300–900s and run it as a background task where possible.
- **Output shape**: the answer is `output_text` (markdown, with inline citations).
  Also present: `model`, `effort`, `tools_used`, `usage` (incl. `cost_in_usd_ticks`),
  `query`, `status`. The huge `raw_response` is dropped by default (it duplicates
  `output_text`); pass `--raw` only for debugging.

### 3. Persistent Threads (`sgx thread`)

Stateful conversations backed by a server-side `previous_response_id` chain — the model
genuinely remembers earlier turns (verified live). Thread metadata lives in
`~/.sgx/threads/<name>.json`.

```bash
sgx thread list --json                       # check first — see warning below
sgx thread new research-project --json       # create ONLY if absent
sgx thread send research-project "next step..." --json
sgx thread send research-project "..." --web --json   # augment one turn with web search
```

- `thread send --json` returns
  `{"status": "success", "thread": ..., "response_id": ..., "output_text": ..., "model": ...}`.
- `thread new` on an existing name **errors** (it will not wipe history); pass
  `--force` only if you deliberately want to discard and recreate. Still, `thread list
  --json` first and reuse existing threads where possible.
- `thread show [--limit N] --json` returns real turns: `{response_id, user, grok}`.
  Turn text is persisted locally at send time (the xAI retrieve endpoint never returns
  user input), so history is reliable for turns sent with current sgx. For legacy
  turns sent before this was stored, Grok's text is recovered from the API but `user`
  is `""` — that's expected, not an error.
- There is no delete command; removing `~/.sgx/threads/<name>.json` deletes a thread.

**When to use:** multi-step investigations, durable agent memory, work spanning
sessions/days.

### 4. HTML Explainers (`--html` / `--open`)

`search` and `research` accept `--html [--html-dir DIR]` to generate a standalone visual
HTML explainer (one extra `grok-4.3` call) and `--open` to launch it in a browser.
This is a human-facing feature — agents should skip it unless the human asks for a
visual artifact. `--html`/`--open` cannot be combined with `--json` (structured error).

## MCP Server (`sgx mcp`)

The same capabilities as an MCP server — prefer this in MCP-capable hosts.

```bash
sgx mcp                            # stdio (default), for local clients
sgx mcp --transport http           # Streamable HTTP, OAuth-protected (remote use)
```

Requires the `[mcp]` extra (see Installation). Tools exposed:

| Tool | Arguments | Notes |
|---|---|---|
| `sgx_status` | — | Preflight: `{ready, provider, source, base_url, api_key_masked, diagnostics}` |
| `x_search` | `query`, `count?`, `web?`, `full?`, `after?`, `before?` | Same output as `sgx search --json` |
| `research` | `question`, `agents?`, `web?`, `x?` | Expensive + slow; same cost rules as CLI |
| `thread_new` | `name`, `model?`, `force?` | Errors if the thread exists; `force=true` discards + recreates |
| `thread_send` | `name`, `message`, `web?` | Returns `output_text`, `response_id`, `usage` |
| `thread_list` | — | `{threads: [...]}` |
| `thread_show` | `name`, `limit?` | Turns stored locally at send time; legacy turns may have `user: ""` |

Check `isError` on every tool result.

**Remote/HTTP mode** (e.g. adding sgx to Muse as a custom connector):
- Requires `SGX_MCP_PASSWORD` (OAuth authorization server with DCR + PKCE);
  `SGX_MCP_TOKEN` is a static bearer alternative for scripted clients.
  `--issuer-url https://your-host` sets the public base; the endpoint is `<issuer>/mcp`.
  Full guide: `docs/remote-mcp.md` in the repo.
- **Never** pass `--no-auth` on a reachable interface — anyone with the URL can spend
  the owner's xAI quota. It exists for localhost testing only.
- Verify a deployment end-to-end (401 discovery → OAuth → tool call) with the repo's
  `scripts/mcp_smoke.py`.

## Error Handling (Machine Readable)

Exit codes: `0` success · `1` handled error · `2` unexpected error / usage error.

Under `--json`, **all** commands (including `search`/`research` runtime failures) emit
this shape on error, on stdout:

```json
{"status": "error", "error": "Human readable message", "type": "ExceptionClassName"}
```

The reliable agent protocol:

1. Check the exit code. Non-zero ⇒ failure; parse the JSON error if present, else
   treat combined output as the error message (defensive fallback for ancient builds).
2. On exit 0, parse stdout as JSON. Successes include `"status": "success"`
   (exception: `thread list --json` returns a bare array via CLI, `{threads: [...]}`
   via MCP).
3. Warnings (e.g. preparser fallback) go to stderr, so `--json` stdout stays parseable.

Over MCP, check `isError` on every tool result instead.

## Recommended Patterns for Agents

**Preflight (once per session)**
```bash
sgx doctor --json   # require "ready": true before any paid call
```

**Quick fact-finding**
```bash
sgx search "<very specific query>" --count 5 --json
```

**Deep one-shot research** (background it; allow ≥300s)
```bash
sgx research "<well-scoped question>" --agents 4 --json   # answer = .output_text
```

**Long-running investigation**
1. `thread list --json`; create the thread only if absent (creating a duplicate errors).
2. Send every step to the thread; review history anytime with `thread show --json`.
3. Reuse the same thread across sessions by name.

**Hybrid**
Use `search` for raw material, then feed the interesting results into `research` or a
thread turn for synthesis.

## Capabilities Discovery

```bash
sgx --help
sgx <subcommand> --help    # search | research | thread | auth | mcp | doctor
```

Over MCP, use `tools/list` — the tool schemas are self-describing.

## Summary — Agent Cheat Sheet

- Install once: `uv tool install 'sgx[mcp] @ git+https://github.com/mikeyobrien/sgx'`
- Preflight: `sgx doctor --json` → `"ready": true` (auth is usually automatic)
- **Always** `--json`; branch on exit code before parsing
- `search` for speed, `research` for depth (expensive — background it, 4 agents first),
  `thread` for memory, **MCP tools when available**
- `thread show` gives history back (locally stored turns); don't `--force` a thread
  you still need, never `--no-auth` an MCP server in public, never `auth logout` unprompted

This skill exists so agents stop guessing how to use privileged xAI tools and instead
call `sgx` correctly every time.
