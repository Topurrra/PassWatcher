# Passwatcher DPAPI Local Vault Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Windows current-user DPAPI-protected local vault that can be selected persistently, migrated safely to or from the existing SSH vault, and used by every existing Passwatcher command.

**Architecture:** The CLI depends on a shared credential-service protocol. The remote implementation remains the JSON/SSH `PasswordService`; the local implementation stores one DPAPI-protected canonical credential document per SQLite row and decrypts in process for search and duplicate matching. Setup selects the active backend only after health and migration succeed, and migration writes one atomic batch into the newly selected destination.

**Tech Stack:** Python 3.11+, `ctypes` Win32 DPAPI, standard-library `sqlite3`/`json`, platformdirs, Typer, Rich, pytest, existing PyInstaller and NSIS packaging.

## Global Constraints

- Support Windows 10 and 11 local mode without a master password or server.
- Use current-user `CryptProtectData`/`CryptUnprotectData`; never set `CRYPTPROTECT_LOCAL_MACHINE`.
- Pass `CRYPTPROTECT_UI_FORBIDDEN`, no prompt structure, and no compiled optional entropy.
- Encrypt service, label, username, password, and notes together in every stored record.
- Store the vault under non-roaming `%LOCALAPPDATA%\Passwatcher\vault.db`.
- Preserve the roaming `%APPDATA%\Passwatcher\config.toml` location.
- Treat legacy configuration without a backend marker as remote.
- `pw setup` without flags displays `--local` and `--remote` choices and mutates nothing.
- `-l/--local` and `-r/--remote` are mutually exclusive.
- The last successfully completed setup becomes active; failed/cancelled setup preserves the previous active backend.
- Migrate only when switching backend, not when repairing the already active backend.
- Resolve migration conflicts once for the whole batch as source-wins, destination-wins, or cancel.
- Never display credential values, protected blobs, or secret-bearing request bodies during setup, migration, health, or errors.
- Delete the local vault only after successful local-to-remote migration and an explicit default-no confirmation.
- Remote-to-local migration never deletes or changes the source remote vault beyond reads.
- Keep all existing command names, CSV formats, duplicate policies, renderer styling, and plain-output behavior.

---

### Task 1: Backend-Aware Configuration and Service Interface

**Files:**
- Modify: `src/passwatcher/config.py`
- Modify: `src/passwatcher/service.py`
- Modify: `src/passwatcher/transport.py`
- Modify: `src/passwatcher/setup.py`
- Modify: `tests/client/test_config.py`
- Modify: `tests/client/test_service.py`
- Modify: `tests/client/test_transport.py`
- Modify: `tests/setup/test_setup.py`
- Modify: `tests/setup/test_doctor.py`

**Interfaces:**
- Retains: `ClientConfig(host: str, user: str, port: int = 22, identity_file: Path | None = None)` as remote connection data.
- Produces: `BackendMode.LOCAL`, `BackendMode.REMOTE`.
- Produces: `AppConfig(backend: BackendMode, remote: ClientConfig | None = None)`.
- Changes: `load_config(path: Path) -> AppConfig` and `save_config(path: Path, config: AppConfig) -> None`.
- Produces: `CredentialService` protocol with `search`, `list_all`, `create`, `update`, `delete`, `health`, and `import_many` matching the existing service signatures.

- [ ] **Step 1: Write failing configuration migration tests**

Update `tests/client/test_config.py` with:

```python
from passwatcher.config import AppConfig, BackendMode, ClientConfig


def test_legacy_connection_config_loads_as_remote(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('host="vault"\nuser="nika"\nport=22\n', encoding="utf-8")
    assert load_config(path) == AppConfig(
        BackendMode.REMOTE, ClientConfig("vault", "nika", 22)
    )


def test_local_config_round_trip_can_remember_remote_settings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    expected = AppConfig(
        BackendMode.LOCAL,
        ClientConfig("vault", "nika", 2222, Path("C:/keys/vault")),
    )
    save_config(path, expected)
    assert load_config(path) == expected
    assert 'backend = "local"' in path.read_text(encoding="utf-8")


def test_remote_backend_requires_remote_settings() -> None:
    with pytest.raises(ConfigError, match="remote settings"):
        AppConfig(BackendMode.REMOTE)
```

Update existing save/load callers in tests to wrap remote settings with `AppConfig(BackendMode.REMOTE, remote)`.

- [ ] **Step 2: Run configuration tests and verify RED**

Run: `python -m pytest tests/client/test_config.py tests/client/test_transport.py tests/setup/test_setup.py tests/setup/test_doctor.py -q`

