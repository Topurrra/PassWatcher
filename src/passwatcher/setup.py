"""Idempotent installation and read-only diagnostics for the Linux vault."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Protocol

from .config import BackendMode, ClientConfig, ConfigError, load_config
from .protocol import PROTOCOL_VERSION, make_request, parse_response


SCHEMA_VERSION = 1
SERVER_PATH = "~/.local/bin/passwatcher-server"
DATA_DIR = "~/.local/share/passwatcher"
BACKUP_DIR = f"{DATA_DIR}/backups"
INSTALL_DIR = f"{DATA_DIR}/install"
STAGED_SERVER_PATH = f"{INSTALL_DIR}/passwatcher-server.pyz.new"
DATABASE_PATH = f"{DATA_DIR}/passwatcher.db"


class SetupError(Exception):
    """A safe, stable setup failure suitable for display to the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RemoteState:
    installed: bool
    protocol_version: int | None
    schema_version: int | None
    database_exists: bool
    python_version: tuple[int, int, int] | None = None
    modes_ok: bool = False
    integrity_check: str | None = None
    read_access: bool = False


@dataclass(frozen=True, slots=True)
class SetupResult:
    action: str
    health: dict[str, object]


class SetupRunner(Protocol):
    """Fixed operations needed by setup; no arbitrary command execution is exposed."""

    def inspect(self, config: ClientConfig) -> RemoteState: ...
    def prepare_directories(self, config: ClientConfig) -> None: ...
    def upload_bundle(self, config: ClientConfig, bundle_path: Path) -> None: ...
    def verify_bundle(self, config: ClientConfig, digest: str) -> None: ...
    def backup(self, config: ClientConfig) -> None: ...
    def install_bundle(self, config: ClientConfig) -> None: ...
    def initialize(self, config: ClientConfig) -> None: ...
    def health(self, config: ClientConfig) -> dict[str, object]: ...


class SetupManager:
    """Inspect and safely attach a client to one existing remote vault."""

    def __init__(self, runner: SetupRunner, bundle_path: Path) -> None:
        self._runner = runner
        self._bundle_path = Path(bundle_path)

    @property
    def runner(self) -> SetupRunner:
        return self._runner

    def inspect(self, config: ClientConfig) -> RemoteState:
        return self._runner.inspect(config)

    def install_or_upgrade(self, config: ClientConfig, state: RemoteState) -> SetupResult:
        if state.python_version is not None and state.python_version < (3, 11, 0):
            raise SetupError(
                "python_unsupported", "The remote server requires Python 3.11 or newer"
            )
        if state.schema_version is not None and state.schema_version > SCHEMA_VERSION:
            raise SetupError(
                "incompatible_schema", "The remote vault schema is newer than this client"
            )

        compatible = (
            state.installed
            and state.protocol_version == PROTOCOL_VERSION
            and state.schema_version in (None, SCHEMA_VERSION)
        )
        if compatible and not state.modes_ok:
            raise SetupError(
                "permissions_insecure", "The remote vault permissions are not secure"
            )
        action = "reused" if compatible else ("upgraded" if state.installed else "installed")

        if not compatible:
            digest = self._bundle_digest()
            if state.installed and state.database_exists:
                self._runner.backup(config)
            self._runner.prepare_directories(config)
            self._runner.upload_bundle(config, self._bundle_path)
            self._runner.verify_bundle(config, digest)
            self._runner.install_bundle(config)

        if not state.database_exists:
            self._runner.initialize(config)

        health = self._runner.health(config)
        if (
            health.get("schema_version") != SCHEMA_VERSION
            or health.get("integrity_check") != "ok"
        ):
            raise SetupError("health_failed", "The remote vault failed its health check")
        if health.get("permissions_ok") is not True:
            raise SetupError(
                "permissions_insecure", "The remote vault permissions are not secure"
            )
        return SetupResult(action, health)

    def _bundle_digest(self) -> str:
        try:
            return hashlib.sha256(self._bundle_path.read_bytes()).hexdigest()
        except OSError as error:
            raise SetupError("bundle_unavailable", "The bundled server could not be read") from error


