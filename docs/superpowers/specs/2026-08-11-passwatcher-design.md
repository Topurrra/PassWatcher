# Passwatcher Design

## Goal

Passwatcher is a single-user password manager with a polished Windows CLI and a user-local Linux server. It makes storing, finding, generating, editing, and deleting credentials easy while using the owner's existing SSH access instead of an HTTP service.

## Scope

Version 1 supports Windows clients and one Linux server. Many Windows devices may connect to the same server-side vault. The project includes the Windows CLI, the Linux request handler, guided setup, a standalone Windows executable, and an NSIS installer.

Passwatcher is intentionally single-user. It does not provide browser extensions, a web interface, teams, sharing, HTTP APIs, background daemons, or server-side encryption. The owner accepts a permissions-protected SQLite database because the Linux server and account are under their control.

## Architecture

The Windows `pw` client invokes the system OpenSSH client for each operation. It starts one fixed remote command and sends a versioned JSON request through SSH standard input. The Linux handler validates the request, performs one operation against SQLite, and returns one versioned JSON response through standard output.

Secrets never appear in SSH command arguments. The protocol does not interpolate searches, usernames, passwords, or notes into a shell command. There is no persistent server process, network listener, root login requirement, or port beyond the existing SSH service.

The Windows client owns presentation, interactive prompts, password generation, clipboard access, and local connection configuration. The Linux component owns validation, matching, transactions, schema migrations, backups made before migrations, and the shared SQLite vault.

## Components and Boundaries

### Windows client

The client provides the `pw` command, renders the Midnight Neon interface, collects interactive input, calls an injectable SSH transport, validates protocol responses, and applies clipboard rules. Its modules separate command handling, presentation, local configuration, transport, and password generation so each can be tested independently.

The client configuration contains only the SSH hostname, username, port, identity-file path, and optional SSH options. It lives in `%APPDATA%\Passwatcher\config.toml`. Password records are never cached or persisted on the client.

### Linux server

The server is a short-lived Python request handler installed under the configured Linux user's home directory. The executable is `~/.local/bin/passwatcher-server`; application data is under `~/.local/share/passwatcher/`; the database is `~/.local/share/passwatcher/passwatcher.db`; migration backups are stored under `~/.local/share/passwatcher/backups/`.

The installation directory is accessible only to its owner, the database and backups use mode `0600`, and executables use mode `0700`. The server uses Python's standard-library `sqlite3` module and does not require `sudo`.

### SSH transport and protocol

Every request runs the fixed remote command `~/.local/bin/passwatcher-server rpc`. A single JSON object is written to standard input and a single JSON object is read from standard output. Both contain `protocol_version: 1`. Responses contain either `ok: true` and a typed result or `ok: false` and a stable error code plus a safe human-readable message.

Malformed JSON, unsupported protocol versions, unknown operations, unexpected fields, invalid field types, and oversized input are rejected before database access. Diagnostic details are emitted only when the client uses `--debug`; passwords must not appear in diagnostic output.

## Credential Data

Each record contains:

- `id`: server-assigned integer primary key
- `service`: required domain or service name
- `label`: optional account label such as `personal` or `work`
- `username`: required username or email address
- `password`: required password
- `notes`: optional free-form notes
- `created_at`: server-assigned UTC timestamp
- `updated_at`: server-assigned UTC timestamp

The database schema has an explicit integer schema version. Migrations run in a transaction and only after a timestamped database backup succeeds.

## Matching and Clipboard Rules

Search is case-insensitive partial matching over `service`, `label`, and `username`. Results are deterministically ordered by service, label, username, then record ID.

`pw QUERY` behaves as follows:

- No matches: show a concise not-found message and suggested next actions; copy nothing.
- Exactly one match: show the service, label, username, and password; copy only the password to the Windows clipboard.
- More than one match: show every matching record, including its password, in a numbered table; copy nothing. Suggest refining the query with a service, label, or username fragment.

The default lookup behavior deliberately reveals passwords because this is an explicit owner requirement.

## Commands

### `pw QUERY`

Search for credentials using one or more query words joined as a single search string. Apply the matching and clipboard rules above.

### `pw add`

Open an interactive form for service, optional label, username, password, and optional notes. The password prompt can accept a typed password or generate one. Flags provide the same fields for deliberate scripting, but secrets supplied through flags are documented as less private because they may enter shell history.

### `pw edit QUERY`

Search for a record. If more than one record matches, prompt for a numbered selection. Present existing non-secret values as editable defaults and allow replacing or generating the password. Commit the update only after confirmation.