Expected: failures because `AppConfig` and `BackendMode` do not exist and `load_config` still returns remote settings directly.

- [ ] **Step 3: Implement backward-compatible application configuration**

Add:

```python
class BackendMode(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"


@dataclass(frozen=True, slots=True)
class AppConfig:
    backend: BackendMode
    remote: ClientConfig | None = None

    def __post_init__(self) -> None:
        if self.backend is BackendMode.REMOTE and self.remote is None:
            raise ConfigError("remote settings are required for the remote backend")
```

Parse the new top-level `backend` and nested `[remote]` table with exact allowed keys. Parse the legacy root-level connection shape when `backend` is absent. Save only the new shape atomically. Preserve remembered remote settings for a local config; never serialize `None`, secrets, or unknown keys.

Change remote-only setup and transport boundaries to accept `ClientConfig` directly instead of calling `load_config` themselves. Change `Doctor.run` to validate `AppConfig` and require `.remote` only for remote diagnostics.

- [ ] **Step 4: Add the shared service protocol**

Add a structural protocol to `src/passwatcher/service.py`:

```python
class CredentialService(Protocol):
    def search(self, query: str) -> list[CredentialRecord]: ...
    def list_all(self) -> list[CredentialRecord]: ...
    def create(self, service: str, label: str, username: str, password: str, notes: str) -> CredentialRecord: ...
    def update(self, credential_id: int, service: str, label: str, username: str, password: str, notes: str) -> CredentialRecord: ...
    def delete(self, credential_id: int) -> None: ...
    def health(self) -> dict[str, object]: ...
    def import_many(self, records: list[CredentialDraft], duplicates: str) -> ImportSummary: ...
```

Keep `PasswordService` behavior unchanged and make CLI runtime type annotations depend on `CredentialService`.

- [ ] **Step 5: Run focused and full existing tests**

Run: `python -m pytest tests/client/test_config.py tests/client/test_service.py tests/client/test_transport.py tests/setup/test_setup.py tests/setup/test_doctor.py -q`

Run: `python -m pytest tests/client -q`

Expected: all existing remote behavior remains green with the new wrapper configuration.

- [ ] **Step 6: Commit backend-aware configuration**

```powershell
git add -- src/passwatcher/config.py src/passwatcher/service.py src/passwatcher/transport.py src/passwatcher/setup.py tests/client tests/setup/test_setup.py tests/setup/test_doctor.py
git commit -m "refactor: add configurable vault backends"
```

---

### Task 2: Current-User Windows DPAPI Adapter

**Files:**
- Create: `src/passwatcher/local_crypto.py`
- Create: `tests/client/test_local_crypto.py`

**Interfaces:**
- Produces: `DataProtector` protocol with `protect(bytes) -> bytes` and `unprotect(bytes) -> bytes`.
- Produces: `DpapiProtector(api: NativeDpapi | None = None)`.
- Produces: `ProtectionError(code: str, message: str)`.
- Internal: `_CtypesDpapi` owns `DATA_BLOB`, calls `Crypt32`, and frees output through `Kernel32.LocalFree`.

- [ ] **Step 1: Write failing adapter-policy tests with an injected API**

Create `tests/client/test_local_crypto.py`:

```python
class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, int]] = []

    def protect(self, value: bytes, *, description: str, flags: int) -> bytes:
        self.calls.append((description, value, flags))
        return b"protected:" + value[::-1]

    def unprotect(self, value: bytes, *, flags: int) -> bytes:
        self.calls.append(("unprotect", value, flags))
        return value.removeprefix(b"protected:")[::-1]


def test_dpapi_protector_uses_noninteractive_current_user_scope() -> None:
    api = FakeApi()
    protector = DpapiProtector(api)
    protected = protector.protect(b"secret")
    assert protector.unprotect(protected) == b"secret"
    assert all(flags == CRYPTPROTECT_UI_FORBIDDEN for _name, _value, flags in api.calls)
    assert CRYPTPROTECT_LOCAL_MACHINE not in [flags for _name, _value, flags in api.calls]


def test_dpapi_failure_never_contains_input(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenApi(FakeApi):
        def protect(self, value: bytes, *, description: str, flags: int) -> bytes:
            raise OSError(5, "access denied")
    with pytest.raises(ProtectionError) as raised:
        DpapiProtector(BrokenApi()).protect(b"hidden-secret")
    assert "hidden-secret" not in str(raised.value)
```

- [ ] **Step 2: Run DPAPI policy tests and verify RED**

Run: `python -m pytest tests/client/test_local_crypto.py -q`

