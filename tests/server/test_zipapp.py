import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


PROJECT_ROOT = Path(__file__).parents[2]
BUILD_SCRIPT = PROJECT_ROOT / "tools" / "build_server_zipapp.py"
ZIPAPP_PATH = PROJECT_ROOT / "src" / "passwatcher" / "assets" / "passwatcher-server.pyz"


def _load_zipapp_builder():
    spec = importlib.util.spec_from_file_location("passwatcher_zipapp_builder", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server_command() -> list[str]:
    return [sys.executable, "-m", "passwatcher_server"]


@pytest.fixture
def source_environment(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PASSWATCHER_DATA_DIR": str(tmp_path),
        "PYTHONPATH": str(PROJECT_ROOT / "src"),
    }


def test_rpc_process_reads_one_request_and_writes_one_response(
    server_command: list[str], source_environment: dict[str, str]
) -> None:
    request = json.dumps({"protocol_version": 1, "operation": "health", "payload": {}})

    completed = subprocess.run(
        server_command + ["rpc"],
        input=request,
        text=True,
        capture_output=True,
        env=source_environment,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.endswith("\n")
    assert completed.stdout.count("\n") == 1
    assert json.loads(completed.stdout)["ok"] is True
    assert completed.stderr == ""


def test_rpc_process_accepts_only_rpc(
    server_command: list[str], source_environment: dict[str, str]
) -> None:
    completed = subprocess.run(
        server_command + ["unexpected"],
        text=True,
        capture_output=True,
        env=source_environment,
        check=False,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""


def test_server_backup_command_copies_the_existing_vault(
    server_command: list[str], source_environment: dict[str, str], tmp_path: Path
) -> None:
    """Catches setup upgrades calling a backup command the installed server lacks."""
    create_request = json.dumps(
        {
            "protocol_version": 1,
            "operation": "create",
            "payload": {
                "service": "github.com",
                "label": "work",
                "username": "nika",
                "password": "secret",
                "notes": "",
            },
        }
    )
    created = subprocess.run(
        server_command + ["rpc"],
        input=create_request,
        text=True,
        capture_output=True,
        env=source_environment,
        check=False,
    )

    backed_up = subprocess.run(
        server_command + ["backup"],
        text=True,
        capture_output=True,
        env=source_environment,
        check=False,
    )

    assert created.returncode == 0
    assert backed_up.returncode == 0
    assert backed_up.stdout == "" and backed_up.stderr == ""
    backups = list(tmp_path.joinpath("backups").glob("passwatcher-*-v1.db"))
    assert len(backups) == 1
    assert tmp_path.joinpath("passwatcher.db").exists()


def test_built_zipapp_is_reproducible_and_runs_without_project_dependencies(tmp_path: Path) -> None:
    first_build = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT)], text=True, capture_output=True, check=False
    )
    first_digest = hashlib.sha256(ZIPAPP_PATH.read_bytes()).hexdigest()
    second_build = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT)], text=True, capture_output=True, check=False
    )
    second_digest = hashlib.sha256(ZIPAPP_PATH.read_bytes()).hexdigest()
    health_request = json.dumps({"protocol_version": 1, "operation": "health", "payload": {}})
    completed = subprocess.run(
        [sys.executable, str(ZIPAPP_PATH), "rpc"],
        input=health_request,
        text=True,
        capture_output=True,
        env={"PASSWATCHER_DATA_DIR": str(tmp_path), "PYTHONNOUSERSITE": "1"},
        check=False,
    )

    assert first_build.returncode == 0, first_build.stderr
    assert second_build.returncode == 0, second_build.stderr
    assert first_digest == second_digest
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["ok"] is True
    assert completed.stderr == ""
    with zipfile.ZipFile(ZIPAPP_PATH) as archive:
        names = archive.namelist()
        assert names == [
            "__main__.py",
            "passwatcher_server/",
            "passwatcher_server/__init__.py",
            "passwatcher_server/__main__.py",
            "passwatcher_server/database.py",
            "passwatcher_server/models.py",
            "passwatcher_server/rpc.py",
        ]
        assert {entry.date_time for entry in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_zipapp_digest_is_independent_of_checked_out_line_endings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _load_zipapp_builder()
    digests: list[str] = []

    for name, newline in (("lf", "\n"), ("crlf", "\r\n")):
        project_root = tmp_path / name
        source_package = project_root / "src" / "passwatcher_server"
        source_package.mkdir(parents=True)
        source_package.joinpath("__init__.py").write_bytes(
            f"VALUE = 1{newline}".encode("utf-8")
        )
        source_package.joinpath("__main__.py").write_bytes(
            f"def main():{newline}    return 0{newline}".encode("utf-8")
        )
        output_path = project_root / "server.pyz"
        monkeypatch.setattr(builder, "PROJECT_ROOT", project_root)
        monkeypatch.setattr(builder, "SOURCE_PACKAGE", source_package)
        monkeypatch.setattr(builder, "OUTPUT_PATH", output_path)

        assert builder.main() == 0
        digests.append(hashlib.sha256(output_path.read_bytes()).hexdigest())

    assert digests[0] == digests[1]


def test_server_bundle_is_declared_as_installed_package_data() -> None:
    """Catches installed clients losing the embedded server required by setup."""
    import tomllib

    with PROJECT_ROOT.joinpath("pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)

    assert project["tool"]["setuptools"]["package-data"]["passwatcher"] == [
        "assets/passwatcher-server.pyz"
    ]
