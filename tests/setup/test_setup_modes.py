from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import passwatcher.cli as cli_module
from passwatcher.cli import app
from passwatcher.config import AppConfig, BackendMode, ClientConfig, load_config, save_config
from passwatcher.service import CredentialDraft, CredentialRecord, ImportSummary
from passwatcher.setup import RemoteState, SetupResult


class FakeService:
    def __init__(self, records: list[CredentialRecord] | None = None) -> None:
        self.records = records or []
        self.imports: list[tuple[list[CredentialDraft], str]] = []

    def list_all(self) -> list[CredentialRecord]:
        return list(self.records)

    def health(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_count": len(self.records),
            "integrity_check": "ok",
        }

    def import_many(
        self, records: list[CredentialDraft], duplicates: str
    ) -> ImportSummary:
        self.imports.append((records, duplicates))
        return ImportSummary(len(records), len(records), 0, 0)


class FakeRunner:
    def __init__(self) -> None:
        self.connections: list[ClientConfig] = []

    def connectivity(self, config: ClientConfig) -> None:
        self.connections.append(config)


class FakeSetupManager:
    def __init__(self) -> None:
        self.runner = FakeRunner()

    def inspect(self, _config: ClientConfig) -> RemoteState:
        return RemoteState(True, 2, 1, True, modes_ok=True)

    def install_or_upgrade(
        self, _config: ClientConfig, _state: RemoteState
    ) -> SetupResult:
        return SetupResult(
            "reused",
            {
                "schema_version": 1,
                "record_count": 0,
                "integrity_check": "ok",
                "permissions_ok": True,
            },
        )


def credential(service: str, password: str = "secret") -> CredentialRecord:
    return CredentialRecord(
        1,
        service,
        "",
        "nika",
        password,
        "",
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00Z",
    )


def test_setup_without_mode_only_shows_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches the chooser prompting, connecting, or creating a vault."""
    config = tmp_path / "config.toml"
    monkeypatch.setattr(
        cli_module,
        "create_setup_manager",
        lambda: pytest.fail("setup manager constructed"),
    )
    monkeypatch.setattr(
        cli_module,
        "create_local_service",
        lambda: pytest.fail("local vault constructed"),
    )

    result = CliRunner().invoke(app, ["--config", str(config), "setup"])

    assert result.exit_code == 0
    assert "pw setup --local" in result.stdout
    assert "pw setup --remote" in result.stdout
    assert not config.exists()


def test_setup_rejects_both_modes_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches ambiguous setup choosing a backend by argument order."""
    monkeypatch.setattr(
        cli_module,
        "create_setup_manager",
        lambda: pytest.fail("setup manager constructed"),
    )

    result = CliRunner().invoke(
        app,
        [
            "--config",
            str(tmp_path / "config.toml"),
            "setup",
            "--local",
            "--remote",
        ],
    )

    assert result.exit_code != 0


def test_local_setup_health_checks_then_activates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches local activation requiring SSH or saving before health succeeds."""
    config = tmp_path / "config.toml"
    service = FakeService()
    monkeypatch.setattr(cli_module, "create_local_service", lambda: service)

    result = CliRunner().invoke(
        app, ["--config", str(config), "setup", "--local"]
    )

    assert result.exit_code == 0, result.stdout
    assert load_config(config) == AppConfig(BackendMode.LOCAL)


def test_switch_local_to_remote_migrates_then_activates_and_keeps_local_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches activation before migration or automatic deletion of the source vault."""
    config = tmp_path / "config.toml"
    remembered = ClientConfig("vault.example", "nika")
    save_config(config, AppConfig(BackendMode.LOCAL, remembered))
    local = FakeService([credential("github")])
    remote = FakeService()
    manager = FakeSetupManager()
    deleted: list[Path] = []
    monkeypatch.setattr(cli_module, "create_local_service", lambda: local)
    monkeypatch.setattr(
        cli_module, "create_remote_service", lambda _config: remote, raising=False
    )
    monkeypatch.setattr(cli_module, "create_setup_manager", lambda: manager)
    monkeypatch.setattr(
        cli_module,
        "delete_local_vault",
        lambda path: deleted.append(path),
        raising=False,
    )

    result = CliRunner().invoke(
        app,
        ["--config", str(config), "setup", "--remote"],
        input="\n\n\n\ny\nn\n",
    )

    assert result.exit_code == 0, result.stdout
    assert len(remote.imports) == 1
    assert remote.imports[0][0][0].service == "github"
    assert load_config(config).backend is BackendMode.REMOTE
    assert deleted == []


