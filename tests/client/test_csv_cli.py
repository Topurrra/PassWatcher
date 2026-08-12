from __future__ import annotations

import csv
from pathlib import Path

import pytest
from typer.testing import CliRunner

from passwatcher import cli as cli_module
from passwatcher.cli import app
from passwatcher.protocol import ProtocolError
from passwatcher.service import CredentialDraft, CredentialRecord, ImportSummary


def record(**overrides: object) -> CredentialRecord:
    values: dict[str, object] = {
        "id": 7,
        "service": "github.com",
        "label": "work",
        "username": "nika",
        "password": "hidden-secret",
        "notes": "",
        "created_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-12T00:00:00Z",
    }
    values.update(overrides)
    return CredentialRecord(**values)  # type: ignore[arg-type]


class FakeService:
    def __init__(self) -> None:
        self.records: list[CredentialRecord] = []
        self.imported: tuple[list[CredentialDraft], str] | None = None
        self.import_result = ImportSummary(total=1, inserted=1, updated=0, skipped=0)
        self.list_error: Exception | None = None
        self.import_error: Exception | None = None

    def list_all(self) -> list[CredentialRecord]:
        if self.list_error is not None:
            raise self.list_error
        return self.records

    def import_many(
        self, records: list[CredentialDraft], duplicates: str
    ) -> ImportSummary:
        if self.import_error is not None:
            raise self.import_error
        self.imported = (records, duplicates)
        return self.import_result


class FakeClipboard:
    def copy(self, _value: str) -> None:
        raise AssertionError("CSV commands must not use the clipboard")


@pytest.fixture
def cli() -> CliRunner:
    return CliRunner()


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    value = FakeService()
    monkeypatch.setattr(cli_module, "create_service", lambda _path: value)
    monkeypatch.setattr(cli_module, "create_clipboard", FakeClipboard)
    return value


def write_browser_csv(tmp_path: Path, password: str = "import-secret") -> Path:
    path = tmp_path / "browser.csv"
    with path.open("w", encoding="utf-8", newline="") as output:
        csv.writer(output, lineterminator="\n").writerows(
            [
                ["name", "url", "username", "password", "note"],
                ["work", "github.com", "nika", password, "main"],
            ]
        )
    return path


def test_import_dry_run_previews_without_mutation(
    cli: CliRunner, service: FakeService, tmp_path: Path
) -> None:
    """Catches dry-run issuing the bulk mutation or displaying a password."""
    path = write_browser_csv(tmp_path)

    result = cli.invoke(app, ["--plain", "import", str(path), "-n"])

    assert result.exit_code == 0, result.stdout
    assert "Import preview" in result.stdout
    assert "Format: browser" in result.stdout
    assert service.imported is None
    assert "import-secret" not in result.stdout


def test_import_yes_sends_one_batch_with_short_policy_flag(
    cli: CliRunner, service: FakeService, tmp_path: Path
) -> None:
    """Catches import sending one request per row or ignoring -d."""
    path = write_browser_csv(tmp_path)
    service.import_result = ImportSummary(total=1, inserted=0, updated=1, skipped=0)
    service.records = [record()]

    result = cli.invoke(app, ["--plain", "import", str(path), "-d", "update", "-y"])

    assert result.exit_code == 0, result.stdout
    assert service.imported is not None
    records, policy = service.imported
    assert records == [CredentialDraft("github.com", "work", "nika", "import-secret", "main")]
    assert policy == "update"
    assert "Import complete: 0 inserted, 1 updated, 0 skipped." in result.stdout
    assert "import-secret" not in result.stdout


def test_import_confirmation_decline_makes_no_mutation(
    cli: CliRunner, service: FakeService, tmp_path: Path
) -> None:
    """Catches import writing before explicit approval."""
    path = write_browser_csv(tmp_path)

    result = cli.invoke(app, ["import", str(path)], input="n\n")

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout
    assert service.imported is None


def test_import_validation_failure_is_secret_safe(
    cli: CliRunner, service: FakeService, tmp_path: Path
) -> None:
    """Catches invalid rows reaching SSH or appearing in errors."""
    path = write_browser_csv(tmp_path, password="")

    result = cli.invoke(app, ["--plain", "import", str(path), "-y"])

    assert result.exit_code == 1
    assert "Row 2" in result.stdout
    assert "password" in result.stdout
    assert service.imported is None


@pytest.mark.parametrize("code", ["incompatible_protocol", "unknown_operation"])
def test_import_older_server_error_instructs_setup(
    cli: CliRunner, service: FakeService, tmp_path: Path, code: str
) -> None:
    """Catches older-server failures becoming generic protocol errors."""
    path = write_browser_csv(tmp_path)
    service.import_error = ProtocolError(code, "old server detail")

    result = cli.invoke(app, ["--plain", "import", str(path), "-y"])

    assert result.exit_code == 1
    assert "pw setup" in result.stdout
    assert "old server detail" not in result.stdout


