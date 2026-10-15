#!/usr/bin/env python3
# ABOUTME: End-to-end smoke test for a deployed sgx remote MCP server, the way Muse connects
# ABOUTME: 401 discovery -> PRM -> AS metadata -> DCR -> PKCE authorize -> password login -> token -> tools

"""Usage:
    SGX_MCP_PASSWORD=... uv run --extra mcp python scripts/mcp_smoke.py https://host/mcp
    SGX_MCP_TOKEN=...   uv run --extra mcp python scripts/mcp_smoke.py https://host/mcp --static-token

Exit code 0 only if discovery, auth, tool listing, and an sgx_status call all succeed.
Set SMOKE_SEARCH=1 to also run one live x_search (spends xAI quota).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import httpx


def fail(step: str, detail: str) -> None:
    print(f"FAIL [{step}] {detail}")
    sys.exit(1)


def ok(step: str, detail: str = "") -> None:
    print(f"  ok [{step}] {detail}")


async def oauth_access_token(mcp_url: str, password: str) -> str:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        # 1. Unauthenticated MCP request must 401 with a resource_metadata pointer.
        resp = await client.post(
            mcp_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        if resp.status_code != 401:
            fail("401", f"expected 401, got {resp.status_code}: {resp.text[:200]}")
        www = resp.headers.get("www-authenticate", "")
        if "resource_metadata" not in www:
            fail("401", f"WWW-Authenticate lacks resource_metadata: {www!r}")
        prm_url = www.split('resource_metadata="')[1].split('"')[0]
        ok("401", f"resource_metadata={prm_url}")

        # 2. Protected resource metadata: resource must match the MCP URL exactly.
        prm = (await client.get(prm_url)).json()
        if prm.get("resource") != mcp_url:
            fail("prm", f"resource={prm.get('resource')!r} != {mcp_url!r}")
        issuer = prm["authorization_servers"][0]
        ok("prm", f"issuer={issuer} scopes={prm.get('scopes_supported')}")

        # 3. Authorization server metadata.
        meta_resp = await client.get(f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server")
        if meta_resp.status_code != 200:
            fail("as-metadata", f"{meta_resp.status_code} at {meta_resp.url}")
        meta = meta_resp.json()
        for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
            if key not in meta:
                fail("as-metadata", f"missing {key}: {json.dumps(meta)[:300]}")
        if "S256" not in meta.get("code_challenge_methods_supported", []):
            fail("as-metadata", "S256 PKCE not advertised")
        ok("as-metadata", "authorize/token/register endpoints + S256")

        # 4. Dynamic client registration (public client, like Muse).
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"
        reg = await client.post(
            meta["registration_endpoint"],
            json={
                "client_name": "sgx-smoke-test",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "sgx",
            },
        )
        if reg.status_code not in (200, 201):
            fail("dcr", f"{reg.status_code}: {reg.text[:300]}")
        client_id = reg.json()["client_id"]
        ok("dcr", f"client_id={client_id[:12]}...")

        # 5. Authorization request with PKCE -> redirects to the login page.
        verifier = secrets.token_urlsafe(32)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_hex(8)
        auth_resp = await client.get(
            meta["authorization_endpoint"],
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": "sgx",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": mcp_url,
            },
        )
        if auth_resp.status_code not in (302, 303, 307):
            fail(
                "authorize",
                f"expected redirect to login, got {auth_resp.status_code}: {auth_resp.text[:300]}",
            )
        login_url = auth_resp.headers["location"]
        ok("authorize", f"-> {login_url}")

        # 6. Operator password on the consent form -> redirect back with a code.
        login_state = parse_qs(urlparse(login_url).query)["state"][0]
        base = f"{urlparse(login_url).scheme}://{urlparse(login_url).netloc}"
        login_resp = await client.post(
            f"{base}/login/callback", data={"state": login_state, "password": password}
        )
        if login_resp.status_code != 302:
            fail("login", f"{login_resp.status_code}: {login_resp.text[:300]}")
        callback = login_resp.headers["location"]
        code = parse_qs(urlparse(callback).query)["code"][0]
        ok("login", "authorization code issued")

        # 7. Code exchange (form-urlencoded, PKCE verifier).
        token_resp = await client.post(
            meta["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": mcp_url,
            },
        )
        if token_resp.status_code != 200:
            fail("token", f"{token_resp.status_code}: {token_resp.text[:300]}")
        tokens = token_resp.json()
        if not tokens.get("refresh_token"):
            fail("token", "no refresh_token issued (Claude needs refresh)")
        ok("token", f"access+refresh issued, expires_in={tokens.get('expires_in')}")

        # 8. Refresh rotates.
        refreshed = await client.post(
            meta["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client_id,
                "resource": mcp_url,
            },
        )
        if refreshed.status_code != 200:
            fail("refresh", f"{refreshed.status_code}: {refreshed.text[:300]}")
        if refreshed.json().get("refresh_token") == tokens["refresh_token"]:
            fail("refresh", "refresh token was not rotated")
        ok("refresh", "rotated refresh token")
        return refreshed.json()["access_token"]


async def mcp_check(mcp_url: str, access_token: str) -> None:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(
        mcp_url, headers={"Authorization": f"Bearer {access_token}"}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            ok("tools/list", ", ".join(names))
            expected = {
                "sgx_status",
                "x_search",
                "research",
                "thread_new",
                "thread_send",
                "thread_list",
                "thread_show",
            }
            if set(names) != expected:
                fail("tools/list", f"missing/extra: {expected.symmetric_difference(names)}")

            result = await session.call_tool("sgx_status", {})
            text = "".join(c.text for c in result.content if hasattr(c, "text"))
            status = json.loads(text)
            ok(
                "sgx_status",
                f"ready={status.get('ready')} provider={status.get('provider')} source={status.get('source')}",
            )
            if not status.get("ready"):
                print(f"  !! xAI credentials not ready on server: {status.get('fix')}")

            if os.environ.get("SMOKE_SEARCH") == "1":
                if not status.get("ready"):
                    fail("x_search", "credentials not ready; cannot run live search")
                search = await session.call_tool("x_search", {"query": "grok", "count": 2})
                stext = "".join(c.text for c in search.content if hasattr(c, "text"))
                parsed = json.loads(stext)
                ok("x_search", f"{len(parsed.get('results', []))} results")
                if search.isError:
                    fail("x_search", stext[:300])


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    mcp_url = args[0].rstrip("/")
    print(f"Smoke testing {mcp_url}")

    if "--static-token" in sys.argv:
        token = os.environ.get("SGX_MCP_TOKEN", "")
        if not token:
            fail("env", "SGX_MCP_TOKEN not set")
        await mcp_check(mcp_url, token)
    else:
        password = os.environ.get("SGX_MCP_PASSWORD", "")
        if not password:
            fail("env", "SGX_MCP_PASSWORD not set")
        access_token = await oauth_access_token(mcp_url, password)
        await mcp_check(mcp_url, access_token)
    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
