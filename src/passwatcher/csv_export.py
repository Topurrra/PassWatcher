"""Exact, atomic local CSV export for PassWatcher credentials."""

from __future__ import annotations

import csv
import os
from pathlib import Path
import tempfile
from collections.abc import Sequence
from typing import TextIO

from .csv_import import CsvFormat
from .service import CredentialRecord


class CsvExportError(OSError):
    """A safe export failure that does not contain credential values."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_export_destination(path: Path, *, force: bool) -> None:
    """Reject unsafe or unexpectedly existing export destinations."""
    path = Path(path)
    if not path.parent.is_dir():
        raise CsvExportError(
            "invalid_destination", "The export destination directory does not exist"
        )
    if path.is_symlink():
        raise CsvExportError("unsafe_destination", "The export destination must not be a symlink")
    if path.exists():
        if not path.is_file():
            raise CsvExportError(
                "unsafe_destination", "The export destination must be a regular file"
            )
        if not force:
            raise CsvExportError(
                "destination_exists", "The export destination already exists; use --force to replace it"
            )


def export_records(
    path: Path,
    records: Sequence[CredentialRecord],
    format: CsvFormat,
    *,
    force: bool,
) -> int:
    """Write all records beside *path* and atomically install the finished CSV."""
    path = Path(path)
    validate_export_destination(path, force=force)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            encoding="utf-8",
            newline="",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            _write_rows(temp_file, records, format)
            temp_file.flush()
        os.replace(temp_path, path)
    except (OSError, csv.Error):
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise CsvExportError("write_failed", "The CSV export could not be written") from None
    return len(records)


def _write_rows(
    output: TextIO,
    records: Sequence[CredentialRecord],
    format: CsvFormat,
) -> None:
    writer = csv.writer(output, lineterminator="\n")
    if format is CsvFormat.PASSWATCHER:
        writer.writerow(["service", "label", "username", "password", "notes"])
        for record in records:
            writer.writerow(_passwatcher_row(record))
        return
    writer.writerow(["name", "url", "username", "password", "note"])
    for record in records:
        writer.writerow(_browser_row(record))


def _passwatcher_row(record: CredentialRecord) -> list[str]:
    return [record.service, record.label, record.username, record.password, record.notes]


def _browser_row(record: CredentialRecord) -> list[str]:
    return [record.label, record.service, record.username, record.password, record.notes]
