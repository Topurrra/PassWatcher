from __future__ import annotations

from pathlib import Path

import pytest

import passwatcher.cli as cli_module
from passwatcher.config import AppConfig, BackendMode, ClientConfig, save_config
from passwatcher.service import PasswordService


def test_create_service_selects_local_without_constructing_ssh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches local mode accidentally requiring SSH or a configured server."""
    config = tmp_path / "config.toml"
    save_config(config, AppConfig(BackendMode.LOCAL))
    local = object()
    monkeypatch.setattr(cli_module, "create_local_service", lambda: local, raising=False)
    monkeypatch.setattr(
        cli_module,
        "SshTransport",
        lambda _config: pytest.fail("SSH transport was constructed for local mode"),
    )

    assert cli_module.create_service(config) is local


def test_create_service_selects_remembered_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches remote mode ignoring the nested remembered connection settings."""
    config = tmp_path / "config.toml"
    remote = ClientConfig("vault", "nika")
    save_config(config, AppConfig(BackendMode.REMOTE, remote))
    seen: list[ClientConfig] = []
    transport = object()
    monkeypatch.setattr(
        cli_module, "SshTransport", lambda value: seen.append(value) or transport
    )

    service = cli_module.create_service(config)

    assert isinstance(service, PasswordService)
    assert seen == [remote]
