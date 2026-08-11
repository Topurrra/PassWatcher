from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from passwatcher import cli as cli_module
from passwatcher import render as render_module
from passwatcher.cli import app
from passwatcher.config import ConfigError
from passwatcher.protocol import ProtocolError
from passwatcher.service import CredentialRecord


class FakeService:
    def __init__(self) -> None:
        self.matches: list[CredentialRecord] = []
        self.all_records: list[CredentialRecord] = []
        self.queries: list[str] = []

    def search(self, query: str) -> list[CredentialRecord]:
        self.queries.append(query)
        return self.matches

    def list_all(self) -> list[CredentialRecord]:
        return self.all_records


class FakeClipboard:
    def __init__(self) -> None:
        self.values: list[str] = []

    def copy(self, text: str) -> None:
        self.values.append(text)


@pytest.fixture
def cli() -> CliRunner:
    return CliRunner()


@pytest.fixture
def service() -> FakeService:
    return FakeService()


@pytest.fixture
def clipboard() -> FakeClipboard:
    return FakeClipboard()


@pytest.fixture(autouse=True)
def inject_dependencies(
    monkeypatch: pytest.MonkeyPatch, service: FakeService, clipboard: FakeClipboard
) -> None:
    monkeypatch.setattr(cli_module, "create_service", lambda _path: service)
    monkeypatch.setattr(cli_module, "create_clipboard", lambda: clipboard)


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


def test_one_match_shows_and_copies_password(cli, service, clipboard):
    service.matches = [record(password="only-secret")]

    result = cli.invoke(app, ["github"])

    assert result.exit_code == 0
    assert "only-secret" in result.stdout
    assert clipboard.values == ["only-secret"]


def test_many_matches_show_all_and_copy_nothing(cli, service, clipboard):
    service.matches = [
        record(label="personal", password="one"),
        record(label="work", password="two"),
    ]

    result = cli.invoke(app, ["github"])

    assert result.exit_code == 0
    assert "one" in result.stdout and "two" in result.stdout
    assert clipboard.values == []


def test_no_match_copies_nothing(cli, service, clipboard):
    service.matches = []

    result = cli.invoke(app, ["missing"])

    assert result.exit_code == 1
    assert "No credentials found" in result.stdout
    assert clipboard.values == []


def test_empty_query_shows_usage(cli):
    result = cli.invoke(app, [])

    assert result.exit_code == 2
    assert "Usage:" in result.output


@pytest.mark.parametrize("arguments", [["github"], ["list"]], ids=["lookup", "list"])
def test_configuration_error_is_rendered_for_each_entry_point(cli, monkeypatch, arguments):
    def fail_to_create_service(_path):
        raise ConfigError("password=configuration-secret")

    monkeypatch.setattr(cli_module, "create_service", fail_to_create_service)

    result = cli.invoke(app, arguments)

    assert result.exit_code == 1
    assert "Configuration problem" in result.stdout
    assert "configuration-secret" not in result.stdout


def test_debug_protocol_error_redacts_server_controlled_fields(cli, service, monkeypatch):
    def fail_search(_query):
        raise ProtocolError("password=code-secret", '{"password":"body-secret"}')

    monkeypatch.setattr(service, "search", fail_search)

    result = cli.invoke(app, ["--debug", "github"])

    assert result.exit_code == 1
    assert "ProtocolError" in result.stdout
    assert "code-secret" not in result.stdout
    assert "body-secret" not in result.stdout


def test_many_match_table_preserves_password_markup_literals(cli, service, monkeypatch):
    password = "[bold]one[/bold]"
    service.matches = [record(password=password), record(id=8, password="two")]
    monkeypatch.setattr(render_module, "_color_output_available", lambda: True)

    result = cli.invoke(app, ["github"])

    assert result.exit_code == 0
    assert password in result.stdout


def test_empty_no_color_environment_value_disables_color(monkeypatch):
    monkeypatch.setattr(render_module.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setenv("NO_COLOR", "")

    renderer = render_module.Renderer()

    assert renderer.plain is True
