# ABOUTME: Typer CLI entrypoint for sgx — search, research, threads, and doctor
# ABOUTME: Clean human + machine (--json) output; reuses the auth + client layers built via TDD

from __future__ import annotations

import json
from typing import NoReturn, Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from .auth import XaiAuthStore, get_xai_auth, resolve_credentials
from .auth_login import login_xai_oauth
from .client import create_response, multi_agent_research, retrieve_response, x_search
from .threads import ThreadNotFoundError, ThreadStorage

import os
import tempfile
import webbrowser
from pathlib import Path
from typing import Optional

def _handle_cli_error(e: Exception, prefix: str = "Error", json_output: bool = False) -> NoReturn:
    """Standard error handler supporting both human and machine (--json) output."""
    if json_output:
        error_obj = {
            "status": "error",
            "error": str(e),
            "type": e.__class__.__name__,
        }
        typer.echo(json.dumps(error_obj, indent=2, default=str))
    else:
        rprint(f"[red]{prefix}:[/red] {e}")
    raise typer.Exit(1)


def _generate_visual_html_explainer(
    content: str,
    title: str,
    html_dir: Optional[str] = None,
) -> str:
    """
    Turn research or search output into a premium self-contained visual HTML explainer
    using the style and quality bar from Nico Preme's visual explainer skill.
    """
    system_prompt = """You are an expert visual explainer following Nico Preme's Visual Explainer methodology.

Your job: Turn the provided research/search content into a beautiful, self-contained, standalone HTML5 artifact.

Strict requirements:
- Output ONLY a complete, valid <!DOCTYPE html> ... </html> document. Nothing before or after.
- Single-file: all CSS and JS must be embedded (use Tailwind via CDN + custom styles).
- Aesthetic: deep navy/black background, glassmorphism panels with subtle borders, cyan (#67e8f9), blue, violet accents. Crisp modern typography.
- Semantic colors: cyan=active/primary, blue=information, violet=latent/agentic, amber=tradeoffs/warnings, emerald=success.
- Structure: Clear title, one-sentence "teaching kernel", well-organized sections, visual models (SVG diagrams preferred), key takeaways at the bottom.
- Interactivity: Add meaningful interactions (hover states, simple tabs/accordions, toggles, or sliders) that help understanding. Respect prefers-reduced-motion.
- Quality bar: A non-expert should understand the core idea in under 60 seconds. The visual state must match the explanation. No placeholder text or dead UI.
- Make it feel premium and educational, like something published to a high-end design system.

Focus on the most important insights from the content. Choose the best visual form (process stepper, architecture diagram, comparison, causal chain, etc.)."""

    user_prompt = f"""Research / Search Title: {title}

Content to visualize:
{content}

Generate the complete standalone visual HTML explainer now."""

    try:
        html_result = create_response(
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model="grok-4.3",
            tools=[],
        )
        html_content = html_result.get("output_text", "").strip()

        # Determine output directory
        if html_dir:
            out_dir = Path(html_dir).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = Path(tempfile.gettempdir())

        safe_title = "".join(c for c in title[:60] if c.isalnum() or c in " -_").strip().replace(" ", "-")
        filename = f"sgx-explainer-{safe_title or 'result'}.html"
        filepath = out_dir / filename

        filepath.write_text(html_content, encoding="utf-8")
        return str(filepath)

    except Exception as e:
        raise RuntimeError(f"Failed to generate visual HTML: {e}") from e

app = typer.Typer(
    name="sgx",
    help="SuperGrok X — privileged xAI tools (starting with real X search) using your Hermes OAuth or API key.",
    add_completion=False,
    no_args_is_help=True,
)

thread_app = typer.Typer(help="Manage persistent research threads (stateful conversations)")

app.add_typer(thread_app, name="thread")

auth_app = typer.Typer(help="Manage xAI credentials (Hermes OAuth or API key)")

