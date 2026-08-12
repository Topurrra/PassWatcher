# Passwatcher Local Vault and Release Automation Design

## Goal

Add a Windows-only, serverless Passwatcher mode backed by a current-user DPAPI-protected local SQLite vault, preserve the existing SSH-backed mode, provide safe switching and migration between them, document installer creation step by step, and publish verified Windows installers through GitHub Releases.

## Scope

This change adds:

- `pw setup` as a non-mutating chooser that prints the two explicit setup commands
- `pw setup --local` to create, verify, migrate into, and activate the local DPAPI vault
- `pw setup --remote` to create or reuse, verify, migrate into, and activate an SSH vault
- a persisted active backend so the most recently completed setup mode wins
- safe one-way migration into the newly selected backend
- whole-migration conflict resolution without displaying secret values
- an optional, explicit deletion prompt for the old local vault after local-to-remote migration
- local-mode diagnostics through `pw doctor`
- a detailed Windows installer build guide
- a tag-driven and manually dispatchable GitHub Actions release workflow

The existing lookup, list, CRUD, generator, CSV import, and CSV export commands retain their names and presentation. They operate on whichever backend is active.

## Approaches Considered

### 1. Direct local service behind a shared service protocol — selected

The CLI depends on a small credential-service protocol. The existing `PasswordService` remains the SSH implementation. A new `LocalPasswordService` performs equivalent operations directly against an encrypted local store. This avoids serializing local calls through JSON or pretending that an in-process vault is an SSH server.

This creates one explicit backend-selection point and keeps storage-specific behavior out of CLI commands.

### 2. In-process RPC transport

A local transport could accept the existing protocol JSON and route it to an in-process RPC dispatcher. That would reuse `PasswordService`, but it would add needless encode/decode work and couple the Windows vault schema to the Linux server dispatcher.

### 3. A second local-only command tree

Commands such as `pw local add` could avoid backend selection. This would duplicate every user workflow, make CSV behavior inconsistent, and require users to remember a command prefix. It is rejected.

## User Interface

### Setup chooser

Running `pw setup` without a mode does not prompt, connect, create files, or modify configuration. It renders the existing Midnight Neon or plain style with exactly two choices:

```text
Choose a vault setup mode:
  pw setup --local   Store a DPAPI-protected vault on this Windows device
  pw setup --remote  Use a vault on a Linux server through SSH
```

The mode flags also have concise aliases:

- `-l, --local`
- `-r, --remote`

Supplying both flags is a usage error and performs no work.

### Active backend

The configuration contains `backend = "local"` or `backend = "remote"`. The last setup operation that completes setup, health verification, and any required migration becomes active. Failed or cancelled setup leaves the previous active backend unchanged.

Legacy connection files without `backend` are interpreted as remote configurations. This preserves upgrades from existing releases.

Every normal command resolves the active service once:

- local selects `LocalPasswordService`
- remote selects the existing `PasswordService(SshTransport(...))`

No command needs a recurring `--local` or `--remote` flag.

### Local setup

`pw setup --local` is supported only on Windows. It creates or opens the fixed local vault, verifies DPAPI and SQLite health, and, when the previously active backend is remote, offers a migration from remote into local. A newly initialized local vault is not made active until health and migration complete.

The remote vault is never deleted when switching to local.

### Remote setup

`pw setup --remote` runs the existing SSH prompts, target confirmation, connection check, server install/upgrade/reuse, and health verification. When the previously active backend is local—or no configuration exists but a local vault already exists—it previews and offers migration from local into the chosen remote vault.

After successful local-to-remote migration and activation, Passwatcher asks whether to delete the old local vault. The prompt defaults to no and explicitly includes the local database and its local backups. Yes deletes only Passwatcher-owned local vault artifacts. No keeps them, but remote remains active because it was the last successfully selected backend.

Re-running setup for the already active backend repairs or verifies that backend and does not merge a stale inactive vault into it.

## Configuration and Paths

The roaming configuration remains:

```text
%APPDATA%\Passwatcher\config.toml
```

Its new shape is:

```toml
backend = "remote"

[remote]
host = "vault.example"
user = "nika"
port = 22
identity_file = "C:/keys/vault"
```

For local-only users, the `[remote]` table is absent. Switching to local preserves remembered remote connection settings so switching back can prefill the prompts. Configuration never stores passwords or DPAPI material.

The non-roaming local data paths are:

```text
%LOCALAPPDATA%\Passwatcher\vault.db
%LOCALAPPDATA%\Passwatcher\backups\passwatcher-local-<UTC timestamp>-v1.db
```

The path comes from `platformdirs.user_data_dir(..., roaming=False)`. Tests inject paths and never touch the real profile.

## DPAPI Boundary

`DpapiProtector` is a focused Windows adapter exposing:

```python
protect(plaintext: bytes) -> bytes
unprotect(ciphertext: bytes) -> bytes
```

It calls `CryptProtectData` and `CryptUnprotectData` through `ctypes` with current-user scope. It does not set `CRYPTPROTECT_LOCAL_MACHINE`. It uses `CRYPTPROTECT_UI_FORBIDDEN`, passes no prompt structure, and frees Windows-allocated output with `LocalFree` on every path. Failures become safe `ProtectionError` messages containing no input or output bytes.

No application-supplied entropy is used. A compiled constant would not be secret and would add a second compatibility input without materially strengthening current-user DPAPI.

Microsoft documents that current-user DPAPI normally limits decryption to the same logged-on user on the same computer, authenticates protected blobs against tampering, and can become unrecoverable after some administrator password-reset scenarios. The README states these limits and recommends an encrypted CSV kept offline when recoverability matters.

Non-Windows unit tests use an injected deterministic authenticated test protector. Production construction rejects non-Windows platforms before any local vault mutation.

## Local Vault Format

The local SQLite schema version is independent from the Linux schema:

```sql
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    protected BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Each protected blob is a versioned, canonical UTF-8 JSON object containing service, label, username, password, and notes. Therefore service names, labels, usernames, passwords, and notes are all encrypted at rest. IDs and timestamps remain plaintext metadata.

Search and duplicate matching decrypt records in process and apply the existing trimmed/case-folded identity and case-insensitive search rules. This is appropriate for the bounded single-user vault and avoids plaintext search indexes that would leak account metadata.

SQLite stores only DPAPI blobs in the main database, journal, WAL, and backups. All writes use transactions. A record is protected before its SQL statement is executed. A protection or database failure rolls back the operation.

Local operations enforce the same required fields, 4,096-byte field bound, deterministic ordering, not-found behavior, duplicate policies, 3,000-row import bound, and typed result shapes as the remote service.

## Local Backups and Deletion

Before a mutating bulk import or migration, the local vault creates a timestamped copy in its backups directory. A backup failure prevents mutation. Individual CRUD operations retain the existing behavior and do not create a backup per edit.

Local-vault deletion is available only as the final confirmed step of a successful switch to remote. It resolves and validates the exact configured local data root, closes all database handles, then removes only:

- `vault.db`
- `vault.db-wal`
- `vault.db-shm`
- backup files matching Passwatcher's exact local backup naming contract
- the backup directory and local data directory only when empty

It never deletes the roaming configuration, CSV exports, unrelated files, or the remote vault. Partial cleanup produces a warning and leaves remote active.

The NSIS uninstaller is updated to ask separately whether to remove the local DPAPI vault. Silent uninstall preserves it by default.

## Migration and Existing Dual Vaults

Migration always flows from the previously active backend into the newly selected backend. The destination is authoritative for destination-only records; source records are considered for copying by normalized `(service, label, username)` identity.

The migration planner decrypts/loads both sides and produces only non-secret counts:

- source-only: copy to destination
- identical identity and fields: skip
- same identity with different password or notes: conflict
- destination-only: preserve

If there are conflicts, the user makes one choice for the entire migration:

- source wins: update all conflicting destination records from the active source
- destination wins: keep all conflicting destination records
- cancel: perform no migration and do not switch the active backend

The preview and conflict prompt display counts only. They never display passwords, notes, raw protected blobs, or request bodies.

The destination applies inserts and selected conflict updates atomically using its existing bulk-import transaction. For remote destinations this is one protocol-v2 import request and its existing pre-import server backup. For local destinations it is one SQLite transaction and local pre-import backup. Configuration is switched only after the destination reports a successful summary and passes health verification.

If both vault files exist, Passwatcher does not choose based on timestamps or file presence. The persisted backend is the current source and the newly completed setup flag is the destination. With no prior configuration, an existing local vault is treated as the source only during `setup --remote`; otherwise the explicitly selected mode initializes or reuses its own vault.

## Diagnostics and Errors

`pw doctor` dispatches by the active backend.

Local checks include:

- Windows platform
- local configuration validity
- vault path safety and existence
- SQLite schema version and integrity
- DPAPI decryptability of every credential
- record count without field values
- backup-directory accessibility

Remote checks remain the current SSH and Linux checks.

Expected protection, configuration, filesystem, migration, transport, protocol, and SQLite failures render through the current renderer without tracebacks. Debug mode may add stable exception types and codes, never secret material.

## Installer Build Guide

Create `docs/BUILDING_INSTALLER.md` with a copy-and-paste Windows PowerShell flow:

1. install Git, Python 3.11 or newer, and NSIS 3.x
2. clone the repository and enter it
3. create and activate `.venv`
4. install `.[dev]`
5. verify `python`, `makensis`, and the source version
6. run `tools/build_windows.ps1`
7. locate `pw.exe`, runtime files, and `Passwatcher-Setup-<version>.exe`
8. calculate SHA-256
9. optionally run guarded smoke tests only in a disposable Windows account
10. test install, new-terminal PATH resolution, upgrade preservation, and uninstall

The guide documents common failures: missing NSIS, stale terminal PATH, version mismatch, PyInstaller output locks, and smoke-test safety guards. README links to the full guide and keeps a concise build summary.

## GitHub Release Workflow

Create `.github/workflows/release.yml` with two entry paths:

- push of a tag matching `v*`
- `workflow_dispatch` with a required existing release tag

The workflow uses a fixed `windows-2022` hosted image to avoid an unannounced `windows-latest` migration changing the release environment. It checks out the exact tag, installs Python 3.11, installs `.[dev]`, installs NSIS through Chocolatey, and verifies that the tag without `v`, `pyproject.toml`, and `passwatcher.__version__` all match.

The build job runs `tools/build_windows.ps1`, both guarded installer smoke tests in the disposable hosted runner account, creates a SHA-256 checksum file, and uploads the installer plus checksum as a workflow artifact. It does not upload the unpacked PyInstaller directory as a public release asset.

The publish job downloads the artifact and creates a GitHub Release for the existing tag with generated release notes using GitHub CLI and `GITHUB_TOKEN`. Its permissions are least-privilege:

```yaml
permissions:
  contents: write