_INSPECT_SCRIPT = r"""
import json, os, re, sqlite3, stat, sys, zipfile
from pathlib import Path
server = Path('~/.local/bin/passwatcher-server').expanduser()
data = Path('~/.local/share/passwatcher').expanduser()
install = data / 'install'
database = data / 'passwatcher.db'
backup = data / 'backups'
def mode(path):
    return stat.S_IMODE(path.stat().st_mode) if path.exists() else None
result = {
    'installed': server.is_file(),
    'protocol_version': None,
    'schema_version': None,
    'database_exists': database.is_file(),
    'python_version': list(sys.version_info[:3]),
    'modes': {
        'server': mode(server),
        'data_dir': mode(data),
        'install_dir': mode(install),
        'backup_dir': mode(backup),
        'database': mode(database),
        'wal': mode(database.with_name(database.name + '-wal')),
        'shm': mode(database.with_name(database.name + '-shm')),
        'backup_files': [mode(path) for path in sorted(backup.glob('passwatcher-*.db'))] if backup.is_dir() else [],
    },
    'integrity_check': None,
    'read_access': False,
}
try:
    if server.is_file():
        with zipfile.ZipFile(server) as archive:
            source = archive.read('passwatcher_server/rpc.py').decode('utf-8')
        match = re.search(r'^PROTOCOL_VERSION\s*=\s*(\d+)', source, re.MULTILINE)
        result['protocol_version'] = int(match.group(1)) if match else None
    if database.is_file():
        uri = database.resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            result['schema_version'] = int(row[0]) if row else None
            result['integrity_check'] = connection.execute('PRAGMA integrity_check').fetchone()[0]
            connection.execute('SELECT id FROM credentials LIMIT 1').fetchone()
            result['read_access'] = True
except Exception:
    pass
print(json.dumps(result, separators=(',', ':')))
""".strip()

_PREPARE_COMMAND = (
    "umask 077; mkdir -p ~/.local/bin ~/.local/share/passwatcher/install "
    "~/.local/share/passwatcher/backups; chmod 700 ~/.local/bin "
    "~/.local/share/passwatcher ~/.local/share/passwatcher/install "
    "~/.local/share/passwatcher/backups"
)
_INSTALL_COMMAND = (
    "chmod 700 ~/.local/share/passwatcher/install/passwatcher-server.pyz.new && "
    "mv -f ~/.local/share/passwatcher/install/passwatcher-server.pyz.new "
    "~/.local/bin/passwatcher-server && chmod 700 ~/.local/bin/passwatcher-server"
)
_BACKUP_COMMAND = "~/.local/bin/passwatcher-server backup"


def _remote_modes_ok(
    value: object, *, installed: bool, database_exists: bool
) -> bool:
    """Validate every required and present remote path against its exact owner-only mode."""
    if not isinstance(value, dict):
        return False
    required_directories = ("data_dir", "install_dir", "backup_dir")
    if any(value.get(name) != 0o700 for name in required_directories):
        return False
    if installed and value.get("server") != 0o700:
        return False
    if database_exists and value.get("database") != 0o600:
        return False
    for optional_file in ("wal", "shm"):
        mode = value.get(optional_file)
        if mode is not None and mode != 0o600:
            return False
    backup_files = value.get("backup_files")
    if not isinstance(backup_files, list):
        return False
    return all(type(mode) is int and mode == 0o600 for mode in backup_files)