app.add_typer(auth_app, name="auth")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query for X posts"),
    count: int = typer.Option(5, "--count", "-c", min=1, max=20, help="Number of results (1-20)"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Grok 4 model to use (default: grok-4.3)"),
    web: bool = typer.Option(False, "--web", "-w", help="Also enable general web search (hybrid X + web results)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Emit machine-readable JSON only"),
    html: bool = typer.Option(False, "--html", help="Generate a premium self-contained visual HTML explainer"),
    open_browser: bool = typer.Option(False, "--open", "-o", help="Open the generated HTML in the default browser"),
    html_dir: Optional[str] = typer.Option(None, "--html-dir", help="Directory to save the HTML file (default: /tmp)"),
):
    """Search X (and optionally the web) using xAI's server-side tools."""
    try:
        result = x_search(query, count=count, model=model, web=web)

        if json_output:
            typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
            raise typer.Exit(0)

        # Handle --html / --open for search results
        if html or open_browser:
            try:
                # Build a nice text representation for the explainer
                if result.get("results"):
                    explainer_text = "\n".join(
                        f"- {r.get('text', '')} ({r.get('url', '')})" for r in result.get("results", [])
                    )
                else:
                    explainer_text = result.get("raw_text", "") or result.get("output_text", "")

                html_path = _generate_visual_html_explainer(
                    content=explainer_text,
                    title=f"X Search: {query}",
                    html_dir=html_dir,
                )

                if open_browser:
                    webbrowser.open(f"file://{html_path}")

                rprint(f"\n[green]✓ Visual HTML explainer generated:[/green] {html_path}")
                if html:
                    raise typer.Exit(0)

            except Exception as e:
                rprint(f"[yellow]Warning:[/yellow] Could not generate visual HTML ({e}). Falling back to text.")

        # Pretty human output
        if result.get("web"):
            rprint(f"[bold]Hybrid Search[/bold] (X + Web)  (model: {result['model']})")
        else:
            rprint(f"[bold]X Search[/bold] (model: {result['model']})")
        rprint(f"Query: [italic]{query}[/italic]\n")

        results = result.get("results", [])
        if results and not result.get("web"):
            # Pure X structured results
            for i, r in enumerate(results, 1):
                author = r.get("author") or ""
                created = r.get("created_at") or ""
                meta = f" ({author} • {created})" if author or created else ""
                rprint(f"[bold]{i}.[/bold] {r['url']}{meta}")
                rprint(f"   {r['text']}\n")
        else:
            # Web-augmented or fallback: show the model's synthesized response
            text = result.get("raw_text", "").strip()
            if text:
                rprint(text)
            else:
                rprint("[yellow]No results found.[/yellow]")

        raise typer.Exit(0)

    except RuntimeError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        rprint(f"[red]Unexpected error:[/red] {e}")
        raise typer.Exit(2)


