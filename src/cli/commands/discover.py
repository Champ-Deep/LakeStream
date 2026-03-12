"""Discovery (search-to-scrape) command."""

import time
from typing import Annotated

import typer
from rich.console import Console

from src.cli import client, output

console = Console()


def discover(
    query: Annotated[str, typer.Argument(help="Search query (e.g. 'B2B data providers')")],
    search_pages: Annotated[
        int, typer.Option("--pages", "-p", help="Search result pages.")
    ] = 3,
    template: Annotated[str, typer.Option("--template", "-t", help="Template ID.")] = "generic",
    max_pages: Annotated[
        int, typer.Option("--max-pages", "-m", help="Max pages per domain.")
    ] = 50,
    data_types: Annotated[
        str, typer.Option("--types", help="Comma-separated data types.")
    ] = "blog_url,article,contact,tech_stack,resource,pricing",
    wait: Annotated[
        bool, typer.Option("--wait", "-w", help="Wait for completion.")
    ] = False,
) -> None:
    """Run a search-to-scrape discovery job."""
    payload = {
        "query": query,
        "search_pages": search_pages,
        "template_id": template,
        "max_pages_per_domain": max_pages,
        "data_types": [t.strip() for t in data_types.split(",")],
    }

    result = client.post("/discover/search", data=payload)
    discovery_id = result["discovery_id"]

    console.print(f"[green]Discovery job submitted:[/green] {discovery_id}")
    console.print(f"  Query: {query}")

    if wait:
        console.print()
        with console.status("[bold blue]Searching and scraping..."):
            while True:
                data = client.get(f"/discover/status/{discovery_id}")
                if data["status"] in ("completed", "failed"):
                    break
                time.sleep(3)
        output.print_discovery_status(data)
