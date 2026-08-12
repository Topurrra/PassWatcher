"""The Passwatcher command-line interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from platformdirs import user_config_dir
from typer.core import TyperGroup

from .clipboard import Clipboard
from .config import AppConfig, BackendMode, ClientConfig, ConfigError, load_config, save_config
from .csv_export import CsvExportError, export_records, validate_export_destination
from .csv_import import (
    CsvFormat,
    CsvImportError,
    DuplicatePolicy,
    parse_import,
    preview_import,
    validate_request_size,
)
from .passwords import PasswordPolicyError, generate_password
from .protocol import ProtocolError
from . import prompts
from .local_crypto import DpapiProtector, ProtectionError
from .local_vault import (
    LocalPasswordService,
    LocalVaultError,
    default_local_vault_path,
    delete_local_vault,
)
from .migration import MigrationError, execute_migration, plan_migration
from .render import Renderer
from .service import CredentialRecord, CredentialService, PasswordService
from .setup import Doctor, LocalDoctor, SetupError, SetupManager, SubprocessSetupRunner
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
    service: CredentialService | None
    clipboard: Clipboard
    renderer: Renderer
    debug: bool
    config_path: Path


def default_config_path() -> Path:
    """Return the user-local location for the connection-only configuration."""
    return Path(user_config_dir("Passwatcher", appauthor=False, roaming=True)) / "config.toml"


def create_service(config_path: Path) -> CredentialService:
    """Build the production service; tests replace this narrow construction seam."""
    try:
        app_config = load_config(config_path)
    except OSError as error:
        raise ConfigError("unable to read configuration") from error
    if app_config.backend is BackendMode.LOCAL:
        return create_local_service()
    if app_config.remote is None:
        raise ConfigError("remote settings are required for the remote backend")
    return PasswordService(SshTransport(app_config.remote))


def create_local_service() -> LocalPasswordService:
    """Build the production current-user DPAPI local service."""
    return LocalPasswordService(default_local_vault_path(), DpapiProtector())


def create_remote_service(config: ClientConfig) -> PasswordService:
    """Build a remote service before its configuration becomes active."""
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


def create_local_doctor() -> LocalDoctor:
    """Build read-only diagnostics for the fixed local vault path."""
    return LocalDoctor(default_local_vault_path())


def _runtime(ctx: typer.Context, config_path: Path, plain: bool, debug: bool) -> _Runtime:
    if ctx.obj is None:
        ctx.obj = _Runtime(None, create_clipboard(), Renderer(plain), debug, config_path)
    return ctx.obj


def _service(runtime: _Runtime) -> CredentialService:
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
    if query[0] in {"add", "edit", "delete", "export", "generate", "import", "list"}:
        return
    try:
        matches = _service(runtime).search(" ".join(query))
    except (ConfigError, TransportError, ProtocolError, LocalVaultError, ProtectionError) as error:
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
    except (ConfigError, TransportError, ProtocolError, LocalVaultError, ProtectionError) as error:
        _render_expected_error(runtime.renderer, runtime.debug, error)
        raise typer.Exit(1) from None
    runtime.renderer.list_records(records, secrets=secrets)


@app.command("import")
def import_credentials(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="CSV file to import.")],
    dry_run: Annotated[
        bool, typer.Option("-n", "--dry-run", help="Validate and preview without importing.")
    ] = False,
    duplicates: Annotated[
        DuplicatePolicy,
        typer.Option("-d", "--duplicates", help="How to handle existing credentials."),
    ] = DuplicatePolicy.SKIP,
    yes: Annotated[
        bool, typer.Option("-y", "--yes", help="Import without confirmation.")
    ] = False,
) -> None:
    """Preview and atomically import credentials from a local CSV file."""
    runtime: _Runtime = ctx.obj
    _import_credentials(runtime, path, dry_run=dry_run, duplicates=duplicates, yes=yes)


def _import_credentials(
    runtime: _Runtime,
    path: Path,
    *,
    dry_run: bool,
    duplicates: DuplicatePolicy,
    yes: bool,
) -> None:
    try:
        parsed = parse_import(path)
        existing = _service(runtime).list_all()
        preview = preview_import(parsed, existing, duplicates)
        validate_request_size(parsed, duplicates)
        runtime.renderer.import_preview(preview)
        if dry_run:
            return
        if not yes and not prompts.confirm("Import credentials?", default=False):
            _cancelled()
            return
        summary = _service(runtime).import_many(list(parsed.records), duplicates.value)
    except prompts.PromptCancelled:
        _cancelled()
        return
    except CsvImportError as error:
        if error.issues:
            runtime.renderer.import_errors(error.issues)
        else:
            runtime.renderer.error(error.message)
        raise typer.Exit(1) from None
    except (ConfigError, TransportError, ProtocolError, LocalVaultError, ProtectionError) as error:
        if isinstance(error, ProtocolError) and error.code in {
            "incompatible_protocol",
            "unknown_operation",
        }:
            runtime.renderer.error("Run `pw setup` to upgrade the remote server.")
        else:
            _render_expected_error(runtime.renderer, runtime.debug, error)
        raise typer.Exit(1) from None
    runtime.renderer.import_complete(summary)


@app.command("export")
def export_credentials(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="Local CSV destination.")],
    csv_format: Annotated[
        CsvFormat,
        typer.Option("-t", "--format", help="Export format."),
    ] = CsvFormat.PASSWATCHER,
    force: Annotated[
        bool, typer.Option("-f", "--force", help="Replace an existing regular file.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("-y", "--yes", help="Acknowledge plaintext risk without prompting.")
    ] = False,
) -> None:
    """Export credentials to an atomic local plaintext CSV file."""
    runtime: _Runtime = ctx.obj
    _export_credentials(runtime, path, csv_format=csv_format, force=force, yes=yes)


def _export_credentials(
    runtime: _Runtime,
    path: Path,
    *,
    csv_format: CsvFormat,
    force: bool,
    yes: bool,
) -> None:
    try:
        validate_export_destination(path, force=force)
        runtime.renderer.export_warning(path, csv_format)
        if not yes and not prompts.confirm("Export plaintext credentials?", default=False):
            _cancelled()
            return
        records = _service(runtime).list_all()
        count = export_records(path, records, csv_format, force=force)
    except prompts.PromptCancelled:
        _cancelled()
        return
    except CsvExportError as error:
        runtime.renderer.error(error.message)
        raise typer.Exit(1) from None
    except (ConfigError, TransportError, ProtocolError, LocalVaultError, ProtectionError) as error:
        _render_expected_error(runtime.renderer, runtime.debug, error)
        raise typer.Exit(1) from None
    runtime.renderer.export_complete(count, path, csv_format)


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
    except (ConfigError, TransportError, ProtocolError, LocalVaultError, ProtectionError) as error:
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
    except (ConfigError, TransportError, ProtocolError, LocalVaultError, ProtectionError) as error:
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
    except (ConfigError, TransportError, ProtocolError, LocalVaultError, ProtectionError) as error:
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
def guided_setup(
    ctx: typer.Context,
    local: Annotated[bool, typer.Option("-l", "--local")] = False,
    remote: Annotated[bool, typer.Option("-r", "--remote")] = False,
) -> None:
    """Choose and configure a local or remote vault."""
    runtime: _Runtime = ctx.obj
    if local and remote:
        raise typer.BadParameter("Choose either --local or --remote, not both.")
    if not local and not remote:
        runtime.renderer.setup_choices()
        return
    if local:
        _setup_local(runtime)
    else:
        _setup_remote(runtime)


def _setup_local(runtime: _Runtime) -> None:
    previous = _load_previous_config(runtime.config_path)
    try:
        with prompts.status("Opening local DPAPI vault"):
            destination = create_local_service()
        with prompts.status("Verifying local health"):
            _require_healthy(destination.health(), "local")
        if previous is not None and previous.backend is BackendMode.REMOTE:
            assert previous.remote is not None
            source = create_remote_service(previous.remote)
            if not _migrate(
                runtime,
                source,
                destination,
                source_name="remote",
                destination_name="local",
            ):
                _cancelled()
                return
            _require_healthy(destination.health(), "local")
        save_config(
            runtime.config_path,
            AppConfig(BackendMode.LOCAL, previous.remote if previous else None),
        )
    except prompts.PromptCancelled:
        _cancelled()
    except (ConfigError, LocalVaultError, ProtectionError, MigrationError, TransportError, ProtocolError, OSError) as error:
        _render_setup_error(runtime, error)
        raise typer.Exit(1) from None
    else:
        typer.echo("Setup complete (local).")


def _setup_remote(runtime: _Runtime) -> None:
    previous = _load_previous_config(runtime.config_path)
    remembered = previous.remote if previous is not None else None
    manager = create_setup_manager()
    try:
        host = prompts.text(
            "SSH host", default=remembered.host if remembered else None
        ).strip()
        user = prompts.text(
            "SSH user", default=remembered.user if remembered else None
        ).strip()
        raw_port = prompts.text(
            "SSH port", default=str(remembered.port if remembered else 22)
        ).strip()
        remembered_identity = (
            str(remembered.identity_file)
            if remembered is not None and remembered.identity_file is not None
            else None
        )
        identity = prompts.optional_text(
            "Identity file (optional)", current=remembered_identity
        ).strip()
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
            _require_healthy(result.health, "remote")

        migrating_local = (
            previous is not None and previous.backend is BackendMode.LOCAL
        ) or (previous is None and default_local_vault_path().is_file())
        if migrating_local:
            source = create_local_service()
            destination = create_remote_service(config)
            if not _migrate(
                runtime,
                source,
                destination,
                source_name="local",
                destination_name="remote",
            ):
                _cancelled()
                return
            _require_healthy(destination.health(), "remote")
        with prompts.status("Saving local configuration"):
            save_config(
                runtime.config_path,
                AppConfig(BackendMode.REMOTE, config),
            )
        if migrating_local and prompts.confirm(
            "Delete the migrated local vault and backups?", default=False
        ):
            delete_local_vault(default_local_vault_path())
    except prompts.PromptCancelled:
        _cancelled()
    except (ConfigError, SetupError, LocalVaultError, ProtectionError, MigrationError, TransportError, ProtocolError, OSError) as error:
        _render_setup_error(runtime, error)
        raise typer.Exit(1) from None
    else:
        typer.echo(f"Setup complete ({result.action}).")


def _migrate(
    runtime: _Runtime,
    source: CredentialService,
    destination: CredentialService,
    *,
    source_name: str,
    destination_name: str,
) -> bool:
    """Plan and execute one backend switch without exposing record values."""
    plan = plan_migration(source.list_all(), destination.list_all())
    runtime.renderer.migration_preview(
        plan, source=source_name, destination=destination_name
    )
    if plan.conflicts:
        policy = prompts.migration_conflict_policy()
        if policy is None:
            return False
    else:
        from .migration import ConflictPolicy

        policy = ConflictPolicy.DESTINATION
    summary = execute_migration(destination, plan, policy)
    runtime.renderer.migration_complete(summary)
    return True


def _load_previous_config(path: Path) -> AppConfig | None:
    try:
        return load_config(path)
    except (ConfigError, OSError):
        return None


def _require_healthy(health: dict[str, object], backend: str) -> None:
    if health.get("integrity_check") != "ok":
        raise SetupError("health_failed", f"The {backend} vault failed its health check")


def _render_setup_error(runtime: _Runtime, error: Exception) -> None:
    if isinstance(error, (SetupError, LocalVaultError, ProtectionError, MigrationError)):
        message = error.message
    elif isinstance(error, ConfigError):
        message = str(error)
    elif isinstance(error, (TransportError, ProtocolError)):
        _render_expected_error(runtime.renderer, runtime.debug, error)
        return
    else:
        message = "The local configuration could not be saved"
    runtime.renderer.error(message)


@app.command("doctor")
def doctor(ctx: typer.Context) -> None:
    """Report health for the active vault without repairing it."""
    runtime: _Runtime = ctx.obj
    config = _load_previous_config(runtime.config_path)
    if config is not None and config.backend is BackendMode.LOCAL:
        checks = create_local_doctor().run()
    else:
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
    error: ConfigError | TransportError | ProtocolError | LocalVaultError | ProtectionError,
) -> None:
    if isinstance(error, ConfigError):
        message = "Configuration problem. Run `pw setup` to update your connection settings."
        debug = type(error).__name__
    elif isinstance(error, TransportError):
        message = "Could not reach the vault. Check SSH connectivity and try again."
        debug = f"{type(error).__name__} (code: {error.code})"
    elif isinstance(error, (LocalVaultError, ProtectionError)):
        message = "Could not use the local vault. Run `pw doctor` for safe diagnostics."
        debug = f"{type(error).__name__} (code: {error.code})"
    else:
        message = "The vault returned an unexpected response. Try again or run `pw doctor`."
        debug = type(error).__name__
    renderer.error(message, debug=debug if debug_enabled else None)