Expected: collection fails because `passwatcher.local_crypto` does not exist.

- [ ] **Step 3: Implement the policy layer and safe errors**

Define constants exactly:

```python
CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4
DESCRIPTION = "Passwatcher local credential v1"
```

`DpapiProtector` rejects non-bytes input, passes only `CRYPTPROTECT_UI_FORBIDDEN`, and maps every native failure to `ProtectionError("dpapi_failed", "Windows could not protect local vault data")` or the corresponding safe unprotect message.

- [ ] **Step 4: Write Windows-only native round-trip and corruption tests**

Add:

```python
@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI contract")
def test_native_dpapi_round_trip_is_opaque() -> None:
    protector = DpapiProtector()
    plaintext = b"local-dpapi-secret"
    protected = protector.protect(plaintext)
    assert plaintext not in protected
    assert protector.unprotect(protected) == plaintext


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI contract")
def test_native_dpapi_rejects_tampered_blob() -> None:
    protector = DpapiProtector()
    protected = bytearray(protector.protect(b"secret"))
    protected[len(protected) // 2] ^= 1
    with pytest.raises(ProtectionError):
        protector.unprotect(bytes(protected))
```

- [ ] **Step 5: Implement the ctypes native boundary**

Use `ctypes.WinDLL("Crypt32.dll", use_last_error=True)` and `ctypes.WinDLL("Kernel32.dll", use_last_error=True)`. Declare `DATA_BLOB`, all `argtypes`, and `restype`. Keep input buffers alive for the call. Copy output with `ctypes.string_at`, then invoke `LocalFree(output.pbData)` in `finally`, including when copying or decoding fails. Pass `None` for entropy, reserved pointer, and prompt structure. Load DLLs only when constructing the production adapter on Windows.

- [ ] **Step 6: Run DPAPI tests**

Run: `python -m pytest tests/client/test_local_crypto.py -q`

Expected on Windows: all tests pass. Expected elsewhere: policy tests pass and only native integration cases skip.

- [ ] **Step 7: Commit the DPAPI boundary**

```powershell
git add -- src/passwatcher/local_crypto.py tests/client/test_local_crypto.py
git commit -m "feat: protect local data with Windows DPAPI"
```

---

### Task 3: Encrypted Local SQLite Service

**Files:**
- Create: `src/passwatcher/local_vault.py`
- Create: `tests/client/test_local_vault.py`
- Create: `tests/client/local_protector.py`

**Interfaces:**
- Produces: `default_local_vault_path() -> Path`.
- Produces: `LocalPasswordService(path: Path, protector: DataProtector)` implementing `CredentialService`.
- Produces: `LocalVaultError(code: str, message: str)`.
- Produces: `delete_local_vault(path: Path) -> LocalDeleteSummary`.
- Schema: metadata version `1`; encrypted credential document version `1`.

- [ ] **Step 1: Add an authenticated test protector and failing CRUD tests**

Create `tests/client/local_protector.py` with a deterministic test-only HMAC envelope so corruption is detectable without Windows APIs. Create `tests/client/test_local_vault.py`:

```python
def test_local_crud_round_trip_and_plaintext_absence(local_service, vault_path: Path) -> None:
    created = local_service.create("github.com", "work", "nika", "secret", "private")
    assert local_service.search("WORK") == [created]
    raw = vault_path.read_bytes()
    for value in (b"github.com", b"work", b"nika", b"secret", b"private"):
        assert value not in raw
    updated = local_service.update(created.id, "gitlab.com", "", "nika", "new", "")
    assert updated.id == created.id
    local_service.delete(created.id)
    assert local_service.list_all() == []


def test_local_search_and_list_match_remote_order(local_service) -> None:
    local_service.create("Zulu", "", "b", "one", "")
    local_service.create("alpha", "Work", "a", "two", "")
    assert [item.service for item in local_service.list_all()] == ["alpha", "Zulu"]
    assert [item.service for item in local_service.search("work")] == ["alpha"]
```

- [ ] **Step 2: Run local CRUD tests and verify RED**

Run: `python -m pytest tests/client/test_local_vault.py -k "crud or search" -q`

Expected: collection fails because `passwatcher.local_vault` does not exist.

- [ ] **Step 3: Implement encrypted schema and CRUD**

Serialize credential fields only as canonical compact JSON:

```python
payload = json.dumps(
    {"version": 1, "service": service, "label": label, "username": username,
     "password": password, "notes": notes},
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")
```

