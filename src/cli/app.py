"""LakeStream CLI -- B2B web scraping from the command line."""

from typing import Annotated

import typer

from src.cli.commands import auth, browse, discover, domains, export, extract, scrape

app = typer.Typer(
    name="lakestream",
    help="LakeStream -- B2B web scraping and data extraction platform.",
    no_args_is_help=True,
)

# Register command groups and commands
app.add_typer(auth.app, name="auth", help="Login, logout, and manage credentials.")
app.command(name="scrape")(scrape.scrape)
app.command(name="status")(scrape.status)
app.command(name="extract")(extract.extract)
app.command(name="browse")(browse.browse)
app.command(name="discover")(discover.discover)
app.command(name="domains")(domains.domains)
app.command(name="export")(export.export)


@app.callback()
def main(
    profile: Annotated[
        str, typer.Option(help="Config profile to use.")
    ] = "default",
    api_url: Annotated[
        str | None, typer.Option("--api-url", help="Override API base URL.")
    ] = None,
    api_key: Annotated[
        str | None, typer.Option("--api-key", help="Override API key/JWT.")
    ] = None,
) -> None:
    """LakeStream CLI -- B2B web scraping and data extraction."""
    from src.cli import config

    config.set_active_profile(profile, api_url_override=api_url, api_key_override=api_key)


if __name__ == "__main__":
    app()
