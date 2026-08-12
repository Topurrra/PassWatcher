from __future__ import annotations

import pytest
from typer.testing import CliRunner

from passwatcher import cli as cli_module
from passwatcher import prompts
from passwatcher.cli import app
from passwatcher.service import CredentialRecord


class FakeService:
    def __init__(self) -> None:
        self.matches: list[CredentialRecord] = []
        self.created: CredentialRecord | None = None
        self.updated_id: int | None = None
        self.deleted_ids: list[int] = []

    def search(self, _query: str) -> list[CredentialRecord]:
        return self.matches

    def create(
        self, service: str, label: str, username: str, password: str, notes: str
    ) -> CredentialRecord:
        self.created = record(
            service=service, label=label, username=username, password=password, notes=notes
        )
        return self.created

    def update(
        self,
        credential_id: int,
        service: str,
        label: str,
        username: str,
        password: str,
        notes: str,
    ) -> CredentialRecord:
        self.updated_id = credential_id
        return record(
            id=credential_id,
            service=service,
            label=label,
            username=username,
            password=password,
            notes=notes,
        )

    def delete(self, credential_id: int) -> None:
        self.deleted_ids.append(credential_id)


class FakeClipboard:
    def __init__(self) -> None:
        self.values: list[str] = []

    def copy(self, value: str) -> None:
        self.values.append(value)


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


def test_add_prompts_and_creates(cli, service):
    """Catches add flows that collect values but never create a credential."""
    result = cli.invoke(
        app,
        ["add"],
        input="github.com\npersonal\nnika@example.com\ntyped-secret\nmain account\ny\n",
    )

    assert result.exit_code == 0
    assert service.created is not None
    assert service.created.password == "typed-secret"


def test_edit_many_requires_numbered_selection(cli, service):
    """Catches updating the first ambiguous result instead of the selected result."""
    service.matches = [record(id=3, label="personal"), record(id=9, label="work")]

    result = cli.invoke(app, ["edit", "github"], input="2\n\n\n\nnew-secret\n\ny\n")

    assert result.exit_code == 0
    assert service.updated_id == 9


def test_delete_cancellation_performs_no_write(cli, service):
    """Catches delete calls occurring before the user confirms them."""
    service.matches = [record(id=3)]

    result = cli.invoke(app, ["delete", "github"], input="n\n")

    assert result.exit_code == 0
    assert service.deleted_ids == []


def test_add_eof_cancellation_performs_no_write(cli, service):
    """Catches EOF being treated as approval or a partially completed write."""
    result = cli.invoke(app, ["add"], input="")

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout
    assert service.created is None


def test_add_keyboard_interrupt_performs_no_write(cli, service, monkeypatch):
    """Catches Ctrl+C escaping the prompt boundary and allowing later writes."""
    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(prompts.typer, "prompt", interrupt)

    result = cli.invoke(app, ["add"])

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout
    assert service.created is None


def test_generate_displays_and_copies_one_password(cli, clipboard):
    """Catches generated secrets that are not copied or are copied repeatedly."""
    result = cli.invoke(app, ["generate", "--length", "12"])

    assert result.exit_code == 0
    assert len(clipboard.values) == 1
    assert clipboard.values[0] in result.stdout
    assert len(clipboard.values[0]) == 12


def test_global_plain_option_precedes_generate_help(cli):
    """Catches root flags causing command help to be parsed as a lookup query."""
    result = cli.invoke(app, ["--plain", "generate", "--help"])

    assert result.exit_code == 0
    assert "--length" in result.stdout


def test_global_config_option_precedes_add(cli, service):
    """Catches root options with values preventing the CRUD command from running."""
    result = cli.invoke(
        app,
        [
            "--config",
            "test-config.toml",
            "add",
            "--service",
            "github.com",
            "--label",
            "personal",
            "--username",
            "nika@example.com",
            "--password",
            "typed-secret",
            "--notes",
            "main account",
        ],
        input="y\n",
    )

    assert result.exit_code == 0
    assert service.created is not None
    assert service.created.service == "github.com"
