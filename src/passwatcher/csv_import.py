"""Pure CSV import parsing, validation, and non-mutating preview logic."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from collections.abc import Sequence

from .protocol import MAX_REQUEST_BYTES, make_request
from .service import CredentialDraft, CredentialRecord


MAX_FIELD_BYTES = 4096
MAX_IMPORT_ROWS = 3000

_PASSWATCHER_REQUIRED = frozenset({"service", "username", "password"})
_PASSWATCHER_OPTIONAL = frozenset({"label", "notes"})
_BROWSER_REQUIRED = frozenset({"url", "username", "password"})
_BROWSER_OPTIONAL = frozenset({"name", "note"})


class CsvFormat(str, Enum):
    PASSWATCHER = "passwatcher"
    BROWSER = "browser"


class DuplicatePolicy(str, Enum):
    SKIP = "skip"
    UPDATE = "update"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CsvIssue:
    row: int | None
    field: str
    message: str


class CsvImportError(ValueError):
    """A safe CSV failure that never contains credential cell values."""

    def __init__(
        self,
        code: str,
        message: str,
        issues: tuple[CsvIssue, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.issues = issues


@dataclass(frozen=True, slots=True)
class ParsedImport:
    format: CsvFormat
    records: tuple[CredentialDraft, ...]
    total_rows: int
    ignored_columns: tuple[str, ...]
    input_duplicates: int


@dataclass(frozen=True, slots=True)
class ImportPreview:
    format: CsvFormat
    total: int
    inserted: int
    updated: int
    skipped: int
    ignored_columns: tuple[str, ...]


def parse_import(path: Path) -> ParsedImport:
    """Parse and validate one local PassWatcher or browser CSV file."""
    path = Path(path)
    if not path.is_file():
        raise CsvImportError("invalid_path", "The import path must be a regular file")

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.reader(csv_file, strict=True)
            try:
                raw_headers = next(reader)
            except StopIteration:
                raise CsvImportError("empty_csv", "The CSV file is empty") from None
            headers = [header.strip().casefold() for header in raw_headers]
            if any(not header for header in headers):
                raise CsvImportError("invalid_header", "CSV headers must not be empty")
            if len(set(headers)) != len(headers):
                raise CsvImportError("duplicate_header", "CSV headers must be unique")

            csv_format = _detect_format(frozenset(headers))
            known = _known_columns(csv_format)
            ignored = tuple(
                raw.strip() for raw, normalized in zip(raw_headers, headers) if normalized not in known
            )
            indexes = {header: index for index, header in enumerate(headers)}

            records: list[CredentialDraft] = []
            seen: dict[tuple[str, str, str], CredentialDraft] = {}
            input_duplicates = 0
            for row_number, row in enumerate(reader, start=2):
                if len(records) >= MAX_IMPORT_ROWS:
                    raise CsvImportError(
                        "too_many_rows",
                        f"The CSV file contains more than {MAX_IMPORT_ROWS} data rows",
                    )
                if len(row) != len(headers):
                    raise CsvImportError(
                        "malformed_row",
                        "A CSV row has the wrong number of columns",
                        (CsvIssue(row_number, "row", "Wrong number of columns"),),
                    )
                draft = _draft_from_row(row, indexes, csv_format, row_number)
                identity = _identity(draft.service, draft.label, draft.username)
                previous = seen.get(identity)
                if previous is not None:
                    if previous != draft:
                        raise CsvImportError(
                            "duplicate_conflict",
                            "The CSV contains conflicting rows for one credential identity",
                            (CsvIssue(row_number, "identity", "Conflicts with an earlier row"),),
                        )
                    input_duplicates += 1
                else:
                    seen[identity] = draft
                records.append(draft)
    except CsvImportError:
        raise
    except UnicodeDecodeError:
        raise CsvImportError("invalid_encoding", "The CSV file must use UTF-8 encoding") from None
    except csv.Error:
        raise CsvImportError("malformed_csv", "The CSV structure is invalid") from None
    except OSError:
        raise CsvImportError("unreadable_csv", "The CSV file could not be read") from None

    if not records:
        raise CsvImportError("empty_csv", "The CSV file has no data rows")
    return ParsedImport(
        format=csv_format,
        records=tuple(records),
        total_rows=len(records),
        ignored_columns=ignored,
        input_duplicates=input_duplicates,
    )


def preview_import(
    parsed: ParsedImport,
    existing: Sequence[CredentialRecord],
    policy: DuplicatePolicy,
) -> ImportPreview:
    """Calculate non-secret import counts without changing either data source."""
    existing_by_identity: dict[tuple[str, str, str], list[CredentialRecord]] = {}
    for record in existing:
        identity = _identity(record.service, record.label, record.username)
        existing_by_identity.setdefault(identity, []).append(record)

    seen: set[tuple[str, str, str]] = set()
    inserted = 0
    updated = 0
    skipped = 0
    for draft in parsed.records:
        identity = _identity(draft.service, draft.label, draft.username)
        if identity in seen:
            skipped += 1
            continue
        seen.add(identity)
        matches = existing_by_identity.get(identity, [])
        if not matches:
            inserted += 1
        elif policy is DuplicatePolicy.SKIP:
            skipped += 1
        elif policy is DuplicatePolicy.UPDATE and len(matches) == 1:
            updated += 1
        else:
            raise CsvImportError(
                "duplicate_conflict",
                "The import matches an existing duplicate credential identity",
            )

    return ImportPreview(
        format=parsed.format,
        total=parsed.total_rows,
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        ignored_columns=parsed.ignored_columns,
    )


def validate_request_size(parsed: ParsedImport, policy: DuplicatePolicy) -> None:
    """Reject an import that cannot fit the server's bounded RPC request."""
    raw = make_request(
        "import",
        {
            "records": [asdict(record) for record in parsed.records],
            "duplicates": policy.value,
        },
    )
    if len(raw) > MAX_REQUEST_BYTES:
        raise CsvImportError(
            "request_too_large",
            f"The encoded import request exceeds {MAX_REQUEST_BYTES} bytes",
        )


