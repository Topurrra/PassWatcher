from pathlib import Path
import json
import shutil
import subprocess
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
    assert "update_user_path.ps1" in text
    assert "nsExec::ExecToLog" in text
    assert "ReadRegStr" not in text
    assert "WriteRegExpandStr" not in text
    assert "DeleteRegValue HKCU \"Environment\" \"Path\"" not in text
    trusted_powershell = "$SYSDIR\\WindowsPowerShell\\v1.0\\powershell.exe"
    assert text.count(trusted_powershell) == 2
    assert "ExecToLog 'powershell.exe " not in text
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
    assert "$installed" not in text
    assert "Test-Path -LiteralPath $uninstaller -PathType Leaf" in text
    assert "Test-Path -LiteralPath $installDirectory" in text
    assert "Restore-OriginalUserPath" in text
    assert "Remove-SmokeRegistryMutations" in text
    assert '"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Passwatcher"' in text
    assert '"Software\\Passwatcher\\Installer"' in text
    assert '"PathValueExistedBeforeInstall"' in text
    assert "DeleteSubKeyTree($uninstallSubKey, $false)" in text
    assert "DeleteValue($installerStateValueName, $false)" in text
    assert "$partialUninstall.ExitCode" in text
    assert "Refusing to overwrite existing Passwatcher uninstall metadata" in text
    assert "Refusing to overwrite existing Passwatcher installer state" in text
    assert "Invoke-SmokeFallbackCleanup" in text
    assert "windows-installer-cleanup.ps1" in text


def test_smoke_fallback_attempts_registry_cleanup_after_earlier_failure(tmp_path) -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        return
    cleanup_helper = Path("tests/setup/windows-installer-cleanup.ps1").resolve()
    harness = tmp_path / "cleanup-harness.ps1"
    harness.write_text(
        f". '{str(cleanup_helper).replace("'", "''")}'\n"
        "$events = [Collections.Generic.List[string]]::new()\n"
        "$message = $null\n"
        "try {\n"
        "  Invoke-SmokeFallbackCleanup `\n"
        "    -UninstallerAction { $events.Add('uninstaller'); throw 'uninstaller failed' } `\n"
        "    -InstallDirectoryAction { $events.Add('install-directory'); throw 'directory failed' } `\n"
        "    -PathAction { $events.Add('path') } `\n"
        "    -RegistryAction { $events.Add('registry') } `\n"
        "    -ConfigAction { $events.Add('config') }\n"
        "}\n"
        "catch { $message = $_.Exception.Message }\n"
        "[PSCustomObject]@{ events = @($events); message = $message } | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)
    assert output["events"] == ["uninstaller", "install-directory", "path", "registry", "config"]
    assert "uninstaller failed" in output["message"]
    assert "directory failed" in output["message"]


def _run_path_transform(operation: str, state: str, value: str, entry: str, restore_absent=False):
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        return None
    command = [
        shell,
        "-NoProfile",
        "-NonInteractive",
        "-File",
        "packaging/update_user_path.ps1",
        "-Operation",
        operation,
        "-Entry",
        entry,
        "-TransformState",
        state,
        "-TransformValue",
        value,
        "-TransformRestoreAbsent",
        str(restore_absent).lower(),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_path_helper_uses_length_safe_registry_api() -> None:
    text = Path("packaging/update_user_path.ps1").read_text(encoding="utf-8")
    assert "[Microsoft.Win32.Registry]::CurrentUser" in text
    assert "DoNotExpandEnvironmentNames" in text
    assert "GetValueNames" in text
    assert "StringSplitOptions]::None" in text
    assert "OrdinalIgnoreCase" in text
    assert "GetEnvironmentVariable" not in text
    assert "SetEnvironmentVariable" not in text


def test_path_transform_preserves_long_values_duplicates_and_unrelated_order() -> None:
    entry = r"C:\Users\tester\AppData\Local\Programs\Passwatcher"
    unrelated = [f"C:\\tools\\segment-{index:04d}" for index in range(800)]
    original = ";".join(unrelated) + ";;"
    assert len(original) > 16 * 1024
    added = _run_path_transform("Add", "Present", original, entry)
    if added is None:
        return
    assert added == {"exists": True, "value": original + ";" + entry, "changed": True}

    with_duplicates = added["value"] + ";" + entry.upper() + ";tail;;"
    removed = _run_path_transform("Remove", "Present", with_duplicates, entry)
    assert removed == {"exists": True, "value": original + ";tail;;", "changed": True}


def test_path_transform_distinguishes_absent_empty_and_trailing_empty_segments() -> None:
    entry = r"C:\Passwatcher"
    absent_add = _run_path_transform("Add", "Absent", "", entry)
    empty_add = _run_path_transform("Add", "Present", "", entry)
    trailing_add = _run_path_transform("Add", "Present", "alpha;;", entry)
    if absent_add is None:
        return

    assert absent_add["value"] == entry
    assert empty_add["value"] == ";" + entry
    assert trailing_add["value"] == "alpha;;;" + entry
    assert _run_path_transform("Remove", "Present", absent_add["value"], entry, True) == {
        "exists": False,
        "value": None,
        "changed": True,
    }
    assert _run_path_transform("Remove", "Present", empty_add["value"], entry) == {
        "exists": True,
        "value": "",
        "changed": True,
    }
    assert _run_path_transform("Remove", "Present", trailing_add["value"], entry) == {
        "exists": True,
        "value": "alpha;;",
        "changed": True,
    }
