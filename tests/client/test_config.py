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
