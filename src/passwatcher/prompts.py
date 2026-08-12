"""Small interactive-input boundary with cancellation-safe helpers."""

from __future__ import annotations

import typer

from .service import CredentialRecord


class PromptCancelled(Exception):
    """Raised when an interactive flow is cancelled before a write."""


def text(label: str, *, default: str | None = None, secret: bool = False) -> str:
    """Prompt for text and translate terminal cancellation into one exception."""
    try:
        return typer.prompt(label, default=default, hide_input=secret)
    except (typer.Abort, EOFError, KeyboardInterrupt) as error:
        raise PromptCancelled from error


def confirm(label: str, *, default: bool = False) -> bool:
    """Prompt for confirmation, defaulting to the caller's safe choice."""
    try:
        return typer.confirm(label, default=default)
    except (typer.Abort, EOFError, KeyboardInterrupt) as error:
        raise PromptCancelled from error


def select_record(records: list[CredentialRecord]) -> CredentialRecord | None:
    """Return the chosen record, or ``None`` when no record was available."""
    if not records:
        return None
    if len(records) == 1:
        return records[0]

    typer.echo("Multiple credentials found:")
    for number, record in enumerate(records, start=1):
        typer.echo(f"{number}. {record.service} | {record.label} | {record.username}")
    while True:
        answer = text("Select a number").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(records):
            return records[int(answer) - 1]
        typer.echo(f"Enter a number from 1 to {len(records)}.")
