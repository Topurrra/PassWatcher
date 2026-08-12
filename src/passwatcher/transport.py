"""Safe argument-vector OpenSSH transport for Passwatcher RPC requests."""

from __future__ import annotations

import subprocess

from .config import ClientConfig


_REMOTE_COMMAND = "~/.local/bin/passwatcher-server rpc"


class TransportError(Exception):
    """A safe, stable error raised when an SSH request cannot complete."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SshTransport:
    """Send one JSON request to the fixed remote server command over OpenSSH."""

    def __init__(self, config: ClientConfig, timeout_seconds: float = 15.0) -> None:
        self._config = config
        self._timeout_seconds = timeout_seconds

    def request(self, raw: bytes) -> bytes:
        """Return one remote response, mapping SSH failures to safe typed errors."""
        try:
            completed = subprocess.run(
                self._command(),
                input=raw,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self._timeout_seconds,
                shell=False,
            )
        except FileNotFoundError:
            raise TransportError("ssh_not_found", "OpenSSH is not available") from None
        except subprocess.TimeoutExpired:
            raise TransportError("timeout", "The SSH connection timed out") from None

        if completed.returncode == 255:
            raise TransportError("connection_failed", "The SSH connection could not be established")
        if completed.returncode != 0:
            raise TransportError("ssh_failed", "The SSH request could not be completed")
        if not completed.stdout:
            raise TransportError("empty_response", "The server returned an empty response")
        return completed.stdout

    def _command(self) -> list[str]:
        command = ["ssh", "-T", "-p", str(self._config.port)]
        if self._config.identity_file is not None:
            command.extend(["-i", str(self._config.identity_file)])
        command.extend(["--", f"{self._config.user}@{self._config.host}", _REMOTE_COMMAND])
        return command
