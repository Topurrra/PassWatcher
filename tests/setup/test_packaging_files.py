from pathlib import Path
import tomllib

import passwatcher


def test_nsis_is_per_user_and_updates_user_path() -> None:
    text = Path("packaging/passwatcher.nsi").read_text(encoding="utf-8")
    assert "RequestExecutionLevel user" in text
    assert "$LOCALAPPDATA\\Programs\\Passwatcher" in text
    assert "HKCU" in text and "Environment" in text and "Path" in text
    assert "WM_SETTINGCHANGE" in text


def test_installer_preserves_config_by_default() -> None:
    text = Path("packaging/passwatcher.nsi").read_text(encoding="utf-8")
    assert "$APPDATA\\Passwatcher\\config.toml" in text
    assert "MessageBox MB_YESNO" in text


def test_package_version_matches_project_metadata() -> None:
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert passwatcher.__version__ == metadata["project"]["version"]


def test_pyinstaller_contract_includes_runtime_and_server_bundle() -> None:
    text = Path("packaging/passwatcher.spec").read_text(encoding="utf-8")
    assert "name=\"pw\"" in text
    assert "console=True" in text
    assert "passwatcher-server.pyz" in text
    assert "copy_metadata(\"typer\")" in text
    assert "copy_metadata(\"rich\")" in text
    assert "__version__" in text


def test_nsis_has_idempotent_path_and_registered_uninstaller_contract() -> None:
    text = Path("packaging/passwatcher.nsi").read_text(encoding="utf-8")
    assert "Function AddToUserPath" in text
    assert "Function un.RemoveFromUserPath" in text
    assert "IfErrors remove_path_done" in text
    assert "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Passwatcher" in text
    assert 'MessageBox MB_YESNO|MB_ICONQUESTION "Remove local Passwatcher connection settings too?" /SD IDNO' in text


def test_build_script_runs_release_gates_in_order() -> None:
    text = Path("tools/build_windows.ps1").read_text(encoding="utf-8")
    commands = [
        "tools/build_server_zipapp.py",
        "-m pytest -q",
        "-m PyInstaller --clean --noconfirm",
        "/DVERSION=$version",
    ]
    positions = [text.index(command) for command in commands]
    assert positions == sorted(positions)
    assert "$LASTEXITCODE" in text
    assert "--basetemp" in text
    assert "-p no:cacheprovider" in text
    assert "New-Item -ItemType Directory -Path $pytestParent" in text


def test_smoke_script_requires_disposable_context_and_refuses_existing_config() -> None:
    text = Path("tests/setup/windows-installer-smoke.ps1").read_text(encoding="utf-8")
    assert "PASSWATCHER_SMOKE_ISOLATED_USER" in text
    assert "Refusing to use an existing Passwatcher config directory" in text
    assert text.count("Invoke-CheckedProcess -FilePath $installerPath") >= 2
    assert "pw --help" in text
    assert "uninstall.exe" in text
