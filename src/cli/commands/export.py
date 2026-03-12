"""Export command for downloading scrape results."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from src.cli import client

console = Console()


def export(
    job_id: Annotated[str, typer.Argument(help="Scrape job ID (UUID).")],
    format: Annotated[
        str, typer.Option("--format", "-f", help="Export format: json or csv.")
    ] = "json",
    output_path: Annotated[
        str | None, typer.Option("--output", "-o", help="Output file path (default: stdout).")
    ] = None,
) -> None:
    """Export scrape results as JSON or CSV."""
    if format not in ("json", "csv"):
        console.print(f"[red]Unsupported format:[/red] {format}. Use 'json' or 'csv'.")
        raise typer.Exit(1)

    content = client.download(f"/export/{format}/{job_id}")

    if output_path:
        path = Path(output_path)
        path.write_bytes(content)
        console.print(f"[green]Exported to:[/green] {path.resolve()}")
    else:
        typer.echo(content.decode("utf-8"))
