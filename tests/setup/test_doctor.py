from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from passwatcher import cli as cli_module
from passwatcher.cli import app
from passwatcher.config import ClientConfig, load_config, save_config
from passwatcher.setup import Doctor, RemoteState, SetupError, SetupResult


class FakeSetupRunner:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.inspect_failure: SetupError | None = None

    def connectivity(self, _config: ClientConfig) -> None:
        self.events.append("connectivity")

    def inspect(self, _config: ClientConfig) -> RemoteState:
        self.events.append("inspect")
        if self.inspect_failure is not None:
            raise self.inspect_failure
        return RemoteState(
            True,
            1,
            1,
            True,
            (3, 12, 0),
            True,
            "ok",
            True,
        )

    def __getattr__(self, name: str) -> object:
        if name in {
            "prepare_directories",
            "upload_bundle",
            "verify_bundle",
            "backup",
            "install_bundle",
            "initialize",
            "health",
        }:
            raise AssertionError(f"doctor attempted mutating operation: {name}")
        raise AttributeError(name)


class FakeSetupManager:
    def __init__(self) -> None:
        self.runner = FakeSetupRunner()
        self.failure: SetupError | None = None
        self.installs = 0

    def inspect(self, config: ClientConfig) -> RemoteState:
        if self.failure is not None:
            raise self.failure
        return self.runner.inspect(config)

    def install_or_upgrade(
        self, _config: ClientConfig, _state: RemoteState
    ) -> SetupResult:
        self.installs += 1
        if self.failure is not None:
            raise self.failure
        return SetupResult("reused", {"schema_version": 1, "integrity_check": "ok"})


class FakeDoctor:
    def __init__(self) -> None:
        self.checks = [
            ("OpenSSH", True, "available"),
            ("SQLite integrity", True, "ok"),
        ]

    def run(self, _config_path: Path) -> list[tuple[str, bool, str]]:
        return self.checks


@pytest.fixture
def cli() -> CliRunner:
    return CliRunner()


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


@pytest.fixture
def setup_manager(monkeypatch: pytest.MonkeyPatch) -> FakeSetupManager:
    manager = FakeSetupManager()
    monkeypatch.setattr(cli_module, "create_setup_manager", lambda: manager, raising=False)
    return manager


@pytest.fixture
def doctor(monkeypatch: pytest.MonkeyPatch) -> FakeDoctor:
    value = FakeDoctor()
    monkeypatch.setattr(cli_module, "create_doctor", lambda: value, raising=False)
    return value


def test_setup_saves_config_only_after_remote_success(
    cli: CliRunner,
    setup_manager: FakeSetupManager,
    config_path: Path,
) -> None:
    """Catches failed remote setup leaving a usable-looking local config behind."""
    setup_manager.failure = SetupError("connect_failed", "SSH connection failed")

    result = cli.invoke(
        app,
        ["--config", str(config_path), "setup"],
        input="vault.example\nnika\n22\nC:/keys/vault\ny\n",
    )

    assert result.exit_code == 1
    assert not config_path.exists()


def test_setup_persists_only_connection_fields_after_confirmed_health(
    cli: CliRunner,
    setup_manager: FakeSetupManager,
    config_path: Path,
) -> None:
    """Catches setup saving secrets or skipping the healthy remote workflow."""
    result = cli.invoke(
        app,
        ["--config", str(config_path), "setup"],
        input="vault.example\nnika\n2222\nC:/keys/vault\ny\n",
    )

    assert result.exit_code == 0, result.stdout
    assert setup_manager.installs == 1
    assert load_config(config_path) == ClientConfig(
        "vault.example", "nika", 2222, Path("C:/keys/vault")
    )
    assert "password" not in config_path.read_text(encoding="utf-8").lower()


def test_setup_accepts_empty_optional_identity_file(
    cli: CliRunner,
    setup_manager: FakeSetupManager,
    config_path: Path,
) -> None:
    """Catches an optional identity prompt consuming the confirmation as a value."""
    result = cli.invoke(
        app,
        ["--config", str(config_path), "setup"],
        input="vault.example\nnika\n22\n\ny\n",
    )

    assert result.exit_code == 0, result.stdout
    assert setup_manager.installs == 1
    assert load_config(config_path).identity_file is None


def test_setup_confirmation_precedes_any_remote_connection(
    cli: CliRunner,
    setup_manager: FakeSetupManager,
    config_path: Path,
) -> None:
    """Catches an unconfirmed or mistyped target receiving an outbound SSH connection."""
    result = cli.invoke(
        app,
        ["--config", str(config_path), "setup"],
        input="vault.example\nnika\n22\n\nn\n",
    )

    assert result.exit_code == 0
    assert setup_manager.runner.events == []
    assert not config_path.exists()


def test_doctor_reports_all_checks_without_secrets(
    cli: CliRunner, doctor: FakeDoctor, config_path: Path
) -> None:
    """Catches doctor hiding checks or printing credential-shaped secret fields."""
    result = cli.invoke(app, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 0
    assert "OpenSSH" in result.stdout and "SQLite integrity" in result.stdout
    assert "password" not in result.stdout.lower()


def test_doctor_returns_failure_after_reporting_every_check(
    cli: CliRunner, doctor: FakeDoctor, config_path: Path
) -> None:
    """Catches doctor stopping at the first failure instead of reporting the full picture."""
    doctor.checks = [
        ("OpenSSH", False, "missing"),
        ("SQLite integrity", False, "unavailable"),
    ]

    result = cli.invoke(app, ["--config", str(config_path), "doctor"])

    assert result.exit_code == 1
    assert "OpenSSH" in result.stdout and "SQLite integrity" in result.stdout


def test_production_doctor_uses_only_non_mutating_runner_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches diagnostics that repair permissions, initialize, or replace remote files."""
    config_path = tmp_path / "config.toml"
    save_config(config_path, ClientConfig("vault.example", "nika"))
    runner = FakeSetupRunner()
    monkeypatch.setattr("passwatcher.setup.shutil.which", lambda _name: "available")

    checks = Doctor(runner).run(config_path)  # type: ignore[arg-type]

    assert runner.events == ["connectivity", "inspect"]
    assert all(passed for _name, passed, _detail in checks)


def test_doctor_does_not_report_contradictory_connectivity_when_inspection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches successful SSH followed by inspect failure producing PASS and FAIL connectivity."""
    config_path = tmp_path / "config.toml"
    save_config(config_path, ClientConfig("vault.example", "nika"))
    runner = FakeSetupRunner()
    runner.inspect_failure = SetupError("inspect_failed", "inspection failed")
    monkeypatch.setattr("passwatcher.setup.shutil.which", lambda _name: "available")

    checks = Doctor(runner).run(config_path)  # type: ignore[arg-type]

    connectivity = [check for check in checks if check[0] == "Connectivity"]
    assert connectivity == [("Connectivity", True, "connected")]
    assert ("Remote inspection", False, "inspect_failed") in checks
