"""Rich output formatters for CLI display."""

from rich.console import Console
from rich.table import Table

console = Console()


def print_job_status(data: dict) -> None:
    """Pretty-print a scrape job status."""
    status_colors = {
        "pending": "yellow",
        "running": "blue",
        "completed": "green",
        "failed": "red",
    }
    color = status_colors.get(data.get("status", ""), "white")

    table = Table(title=f"Job {str(data['job_id'])[:8]}...")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Domain", data.get("domain", ""))
    table.add_row("Status", f"[{color}]{data.get('status', '')}[/{color}]")
    table.add_row("Strategy", data.get("strategy_used") or "pending")
    table.add_row("Pages Scraped", str(data.get("pages_scraped", 0)))
    table.add_row("Data Points", str(data.get("data_count", 0)))
    table.add_row("Cost", f"${data.get('cost_usd', 0) or 0:.4f}")

    if data.get("duration_ms"):
        table.add_row("Duration", f"{data['duration_ms']}ms")
    if data.get("error_message"):
        table.add_row("Error", f"[red]{data['error_message']}[/red]")

    table.add_row("Created", data.get("created_at", ""))

    console.print(table)


def print_domains_table(domains: list[dict]) -> None:
    """Pretty-print a domain list."""
    table = Table(title="Scraped Domains")
    table.add_column("Domain", style="cyan")
    table.add_column("Strategy")
    table.add_column("Success Rate")
    table.add_column("Avg Cost")
    table.add_column("Last Scraped")

    for d in domains:
        rate = f"{(d.get('success_rate') or 0) * 100:.0f}%" if d.get("success_rate") else "N/A"
        cost = f"${d.get('avg_cost_usd', 0):.4f}" if d.get("avg_cost_usd") else "N/A"
        table.add_row(
            d.get("domain", ""),
            d.get("last_successful_strategy") or "none",
            rate,
            cost,
            d.get("last_scraped_at") or "never",
        )

    console.print(table)


def print_discovery_status(data: dict) -> None:
    """Pretty-print discovery job status."""
    console.print(f"\n[bold]Discovery:[/bold] {data.get('query', '')}")
    console.print(
        f"Status: {data.get('status', '')} | Domains found: {data.get('domains_found', 0)}"
    )
    console.print(
        f"Scraped: {data.get('domains_scraped', 0)} | "
        f"Pending: {data.get('domains_pending', 0)} | "
        f"Skipped: {data.get('domains_skipped', 0)}"
    )
    console.print(f"Total cost: ${data.get('total_cost_usd', 0):.4f}\n")

    if data.get("child_jobs"):
        table = Table(title="Child Scrape Jobs")
        table.add_column("Domain", style="cyan")
        table.add_column("Status")
        table.add_column("Pages")
        table.add_column("Cost")

        for job in data["child_jobs"]:
            table.add_row(
                job.get("domain", ""),
                job.get("status", ""),
                str(job.get("pages_scraped", 0)),
                f"${job.get('cost_usd', 0):.4f}",
            )
        console.print(table)
