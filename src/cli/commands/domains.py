"""Domain listing command."""

from typing import Annotated

import typer

from src.cli import client, output


def domains(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max domains to show.")] = 50,
    sort: Annotated[
        str,
        typer.Option("--sort", "-s", help="Sort by: last_scraped_at, domain, success_rate."),
    ] = "last_scraped_at",
) -> None:
    """List all scraped domains with stats."""
    data = client.get("/domains", limit=limit, sort_by=sort)
    if isinstance(data, list):
        output.print_domains_table(data)
    else:
        output.print_domains_table(data.get("domains", data))
