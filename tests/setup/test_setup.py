from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from passwatcher.config import ClientConfig
from passwatcher.setup import RemoteState, SetupError, SetupManager, SubprocessSetupRunner


class FakeRemote:
    def __init__(self) -> None:
        self.state = RemoteState(False, None, None, False)
        self.events: list[str] = []
        self.uploads: list[Path] = []
        self.database_mutations: list[str] = []
        self.created_databases = 0
        self.health_result = {"schema_version": 1, "integrity_check": "ok"}

    def inspect(self, _config: ClientConfig) -> RemoteState:
        return self.state

    def prepare_directories(self, _config: ClientConfig) -> None:
        self.events.append("prepare_directories")

    def upload_bundle(self, _config: ClientConfig, bundle_path: Path) -> None:
        self.events.append("upload_bundle")
        self.uploads.append(bundle_path)

    def verify_bundle(self, _config: ClientConfig, _digest: str) -> None:
        self.events.append("verify_bundle")

    def backup(self, _config: ClientConfig) -> None:
        self.events.append("backup")

    def install_bundle(self, _config: ClientConfig) -> None:
        self.events.append("install_bundle")

    def initialize(self, _config: ClientConfig) -> None:
        self.events.append("initialize")
        self.database_mutations.append("initialize")
        self.created_databases += 1
        self.state = RemoteState(True, 1, 1, True)

    def health(self, _config: ClientConfig) -> dict[str, object]:
        self.events.append("health")
        return self.health_result


@pytest.fixture
def remote() -> FakeRemote:
    return FakeRemote()


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    path = tmp_path / "passwatcher-server.pyz"
    path.write_bytes(b"verified server bundle")
    return path


@pytest.fixture
def manager(remote: FakeRemote, bundle: Path) -> SetupManager:
    return SetupManager(remote, bundle)


def config() -> ClientConfig:
    return ClientConfig("vault.example", "nika", 22, Path("C:/keys/vault"))


def test_setup_reuses_compatible_installation(manager: SetupManager, remote: FakeRemote) -> None:
    """Catches setup replacing a compatible server or touching its database."""
    remote.state = RemoteState(True, 1, 1, True)

    result = manager.install_or_upgrade(config(), remote.state)

    assert result.action == "reused"
    assert remote.uploads == []
    assert remote.database_mutations == []


def test_upgrade_backs_up_before_replacing_server(
    manager: SetupManager, remote: FakeRemote
) -> None:
    """Catches a bundle replacement that can strand an unbacked-up vault."""
    remote.state = RemoteState(True, 0, 1, True)

    manager.install_or_upgrade(config(), remote.state)

    assert remote.events.index("backup") < remote.events.index("install_bundle")


def test_fresh_setup_rerun_does_not_create_second_database(
    manager: SetupManager, remote: FakeRemote
) -> None:
    """Catches reruns initializing over the one existing shared database."""
    manager.install_or_upgrade(config(), RemoteState(False, None, None, False))
    manager.install_or_upgrade(config(), manager.inspect(config()))

    assert remote.created_databases == 1


def test_existing_database_is_never_initialized_during_server_install(
    manager: SetupManager, remote: FakeRemote
) -> None:
    """Catches attaching a new client by recreating the existing Linux vault."""
    manager.install_or_upgrade(config(), RemoteState(False, None, 1, True))

    assert remote.database_mutations == []


def test_bundle_digest_is_verified_before_atomic_install(
    manager: SetupManager, remote: FakeRemote
) -> None:
    """Catches installing a partially transferred or corrupted server bundle."""
    manager.install_or_upgrade(config(), RemoteState(False, None, None, False))

    assert remote.events.index("verify_bundle") < remote.events.index("install_bundle")


def test_failed_health_is_a_setup_error(manager: SetupManager, remote: FakeRemote) -> None:
    """Catches setup claiming success when remote integrity is not healthy."""
    remote.health_result = {"schema_version": 1, "integrity_check": "corrupt"}

    with pytest.raises(SetupError) as error:
        manager.install_or_upgrade(config(), RemoteState(True, 1, 1, True))

    assert error.value.code == "health_failed"


def test_setup_rejects_an_explicitly_unsupported_remote_python(
    manager: SetupManager, remote: FakeRemote
) -> None:
    """Catches uploading a bundle that the inspected remote Python cannot execute."""
    state = RemoteState(False, None, None, False, (3, 10, 14))

    with pytest.raises(SetupError) as error:
        manager.install_or_upgrade(config(), state)

    assert error.value.code == "python_unsupported"
    assert remote.uploads == []


def test_health_rpc_does_not_pass_both_input_and_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches production health checks failing before SSH starts with ValueError."""
    response = (
        b'{"protocol_version":1,"ok":true,"result":'
        b'{"schema_version":1,"integrity_check":"ok"}}'
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert "input" in kwargs
        assert "stdin" not in kwargs
        return subprocess.CompletedProcess(command, 0, stdout=response, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert SubprocessSetupRunner().health(config())["integrity_check"] == "ok"