Protect before executing SQL. Decrypt and strictly validate exact keys, types, version, required fields, and 4,096-byte limits. Map a corrupt blob to `LocalVaultError("decrypt_failed", "The local vault contains unreadable protected data")` without blob bytes or fields.

Initialize metadata and credential tables transactionally. Enable `foreign_keys`, `busy_timeout=5000`, and WAL. Search decrypts all records, matches service/label/username case-insensitively, and sorts by case-folded service, label, username, then ID. Use UTC `Z` timestamps. CRUD requires existing IDs and rolls back protection or SQLite failures.

- [ ] **Step 4: Add failing import, backup, rollback, health, and deletion tests**

Add cases equivalent to the server import contract:

```python
def test_local_import_is_atomic_and_backs_up_before_mutation(local_service, vault_path: Path) -> None:
    local_service.create("existing", "", "nika", "old", "")
    summary = local_service.import_many(
        [CredentialDraft("github", "", "nika", "new", "")], "skip"
    )
    assert summary.inserted == 1
    backups = list((vault_path.parent / "backups").glob("passwatcher-local-*-v1.db"))
    assert len(backups) == 1
    assert b"old" not in backups[0].read_bytes()


def test_corrupt_record_fails_health_without_leaking_blob(local_service, vault_path: Path) -> None:
    record = local_service.create("github", "", "nika", "secret", "")
    with sqlite3.connect(vault_path) as connection:
        connection.execute("UPDATE credentials SET protected = ? WHERE id = ?", (b"broken", record.id))
        connection.commit()
    with pytest.raises(LocalVaultError) as raised:
        local_service.health()
    assert "broken" not in str(raised.value)
```

Also test skip/update/error duplicates, identical input collapse, conflicting input, ambiguous existing identities, 0/3,001 bounds, field limits, backup failure, forced second-statement rollback, SQLite integrity, wrong schema, default path under non-roaming LOCALAPPDATA, and non-Windows production-construction refusal.

Add deletion tests proving default paths are not inferred from broad parents, unrelated files survive, exact DB/WAL/SHM and exact backup names are removed, and directories are removed only when empty.

- [ ] **Step 5: Implement atomic import, health, backup, and exact cleanup**

Reuse the remote service's public `CredentialDraft` and `ImportSummary`. Plan all duplicate decisions before writes. Protect all inserted/updated documents before opening the write transaction. If there is a mutation, create a consistent SQLite backup through `Connection.backup` before `BEGIN`; then apply one transaction and commit once.

`health()` runs `PRAGMA integrity_check`, verifies schema version, decrypts and validates every row, and returns only schema, count, integrity, and protection status.

`delete_local_vault` requires an explicit vault file whose resolved parent equals the resolved Passwatcher local data directory passed by the caller. Delete only exact owned filenames and backup-name regex matches; use `unlink(missing_ok=True)` and non-recursive `rmdir` for empty directories. Return removed/retained counts without paths containing user secrets.

- [ ] **Step 6: Run complete local-vault tests**

Run: `python -m pytest tests/client/test_local_vault.py tests/client/test_local_crypto.py -q`

Expected: all applicable tests pass, with only native DPAPI tests skipped off Windows.

- [ ] **Step 7: Commit the encrypted local vault**

```powershell
git add -- src/passwatcher/local_vault.py tests/client/test_local_vault.py tests/client/local_protector.py
git commit -m "feat: add encrypted local vault service"
```

---

### Task 4: Non-Secret Migration Planner

**Files:**
- Create: `src/passwatcher/migration.py`
- Create: `tests/client/test_migration.py`

**Interfaces:**
- Produces: `ConflictPolicy.SOURCE`, `ConflictPolicy.DESTINATION`.
- Produces: `MigrationPlan(total_source, source_only, identical, conflicts, destination_only, records=repr(False))`.
- Produces: `plan_migration(source: Sequence[CredentialRecord], destination: Sequence[CredentialRecord]) -> MigrationPlan`.
- Produces: `execute_migration(destination: CredentialService, plan: MigrationPlan, conflicts: ConflictPolicy) -> ImportSummary`.
- Raises: `MigrationError(code: str, message: str)` without record values.

- [ ] **Step 1: Write failing migration classification tests**

Create `tests/client/test_migration.py`:

```python
def test_plan_classifies_source_identical_conflict_and_destination_only() -> None:
    plan = plan_migration(
        [record(1, "only-source", password="a"), record(2, "same", password="b"), record(3, "conflict", password="source")],
        [record(8, "same", password="b"), record(9, "conflict", password="destination"), record(10, "only-destination", password="d")],
    )
    assert (plan.source_only, plan.identical, plan.conflicts, plan.destination_only) == (1, 1, 1, 1)
    assert "source" not in repr(plan) and "destination" not in repr(plan)


def test_plan_rejects_ambiguous_identity_without_values() -> None:
    with pytest.raises(MigrationError) as raised:
        plan_migration([record(1, "same"), record(2, " SAME ")], [])
    assert raised.value.code == "ambiguous_source"
    assert "same" not in str(raised.value).casefold()
```

