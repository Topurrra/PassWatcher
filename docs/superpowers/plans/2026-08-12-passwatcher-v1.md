# Passwatcher V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polished Windows password-manager CLI that performs safe CRUD operations against one permissions-locked SQLite vault on a Linux server through SSH, including guided idempotent setup and an NSIS installer.

**Architecture:** A Typer/Rich Windows client sends one versioned JSON request per invocation to the fixed `~/.local/bin/passwatcher-server rpc` command through OpenSSH standard input and reads one JSON response from standard output. A standard-library-only Python server validates requests and owns SQLite transactions, schema migration, matching, and health checks. PyInstaller produces `pw.exe`; NSIS installs it per-user and adds it to the user `PATH`.

**Tech Stack:** Python 3.11+, Typer 0.12+, Rich 13.7+, platformdirs 4+, pyperclip 1.9+, pytest 8+, PyInstaller 6+, NSIS 3.x, standard-library `sqlite3` on Linux.

## Global Constraints

- Target Windows 10/11 clients and a Linux server with Python 3.11+ and an existing OpenSSH service.
- Treat the product as single-user; do not add accounts, sharing, HTTP, a daemon, a browser extension, or server-side encryption.
- Store the only vault at `~/.local/share/passwatcher/passwatcher.db`; use mode `0600` for the database and backups and `0700` for directories and executables.
- Invoke the fixed remote command `~/.local/bin/passwatcher-server rpc`; send secrets only in one JSON object on standard input, never in SSH command arguments.
- Every protocol request and response contains `protocol_version: 1`.
- Search case-insensitively across service, label, and username; order by service, label, username, then ID.
- For zero matches copy nothing; for one match show the password and copy only the password; for multiple matches show all passwords and copy nothing.
- Store only SSH connection configuration in `%APPDATA%\Passwatcher\config.toml`; never cache credential records on Windows.
- `pw setup` is idempotent across devices and must preserve an existing compatible database.
- The CLI follows the approved Midnight Neon visual direction and provides color-disabled/plain-output behavior.
- Follow test-driven development: record RED and GREEN commands/output in every task report.
- Never expose passwords or secret-bearing request bodies in normal errors, debug logs, tracebacks, or installer logs.

---

## File Structure

- `pyproject.toml`: package metadata, runtime dependencies, pytest settings, and the `pw` entry point.
- `src/passwatcher/cli.py`: Typer command definitions and exit behavior only.
- `src/passwatcher/config.py`: TOML client configuration loading, validation, and atomic saving.
- `src/passwatcher/protocol.py`: client request construction and response validation.
- `src/passwatcher/transport.py`: argument-vector OpenSSH execution with JSON over stdin.
- `src/passwatcher/service.py`: client-facing typed operations independent of UI and transport implementation.
- `src/passwatcher/render.py`: Midnight Neon panels, tables, status, and plain-output rendering.
- `src/passwatcher/prompts.py`: interactive add/edit/delete/setup input and cancellation.
- `src/passwatcher/passwords.py`: cryptographically secure password generation.
- `src/passwatcher/clipboard.py`: injectable Windows clipboard boundary.
- `src/passwatcher/setup.py`: SSH inspection, server bundle upload/install, idempotent setup, and doctor checks.
- `src/passwatcher/assets/passwatcher-server.pyz`: generated standard-library-only Linux server bundle.
- `src/passwatcher_server/models.py`: server request validation and stable error types.
- `src/passwatcher_server/database.py`: schema, migrations, transactions, matching, CRUD, and health checks.
- `src/passwatcher_server/rpc.py`: one-request stdin/stdout JSON dispatcher.
- `src/passwatcher_server/__main__.py`: `rpc` process entry point.
- `tools/build_server_zipapp.py`: reproducibly creates the server `.pyz` bundled with the client.
- `packaging/passwatcher.nsi`: per-user NSIS install, PATH update, upgrade, and uninstall behavior.
- `tests/client/`: client unit and command tests.
- `tests/server/`: SQLite and RPC integration tests.
- `tests/setup/`: setup and packaging behavior tests.
- `scripts/linux-smoke-test.sh`: temporary-home Linux installation smoke test.

---

### Task 1: Project Foundation, Client Configuration, and Protocol

**Files:**
- Create: `pyproject.toml`
- Create: `src/passwatcher/__init__.py`
- Create: `src/passwatcher/config.py`
- Create: `src/passwatcher/protocol.py`
- Create: `tests/client/test_config.py`
- Create: `tests/client/test_protocol.py`

