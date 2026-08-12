"""Small interactive-input boundary with cancellation-safe helpers."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

import typer
from rich.console import Console

from .service import CredentialRecord


class PromptCancelled(Exception):
    """Raised when an interactive flow is cancelled before a write."""


def text(label: str, *, default: str | None = None, secret: bool = False) -> str:
    """Prompt for text and translate terminal cancellation into one exception."""
    try:
        return typer.prompt(label, default=default, hide_input=secret)
    except (typer.Abort, EOFError, KeyboardInterrupt) as error:
        raise PromptCancelled from error


def optional_text(label: str, *, current: str | None = None) -> str:
    """Read optional text; Enter keeps a current value and ``/clear`` removes it."""
    try:
        value = typer.prompt(
            label,
            default=current if current is not None else "",
            show_default=current not in (None, ""),
        )
    except (typer.Abort, EOFError, KeyboardInterrupt) as error:
        raise PromptCancelled from error
    if current is not None and value == "/clear":
        return ""
    return value


def confirm(label: str, *, default: bool = False) -> bool:
    """Prompt for confirmation, defaulting to the caller's safe choice."""
    try:
        return typer.confirm(label, default=default)
    except (typer.Abort, EOFError, KeyboardInterrupt) as error:
        raise PromptCancelled from error


@contextmanager
def status(label: str) -> Iterator[None]:
    """Display one non-secret progress spinner around a bounded operation."""
    with Console(stderr=True).status(label):
        yield


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
