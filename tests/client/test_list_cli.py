from __future__ import annotations

import pytest
from typer.testing import CliRunner

from passwatcher import cli as cli_module
from passwatcher import render as render_module
from passwatcher.cli import app
from passwatcher.service import CredentialRecord


class FakeService:
    def __init__(self) -> None:
        self.all_records: list[CredentialRecord] = []

    def search(self, query: str) -> list[CredentialRecord]:
        raise AssertionError(f"list must not search for {query!r}")

    def list_all(self) -> list[CredentialRecord]:
        return self.all_records


class FakeClipboard:
    def copy(self, text: str) -> None:
        raise AssertionError(f"list must not copy {text!r}")


@pytest.fixture
def cli() -> CliRunner:
    return CliRunner()


@pytest.fixture
def service() -> FakeService:
    return FakeService()


@pytest.fixture(autouse=True)
def inject_dependencies(monkeypatch: pytest.MonkeyPatch, service: FakeService) -> None:
    monkeypatch.setattr(cli_module, "create_service", lambda _path: service)
    monkeypatch.setattr(cli_module, "create_clipboard", FakeClipboard)


def record(**overrides: object) -> CredentialRecord:
    values: dict[str, object] = {
        "id": 7,
        "service": "github.com",
        "label": "work",
        "username": "nika",
        "password": "secret",
        "notes": "",
        "created_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-12T00:00:00Z",
    }
    values.update(overrides)
    return CredentialRecord(**values)  # type: ignore[arg-type]


def test_list_hides_passwords_by_default(cli, service):
    service.all_records = [record(password="hidden-secret")]

    result = cli.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "hidden-secret" not in result.stdout


def test_list_secrets_reveals_passwords(cli, service):
    service.all_records = [record(password="visible-secret")]

    result = cli.invoke(app, ["list", "--secrets"])

    assert result.exit_code == 0
    assert "visible-secret" in result.stdout


def test_list_secrets_preserves_password_markup_literals(cli, service, monkeypatch):
    password = "[bold]visible-secret[/bold]"
    service.all_records = [record(password=password)]
    monkeypatch.setattr(render_module, "_color_output_available", lambda: True)

    result = cli.invoke(app, ["list", "--secrets"])

    assert result.exit_code == 0
    assert password in result.stdout
