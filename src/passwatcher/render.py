"""Midnight Neon and plain-text rendering for Passwatcher records."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from collections.abc import Iterable, Sequence

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .csv_import import CsvFormat, CsvIssue, ImportPreview
from .service import CredentialRecord, ImportSummary


BACKGROUND = "#090d14"
CYAN = "#67e8f9"
VIOLET = "#a78bfa"
GREEN = "#4ade80"
RED = "#fb7185"
MUTED = "#64748b"

_THEME = Theme(
    {
        "cyan": CYAN,
        "violet": VIOLET,
        "green": GREEN,
        "red": RED,
        "muted": MUTED,
    }
)


class Renderer:
    """Render records without relying on terminal capabilities in callers."""

    def __init__(self, plain: bool = False) -> None:
        self.plain = plain or not _color_output_available()

    def one_match(self, record: CredentialRecord) -> None:
        if self.plain:
            self._print_rows("Credential", [record], secrets=True)
            return

        details = Group(
            Text.assemble(("Service: ", "muted"), (record.service, "cyan")),
            Text.assemble(("Label: ", "muted"), record.label),
            Text.assemble(("Username: ", "muted"), record.username),
            Text.assemble(("Password: ", "muted"), (record.password, "green")),
        )
        self._console().print(
            Panel(details, title=f"[cyan]Credential #{record.id}[/cyan]", border_style="violet", expand=False)
        )

    def many_matches(self, records: Iterable[CredentialRecord]) -> None:
        records = list(records)
        if self.plain:
            self._print_rows("Credentials", records, secrets=True)
            self._print_plain("Refine the query with a service, label, or username fragment.")
            return

        table = self._table(secrets=True)
        for record in records:
            table.add_row(
                Text(str(record.id)),
                Text(record.service),
                Text(record.label),
                Text(record.username),
                Text(record.password, style="green"),
            )
        self._console().print(table)
        self._console().print("[muted]Refine the query with a service, label, or username fragment.[/muted]")

    def list_records(self, records: Iterable[CredentialRecord], *, secrets: bool) -> None:
        records = list(records)
        if self.plain:
            self._print_rows("Credentials", records, secrets=secrets)
            return

        table = self._table(secrets=secrets)
        for record in records:
            values = [
                Text(str(record.id)),
                Text(record.service),
                Text(record.label),
                Text(record.username),
            ]
            if secrets:
                values.append(Text(record.password, style="green"))
            table.add_row(*values)
        self._console().print(table)

    def not_found(self) -> None:
        self._print_plain("No credentials found. Try a different service, label, or username.")

    def import_preview(self, preview: ImportPreview) -> None:
        """Render one non-secret import plan."""
        if self.plain:
            lines = [
                "Import preview",
                f"Format: {preview.format.value}",
                f"Total: {preview.total}",
                f"Insert: {preview.inserted}",
                f"Update: {preview.updated}",
                f"Skip: {preview.skipped}",
            ]
            if preview.ignored_columns:
                lines.append(f"Ignored columns: {', '.join(preview.ignored_columns)}")
            for line in lines:
                self._print_plain(line)
            return

        table = Table(
            title="[cyan]Import preview[/cyan]",
            border_style="violet",
            header_style="cyan",
        )
        table.add_column("Item")
        table.add_column("Value", justify="right")
        for label, value in (
            ("Format", preview.format.value),
            ("Total", str(preview.total)),
            ("Insert", str(preview.inserted)),
            ("Update", str(preview.updated)),
            ("Skip", str(preview.skipped)),
        ):
            table.add_row(label, value)
        self._console().print(table)
        if preview.ignored_columns:
            self._console().print(
                f"[muted]Ignored columns: {', '.join(preview.ignored_columns)}[/muted]"
            )

    def import_errors(self, issues: Sequence[CsvIssue]) -> None:
        """Render validation locations without rendering their source values."""
        if self.plain:
            self._print_plain("Import validation failed")
            for issue in issues:
                location = f"Row {issue.row}" if issue.row is not None else "Header"
                self._print_plain(f"{location} | {issue.field} | {issue.message}")
            return

        table = Table(
            title="[red]Import validation failed[/red]",
            border_style="red",
            header_style="cyan",
        )
        table.add_column("Location")
        table.add_column("Field")
        table.add_column("Problem")
        for issue in issues:
            location = f"Row {issue.row}" if issue.row is not None else "Header"
            table.add_row(location, issue.field, issue.message)
        self._console().print(table)

    def export_warning(self, path: Path, format: CsvFormat) -> None:
        """Warn before creating a portable plaintext credential file."""
        lines = [
            f"Warning: {format.value} CSV export is plaintext: {path}",
            "Anyone who can read the file can read every password.",
            "Spreadsheet software may interpret password-like values as formulas.",
            "Delete or secure the file immediately after use.",
        ]
        if self.plain:
            for line in lines:
                self._print_plain(line)
            return
        for line in lines:
            self._console().print(f"[red]{line}[/red]")

    def import_complete(self, summary: ImportSummary) -> None:
        """Render non-secret committed import counts."""
        message = (
            f"Import complete: {summary.inserted} inserted, "
            f"{summary.updated} updated, {summary.skipped} skipped."
        )
        if self.plain:
            self._print_plain(message)
        else:
            self._console().print(f"[green]{message}[/green]")

    def export_complete(self, count: int, path: Path, format: CsvFormat) -> None:
        """Render a non-secret local export summary."""
        message = f"Export complete: {count} records in {format.value} format at {path}."
        if self.plain:
            self._print_plain(message)
        else:
            self._console().print(f"[green]{message}[/green]")

    def error(self, message: str, *, debug: str | None = None) -> None:
        if self.plain:
            self._print_plain(f"Error: {message}")
            if debug is not None:
                self._print_plain(f"Debug: {debug}")
            return
        self._console().print(f"[red]Error:[/red] {message}")
        if debug is not None:
            self._console().print(f"[muted]Debug: {debug}[/muted]")

    def _table(self, *, secrets: bool) -> Table:
        table = Table(title="[cyan]Credentials[/cyan]", border_style="violet", header_style="cyan")
        for heading in ("ID", "Service", "Label", "Username"):
            table.add_column(heading)
        if secrets:
            table.add_column("Password")
        return table

    def _print_rows(self, heading: str, records: Iterable[CredentialRecord], *, secrets: bool) -> None:
        headings = ["ID", "Service", "Label", "Username"]
        if secrets:
            headings.append("Password")
        self._print_plain(heading)
        self._print_plain("\t".join(headings))
        for record in records:
            values = [str(record.id), record.service, record.label, record.username]
            if secrets:
                values.append(record.password)
            self._print_plain("\t".join(values))

    def _console(self) -> Console:
        return Console(theme=_THEME, force_terminal=True, color_system="truecolor")

    @staticmethod
    def _print_plain(text: str) -> None:
        print(text)


def _color_output_available() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ
