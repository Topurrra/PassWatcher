from pathlib import Path

import pytest

from passwatcher.config import ClientConfig, ConfigError, load_config, save_config


def test_config_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    expected = ClientConfig("vault.example", "nika", 2222, Path("C:/keys/vault"))

    save_config(path, expected)

    assert load_config(path) == expected
    assert "password" not in path.read_text(encoding="utf-8").lower()


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
