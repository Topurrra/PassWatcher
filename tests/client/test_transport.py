from pathlib import Path
import subprocess

import pytest

from passwatcher.config import ClientConfig
from passwatcher.transport import SshTransport, TransportError


@pytest.fixture
def config() -> ClientConfig:
    return ClientConfig("vault.example", "nika", 2222, Path("C:/keys/vault"))


def test_transport_uses_fixed_remote_command_and_json_stdin(
    monkeypatch: pytest.MonkeyPatch, config: ClientConfig
) -> None:
    """A password-like request must remain stdin, never an SSH argument."""
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.update(argv=argv, kwargs=kwargs)
        return subprocess.CompletedProcess(
            argv, 0, stdout=b'{"protocol_version":1,"ok":true,"result":{}}', stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    raw = b'{"password":"a b;$(bad)"}'

    assert SshTransport(config).request(raw) == b'{"protocol_version":1,"ok":true,"result":{}}'
    assert seen["argv"] == [
        "ssh",
        "-T",
        "-p",
        "2222",
        "-i",
        str(config.identity_file),
        "--",
        "nika@vault.example",
        "~/.local/bin/passwatcher-server rpc",
    ]
    assert "a b;$(bad)" not in seen["argv"]
    assert seen["kwargs"] == {
        "input": raw,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "timeout": 15.0,
        "shell": False,
    }


def test_transport_omits_identity_argument_when_no_identity_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An SSH identity argument is optional rather than sent with a null value."""
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout=b"{}", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    SshTransport(ClientConfig("vault.example", "nika")).request(b"{}")

    assert seen["argv"] == [
        "ssh",
        "-T",
        "-p",
        "22",
        "--",
        "nika@vault.example",
        "~/.local/bin/passwatcher-server rpc",
    ]


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (FileNotFoundError(), "ssh_not_found"),
        (subprocess.TimeoutExpired(["ssh"], 15), "timeout"),
    ],
)
def test_transport_maps_local_execution_failures_without_request_contents(
    monkeypatch: pytest.MonkeyPatch, config: ClientConfig, failure: Exception, code: str
) -> None:
    """Local SSH setup failures expose a stable safe error, not request JSON."""

    def fake_run(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(TransportError) as error:
        SshTransport(config).request(b'{"password":"do-not-expose"}')

    assert error.value.code == code
    assert "do-not-expose" not in error.value.message


@pytest.mark.parametrize(
    ("returncode", "code"), [(255, "connection_failed"), (1, "ssh_failed")])
def test_transport_maps_ssh_exit_failures_without_request_contents(
    monkeypatch: pytest.MonkeyPatch, config: ClientConfig, returncode: int, code: str
) -> None:
    """SSH failures do not leak data sent to the remote server."""

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, returncode, stdout=b"", stderr=b"remote detail")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(TransportError) as error:
        SshTransport(config).request(b'{"password":"do-not-expose"}')

    assert error.value.code == code
    assert "do-not-expose" not in error.value.message


def test_transport_rejects_empty_stdout(monkeypatch: pytest.MonkeyPatch, config: ClientConfig) -> None:
    """An empty SSH response cannot be parsed as a server response."""

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(TransportError) as error:
        SshTransport(config).request(b"{}")

    assert error.value.code == "empty_response"
