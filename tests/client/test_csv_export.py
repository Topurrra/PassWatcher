from __future__ import annotations

import csv
from pathlib import Path

import pytest

from passwatcher import csv_export
from passwatcher.csv_export import (
    CsvExportError,
    export_records,
    validate_export_destination,
)
from passwatcher.csv_import import CsvFormat, parse_import
from passwatcher.service import CredentialDraft, CredentialRecord


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


def read_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as exported:
        return list(csv.reader(exported))


def test_passwatcher_export_uses_lossless_schema(tmp_path: Path) -> None:
    """Catches exports rewriting formula-like secrets or multiline notes."""
    path = tmp_path / "passwords.csv"

    count = export_records(
        path,
        [record(password="=SUM(1,2)", notes="line one\nline two")],
        CsvFormat.PASSWATCHER,
        force=False,
    )

    assert count == 1
    assert read_rows(path) == [
        ["service", "label", "username", "password", "notes"],
        ["github.com", "work", "nika", "=SUM(1,2)", "line one\nline two"],
    ]


def test_browser_export_maps_exact_columns(tmp_path: Path) -> None:
    """Catches label/service/notes being emitted under wrong browser headers."""
    path = tmp_path / "browser.csv"

    export_records(path, [record()], CsvFormat.BROWSER, force=False)

    assert read_rows(path) == [
        ["name", "url", "username", "password", "note"],
        ["work", "github.com", "nika", "secret", ""],
    ]


def test_passwatcher_export_round_trips_every_mutable_field(tmp_path: Path) -> None:
    """Catches CSV serialization losing quotes, Unicode, commas, or line breaks."""
    path = tmp_path / "round-trip.csv"
    records = [
        record(
            service="exämple.com",
            label='work, "main"',
            username="ნიკა",
            password='="secret",\r\nnext',
            notes="line one\nline two",
        ),
        record(id=8, service="blank.test", label="", username="user", password="p", notes=""),
    ]

    export_records(path, records, CsvFormat.PASSWATCHER, force=False)
    parsed = parse_import(path)

    assert parsed.records == (
        CredentialDraft(
            "exämple.com",
            'work, "main"',
            "ნიკა",
            '="secret",\r\nnext',
            "line one\nline two",
        ),
        CredentialDraft("blank.test", "", "user", "p", ""),
    )


def test_export_refuses_existing_file_without_force(tmp_path: Path) -> None:
    """Catches a default export overwriting an existing plaintext file."""
    path = tmp_path / "passwords.csv"
    path.write_text("original", encoding="utf-8")

    with pytest.raises(CsvExportError) as raised:
        export_records(path, [record()], CsvFormat.PASSWATCHER, force=False)

    assert raised.value.code == "destination_exists"
    assert path.read_text(encoding="utf-8") == "original"


def test_export_force_atomically_replaces_existing_file(tmp_path: Path) -> None:
    """Catches --force appending to or retaining an old destination."""
    path = tmp_path / "passwords.csv"
    path.write_text("original", encoding="utf-8")

    export_records(path, [record()], CsvFormat.PASSWATCHER, force=True)

    assert read_rows(path)[0] == ["service", "label", "username", "password", "notes"]
    assert "original" not in path.read_text(encoding="utf-8")


def test_export_rejects_non_regular_destinations(tmp_path: Path) -> None:
    """Catches --force replacing a directory or following a destination symlink."""
    with pytest.raises(CsvExportError) as directory:
        validate_export_destination(tmp_path, force=True)
    assert directory.value.code == "unsafe_destination"

    target = tmp_path / "target.csv"
    target.write_text("target", encoding="utf-8")
    link = tmp_path / "link.csv"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable for this Windows user")
    with pytest.raises(CsvExportError) as symlink:
        validate_export_destination(link, force=True)
    assert symlink.value.code == "unsafe_destination"
    assert target.read_text(encoding="utf-8") == "target"


def test_failed_export_preserves_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a partial export replacing the last good destination."""
    path = tmp_path / "passwords.csv"
    path.write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        csv_export,
        "_write_rows",
        lambda *_args: (_ for _ in ()).throw(OSError("secret detail")),
    )

    with pytest.raises(CsvExportError) as raised:
        export_records(path, [record(password="hidden-secret")], CsvFormat.PASSWATCHER, force=True)

    assert raised.value.code == "write_failed"
    assert "hidden-secret" not in str(raised.value)
    assert "secret detail" not in str(raised.value)
    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(".passwords.csv.*.tmp")) == []


def test_export_reports_unwritable_parent_without_creating_files(tmp_path: Path) -> None:
    """Catches missing destination parents surfacing raw filesystem errors."""
    path = tmp_path / "missing" / "passwords.csv"

    with pytest.raises(CsvExportError) as raised:
        export_records(path, [record()], CsvFormat.PASSWATCHER, force=False)

    assert raised.value.code == "invalid_destination"
    assert not path.exists()
