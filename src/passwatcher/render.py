"""Midnight Neon and plain-text rendering for Passwatcher records."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .service import CredentialRecord


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