**Interfaces:**
- Produces: `ClientConfig(host: str, user: str, port: int, identity_file: Path | None)`
- Produces: `load_config(path: Path) -> ClientConfig`, `save_config(path: Path, config: ClientConfig) -> None`
- Produces: `make_request(operation: str, payload: dict[str, object]) -> bytes`
- Produces: `parse_response(raw: bytes) -> dict[str, object]`
- Produces: `ProtocolError(code: str, message: str)`

- [ ] **Step 1: Add package metadata and test configuration**

Create `pyproject.toml` with Python `>=3.11`, runtime dependencies `typer>=0.12,<1`, `rich>=13.7,<15`, `platformdirs>=4,<5`, `pyperclip>=1.9,<2`, `tomli-w>=1,<2`, development dependencies `pytest>=8,<9`, `pyinstaller>=6,<7`, the `pw = "passwatcher.cli:app"` script, `src` package discovery, and pytest paths set to `tests`.

- [ ] **Step 2: Write failing configuration tests**

```python
def test_config_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    expected = ClientConfig("vault.example", "nika", 2222, Path("C:/keys/vault"))
    save_config(path, expected)
    assert load_config(path) == expected
    assert "password" not in path.read_text(encoding="utf-8").lower()

def test_config_rejects_invalid_port(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('host="vault"\nuser="nika"\nport=70000\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="port"):
        load_config(path)
```

- [ ] **Step 3: Run configuration tests and verify RED**

Run: `python -m pytest tests/client/test_config.py -q`

Expected: collection fails because `passwatcher.config` does not exist.

- [ ] **Step 4: Implement immutable validated configuration and atomic writes**

```python
@dataclass(frozen=True, slots=True)
class ClientConfig:
    host: str
    user: str
    port: int = 22
    identity_file: Path | None = None

    def __post_init__(self) -> None:
        if not self.host.strip() or not self.user.strip():
            raise ConfigError("host and user are required")
        if not 1 <= self.port <= 65535:
            raise ConfigError("port must be between 1 and 65535")
```

Serialize only `host`, `user`, `port`, and optional `identity_file` using `tomllib`/`tomli_w`; write a sibling temporary file and replace the target atomically after creating its parent.

- [ ] **Step 5: Run configuration tests and verify GREEN**

Run: `python -m pytest tests/client/test_config.py -q`

Expected: all configuration tests pass with no warnings.

- [ ] **Step 6: Write failing protocol tests**

```python
def test_request_is_versioned_json_line():
    raw = make_request("search", {"query": "github"})
    assert json.loads(raw) == {
        "protocol_version": 1,
        "operation": "search",
        "payload": {"query": "github"},
    }

def test_response_rejects_wrong_version():
    raw = b'{"protocol_version":2,"ok":true,"result":{}}'
    with pytest.raises(ProtocolError) as error:
        parse_response(raw)
    assert error.value.code == "incompatible_protocol"

def test_response_maps_safe_server_error():
    raw = b'{"protocol_version":1,"ok":false,"error":{"code":"not_found","message":"No match"}}'
    with pytest.raises(ProtocolError, match="No match") as error:
        parse_response(raw)
    assert error.value.code == "not_found"
```

- [ ] **Step 7: Run protocol tests and verify RED**

Run: `python -m pytest tests/client/test_protocol.py -q`

Expected: tests fail because the protocol functions are missing.

- [ ] **Step 8: Implement strict client protocol helpers**

Use compact UTF-8 JSON without logging the payload. Reject empty/non-object responses, wrong versions, non-boolean `ok`, malformed error objects, and successful responses without an object/list/scalar `result`. Raise `ProtocolError("malformed_response", "The server returned an invalid response")` for unsafe or malformed content.

- [ ] **Step 9: Run Task 1 tests and commit**

Run: `python -m pytest tests/client/test_config.py tests/client/test_protocol.py -q`

Expected: all tests pass with pristine output.

Commit: `git add pyproject.toml src/passwatcher tests/client && git commit -m "feat: add client foundation and protocol"`

---

### Task 2: Transactional SQLite Vault

**Files:**
- Create: `src/passwatcher_server/__init__.py`
- Create: `src/passwatcher_server/models.py`
- Create: `src/passwatcher_server/database.py`
- Create: `tests/server/test_database.py`

**Interfaces:**
- Produces: `Credential(id, service, label, username, password, notes, created_at, updated_at)`
- Produces: `Vault(path: Path, backup_dir: Path)` with `initialize()`, `create()`, `search()`, `list_all()`, `update()`, `delete()`, `health()`
- Produces: `ValidationError(code: str, message: str)`, `NotFoundError`, and `DatabaseError`

