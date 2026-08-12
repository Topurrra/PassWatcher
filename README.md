# Passwatcher

Passwatcher is a single-user Windows password-manager CLI. It can keep a serverless local vault protected for the current Windows user with DPAPI, or use one vault on a Linux server you own through OpenSSH. The last successfully configured mode is used by every normal command.

## Install on Windows

Download `Passwatcher-Setup-<version>.exe` and run it as your normal Windows user. The NSIS installer needs no administrator access. It installs under `%LOCALAPPDATA%\Programs\Passwatcher` and adds that exact directory to your user `PATH` once.

Open a new terminal after installation so it receives the updated `PATH`, then run:

```powershell
pw --help
pw setup
```

If another program named `pw` appears earlier in the machine `PATH`, use `passwatcher` instead. The installer provides both commands, and `passwatcher` is the unambiguous fallback without modifying machine-wide tools or settings.

`pw setup` only displays the two explicit setup choices and changes nothing:

```powershell
pw setup --local
pw setup --remote
```

`pw setup --local` creates or opens `%LOCALAPPDATA%\Passwatcher\vault.db`, verifies DPAPI and SQLite, and selects it as the active vault. It needs no server and no master-password prompt.

`pw setup --remote` asks for the Linux SSH host, SSH user, port (normally `22`), and an optional identity-file path. Confirm the displayed target. Passwatcher checks connectivity, safely installs or reuses the server component, verifies the vault, and only then selects remote mode in `%APPDATA%\Passwatcher\config.toml`. That file contains mode and connection settings, never passwords or DPAPI keys.

When switching modes, Passwatcher previews non-secret counts and copies source-only credentials into the newly selected vault. Identical credentials are skipped. If the same service/label/username identity has different contents, choose once whether all source or destination versions win, or cancel without changing the active mode. After a successful local-to-remote switch, local deletion is a separate default-No prompt. Remote-to-local switching never deletes the remote vault.

Local mode supports Windows 10 or 11. Remote mode additionally requires the Windows OpenSSH `ssh` and `scp` clients; the Linux account needs Python 3.11 or newer and an already-running OpenSSH service.

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

Label and notes are genuinely optional: press Enter to leave them empty. During `pw edit`, Enter keeps an existing value and `/clear` removes an existing label or note.

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

## CSV import and export

Preview a browser or Passwatcher CSV, then import it atomically:

```powershell
pw import chrome-passwords.csv -n
pw import chrome-passwords.csv -d skip
pw import updated-passwords.csv -d update
```

`-n, --dry-run` validates and previews without changing the vault. `-d, --duplicates` accepts `skip` (the default), `update`, or `error`. `-y, --yes` skips only the final confirmation; validation, backup, and transactional behavior still apply.

Passwatcher CSV requires `service`, `username`, and `password`; `label` and `notes` are optional. Browser CSV requires `url`, `username`, and `password`; `name` and `note` are optional. Header matching is case-insensitive, common extra browser columns are ignored, and empty optional cells are valid. Duplicate identity is the case-insensitive combination of service/URL, label/name, and username.

Every import is fully parsed and validated on Windows before mutation. A mutating import creates a protected local backup or an owner-only remote backup, then commits in one SQLite transaction. Remote imports send one bounded request through SSH; the CSV file itself is never uploaded or retained. A validation, conflict, backup, protection, or database failure imports nothing.

Export a lossless Passwatcher CSV or a browser migration CSV:

```powershell
pw export passwatcher-backup.csv
pw export browser-passwords.csv -t browser
pw export passwatcher-backup.csv -f -y
```

`-t, --format` accepts `passwatcher` (the default) or `browser`. `-f, --force` permits replacement of an existing regular file, and `-y, --yes` acknowledges the plaintext warning without prompting. Passwatcher format writes `service,label,username,password,notes` and supports exact round trips. Browser format writes `name,url,username,password,note` and is intended for migration rather than lossless backup.

**CSV exports are unencrypted plaintext.** Anyone who can read the file can read every password. Do not upload or email it, secure or delete it immediately after use, and remember that spreadsheet software may interpret password-like values as formulas. Passwatcher preserves credential values exactly instead of rewriting them for spreadsheet safety. Exports use a temporary file and atomic replacement so an interrupted write does not leave a partial destination.

Run read-only diagnostics whenever setup or a command fails:

```powershell
pw doctor
pw --debug doctor
```

Use `--plain` for stable output without color, for example `pw --plain list`. `--debug` adds safe diagnostic types and exit information; it does not print request payloads or credentials.

## Multiple Windows devices

