# ABOUTME: Remote MCP server exposing sgx tools (X search, research, threads) over stdio or Streamable HTTP
# ABOUTME: HTTP mode serves OAuth (DCR+PKCE) so it can be onboarded as a Claude custom connector

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import AnyHttpUrl

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .auth import resolve_credentials
from .client import create_response, multi_agent_research, slim_search_result, x_search
from .mcp_auth import SCOPE, SgxOAuthProvider
from .threads import ThreadStorage, thread_turns

SERVER_NAME = "sgx"
INSTRUCTIONS = (
    "sgx gives you xAI's privileged server-side tools via the operator's own "
    "SuperGrok/xAI credentials. Use x_search for fast searches of X (Twitter) posts "
    "(and optionally the web). Use research for deep multi-agent investigations — it "
    "is slow and spends real xAI quota, so reserve it for questions that need "
    "synthesis across many sources; prefer agents=4. Threads are persistent "
    "conversations stored on the server. Start with sgx_status if a tool reports "
    "missing credentials."
)

_DEFAULT_PORT = 8321
_thread_lock = threading.Lock()


@dataclass
class McpConfig:
    transport: str = "stdio"  # stdio | http (streamable-http)
    host: str = "127.0.0.1"
    port: int = _DEFAULT_PORT
    issuer_url: Optional[str] = None  # public base URL, e.g. https://host.ts.net
    password: Optional[str] = None
    static_token: Optional[str] = None
    no_auth: bool = False
    store_path: Optional[Path] = None

    @classmethod
    def from_env(cls, **overrides: Any) -> "McpConfig":
        env = os.environ
        values: dict[str, Any] = {
            "host": env.get("SGX_MCP_HOST", "127.0.0.1"),
            "port": int(env.get("SGX_MCP_PORT", str(_DEFAULT_PORT))),
            "issuer_url": env.get("SGX_MCP_ISSUER_URL") or None,
            "password": env.get("SGX_MCP_PASSWORD") or None,
            "static_token": env.get("SGX_MCP_TOKEN") or None,
            "store_path": Path(env["SGX_MCP_STORE"]) if env.get("SGX_MCP_STORE") else None,
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    @property
    def effective_issuer_url(self) -> str:
        return (self.issuer_url or f"http://{self.host}:{self.port}").rstrip("/")

    def validate(self) -> None:
        if self.transport not in ("stdio", "http"):
            raise ValueError(f"Unknown transport {self.transport!r} (use stdio|http)")
        if self.transport == "http" and not self.no_auth:
            if not self.password and not self.static_token:
                raise ValueError(
                    "Refusing to serve MCP over HTTP without authentication: "
                    "set SGX_MCP_PASSWORD (OAuth, required for Claude connectors) "
                    "and/or SGX_MCP_TOKEN (static bearer), or pass --no-auth to "
                    "run an open server. These tools spend real xAI quota."
                )
            if not self.password:
                raise ValueError(
                    "SGX_MCP_TOKEN alone cannot onboard a Claude custom connector "
                    "(Claude requires OAuth). Set SGX_MCP_PASSWORD as well."
                )
        if self.transport == "http" and not self.no_auth:
            issuer = urlparse(self.effective_issuer_url)
            if issuer.scheme not in ("http", "https") or not issuer.netloc:
                raise ValueError(f"Invalid issuer URL: {self.effective_issuer_url!r}")


def _transport_security(issuer_url: str) -> TransportSecuritySettings:
    parsed = urlparse(issuer_url)
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_origins = ["http://127.0.0.1:*", "http://localhost:*"]
    if parsed.netloc:
        allowed_hosts.append(parsed.netloc)
        if parsed.hostname:
            allowed_hosts.append(f"{parsed.hostname}:*")
        allowed_origins.append(f"{parsed.scheme}://{parsed.netloc}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def build_server(config: McpConfig) -> FastMCP:
    """Build the FastMCP server for the given config (does not start it)."""
    config.validate()
    issuer_url = config.effective_issuer_url

    kwargs: dict[str, Any] = {
        "name": SERVER_NAME,
        "instructions": INSTRUCTIONS,
        "host": config.host,
        "port": config.port,
        "stateless_http": True,
        "json_response": True,
        "transport_security": _transport_security(issuer_url),
    }

    provider: Optional[SgxOAuthProvider] = None
    if config.transport == "http" and not config.no_auth:
        assert config.password  # guaranteed by validate()
        provider = SgxOAuthProvider(
            password=config.password,
            issuer_url=issuer_url,
            store_path=config.store_path,
            static_token=config.static_token,
        )
        kwargs["auth_server_provider"] = provider
        kwargs["auth"] = AuthSettings(
            issuer_url=AnyHttpUrl(issuer_url),
            resource_server_url=AnyHttpUrl(f"{issuer_url}/mcp"),
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            revocation_options=RevocationOptions(enabled=True),
        )

    server = FastMCP(**kwargs)
    _register_tools(server)

    if provider is not None:

        @server.custom_route("/login", methods=["GET"])
        async def login_page(request: Request) -> Response:
            state = request.query_params.get("state", "")
            return await provider.login_page(state)

        @server.custom_route("/login/callback", methods=["POST"])
        async def login_callback(request: Request) -> Response:
            return await provider.login_callback(request)

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok", "server": SERVER_NAME})

    return server


def run(config: McpConfig) -> None:
    server = build_server(config)
    if config.transport == "http":
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)


def _register_tools(server: FastMCP) -> None:
    @server.tool(annotations=_READ_ONLY)
    async def sgx_status() -> dict[str, Any]:
        """Check whether the sgx server can resolve xAI credentials.

        Returns the active credential provider/source (token is masked) or a
        ready=false status explaining what the operator must fix. Call this
        first when another tool reports a credentials error. Free and makes
        no xAI API call, so ready=true means credentials resolved — it does
        not prove the token is currently accepted (connectivity_checked
        is always false here; `sgx doctor` on the host probes for real).
        """
        try:
            creds = await asyncio.to_thread(resolve_credentials)
        except Exception as exc:  # missing/unusable credentials are a status, not a crash
            return {
                "ready": False,
                "error": str(exc),
                "fix": "On the server host, run `sgx auth device-login` (or `sgx auth login`), "
                "or set XAI_API_KEY in the server environment, then restart the MCP service.",
            }
        api_key = str(creds.get("api_key", ""))
        masked = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
        return {
            "ready": True,
            "connectivity_checked": False,
            "provider": creds.get("provider"),
            "source": creds.get("source"),
            "base_url": creds.get("base_url"),
            "api_key_masked": masked,
            "diagnostics": creds.get("diagnostics", []),
        }

    @server.tool(name="x_search", annotations=_READ_ONLY)
    async def x_search_tool(
        query: str,
        count: int = 5,
        web: bool = False,
        full: bool = False,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> dict[str, Any]:
        """Search X (Twitter) posts with xAI's privileged server-side x_search.

        Args:
            query: What to search for.
            count: Number of results, 1-20 (pure X mode).
            web: Also search the general web and synthesize (results become prose in raw_text).
            full: Return complete post text instead of ~500-char truncations.
            after: Only posts after this date (YYYY-MM-DD).
            before: Only posts before this date (YYYY-MM-DD).

        Makes one billed xAI call. Prefer this over research for simple lookups.
        """
        if not query.strip():
            raise ValueError("query must not be empty")
        count = max(1, min(count, 20))
        result = await asyncio.to_thread(
            x_search,
            query.strip(),
            count=count,
            web=web,
            full=full,
            after=after,
            before=before,
        )
        # Pure-X raw_text duplicates `results` byte-for-byte; don't ship both.
        return slim_search_result(result)

    @server.tool(annotations=_READ_ONLY)
    async def research(
        question: str,
        agents: int = 4,
        web: bool = True,
        x: bool = True,
    ) -> dict[str, Any]:
        """Deep multi-agent research on grok-4.20-multi-agent.

        A team of agents searches and synthesizes a thorough answer. This is
        slow (often 1-4 minutes) and is the most expensive sgx tool — use it
        only for questions that genuinely need multi-source synthesis, and
        prefer agents=4. agents=16 is deeper but may exceed the MCP client's
        tool-call timeout (240s on claude.ai).

        Args:
            question: The research question.
            agents: 4 (fast, default) or 16 (deep).
            web: Enable general web search for the agents.
            x: Enable X (Twitter) search for the agents.
        """
        if not question.strip():
            raise ValueError("question must not be empty")
        if agents not in (4, 16):
            raise ValueError("agents must be 4 or 16")
        tools: list[str] = []
        if web:
            tools.append("web_search")
        if x:
            tools.append("x_search")
        result = await asyncio.to_thread(
            multi_agent_research,
            question.strip(),
            effort="low" if agents == 4 else "high",
            tools=tools,
        )
        # raw_response can be huge and duplicates output_text; drop it.
        result.pop("raw_response", None)
        return result

    @server.tool(annotations=_WRITE)
    async def thread_new(name: str, model: str = "grok-4.3", force: bool = False) -> dict[str, Any]:
        """Create a persistent research thread stored on the server.

        Threads chain xAI responses (previous_response_id) so a conversation
        keeps full context across days and sessions. Creating one is free.
        Fails if the thread already exists — reuse it (thread_list to check),
        or pass force=true to deliberately discard its history and recreate.
        """
        if not name.strip():
            raise ValueError("name must not be empty")

        def create() -> dict[str, Any]:
            with _thread_lock:
                thread = ThreadStorage().create(name=name.strip(), model=model, exist_ok=force)
            return {"name": thread.name, "model": thread.model, "message_count": 0}

        return await asyncio.to_thread(create)

    @server.tool(annotations=_WRITE)
    async def thread_send(name: str, message: str, web: bool = False) -> dict[str, Any]:
        """Send a message to a persistent thread and return Grok's reply.

        The reply continues the thread's full server-side conversation context.
        Set web=true to let this turn use web + X search. Makes one billed xAI
        call. The thread must already exist (see thread_new).
        """
        if not message.strip():
            raise ValueError("message must not be empty")

        def send() -> dict[str, Any]:
            with _thread_lock:
                storage = ThreadStorage()
                thread = storage.load(name)  # raises ThreadNotFoundError
                previous_id = thread.response_ids[-1] if thread.response_ids else None
                tools = ["web_search", "x_search"] if web else None
                result = create_response(
                    input=[{"role": "user", "content": message}],
                    model=thread.model,
                    previous_response_id=previous_id,
                    tools=tools,
                )
                if result.get("id"):
                    storage.append_response(
                        name,
                        result["id"],
                        user=message,
                        grok=result.get("output_text", ""),
                    )
                return {
                    "thread": name,
                    "model": thread.model,
                    "response_id": result.get("id"),
                    "output_text": result.get("output_text", ""),
                    "usage": result.get("usage", {}),
                }

        return await asyncio.to_thread(send)

    @server.tool(annotations=_READ_ONLY)
    async def thread_list() -> dict[str, Any]:
        """List persistent research threads stored on the server. Free."""

        def list_threads() -> dict[str, Any]:
            threads = ThreadStorage().list()
            return {
                "threads": [
                    {
                        "name": t.name,
                        "model": t.model,
                        "message_count": len(t.response_ids),
                        "last_used": t.last_used,
                    }
                    for t in threads
                ]
            }

        return await asyncio.to_thread(list_threads)

    @server.tool(annotations=_READ_ONLY)
    async def thread_show(name: str, limit: int = 5) -> dict[str, Any]:
        """Show recent turns of a persistent thread (user + Grok text).

        Fetches stored responses from xAI; each turn's text is truncated to
        keep the result small. Default shows the last 5 turns.
        """
        limit = max(1, min(limit, 20))

        def show() -> dict[str, Any]:
            storage = ThreadStorage()
            thread = storage.load(name)  # raises ThreadNotFoundError
            turns = [
                {
                    "response_id": t["response_id"],
                    "user": t["user"][:2000],
                    "grok": t["grok"][:4000],
                }
                for t in thread_turns(thread, limit)
            ]
            return {
                "thread": {
                    "name": thread.name,
                    "model": thread.model,
                    "message_count": len(thread.response_ids),
                    "last_used": thread.last_used,
                },
                "turns": turns,
            }

        return await asyncio.to_thread(show)
