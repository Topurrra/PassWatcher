"""Persistent client connection configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
import tomllib

import tomli_w


class ConfigError(ValueError):
    """Raised when client configuration is invalid."""


@dataclass(frozen=True, slots=True)
class ClientConfig:
    host: str
    user: str
    port: int = 22
    identity_file: Path | None = None

    def __post_init__(self) -> None:
        if not self.host.strip() or not self.user.strip():
            raise ConfigError("host and user are required")
        if not 1 <= self.port <= 65535:
            raise ConfigError("port must be between 1 and 65535")


def load_config(path: Path) -> ClientConfig:
    """Load and validate a client configuration TOML document."""
    try:
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
        identity_file = data.get("identity_file")
        return ClientConfig(
            host=data["host"],
            user=data["user"],
            port=data.get("port", 22),
            identity_file=Path(identity_file) if identity_file is not None else None,
        )
    except (KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError("configuration is invalid") from error


def save_config(path: Path, config: ClientConfig) -> None:
    """Persist configuration without secrets, replacing the file atomically."""
    data: dict[str, object] = {
        "host": config.host,
        "user": config.user,
        "port": config.port,
    }
    if config.identity_file is not None:
        data["identity_file"] = str(config.identity_file)

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            temp_file.write(tomli_w.dumps(data))
        os.replace(temp_path, path)
    except OSError:
        if temp_path is not None:
            Path(temp_path).unlink(missing_ok=True)
        raise