- [ ] **Step 1: Write failing schema and CRUD tests**

```python
def test_initialize_is_idempotent_and_create_round_trips(vault):
    vault.initialize()
    vault.initialize()
    created = vault.create(service="github.com", label="personal", username="nika@example.com", password="s3cret", notes="main")
    assert created.id == 1
    assert vault.search("github") == [created]

def test_search_is_case_insensitive_across_required_fields(vault):
    vault.initialize()
    vault.create(service="github.com", label="Work", username="nika@company.test", password="one", notes="")
    vault.create(service="gitlab.com", label="Personal", username="nika@example.test", password="two", notes="")
    assert [item.service for item in vault.search("WORK")] == ["github.com"]
    assert len(vault.search("NIKA")) == 2
```

- [ ] **Step 2: Run database tests and verify RED**

Run: `python -m pytest tests/server/test_database.py -q`

Expected: collection fails because `passwatcher_server.database` does not exist.

- [ ] **Step 3: Implement schema version 1 and validated create/search/list**

Create `metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)` and `credentials` with the exact design fields. Enable `PRAGMA foreign_keys=ON`, `PRAGMA journal_mode=WAL`, and `PRAGMA busy_timeout=5000` per connection. Use UTC ISO-8601 timestamps ending in `Z`. Normalize surrounding whitespace but preserve case and secret bytes. Reject empty service, username, or password and fields over 4096 UTF-8 bytes.

Use this search predicate and ordering:

```sql
WHERE instr(lower(service), lower(?)) > 0
   OR instr(lower(coalesce(label, '')), lower(?)) > 0
   OR instr(lower(username), lower(?)) > 0
ORDER BY lower(service), lower(coalesce(label, '')), lower(username), id
```

- [ ] **Step 4: Run CRUD tests and verify GREEN**

Run: `python -m pytest tests/server/test_database.py -q`

Expected: schema, create, search, and list tests pass.

- [ ] **Step 5: Add failing update/delete/rollback/permission tests**

```python
def test_failed_update_rolls_back(vault):
    original = vault.create(service="github.com", label="", username="nika", password="old", notes="")
    with pytest.raises(ValidationError):
        vault.update(original.id, service="", label="", username="nika", password="new", notes="")
    assert vault.search("github")[0].password == "old"

def test_delete_requires_existing_id(vault):
    with pytest.raises(NotFoundError):
        vault.delete(999)

@pytest.mark.skipif(os.name == "nt", reason="POSIX permission assertion")
def test_database_is_owner_read_write_only(vault):
    vault.initialize()
    assert stat.S_IMODE(vault.path.stat().st_mode) == 0o600
```

- [ ] **Step 6: Implement transactional update, delete, health, and migration backup guard**

Each write opens one connection with a context-manager transaction. Update all mutable fields and `updated_at`, checking `rowcount == 1`. Delete by ID and require `rowcount == 1`. `health()` returns schema version, record count, `PRAGMA integrity_check`, and permission status without credential values. Before any future version migration, copy the database to `backups/passwatcher-<UTC timestamp>-v<old>.db`, chmod it `0600`, then migrate in one transaction.

- [ ] **Step 7: Run Task 2 tests and commit**

Run: `python -m pytest tests/server/test_database.py -q`

Expected: all database tests pass with pristine output.

Commit: `git add src/passwatcher_server tests/server/test_database.py && git commit -m "feat: add transactional sqlite vault"`

---

### Task 3: Linux JSON RPC Server and Zipapp

**Files:**
- Create: `src/passwatcher_server/rpc.py`
- Create: `src/passwatcher_server/__main__.py`
- Create: `tools/build_server_zipapp.py`
- Create: `tests/server/test_rpc.py`
- Create: `tests/server/test_zipapp.py`

**Interfaces:**
- Produces: `handle_request(raw: bytes, vault: Vault) -> bytes`
- Produces operations: `search`, `list`, `create`, `update`, `delete`, `health`
- Produces CLI: `python passwatcher-server.pyz rpc`
- Produces artifact: `src/passwatcher/assets/passwatcher-server.pyz`

- [ ] **Step 1: Write failing RPC validation and search tests**

