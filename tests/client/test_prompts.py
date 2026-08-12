from __future__ import annotations

import typer
from typer.testing import CliRunner

from passwatcher import prompts


def _optional_app(current: str | None = None) -> typer.Typer:
    app = typer.Typer()

    @app.command()
    def read() -> None:
        typer.echo(repr(prompts.optional_text("Optional", current=current)))

    return app


def test_optional_text_accepts_empty_input() -> None:
    """Catches optional prompts rejecting Enter when no value is required."""
    result = CliRunner().invoke(_optional_app(), [], input="\n")

    assert result.exit_code == 0
    assert "''" in result.stdout


def test_optional_text_keeps_current_value_on_enter() -> None:
    """Catches an edit prompt clearing a value when Enter should retain it."""
    result = CliRunner().invoke(_optional_app("work"), [], input="\n")

    assert result.exit_code == 0
    assert "'work'" in result.stdout


def test_optional_text_clears_current_value() -> None:
    """Catches the explicit clear token being stored as a literal label."""
    result = CliRunner().invoke(_optional_app("work"), [], input="/clear\n")

    assert result.exit_code == 0
    assert "''" in result.stdout
