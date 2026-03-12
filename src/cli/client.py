"""Thin httpx wrapper for LakeStream API calls."""

import httpx
import typer
from rich.console import Console

from src.cli.config import get_config

console = Console(stderr=True)


def _get_client() -> httpx.Client:
    cfg = get_config()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    return httpx.Client(base_url=cfg.api_url, headers=headers, timeout=30.0)


def api_request(method: str, path: str, **kwargs) -> dict:
    """Make an API request with uniform error handling."""
    try:
        with _get_client() as client:
            response = client.request(method, f"/api{path}", **kwargs)

        if response.status_code == 401:
            console.print("[red]Authentication failed.[/red] Run: lakestream auth login")
            raise typer.Exit(1)

        if response.status_code >= 400:
            detail = response.json().get("detail", response.text)
            console.print(f"[red]Error {response.status_code}:[/red] {detail}")
            raise typer.Exit(1)

        return response.json()

    except httpx.ConnectError:
        console.print(
            f"[red]Cannot connect to {get_config().api_url}[/red]\n"
            "Is the LakeStream server running? Try: make dev"
        )
        raise typer.Exit(1)


def get(path: str, **params) -> dict:
    return api_request("GET", path, params=params)


def post(path: str, data: dict | None = None) -> dict:
    return api_request("POST", path, json=data or {})


def download(path: str, **params) -> bytes:
    """Download raw bytes (for CSV/JSON file export)."""
    cfg = get_config()
    headers: dict[str, str] = {}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    try:
        with httpx.Client(base_url=cfg.api_url, headers=headers, timeout=60.0) as client:
            response = client.get(f"/api{path}", params=params)
            if response.status_code >= 400:
                console.print(f"[red]Export failed:[/red] {response.text}")
                raise typer.Exit(1)
            return response.content
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to {get_config().api_url}[/red]")
        raise typer.Exit(1)
