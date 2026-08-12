# PassWatcher CSV Portability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe browser/PassWatcher CSV import and export, atomic server-side bulk import, and truly optional interactive fields while preserving the existing CLI styling and security model.

**Architecture:** The Windows client parses, validates, previews, and exports CSV through two focused modules. Imports cross SSH as one bounded protocol-v2 JSON request; the Linux vault repeats validation, backs up, and applies the batch in one SQLite transaction. Protocol-v2 servers continue serving existing protocol-v1 clients.

**Tech Stack:** Python 3.11+, Typer, Rich, standard-library `csv`/`sqlite3`/`tempfile`, OpenSSH transport, pytest, deterministic Python zipapp packaging.

## Global Constraints

- PassWatcher import accepts at most 3,000 data rows per request.
- Stored fields retain the existing 4,096-byte UTF-8 limit.
- PassWatcher headers are `service,label,username,password,notes`; browser headers are `name,url,username,password,note`.
- Duplicate identity is the trimmed, case-folded `(service, label, username)` tuple.
- Duplicate policies are exactly `skip`, `update`, and `error`; default is `skip`.
- A mutating import must create an owner-only backup before its single write transaction.
- Protocol v2 adds bulk import; the server must continue accepting valid protocol-v1 existing operations.
- CSV files remain local to Windows; no import file is uploaded or retained on Linux.
- Export writes UTF-8 CSV atomically and never overwrites without `--force`.
- No preview, warning, error, response, or debug output may reveal a credential value.
- All new styled output reuses the existing Midnight Neon renderer; `--plain` remains deterministic.
- The DPAPI local vault is a separate future project and is not part of this plan.

## File Structure

- Create `src/passwatcher/csv_import.py`: CSV format/policy enums, import parsing, row validation, duplicate preflight, and non-secret preview models.
- Create `src/passwatcher/csv_export.py`: exact schema mapping and atomic local CSV writing.
- Modify `src/passwatcher/prompts.py`: explicit optional prompt and `/clear` behavior.
- Modify `src/passwatcher/cli.py`: import/export commands, short flags, orchestration, and optional-field wiring.
- Modify `src/passwatcher/render.py`: Midnight Neon/plain import previews, plaintext warnings, validation failures, and completion summaries.
- Modify `src/passwatcher/service.py`: typed credential drafts, import summaries, and bulk-import RPC mapping.
- Modify `src/passwatcher/protocol.py`: protocol-v2 client requests and safe older-server mapping support.
- Modify `src/passwatcher_server/models.py`: server credential draft and import summary values.
- Modify `src/passwatcher_server/database.py`: authoritative duplicate planning, backup ordering, and transactional bulk import.
- Modify `src/passwatcher_server/rpc.py`: dual-version validation, bounded bulk-import payload, and version-matched responses.
- Modify `src/passwatcher_server/__main__.py`: enlarged but bounded RPC stdin read.
- Modify `README.md`: commands, formats, warnings, and optional prompt behavior.
- Rebuild `src/passwatcher/assets/passwatcher-server.pyz`: deterministic bundled server containing protocol v2.
- Create `tests/client/test_prompts.py`, `tests/client/test_csv_import.py`, `tests/client/test_csv_export.py`, and `tests/client/test_csv_cli.py`.
- Modify `tests/client/test_protocol.py`, `tests/client/test_service.py`, `tests/client/test_crud_cli.py`, `tests/server/test_database.py`, `tests/server/test_rpc.py`, `tests/server/test_zipapp.py`, `tests/setup/test_setup.py`, and `tests/setup/test_doctor.py`.

---

### Task 1: Truly Optional Interactive Fields

**Files:**
- Modify: `src/passwatcher/prompts.py`
- Modify: `src/passwatcher/cli.py`
- Create: `tests/client/test_prompts.py`
- Modify: `tests/client/test_crud_cli.py`
- Modify: `tests/setup/test_doctor.py`

**Interfaces:**
- Produces: `prompts.optional_text(label: str, *, current: str | None = None) -> str`
- Behavior: no current value + Enter returns `""`; current value + Enter returns current; current value + `/clear` returns `""`.

- [ ] **Step 1: Write failing prompt-helper tests**

Create `tests/client/test_prompts.py` with direct tests around Typer's real prompt input:

```python
from typer.testing import CliRunner
import typer

from passwatcher import prompts


def _optional_app(current: str | None = None) -> typer.Typer:
    app = typer.Typer()

    @app.command()
    def read() -> None:
        typer.echo(repr(prompts.optional_text("Optional", current=current)))

    return app


def test_optional_text_accepts_empty_input() -> None:
    result = CliRunner().invoke(_optional_app(), [], input="\n")
    assert result.exit_code == 0
    assert "''" in result.stdout


def test_optional_text_keeps_current_value_on_enter() -> None:
    result = CliRunner().invoke(_optional_app("work"), [], input="\n")
    assert result.exit_code == 0
    assert "'work'" in result.stdout


def test_optional_text_clears_current_value() -> None:
    result = CliRunner().invoke(_optional_app("work"), [], input="/clear\n")
    assert result.exit_code == 0
    assert "''" in result.stdout
```

- [ ] **Step 2: Run the new prompt tests and verify failure**

Run: `python -m pytest tests/client/test_prompts.py -q`

Expected: FAIL because `passwatcher.prompts.optional_text` does not exist.

- [ ] **Step 3: Implement the optional prompt helper**

Add a cancellation-safe helper in `src/passwatcher/prompts.py`:

