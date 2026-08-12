# Building the Passwatcher Windows Installer

This guide builds the same Windows installer published by the release workflow. Run every command from PowerShell as a normal Windows user unless a package installer explicitly requests elevation.

## 1. Install the prerequisites

Install:

- Git for Windows
- Python 3.11 or newer, including the Python launcher (`py`)
- NSIS 3.x, including `makensis.exe`

You can install NSIS from [nsis.sourceforge.io](https://nsis.sourceforge.io/Download), or with Chocolatey from an elevated PowerShell:

```powershell
choco install nsis -y
```

If you use the graphical NSIS installer, add its directory—normally `C:\Program Files (x86)\NSIS` or `C:\Program Files\NSIS`—to `PATH`. Close PowerShell and open a new one after changing `PATH`.

Verify the tools:

```powershell
git --version
py -3.11 --version
makensis /VERSION
```

Any installed Python version newer than 3.11 is also supported. Replace `py -3.11` below if you intentionally use another version.

## 2. Clone Passwatcher

```powershell
git clone https://github.com/Topurrra/PassWatcher.git
Set-Location PassWatcher
```

To build an existing release, check out its tag first—for example:

```powershell
git checkout v0.1.0
```

## 3. Create the build environment

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell blocks virtual-environment activation, enable locally created scripts for your Windows account, then retry:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Alternatively, leave the policy unchanged and replace `python` in later commands with `.\.venv\Scripts\python.exe`.

## 4. Verify the source version

The Git release tag, `pyproject.toml`, and `src\passwatcher\__init__.py` must contain the same version. For a `v0.1.0` release:

```powershell
python tools/verify_release_version.py v0.1.0
```

The command must print only `0.1.0`. When preparing a new release, update both source declarations, commit them, and use the matching `v<version>` tag.

## 5. Build and test the installer

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1
```

The build script stops at the first failed gate. In order, it:

1. rebuilds the deterministic Linux server bundle embedded in the client;
2. runs the complete Python test suite;
3. builds the Windows runtime with PyInstaller;
4. verifies that `pw.exe` exists;
5. reads and validates the project version;
6. compiles the per-user NSIS installer.

Successful output is written beneath `dist`:

```text
dist\passwatcher\pw.exe
dist\passwatcher\_internal\...
dist\Passwatcher-Setup-<version>.exe
```

Inspect the output and calculate the public installer checksum:

```powershell
Get-ChildItem dist
Get-FileHash -Algorithm SHA256 dist\Passwatcher-Setup-0.1.0.exe
```

Publish the versioned installer, not only `pw.exe`: the executable depends on its collected runtime files until NSIS packages them. During installation, NSIS also creates the `passwatcher.exe` command alias from `pw.exe`.

## 6. Test installation manually

Run the installer as your normal Windows user. It installs into `%LOCALAPPDATA%\Programs\Passwatcher` without administrator access.

Close the terminal after installation and open a new PowerShell so it receives the updated user `PATH`. Then run:

```powershell
pw --help
passwatcher --help
```

Install the same version again to exercise the upgrade path. Existing `%APPDATA%\Passwatcher\config.toml` must survive. Finally, uninstall from Windows Settings > Apps and verify both commands no longer resolve in a newly opened terminal.

## 7. Run the guarded smoke tests

These tests install, upgrade, alter the current user's `PATH`, and uninstall. Run them only inside a disposable Windows user account or disposable CI runner. They deliberately refuse an existing Passwatcher installation, configuration, registry state, or exact `PATH` entry.

Standard installer lifecycle test:

```powershell
$env:PASSWATCHER_SMOKE_ISOLATED_USER = "1"
powershell -ExecutionPolicy Bypass -File tests/setup/windows-installer-smoke.ps1 -Installer "dist/Passwatcher-Setup-*.exe"
```

Destructive-option boundary and canary test:

```powershell
$env:PASSWATCHER_SMOKE_ISOLATED_USER = "1"
$env:PASSWATCHER_SMOKE_DESTRUCTIVE_CANARIES = "1"
powershell -ExecutionPolicy Bypass -File tests/setup/windows-installer-safety-smoke.ps1 -Installer "dist/Passwatcher-Setup-*.exe"
```

Do not set these guards merely to bypass a refusal on your everyday Windows account. Use a disposable account whose Passwatcher paths contain no valued data.

## Troubleshooting

### `makensis` was not found

Install NSIS 3.x and open a new terminal. Check `makensis /VERSION`. The build script also checks the standard 32-bit and 64-bit NSIS installation directories when the command is absent from `PATH`.

### A release version does not match

Run `python tools/verify_release_version.py v<version>`. Update both `pyproject.toml` and `src\passwatcher\__init__.py`, commit the change, and build from the matching tag. Do not rename an installer to hide a mismatch.

### PyInstaller cannot replace a file in `dist`

Close running copies of `pw.exe`, Explorer preview windows, antivirus scan dialogs, and terminals whose current directory is inside `dist`. Then rerun the build. Do not delete unrelated repository files.

### `pw` is not recognized after installation

Open a new terminal. Existing processes keep their old environment. If another application already owns `pw`, use the installed `passwatcher` alias.

### A smoke test refuses to start

Read the refusal literally. The scripts stop when the current account is not disposable or when they detect Passwatcher state they do not own. Use a fresh disposable Windows account or GitHub-hosted runner instead of deleting real settings to satisfy the test.

### The test suite reports temporary-directory access errors

Ensure no previous pytest process is still running. On managed Windows environments, run the build from a workspace where Python may create temporary directories, or configure the runner's temp directory before starting Python.