```python
def test_rpc_search_returns_versioned_result(vault):
    vault.create(service="github.com", label="personal", username="nika", password="secret", notes="")
    response = invoke(vault, "search", {"query": "github"})
    assert response["protocol_version"] == 1
    assert response["ok"] is True
    assert response["result"][0]["password"] == "secret"

def test_rpc_rejects_unknown_and_oversized_requests(vault):
    assert invoke(vault, "unknown", {})["error"]["code"] == "unknown_operation"
    raw = b"{" + b"x" * 1_048_577
    response = json.loads(handle_request(raw, vault))
    assert response["error"]["code"] == "request_too_large"
```

- [ ] **Step 2: Run RPC tests and verify RED**

Run: `python -m pytest tests/server/test_rpc.py -q`

Expected: tests fail because `handle_request` is missing.

- [ ] **Step 3: Implement strict dispatcher and safe error envelopes**

Accept at most 1 MiB. Require exactly `protocol_version`, `operation`, and `payload` at the request top level. Validate each operation's required and permitted payload fields before calling `Vault`. Map known exceptions to stable codes; map unexpected exceptions to `internal_error` with message `The server could not complete the request` and no traceback or request echo on stdout.

- [ ] **Step 4: Add failing subprocess and zipapp tests**

```python
def test_rpc_process_reads_one_request_and_writes_one_response(server_command, tmp_path):
    request = json.dumps({"protocol_version": 1, "operation": "health", "payload": {}})
    completed = subprocess.run(server_command + ["rpc"], input=request, text=True, capture_output=True, env={"PASSWATCHER_DATA_DIR": str(tmp_path)})
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["ok"] is True
    assert completed.stderr == ""

def test_built_zipapp_contains_no_third_party_imports(zipapp_path):
    completed = subprocess.run([sys.executable, str(zipapp_path), "rpc"], input=health_request, text=True, capture_output=True, env=minimal_env)
    assert completed.returncode == 0
```

- [ ] **Step 5: Implement process entry point and reproducible zipapp builder**

`__main__.py` accepts only `rpc`, resolves the data directory from `PASSWATCHER_DATA_DIR` for tests or `~/.local/share/passwatcher` normally, initializes the vault, reads bounded stdin, writes exactly one response plus newline, and exits `0` for protocol-level errors. The builder stages `passwatcher_server` and a root `__main__.py`, normalizes every staged file timestamp to `1980-01-01T00:00:00`, and calls `zipapp.create_archive(staging_dir, target=output_path, interpreter="/usr/bin/env python3", compressed=True)`. Traverse and copy source paths in lexical order so two builds from the same source have the same SHA-256 digest.

- [ ] **Step 6: Run Task 3 tests, build the bundle, and commit**

Run: `python -m pytest tests/server/test_rpc.py tests/server/test_zipapp.py -q`

Run: `python tools/build_server_zipapp.py`

Expected: all tests pass and `src/passwatcher/assets/passwatcher-server.pyz` is created reproducibly.

Commit: `git add src/passwatcher_server src/passwatcher/assets tools tests/server && git commit -m "feat: add linux rpc server bundle"`

---

### Task 4: OpenSSH Transport and Typed Client Service

**Files:**
- Create: `src/passwatcher/transport.py`
- Create: `src/passwatcher/service.py`
- Create: `tests/client/test_transport.py`
- Create: `tests/client/test_service.py`

**Interfaces:**
- Produces: `SshTransport(config: ClientConfig, timeout_seconds: float = 15.0).request(raw: bytes) -> bytes`
- Produces: `CredentialRecord` client value object
- Produces: `PasswordService(transport)` methods `search`, `list_all`, `create`, `update`, `delete`, `health`
- Consumes: Task 1 protocol/config and Task 3 RPC operation/result shapes

- [ ] **Step 1: Write failing argument-vector and stdin transport tests**

```python
def test_transport_uses_fixed_remote_command_and_json_stdin(monkeypatch, config):
    seen = {}
    def fake_run(argv, **kwargs):
        seen.update(argv=argv, kwargs=kwargs)
        return CompletedProcess(argv, 0, stdout=b'{"protocol_version":1,"ok":true,"result":{}}', stderr=b"")
    monkeypatch.setattr(subprocess, "run", fake_run)
    SshTransport(config).request(b'{"password":"a b;$(bad)"}')
    assert seen["argv"][-1] == "~/.local/bin/passwatcher-server rpc"
    assert "a b;$(bad)" not in seen["argv"]
    assert seen["kwargs"]["input"] == b'{"password":"a b;$(bad)"}'
    assert seen["kwargs"]["shell"] is False
```

- [ ] **Step 2: Run transport tests and verify RED**