```python
def optional_text(label: str, *, current: str | None = None) -> str:
    """Read optional text; Enter keeps a current value and `/clear` removes it."""
    try:
        value = typer.prompt(
            label,
            default=current if current is not None else "",
            show_default=current not in (None, ""),
        )
    except (typer.Abort, EOFError, KeyboardInterrupt) as error:
        raise PromptCancelled from error
    if current is not None and value == "/clear":
        return ""
    return value
```

Use `optional_text` for add label/notes, edit label/notes, and setup identity file. Keep required fields on `text`.

- [ ] **Step 4: Add CLI regression tests for empty and cleared optional values**

Add these cases to `tests/client/test_crud_cli.py` using the existing fixtures:

```python
def test_add_accepts_empty_optional_fields(cli, service):
    result = cli.invoke(app, ["add"], input="github.com\n\nnika\nsecret\n\ny\n")
    assert result.exit_code == 0
    assert service.created is not None
    assert service.created.label == ""
    assert service.created.notes == ""


def test_edit_can_clear_optional_fields(cli, service):
    service.matches = [record(label="work", notes="private")]
    result = cli.invoke(
        app,
        ["edit", "github"],
        input="\n/clear\n\n\n/clear\ny\n",
    )
    assert result.exit_code == 0
    assert service.updated is not None
    assert service.updated.label == ""
    assert service.updated.notes == ""
```

Extend the fake service to retain the updated record, not only its ID. Add a setup-flow assertion in `tests/setup/test_doctor.py` that an empty identity prompt produces `identity_file is None` without a second prompt.

- [ ] **Step 5: Run focused optional-field tests**

Run: `python -m pytest tests/client/test_prompts.py tests/client/test_crud_cli.py tests/setup/test_doctor.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the optional-field fix**

```bash
git add src/passwatcher/prompts.py src/passwatcher/cli.py tests/client/test_prompts.py tests/client/test_crud_cli.py tests/setup/test_doctor.py
git commit -m "fix: allow empty optional credential fields"
```

---

### Task 2: Authoritative Transactional Vault Import

**Files:**
- Modify: `src/passwatcher_server/models.py`
- Modify: `src/passwatcher_server/database.py`
- Modify: `tests/server/test_database.py`

**Interfaces:**
- Produces: `CredentialDraft(service: str, label: str, username: str, password: str, notes: str)`
- Produces: `ImportSummary(total: int, inserted: int, updated: int, skipped: int)`
- Produces: `Vault.import_many(records: list[CredentialDraft], duplicates: str) -> ImportSummary`
- Raises: `ValidationError("invalid_duplicate_policy", "Duplicate policy must be skip, update, or error")` and `ValidationError("duplicate_conflict", "The import contains a duplicate credential identity")` without credential values.
- Enforces: one to 3,000 input records when called directly or through RPC.

- [ ] **Step 1: Write failing vault tests for skip, update, and error**

Add typed draft helpers and cases to `tests/server/test_database.py`:

```python
from passwatcher_server.models import CredentialDraft


def draft(**overrides: str) -> CredentialDraft:
    values = {
        "service": "github.com",
        "label": "work",
        "username": "nika",
        "password": "secret",
        "notes": "",
    }
    values.update(overrides)
    return CredentialDraft(**values)


def test_import_many_inserts_all_records_in_one_result(vault: Vault) -> None:
    summary = vault.import_many(
        [draft(), draft(service="gitlab.com", password="other")],
        duplicates="skip",
    )
    assert summary.total == 2
    assert summary.inserted == 2
    assert summary.updated == 0
    assert summary.skipped == 0
    assert len(vault.list_all()) == 2


def test_import_many_updates_matching_identity(vault: Vault) -> None:
    original = vault.create(
        service="github.com", label="work", username="nika", password="old", notes="old"
    )
    summary = vault.import_many(
        [draft(password="new", notes="new")], duplicates="update"
    )
    updated = vault.list_all()[0]
    assert summary.updated == 1
    assert updated.id == original.id
    assert updated.created_at == original.created_at
    assert updated.password == "new"


def test_import_many_error_policy_changes_nothing(vault: Vault) -> None:
    vault.create(service="github.com", label="work", username="nika", password="old", notes="")
    with pytest.raises(ValidationError) as raised:
        vault.import_many([draft(password="new")], duplicates="error")
    assert raised.value.code == "duplicate_conflict"
    assert vault.list_all()[0].password == "old"
```

- [ ] **Step 2: Run the core import tests and verify failure**

Run: `python -m pytest tests/server/test_database.py -k import_many -q`

Expected: FAIL because the import models and `Vault.import_many` do not exist.

- [ ] **Step 3: Add server import value objects and normalized identity**

Add to `src/passwatcher_server/models.py`:

```python
@dataclass(frozen=True, slots=True)
class CredentialDraft:
    service: str
    label: str
    username: str
    password: str
    notes: str


@dataclass(frozen=True, slots=True)
class ImportSummary:
    total: int
    inserted: int
    updated: int
    skipped: int
```

Add a private identity helper in `Vault`:

```python
@staticmethod
def _identity(service: str, label: str, username: str) -> tuple[str, str, str]:
    return service.strip().casefold(), label.strip().casefold(), username.strip().casefold()
