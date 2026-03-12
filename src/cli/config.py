"""CLI configuration -- reads/writes ~/.config/lakestream/config.toml."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "lakestream"
CONFIG_FILE = CONFIG_DIR / "config.toml"


@dataclass
class CLIConfig:
    api_url: str = "http://localhost:8000"
    api_key: str = ""
    profile: str = "default"


_active: CLIConfig = CLIConfig()


def set_active_profile(
    profile: str = "default",
    api_url_override: str | None = None,
    api_key_override: str | None = None,
) -> None:
    """Load config from file, then apply env vars, then CLI overrides."""
    global _active
    _active = CLIConfig(profile=profile)

    # Layer 1: Config file
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "rb") as f:
            data = tomllib.load(f)
        section = data.get(profile, {})
        _active.api_url = section.get("api_url", _active.api_url)
        _active.api_key = section.get("api_key", _active.api_key)

    # Layer 2: Environment variables
    env_key = os.environ.get("LAKESTREAM_API_KEY")
    env_url = os.environ.get("LAKESTREAM_API_URL")
    if env_key:
        _active.api_key = env_key
    if env_url:
        _active.api_url = env_url

    # Layer 3: CLI flags (highest priority)
    if api_url_override:
        _active.api_url = api_url_override
    if api_key_override:
        _active.api_key = api_key_override


def get_config() -> CLIConfig:
    return _active


def save_profile(profile: str, api_url: str, api_key: str) -> None:
    """Write a profile to the config file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Read existing config
    data: dict = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "rb") as f:
            data = tomllib.load(f)

    # Update profile section
    data[profile] = {"api_url": api_url, "api_key": api_key}

    # Write back (tomllib is read-only, write manually)
    lines = []
    for section, values in data.items():
        lines.append(f"[{section}]")
        for k, v in values.items():
            lines.append(f'{k} = "{v}"')
        lines.append("")

    CONFIG_FILE.write_text("\n".join(lines))