Install Passwatcher and run `pw setup` on each device with credentials for the same Linux account. Each device keeps only its own SSH connection configuration. Setup detects and reuses a compatible remote server and existing database; it never creates a second vault or clears the shared one. An older server is backed up and upgraded in place.

Local DPAPI vaults are intentionally device/user-bound and do not synchronize. Use remote mode when several devices must share one active vault, or transfer deliberately through a protected workflow. CSV exports are plaintext and require special care.

## Linux files, permissions, and backups

Passwatcher uses these fixed paths for the selected Linux user:

- `~/.local/bin/passwatcher-server`: short-lived RPC executable, mode `0700`.
- `~/.local/share/passwatcher/`: application data directory, mode `0700`.
- `~/.local/share/passwatcher/passwatcher.db`: the one SQLite vault, mode `0600`.
- `~/.local/share/passwatcher/backups/`: migration and upgrade backups, mode `0700`; backup files use mode `0600`.

Before replacing an older server or migrating an older schema, setup requires a successful timestamped backup. Database migrations are transactional. A failed backup or migration leaves the prior database in place.

## Upgrade and uninstall

Run a newer NSIS installer to upgrade. It replaces the application runtime in `%LOCALAPPDATA%\Programs\Passwatcher`, keeps a single exact user `PATH` entry, and never changes `%APPDATA%\Passwatcher\config.toml` or `%LOCALAPPDATA%\Passwatcher\vault.db`.

Uninstall from Windows Settings > Apps or run `%LOCALAPPDATA%\Programs\Passwatcher\uninstall.exe`. The uninstaller removes the application directory, registration, and exact Passwatcher `PATH` entry. It asks separately whether to remove connection settings and whether to remove the local DPAPI vault and its backups; both default to No. Silent uninstall retains both. Uninstalling the Windows client never deletes or changes the Linux vault.

## Troubleshooting

Start with `pw doctor`. In local mode it checks Windows, the local path, SQLite integrity, DPAPI decryptability, record count, and backups. In remote mode it checks configuration, `ssh`/`scp`, connectivity, protocol/schema compatibility, Linux permissions, SQLite integrity, and read access. Diagnostics do not repair a vault.

- If `pw` is not recognized just after install, close the terminal and open a new one.
- If configuration is missing or invalid, run `pw setup` again.
- If SSH fails, verify `ssh user@host`, the port, identity-file path, and host-key prompt outside Passwatcher.
- If the server is incompatible or permissions are unsafe, rerun `pw setup`; it performs the safe repair or upgrade workflow.
- Use `pw --debug doctor` for safe diagnostic metadata. Passwords and secret-bearing JSON are intentionally excluded.

## Security model

### Local mode

Every credential field is stored inside a current-user Windows DPAPI blob. Passwatcher does not use machine scope and does not store a master password or application encryption key. Normally, the same Windows user on the same device is required to decrypt the data, and DPAPI authenticates blobs against tampering.

DPAPI does not protect passwords from malware or another process already running as the same logged-on user. It is not a replacement for a strong Windows password, Windows Hello, BitLocker/device encryption, endpoint security, or backups. Some domain/roaming-profile environments can change portability, and an administrator resetting rather than changing a Windows password can make older DPAPI data unrecoverable. Do not assume that copying `vault.db` to another device creates a usable backup; keep a deliberately secured offline recovery export if your risk model requires one.

### Remote mode

Credential records are plaintext inside a permissions-protected SQLite database on the Linux server. There is no server-side encryption. Anyone who can read the database as that Linux user, including a sufficiently privileged administrator or a compromised account, can read every credential.

SSH encrypts credentials in transit. Secrets are sent in JSON on SSH standard input, never in shell command arguments. Use a dedicated, strongly protected Linux account, secure SSH keys, trusted endpoint devices, and appropriate server backups. Switching from local to remote necessarily decrypts records inside Passwatcher before sending them through SSH; values are never placed in command arguments or logs.

## Build a Windows release

Install Python 3.11+, the development dependencies (including PyInstaller 6), and NSIS 3.x with `makensis` available. From the repository root run:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1
```

The script rebuilds the deterministic Linux server zipapp, runs the complete test suite, builds `dist\passwatcher\pw.exe` plus its runtime files, and creates `dist\Passwatcher-Setup-<version>.exe`. It stops at the first failed gate.

See the [step-by-step Windows installer guide](docs/BUILDING_INSTALLER.md) for clean-machine setup, version verification, checksums, manual upgrade/uninstall checks, troubleshooting, and the guarded smoke-test commands. Run smoke tests only in a disposable Windows account or CI worker: they intentionally change that user's installation directory, registry state, and `PATH`.