Run: `python -m pytest tests/client/test_transport.py -q`

Expected: tests fail because `SshTransport` is missing.

- [ ] **Step 3: Implement OpenSSH execution and safe error mapping**

Build `ssh`, `-T`, `-p`, the decimal port, optional `-i` identity path, `--`, `user@host`, and the fixed remote command as a list. Use `shell=False`, byte input/output, timeout, `stdin=PIPE`, and no inherited stderr. Convert missing executable, timeout, exit `255`, other nonzero exits, and empty output into typed transport errors whose normal messages never include stdin.

- [ ] **Step 4: Write failing service mapping tests**

```python
def test_service_search_maps_records(fake_transport):
    fake_transport.result = [{"id": 7, "service": "github.com", "label": "work", "username": "nika", "password": "secret", "notes": "", "created_at": "2026-08-12T00:00:00Z", "updated_at": "2026-08-12T00:00:00Z"}]
    assert PasswordService(fake_transport).search("github")[0].id == 7

def test_service_never_places_secret_in_operation_name(fake_transport):
    PasswordService(fake_transport).create("github.com", "work", "nika", "a b;secret", "")
    assert fake_transport.request_json["operation"] == "create"
    assert fake_transport.request_json["payload"]["password"] == "a b;secret"
```

- [ ] **Step 5: Implement typed service methods and response shape checks**

Each service method creates one Task 1 request, invokes transport, parses one response, and converts credential dictionaries to immutable `CredentialRecord` objects. Reject missing or wrong field types as `ProtocolError("malformed_response", "The server returned an invalid credential record")`.

- [ ] **Step 6: Run Task 4 tests and commit**

Run: `python -m pytest tests/client/test_transport.py tests/client/test_service.py -q`

Expected: all tests pass with pristine output.

Commit: `git add src/passwatcher/transport.py src/passwatcher/service.py tests/client && git commit -m "feat: add ssh client service"`

---

### Task 5: Midnight Neon Lookup, List, and Clipboard Rules

**Files:**
- Create: `src/passwatcher/render.py`
- Create: `src/passwatcher/clipboard.py`
- Create: `src/passwatcher/cli.py`
- Create: `tests/client/test_lookup_cli.py`
- Create: `tests/client/test_list_cli.py`

**Interfaces:**
- Produces: Typer `app`
- Produces: default callback accepting query words and global `--plain`, `--debug`, `--config`
- Produces: `list` command with `--secrets`
- Produces: `Clipboard.copy(text: str) -> None`
- Consumes: Task 4 `PasswordService`

- [ ] **Step 1: Write failing zero/one/many lookup behavior tests**

```python
def test_one_match_shows_and_copies_password(cli, service, clipboard):
    service.matches = [record(password="only-secret")]
    result = cli.invoke(app, ["github"])
    assert result.exit_code == 0
    assert "only-secret" in result.stdout
    assert clipboard.values == ["only-secret"]

def test_many_matches_show_all_and_copy_nothing(cli, service, clipboard):
    service.matches = [record(label="personal", password="one"), record(label="work", password="two")]
    result = cli.invoke(app, ["github"])
    assert result.exit_code == 0
    assert "one" in result.stdout and "two" in result.stdout
    assert clipboard.values == []

def test_no_match_copies_nothing(cli, service, clipboard):
    service.matches = []
    result = cli.invoke(app, ["missing"])
    assert result.exit_code == 1
    assert "No credentials found" in result.stdout
    assert clipboard.values == []
```

- [ ] **Step 2: Run lookup tests and verify RED**

Run: `python -m pytest tests/client/test_lookup_cli.py -q`

Expected: tests fail because the CLI and renderer are missing.

- [ ] **Step 3: Implement renderer theme and lookup callback**

Define Midnight Neon colors `background=#090d14`, `cyan=#67e8f9`, `violet=#a78bfa`, `green=#4ade80`, `red=#fb7185`, and muted `#64748b`. Use a compact Rich panel for one match and a table with ID, service, label, username, and password for many matches. When `--plain` or no-color output is active, use stable headings and tab-separated rows without ANSI sequences.

The default callback joins query words with one ASCII space, rejects an empty query with usage, calls `service.search`, then applies the exact Global Constraints clipboard behavior.

- [ ] **Step 4: Write failing list visibility tests**

