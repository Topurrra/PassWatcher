"""The Passwatcher command-line interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from platformdirs import user_config_dir
from typer.core import TyperGroup

from .clipboard import Clipboard
from .config import ClientConfig, ConfigError, load_config, save_config
from .passwords import PasswordPolicyError, generate_password
from .protocol import ProtocolError
from . import prompts
from .render import Renderer
from .service import CredentialRecord, PasswordService
from .setup import Doctor, SetupError, SetupManager, SubprocessSetupRunner
from .transport import SshTransport, TransportError


class _PasswatcherGroup(TyperGroup):
    """Reserve known commands before the default lookup consumes positional words."""

    def parse_args(self, ctx: typer.Context, args: list[str]) -> list[str]:
        command_index = self._command_index(args)
        if command_index is not None:
            super().parse_args(ctx, args[:command_index])
            ctx._protected_args = args[command_index : command_index + 1]
            ctx.args = args[command_index + 1 :]
            return ctx.args
        return super().parse_args(ctx, args)

    def _command_index(self, args: list[str]) -> int | None:
        """Locate a command after any root-level options and their values."""
        index = 0
        while index < len(args):
            argument = args[index]
            if argument in self.commands:
                return index
            if argument in {"--plain", "--debug"} or argument.startswith("--config="):
                index += 1
                continue
            if argument == "--config":
                index += 2
                continue
            return None
        return None


app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    cls=_PasswatcherGroup,
)


@dataclass(slots=True)
class _Runtime:
    service: PasswordService | None
    clipboard: Clipboard
    renderer: Renderer
    debug: bool
    config_path: Path


def default_config_path() -> Path:
    """Return the user-local location for the connection-only configuration."""
    return Path(user_config_dir("Passwatcher", appauthor=False, roaming=True)) / "config.toml"


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


def create_setup_manager() -> SetupManager:
    """Build the production guided-setup workflow."""
    bundle = Path(__file__).with_name("assets") / "passwatcher-server.pyz"
    return SetupManager(SubprocessSetupRunner(), bundle)


def create_doctor() -> Doctor:
    """Build the production read-only diagnostic workflow."""
    return Doctor(SubprocessSetupRunner())


def _runtime(ctx: typer.Context, config_path: Path, plain: bool, debug: bool) -> _Runtime:
    if ctx.obj is None:
        ctx.obj = _Runtime(None, create_clipboard(), Renderer(plain), debug, config_path)
    return ctx.obj


def _service(runtime: _Runtime) -> PasswordService:
    if runtime.service is None:
        runtime.service = create_service(runtime.config_path)
    return runtime.service


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

    runtime = _runtime(ctx, config_path, plain, debug)

    if ctx.invoked_subcommand is not None:
        return
    assert query is not None
    _dispatch_command(runtime, query)
    if query[0] in {"add", "edit", "delete", "generate", "list"}:
        return
    try:
        matches = _service(runtime).search(" ".join(query))
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
        records = _service(runtime).list_all()
    except (ConfigError, TransportError, ProtocolError) as error:
        _render_expected_error(runtime.renderer, runtime.debug, error)
        raise typer.Exit(1) from None
    runtime.renderer.list_records(records, secrets=secrets)


@app.command("add")
def add_credential(
    ctx: typer.Context,
    service: Annotated[str | None, typer.Option("--service")] = None,
    label: Annotated[str | None, typer.Option("--label")] = None,
    username: Annotated[str | None, typer.Option("--username")] = None,
    password: Annotated[str | None, typer.Option("--password")] = None,
    notes: Annotated[str | None, typer.Option("--notes")] = None,
) -> None:
    """Interactively add a credential after confirmation."""
    runtime: _Runtime = ctx.obj
    _add(runtime, service, label, username, password, notes)


def _add(
    runtime: _Runtime,
    service: str | None,
    label: str | None,
    username: str | None,
    password: str | None,
    notes: str | None,
) -> None:
    try:
        service = service if service is not None else prompts.text("Service")
        label = label if label is not None else prompts.optional_text("Label (optional)")
        username = username if username is not None else prompts.text("Username")
        password = _password_value(password)
        notes = notes if notes is not None else prompts.optional_text("Notes (optional)")
        if not prompts.confirm("Create credential?", default=False):
            _cancelled()
            return
        _service(runtime).create(service, label, username, password, notes)
    except prompts.PromptCancelled:
        _cancelled()
    except (ConfigError, TransportError, ProtocolError) as error:
        _render_expected_error(runtime.renderer, runtime.debug, error)
        raise typer.Exit(1) from None
    else:
        typer.echo("Credential added.")


@app.command("edit")
def edit_credential(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument()],
    service: Annotated[str | None, typer.Option("--service")] = None,
    label: Annotated[str | None, typer.Option("--label")] = None,
    username: Annotated[str | None, typer.Option("--username")] = None,
    password: Annotated[str | None, typer.Option("--password")] = None,
    notes: Annotated[str | None, typer.Option("--notes")] = None,
) -> None:
    """Find, edit, and confirm one credential."""
    runtime: _Runtime = ctx.obj
    _edit(runtime, query, service, label, username, password, notes)


def _edit(
    runtime: _Runtime,
    query: str,
    service: str | None,
    label: str | None,
    username: str | None,
    password: str | None,
    notes: str | None,
) -> None:
    try:
        record = _select_match(runtime, query)
        service = service if service is not None else prompts.text("Service", default=record.service)
        label = label if label is not None else prompts.optional_text(
            "Label (optional; /clear removes)", current=record.label
        )
        username = username if username is not None else prompts.text("Username", default=record.username)
        password = _password_value(password, current=record.password)
        notes = notes if notes is not None else prompts.optional_text(
            "Notes (optional; /clear removes)", current=record.notes
        )
        if not prompts.confirm("Update credential?", default=False):
            _cancelled()
            return
        _service(runtime).update(record.id, service, label, username, password, notes)
    except prompts.PromptCancelled:
        _cancelled()
    except (ConfigError, TransportError, ProtocolError) as error:
        _render_expected_error(runtime.renderer, runtime.debug, error)
        raise typer.Exit(1) from None
    else:
        typer.echo("Credential updated.")


@app.command("delete")
def delete_credential(ctx: typer.Context, query: Annotated[str, typer.Argument()]) -> None:
    """Find and delete one credential after an explicit confirmation."""
    runtime: _Runtime = ctx.obj
    _delete(runtime, query)


def _delete(runtime: _Runtime, query: str) -> None:
    try:
        record = _select_match(runtime, query)
        typer.echo(f"Delete credential: {record.service} | {record.label} | {record.username}")
        if not prompts.confirm("Delete this credential?", default=False):
            _cancelled()
            return
        _service(runtime).delete(record.id)
    except prompts.PromptCancelled:
        _cancelled()
    except (ConfigError, TransportError, ProtocolError) as error:
        _render_expected_error(runtime.renderer, runtime.debug, error)
        raise typer.Exit(1) from None
    else:
        typer.echo("Credential deleted.")


@app.command("generate")
def generate(
    ctx: typer.Context,
    length: Annotated[int, typer.Option("--length", min=8, max=256)] = 24,
    no_lower: Annotated[bool, typer.Option("--no-lower")] = False,
    no_upper: Annotated[bool, typer.Option("--no-upper")] = False,
    no_digits: Annotated[bool, typer.Option("--no-digits")] = False,
    no_symbols: Annotated[bool, typer.Option("--no-symbols")] = False,
) -> None:
    """Generate a password and copy the exact result to the clipboard."""
    runtime: _Runtime = ctx.obj
    _generate(runtime, length, not no_lower, not no_upper, not no_digits, not no_symbols)


def _generate(
    runtime: _Runtime, length: int, lower: bool, upper: bool, digits: bool, symbols: bool
) -> None:
    try:
        password = generate_password(length, lower, upper, digits, symbols)
    except PasswordPolicyError as error:
        raise typer.BadParameter(str(error), param_hint="--length") from None
    typer.echo(password)
    runtime.clipboard.copy(password)


@app.command("setup")
def guided_setup(ctx: typer.Context) -> None:
    """Connect this device to the one shared Linux vault."""
    runtime: _Runtime = ctx.obj
    manager = create_setup_manager()
    try:
        host = prompts.text("SSH host").strip()
        user = prompts.text("SSH user").strip()
        raw_port = prompts.text("SSH port", default="22").strip()
        identity = prompts.optional_text("Identity file (optional)").strip()
        try:
            port = int(raw_port)
        except ValueError:
            raise ConfigError("port must be an integer") from None
        config = ClientConfig(host, user, port, Path(identity) if identity else None)

        typer.echo(f"Target: {config.user}@{config.host}:{config.port}")
        if not prompts.confirm("Use this vault?", default=False):
            _cancelled()
            return

        with prompts.status("Checking connectivity"):
            connectivity = getattr(manager.runner, "connectivity", None)
            if connectivity is not None:
                connectivity(config)
        with prompts.status("Inspecting remote vault"):
            state = manager.inspect(config)

        with prompts.status("Installing, upgrading, or reusing server"):
            result = manager.install_or_upgrade(config, state)
        with prompts.status("Verifying remote health"):
            if result.health.get("integrity_check") != "ok":
                raise SetupError("health_failed", "The remote vault failed its health check")
        with prompts.status("Saving local configuration"):
            save_config(runtime.config_path, config)
    except prompts.PromptCancelled:
        _cancelled()
    except (ConfigError, SetupError, OSError) as error:
        if isinstance(error, SetupError):
            message = error.message
        elif isinstance(error, ConfigError):
            message = str(error)
        else:
            message = "The local configuration could not be saved"
        runtime.renderer.error(message)
        raise typer.Exit(1) from None
    else:
        typer.echo(f"Setup complete ({result.action}).")


@app.command("doctor")
def doctor(ctx: typer.Context) -> None:
    """Report local and remote health without changing vault state."""
    runtime: _Runtime = ctx.obj
    checks = create_doctor().run(runtime.config_path)
    for name, passed, detail in checks:
        typer.echo(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")
    if not all(passed for _name, passed, _detail in checks):
        raise typer.Exit(1)


def _dispatch_command(runtime: _Runtime, arguments: list[str]) -> None:
    """Dispatch commands while preserving positional lookup as the default command."""
    command, *remaining = arguments
    if command == "list":
        if remaining not in ([], ["--secrets"]):
            raise typer.BadParameter("The list command accepts only --secrets.", param_hint="list")
        _list_records(runtime, secrets=remaining == ["--secrets"])
    elif command == "add":
        _add(runtime, **_field_options(remaining))
    elif command == "edit":
        query, options = _query_and_field_options(remaining)
        _edit(runtime, query, **options)
    elif command == "delete":
        if len(remaining) != 1:
            raise typer.BadParameter("The delete command requires one QUERY.", param_hint="QUERY")
        _delete(runtime, remaining[0])
    elif command == "generate":
        _generate(runtime, **_generation_options(remaining))


def _field_options(arguments: list[str]) -> dict[str, str | None]:
    values: dict[str, str | None] = {
        "service": None,
        "label": None,
        "username": None,
        "password": None,
        "notes": None,
    }
    iterator = iter(arguments)
    for option in iterator:
        if option not in {f"--{name}" for name in values}:
            raise typer.BadParameter(f"Unknown option: {option}")
        try:
            values[option.removeprefix("--")] = next(iterator)
        except StopIteration:
            raise typer.BadParameter(f"Option {option} requires a value.") from None
    return values


def _query_and_field_options(arguments: list[str]) -> tuple[str, dict[str, str | None]]:
    try:
        first_option = next(index for index, value in enumerate(arguments) if value.startswith("--"))
    except StopIteration:
        first_option = len(arguments)
    query_parts, option_parts = arguments[:first_option], arguments[first_option:]
    if not query_parts:
        raise typer.BadParameter("The edit command requires a QUERY.", param_hint="QUERY")
    return " ".join(query_parts), _field_options(option_parts)


def _generation_options(arguments: list[str]) -> dict[str, int | bool]:
    values: dict[str, int | bool] = {
        "length": 24,
        "lower": True,
        "upper": True,
        "digits": True,
        "symbols": True,
    }
    iterator = iter(arguments)
    for option in iterator:
        if option == "--length":
            try:
                values["length"] = int(next(iterator))
            except (StopIteration, ValueError):
                raise typer.BadParameter("--length requires an integer.") from None
        elif option in {"--no-lower", "--no-upper", "--no-digits", "--no-symbols"}:
            values[option.removeprefix("--no-")] = False
        else:
            raise typer.BadParameter(f"Unknown option: {option}")
    return values


def _password_value(value: str | None, *, current: str | None = None) -> str:
    """Resolve a typed or generated password without displaying existing secrets."""
    if value is None:
        value = prompts.text(
            "Password (or 'generate')",
            default="" if current is not None else None,
            secret=True,
        )
    if not value and current is not None:
        return current
    if value.casefold() == "generate":
        return generate_password()
    return value


def _select_match(runtime: _Runtime, query: str) -> CredentialRecord:
    matches = _service(runtime).search(query)
    if not matches:
        runtime.renderer.not_found()
        raise typer.Exit(1)
    selected = prompts.select_record(matches)
    assert selected is not None
    return selected


def _cancelled() -> None:
    typer.echo("Cancelled")


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
