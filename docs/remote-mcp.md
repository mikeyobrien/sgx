# sgx as a remote MCP server (Claude custom connector)

`sgx mcp` runs sgx as a [Model Context Protocol](https://modelcontextprotocol.io)
server so Muse (claude.ai, Claude Desktop, mobile, Claude Code) can call sgx
tools directly — no terminal in the loop.

| MCP tool | CLI equivalent | Cost |
|---|---|---|
| `x_search` | `sgx search` | 1 xAI call |
| `research` | `sgx research --agents 4\|16` | expensive multi-agent call |
| `thread_new` / `thread_send` / `thread_list` / `thread_show` | `sgx thread …` | send = 1 call; others free |
| `sgx_status` | `sgx doctor` (credential part) | free |

> **Quota warning:** every tool runs against *your* xAI credentials on the
> server host. A remote MCP endpoint is a public URL that spends real money,
> so HTTP mode refuses to start without authentication (see below).

## How Muse connects (what the server implements)

Claude custom connectors require a **publicly reachable HTTPS** Streamable
HTTP MCP server. For auth, Claude supports OAuth 2.0 with **Dynamic Client
Registration (RFC 7591)** + PKCE S256 out of the box, so sgx embeds a small
single-user authorization server (`src/sgx/mcp_auth.py`):

1. Unauthenticated MCP requests get `401` + `WWW-Authenticate: Bearer
   resource_metadata="…/.well-known/oauth-protected-resource/mcp"` (RFC 9728).
2. Muse dynamically registers itself as a public OAuth client.
3. The operator approves the connection by entering **`SGX_MCP_PASSWORD`**
   on sgx's consent page (which shows the client name and redirect host,
   e.g. `claude.ai`).
4. sgx issues a 1-hour access token + 90-day **rotating** refresh token.
   Registered clients and (hashed) tokens persist in `~/.sgx/mcp_oauth.json`
   (mode 0600), so restarts don't force re-onboarding.
5. Muse's fixed callback `https://claude.ai/api/mcp/auth_callback` works
   via DCR; Claude Code's loopback callback works the same way.

Anyone can *register* a client, but no token is ever issued without the
password. Failed logins are throttled (10 per 10 min per source). A static
bearer (`SGX_MCP_TOKEN`) is also accepted for scripted clients — Muse
connectors don't use it.

Muse limits (hosted surfaces): **240 s per tool call**, ~150k chars per
result. `research` with `agents=16` can exceed 240 s; prefer `agents=4`.

## Running it

### Local (stdio, for Claude Code / desktop configs on the same machine)

```bash
uvx --from 'git+https://github.com/mikeyobrien/sgx[mcp]' sgx mcp
# or from a clone:
uv run --extra mcp sgx mcp
```

### Remote (HTTP, for Muse custom connectors)

Prerequisites on the server host:

1. **xAI credentials** (the tools are useless without them):
   ```bash
   sgx auth device-login    # or sgx auth login / XAI_API_KEY
   sgx doctor               # must say a provider is active
   ```
2. **A password and a public HTTPS URL** that reaches the server.

```bash
export SGX_MCP_PASSWORD="$(openssl rand -base64 18)"
export SGX_MCP_ISSUER_URL="https://your-public-host.example.net"   # no path
uv run --extra mcp sgx mcp --transport http --port 8321
```

The MCP endpoint is `$SGX_MCP_ISSUER_URL/mcp`. The `resource` in the OAuth
metadata is derived from the issuer URL, so it must exactly match the URL
users paste into Muse — set it once and don't put a redirecting host in
front (Claude drops `Authorization` on cross-host redirects).

Configuration (env, all optional except the password in HTTP mode):

| Env var | Meaning |
|---|---|
| `SGX_MCP_PASSWORD` | OAuth consent password (required for HTTP) |
| `SGX_MCP_TOKEN` | Static bearer for scripted clients |
| `SGX_MCP_ISSUER_URL` | Public base URL (default `http://<host>:<port>`) |
| `SGX_MCP_HOST` / `SGX_MCP_PORT` | Bind address (default `127.0.0.1:8321`) |
| `SGX_MCP_STORE` | OAuth store path (default `~/.sgx/mcp_oauth.json`) |

`--no-auth` exists for trusted-network experiments; it makes the endpoint
open to anyone with the URL. Don't use it on a public URL.

### Public HTTPS via Tailscale Funnel (the northpi deployment)

Tailscale Funnel gives a stable public `*.ts.net` HTTPS URL with no domain
or open ports. One-time setup:

```bash
sudo tailscale set --operator=$USER
tailscale funnel --bg --https=443 http://127.0.0.1:8321
# -> https://<machine>.<tailnet>.ts.net  (MCP URL: .../mcp)
```

Run the server as a user service (linger must be on so it survives logout):

```bash
mkdir -p ~/.sgx ~/.config/systemd/user
cp deploy/mcp.env.example ~/.sgx/mcp.env   # edit secrets + issuer URL
chmod 600 ~/.sgx/mcp.env
cp deploy/sgx-mcp.service ~/.config/systemd/user/
systemctl --user enable --now sgx-mcp.service
loginctl enable-linger $USER
```

(Cloudflare Tunnel or a reverse proxy with a real domain works identically —
just point it at `127.0.0.1:8321` and set `SGX_MCP_ISSUER_URL` accordingly.)

### Verify a deployment

`scripts/mcp_smoke.py` replays exactly what Muse does — 401 discovery,
metadata, DCR, PKCE authorize, password login, token exchange + refresh
rotation, then `tools/list` and an `sgx_status` call:

```bash
SGX_MCP_PASSWORD=... uv run --extra mcp python scripts/mcp_smoke.py https://your-host/mcp
SMOKE_SEARCH=1 ...   # additionally run one live x_search (spends quota)
```

## Onboarding in Muse

1. In Muse: **Customize → Connectors → + → Add custom connector**
   (Team/Enterprise Owners: Organization settings → Connectors → Add →
   Custom → Web).
2. Name: `sgx` · Remote MCP server URL: `https://your-host/mcp`
   Leave the advanced OAuth client ID/secret fields **empty** — Muse
   registers itself via DCR.
3. Click **Add**, then **Connect**. Your browser opens the sgx consent page:
   check it says the redirect goes to `claude.ai`, enter `SGX_MCP_PASSWORD`,
   and approve.
4. In a chat, enable the sgx connector and ask e.g. *"Use sgx_status, then
   search X for reactions to the latest Grok release."*

If the connection later shows as expired, just reconnect — refresh tokens
rotate for 90 days; after that (or after deleting `~/.sgx/mcp_oauth.json`)
the password step repeats.

## Troubleshooting

- **"Couldn't reach the MCP server"** — Muse connects from Anthropic's
  cloud, not your device. The URL must work from the public internet:
  `curl -sI https://your-host/mcp` should return `401` (not a redirect,
  not a timeout). Run the smoke script.
- **"Authorization with the MCP server failed"** — usually the issuer URL:
  it must exactly match the URL entered in Muse (scheme, host, `/mcp`),
  with no cross-host redirect in between.
- **Tools say credentials are missing** — run `sgx doctor` on the server
  host; the MCP service reads the same credential stores as the CLI.
  Restart the service after `sgx auth device-login` isn't required (creds
  are resolved per call), but `XAI_API_KEY` changes in `mcp.env` are.
- **Logs**: `journalctl --user -u sgx-mcp.service -f`.
- **Revoke everything**: stop the service, delete `~/.sgx/mcp_oauth.json`,
  change `SGX_MCP_PASSWORD`, start the service. All clients must reconnect.
