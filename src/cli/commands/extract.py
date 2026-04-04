"""Extract command — scrape a URL and extract structured data."""

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from src.cli import client

console = Console()


def extract(
    url: Annotated[str, typer.Argument(help="URL to extract data from")],
    prompt: Annotated[
        str | None,
        typer.Option("--prompt", "-p", help="Natural language: what to extract (no schema needed)"),
    ] = None,
    schema_file: Annotated[
        Path | None,
        typer.Option("--schema", "-s", help="Path to JSON schema file"),
    ] = None,
    mode: Annotated[
        str,
        typer.Option("--mode", "-m", help="Extraction mode: prompt, css, ai, auto"),
    ] = "prompt",
    output_format: Annotated[
        str,
        typer.Option("--output", "-o", help="Output format: json, table"),
    ] = "json",
    region: Annotated[
        str | None,
        typer.Option("--region", "-r", help="Geo-target region: us, eu, uk, asia, in, au"),
    ] = None,
) -> None:
    """Extract structured data from a URL.

    Examples:

      # Prompt-only (simplest — no schema needed):
      lakestream extract https://stripe.com/pricing --prompt "extract all plan names and prices"

      # Schema-based (precise, reusable):
      lakestream extract https://stripe.com/pricing --schema pricing.json --mode css

      # Auto mode (try CSS first, fallback to AI):
      lakestream extract https://example.com --schema schema.json --mode auto
    """
    if not prompt and not schema_file:
        console.print("[red]Error:[/red] Provide --prompt or --schema")
        raise typer.Exit(1)

    payload: dict = {"url": url, "region": region}

    if schema_file:
        if not schema_file.exists():
            console.print(f"[red]Error:[/red] Schema file not found: {schema_file}")
            raise typer.Exit(1)
        payload["schema"] = json.loads(schema_file.read_text())
        payload["mode"] = mode if mode != "prompt" else "auto"
    else:
        payload["prompt"] = prompt
        payload["mode"] = "prompt"

    with console.status("[bold blue]Extracting...[/bold blue]"):
        result = client.post("/scrape/extract", data=payload)

    if not result.get("success"):
        console.print(f"[red]Extraction failed:[/red] {result.get('error', 'Unknown error')}")
        raise typer.Exit(1)

    data = result.get("data", {})
    mode_used = result.get("mode", "")

    console.print(f"[dim]Mode: {mode_used} | URL: {result.get('url', url)}[/dim]\n")

    if output_format == "table" and isinstance(data, list) and data:
        _print_table(data)
    elif output_format == "table" and isinstance(data, dict):
        _print_dict_table(data)
    else:
        syntax = Syntax(json.dumps(data, indent=2), "json", theme="monokai")
        console.print(syntax)

    if result.get("fields_missing"):
        console.print(f"\n[yellow]Missing fields:[/yellow] {', '.join(result['fields_missing'])}")


def _print_table(rows: list[dict]) -> None:
    if not rows:
        return
    table = Table(show_header=True, header_style="bold")
    for key in rows[0].keys():
        table.add_column(key)
    for row in rows:
        table.add_row(*[str(v) for v in row.values()])
    console.print(table)


def _print_dict_table(data: dict) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Field")
    table.add_column("Value")
    for k, v in data.items():
        table.add_row(k, str(v))
    console.print(table)
