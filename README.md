# Passwatcher

Passwatcher is a single-user Windows password-manager CLI backed by one vault on a Linux server you own. The Windows client reaches the vault through your existing OpenSSH login. It does not run an HTTP service or store credential records on Windows.

## Install on Windows

Download `Passwatcher-Setup-<version>.exe` and run it as your normal Windows user. The NSIS installer needs no administrator access. It installs under `%LOCALAPPDATA%\Programs\Passwatcher` and adds that exact directory to your user `PATH` once.

Open a new terminal after installation so it receives the updated `PATH`, then run:

```powershell
pw --help
pw setup
```

`pw setup` asks for the Linux SSH host, SSH user, port (normally `22`), and an optional identity-file path. Confirm the displayed target. Passwatcher checks connectivity, safely installs or reuses the server component, verifies the vault, and only then writes `%APPDATA%\Passwatcher\config.toml`. That file contains connection settings, not passwords.

Windows 10 or 11 must have the OpenSSH `ssh` and `scp` clients available. The Linux account needs Python 3.11 or newer and an already-running OpenSSH service.

## Commands

Lookup is the default command. Query words are joined and matched case-insensitively against service, label, and username:

```powershell
pw github
pw github work
pw nika@example.com
```

Lookup behavior is exact:

- Zero matches: prints a not-found message, copies nothing, and exits with status 1.
- One match: displays the service, label, username, and password, then copies only the password to the clipboard.
- Many matches: displays every matching record, including passwords, in deterministic order and copies nothing. Refine the query to select one.

List records without revealing passwords, or explicitly include them:

```powershell
pw list
pw list --secrets
```

Add a record interactively, or provide field values as flags:

```powershell
pw add
pw add --service github.com --label personal --username nika@example.com --password "typed-secret" --notes "main account"
```

Edit and delete search first. If several records match, choose from the numbered list. Both operations ask for confirmation; delete defaults to no.

```powershell
pw edit "github work"
pw edit github --username new-address@example.com
pw delete "github personal"
```

Generate a password and copy it to the clipboard:

```powershell
pw generate
pw generate --length 32
pw generate --length 20 --no-symbols
```

Run read-only diagnostics whenever setup or a command fails:

```powershell
pw doctor
pw --debug doctor
```

Use `--plain` for stable output without color, for example `pw --plain list`. `--debug` adds safe diagnostic types and exit information; it does not print request payloads or credentials.

## Multiple Windows devices

Install Passwatcher and run `pw setup` on each device with credentials for the same Linux account. Each device keeps only its own SSH connection configuration. Setup detects and reuses a compatible remote server and existing database; it never creates a second vault or clears the shared one. An older server is backed up and upgraded in place.

## Linux files, permissions, and backups

Passwatcher uses these fixed paths for the selected Linux user:

- `~/.local/bin/passwatcher-server`: short-lived RPC executable, mode `0700`.
- `~/.local/share/passwatcher/`: application data directory, mode `0700`.
- `~/.local/share/passwatcher/passwatcher.db`: the one SQLite vault, mode `0600`.
- `~/.local/share/passwatcher/backups/`: migration and upgrade backups, mode `0700`; backup files use mode `0600`.

Before replacing an older server or migrating an older schema, setup requires a successful timestamped backup. Database migrations are transactional. A failed backup or migration leaves the prior database in place.

## Upgrade and uninstall

Run a newer NSIS installer to upgrade. It replaces the application runtime in `%LOCALAPPDATA%\Programs\Passwatcher`, keeps a single exact user `PATH` entry, and never changes `%APPDATA%\Passwatcher\config.toml`.

Uninstall from Windows Settings > Apps or run `%LOCALAPPDATA%\Programs\Passwatcher\uninstall.exe`. The uninstaller removes the application directory, registration, and exact Passwatcher `PATH` entry. It then asks whether to remove local Passwatcher connection settings. Choose No to keep them for a later reinstall. Silent uninstall retains configuration by default. Uninstalling the Windows client never deletes or changes the Linux vault.

## Troubleshooting

Start with `pw doctor`. It checks the local configuration, `ssh`/`scp`, connectivity, protocol and schema compatibility, Linux permissions, SQLite integrity, and read access without modifying the vault.

- If `pw` is not recognized just after install, close the terminal and open a new one.
- If configuration is missing or invalid, run `pw setup` again.
- If SSH fails, verify `ssh user@host`, the port, identity-file path, and host-key prompt outside Passwatcher.
- If the server is incompatible or permissions are unsafe, rerun `pw setup`; it performs the safe repair or upgrade workflow.
- Use `pw --debug doctor` for safe diagnostic metadata. Passwords and secret-bearing JSON are intentionally excluded.

## Security model

Passwatcher is for one person using a Linux server and account they own and control. Credential records are plaintext inside a permissions-protected SQLite database on that server. There is no server-side encryption. Anyone who can read the database as that Linux user (including a sufficiently privileged administrator or a compromised account) can read every credential.

SSH encrypts credentials in transit. Secrets are sent in JSON on SSH standard input, never in shell command arguments. Windows stores only SSH connection settings; it does not cache vault records. Use a dedicated, strongly protected Linux account, SSH keys with appropriate filesystem permissions, trusted endpoint devices, and server backups appropriate for plaintext secrets. If you do not accept plaintext storage on an owned server, do not use Passwatcher.

## Build a Windows release

Install Python 3.11+, the development dependencies (including PyInstaller 6), and NSIS 3.x with `makensis` on `PATH`. From the repository root run:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1
```

The script rebuilds the deterministic Linux server zipapp, runs the complete test suite, builds `dist\passwatcher\pw.exe` plus its runtime files, and creates `dist\Passwatcher-Setup-<version>.exe`. It stops at the first failed gate.

The installer smoke test is intentionally guarded because it changes the current user's install directory and user `PATH`. Run it only inside a disposable Windows user account or CI worker whose Passwatcher paths do not exist:

```powershell
$env:PASSWATCHER_SMOKE_ISOLATED_USER = "1"
powershell -ExecutionPolicy Bypass -File tests/setup/windows-installer-smoke.ps1 -Installer "dist/Passwatcher-Setup-*.exe"
```

The smoke test refuses an existing Passwatcher installation or config directory. It installs twice, resolves `pw --help` through a fresh process and the user `PATH`, verifies configuration preservation, silently uninstalls with configuration retention, checks cleanup, and removes only the sentinel config directory that it created.