```python
def test_list_hides_passwords_by_default(cli, service):
    service.all_records = [record(password="hidden-secret")]
    result = cli.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "hidden-secret" not in result.stdout

def test_list_secrets_reveals_passwords(cli, service):
    service.all_records = [record(password="visible-secret")]
    result = cli.invoke(app, ["list", "--secrets"])
    assert result.exit_code == 0
    assert "visible-secret" in result.stdout
```

- [ ] **Step 5: Implement list command and redacted error handling**

Render list records deterministically in server order. Centralize expected `ConfigError`, `TransportError`, and `ProtocolError` rendering. Normal output shows concise corrective text; `--debug` adds exception class and safe transport metadata, never request JSON, response JSON containing credentials, or passwords.

- [ ] **Step 6: Run Task 5 tests and commit**

Run: `python -m pytest tests/client/test_lookup_cli.py tests/client/test_list_cli.py -q`

Expected: all CLI tests pass, clipboard assertions pass, and captured output has no warnings.

Commit: `git add src/passwatcher tests/client && git commit -m "feat: add midnight neon lookup cli"`

---

### Task 6: Interactive CRUD and Password Generation

**Files:**
- Create: `src/passwatcher/prompts.py`
- Create: `src/passwatcher/passwords.py`
- Modify: `src/passwatcher/cli.py`
- Create: `tests/client/test_passwords.py`
- Create: `tests/client/test_crud_cli.py`

**Interfaces:**
- Produces: `generate_password(length: int = 24, lower=True, upper=True, digits=True, symbols=True) -> str`
- Produces commands: `add`, `edit QUERY`, `delete QUERY`, `generate`
- Produces: numbered selection helper returning record or cancellation
- Consumes: Task 4 service methods and Task 5 renderer/clipboard

- [ ] **Step 1: Write failing password generator tests**

```python
def test_generated_password_has_requested_categories():
    value = generate_password(24)
    assert len(value) == 24
    assert any(c.islower() for c in value)
    assert any(c.isupper() for c in value)
    assert any(c.isdigit() for c in value)
    assert any(c in "!@#$%^&*()-_=+[]{}" for c in value)

def test_generator_rejects_impossible_policy():
    with pytest.raises(PasswordPolicyError):
        generate_password(3, lower=True, upper=True, digits=True, symbols=True)
```

- [ ] **Step 2: Run generator tests and verify RED**

Run: `python -m pytest tests/client/test_passwords.py -q`

Expected: tests fail because the generator is missing.

- [ ] **Step 3: Implement unbiased cryptographic generation**

Use `secrets.choice`, guarantee one character from each enabled category, fill remaining positions from the union, and shuffle with `secrets.SystemRandom().shuffle`. Require length `8..256`, at least one category, and length at least the enabled-category count.

- [ ] **Step 4: Write failing interactive add/edit/delete tests**

```python
def test_add_prompts_and_creates(cli, service):
    result = cli.invoke(app, ["add"], input="github.com\npersonal\nnika@example.com\ntyped-secret\nmain account\ny\n")
    assert result.exit_code == 0
    assert service.created.password == "typed-secret"

def test_edit_many_requires_numbered_selection(cli, service):
    service.matches = [record(id=3, label="personal"), record(id=9, label="work")]
    result = cli.invoke(app, ["edit", "github"], input="2\n\n\n\nnew-secret\n\ny\n")
    assert result.exit_code == 0
    assert service.updated_id == 9

def test_delete_cancellation_performs_no_write(cli, service):
    service.matches = [record(id=3)]
    result = cli.invoke(app, ["delete", "github"], input="n\n")
    assert result.exit_code == 0
    assert service.deleted_ids == []
```

- [ ] **Step 5: Run CRUD tests and verify RED**

Run: `python -m pytest tests/client/test_crud_cli.py -q`

Expected: tests fail because the commands are missing.

- [ ] **Step 6: Implement interactive prompts and commands**

`add` prompts service, optional label, username, password choice (`type` or `generate`), optional notes, and final confirmation. Flags mirror all fields. `edit`/`delete` search first and use one-based validated selection for ambiguity. Edit offers existing non-secret values as defaults; entering an empty password keeps the current one, while choosing generation replaces it. Delete displays the selected service/label/username and requires a default-no confirmation. Ctrl+C and EOF render `Cancelled` and perform no service write.

`generate` accepts `--length` and category-disable flags, displays the result, and copies it exactly once.

- [ ] **Step 7: Run Task 6 tests and commit**

Run: `python -m pytest tests/client/test_passwords.py tests/client/test_crud_cli.py -q`

Expected: all tests pass with pristine output.