- [ ] **Step 2: Run migration tests and verify RED**

Run: `python -m pytest tests/client/test_migration.py -q`

Expected: collection fails because `passwatcher.migration` does not exist.

- [ ] **Step 3: Implement strict migration planning**

Normalize identity by trimming and case-folding service, label, and username. Reject multiple source or destination records with one identity instead of guessing. Exact equality compares all five mutable fields after the source record is validated; ID and timestamps do not determine equality.

Store source drafts needed for execution in a tuple field declared `repr=False`. Public preview properties expose counts only.

- [ ] **Step 4: Add failing execution-policy tests**

```python
def test_source_wins_uses_one_update_import(fake_destination) -> None:
    plan = plan_migration(source_records(), destination_records())
    summary = execute_migration(fake_destination, plan, ConflictPolicy.SOURCE)
    assert fake_destination.calls == [(list(plan.records), "update")]
    assert summary.total == plan.source_only + plan.conflicts + plan.identical


def test_destination_wins_uses_one_skip_import(fake_destination) -> None:
    plan = plan_migration(source_records(), destination_records())
    execute_migration(fake_destination, plan, ConflictPolicy.DESTINATION)
    assert fake_destination.calls[0][1] == "skip"
```

Assert no call for an empty/identical-only migration, exactly one call otherwise, and failures propagate without converting them to success.

- [ ] **Step 5: Implement one-batch execution and run tests**

`SOURCE` sends source-only plus conflicts with `duplicates="update"`. `DESTINATION` sends source-only plus conflicts with `duplicates="skip"`. Include identical rows only if required for summary consistency; otherwise add their skip count to the destination summary without a write. Never loop over destination service mutations.

