from __future__ import annotations

from pathlib import Path

import pytest

from passwatcher.csv_import import (
    CsvFormat,
    CsvImportError,
    DuplicatePolicy,
    parse_import,
    preview_import,
    validate_request_size,
)
from passwatcher.service import CredentialDraft, CredentialRecord


def record(**overrides: object) -> CredentialRecord:
    values: dict[str, object] = {
        "id": 7,
        "service": "github.com",
        "label": "work",
        "username": "nika",
        "password": "old",
        "notes": "",
        "created_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-12T00:00:00Z",
    }
    values.update(overrides)
    return CredentialRecord(**values)  # type: ignore[arg-type]


def write_csv(tmp_path: Path, text: str, name: str = "passwords.csv") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_parse_passwatcher_csv_preserves_optional_blanks(tmp_path: Path) -> None:
    """Catches optional CSV cells being treated as missing required values."""
    path = write_csv(
        tmp_path,
        "service,label,username,password,notes\n"
        "github.com,,nika,secret,\n",
    )

    parsed = parse_import(path)

    assert parsed.format is CsvFormat.PASSWATCHER
    assert parsed.records == (
        CredentialDraft("github.com", "", "nika", "secret", ""),
    )
    assert parsed.total_rows == 1
    assert parsed.ignored_columns == ()


def test_parse_browser_csv_maps_optional_columns_and_ignores_extras(
    tmp_path: Path,
) -> None:
    """Catches browser fields being stored under the wrong credential fields."""
    path = write_csv(
        tmp_path,
        "name,url,username,password,note,timeCreated\n"
        "Work,https://github.com,nika,secret,main,123\n",
    )

    parsed = parse_import(path)

    assert parsed.format is CsvFormat.BROWSER
    assert parsed.records == (
        CredentialDraft("https://github.com", "Work", "nika", "secret", "main"),
    )
    assert parsed.total_rows == 1
    assert parsed.ignored_columns == ("timeCreated",)


def test_parse_csv_handles_bom_case_quotes_unicode_and_newlines(tmp_path: Path) -> None:
    """Catches valid browser exports being split or decoded incorrectly."""
    path = tmp_path / "browser.csv"
    path.write_bytes(
        (
            "\ufeff NAME , URL , USERNAME , PASSWORD , NOTE \r\n"
            '"სამუშაო, main",https://example.com,nika,"p""ass","line one\nline two"\r\n'
        ).encode("utf-8")
    )

    parsed = parse_import(path)

    assert parsed.format is CsvFormat.BROWSER
    assert parsed.records == (
        CredentialDraft(
            "https://example.com",
            "სამუშაო, main",
            "nika",
            'p"ass',
            "line one\nline two",
        ),
    )
    assert parsed.total_rows == 1
    assert parsed.ignored_columns == ()


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "empty_csv"),
        ("service,label,username,password,notes\n", "empty_csv"),
        ("service,label,username,notes\ngithub.com,,nika,\n", "missing_columns"),
        (
            "service,SERVICE,username,password\ngithub.com,other,nika,secret\n",
            "duplicate_header",
        ),
        (
            "service,url,username,password\ngithub.com,https://github.com,nika,secret\n",
            "ambiguous_format",
        ),
        (
            "service,label,username,password,notes\ngithub.com,,nika,secret,,extra\n",
            "malformed_row",
        ),
        ("service,label,username,password,notes\n,,nika,secret,\n", "invalid_row"),
        (
            'service,label,username,password,notes\n"github.com,nika,secret,\n',
            "malformed_csv",
        ),
    ],
)
def test_parse_import_rejects_invalid_csv_without_values(
    tmp_path: Path, text: str, code: str
) -> None:
    """Catches malformed files reaching preview or exposing their cell values."""
    path = write_csv(tmp_path, text)

    with pytest.raises(CsvImportError) as raised:
        parse_import(path)

    assert raised.value.code == code
    assert "secret" not in str(raised.value)


def test_parse_import_rejects_invalid_path_and_utf8(tmp_path: Path) -> None:
    """Catches directories and undecodable files being treated as empty imports."""
    with pytest.raises(CsvImportError) as directory:
        parse_import(tmp_path)
    assert directory.value.code == "invalid_path"

    path = tmp_path / "invalid.csv"
    path.write_bytes(b"service,username,password\n\xff")
    with pytest.raises(CsvImportError) as encoding:
        parse_import(path)
    assert encoding.value.code == "invalid_encoding"