def _detect_format(headers: frozenset[str]) -> CsvFormat:
    passwatcher = _PASSWATCHER_REQUIRED <= headers
    browser = _BROWSER_REQUIRED <= headers
    if passwatcher and browser:
        raise CsvImportError(
            "ambiguous_format", "The CSV headers match more than one supported format"
        )
    if passwatcher:
        return CsvFormat.PASSWATCHER
    if browser:
        return CsvFormat.BROWSER
    raise CsvImportError(
        "missing_columns", "The CSV is missing required service/url, username, or password columns"
    )


def _known_columns(csv_format: CsvFormat) -> frozenset[str]:
    if csv_format is CsvFormat.PASSWATCHER:
        return _PASSWATCHER_REQUIRED | _PASSWATCHER_OPTIONAL
    return _BROWSER_REQUIRED | _BROWSER_OPTIONAL


def _draft_from_row(
    row: list[str],
    indexes: dict[str, int],
    csv_format: CsvFormat,
    row_number: int,
) -> CredentialDraft:
    def value(column: str) -> str:
        index = indexes.get(column)
        return "" if index is None else row[index]

    if csv_format is CsvFormat.PASSWATCHER:
        fields = (
            value("service").strip(),
            value("label").strip(),
            value("username").strip(),
            value("password"),
            value("notes").strip(),
        )
    else:
        fields = (
            value("url").strip(),
            value("name").strip(),
            value("username").strip(),
            value("password"),
            value("note").strip(),
        )

    names = ("service", "label", "username", "password", "notes")
    issues: list[CsvIssue] = []
    for name, field in zip(names, fields):
        if name in {"service", "username", "password"} and not field:
            issues.append(CsvIssue(row_number, name, "Required field is empty"))
        elif len(field.encode("utf-8")) > MAX_FIELD_BYTES:
            issues.append(CsvIssue(row_number, name, f"Field exceeds {MAX_FIELD_BYTES} UTF-8 bytes"))
    if issues:
        raise CsvImportError("invalid_row", "A CSV row contains invalid fields", tuple(issues))
    return CredentialDraft(*fields)


def _identity(service: str, label: str, username: str) -> tuple[str, str, str]:
    return service.strip().casefold(), label.strip().casefold(), username.strip().casefold()