@app.command()
def research(
    query: str = typer.Argument(..., help="The deep research question to investigate"),
    effort: str = typer.Option(
        "low",
        "--effort",
        "-e",
        help="Advanced: low/medium/high/xhigh. Use --agents instead for most cases.",
    ),
    agents: int = typer.Option(
        0,
        "--agents",
        "-a",
        help="Number of collaborating agents: 4 (fast) or 16 (deep research). Recommended way to control depth.",
    ),
    tools: str = typer.Option(
        "web_search,x_search",
        "--tools",
        "-t",
        help="Comma-separated list of built-in tools to enable (web_search,x_search,code_execution)",
    ),
    web: bool = typer.Option(False, "--web", "-w", help="Ensure general web search is enabled (augments X search)"),
    no_tools: bool = typer.Option(False, "--no-tools", help="Disable all server-side tools"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output full result as JSON"),
    html: bool = typer.Option(False, "--html", help="Generate a premium self-contained visual HTML explainer"),
    open_browser: bool = typer.Option(False, "--open", "-o", help="Open the generated HTML file in the default browser"),
    html_dir: Optional[str] = typer.Option(None, "--html-dir", help="Directory to save the HTML file (default: /tmp)"),
):
    """
    Deep multi-agent research using grok-4.20-multi-agent.

    Spins up a team of specialized agents (4 or 16) that collaborate in real time,
    use tools, and produce a high-quality synthesized answer.

    Use --agents 4 for quick focused research or --agents 16 for deep, thorough work.
    Use --web to ensure general web search is included.

    Use --html to turn the output into a beautiful standalone visual explainer HTML (using Nico Preme style).
    Use --open to automatically open it in your browser.

    Significantly more powerful (and expensive) than normal `sgx search`.
    """
    try:
        # Handle --agents as a user-friendly alias for effort
        final_effort = effort
        if agents != 0:
            if agents == 4:
                final_effort = "low"
            elif agents == 16:
                final_effort = "high"
            else:
                rprint("[red]--agents only supports 4 or 16[/red]")
                raise typer.Exit(1)

            if effort != "low":
                rprint(f"[yellow]Note:[/yellow] --agents {agents} overrides --effort {effort}")

        if no_tools:
            tool_list = []
        else:
            tool_list = [t.strip() for t in tools.split(",") if t.strip()]

            if web and "web_search" not in tool_list:
                tool_list.append("web_search")

        result = multi_agent_research(
            query,
            effort=final_effort,
            tools=tool_list if tool_list else [],
        )

        if json_output:
            typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            raise typer.Exit(0)

        answer = result.get("output_text", "").strip()

        # Handle --html / --open using visual explainer style (Nico Preme style)
        if html or open_browser:
            try:
                html_path = _generate_visual_html_explainer(
                    content=answer,
                    title=query,
                    html_dir=html_dir,
                )

                if open_browser:
                    webbrowser.open(f"file://{html_path}")

                rprint(f"\n[green]✓ Visual HTML explainer generated:[/green] {html_path}")
                if html:
                    raise typer.Exit(0)

            except Exception as e:
                rprint(f"[yellow]Warning:[/yellow] Could not generate visual HTML ({e}). Falling back to text output.")

        # Human-friendly output (only if we didn't exit for HTML)
        rprint(f"[bold]Multi-Agent Research[/bold]  (model: {result['model']}, effort: {result['effort']})")
        rprint(f"Tools: {', '.join(result['tools_used']) or 'none'}")
        rprint(f"Query: [italic]{query}[/italic]\n")

        if answer:
            rprint(answer)
        else:
            rprint("[yellow]No final answer returned by leader agent.[/yellow]")

        # Usage summary
        usage = result.get("usage", {})
        if usage:
            total = usage.get("total_tokens", 0)
            tool_calls = usage.get("num_server_side_tools_used", 0)
            rprint(f"\n[dim]Usage: {total:,} tokens | {tool_calls} server-side tool calls[/dim]")

        raise typer.Exit(0)

    except ValueError as e:
        rprint(f"[red]Invalid option:[/red] {e}")
        raise typer.Exit(1)
    except RuntimeError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        rprint(f"[red]Unexpected error:[/red] {e}")
        raise typer.Exit(2)


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", "-j", help="Emit machine-readable JSON only"),
):
    """Show which credentials sgx will use and basic connectivity info."""
    console = Console()

    try:
        creds = resolve_credentials()
        provider = creds["provider"]
        base = creds["base_url"]

        masked_key = creds["api_key"][:8] + "..." + creds["api_key"][-4:]

        if json_output:
            result = {
                "status": "success",
                "credentials": {
                    "provider": provider,
                    "base_url": base,
                    "api_key_masked": masked_key,
                },
                "ready": True,
            }
            typer.echo(json.dumps(result, indent=2, default=str))
            raise typer.Exit(0)

        table = Table(title="sgx doctor — credential status")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Active provider", provider)
        table.add_row("Base URL", base)
        table.add_row("API key (masked)", masked_key)

        console.print(table)

        if provider == "xai-oauth":
            rprint("\n[green]✓ Using Hermes SuperGrok OAuth (best experience)[/green]")
        else:
            rprint("\n[yellow]Using classic XAI_API_KEY (still works for search)[/yellow]")

        rprint("\nTrying a tiny connectivity check to the Responses endpoint...")

        # Light probe — we don't want to burn quota, so just resolve + show readiness
        rprint("[green]Credentials resolved successfully. Ready for `sgx search`.[/green]")

    except RuntimeError as e:
        _handle_cli_error(e, "Credential problem", json_output=json_output)