def test_import_duplicate_error_happens_before_confirmation(
    cli: CliRunner, service: FakeService, tmp_path: Path
) -> None:
    """Catches duplicate-error mode prompting despite an impossible import."""
    path = write_browser_csv(tmp_path)
    service.records = [record()]

    result = cli.invoke(app, ["--plain", "import", str(path), "-d", "error"])

    assert result.exit_code == 1
    assert "duplicate" in result.stdout.lower()
    assert "Import credentials?" not in result.stdout
    assert service.imported is None


def test_export_browser_short_flags_write_expected_file(
    cli: CliRunner, service: FakeService, tmp_path: Path
) -> None:
    """Catches -t/-y being ignored or secrets leaking to output."""
    service.records = [record()]
    path = tmp_path / "browser-export.csv"

    result = cli.invoke(app, ["--plain", "export", str(path), "-t", "browser", "-y"])

    assert result.exit_code == 0, result.stdout
    with path.open(encoding="utf-8", newline="") as exported:
        assert list(csv.reader(exported)) == [
            ["name", "url", "username", "password", "note"],
            ["work", "github.com", "nika", "hidden-secret", ""],
        ]
    assert "hidden-secret" not in result.stdout
    assert "Export complete" in result.stdout


def test_export_decline_creates_no_file_or_service_read(
    cli: CliRunner, service: FakeService, tmp_path: Path
) -> None:
    """Catches export retrieving secrets or creating a temp file before approval."""
    service.records = [record()]
    service.list_error = AssertionError("list_all must not run")
    path = tmp_path / "passwords.csv"

    result = cli.invoke(app, ["export", str(path)], input="n\n")

    assert result.exit_code == 0
    assert "Cancelled" in result.stdout
    assert not path.exists()
    assert list(tmp_path.glob(".passwords.csv.*.tmp")) == []


def test_export_refuses_existing_file_before_retrieving_secrets(
    cli: CliRunner, service: FakeService, tmp_path: Path
) -> None:
    """Catches overwrite refusal occurring only after a vault read."""
    path = tmp_path / "passwords.csv"
    path.write_text("original", encoding="utf-8")
    service.list_error = AssertionError("list_all must not run")

    result = cli.invoke(app, ["--plain", "export", str(path), "-y"])

    assert result.exit_code == 1
    assert path.read_text(encoding="utf-8") == "original"


def test_export_force_replaces_existing_file(
    cli: CliRunner, service: FakeService, tmp_path: Path
) -> None:
    """Catches -f failing to authorize the already-confirmed replacement."""
    path = tmp_path / "passwords.csv"
    path.write_text("original", encoding="utf-8")
    service.records = [record()]

    result = cli.invoke(app, ["export", str(path), "-f", "-y"])

    assert result.exit_code == 0
    assert "original" not in path.read_text(encoding="utf-8")


def test_export_service_error_creates_no_destination(
    cli: CliRunner, service: FakeService, tmp_path: Path
) -> None:
    """Catches vault read failure leaving an empty plaintext file."""
    service.list_error = ProtocolError("database_error", "secret server detail")
    path = tmp_path / "passwords.csv"

    result = cli.invoke(app, ["--plain", "export", str(path), "-y"])

    assert result.exit_code == 1
    assert not path.exists()
    assert "secret server detail" not in result.stdout


def test_csv_help_shows_short_and_long_flags(cli: CliRunner, service: FakeService) -> None:
    """Catches approved concise aliases disappearing from command help."""
    imported = cli.invoke(app, ["import", "--help"], env={"NO_COLOR": "1"})
    exported = cli.invoke(app, ["export", "--help"], env={"NO_COLOR": "1"})

    assert imported.exit_code == exported.exit_code == 0
    assert "-n" in imported.stdout and "--dry-run" in imported.stdout
    assert "-d" in imported.stdout and "--duplicates" in imported.stdout
    assert "-t" in exported.stdout and "--format" in exported.stdout
    assert "-f" in exported.stdout and "--force" in exported.stdout
    assert "-y" in imported.stdout and "--yes" in imported.stdout


def test_global_options_route_to_csv_commands(
    cli: CliRunner, service: FakeService, tmp_path: Path
) -> None:
    """Catches the default lookup callback consuming CSV commands as queries."""
    imported = cli.invoke(
        app,
        ["--plain", "import", str(write_browser_csv(tmp_path)), "-n"],
    )
    service.records = [record()]
    exported = cli.invoke(
        app,
        [
            "--config",
            str(tmp_path / "config.toml"),
            "export",
            str(tmp_path / "export.csv"),
            "-y",
        ],
    )

    assert imported.exit_code == 0, imported.stdout
    assert exported.exit_code == 0, exported.stdout