Commit: `git add src/passwatcher tests/client && git commit -m "feat: add interactive credential workflows"`

---

### Task 7: Idempotent Guided Setup and Doctor

**Files:**
- Create: `src/passwatcher/setup.py`
- Modify: `src/passwatcher/prompts.py`
- Modify: `src/passwatcher/cli.py`
- Create: `tests/setup/test_setup.py`
- Create: `tests/setup/test_doctor.py`
- Create: `scripts/linux-smoke-test.sh`

**Interfaces:**
- Produces: `SetupManager(runner, bundle_path).inspect(config) -> RemoteState`
- Produces: `SetupManager.install_or_upgrade(config, state) -> SetupResult`
- Produces commands: `setup`, `doctor`
- Consumes: Task 1 configuration, Task 3 server `.pyz`, and Task 4 SSH conventions

- [ ] **Step 1: Write failing idempotency and preservation tests**

```python
def test_setup_reuses_compatible_installation(manager, remote):
    remote.state = RemoteState(installed=True, protocol_version=1, schema_version=1, database_exists=True)
    result = manager.install_or_upgrade(config(), remote.state)
    assert result.action == "reused"
    assert remote.uploads == []
    assert remote.database_mutations == []

def test_upgrade_backs_up_before_replacing_server(manager, remote):
    remote.state = RemoteState(installed=True, protocol_version=0, schema_version=1, database_exists=True)
    manager.install_or_upgrade(config(), remote.state)
    assert remote.events.index("backup") < remote.events.index("install_bundle")

def test_fresh_setup_rerun_does_not_create_second_database(manager, remote):
    manager.install_or_upgrade(config(), RemoteState(False, None, None, False))
    manager.install_or_upgrade(config(), manager.inspect(config()))
    assert remote.created_databases == 1
```

- [ ] **Step 2: Run setup tests and verify RED**

Run: `python -m pytest tests/setup/test_setup.py -q`

Expected: tests fail because setup is missing.

- [ ] **Step 3: Implement safe remote inspection and install workflow**

Use subprocess argument lists with `shell=False`. Inspect Python version and constant remote paths. Upload the embedded zipapp to `~/.local/share/passwatcher/install/passwatcher-server.pyz.new` using `scp` arguments, verify a bundled SHA-256 digest remotely, then atomically rename it to `~/.local/bin/passwatcher-server` and chmod `0700`. Create data/backup directories at `0700`; initialize only when the database does not exist. Never run `rm` against the database path. For upgrade, invoke a fixed remote backup command through the existing server before bundle replacement.

- [ ] **Step 4: Add failing guided setup and doctor tests**

```python
def test_setup_saves_config_only_after_remote_success(cli, setup_manager, config_path):
    setup_manager.failure = SetupError("connect_failed", "SSH connection failed")
    result = cli.invoke(app, ["setup"], input="vault.example\nnika\n22\nC:/keys/vault\n")
    assert result.exit_code == 1
    assert not config_path.exists()

def test_doctor_reports_all_checks_without_secrets(cli, doctor):
    doctor.checks = [("OpenSSH", True, "available"), ("SQLite integrity", True, "ok")]
    result = cli.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "OpenSSH" in result.stdout and "SQLite integrity" in result.stdout
    assert "password" not in result.stdout.lower()
```

- [ ] **Step 5: Implement guided setup and non-mutating doctor**

Prompt host, user, port default `22`, and optional identity file; confirm the resolved target; show spinners for connectivity, inspection, install/upgrade/reuse, health, and save. Save local config atomically only after remote health succeeds. `doctor` checks config, local `ssh`/`scp`, connectivity, fixed-command protocol, server/schema version, mode bits, SQLite integrity, and read access without changing remote state.

- [ ] **Step 6: Add Linux temporary-home smoke script**

The script creates a temporary home, sets `PASSWATCHER_DATA_DIR`, runs the built `.pyz` health RPC twice, creates one credential between runs, asserts the second run reports one record, and on POSIX asserts the database mode is `600`. It cleans only its exact `mktemp -d` directory via a quoted trap.

- [ ] **Step 7: Run Task 7 tests and commit**

Run: `python -m pytest tests/setup -q`

Run on Linux or WSL: `bash scripts/linux-smoke-test.sh`

Expected: setup/doctor tests pass; smoke test reports reuse of one database and correct permissions.

Commit: `git add src/passwatcher tests/setup scripts && git commit -m "feat: add guided server setup and doctor"`

---

### Task 8: Standalone Windows Executable and NSIS Installer