```

- [ ] **Step 4: Implement duplicate planning and one transaction**

Implement `Vault.import_many` so it validates every draft with `_validate_fields`, rejects policies outside the exact three-value set, detects conflicting within-file identities, loads existing identities, and calculates inserts/updates/skips before mutation. Use one connection context for all SQL changes and return `ImportSummary` only after commit.

The mutating branch must execute backup before opening the write transaction:

```python
if inserted_count == 0 and updated_count == 0:
    return ImportSummary(total=len(records), inserted=0, updated=0, skipped=skipped_count)

self.backup()
connection = self._connect()
try:
    with connection:
        for fields in inserts:
            connection.execute(
                "INSERT INTO credentials(service, label, username, password, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*fields, now, now),
            )
        for credential_id, fields in updates:
            connection.execute(
                "UPDATE credentials SET password = ?, notes = ?, updated_at = ? WHERE id = ?",
                (fields[3], fields[4], now, credential_id),
            )
finally:
    self._secure_and_close(connection)
```

Wrap SQLite failures as `DatabaseError("The vault database could not import credentials")`.

- [ ] **Step 5: Add backup, ambiguity, bounds, and rollback tests**

Add explicit cases to `tests/server/test_database.py`:

```python
def test_import_many_backs_up_before_mutation(vault: Vault) -> None:
    vault.create(service="existing.test", label="", username="old", password="old", notes="")
    vault.import_many([draft()], duplicates="skip")
    backups = list(vault.backup_dir.glob("passwatcher-*-v1.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("SELECT service FROM credentials").fetchall() == [("existing.test",)]


def test_import_many_backup_failure_prevents_mutation(vault: Vault, monkeypatch) -> None:
    monkeypatch.setattr(vault, "backup", lambda: (_ for _ in ()).throw(DatabaseError("failed")))
    with pytest.raises(DatabaseError):
        vault.import_many([draft()], duplicates="skip")
    assert vault.list_all() == []


def test_import_many_rejects_conflicting_input_identity(vault: Vault) -> None:
    with pytest.raises(ValidationError) as raised:
        vault.import_many([draft(password="one"), draft(password="two")], duplicates="skip")
    assert raised.value.code == "duplicate_conflict"
    assert vault.list_all() == []
```

Also test: identical input duplicates collapse and count as skipped, all-skipped imports create no backup, existing ambiguous identities reject update/error and skip safely, invalid policy, zero records, 3,001 records, empty required fields, oversized UTF-8 fields, and a forced second SQL statement failure rolls back the first insert.

- [ ] **Step 6: Run the complete database tests**

Run: `python -m pytest tests/server/test_database.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the vault import boundary**

```bash
git add src/passwatcher_server/models.py src/passwatcher_server/database.py tests/server/test_database.py
git commit -m "feat: add transactional vault imports"
```

---

### Task 3: Protocol v2 and Typed Bulk-Import Service

**Files:**
- Modify: `src/passwatcher/protocol.py`
- Modify: `src/passwatcher/service.py`
- Modify: `src/passwatcher_server/rpc.py`
- Modify: `src/passwatcher_server/__main__.py`
- Modify: `tests/client/test_protocol.py`
- Modify: `tests/client/test_service.py`
- Modify: `tests/server/test_rpc.py`
- Modify: `tests/server/test_zipapp.py`
- Modify: `tests/setup/test_setup.py`
- Modify: `tests/setup/test_doctor.py`
- Rebuild: `src/passwatcher/assets/passwatcher-server.pyz`

**Interfaces:**
- Produces client: `CredentialDraft` and `ImportSummary` dataclasses in `passwatcher.service`.
- Produces client: `PasswordService.import_many(records: list[CredentialDraft], duplicates: str) -> ImportSummary`.
- Produces RPC operation: `import` with payload `{"records": list[credential-object], "duplicates": str}`.
- Produces client/server constant: `MAX_REQUEST_BYTES = 16 * 1024 * 1024`.
- Produces server constants: `PROTOCOL_VERSION = 2`, `SUPPORTED_PROTOCOL_VERSIONS = frozenset({1, 2})`, and `MAX_IMPORT_ROWS = 3000`.

- [ ] **Step 1: Write failing client protocol and service tests**

Update `tests/client/test_protocol.py` to expect v2 requests and add an older-server error case. Add to `tests/client/test_service.py`:

```python
from passwatcher.service import CredentialDraft, ImportSummary


def test_service_maps_bulk_import_summary(fake_transport: FakeTransport) -> None:
    fake_transport.result = {"total": 2, "inserted": 1, "updated": 0, "skipped": 1}
    drafts = [
        CredentialDraft("github.com", "work", "nika", "secret", ""),
        CredentialDraft("gitlab.com", "", "nika", "other", "note"),
    ]
    summary = PasswordService(fake_transport).import_many(drafts, "skip")
    assert summary == ImportSummary(total=2, inserted=1, updated=0, skipped=1)
    assert fake_transport.request_json == {
        "protocol_version": 2,
        "operation": "import",
        "payload": {
            "records": [
                {"service": "github.com", "label": "work", "username": "nika", "password": "secret", "notes": ""},
                {"service": "gitlab.com", "label": "", "username": "nika", "password": "other", "notes": "note"},
            ],
            "duplicates": "skip",
        },
    }
```

Add malformed summary cases for booleans, negative counts, wrong keys, and inconsistent totals.

- [ ] **Step 2: Write failing dual-version server RPC tests**

Add to `tests/server/test_rpc.py`:

```python
def test_rpc_v2_import_returns_non_secret_summary(vault: Vault) -> None:
    response = invoke(
        vault,
        "import",
        {
            "records": [
                {"service": "github.com", "label": "", "username": "nika", "password": "secret", "notes": ""}
            ],
            "duplicates": "skip",
        },
        version=2,
    )
    assert response == {
        "protocol_version": 2,
        "ok": True,
        "result": {"total": 1, "inserted": 1, "updated": 0, "skipped": 0},
    }
    assert "secret" not in repr(response)


def test_rpc_v1_existing_operation_remains_compatible(vault: Vault) -> None:
    response = invoke(vault, "health", {}, version=1)
    assert response["protocol_version"] == 1
    assert response["ok"] is True


def test_rpc_v1_cannot_use_import(vault: Vault) -> None:
    response = invoke(vault, "import", {"records": [], "duplicates": "skip"}, version=1)
    assert_error(response, "incompatible_protocol", version=1)
```

Update the test helpers to accept an explicit version. Add cases for more than 3,000 rows, wrong nested keys/types, invalid duplicate policy type, and oversized raw input.

- [ ] **Step 3: Run focused protocol tests and verify failure**

Run: `python -m pytest tests/client/test_protocol.py tests/client/test_service.py tests/server/test_rpc.py -q`

Expected: FAIL because the current protocol is v1-only and has no import operation.

- [ ] **Step 4: Implement typed client import mapping**

In `src/passwatcher/service.py`, add immutable `CredentialDraft` and `ImportSummary` dataclasses. Serialize drafts with `dataclasses.asdict`. Validate the result with exact keys, `type(value) is int`, non-negative counts, and `total == inserted + updated + skipped`:

```python
def import_many(self, records: list[CredentialDraft], duplicates: str) -> ImportSummary:
    result = self._invoke(
        "import",
        {"records": [asdict(record) for record in records], "duplicates": duplicates},
    )
    if not isinstance(result, dict) or set(result) != {"total", "inserted", "updated", "skipped"}:
        raise _malformed_response()
    if any(type(result[key]) is not int or result[key] < 0 for key in result):
        raise _malformed_response()
    if result["total"] != result["inserted"] + result["updated"] + result["skipped"]:
        raise _malformed_response()
    return ImportSummary(**result)
```

Set client `PROTOCOL_VERSION = 2` and `MAX_REQUEST_BYTES = 16 * 1024 * 1024` in `src/passwatcher/protocol.py`.

- [ ] **Step 5: Implement dual-version RPC validation and dispatch**

In `src/passwatcher_server/rpc.py`:

```python
PROTOCOL_VERSION = 2
SUPPORTED_PROTOCOL_VERSIONS = frozenset({1, 2})
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_IMPORT_ROWS = 3000
```

Make request validation return the accepted version. Permit existing operations under v1/v2 and require v2 for `import`. Validate `records` as a list no longer than 3,000, require exact credential keys, require every nested value to be text, then convert them to server `CredentialDraft` values. Dispatch with:

```python
if operation == "import":
    drafts = [CredentialDraft(**record) for record in payload["records"]]
    return asdict(vault.import_many(drafts, payload["duplicates"]))
```

Pass the accepted version into `_success` and `_error`; when the request version is unavailable or unsupported, use current version 2. Increase the bounded stdin read in `src/passwatcher_server/__main__.py` by continuing to use `MAX_REQUEST_BYTES + 1` from the updated constant.

- [ ] **Step 6: Add actionable older-server mapping**

Keep `ProtocolError.code` stable. In the import CLI task, `incompatible_protocol` and `unknown_operation` will map to the `pw setup` instruction. At this layer, add tests proving `parse_response` retains the safe error code/message from a v1 older-server response even though the local client uses v2; do not discard the server error solely because its version is 1 when `ok` is false and the shape is valid.

- [ ] **Step 7: Update setup expectations and rebuild the server zipapp**

Change setup/doctor fixtures that represent the bundled server from protocol 1 to protocol 2. Preserve explicit protocol-v1 compatibility tests. Run:

`python tools/build_server_zipapp.py`

Then update `tests/server/test_zipapp.py` to send both a v1 health request and a v2 import request to the built zipapp and assert one JSON line, correct response versions, and no stderr.

- [ ] **Step 8: Run protocol, setup, and zipapp tests**

Run: `python -m pytest tests/client/test_protocol.py tests/client/test_service.py tests/server/test_rpc.py tests/server/test_zipapp.py tests/setup/test_setup.py tests/setup/test_doctor.py -q`

Expected: PASS.

- [ ] **Step 9: Commit protocol v2 and the rebuilt artifact**

```bash
git add src/passwatcher/protocol.py src/passwatcher/service.py src/passwatcher_server/rpc.py src/passwatcher_server/__main__.py src/passwatcher/assets/passwatcher-server.pyz tests/client/test_protocol.py tests/client/test_service.py tests/server/test_rpc.py tests/server/test_zipapp.py tests/setup/test_setup.py tests/setup/test_doctor.py
git commit -m "feat: add protocol v2 bulk imports"
```

---

### Task 4: Client CSV Import Parsing and Duplicate Preview

**Files:**
- Create: `src/passwatcher/csv_import.py`
- Create: `tests/client/test_csv_import.py`

**Interfaces:**
- Produces: `CsvFormat(str, Enum)` with `PASSWATCHER` and `BROWSER`.
- Produces: `DuplicatePolicy(str, Enum)` with `SKIP`, `UPDATE`, and `ERROR`.
- Produces: `CsvImportError(code: str, message: str, issues: tuple[CsvIssue, ...] = ())`.
- Produces: `CsvIssue(row: int | None, field: str, message: str)` containing no CSV cell value.
- Produces: `ParsedImport(format: CsvFormat, records: tuple[CredentialDraft, ...], total_rows: int, ignored_columns: tuple[str, ...], input_duplicates: int)`.
- Produces: `ImportPreview(format: CsvFormat, total: int, inserted: int, updated: int, skipped: int, ignored_columns: tuple[str, ...])`.
- Produces: `parse_import(path: Path) -> ParsedImport`.
- Produces: `preview_import(parsed: ParsedImport, existing: Sequence[CredentialRecord], policy: DuplicatePolicy) -> ImportPreview`.
- Produces: `validate_request_size(parsed: ParsedImport, policy: DuplicatePolicy) -> None` using the exact encoded protocol-v2 request.

- [ ] **Step 1: Write failing format and parser tests**

Create `tests/client/test_csv_import.py` with table-driven examples:

```python
def test_parse_passwatcher_csv_preserves_optional_blanks(tmp_path: Path) -> None:
    path = tmp_path / "passwords.csv"
    path.write_text(
        "service,label,username,password,notes\n"
        "github.com,,nika,secret,\n",
        encoding="utf-8",
    )
    parsed = parse_import(path)
    assert parsed.format is CsvFormat.PASSWATCHER
    assert parsed.records == (CredentialDraft("github.com", "", "nika", "secret", ""),)


def test_parse_browser_csv_maps_optional_columns_and_ignores_extras(tmp_path: Path) -> None:
    path = tmp_path / "browser.csv"
    path.write_text(
        "name,url,username,password,note,timeCreated\n"
        "Work,https://github.com,nika,secret,main,123\n",
        encoding="utf-8",
    )
    parsed = parse_import(path)
    assert parsed.format is CsvFormat.BROWSER
    assert parsed.records[0] == CredentialDraft("https://github.com", "Work", "nika", "secret", "main")
    assert parsed.ignored_columns == ("timeCreated",)
```

Add exact cases for UTF-8 BOM, case/whitespace-normalized headers, CRLF, commas, quotes, Unicode, and embedded newlines. Each case asserts the complete resulting `CredentialDraft`, detected format, row count, and ignored-column tuple.

- [ ] **Step 2: Write failing invalid-input and duplicate tests**

Add cases asserting stable codes and secret-free messages for unreadable path, directory path, invalid UTF-8, empty file, header-only file, missing required header, duplicate normalized header, ambiguous browser/PassWatcher headers, malformed extra cells, empty required values, fields over 4,096 UTF-8 bytes, row 3,001, identical input duplicates, and conflicting input duplicates.

Use this conflict assertion:

```python
with pytest.raises(CsvImportError) as raised:
    parse_import(path)
assert raised.value.code == "duplicate_conflict"
assert "one-secret" not in str(raised.value)
assert "two-secret" not in str(raised.value)
```

- [ ] **Step 3: Run parser tests and verify failure**

Run: `python -m pytest tests/client/test_csv_import.py -q`

Expected: FAIL because `passwatcher.csv_import` does not exist.

- [ ] **Step 4: Implement exact header detection and row mapping**

Use `csv.reader` rather than `DictReader` so duplicate headers and surplus cells are detectable. Define normalized required/optional sets and mappings:

```python
_PASSWATCHER_REQUIRED = frozenset({"service", "username", "password"})
_PASSWATCHER_OPTIONAL = frozenset({"label", "notes"})
_BROWSER_REQUIRED = frozenset({"url", "username", "password"})
_BROWSER_OPTIONAL = frozenset({"name", "note"})

_BROWSER_TO_DRAFT = {
    "url": "service",
    "name": "label",
    "username": "username",
    "password": "password",
    "note": "notes",
}
```

Add the enums and immutable models before the parser:

```python
class CsvFormat(str, Enum):
    PASSWATCHER = "passwatcher"
    BROWSER = "browser"


class DuplicatePolicy(str, Enum):
    SKIP = "skip"
    UPDATE = "update"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CsvIssue:
    row: int | None
    field: str
    message: str


class CsvImportError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        issues: tuple[CsvIssue, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.issues = issues


@dataclass(frozen=True, slots=True)
class ParsedImport:
    format: CsvFormat
    records: tuple[CredentialDraft, ...]
    total_rows: int
    ignored_columns: tuple[str, ...]
    input_duplicates: int


@dataclass(frozen=True, slots=True)
class ImportPreview:
    format: CsvFormat
    total: int
    inserted: int
    updated: int
    skipped: int
    ignored_columns: tuple[str, ...]
```

Read with `encoding="utf-8-sig"` and `newline=""`, and instantiate `csv.reader(csv_file, strict=True)`. Normalize only header names and the same non-secret fields normalized by the server; preserve password bytes as decoded text. Record row-number/field-name issues without storing cell values in exception messages.

- [ ] **Step 5: Implement within-file duplicate collapse and preview**

Retain all input rows in `parsed.records` so the server can independently count them. Count later byte-for-byte identical drafts with the same normalized identity in `input_duplicates`; reject differing password/notes for the same identity. For existing vault preview:

```python
if len(existing_by_identity[identity]) > 1:
    if policy is DuplicatePolicy.SKIP:
        skipped += 1
        continue
    raise CsvImportError("duplicate_conflict", "The vault contains an ambiguous credential identity")
if identity not in existing_by_identity:
    inserted += 1
elif policy is DuplicatePolicy.SKIP:
    skipped += 1
elif policy is DuplicatePolicy.UPDATE:
    updated += 1
else:
    raise CsvImportError("duplicate_conflict", "The CSV matches an existing credential")
```

Include identical input duplicates in the skipped preview count so `total == inserted + updated + skipped`. Only the first identical identity can be classified as insert/update/existing-skip; every later identical row is skipped.

Implement `validate_request_size` by serializing the same payload as `PasswordService.import_many` through `make_request` and comparing its byte length with client `MAX_REQUEST_BYTES`. Raise `CsvImportError("request_too_large", "The encoded import request exceeds 16777216 bytes")` before confirmation.

- [ ] **Step 6: Add and pass preview-policy tests**

Test new identity insertion, skip/update/error against one existing record, ambiguous existing identity behavior, case-folded/trimmed identity, and total-count consistency.

Run: `python -m pytest tests/client/test_csv_import.py -q`

Expected: PASS.

- [ ] **Step 7: Commit client import parsing**

```bash
git add src/passwatcher/csv_import.py tests/client/test_csv_import.py
git commit -m "feat: parse and preview password csv imports"
```

---

### Task 5: Atomic Client CSV Export

**Files:**
- Create: `src/passwatcher/csv_export.py`
- Create: `tests/client/test_csv_export.py`

**Interfaces:**
- Consumes: `CsvFormat` from `passwatcher.csv_import` and `CredentialRecord` from `passwatcher.service`.
- Produces: `CsvExportError(code: str, message: str)`.
- Produces: `validate_export_destination(path: Path, *, force: bool) -> None`.
- Produces: `export_records(path: Path, records: Sequence[CredentialRecord], format: CsvFormat, *, force: bool) -> int` returning the written record count.

- [ ] **Step 1: Write failing exact-schema and round-trip tests**

Create `tests/client/test_csv_export.py`:

```python
def test_passwatcher_export_uses_lossless_schema(tmp_path: Path) -> None:
    path = tmp_path / "passwords.csv"
    count = export_records(path, [record(password='=SUM(1,2)', notes='line one\nline two')], CsvFormat.PASSWATCHER, force=False)
    with path.open(encoding="utf-8", newline="") as exported:
        rows = list(csv.reader(exported))
    assert count == 1
    assert rows == [
        ["service", "label", "username", "password", "notes"],
        ["github.com", "work", "nika", "=SUM(1,2)", "line one\nline two"],
    ]


def test_browser_export_maps_exact_columns(tmp_path: Path) -> None:
    path = tmp_path / "browser.csv"
    export_records(path, [record()], CsvFormat.BROWSER, force=False)
    with path.open(encoding="utf-8", newline="") as exported:
        assert list(csv.reader(exported)) == [
            ["name", "url", "username", "password", "note"],
            ["work", "github.com", "nika", "secret", ""],
        ]
```

Add a PassWatcher export followed by `parse_import` assertion covering empty optionals, Unicode, commas, quotes, CRLF characters, and formula-like passwords unchanged. Compare every `CredentialDraft` field exactly.

- [ ] **Step 2: Write failing filesystem-safety tests**

Test directory rejection, existing-file refusal without force, forced replacement, original preservation when `csv.writer.writerow` fails on the second record, and no remaining `.<name>.*.tmp` file after failure.

```python
def test_failed_export_preserves_existing_destination(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "passwords.csv"
    path.write_text("original", encoding="utf-8")
    monkeypatch.setattr(csv_export, "_write_rows", lambda *_args: (_ for _ in ()).throw(OSError("failed")))
    with pytest.raises(CsvExportError):
        export_records(path, [record()], CsvFormat.PASSWATCHER, force=True)
    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(".passwords.csv.*.tmp")) == []
```

- [ ] **Step 3: Run export tests and verify failure**

Run: `python -m pytest tests/client/test_csv_export.py -q`

Expected: FAIL because `passwatcher.csv_export` does not exist.

- [ ] **Step 4: Implement exact row mapping and atomic replacement**

Implement `validate_export_destination` and call it again inside `export_records` to avoid time-of-check-only safety. Use `tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", encoding="utf-8", newline="", delete=False)`. Write through `csv.writer(temp_file, lineterminator="\n")`. Flush, close, and call `os.replace` only after all rows succeed. Reject a symlink, directory, or other non-regular destination even with force, and reject an existing regular file without force.

Use exact row functions:

```python
def _passwatcher_row(record: CredentialRecord) -> list[str]:
    return [record.service, record.label, record.username, record.password, record.notes]


def _browser_row(record: CredentialRecord) -> list[str]:
    return [record.label, record.service, record.username, record.password, record.notes]
```

On failure, unlink only the created temporary path and raise a safe `CsvExportError` without including record contents.

- [ ] **Step 5: Run all export tests**

Run: `python -m pytest tests/client/test_csv_export.py -q`

Expected: PASS.

- [ ] **Step 6: Commit atomic CSV export**

```bash
git add src/passwatcher/csv_export.py tests/client/test_csv_export.py
git commit -m "feat: add atomic password csv exports"
```

---

### Task 6: Styled CSV Presentation

**Files:**
- Modify: `src/passwatcher/render.py`
- Create: `tests/client/test_csv_render.py`

**Interfaces:**
- Consumes: `ImportPreview`, `CsvIssue`, `CsvFormat`, and `ImportSummary`.
- Produces: `Renderer.import_preview(preview: ImportPreview) -> None`.
- Produces: `Renderer.import_errors(issues: Sequence[CsvIssue]) -> None`.
- Produces: `Renderer.export_warning(path: Path, format: CsvFormat) -> None`.
- Produces: `Renderer.import_complete(summary: ImportSummary) -> None`.
- Produces: `Renderer.export_complete(count: int, path: Path, format: CsvFormat) -> None`.

- [ ] **Step 1: Write failing plain-output rendering tests**

Create `tests/client/test_csv_render.py` using `Renderer(plain=True)` and `capsys`:

```python
def test_plain_import_preview_contains_only_counts(capsys) -> None:
    preview = ImportPreview(
        format=CsvFormat.PASSWATCHER,
        total=3,
        inserted=1,
        updated=1,
        skipped=1,
        ignored_columns=("timeCreated",),
    )
    Renderer(plain=True).import_preview(preview)
    assert capsys.readouterr().out.splitlines() == [
        "Import preview",
        "Format: passwatcher",
        "Total: 3",
        "Insert: 1",
        "Update: 1",
        "Skip: 1",
        "Ignored columns: timeCreated",
    ]


def test_export_warning_never_contains_records(capsys, tmp_path: Path) -> None:
    Renderer(plain=True).export_warning(tmp_path / "passwords.csv", CsvFormat.PASSWATCHER)
    output = capsys.readouterr().out
    assert "plaintext" in output.lower()
    assert "spreadsheet" in output.lower()
```

Add exact plain completion summaries and row/field-only validation error output.

- [ ] **Step 2: Write failing styled-output tests**

Monkeypatch `render_module.sys.stdout.isatty` as existing renderer tests do. Assert styled preview output contains ANSI codes, headings, counts, and ignored header names while a sentinel password is absent. Assert warning output uses the existing red style and completion output uses green.

- [ ] **Step 3: Run renderer tests and verify failure**

Run: `python -m pytest tests/client/test_csv_render.py -q`

Expected: FAIL because the CSV renderer methods do not exist.

- [ ] **Step 4: Implement renderer methods using the existing theme**

Build the preview with the existing `_console`, `Table`, `CYAN`, `VIOLET`, `GREEN`, `RED`, and `MUTED` conventions. Do not create a second `Console` configuration or new theme. In plain mode, emit the exact stable lines from the tests. Validation rows contain only row number, field, and safe message.

Completion methods use green labels and non-secret counts:

```python
def import_complete(self, summary: ImportSummary) -> None:
    message = (
        f"Import complete: {summary.inserted} inserted, "
        f"{summary.updated} updated, {summary.skipped} skipped."
    )
    self._print_plain(message) if self.plain else self._console().print(f"[green]{message}[/green]")
```

- [ ] **Step 5: Run renderer tests plus existing lookup/list rendering tests**

Run: `python -m pytest tests/client/test_csv_render.py tests/client/test_lookup_cli.py tests/client/test_list_cli.py -q`

Expected: PASS.

- [ ] **Step 6: Commit CSV presentation**

```bash
git add src/passwatcher/render.py tests/client/test_csv_render.py
git commit -m "feat: render styled csv workflows"
```

---

### Task 7: Import and Export CLI Orchestration

**Files:**
- Modify: `src/passwatcher/cli.py`
- Create: `tests/client/test_csv_cli.py`
- Modify: `tests/client/test_lookup_cli.py`

**Interfaces:**
- Consumes all Task 3–6 interfaces.
- Produces Typer commands `import` and `export` with the approved short/long flags.
- Produces safe mapping from older-server import errors to `Run `pw setup` to upgrade the remote server.`

- [ ] **Step 1: Build a fake service and write failing import command tests**

Create `tests/client/test_csv_cli.py` with a fake service supporting `list_all`, `import_many`, and captured arguments. Add tests for:

```python
def test_import_dry_run_previews_without_mutation(cli, service, tmp_path: Path) -> None:
    path = write_browser_csv(tmp_path)
    result = cli.invoke(app, ["--plain", "import", str(path), "-n"])
    assert result.exit_code == 0
    assert "Import preview" in result.stdout
    assert service.imported is None
    assert "secret" not in result.stdout


def test_import_yes_sends_one_batch(cli, service, tmp_path: Path) -> None:
    path = write_browser_csv(tmp_path)
    service.import_result = ImportSummary(total=1, inserted=1, updated=0, skipped=0)
    result = cli.invoke(app, ["import", str(path), "-d", "skip", "-y"])
    assert result.exit_code == 0
    assert service.imported is not None
    records, policy = service.imported
    assert len(records) == 1
    assert policy == "skip"
```

Add cancellation, validation failure, duplicate error, service failure, `-d update`, `-d error`, and older-server `incompatible_protocol`/`unknown_operation` cases. Assert no password appears in output.

- [ ] **Step 2: Write failing export command tests**

Add cases for default PassWatcher format, `-t browser`, plaintext confirmation decline, `-y`, existing destination refusal, `-f`, service failure before file creation, filesystem failure, success summary, and secret-free errors.

```python
def test_export_browser_short_flags(cli, service, tmp_path: Path) -> None:
    service.records = [record(password="hidden-secret")]
    path = tmp_path / "browser.csv"
    result = cli.invoke(app, ["export", str(path), "-t", "browser", "-y"])
    assert result.exit_code == 0
    assert path.exists()
    assert "hidden-secret" not in result.stdout
```

- [ ] **Step 3: Write failing help and global-option routing tests**

Assert both aliases appear in help:

```python
def test_csv_help_shows_short_and_long_flags(cli) -> None:
    imported = cli.invoke(app, ["import", "--help"])
    exported = cli.invoke(app, ["export", "--help"])
    assert "-n" in imported.stdout and "--dry-run" in imported.stdout
    assert "-d" in imported.stdout and "--duplicates" in imported.stdout
    assert "-t" in exported.stdout and "--format" in exported.stdout
    assert "-f" in exported.stdout and "--force" in exported.stdout
    assert "-y" in imported.stdout and "--yes" in imported.stdout
```

Add `pw --plain import passwords.csv -n` and `pw --config test.toml export passwords.csv -y` routing regressions so the default lookup callback never consumes these commands as query text.

- [ ] **Step 4: Run CLI tests and verify failure**

Run: `python -m pytest tests/client/test_csv_cli.py tests/client/test_lookup_cli.py -q`

Expected: FAIL because the commands are not registered.

- [ ] **Step 5: Implement `pw import` orchestration**

Register a Typer command with exact options:

```python
@app.command("import")
def import_credentials(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument()],
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
    duplicates: Annotated[DuplicatePolicy, typer.Option("--duplicates", "-d")] = DuplicatePolicy.SKIP,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
```

Parse locally, load existing records, calculate the preview, call `validate_request_size`, and render the preview. Return on dry-run, confirm unless `yes`, then call `service.import_many(list(parsed.records), duplicates.value)` exactly once. Render the server summary rather than trusting preview counts. Map CSV errors, transport/config errors, and protocol errors through safe renderer paths. For older-server codes, instruct `pw setup`.

- [ ] **Step 6: Implement `pw export` orchestration**

Register:

```python
@app.command("export")
def export_credentials(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument()],
    format: Annotated[CsvFormat, typer.Option("--format", "-t")] = CsvFormat.PASSWATCHER,
    force: Annotated[bool, typer.Option("--force", "-f")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
```

Call `validate_export_destination` before retrieving secrets, render the plaintext warning, confirm unless `yes`, retrieve records, call `export_records`, and render the count/path/format summary. Confirmation cancellation creates no temporary file. Update `_dispatch_command` and the post-dispatch command-name set for both new commands so global root options work consistently.

- [ ] **Step 7: Run all client CSV and command tests**

Run: `python -m pytest tests/client/test_csv_import.py tests/client/test_csv_export.py tests/client/test_csv_render.py tests/client/test_csv_cli.py tests/client/test_crud_cli.py tests/client/test_lookup_cli.py tests/client/test_list_cli.py -q`

Expected: PASS.

- [ ] **Step 8: Commit CLI integration**

```bash
git add src/passwatcher/cli.py tests/client/test_csv_cli.py tests/client/test_lookup_cli.py
git commit -m "feat: add csv import and export commands"
```

---

### Task 8: Documentation, Bundled Artifact, and Release Verification

**Files:**
- Modify: `README.md`
- Rebuild: `src/passwatcher/assets/passwatcher-server.pyz`
- Modify only if verification exposes an actual defect: files already named in Tasks 1–7 and their matching tests.

**Interfaces:**
- Consumes: completed CSV commands and protocol-v2 server.
- Produces: documented user workflow and a deterministic bundled server matching source.

- [ ] **Step 1: Update README commands and security guidance**

Add concise examples:

```powershell
pw import chrome-passwords.csv -n
pw import chrome-passwords.csv -d skip
pw export passwatcher-backup.csv
pw export browser-passwords.csv -t browser
```

Document required/optional columns, short/long flags, duplicate identity/policies, `/clear`, atomic import/backup behavior, and the plaintext CSV warning. State that PassWatcher format is lossless and browser format is migration-oriented. Do not claim CSV is an encrypted backup.

- [ ] **Step 2: Rebuild the deterministic server artifact**

Run: `python tools/build_server_zipapp.py`

Run it a second time and use the existing reproducibility test rather than manually editing the binary.

- [ ] **Step 3: Run formatting-neutral repository checks**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `python -m pytest tests/client tests/server tests/setup/test_setup.py tests/setup/test_doctor.py -q`

Expected: all selected tests pass; platform-specific skips remain documented by pytest.

- [ ] **Step 4: Run the complete test suite**

Run: `python -m pytest -q`

Expected: all tests pass with only intentional platform-dependent skips.

- [ ] **Step 5: Verify the rebuilt zipapp specifically**

Run: `python -m pytest tests/server/test_zipapp.py tests/setup/test_packaging_files.py -q`

Expected: deterministic artifact, protocol-v1 health compatibility, protocol-v2 import success, and package-data declarations all pass.

- [ ] **Step 6: Inspect final secret-bearing diffs and CLI help**

Run: `git diff -- src/passwatcher src/passwatcher_server tests README.md`

Confirm no test assertion or error path prints imported passwords, raw request bodies, or CSV row values.

Run: `python -m pytest tests/client/test_csv_cli.py -k help -q`

Expected: the help tests pass, all approved short and long flags are visible, and descriptions contain no misleading encryption claim.

- [ ] **Step 7: Commit documentation and final artifact**

```bash
git add README.md src/passwatcher/assets/passwatcher-server.pyz
git commit -m "docs: explain csv portability workflows"
```

- [ ] **Step 8: Record final verification evidence**

Run: `git status --short`

Expected: clean working tree.

Run: `git log -8 --oneline`

Expected: the focused commits from Tasks 1–8 appear in dependency order.
