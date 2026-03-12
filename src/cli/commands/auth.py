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
    email: Annotated[str, typer.Option("--email", "-e", prompt=True, help="Account email.")],
    password: Annotated[
        str,
        typer.Option("--password", "-p", prompt=True, hide_input=True, help="Password."),
    ],
    api_url: Annotated[
        str, typer.Option("--url", help="API server URL.")
    ] = "http://localhost:8000",
) -> None:
    """Login to LakeStream and save credentials."""
    try:
        with httpx.Client(base_url=api_url, timeout=10.0) as c:
            response = c.post(
                "/api/auth/login",
                json={"email": email, "password": password},
            )

        if response.status_code == 401:
            console.print("[red]Invalid credentials.[/red]")
            raise typer.Exit(1)

        data = response.json()
        token = data["access_token"]
        user = data["user"]

        profile = config.get_config().profile
        config.save_profile(profile, api_url=api_url, api_key=token)

        console.print(f"[green]Logged in as {user['email']}[/green] ({user['org_name']})")
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