# =============================================================================
# Thread Commands (Persistent Research Threads)
# =============================================================================

@thread_app.command("new")
def thread_new(
    name: str = typer.Argument(..., help="Name for the new research thread"),
    model: str = typer.Option("grok-4.3", "--model", "-m", help="Model to use for this thread"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Emit machine-readable JSON only"),
):
    """Create a new persistent research thread."""
    storage = ThreadStorage()
    try:
        thread = storage.create(name=name, model=model)
        if json_output:
            typer.echo(json.dumps({
                "status": "success",
                "thread": {"name": thread.name, "model": thread.model}
            }, indent=2, default=str))
            raise typer.Exit(0)

        rprint(f"[green]Created thread[/green] '{name}' (model: {model})")
        rprint("Use: sgx thread send " + name + ' "your first message"')
    except Exception as e:
        _handle_cli_error(e, "Error creating thread", json_output=json_output)


@thread_app.command("send")
def thread_send(
    name: str = typer.Argument(..., help="Name of the thread"),
    message: str = typer.Argument(..., help="Message to send to the thread"),
    web: bool = typer.Option(False, "--web", "-w", help="Augment this message with general web search"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Emit machine-readable JSON only"),
):
    """Send a message to a persistent thread (continues the conversation)."""
    storage = ThreadStorage()

    try:
        thread = storage.load(name)
    except ThreadNotFoundError:
        if json_output:
            typer.echo(json.dumps({
                "status": "error",
                "error": f"Thread '{name}' not found",
                "suggestion": f"sgx thread new {name}"
            }, indent=2))
            raise typer.Exit(1)
        rprint(f"[red]Thread '{name}' not found.[/red] Use 'sgx thread new {name}' first.")
        raise typer.Exit(1)

    previous_id = thread.response_ids[-1] if thread.response_ids else None

    tools = None
    if web:
        tools = ["web_search", "x_search"]

    try:
        result = create_response(
            input=[{"role": "user", "content": message}],
            model=thread.model,
            previous_response_id=previous_id,
            tools=tools,
        )

        new_id = result.get("id")
        if new_id:
            storage.append_response(name, new_id)

        output = result.get("output_text", "").strip()

        if json_output:
            response_data = {
                "status": "success",
                "thread": name,
                "response_id": new_id,
                "output_text": output,
                "model": thread.model,
            }
            typer.echo(json.dumps(response_data, indent=2, default=str))
            raise typer.Exit(0)

        if output:
            rprint(f"[bold]Thread:[/bold] {name}\n")
            rprint(output)
        else:
            rprint("[yellow]No response text returned.[/yellow]")

    except Exception as e:
        _handle_cli_error(e, "Error sending to thread", json_output=json_output)


@thread_app.command("list")
def thread_list(
    json_output: bool = typer.Option(False, "--json", "-j", help="Emit machine-readable JSON only"),
):
    """List all persistent research threads."""
    storage = ThreadStorage()
    threads = storage.list()

    if json_output:
        result = [
            {
                "name": t.name,
                "model": t.model,
                "message_count": len(t.response_ids),
                "last_used": t.last_used,
            }
            for t in threads
        ]
        typer.echo(json.dumps(result, indent=2, default=str))
        raise typer.Exit(0)

    if not threads:
        rprint("[yellow]No threads found.[/yellow] Create one with 'sgx thread new <name>'")
        raise typer.Exit(0)

    console = Console()
    table = Table(title="Research Threads")
    table.add_column("Name", style="cyan")
    table.add_column("Model", style="magenta")
    table.add_column("Messages", justify="right")
    table.add_column("Last Used", style="dim")

    for t in threads:
        last = t.last_used[:19] if t.last_used else "-"
        table.add_row(t.name, t.model, str(len(t.response_ids)), last)

    console.print(table)


@thread_app.command("show")
def thread_show(
    name: str = typer.Argument(..., help="Name of the thread to inspect"),
    limit: int = typer.Option(10, "--limit", "-l", help="Number of recent turns to show"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Emit machine-readable JSON only"),
):
    """Show the conversation history of a thread."""
    storage = ThreadStorage()

    try:
        thread = storage.load(name)
    except ThreadNotFoundError:
        if json_output:
            typer.echo(json.dumps({"status": "error", "error": f"Thread '{name}' not found"}, indent=2))
            raise typer.Exit(1)
        rprint(f"[red]Thread '{name}' not found.[/red]")
        raise typer.Exit(1)

    if json_output:
        turns = []
        ids_to_show = thread.response_ids[-limit:]
        for rid in ids_to_show:
            try:
                resp = retrieve_response(rid)
            except Exception:
                continue

            user_parts = []
            for item in resp.get("input", []):
                if item.get("role") == "user":
                    content = item.get("content", "")
                    if isinstance(content, str):
                        user_parts.append(content)
                    elif isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and "text" in c:
                                user_parts.append(c["text"])

            turns.append({
                "response_id": rid,
                "user": " ".join(user_parts).strip(),
                "grok": resp.get("output_text", "").strip(),
            })

        result = {
            "status": "success",
            "thread": {
                "name": thread.name,
                "model": thread.model,
                "message_count": len(thread.response_ids),
                "last_used": thread.last_used,
            },
            "turns": turns,
        }
        typer.echo(json.dumps(result, indent=2, default=str))
        raise typer.Exit(0)

    rprint(f"[bold]Thread:[/bold] {thread.name}  (model: {thread.model})")
    rprint(f"Total messages: {len(thread.response_ids)}")
    rprint(f"Last used: {thread.last_used}\n")

    if not thread.response_ids:
        rprint("[yellow]This thread has no messages yet.[/yellow]")
        return

    # Show the most recent turns
    ids_to_show = thread.response_ids[-limit:]

    for i, rid in enumerate(ids_to_show, 1):
        try:
            resp = retrieve_response(rid)
        except Exception as e:
            rprint(f"[red]Failed to retrieve response {rid}: {e}[/red]")
            continue

        # Extract user input(s) for this turn
        user_parts = []
        for item in resp.get("input", []):
            if item.get("role") == "user":
                content = item.get("content", "")
                if isinstance(content, str):
                    user_parts.append(content)
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and "text" in c:
                            user_parts.append(c["text"])

        user_msg = " ".join(user_parts).strip() or "(no user input captured)"

        # Extract assistant output
        assistant_msg = resp.get("output_text", "").strip() or "(no output text)"

        rprint(f"[bold cyan]Turn {len(thread.response_ids) - len(ids_to_show) + i}:[/bold cyan]")
        rprint(f"[green]User:[/green] {user_msg[:200]}{'...' if len(user_msg) > 200 else ''}")
        rprint(f"[blue]Grok:[/blue] {assistant_msg[:400]}{'...' if len(assistant_msg) > 400 else ''}\n")


# =============================================================================
# Auth Commands (credential management)
# =============================================================================

@auth_app.command("status")
def auth_status(
    json_output: bool = typer.Option(False, "--json", "-j", help="Emit machine-readable JSON only"),
):
    """Show which credential source is active and basic info."""
    console = Console()

    try:
        creds = resolve_credentials()
        provider = creds["provider"]
        base = creds["base_url"]
        masked = creds["api_key"][:8] + "..." + creds["api_key"][-4:]

        if json_output:
            result = {
                "status": "success",
                "credentials": {
                    "provider": provider,
                    "base_url": base,
                    "api_key_masked": masked,
                }
            }
            typer.echo(json.dumps(result, indent=2, default=str))
            raise typer.Exit(0)

        table = Table(title="sgx auth status")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Active provider", provider)
        table.add_row("Base URL", base)
        table.add_row("API key (masked)", masked)

        console.print(table)

        if provider == "xai-oauth":
            rprint("\n[green]✓ Using native sgx or Hermes SuperGrok OAuth[/green]")
        else:
            rprint("\n[yellow]Using XAI_API_KEY from environment[/yellow]")

        rprint("\n[dim]Use 'sgx auth logout' to clear the native sgx credential store.[/dim]")

    except RuntimeError as e:
        _handle_cli_error(e, "No credentials available", json_output=json_output)


@auth_app.command("logout")
def auth_logout(
    json_output: bool = typer.Option(False, "--json", "-j", help="Emit machine-readable JSON only"),
):
    """Clear credentials stored in the native sgx auth store (~/.sgx/auth.json)."""
    store = XaiAuthStore()
    try:
        store.clear()
        if json_output:
            typer.echo(json.dumps({"status": "success", "message": "Cleared native sgx credentials"}, indent=2))
            raise typer.Exit(0)

        rprint("[green]Cleared native sgx credentials.[/green]")
        rprint("[dim]Hermes fallback (if present) and XAI_API_KEY env var are unaffected.[/dim]")
    except Exception as e:
        _handle_cli_error(e, "Error during logout", json_output=json_output)


@auth_app.command("login")
def auth_login(
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the authorization URL but do not open a browser automatically"
    ),
    timeout: float = typer.Option(
        180.0, "--timeout", "-t", help="Seconds to wait for the browser callback"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Emit machine-readable JSON only"),
):
    """Log in to xAI using your SuperGrok subscription (browser OAuth PKCE flow).

    This writes credentials directly into the native sgx store (~/.sgx/auth.json).
    After a successful login you can use all sgx commands without setting XAI_API_KEY.
    """
    store = XaiAuthStore()

    # Check for existing official Grok CLI credentials first (best UX)
    grok_creds = store.load_grok_cli_xai_oauth()
    if grok_creds:
        if typer.confirm(
            "Found existing official Grok CLI credentials in ~/.grok/auth.json. "
            "Use them instead of opening a new xAI OAuth login?",
            default=True,
        ):
            try:
                # Import them into our native store
                tokens = {
                    "access_token": grok_creds["api_key"],
                }
                if "refresh_token" in grok_creds:
                    tokens["refresh_token"] = grok_creds["refresh_token"]

                store.save_xai_oauth(tokens)
                if json_output:
                    typer.echo(json.dumps({
                        "status": "success",
                        "message": "Imported credentials from official Grok CLI"
                    }, indent=2))
                    raise typer.Exit(0)

                rprint("[green]✓ Imported credentials from official Grok CLI.[/green]")
                rprint("You can now use sgx without setting any environment variables.")
                return
            except Exception as e:
                _handle_cli_error(e, "Failed to import Grok CLI credentials", json_output=json_output)

    # Normal full login flow
    try:
        result = login_xai_oauth(
            store=store,
            open_browser=not no_browser,
            timeout_seconds=timeout,
        )

        if json_output:
            masked = result["api_key"][:8] + "..." + result["api_key"][-4:]
            typer.echo(json.dumps({
                "status": "success",
                "provider": result["provider"],
                "api_key_masked": masked,
            }, indent=2, default=str))
            raise typer.Exit(0)

        rprint("[green]✓ Login successful![/green]")
        rprint(f"Active provider: [bold]{result['provider']}[/bold]")
        masked = result["api_key"][:8] + "..." + result["api_key"][-4:]
        rprint(f"Token (masked): {masked}")
        rprint("\nYou can now run [bold]sgx search[/bold], [bold]sgx research[/bold], etc. without any environment variables.")

    except Exception as e:
        _handle_cli_error(e, "Login failed", json_output=json_output)


if __name__ == "__main__":
    app()
