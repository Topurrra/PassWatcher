# PassWatcher CSV Portability Design

## Goal

Add safe CSV import and export to PassWatcher, restore genuinely optional interactive fields, and reuse the existing Midnight Neon and plain-text presentation. Imports must be validated before mutation and committed atomically on the Linux server. Exports must preserve credentials exactly while making their plaintext risk explicit.

## Scope

This change adds:

- CSV import from PassWatcher and common browser exports
- lossless PassWatcher CSV export
- browser-compatible CSV export
- dry-run import previews and explicit duplicate policies
- atomic server-side bulk import with a pre-import vault backup
- empty optional values for label, notes, and SSH identity-file prompts
- interactive removal of existing optional label and note values
- styled and plain output for all new interactions
- backward-compatible server handling for existing protocol-v1 clients

An encrypted local Windows vault is a separate follow-up project. Its accepted direction is a local, non-roaming SQLite vault protected with current-user Windows DPAPI, without a master-password prompt. It will share the CLI, service interface, and renderer with SSH mode. It is not implemented as part of this CSV change.

## Commands

### Import

```powershell
pw import passwords.csv
pw import passwords.csv -n
pw import passwords.csv -d update
pw import passwords.csv -y
```

Options:

- `-n, --dry-run`: parse, validate, and preview without changing the vault
- `-d, --duplicates POLICY`: choose `skip`, `update`, or `error`; default to `skip`
- `-y, --yes`: accept the import after a successful preview without an interactive confirmation

The client always completes the same preflight validation whether or not `--dry-run` is used. `--yes` skips only the confirmation; it never skips validation, backup, or transactional behavior.

### Export

```powershell
pw export passwords.csv
pw export passwords.csv -t browser
pw export passwords.csv -f -y
```

Options:

- `-t, --format FORMAT`: choose `passwatcher` or `browser`; default to `passwatcher`
- `-f, --force`: allow replacement of an existing regular destination file
- `-y, --yes`: acknowledge the plaintext warning without an interactive confirmation

`--force` and `--yes` are independent. Overwriting requires `--force`, and writing plaintext credentials requires confirmation or `--yes`.

## CSV Formats

Header matching is case-insensitive and ignores surrounding header whitespace. Duplicate normalized headers are invalid. Files are decoded as UTF-8 with an optional UTF-8 byte-order mark and parsed with Python's standard CSV parser so commas, quotes, Unicode, CRLF/LF line endings, and embedded newlines are handled correctly.

### PassWatcher format

Required columns:

- `service`
- `username`
- `password`

Optional columns:

- `label`
- `notes`

PassWatcher export writes the columns in this order:

```text
service,label,username,password,notes
```

This is the lossless round-trip format. Empty optional cells remain empty.

### Browser format

Required columns:

- `url`
- `username`
- `password`

Optional columns:

- `name`
- `note`

Browser import maps `url` to service, `name` to label, and `note` to notes. Missing optional columns and empty optional cells map to empty strings. Unknown columns are ignored and listed by header name in the preview; their row values are never printed.

Browser export writes the columns in this order:

```text
name,url,username,password,note
```

It maps label to `name`, service to `url`, and notes to `note`. Browser format is intended for migration and is not promised to preserve semantics across every browser. PassWatcher format remains the authoritative round-trip format.

## Import Validation and Preview

The Windows client reads and parses the CSV locally. The CSV file itself is never uploaded or retained on the server. The client enforces:

- a header row and at least one data row
- no more than 3,000 data rows per import
- exactly one recognized format
- every required column present exactly once
- required service/URL, username, and password values are non-empty
- every stored field fits the server's existing 4,096-byte UTF-8 limit
- the encoded bulk request fits its configured protocol limit

The preview reports:

- detected format
- total data rows
- rows eligible to insert
- rows eligible to update
- rows skipped as existing duplicates
- ignored column names
- validation errors by row number and field name

Passwords and other cell values never appear in previews, errors, debug output, or confirmation messages.

If preflight finds any invalid or ambiguous row, the import does not offer confirmation and sends no mutating request. `--dry-run` exits successfully only when the file is valid under the selected duplicate policy.

## Duplicate Semantics

A credential identity is the trimmed, case-folded tuple of service, label, and username. The password and notes are not part of identity.

Duplicate handling applies both to records already in the vault and to repeated identities inside the CSV:

- `skip`: retain an existing vault record and count the incoming row as skipped
- `update`: replace the existing record's password and notes with incoming values while retaining its server-assigned ID and creation timestamp
- `error`: report the matching row and perform no import

Two rows inside one CSV with the same identity and identical stored fields collapse to one row and are counted as duplicates. Two rows with the same identity but different password or notes are an ambiguous conflict and fail preflight under every policy; PassWatcher never guesses which input row should win.

If the existing vault already contains more than one record with the same identity, `update` and `error` reject the ambiguity. `skip` skips the incoming identity without changing any existing record.

## Server Transaction and Backup

Protocol v2 adds one bounded bulk-import operation. The client sends normalized credential objects and the duplicate policy through SSH standard input. The server repeats all security-relevant type, field, size, row-count, identity, and duplicate validation rather than trusting client preflight.

For an import that will insert or update at least one record, the server:

1. initializes and validates the vault
2. resolves all duplicate decisions without mutating data
3. creates a timestamped owner-only SQLite backup
4. starts one write transaction
5. applies every planned insert and update
6. commits once and returns a non-secret summary

Failure to create the backup prevents the transaction. Any validation, conflict, SQLite, interruption, or internal failure before commit rolls back the whole import. The response contains only inserted, updated, skipped, and total counts.