Run: `python -m pytest tests/client/test_migration.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit migration planning**

```powershell
git add -- src/passwatcher/migration.py tests/client/test_migration.py
git commit -m "feat: plan safe vault migrations"
```

---

### Task 5: Active Backend Resolution for Existing Commands

**Files:**
- Modify: `src/passwatcher/cli.py`
- Modify: `src/passwatcher/render.py`
- Modify: `tests/client/test_lookup_cli.py`
- Modify: `tests/client/test_csv_cli.py`
- Create: `tests/client/test_backend_selection.py`

**Interfaces:**
- Changes: `create_service(config_path: Path) -> CredentialService`.
- Produces: `create_local_service(path: Path | None = None) -> LocalPasswordService` as a narrow test seam.
- Consumes: `AppConfig`, `BackendMode`, `LocalPasswordService`, and existing remote `PasswordService`.

- [ ] **Step 1: Write failing backend-selection tests**

Create `tests/client/test_backend_selection.py`:

```python
def test_create_service_selects_local_without_constructing_ssh(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    save_config(config, AppConfig(BackendMode.LOCAL))
    local = object()
    monkeypatch.setattr(cli_module, "create_local_service", lambda: local)
    monkeypatch.setattr(cli_module, "SshTransport", lambda _config: pytest.fail("SSH constructed"))
    assert cli_module.create_service(config) is local


def test_create_service_selects_remembered_remote(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config.toml"
    remote = ClientConfig("vault", "nika")
    save_config(config, AppConfig(BackendMode.REMOTE, remote))
    seen = []
    monkeypatch.setattr(cli_module, "SshTransport", lambda value: seen.append(value) or FakeTransport())
    assert isinstance(cli_module.create_service(config), PasswordService)
    assert seen == [remote]
```

- [ ] **Step 2: Run selection tests and verify RED**

Run: `python -m pytest tests/client/test_backend_selection.py -q`

Expected: local selection fails because `create_service` always constructs SSH.

- [ ] **Step 3: Implement one backend resolution point**

Load `AppConfig` once. For local, construct the fixed-path production `LocalPasswordService` with `DpapiProtector`; for remote, require the nested connection and construct the existing transport service. Map `LocalVaultError` and `ProtectionError` through `_render_expected_error` with a local-specific corrective message. Do not catch or print protected bytes.

Change `_Runtime.service` to `CredentialService | None`; every existing command continues calling `_service(runtime)` without backend branches.

- [ ] **Step 4: Prove existing commands are backend-neutral**

Add parametrized tests invoking lookup, list, add, edit, delete, CSV import, and CSV export with both fake local and fake remote services. Assert identical stdout/clipboard/write calls for each backend kind and no SSH constructor use for the local cases.

Run: `python -m pytest tests/client/test_backend_selection.py tests/client/test_lookup_cli.py tests/client/test_csv_cli.py tests/client/test_crud_cli.py -q`

Expected: all pass.

- [ ] **Step 5: Commit active service resolution**

```powershell
git add -- src/passwatcher/cli.py src/passwatcher/render.py tests/client/test_backend_selection.py tests/client/test_lookup_cli.py tests/client/test_csv_cli.py
git commit -m "feat: route commands to the active vault"
```

---

### Task 6: Two-Mode Setup, Migration, and Local Cleanup

**Files:**
- Modify: `src/passwatcher/cli.py`
- Modify: `src/passwatcher/prompts.py`
- Modify: `src/passwatcher/render.py`
- Create: `tests/setup/test_setup_modes.py`
- Modify: `tests/setup/test_doctor.py`

**Interfaces:**
- Changes CLI: `pw setup [-l|--local] [-r|--remote]`.
- Produces renderer methods: `setup_choices()`, `migration_preview(plan)`, `migration_complete(summary)`, `local_delete_warning()`.
- Produces prompt: `migration_conflict_policy() -> ConflictPolicy | None`.

- [ ] **Step 1: Write failing chooser and flag tests**

Create `tests/setup/test_setup_modes.py`:

```python
def test_setup_without_mode_only_shows_choices(cli, mutation_spies) -> None:
    result = cli.invoke(app, ["setup"])
    assert result.exit_code == 0
    assert "pw setup --local" in result.stdout
    assert "pw setup --remote" in result.stdout
    assert mutation_spies.calls == []


def test_setup_rejects_both_modes_before_mutation(cli, mutation_spies) -> None:
    result = cli.invoke(app, ["setup", "--local", "--remote"])
    assert result.exit_code != 0
    assert mutation_spies.calls == []
```

- [ ] **Step 2: Run chooser tests and verify RED**

Run: `python -m pytest tests/setup/test_setup_modes.py -k "choices or both_modes" -q`

Expected: failure because setup has no mode flags and immediately prompts for SSH.

- [ ] **Step 3: Implement non-mutating chooser and mode flags**

Add boolean options `-l/--local` and `-r/--remote`. Return after renderer choices when neither is set. Reject both using `typer.BadParameter` before constructing setup managers, services, paths, or protectors. Move current remote logic into `_setup_remote`; add `_setup_local` as a separate function.

- [ ] **Step 4: Write failing activation and migration tests**

Add cases:

```python
def test_local_setup_activates_only_after_health_and_remote_migration(cli, configured_remote, fakes) -> None:
    result = cli.invoke(app, ["setup", "--local"], input="source\ny\n")
    assert result.exit_code == 0
    assert fakes.events == ["local-init", "local-health", "remote-list", "local-import", "save-local"]
    assert load_config(configured_remote).backend is BackendMode.LOCAL


def test_failed_migration_preserves_previous_backend(cli, configured_local, fakes) -> None:
    fakes.local_to_remote_failure = LocalVaultError("import_failed", "failed")
    result = cli.invoke(app, ["setup", "--remote"], input=remote_answers())
    assert result.exit_code == 1
    assert load_config(configured_local).backend is BackendMode.LOCAL


def test_remote_switch_prompts_default_no_local_delete_after_activation(cli, configured_local, fakes) -> None:
    result = cli.invoke(app, ["setup", "--remote"], input=remote_answers() + "destination\nn\n")
    assert result.exit_code == 0
    assert load_config(configured_local).backend is BackendMode.REMOTE
    assert fakes.local_delete_calls == 0
```

Also test source-wins, destination-wins, cancel, identical-only, no-source-records, remote-to-local leaves remote intact, missing config plus existing local migrates on remote setup, already-active remote does not merge stale local, remembered remote values prefill prompts, local setup rejects non-Windows, confirmation precedes SSH, and deletion occurs only after save plus explicit yes.

- [ ] **Step 5: Implement migration orchestration with event-safe ordering**

For both directions:

1. load the prior config if present without modifying it
2. set up and health-check the destination
3. when switching, load source and destination records
4. render non-secret `MigrationPlan` counts
5. ask one conflict choice only if conflicts exist
6. cancel without saving on cancel
7. call `execute_migration` once
8. health-check the destination again
9. atomically save the destination as active while preserving remembered remote settings
10. only for local-to-remote, offer default-no local deletion

When setup mode equals the prior active backend, perform its setup/health repair and save refreshed settings without consulting the inactive backend.

Use safe `prompts.text` choices `source`, `destination`, and `cancel`, looping on invalid input. Renderer previews include counts and backend names only.

- [ ] **Step 6: Run setup, migration, and regression tests**

Run: `python -m pytest tests/setup/test_setup_modes.py tests/setup/test_doctor.py tests/client/test_migration.py tests/client/test_crud_cli.py -q`

Expected: all tests pass with secret-free captured output.

- [ ] **Step 7: Commit mode setup and migration**

```powershell
git add -- src/passwatcher/cli.py src/passwatcher/prompts.py src/passwatcher/render.py tests/setup/test_setup_modes.py tests/setup/test_doctor.py
git commit -m "feat: switch and migrate vault backends"
```

---

### Task 7: Local Doctor, Installer Lifecycle, Documentation, and Version

**Files:**
- Modify: `src/passwatcher/setup.py`
- Modify: `src/passwatcher/cli.py`
- Modify: `packaging/passwatcher.nsi`
- Modify: `tests/setup/test_doctor.py`
- Modify: `tests/setup/test_packaging_files.py`
- Modify: `tests/setup/windows-installer-smoke.ps1`
- Modify: `tests/setup/windows-installer-safety-smoke.ps1`
- Modify: `README.md`
- Modify: `docs/BUILDING_INSTALLER.md`
- Modify: `pyproject.toml`
- Modify: `src/passwatcher/__init__.py`

**Interfaces:**
- Produces: `LocalDoctor(service: LocalPasswordService).run() -> list[DoctorCheck]`.
- Changes installer: separate default-no prompts for roaming configuration and local DPAPI data.
- Changes release version: `0.2.0` in both declarations.

- [ ] **Step 1: Write failing local doctor dispatch tests**

Add to `tests/setup/test_doctor.py`:

```python
def test_doctor_uses_local_checks_without_ssh(cli, local_config, monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "create_doctor", lambda: pytest.fail("remote doctor used"))
    local = FakeLocalDoctor([("DPAPI", True, "current-user protection available"), ("SQLite integrity", True, "ok")])
    monkeypatch.setattr(cli_module, "create_local_doctor", lambda: local)
    result = cli.invoke(app, ["--config", str(local_config), "doctor"])
    assert result.exit_code == 0
    assert "DPAPI" in result.stdout and "SQLite integrity" in result.stdout
```

Test platform failure, missing vault, integrity failure, unreadable protected row, record-count reporting, and no secret output.

- [ ] **Step 2: Run doctor tests and verify RED**

Run: `python -m pytest tests/setup/test_doctor.py -k local -q`

Expected: failure because doctor always uses the remote implementation.

- [ ] **Step 3: Implement read-only local diagnostics**

Dispatch doctor from `AppConfig.backend`. Local doctor checks platform, canonical path, existence, schema, SQLite integrity, DPAPI decryption of every record, record count, and backup-directory readability. It performs no create, repair, backup, or re-protection. Remote doctor behavior remains unchanged.

- [ ] **Step 4: Write failing installer-local-data contract tests**

Add to `tests/setup/test_packaging_files.py`:

```python
def test_uninstaller_preserves_local_dpapi_vault_by_default() -> None:
    text = Path("packaging/passwatcher.nsi").read_text(encoding="utf-8")
    assert 'MessageBox MB_YESNO|MB_ICONQUESTION "Remove the local DPAPI vault and its backups too?" /SD IDNO' in text
    assert 'Delete "$LOCALAPPDATA\\Passwatcher\\vault.db"' in text
    assert 'RMDir /r "$LOCALAPPDATA\\Passwatcher"' not in text


def test_feature_release_version_is_0_2_0() -> None:
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == "0.2.0"
    assert passwatcher.__version__ == "0.2.0"
```

Extend smoke scripts to refuse pre-existing `%LOCALAPPDATA%\Passwatcher`, create an encrypted-vault sentinel only in their isolated account, prove upgrade preservation, prove silent-uninstall preservation, and prove explicit cleanup cannot escape the exact local data directory. Use the existing cleanup helper and canary rules.

- [ ] **Step 5: Implement exact NSIS lifecycle and bump version**

On uninstall, after the existing configuration prompt, add a separate default-no local-vault prompt. On yes, delete only exact DB/WAL/SHM names and exact `backups\passwatcher-local-*-v1.db` files, then remove backup/data directories only if empty. Do not recursively remove `%LOCALAPPDATA%\Passwatcher`.

Set both version declarations to `0.2.0`. Run `python tools/verify_release_version.py v0.2.0`.

- [ ] **Step 6: Update README and installer guide**

Document:

```powershell
pw setup
pw setup --local
pw setup --remote
```

Explain active-backend persistence, switching/migration rules, global conflict choice, default-no local deletion, fixed paths, DPAPI same-user/device behavior, administrator password-reset recovery risk, same-user malware limitation, BitLocker recommendation, encrypted offline backup recommendation, remote plaintext security model, local/remote doctor behavior, and `v0.2.0` release commands. Remove the claim that Passwatcher never stores records on Windows; replace it with mode-specific wording.

- [ ] **Step 7: Run doctor, packaging, guide, and version tests**

Run: `python -m pytest tests/setup/test_doctor.py tests/setup/test_packaging_files.py tests/setup/test_installer_guide.py tests/setup/test_release_version.py -q`

Run: `python tools/verify_release_version.py v0.2.0`

Expected: tests pass, version prints `0.2.0`, and only environment-dependent NSIS cases may skip.

- [ ] **Step 8: Commit lifecycle and documentation**

```powershell
git add -- src/passwatcher/setup.py src/passwatcher/cli.py packaging/passwatcher.nsi tests/setup README.md docs/BUILDING_INSTALLER.md pyproject.toml src/passwatcher/__init__.py
git commit -m "docs: complete local vault release lifecycle"
```

---

### Task 8: Packaging and End-to-End Verification

**Files:**
- Modify only if verification exposes a defect: files and tests named in Tasks 1–7.
- Rebuild: `src/passwatcher/assets/passwatcher-server.pyz` only through `tools/build_server_zipapp.py`.

**Interfaces:**
- Produces a clean `main` tree whose installer includes local-vault modules and whose remote bundle remains deterministic.

- [ ] **Step 1: Run all local-mode focused tests**

Run: `python -m pytest tests/client/test_local_crypto.py tests/client/test_local_vault.py tests/client/test_migration.py tests/client/test_backend_selection.py tests/setup/test_setup_modes.py tests/setup/test_doctor.py -q`

Expected: all applicable tests pass; only native Windows cases skip off Windows.

- [ ] **Step 2: Run complete tests before packaging**

Run: `python -m pytest -q`

Expected: zero failures and only declared platform/tool skips.

- [ ] **Step 3: Rebuild and prove deterministic remote bundle**

Run: `python tools/build_server_zipapp.py`

Run: `(Get-FileHash -Algorithm SHA256 src/passwatcher/assets/passwatcher-server.pyz).Hash`

Run the builder and hash a second time. Expected: both hashes are identical and `git diff -- src/passwatcher/assets/passwatcher-server.pyz` is empty unless server source actually changed.

- [ ] **Step 4: Run packaging-specific tests**

Run: `python -m pytest tests/server/test_zipapp.py tests/setup/test_packaging_files.py tests/setup/test_release_workflow.py -q`

Expected: all tests pass, including PyInstaller data/module discovery and release contracts.

- [ ] **Step 5: Build the Windows installer**

Run: `powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1`

Expected: `dist\Passwatcher-Setup-0.2.0.exe` exists and the build script has already run the complete suite.

- [ ] **Step 6: Run guarded installer smoke tests only in the disposable test context**

```powershell
$env:PASSWATCHER_SMOKE_ISOLATED_USER = "1"
powershell -ExecutionPolicy Bypass -File tests/setup/windows-installer-smoke.ps1 -Installer "dist/Passwatcher-Setup-0.2.0.exe"
$env:PASSWATCHER_SMOKE_DESTRUCTIVE_CANARIES = "1"
powershell -ExecutionPolicy Bypass -File tests/setup/windows-installer-safety-smoke.ps1 -Installer "dist/Passwatcher-Setup-0.2.0.exe"
```

Expected: install, upgrade, PATH, DPAPI local-data preservation, explicit cleanup boundaries, and uninstall checks pass without touching pre-existing user state.

- [ ] **Step 7: Manually exercise both backends against disposable vaults**

Run local setup, add/list/search/edit/delete, CSV import/export, doctor, switch to a disposable remote, choose both conflict policies in separate test vaults, decline and accept local deletion in separate runs, and switch back to local. Confirm normal command styling is identical and no migration preview prints record values.

- [ ] **Step 8: Final repository checks**

Run: `git diff --check`

Run: `git status --short`

Run: `git log -12 --oneline`

Expected: clean working tree and focused commits in dependency order.

