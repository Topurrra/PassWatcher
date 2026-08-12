from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import tomllib

import passwatcher
import pytest


def _nsis_function(source: str, name: str) -> str:
    match = re.search(
        rf"^Function {re.escape(name)}\s*$([\s\S]*?)^FunctionEnd\s*$",
        source,
        re.MULTILINE,
    )
    assert match is not None, f"missing NSIS function {name}"
    return match.group(1)


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


def test_uninstaller_preserves_local_dpapi_vault_by_default() -> None:
    """Catches uninstall silently destroying the only local credential vault."""
    text = Path("packaging/passwatcher.nsi").read_text(encoding="utf-8")
    assert (
        'MessageBox MB_YESNO|MB_ICONQUESTION "Remove the local DPAPI vault and its backups too?" /SD IDNO'
        in text
    )
    assert 'Delete "$LOCALAPPDATA\\Passwatcher\\vault.db"' in text
    assert 'Delete "$LOCALAPPDATA\\Passwatcher\\vault.db-wal"' in text
    assert 'Delete "$LOCALAPPDATA\\Passwatcher\\vault.db-shm"' in text
    assert 'RMDir /r "$LOCALAPPDATA\\Passwatcher"' not in text


def test_feature_release_version_is_1_0_0() -> None:
    """Catches the release workflow producing a stale versioned installer."""
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == "1.0.0"
    assert passwatcher.__version__ == "1.0.0"


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


def test_nsis_pins_command_line_overrides_to_the_canonical_install_directory() -> None:
    text = Path("packaging/passwatcher.nsi").read_text(encoding="utf-8")
    assert '!define INSTALL_DIR "$LOCALAPPDATA\\Programs\\Passwatcher"' in text
    assert 'InstallDir "${INSTALL_DIR}"' in text

    for function_name in (".onInit", "un.onInit"):
        function = _nsis_function(text, function_name)
        assert "SetShellVarContext current" in function
        assert 'StrCmp $INSTDIR "${INSTALL_DIR}"' in function
        assert 'StrCpy $INSTDIR "${INSTALL_DIR}"' in function
        assert function.index("StrCmp $INSTDIR") < function.index("StrCpy $INSTDIR")
        assert "Abort" in function


def test_nsis_deletes_only_known_application_artifacts() -> None:
    text = Path("packaging/passwatcher.nsi").read_text(encoding="utf-8")
    assert 'RMDir /r "$INSTDIR"' not in text
    assert text.count('Delete "$INSTDIR\\pw.exe"') == 2
    assert text.count('Delete "$INSTDIR\\uninstall.exe"') == 2
    assert text.count('RMDir /r "$INSTDIR\\_internal"') == 2
    assert text.count('RMDir "$INSTDIR"') == 2
    assert 'RMDir /r "$APPDATA\\Passwatcher"' not in text
    assert 'Delete "$APPDATA\\Passwatcher\\config.toml"' in text
    assert 'RMDir "$APPDATA\\Passwatcher"' in text


def test_nsis_cleans_empty_installer_state_keys_without_deleting_unrelated_state() -> None:
    text = Path("packaging/passwatcher.nsi").read_text(encoding="utf-8")
    assert 'EnumRegValue $0 HKCU "Software\\Passwatcher\\Installer" 0' in text
    assert 'EnumRegValue $0 HKCU "Software\\Passwatcher" 0' in text
    assert 'DeleteRegKey /ifempty HKCU "Software\\Passwatcher\\Installer\\"' in text
    assert 'DeleteRegKey /ifempty HKCU "Software\\Passwatcher\\"' in text
    assert 'DeleteRegKey HKCU "Software\\Passwatcher\\Installer"' not in text
    assert 'DeleteRegKey HKCU "Software\\Passwatcher"' not in text


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