An import whose entire contents are skipped performs no backup and no write.

## Protocol Compatibility

The client protocol version becomes 2. The upgraded server accepts versions 1 and 2:

- valid v1 requests continue supporting the existing search, list, create, update, delete, and health operations
- bulk import requires v2
- responses use the request's accepted protocol version
- safe error responses use the request version when it can be validated

This lets existing installed clients continue working after a server upgrade. A v2 client that reaches an older server maps the incompatible or unknown import response to an actionable message instructing the user to run `pw setup`. Existing database schema version 1 is sufficient; CSV support does not require a schema migration.

## Export Safety

Export obtains records through the existing authenticated list operation and writes them only on the Windows client.

Before writing, the CLI warns that:

- CSV credentials are readable plaintext
- the file should not be uploaded, emailed, or left behind
- opening it in spreadsheet software can interpret password-like values as formulas
- PassWatcher preserves values exactly and therefore does not prefix or rewrite formula-like text

The export writer:

- rejects a directory or other non-regular destination
- refuses an existing file unless `--force` is present
- creates a temporary file beside the destination
- writes complete UTF-8 CSV through Python's CSV writer
- flushes and closes the temporary file before replacement
- atomically replaces the destination when allowed
- removes its temporary file after any failure

An interrupted export leaves the original destination unchanged. A successful result reports the record count, format, and path without printing record values.

## Optional Interactive Fields

The prompt boundary gains explicit optional-input behavior instead of relying on Typer's required prompt default.

For add and setup:

- Enter accepts an empty label, notes value, or identity-file path immediately
- required service, username, password, SSH host, and SSH user remain required

For edit:

- Enter retains the current field value
- entering `/clear` replaces an existing optional label or notes value with an empty string
- `/clear` is special only for optional interactive fields; an explicit CLI flag value remains literal

The server retains final authority for required and maximum-length validation.

## Presentation

All new output goes through the existing renderer rather than introducing raw command-specific formatting.

Midnight Neon output uses:

- the existing cyan and violet table treatment for import previews
- muted text for ignored columns and secondary explanations
- red for unsafe export warnings and validation failures
- green for completed import/export summaries

Passwords never appear in preview tables. `--plain` provides deterministic text containing the same non-secret counts, row numbers, field names, format, and destination information without ANSI color.

## Error Handling

Expected CSV, filesystem, protocol, transport, and database failures produce concise messages and exit status 1 without Python tracebacks. Debug mode may include exception type and stable error code, but never request bodies, CSV rows, credential values, or file contents.

Errors distinguish at least:

- unreadable or non-regular import path
- invalid encoding or CSV structure
- unrecognized or ambiguous headers
- invalid row and field
- row or request limit exceeded
- duplicate conflict
- backup failure
- transaction failure
- unsupported remote import capability
- unsafe, existing, or unwritable export destination
- interrupted export cleanup failure

Cancellation before import confirmation makes no remote mutation. Cancellation before export confirmation creates no destination or temporary file.

## Component Boundaries

- `csv_io` owns pure CSV detection, parsing, mapping, validation reporting, and serialization.
- the CLI owns paths, confirmations, flag parsing, and orchestration.
- the renderer owns preview tables, warnings, and summaries.
- `PasswordService` owns typed bulk-import request and response mapping.
- the RPC handler owns protocol shape, bounds, and safe error mapping.
- `Vault` owns authoritative duplicate planning, backup ordering, and the SQLite transaction.
- the prompt helper owns required, optional, keep-current, and clear behavior.

These units remain independently testable. CSV parsing has no SSH, SQLite, terminal, or filesystem-write dependency. The vault import accepts already structured records but repeats validation at its trust boundary.

## Testing Strategy

Development follows test-driven development. Tests cover:

- PassWatcher and browser header detection
- case-insensitive headers, optional BOM, Unicode, commas, quotes, and embedded newlines
- missing, duplicate, unknown, ambiguous, and malformed headers
- empty optional cells and empty required cells
- field, row-count, and request-size limits
- all duplicate policies and ambiguous existing duplicates
- secret-free previews, errors, debug details, and responses
- dry-run and cancellation performing no mutation
- backup occurring before mutation
- backup failure preventing writes
- successful all-or-nothing insert/update behavior
- rollback on validation and database failures
- protocol-v1 compatibility and protocol-v2 import requirements
- actionable older-server errors
- both exact export schemas
- export/import round trips for PassWatcher format
- browser export mappings
- overwrite refusal, forced atomic replacement, and temporary-file cleanup
- formula-like values preserved exactly with an accompanying warning
- optional add/setup prompts accepting Enter
- edit prompts retaining values on Enter and clearing on `/clear`
- styled output and deterministic `--plain` output
- CLI help showing both short and long flags

After implementation, the complete test suite runs, the deterministic Linux server zipapp is rebuilt, and the tests run again against the bundled artifact. Existing Windows packaging tests must remain green.

## Success Criteria

- A Chrome-, Edge-, Firefox-, or compatible generic browser CSV containing `url`, `username`, and `password` imports without manual column editing.
- Empty optional fields never force the user to type placeholder text.
- A malformed or conflicting import changes no vault records.
- A valid import that inserts or updates records creates a backup and commits exactly once.
- An older client retains existing functionality after the server upgrade.
- PassWatcher CSV export and re-import preserve all mutable credential fields exactly.
- Browser export is accepted as a migration format and clearly identified as non-lossless.
- No preview, warning, error, or debug output reveals a credential value.
- Existing files survive refused or failed exports unchanged.
- All new output matches the existing Midnight Neon renderer and `--plain` conventions.
