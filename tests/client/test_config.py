import os
from pathlib import Path
import sys

import pytest

from passwatcher.config import (
    AppConfig,
    BackendMode,
    ClientConfig,
    ConfigError,
    load_config,
    save_config,
)
from passwatcher.cli import default_config_path


@pytest.mark.skipif(sys.platform != "win32", reason="Windows config location contract")
def test_default_config_path_uses_roaming_appdata_on_windows():
    expected = Path(os.environ["APPDATA"]) / "Passwatcher" / "config.toml"

    assert default_config_path() == expected


def test_config_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    expected = AppConfig(
        BackendMode.REMOTE,
        ClientConfig("vault.example", "nika", 2222, Path("C:/keys/vault")),
    )

    save_config(path, expected)

    assert load_config(path) == expected
    assert "password" not in path.read_text(encoding="utf-8").lower()


def test_legacy_connection_config_loads_as_remote(tmp_path):
    """Catches an upgrade making existing remote-only installations unusable."""
    path = tmp_path / "config.toml"
    path.write_text('host="vault"\nuser="nika"\nport=22\n', encoding="utf-8")

    assert load_config(path) == AppConfig(
        BackendMode.REMOTE, ClientConfig("vault", "nika", 22)
    )


def test_local_config_round_trip_can_remember_remote_settings(tmp_path):
    """Catches local activation discarding values needed to switch back to remote."""
    path = tmp_path / "config.toml"
    expected = AppConfig(
        BackendMode.LOCAL,
        ClientConfig("vault", "nika", 2222, Path("C:/keys/vault")),
    )

    save_config(path, expected)

    assert load_config(path) == expected
    assert 'backend = "local"' in path.read_text(encoding="utf-8")


def test_remote_backend_requires_remote_settings():
    """Catches an active remote mode that cannot construct its transport."""
    with pytest.raises(ConfigError, match="remote settings"):
        AppConfig(BackendMode.REMOTE)


def test_config_rejects_invalid_port(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('host="vault"\nuser="nika"\nport=70000\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="port"):
        load_config(path)


@pytest.mark.parametrize("port", ["22.5", "true"])
def test_config_rejects_non_integer_port(tmp_path, port):
    path = tmp_path / "config.toml"
    path.write_text(f'host="vault"\nuser="nika"\nport={port!s}\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="port"):
        load_config(path)


@pytest.mark.parametrize(
    "contents",
    ['host=1\nuser="nika"\n', 'host="vault"\nuser=1\n'],
)
def test_config_rejects_non_string_required_fields(tmp_path, contents):
    path = tmp_path / "config.toml"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(ConfigError, match="host and user"):
        load_config(path)


def test_config_rejects_non_path_identity_file():
    with pytest.raises(ConfigError, match="identity_file"):
        ClientConfig("vault", "nika", identity_file="C:/keys/vault")