```

The release is created only after every build and smoke gate succeeds. Existing releases are rejected rather than overwritten. Manual dispatch checks out and publishes the supplied existing tag; it never invents or pushes a tag.

The workflow uses only official GitHub actions plus the GitHub CLI already supplied by the hosted Windows image. A repository contract test reads the YAML as text and asserts triggers, pinned runner, version gate, tests, artifacts, checksum, permissions, and release command.

## Versioning

This feature release updates the project version to `0.2.0` in both `pyproject.toml` and `src/passwatcher/__init__.py`. Future releases must update both before creating the matching `v<version>` tag. The build script and workflow fail on disagreement.

## Testing Strategy

Development follows test-driven development and covers:

- legacy remote configuration migration
- local-only and remote configuration round trips
- non-mutating no-flag setup chooser
- mutually exclusive setup flags
- last-successful-setup backend activation
- current-user DPAPI flags, byte round trips, Windows allocation cleanup, and safe failures
- encrypted-at-rest assertions that all credential strings are absent from the database bytes
- local CRUD, search, ordering, import policies, rollback, backup, corruption, and health
- unchanged CLI/renderer behavior across local and remote service implementations
- local-to-remote and remote-to-local migration previews
- identical, source-only, destination-only, and conflicting records
- source-wins, destination-wins, and cancelled conflict choices
- activation only after successful migration
- default-no local deletion and exact confirmed cleanup
- active-backend doctor dispatch
- installer preservation/removal behavior for the local vault
- installer documentation commands and paths
- release workflow triggers, version checks, gates, artifacts, permissions, and publishing
- PyInstaller collection of the local backend and DPAPI code

The full suite and deterministic Linux zipapp checks remain release gates. The installer smoke tests run in the GitHub-hosted disposable Windows account and continue refusing pre-existing Passwatcher state.

## Security Limits

DPAPI removes the need for a repeatedly entered master password, but it does not protect credentials from malware or another process running as the same logged-on Windows user. It also does not replace device encryption, Windows account security, backups, or safe CSV handling.

Local protected data is normally tied to the current Windows user and device. Some domain/roaming-profile environments can alter portability, and administrator password resets can affect recoverability. Passwatcher does not claim that the local database can be copied to another device and decrypted there.

The remote mode retains its existing plaintext-on-owned-Linux-server security model. Migrating to remote necessarily decrypts local records in the Passwatcher process and sends them through SSH standard input; they are never placed in command arguments or logs.

## Success Criteria

- `pw setup` only displays the two mode commands and changes no state.
- `pw setup --local` creates and activates an encrypted local vault without SSH or a master password.
- `pw setup --remote` retains the existing guided remote setup.
- The last successfully completed setup mode is always the backend used by normal commands.
- Every credential field is absent from local SQLite bytes in plaintext.
- Both backends implement identical user-visible CRUD, search, CSV, and import behavior.
- Switching modes migrates source-only data, safely resolves conflicts, and never silently destroys either vault.
- Confirmed local cleanup targets only owned local-vault artifacts.
- The installer build guide is sufficient on a clean Windows development machine.
- A matching `v<version>` tag produces a tested GitHub Release containing the versioned installer and SHA-256 checksum.
- Existing remote-only installations upgrade without reconfiguration or data loss.