def test_parse_import_enforces_field_and_row_limits(tmp_path: Path) -> None:
    """Catches batches that can exceed server validation or protocol bounds."""
    oversized = write_csv(
        tmp_path,
        "service,username,password\n" + "github.com,nika," + "é" * 2049 + "\n",
        "oversized.csv",
    )
    with pytest.raises(CsvImportError) as field:
        parse_import(oversized)
    assert field.value.code == "invalid_row"
    assert field.value.issues[0].field == "password"

    too_many = write_csv(
        tmp_path,
        "service,username,password\n"
        + "".join(f"site-{index}.test,user,secret\n" for index in range(3001)),
        "too-many.csv",
    )
    with pytest.raises(CsvImportError) as rows:
        parse_import(too_many)
    assert rows.value.code == "too_many_rows"


def test_parse_import_counts_identical_duplicates_and_rejects_conflicts(
    tmp_path: Path,
) -> None:
    """Catches repeated identities being silently resolved by input order."""
    identical = write_csv(
        tmp_path,
        "service,label,username,password,notes\n"
        "github.com,work,nika,same,note\n"
        " github.com , work , nika ,same,note\n",
        "identical.csv",
    )
    parsed = parse_import(identical)
    assert parsed.total_rows == 2
    assert parsed.input_duplicates == 1
    assert len(parsed.records) == 2

    conflicting = write_csv(
        tmp_path,
        "service,label,username,password,notes\n"
        "github.com,work,nika,one-secret,note\n"
        "github.com,work,nika,two-secret,note\n",
        "conflicting.csv",
    )
    with pytest.raises(CsvImportError) as raised:
        parse_import(conflicting)
    assert raised.value.code == "duplicate_conflict"
    assert "one-secret" not in str(raised.value)
    assert "two-secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("policy", "inserted", "updated", "skipped"),
    [
        (DuplicatePolicy.SKIP, 0, 0, 1),
        (DuplicatePolicy.UPDATE, 0, 1, 0),
    ],
)
def test_preview_import_applies_existing_duplicate_policy(
    tmp_path: Path,
    policy: DuplicatePolicy,
    inserted: int,
    updated: int,
    skipped: int,
) -> None:
    """Catches preview counts disagreeing with the selected duplicate policy."""
    parsed = parse_import(
        write_csv(
            tmp_path,
            "service,label,username,password,notes\n"
            " GITHUB.COM , Work , NIKA ,new,note\n",
        )
    )

    preview = preview_import(parsed, [record()], policy)

    assert preview.format is CsvFormat.PASSWATCHER
    assert (preview.total, preview.inserted, preview.updated, preview.skipped) == (
        1,
        inserted,
        updated,
        skipped,
    )


def test_preview_import_rejects_duplicate_error_and_ambiguous_update(
    tmp_path: Path,
) -> None:
    """Catches preview guessing which existing identity should be changed."""
    parsed = parse_import(
        write_csv(
            tmp_path,
            "service,label,username,password,notes\ngithub.com,work,nika,new,note\n",
        )
    )
    with pytest.raises(CsvImportError) as existing:
        preview_import(parsed, [record()], DuplicatePolicy.ERROR)
    assert existing.value.code == "duplicate_conflict"

    ambiguous = [record(id=1), record(id=2, password="another")]
    with pytest.raises(CsvImportError) as update:
        preview_import(parsed, ambiguous, DuplicatePolicy.UPDATE)
    assert update.value.code == "duplicate_conflict"
    skipped = preview_import(parsed, ambiguous, DuplicatePolicy.SKIP)
    assert skipped.skipped == 1


def test_preview_counts_identical_input_duplicates_as_skipped(tmp_path: Path) -> None:
    """Catches preview totals omitting repeated but valid input rows."""
    parsed = parse_import(
        write_csv(
            tmp_path,
            "service,label,username,password,notes\n"
            "github.com,work,nika,same,note\n"
            "github.com,work,nika,same,note\n",
        )
    )

    preview = preview_import(parsed, [], DuplicatePolicy.SKIP)

    assert (preview.total, preview.inserted, preview.updated, preview.skipped) == (2, 1, 0, 1)


def test_validate_request_size_rejects_encoded_oversize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches confirmation occurring for a request the server must reject."""
    parsed = parse_import(
        write_csv(tmp_path, "service,username,password\ngithub.com,nika,secret\n")
    )
    monkeypatch.setattr("passwatcher.csv_import.MAX_REQUEST_BYTES", 10)

    with pytest.raises(CsvImportError) as raised:
        validate_request_size(parsed, DuplicatePolicy.SKIP)

    assert raised.value.code == "request_too_large"