### `pw delete QUERY`

Search for a record, prompt for a numbered selection when necessary, show the selected account, and require explicit confirmation. Cancellation performs no write.

### `pw list`

List all records with service, label, and username. Passwords are hidden by default. `pw list --secrets` includes passwords.

### `pw generate`

Generate a strong password, show it, and copy it to the clipboard. Interactive options allow changing length and character categories; sensible defaults make plain `pw generate` sufficient.

### `pw setup`

Guide the user through SSH host, username, port, and identity-file configuration; test connectivity; inspect the remote installation; install or upgrade the server component; initialize or migrate the database; verify permissions and health; then save the local configuration.

Setup is idempotent across devices. It detects an existing database and never recreates, imports, clears, or overwrites it. Each Windows device stores only its own connection configuration and connects to the same Linux database. An existing compatible server is verified and reused. An older server is upgraded only after a successful timestamped backup, and schema migrations are transactional.

### `pw doctor`

Check local configuration, OpenSSH availability, SSH connectivity, remote server version, protocol compatibility, filesystem permissions, schema version, SQLite integrity, and basic read access. It reports actionable fixes without changing data.

## User Experience

The visual direction is **Midnight Neon**: a compact near-black/navy surface, cyan primary accents, restrained violet borders and highlights, subtle glow, and unambiguous green/red status colors. It combines Midnight Minimal's density and legibility with Neon Vault's personality.

The CLI uses readable panels for a single result, tables for multiple results, short status lines, and spinners only while waiting on SSH. It supports terminals without color and non-interactive output. Routine success output is concise. Errors state what failed and the next useful action. `--debug` adds technical context without revealing stored passwords.

Interactive forms provide defaults, validation close to the field, cancellation without writes, and numbered selection when a query is ambiguous.

## Reliability and Error Handling

All database writes use SQLite transactions. Validation completes before a transaction begins. An interrupted SSH connection cannot leave a partial logical write. The server returns stable error codes for invalid input, missing records, ambiguous selection, conflicts, database failures, incompatible versions, and internal failures.

The client distinguishes configuration, SSH, protocol, validation, and server errors. Normal errors do not print Python tracebacks. Debug output may include exception types and transport diagnostics but must redact passwords and request bodies containing secrets.

Migrations require a successful backup, run transactionally, update the schema version only when complete, and preserve the prior database if they fail.

## Windows Packaging

The Windows client is bundled as a standalone `pw.exe` and shipped in an NSIS installer. Python is not required on the client device.

The installer:

- installs per-user under `%LOCALAPPDATA%\Programs\Passwatcher\`
- requires no administrator privileges
- adds the installation directory to the current user's `PATH`
- broadcasts the Windows environment change so new terminals can run `pw`
- creates a registered uninstaller
- upgrades an older client in place
- preserves `%APPDATA%\Passwatcher\config.toml` during upgrades
- asks whether to retain or remove local configuration during uninstall
- never contains or stores credential records

## Testing Strategy

Development follows test-driven development. Unit tests cover record validation, case-insensitive partial matching, deterministic ordering, password generation, protocol validation, response redaction, configuration, and clipboard decisions.

Client command tests cover interactive add/edit/delete flows, cancellation, zero/one/many lookup behavior, list secret visibility, error rendering, and setup prompts. Clipboard access and SSH execution are injected at their boundaries; behavioral tests assert the client's real decisions rather than testing mock call counts alone.

Server integration tests use temporary SQLite databases and exercise create, read, update, delete, transactions, rollback, integrity checks, migration backup requirements, migration failure recovery, and repeated idempotent setup. Protocol integration tests pass real JSON through the server handler's standard input and output.

A packaging smoke test runs the standalone Windows executable, checks the NSIS install/uninstall behavior and per-user `PATH`, and confirms configuration preservation. A Linux smoke-test script installs into a temporary home directory, reruns setup without creating a second database, and verifies permissions and server health.

## Success Criteria

- A new Windows device can install Passwatcher and run `pw setup` without installing Python or using administrator privileges.
- Multiple devices can connect to one existing Linux vault without duplicating or overwriting its database.
- `pw github` completes a search through SSH and follows the exact zero/one/many display and clipboard rules.
- All CRUD and generation commands work interactively with clear cancellation and error behavior.
- No secret is placed in an SSH command argument, local configuration file, debug log, or server diagnostic message.
- Repeated setup is safe, and failed writes or migrations leave the prior database usable.
- The CLI consistently renders the approved Midnight Neon presentation with a plain-output fallback.
