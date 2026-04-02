"""Browse command — autonomous AI browser agent."""

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.syntax import Syntax

from src.cli import client

console = Console()


def browse(
    task: Annotated[str, typer.Argument(help="Task for the AI agent (natural language)")],
    url: Annotated[
        str | None,
        typer.Option("--url", "-u", help="Starting URL (optional)"),
    ] = None,
    max_steps: Annotated[
        int,
        typer.Option("--max-steps", help="Maximum browser steps (default 20, max 50)"),
    ] = 20,
    output_format: Annotated[
        str,
        typer.Option("--output", "-o", help="Output format: text, json"),
    ] = "text",
) -> None:
    """Use an AI browser agent to complete a multi-step web task.

    The agent can navigate pages, click buttons, fill forms, handle
    pagination, and extract data — like a human browsing on your behalf.

    Requires OPENROUTER_API_KEY to be configured.

    Examples:

      lakestream browse "find all pricing plans" --url https://stripe.com/pricing

      lakestream browse "search for B2B analytics tools on g2.com and list top 10"

      lakestream browse "find the founding year and CEO of acme.com" --url https://acme.com/about
    """
    payload = {
        "task": task,
        "start_url": url or "",
        "max_steps": min(max_steps, 50),
    }

    console.print(f"[bold blue]Starting browser agent[/bold blue]")
    console.print(f"  Task: {task}")
    if url:
        console.print(f"  Starting URL: {url}")
    console.print(f"  Max steps: {max_steps}\n")

    with console.status("[bold blue]Agent running...[/bold blue]"):
        result = client.post("/scrape/browse", data=payload)

    if not result.get("success"):
        console.print(f"[red]Agent failed:[/red] {result.get('error', 'Unknown error')}")
        raise typer.Exit(1)

    steps = result.get("steps_taken", 0)
    urls = result.get("urls_visited", [])
    agent_result = result.get("result", "")

    if output_format == "json":
        syntax = Syntax(json.dumps(result, indent=2), "json", theme="monokai")
        console.print(syntax)
    else:
        console.print(f"[green]Done[/green] ({steps} steps)\n")
        if agent_result:
            console.print(agent_result)
        if urls:
            console.print(f"\n[dim]Pages visited: {', '.join(urls[:5])}"
                         f"{'...' if len(urls) > 5 else ''}[/dim]")
