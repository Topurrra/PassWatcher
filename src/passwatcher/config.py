"""Persistent client connection configuration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import os
import tempfile
import tomllib

import tomli_w


class ConfigError(ValueError):
    """Raised when client configuration is invalid."""


class BackendMode(str, Enum):
    """Persisted vault backend selected by the last successful setup."""

    LOCAL = "local"
    REMOTE = "remote"


@dataclass(frozen=True, slots=True)
class ClientConfig:
    host: str
    user: str
    port: int = 22
    identity_file: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not isinstance(self.user, str):
            raise ConfigError("host and user must be strings")
        if not self.host.strip() or not self.user.strip():
            raise ConfigError("host and user are required")
        if type(self.port) is not int:
            raise ConfigError("port must be an integer")
        if not 1 <= self.port <= 65535:
            raise ConfigError("port must be between 1 and 65535")
        if self.identity_file is not None and not isinstance(self.identity_file, Path):
            raise ConfigError("identity_file must be a Path or None")


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Application mode plus optional remembered remote connection settings."""

    backend: BackendMode
    remote: ClientConfig | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.backend, BackendMode):
            raise ConfigError("backend must be local or remote")
        if self.remote is not None and not isinstance(self.remote, ClientConfig):
            raise ConfigError("remote settings are invalid")
        if self.backend is BackendMode.REMOTE and self.remote is None:
            raise ConfigError("remote settings are required for the remote backend")


def load_config(path: Path) -> AppConfig:
    """Load and validate a client configuration TOML document."""
    try:
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
        if "backend" not in data:
            return AppConfig(BackendMode.REMOTE, _remote_config(data))

        if set(data) - {"backend", "remote"}:
            raise ConfigError("configuration contains unknown fields")
        try:
            backend = BackendMode(data["backend"])
        except (ValueError, TypeError):
            raise ConfigError("backend must be local or remote") from None
        remote_data = data.get("remote")
        remote = None if remote_data is None else _remote_config(remote_data)
        return AppConfig(backend, remote)
    except ConfigError:
        raise
    except (KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError("configuration is invalid") from error


def save_config(path: Path, config: AppConfig) -> None:
    """Persist configuration without secrets, replacing the file atomically."""
    data: dict[str, object] = {"backend": config.backend.value}
    if config.remote is not None:
        remote: dict[str, object] = {
            "host": config.remote.host,
            "user": config.remote.user,
            "port": config.remote.port,
        }
        if config.remote.identity_file is not None:
            remote["identity_file"] = str(config.remote.identity_file)
        data["remote"] = remote

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


def _remote_config(data: object) -> ClientConfig:
    """Parse one exact remote connection table or legacy root document."""
    if not isinstance(data, dict) or set(data) - {"host", "user", "port", "identity_file"}:
        raise ConfigError("remote settings are invalid")
    identity_file = data.get("identity_file")
    return ClientConfig(
        host=data["host"],
        user=data["user"],
        port=data.get("port", 22),
        identity_file=Path(identity_file) if identity_file is not None else None,
    )