def test_cancelled_conflict_keeps_previous_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a cancelled migration changing active mode or destination data."""
    config = tmp_path / "config.toml"
    remembered = ClientConfig("vault.example", "nika")
    save_config(config, AppConfig(BackendMode.LOCAL, remembered))
    local = FakeService([credential("github", "local-secret")])
    remote = FakeService([credential("github", "remote-secret")])
    monkeypatch.setattr(cli_module, "create_local_service", lambda: local)
    monkeypatch.setattr(
        cli_module, "create_remote_service", lambda _config: remote, raising=False
    )
    monkeypatch.setattr(cli_module, "create_setup_manager", FakeSetupManager)

    result = CliRunner().invoke(
        app,
        ["--config", str(config), "setup", "--remote"],
        input="\n\n\n\ny\ncancel\n",
    )

    assert result.exit_code == 0, result.stdout
    assert remote.imports == []
    assert load_config(config).backend is BackendMode.LOCAL


def test_remote_to_local_migrates_without_deleting_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches switching to local failing to prefill from the active remote vault."""
    config = tmp_path / "config.toml"
    remote_config = ClientConfig("vault.example", "nika")
    save_config(config, AppConfig(BackendMode.REMOTE, remote_config))
    remote = FakeService([credential("github")])
    local = FakeService()
    monkeypatch.setattr(cli_module, "create_local_service", lambda: local)
    monkeypatch.setattr(
        cli_module, "create_remote_service", lambda _config: remote
    )

    result = CliRunner().invoke(
        app, ["--config", str(config), "setup", "--local"]
    )

    assert result.exit_code == 0, result.stdout
    assert len(local.imports) == 1
    assert local.imports[0][0][0].service == "github"
    assert load_config(config).backend is BackendMode.LOCAL


@pytest.mark.parametrize(
    ("answer", "expected_policy"),
    [("source", "update"), ("destination", None)],
)
def test_one_conflict_choice_controls_the_whole_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    expected_policy: str | None,
) -> None:
    """Catches conflict choices being ignored or requested per credential."""
    config = tmp_path / "config.toml"
    remembered = ClientConfig("vault.example", "nika")
    save_config(config, AppConfig(BackendMode.LOCAL, remembered))
    local = FakeService([credential("github", "local-secret")])
    remote = FakeService([credential("github", "remote-secret")])
    monkeypatch.setattr(cli_module, "create_local_service", lambda: local)
    monkeypatch.setattr(
        cli_module, "create_remote_service", lambda _config: remote
    )
    monkeypatch.setattr(cli_module, "create_setup_manager", FakeSetupManager)

    result = CliRunner().invoke(
        app,
        ["--config", str(config), "setup", "--remote"],
        input=f"\n\n\n\ny\n{answer}\nn\n",
    )

    assert result.exit_code == 0, result.stdout
    if expected_policy is None:
        assert remote.imports == []
    else:
        assert len(remote.imports) == 1
        assert remote.imports[0][1] == expected_policy
    assert load_config(config).backend is BackendMode.REMOTE


def test_remote_setup_detects_existing_local_vault_without_prior_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches an installer-era local vault being orphaned when config is missing."""
    config = tmp_path / "config.toml"
    local_path = tmp_path / "local" / "vault.db"
    local_path.parent.mkdir()
    local_path.write_bytes(b"sentinel")
    local = FakeService([credential("github")])
    remote = FakeService()
    monkeypatch.setattr(cli_module, "default_local_vault_path", lambda: local_path)
    monkeypatch.setattr(cli_module, "create_local_service", lambda: local)
    monkeypatch.setattr(
        cli_module, "create_remote_service", lambda _config: remote
    )
    monkeypatch.setattr(cli_module, "create_setup_manager", FakeSetupManager)

    result = CliRunner().invoke(
        app,
        ["--config", str(config), "setup", "--remote"],
        input="vault.example\nnika\n22\n\ny\nn\n",
    )

    assert result.exit_code == 0, result.stdout
    assert len(remote.imports) == 1
    assert load_config(config).backend is BackendMode.REMOTE