def test_nsis_compiles_from_the_repository_root(tmp_path) -> None:
    makensis = shutil.which("makensis")
    if makensis is None:
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable)
            if root is not None:
                candidate = Path(root, "NSIS", "makensis.exe")
                if candidate.is_file():
                    makensis = str(candidate)
                    break
    if makensis is None:
        pytest.skip("NSIS makensis is not installed")

    project = tmp_path / "project"
    packaging = project / "packaging"
    payload = project / "dist" / "passwatcher"
    packaging.mkdir(parents=True)
    payload.mkdir(parents=True)
    shutil.copy2("packaging/passwatcher.nsi", packaging / "passwatcher.nsi")
    shutil.copy2("packaging/update_user_path.ps1", packaging / "update_user_path.ps1")
    (payload / "pw.exe").write_bytes(b"passwatcher compiler fixture")

    result = subprocess.run(
        [makensis, "/V4", "/DVERSION=0.0.0", "packaging/passwatcher.nsi"],
        cwd=project,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning 6000" not in result.stdout + result.stderr
    assert 'File: "pw.exe"->"passwatcher.exe"' in result.stdout
    assert (project / "dist" / "Passwatcher-Setup-0.0.0.exe").is_file()


def test_nsis_owns_the_passwatcher_command_alias_lifecycle() -> None:
    text = Path("packaging/passwatcher.nsi").read_text(encoding="utf-8")
    assert 'File /oname=passwatcher.exe "${BUILD_DIR}\\pw.exe"' in text
    assert text.count('Delete "$INSTDIR\\passwatcher.exe"') == 2


def test_smoke_script_requires_disposable_context_and_refuses_existing_config() -> None:
    text = Path("tests/setup/windows-installer-smoke.ps1").read_text(encoding="utf-8")
    assert "PASSWATCHER_SMOKE_ISOLATED_USER" in text
    assert "Refusing to use an existing Passwatcher config directory" in text
    assert "Refusing to use an existing Passwatcher local data directory" in text
    assert "Silent uninstall did not retain the local DPAPI vault sentinel" in text
    assert text.count("Invoke-CheckedProcess -FilePath $installerPath") >= 2
    assert 'Invoke-CheckedProcess -FilePath $resolvedPw -ArgumentList @("--help")' in text
    assert "uninstall.exe" in text
    assert "$installed" not in text
    assert "Test-Path -LiteralPath $uninstaller -PathType Leaf" in text
    assert "Test-Path -LiteralPath $installDirectory" in text
    assert "Restore-OriginalUserPath" in text
    assert "Remove-SmokeRegistryMutations" in text
    assert '"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Passwatcher"' in text
    assert '"Software\\Passwatcher\\Installer"' in text
    path_helper = Path("packaging/update_user_path.ps1").read_text(encoding="utf-8")
    assert '"PathValueExistedBeforeInstall"' in path_helper
    assert '"PathEntryAddedByInstall"' in path_helper
    assert "DeleteSubKeyTree($uninstallSubKey, $false)" in text
    assert "DeleteSubKeyTree($installerProductSubKey, $false)" in text
    assert "$partialUninstall.ExitCode" in text
    assert "Refusing to overwrite existing Passwatcher uninstall metadata" in text
    assert "Refusing to overwrite existing Passwatcher installer state" in text
    assert "Invoke-SmokeFallbackCleanup" in text
    assert "windows-installer-cleanup.ps1" in text


def test_smoke_verifies_exact_launchers_and_reports_bare_pw_collisions() -> None:
    text = Path("tests/setup/windows-installer-smoke.ps1").read_text(encoding="utf-8")
    assert '$passwatcherExecutable = Join-Path $installDirectory "passwatcher.exe"' in text
    assert "Invoke-CheckedProcess -FilePath $pwExecutable" in text
    assert "Invoke-CheckedProcess -FilePath $passwatcherExecutable" in text
    assert "Get-Command passwatcher.exe -CommandType Application" in text
    assert "Passwatcher command collision:" in text
    assert "Use 'passwatcher' as the unambiguous command." in text
    assert "$previousProcessPath = $env:Path" in text
    assert "$env:Path = $previousProcessPath" in text
    assert text.count("Select-Object -First 1 -ExpandProperty Source") == 2


def test_smoke_has_guarded_override_and_unexpected_file_canaries() -> None:
    text = Path("tests/setup/windows-installer-safety-smoke.ps1").read_text(encoding="utf-8")
    assert 'if ($env:PASSWATCHER_SMOKE_ISOLATED_USER -ne "1")' in text
    assert 'if ($env:PASSWATCHER_SMOKE_DESTRUCTIVE_CANARIES -ne "1")' in text
    assert '@("/S", "/D=$installerOverrideDirectory")' in text
    assert '@("/S", "/D=$uninstallerDOverrideDirectory")' in text
    assert '@("/S", "_?=$uninstallerQuestionOverrideDirectory")' in text
    assert text.count("Assert-CanarySurvived") >= 3
    assert "$unexpectedInstallFile" in text
    assert '$installedPasswatcher = Join-Path $installDirectory "passwatcher.exe"' in text
    assert "-not (Test-Path -LiteralPath $installedPasswatcher)" in text
    assert "Installer did not create passwatcher.exe" in text
    assert "Uninstall deleted the unexpected install-directory sentinel" in text
    assert "PathEntryAddedByInstall" in text
    assert "$preexistingPathValue" in text
    assert "$unrelatedInstallerStateValueName" in text
    assert "Get-PathOwnershipValue) -ne 1" in text


def test_smoke_fallback_attempts_registry_cleanup_after_earlier_failure(tmp_path) -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        return
    cleanup_helper = Path("tests/setup/windows-installer-cleanup.ps1").resolve()
    escaped_cleanup_helper = str(cleanup_helper).replace("'", "''")
    harness = tmp_path / "cleanup-harness.ps1"
    harness.write_text(
        f". '{escaped_cleanup_helper}'\n"
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


def _run_path_transform(
    operation: str,
    state: str,
    value: str,
    entry: str,
    restore_absent: bool = False,
    *,
    ownership_known: bool = False,
    entry_added_by_install: bool = False,
    legacy_path_value_existed: bool | None = None,
):
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
        "-TransformOwnershipKnown",
        str(ownership_known).lower(),
        "-TransformEntryAddedByInstall",
        str(entry_added_by_install).lower(),
        "-TransformLegacyPathValueExisted",
        (
            "Unknown"
            if legacy_path_value_existed is None
            else ("Present" if legacy_path_value_existed else "Absent")
        ),
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
    assert added == {
        "exists": True,
        "value": original + ";" + entry,
        "changed": True,
        "entryAddedByInstall": True,
    }

    with_duplicates = added["value"] + ";" + entry.upper() + ";tail;;"
    removed = _run_path_transform(
        "Remove", "Present", with_duplicates, entry, entry_added_by_install=True
    )
    assert removed == {
        "exists": True,
        "value": original + ";" + entry.upper() + ";tail;;",
        "changed": True,
        "entryAddedByInstall": False,
    }


def test_path_transform_preserves_unowned_matches_and_removes_one_owned_occurrence() -> None:
    entry = r"C:\Passwatcher"
    preexisting = r"alpha;C:\Passwatcher;C:\PASSWATCHER;omega;;"

    fresh_install = _run_path_transform("Add", "Present", preexisting, entry)
    if fresh_install is None:
        return
    assert fresh_install == {
        "exists": True,
        "value": preexisting,
        "changed": False,
        "entryAddedByInstall": False,
    }
    assert _run_path_transform(
        "Remove", "Present", preexisting, entry, entry_added_by_install=False
    ) == {
        "exists": True,
        "value": preexisting,
        "changed": False,
        "entryAddedByInstall": False,
    }

    owned_install = _run_path_transform("Add", "Present", "alpha", entry)
    assert owned_install == {
        "exists": True,
        "value": r"alpha;C:\Passwatcher",
        "changed": True,
        "entryAddedByInstall": True,
    }
    owned_with_later_duplicate = owned_install["value"] + r";C:\PASSWATCHER;omega;;"
    assert _run_path_transform(
        "Remove",
        "Present",
        owned_with_later_duplicate,
        entry,
        entry_added_by_install=True,
    ) == {
        "exists": True,
        "value": r"alpha;C:\PASSWATCHER;omega;;",
        "changed": True,
        "entryAddedByInstall": False,
    }


def test_path_transform_preserves_owned_state_across_an_idempotent_upgrade() -> None:
    entry = r"C:\Passwatcher"
    result = _run_path_transform(
        "Add",
        "Present",
        r"alpha;C:\Passwatcher",
        entry,
        ownership_known=True,
        entry_added_by_install=True,
    )
    if result is None:
        return
    assert result == {
        "exists": True,
        "value": r"alpha;C:\Passwatcher",
        "changed": False,
        "entryAddedByInstall": True,
    }


def test_path_transform_migrates_only_provable_legacy_ownership() -> None:
    entry = r"C:\Passwatcher"
    legacy_value = r"alpha;C:\Passwatcher"

    proven_owned = _run_path_transform(
        "Add",
        "Present",
        legacy_value,
        entry,
        legacy_path_value_existed=False,
    )
    if proven_owned is None:
        return
    assert proven_owned == {
        "exists": True,
        "value": legacy_value,
        "changed": False,
        "entryAddedByInstall": True,
    }
    assert _run_path_transform(
        "Add",
        "Present",
        legacy_value,
        entry,
        legacy_path_value_existed=True,
    ) == {
        "exists": True,
        "value": legacy_value,
        "changed": False,
        "entryAddedByInstall": False,
    }


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
    assert absent_add["entryAddedByInstall"] is True
    assert empty_add["entryAddedByInstall"] is True
    assert trailing_add["entryAddedByInstall"] is True
    assert _run_path_transform(
        "Remove", "Present", absent_add["value"], entry, True, entry_added_by_install=True
    ) == {
        "exists": False,
        "value": None,
        "changed": True,
        "entryAddedByInstall": False,
    }
    assert _run_path_transform(
        "Remove", "Present", empty_add["value"], entry, entry_added_by_install=True
    ) == {
        "exists": True,
        "value": "",
        "changed": True,
        "entryAddedByInstall": False,
    }
    assert _run_path_transform(
        "Remove", "Present", trailing_add["value"], entry, entry_added_by_install=True
    ) == {
        "exists": True,
        "value": "alpha;;",
        "changed": True,
        "entryAddedByInstall": False,
    }
