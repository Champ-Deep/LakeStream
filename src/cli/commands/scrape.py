"""Scrape and status commands."""

import time
from typing import Annotated

import typer
from rich.console import Console

from src.cli import client, output

console = Console()


def scrape(
    domain: Annotated[str, typer.Argument(help="Domain to scrape (e.g. example.com)")],
    template: Annotated[
        str | None, typer.Option("--template", "-t", help="Template ID.")
    ] = None,
    tier: Annotated[
        str | None,
        typer.Option(help="Force tier: playwright, playwright_proxy."),
    ] = None,
    max_pages: Annotated[int, typer.Option("--max-pages", "-m", help="Max pages.")] = 100,
    data_types: Annotated[
        str, typer.Option("--types", help="Comma-separated data types.")
    ] = "blog_url,article,contact,tech_stack,resource,pricing",
    wait: Annotated[
        bool, typer.Option("--wait", "-w", help="Wait for job completion.")
    ] = False,
) -> None:
    """Submit a scrape job for a domain."""
    payload = {
        "domain": domain,
        "template_id": template,
        "tier": tier,
        "max_pages": max_pages,
        "data_types": [t.strip() for t in data_types.split(",")],
    }

    result = client.post("/scrape/execute", data=payload)
    job_id = result["job_id"]

    console.print(f"[green]Job submitted:[/green] {job_id}")
    console.print(f"  Domain: {domain}")
    console.print(f"\nCheck progress: [bold]lakestream status {job_id}[/bold]")

    if wait:
        console.print()
        _poll_until_done(job_id)


def status(
    job_id: Annotated[str, typer.Argument(help="Scrape job ID (UUID).")],
    watch: Annotated[
        bool, typer.Option("--watch", "-w", help="Poll until job completes.")
    ] = False,
) -> None:
    """Check the status of a scrape job."""
    if watch:
        _poll_until_done(job_id)
    else:
        data = client.get(f"/scrape/status/{job_id}")
        output.print_job_status(data)


def _poll_until_done(job_id: str, interval: float = 2.0) -> None:
    """Poll job status until completed or failed."""
    with console.status("[bold blue]Scraping..."):
        while True:
            data = client.get(f"/scrape/status/{job_id}")
            if data["status"] in ("completed", "failed"):
                break
            time.sleep(interval)

    output.print_job_status(data)
