"""Authentication commands: login, logout, whoami."""

from typing import Annotated

import httpx
import typer
from rich.console import Console

from src.cli import client, config

console = Console()

app = typer.Typer()


@app.command()
def login(
    api_key: Annotated[
        str,
        typer.Option(
            "--api-key", "-k", prompt="API key (ls_...)", hide_input=True,
            help="LakeStream API key — mint one under Settings → API Keys in the web UI.",
        ),
    ],
    api_url: Annotated[
        str, typer.Option("--url", help="API server URL.")
    ] = "http://localhost:8000",
) -> None:
    """Save an API key for CLI access (password login is retired)."""
    api_key = api_key.strip()
    if not api_key.startswith("ls_"):
        console.print(
            "[red]That doesn't look like a LakeStream API key (expected ls_ prefix).[/red]"
        )
        raise typer.Exit(1)

    try:
        with httpx.Client(base_url=api_url, timeout=10.0) as c:
            response = c.get("/api/auth/me", headers={"X-API-Key": api_key})

        if response.status_code == 401:
            console.print("[red]Invalid or revoked API key.[/red]")
            raise typer.Exit(1)
        if response.status_code >= 400:
            console.print(f"[red]Error {response.status_code}:[/red] {response.text}")
            raise typer.Exit(1)

        user = response.json()
        profile = config.get_config().profile
        config.save_profile(profile, api_url=api_url, api_key=api_key)

        org_name = user.get("org_name", "")
        console.print(f"[green]Authenticated as {user['email']}[/green] ({org_name})")
        console.print(f"Config saved to: {config.CONFIG_FILE}")

    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to {api_url}[/red]")
        raise typer.Exit(1)


@app.command()
def logout() -> None:
    """Clear saved credentials."""
    profile = config.get_config().profile
    config.save_profile(profile, api_url="http://localhost:8000", api_key="")
    console.print("[green]Logged out.[/green] Credentials cleared.")


@app.command()
def whoami() -> None:
    """Show current authenticated user."""
    data = client.get("/auth/me")
    console.print(f"Email:  {data['email']}")
    console.print(f"Name:   {data['full_name']}")
    console.print(f"Org:    {data['org_name']}")
    console.print(f"Role:   {data['role']}")