class SubprocessSetupRunner:
    """Run only the fixed SSH/SCP operations required by setup and doctor."""

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._timeout_seconds = timeout_seconds

    def connectivity(self, config: ClientConfig) -> None:
        self._ssh(config, "python3 --version")

    def inspect(self, config: ClientConfig) -> RemoteState:
        completed = self._ssh(config, f"python3 -c {shlex.quote(_INSPECT_SCRIPT)}")
        try:
            value = json.loads(completed.stdout.decode("utf-8"))
            python_version = value["python_version"]
            installed = value["installed"]
            database_exists = value["database_exists"]
            return RemoteState(
                installed=installed,
                protocol_version=value["protocol_version"],
                schema_version=value["schema_version"],
                database_exists=database_exists,
                python_version=tuple(python_version),
                modes_ok=_remote_modes_ok(
                    value["modes"],
                    installed=installed,
                    database_exists=database_exists,
                ),
                integrity_check=value["integrity_check"],
                read_access=value["read_access"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise SetupError("inspect_failed", "The remote installation could not be inspected") from None

    def prepare_directories(self, config: ClientConfig) -> None:
        self._ssh(config, _PREPARE_COMMAND)

    def upload_bundle(self, config: ClientConfig, bundle_path: Path) -> None:
        command = ["scp", "-q", "-P", str(config.port)]
        if config.identity_file is not None:
            command.extend(["-i", str(config.identity_file)])
        command.extend(
            ["--", str(bundle_path), f"{self._target(config)}:{STAGED_SERVER_PATH}"]
        )
        self._run(command)

    def verify_bundle(self, config: ClientConfig, digest: str) -> None:
        script = (
            "import hashlib,pathlib,sys;"
            "p=pathlib.Path('~/.local/share/passwatcher/install/passwatcher-server.pyz.new').expanduser();"
            "raise SystemExit(0 if hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[1] else 1)"
        )
        self._ssh(config, f"python3 -c {shlex.quote(script)} {digest}")

    def backup(self, config: ClientConfig) -> None:
        self._ssh(config, _BACKUP_COMMAND)

    def install_bundle(self, config: ClientConfig) -> None:
        self._ssh(config, _INSTALL_COMMAND)

    def initialize(self, config: ClientConfig) -> None:
        self.health(config)

    def health(self, config: ClientConfig) -> dict[str, object]:
        completed = self._ssh(config, f"{SERVER_PATH} rpc", input_data=make_request("health", {}))
        try:
            response = parse_response(completed.stdout)
            result = response["result"]
        except Exception as error:
            if isinstance(error, SetupError):
                raise
            raise SetupError("health_failed", "The remote vault failed its health check") from None
        if not isinstance(result, dict):
            raise SetupError("health_failed", "The remote vault failed its health check")
        return result

    def _ssh(
        self, config: ClientConfig, remote_command: str, *, input_data: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        command = ["ssh", "-T", "-p", str(config.port)]
        if config.identity_file is not None:
            command.extend(["-i", str(config.identity_file)])
        command.extend(["--", self._target(config), remote_command])
        return self._run(command, input_data=input_data)

    def _run(
        self, command: list[str], *, input_data: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            common = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.DEVNULL,
                "timeout": self._timeout_seconds,
                "shell": False,
            }
            if input_data is None:
                completed = subprocess.run(command, stdin=subprocess.DEVNULL, **common)
            else:
                completed = subprocess.run(command, input=input_data, **common)
        except FileNotFoundError:
            raise SetupError("openssh_not_found", "OpenSSH is not available") from None
        except subprocess.TimeoutExpired:
            raise SetupError("connect_failed", "The SSH connection timed out") from None
        if completed.returncode == 255:
            raise SetupError("connect_failed", "The SSH connection failed")
        if completed.returncode != 0:
            raise SetupError("remote_failed", "The remote setup operation failed")
        return completed

    @staticmethod
    def _target(config: ClientConfig) -> str:
        return f"{config.user}@{config.host}"


DoctorCheck = tuple[str, bool, str]


class Doctor:
    """Report local and remote state without changing either installation."""

    def __init__(self, runner: SubprocessSetupRunner) -> None:
        self._runner = runner

    def run(self, config_path: Path) -> list[DoctorCheck]:
        checks: list[DoctorCheck] = []
        try:
            config = load_config(config_path)
        except (ConfigError, OSError):
            config = None
            checks.append(("Configuration", False, "unavailable"))
        else:
            checks.append(("Configuration", True, "valid"))

        remote_config = (
            config.remote
            if config is not None and config.backend is BackendMode.REMOTE
            else None
        )

        ssh_available = shutil.which("ssh") is not None
        scp_available = shutil.which("scp") is not None
        checks.extend(
            [
                ("OpenSSH", ssh_available, "available" if ssh_available else "missing"),
                ("SCP", scp_available, "available" if scp_available else "missing"),
            ]
        )
        if remote_config is None or not ssh_available:
            checks.extend(self._unavailable_remote_checks())
            return checks

        try:
            self._runner.connectivity(remote_config)
            checks.append(("Connectivity", True, "connected"))
        except SetupError as error:
            checks.append(("Connectivity", False, error.code))
            checks.extend(self._unavailable_remote_checks(include_connectivity=False))
            return checks

        try:
            state = self._runner.inspect(remote_config)
        except SetupError as error:
            checks.append(("Remote inspection", False, error.code))
            checks.extend(self._unavailable_remote_checks(include_connectivity=False))
            return checks

        checks.extend(
            [
                (
                    "Fixed-command protocol",
                    state.installed and state.protocol_version == PROTOCOL_VERSION,
                    f"version {state.protocol_version}" if state.protocol_version is not None else "unavailable",
                ),
                (
                    "Server/schema version",
                    state.protocol_version == PROTOCOL_VERSION
                    and state.schema_version == SCHEMA_VERSION,
                    f"protocol {state.protocol_version}, schema {state.schema_version}",
                ),
                ("Mode bits", state.modes_ok, "secure" if state.modes_ok else "check failed"),
                (
                    "SQLite integrity",
                    state.integrity_check == "ok",
                    state.integrity_check or "unavailable",
                ),
                ("Read access", state.read_access, "available" if state.read_access else "unavailable"),
            ]
        )
        return checks

    @staticmethod
    def _unavailable_remote_checks(*, include_connectivity: bool = True) -> list[DoctorCheck]:
        names = [
            "Connectivity",
            "Fixed-command protocol",
            "Server/schema version",
            "Mode bits",
            "SQLite integrity",
            "Read access",
        ]
        if not include_connectivity:
            names = names[1:]
        return [(name, False, "unavailable") for name in names]
