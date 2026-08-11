"""The Passwatcher lookup and list command-line interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from platformdirs import user_config_dir

from .clipboard import Clipboard
from .config import ConfigError, load_config
from .protocol import ProtocolError
from .render import Renderer
from .service import PasswordService
from .transport import SshTransport, TransportError


app = typer.Typer(add_completion=False, invoke_without_command=True, no_args_is_help=False)


@dataclass(slots=True)
class _Runtime:
    service: PasswordService
    clipboard: Clipboard
    renderer: Renderer
    debug: bool


def default_config_path() -> Path:
    """Return the user-local location for the connection-only configuration."""
    return Path(user_config_dir("Passwatcher", appauthor=False)) / "config.toml"


def create_service(config_path: Path) -> PasswordService:
    """Build the production service; tests replace this narrow construction seam."""
    try:
        config = load_config(config_path)
    except OSError as error:
        raise ConfigError("unable to read configuration") from error
    return PasswordService(SshTransport(config))


def create_clipboard() -> Clipboard:
    """Build the production clipboard; tests replace this narrow construction seam."""
    return Clipboard()


def _runtime(ctx: typer.Context, config_path: Path, plain: bool, debug: bool) -> _Runtime:
    if ctx.obj is None:
        ctx.obj = _Runtime(create_service(config_path), create_clipboard(), Renderer(plain), debug)
    return ctx.obj


@app.callback()
def lookup(
    ctx: typer.Context,
    query: Annotated[list[str] | None, typer.Argument()] = None,
    plain: Annotated[bool, typer.Option("--plain", help="Use stable plain-text output.")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show safe diagnostic metadata.")] = False,
    config_path: Annotated[
        Path, typer.Option("--config", help="Path to the connection configuration.")
    ] = default_config_path(),
) -> None:
    """Look up credentials with one or more query words."""
    if ctx.invoked_subcommand is None and not query:
        raise typer.BadParameter("A search query is required.", param_hint="QUERY")

    try:
        runtime = _runtime(ctx, config_path, plain, debug)
    except (ConfigError, TransportError, ProtocolError) as error:
        _render_expected_error(Renderer(plain), debug, error)
        raise typer.Exit(1) from None

    if ctx.invoked_subcommand is not None:
        return
    assert query is not None
    if query[0] == "list":
        extra_arguments = query[1:]
        if extra_arguments not in ([], ["--secrets"]):
            raise typer.BadParameter("The list command accepts only --secrets.", param_hint="list")
        _list_records(runtime, secrets=extra_arguments == ["--secrets"])
        return
    try:
        matches = runtime.service.search(" ".join(query))
    except (ConfigError, TransportError, ProtocolError) as error:
        _render_expected_error(runtime.renderer, runtime.debug, error)
        raise typer.Exit(1) from None

    if not matches:
        runtime.renderer.not_found()
        raise typer.Exit(1)
    if len(matches) == 1:
        runtime.renderer.one_match(matches[0])
        runtime.clipboard.copy(matches[0].password)
        return
    runtime.renderer.many_matches(matches)


@app.command("list")
def list_credentials(
    ctx: typer.Context,
    secrets: Annotated[bool, typer.Option("--secrets", help="Include passwords in output.")] = False,
) -> None:
    """List every credential in the server's deterministic order."""
    runtime: _Runtime = ctx.obj
    _list_records(runtime, secrets=secrets)


def _list_records(runtime: _Runtime, *, secrets: bool) -> None:
    try:
        records = runtime.service.list_all()
    except (ConfigError, TransportError, ProtocolError) as error:
        _render_expected_error(runtime.renderer, runtime.debug, error)
        raise typer.Exit(1) from None
    runtime.renderer.list_records(records, secrets=secrets)


def _render_expected_error(
    renderer: Renderer,
    debug_enabled: bool,
    error: ConfigError | TransportError | ProtocolError,
) -> None:
    if isinstance(error, ConfigError):
        message = "Configuration problem. Run `pw setup` to update your connection settings."
        debug = type(error).__name__
    elif isinstance(error, TransportError):
        message = "Could not reach the vault. Check SSH connectivity and try again."
        debug = f"{type(error).__name__} (code: {error.code})"
    else:
        message = "The vault returned an unexpected response. Try again or run `pw doctor`."
        debug = type(error).__name__
    renderer.error(message, debug=debug if debug_enabled else None)