**Files:**
- Create: `packaging/passwatcher.spec`
- Create: `packaging/passwatcher.nsi`
- Create: `tools/build_windows.ps1`
- Create: `tests/setup/test_packaging_files.py`
- Create: `tests/setup/windows-installer-smoke.ps1`
- Create: `README.md`

**Interfaces:**
- Produces: `dist/passwatcher/pw.exe`
- Produces: `dist/Passwatcher-Setup-<version>.exe`
- Consumes: complete client package and bundled server `.pyz`

- [ ] **Step 1: Write failing packaging contract tests**

```python
def test_nsis_is_per_user_and_updates_user_path():
    text = Path("packaging/passwatcher.nsi").read_text(encoding="utf-8")
    assert "RequestExecutionLevel user" in text
    assert "$LOCALAPPDATA\\Programs\\Passwatcher" in text
    assert "HKCU" in text and "Environment" in text and "Path" in text
    assert "WM_SETTINGCHANGE" in text

def test_installer_preserves_config_by_default():
    text = Path("packaging/passwatcher.nsi").read_text(encoding="utf-8")
    assert "$APPDATA\\Passwatcher\\config.toml" in text
    assert "MessageBox MB_YESNO" in text
```

- [ ] **Step 2: Run packaging tests and verify RED**

Run: `python -m pytest tests/setup/test_packaging_files.py -q`

Expected: tests fail because packaging files are missing.

- [ ] **Step 3: Implement PyInstaller build**

The spec names the executable `pw`, uses console mode, includes `src/passwatcher/assets/passwatcher-server.pyz`, collects Typer/Rich metadata, and sets a deterministic version from `passwatcher.__version__`. `tools/build_windows.ps1` runs the server zipapp builder, the full test suite, PyInstaller with `--clean --noconfirm`, then `makensis` with the version define; it stops on the first nonzero exit.

- [ ] **Step 4: Implement per-user NSIS installation and uninstall**

Use `RequestExecutionLevel user` and install to `$LOCALAPPDATA\Programs\Passwatcher`. Copy `pw.exe` and runtime files, write uninstall metadata under `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Passwatcher`, append the install directory to `HKCU\Environment\Path` only when absent, and broadcast `WM_SETTINGCHANGE`. Upgrades replace application files but never touch `$APPDATA\Passwatcher\config.toml`. Uninstall removes the exact PATH entry and application directory, then asks `Remove local Passwatcher connection settings too?` before deleting the exact config directory.

- [ ] **Step 5: Add Windows installer smoke test**

The PowerShell smoke test installs silently into an isolated test user context or CI sandbox, starts a new process that runs `pw --help`, verifies the executable resolves through the user PATH, creates a sentinel config, upgrades, verifies the sentinel remains, uninstalls silently with config retention, and verifies the application files and PATH entry are removed. It must never target an existing real `%APPDATA%\Passwatcher` directory.

- [ ] **Step 6: Write user documentation**

Document NSIS installation, opening a new terminal after install, `pw setup`, all commands with concrete examples, exact zero/one/many lookup behavior, multi-device reuse, Linux paths/permissions, backup behavior, upgrade/uninstall behavior, troubleshooting through `pw doctor`, and the explicit plaintext-on-owned-server security model.

- [ ] **Step 7: Run full verification and commit**

Run: `python -m pytest -q`

Run on Windows with NSIS installed: `powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1`

Run in an isolated Windows test context: `powershell -ExecutionPolicy Bypass -File tests/setup/windows-installer-smoke.ps1 -Installer dist/Passwatcher-Setup-*.exe`

Expected: full tests pass without warnings; `pw.exe` runs; installer smoke passes; both deliverables exist.

Commit: `git add packaging tools tests/setup README.md src/passwatcher/assets && git commit -m "build: add windows installer and release workflow"`

---

## Final Verification

- [ ] Run `python -m pytest -q` and require zero failures and zero warnings.
- [ ] Run `python tools/build_server_zipapp.py` twice and compare SHA-256 hashes for reproducibility.
- [ ] Run `bash scripts/linux-smoke-test.sh` on Linux or WSL.
- [ ] Run `powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1` on Windows with NSIS.
- [ ] Run the isolated NSIS smoke test and verify per-user PATH install, upgrade preservation, and uninstall cleanup.
- [ ] Manually exercise `pw setup`, `pw add`, zero/one/many `pw github`, `pw edit`, `pw delete`, `pw list`, `pw list --secrets`, `pw generate`, and `pw doctor` against a disposable Linux test account.
