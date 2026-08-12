# Passwatcher

Passwatcher is a password manager for Windows. It can use a local vault protected by Windows DPAPI or a remote vault on your own Linux server over SSH. The last successful setup becomes the active vault for every command.

## Install

Download `Passwatcher-Setup-<version>.exe` from GitHub Releases and run it as your normal Windows user. Open a new terminal after installation, then run:

```powershell
pw --help
pw setup
```

The installer provides both `pw` and `passwatcher`. Use `passwatcher` if another program already uses the shorter command.

Local mode requires Windows 10 or 11. Remote mode also requires the Windows OpenSSH `ssh` and `scp` clients. The Linux account needs OpenSSH and Python 3.11 or newer.

## Choose a vault

Running `pw setup` only shows the two choices. It does not change anything.

```powershell
pw setup -l
pw setup -r
```

`-l, --local` creates or opens `%LOCALAPPDATA%\Passwatcher\vault.db`. Every credential field is protected with current-user Windows DPAPI. Local mode needs no server or master password.

`-r, --remote` configures a Linux vault over SSH. Host and user are required. Port and identity file are optional. Connection settings are saved in `%APPDATA%\Passwatcher\config.toml`, never passwords or encryption keys.

When switching modes, Passwatcher compares both vaults and previews non-secret counts. Source-only credentials are copied and identical credentials are skipped. For conflicts, choose source wins, destination wins, or cancel. The choice applies to the whole migration. A cancelled or failed migration keeps the previous vault active.

After a successful local-to-remote migration, Passwatcher asks whether to delete the local vault. The default is No. Remote-to-local migration never deletes the remote vault.

## Use Passwatcher

Search is the default command:

```powershell
pw github
pw github work
```

A single match displays the credential and copies its password. Multiple matches are displayed without copying. Other common commands are:

```powershell
pw list
pw list --secrets
pw add
pw edit github
pw delete github
pw generate --length 32
pw doctor
```

`label` and `notes` are optional. Press Enter to leave them empty. During editing, Enter keeps the existing value and `/clear` removes it.

Use `pw --plain list` for stable output without color. Use `pw --debug doctor` for safe diagnostic details. Debug output excludes credentials and secret request data.

## CSV import and export

Passwatcher imports its own lossless CSV format and common browser password CSV files:

```powershell
pw import chrome-passwords.csv -n
pw import chrome-passwords.csv -d skip
pw import updated-passwords.csv -d update
```

`-n, --dry-run` validates without writing. `-d, --duplicates` accepts `skip`, `update`, or `error`. `-y, --yes` skips the final confirmation only.

Passwatcher CSV uses `service,label,username,password,notes`. Browser CSV uses `name,url,username,password,note`. Label, name, notes, and note are optional, and empty optional cells are valid.

Export either format:

```powershell
pw export passwatcher-backup.csv
pw export browser-passwords.csv -t browser
pw export passwatcher-backup.csv -f -y
```

The default `passwatcher` format supports exact round trips. The `browser` format is intended for browser migration. `-f, --force` replaces an existing regular file.

CSV exports are unencrypted plaintext. Anyone who can read the file can read every password. Store exports securely, do not email or upload them, and remove them when they are no longer needed.

## Security and data safety

Local mode uses current-user DPAPI without machine scope or a stored application key. Normally, the same Windows user on the same device is required to decrypt the vault. DPAPI does not protect against malware or another process running as that signed-in user. Use a strong Windows login, device encryption, and reliable backups.

A copied local database may not be recoverable on another device or after some Windows account resets. Keep a securely stored recovery export if your risk model requires one.

Remote credentials are plaintext inside a permissions-restricted SQLite database on the Linux server. SSH protects them in transit, but the Linux user, a privileged administrator, or a compromised server can read them. Use a dedicated account, secure SSH keys, and protected backups.

## Upgrade and uninstall

Run a newer installer to upgrade. Configuration and vault data are preserved.

Uninstall through Windows Settings or `%LOCALAPPDATA%\Programs\Passwatcher\uninstall.exe`. Removing configuration and removing the local vault are separate prompts. Both default to No. Silent uninstall preserves both. Uninstall never deletes the remote Linux vault.

## Build and release

Install Git, Python 3.11 or newer, and NSIS 3.x. Ensure `makensis` is available, then run from PowerShell:

```powershell
git clone https://github.com/Topurrra/PassWatcher.git
Set-Location PassWatcher
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
makensis /VERSION
python tools/verify_release_version.py v0.2.0
powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1
Get-FileHash -Algorithm SHA256 dist\Passwatcher-Setup-0.2.0.exe
```

The build runs the complete test suite and produces `dist\passwatcher\pw.exe` and `dist\Passwatcher-Setup-<version>.exe`.

Installer smoke tests change the current user's installation, registry, and PATH. Run them only in a disposable Windows account or CI runner:

```powershell
$env:PASSWATCHER_SMOKE_ISOLATED_USER = "1"
powershell -ExecutionPolicy Bypass -File tests/setup/windows-installer-smoke.ps1 -Installer "dist/Passwatcher-Setup-*.exe"

$env:PASSWATCHER_SMOKE_DESTRUCTIVE_CANARIES = "1"
powershell -ExecutionPolicy Bypass -File tests/setup/windows-installer-safety-smoke.ps1 -Installer "dist/Passwatcher-Setup-*.exe"
```

For a release, update the version in `pyproject.toml` and `src/passwatcher/__init__.py`, commit it, and push `main`. Create and push the matching tag:

```powershell
python tools/verify_release_version.py v<version>
git push origin main
git tag -a v<version> -m "Passwatcher <version>"
git push origin v<version>
```

The GitHub release workflow verifies the tag, builds the installer, runs both guarded smoke suites on a disposable Windows runner, creates a SHA-256 checksum, and publishes both files. Manual workflow dispatch accepts an existing tag and refuses to overwrite an existing release.
