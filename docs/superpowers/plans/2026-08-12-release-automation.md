# Passwatcher Installer Guide and GitHub Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Windows installer creation reproducible for maintainers and publish a verified installer plus SHA-256 checksum from matching Git tags.

**Architecture:** A standalone version verifier enforces agreement between the release tag and both source version declarations. A Windows-only GitHub Actions workflow builds through the existing guarded PowerShell script, exercises both installer smoke suites on a disposable hosted runner, uploads an intermediate artifact, and publishes it with GitHub CLI only after all gates pass. A dedicated guide documents the identical local process.

**Tech Stack:** PowerShell 7/Windows PowerShell, Python 3.11, pytest, PyInstaller 6, NSIS 3.x, GitHub Actions official actions, GitHub CLI.

## Global Constraints

- Support tag pushes matching `v*` and manual dispatch with one required existing tag.
- Require exact `v<project-version>` agreement with `pyproject.toml` and `passwatcher.__version__`.
- Build on `windows-2022`, not the moving `windows-latest` label.
- Use only official GitHub actions and the GitHub CLI available on the hosted image.
- Grant only `contents: write` to the publishing workflow.
- Do not create or push tags from the workflow.
- Do not overwrite an existing GitHub Release.
- Publish only `Passwatcher-Setup-<version>.exe` and its SHA-256 checksum file.
- Run the existing full build and both guarded installer smoke tests before publishing.
- Keep all installer smoke mutations inside the disposable GitHub-hosted Windows user.

---

### Task 1: Release Version Contract

**Files:**
- Create: `tools/verify_release_version.py`
- Create: `tests/setup/test_release_version.py`

**Interfaces:**
- Produces: `release_version(tag: str, pyproject_path: Path, source_root: Path) -> str`
- Produces CLI: `python tools/verify_release_version.py v0.1.0`
- Raises: `ReleaseVersionError` with non-secret mismatch text and exit status 1 from the CLI.

- [ ] **Step 1: Write failing version-verifier tests**

Create `tests/setup/test_release_version.py`:

```python
from pathlib import Path

import pytest

from tools.verify_release_version import ReleaseVersionError, release_version


def project(tmp_path: Path, metadata: str, source_version: str) -> tuple[Path, Path]:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        f'[project]\nname="passwatcher"\nversion="{metadata}"\n', encoding="utf-8"
    )
    package = tmp_path / "src" / "passwatcher"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        f'__version__ = "{source_version}"\n', encoding="utf-8"
    )
    return pyproject, tmp_path / "src"


def test_release_version_accepts_exact_v_tag(tmp_path: Path) -> None:
    pyproject, source = project(tmp_path, "1.2.3", "1.2.3")
    assert release_version("v1.2.3", pyproject, source) == "1.2.3"


@pytest.mark.parametrize(
    ("tag", "metadata", "source"),
    [("1.2.3", "1.2.3", "1.2.3"), ("v1.2.4", "1.2.3", "1.2.3"), ("v1.2.3", "1.2.3", "1.2.4")],
)
def test_release_version_rejects_invalid_or_mismatched_versions(
    tmp_path: Path, tag: str, metadata: str, source: str
) -> None:
    pyproject, source_root = project(tmp_path, metadata, source)
    with pytest.raises(ReleaseVersionError):
        release_version(tag, pyproject, source_root)
```

- [ ] **Step 2: Run the verifier tests and verify RED**

Run: `python -m pytest tests/setup/test_release_version.py -q`

Expected: collection fails because `tools.verify_release_version` does not exist.

- [ ] **Step 3: Implement strict version verification**

Create `tools/verify_release_version.py` with no third-party dependency:

```python
class ReleaseVersionError(ValueError):
    pass


def release_version(tag: str, pyproject_path: Path, source_root: Path) -> str:
    if re.fullmatch(r"v\d+(?:\.\d+){1,3}", tag) is None:
        raise ReleaseVersionError("release tag must be v followed by two to four numeric components")
    metadata = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"]["version"]
    namespace = runpy.run_path(str(source_root / "passwatcher" / "__init__.py"))
    source = namespace["__version__"]
    version = tag.removeprefix("v")
    if metadata != version or source != version:
        raise ReleaseVersionError("release tag, project metadata, and source version must match")
    return version
```

The CLI resolves repository-relative paths from `__file__`, prints only the verified version, and catches `ReleaseVersionError`, `OSError`, `KeyError`, `TypeError`, and TOML parse failures without a traceback.

- [ ] **Step 4: Run verifier tests and real-project check**

Run: `python -m pytest tests/setup/test_release_version.py -q`

Run: `python tools/verify_release_version.py v0.1.0`

Expected: tests pass and the real command prints `0.1.0`.

- [ ] **Step 5: Commit the version boundary**

```powershell
git add -- tools/verify_release_version.py tests/setup/test_release_version.py
git commit -m "build: validate release tag versions"
```

---

### Task 2: Step-by-Step Installer Guide

**Files:**
- Create: `docs/BUILDING_INSTALLER.md`
- Modify: `README.md`
- Create: `tests/setup/test_installer_guide.py`

**Interfaces:**
- Produces: a clean-machine PowerShell guide whose commands match `tools/build_windows.ps1` and the guarded smoke scripts.
- Consumes: the version verifier from Task 1.

- [ ] **Step 1: Write failing documentation-contract tests**

Create `tests/setup/test_installer_guide.py`:

```python
from pathlib import Path


def test_installer_guide_contains_reproducible_build_sequence() -> None:
    text = Path("docs/BUILDING_INSTALLER.md").read_text(encoding="utf-8")
    commands = [
        "py -3.11 -m venv .venv",
        ".\\.venv\\Scripts\\Activate.ps1",
        'python -m pip install -e ".[dev]"',
        "python tools/verify_release_version.py",
        "tools/build_windows.ps1",
        "Get-FileHash -Algorithm SHA256",
    ]
    positions = [text.index(command) for command in commands]
    assert positions == sorted(positions)


def test_installer_guide_marks_smoke_tests_as_disposable_only() -> None:
    text = Path("docs/BUILDING_INSTALLER.md").read_text(encoding="utf-8")
    assert "PASSWATCHER_SMOKE_ISOLATED_USER" in text
    assert "PASSWATCHER_SMOKE_DESTRUCTIVE_CANARIES" in text
    assert "disposable Windows user" in text
    assert "windows-installer-smoke.ps1" in text
    assert "windows-installer-safety-smoke.ps1" in text


def test_readme_links_to_full_installer_guide() -> None:
    assert "docs/BUILDING_INSTALLER.md" in Path("README.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run guide tests and verify RED**

Run: `python -m pytest tests/setup/test_installer_guide.py -q`

Expected: failure because the guide and README link do not exist.

- [ ] **Step 3: Write the exact installer guide**

Create `docs/BUILDING_INSTALLER.md` with these concrete sections and commands:

```powershell
git clone <repository-url>
Set-Location PassWatcher
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python --version
makensis /VERSION
python tools/verify_release_version.py v0.1.0
powershell -ExecutionPolicy Bypass -File tools/build_windows.ps1
Get-ChildItem dist
Get-FileHash -Algorithm SHA256 dist\Passwatcher-Setup-0.1.0.exe
```

Explain installing NSIS 3.x from its official installer and adding its installation directory to `PATH`. Document the two output locations, the disposable-user guards and smoke commands, installation in a fresh terminal, upgrade testing, uninstall testing, and exact fixes for missing `makensis`, execution-policy restrictions, locked `dist` files, version mismatches, stale PATH state, and smoke-test refusal.

Replace the long README build section with a concise build command, output summary, safety warning, and relative link to the full guide. Do not remove the security model or user installation instructions.

- [ ] **Step 4: Run guide and existing packaging tests**

Run: `python -m pytest tests/setup/test_installer_guide.py tests/setup/test_packaging_files.py -q`

Expected: all tests pass; an absent local NSIS executable may retain the existing intentional skip.

- [ ] **Step 5: Commit the guide**

```powershell
git add -- docs/BUILDING_INSTALLER.md README.md tests/setup/test_installer_guide.py
git commit -m "docs: add Windows installer build guide"
```

---

### Task 3: Verified GitHub Release Workflow

**Files:**
- Create: `.github/workflows/release.yml`
- Create: `tests/setup/test_release_workflow.py`

**Interfaces:**
- Consumes: `tools/verify_release_version.py TAG` and `tools/build_windows.ps1`.
- Produces: workflow artifact `passwatcher-installer-<version>`.
- Produces release assets: `Passwatcher-Setup-<version>.exe` and `Passwatcher-Setup-<version>.exe.sha256`.

- [ ] **Step 1: Write failing workflow-contract tests**

Create `tests/setup/test_release_workflow.py`:

```python
from pathlib import Path


def workflow() -> str:
    return Path(".github/workflows/release.yml").read_text(encoding="utf-8")


def test_release_workflow_has_safe_triggers_and_permissions() -> None:
    text = workflow()
    assert "workflow_dispatch:" in text
    assert "tags:" in text and "- 'v*'" in text
    assert "contents: write" in text
    assert "windows-2022" in text
    assert "windows-latest" not in text


def test_release_workflow_verifies_builds_and_smoke_tests_before_publish() -> None:
    text = workflow()
    gates = [
        "tools/verify_release_version.py",
        "tools/build_windows.ps1",
        "windows-installer-smoke.ps1",
        "windows-installer-safety-smoke.ps1",
        "Get-FileHash -Algorithm SHA256",
        "actions/upload-artifact@v4",
        "actions/download-artifact@v5",
        "gh release create",
    ]
    positions = [text.index(gate) for gate in gates]
    assert positions == sorted(positions)
    assert "PASSWATCHER_SMOKE_ISOLATED_USER: '1'" in text
    assert "PASSWATCHER_SMOKE_DESTRUCTIVE_CANARIES: '1'" in text


def test_release_workflow_checks_out_existing_tag_and_rejects_existing_release() -> None:
    text = workflow()
    assert "inputs.tag || github.ref" in text
    assert "gh release view" in text
    assert "git tag" not in text
    assert "git push" not in text
```

- [ ] **Step 2: Run workflow tests and verify RED**

Run: `python -m pytest tests/setup/test_release_workflow.py -q`

Expected: failure because `.github/workflows/release.yml` does not exist.

- [ ] **Step 3: Implement build and publish jobs**

Create `.github/workflows/release.yml` with this structure:

```yaml
name: Release Windows installer

on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:
    inputs:
      tag:
        description: Existing v<version> tag to publish
        required: true
        type: string

permissions:
  contents: write

jobs:
  build:
    runs-on: windows-2022
    outputs:
      version: ${{ steps.version.outputs.version }}
      tag: ${{ steps.version.outputs.tag }}
    steps:
      - uses: actions/checkout@v6
        with:
          ref: ${{ inputs.tag || github.ref }}
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip
      - name: Install build tools
        shell: pwsh
        run: |
          python -m pip install --upgrade pip
          python -m pip install -e ".[dev]"
          choco install nsis --no-progress -y
      - name: Verify release version
        id: version
        shell: pwsh
        run: |
          $tag = if ('${{ github.event_name }}' -eq 'workflow_dispatch') { '${{ inputs.tag }}' } else { '${{ github.ref_name }}' }
          $version = python tools/verify_release_version.py $tag
          if ($LASTEXITCODE -ne 0) { throw 'Release version verification failed.' }
          "tag=$tag" >> $env:GITHUB_OUTPUT
          "version=$version" >> $env:GITHUB_OUTPUT
      - name: Build installer
        shell: pwsh
        run: ./tools/build_windows.ps1
      - name: Smoke installer
        shell: pwsh
        env:
          PASSWATCHER_SMOKE_ISOLATED_USER: '1'
          PASSWATCHER_SMOKE_DESTRUCTIVE_CANARIES: '1'
        run: |
          ./tests/setup/windows-installer-smoke.ps1 -Installer "dist/Passwatcher-Setup-*.exe"
          ./tests/setup/windows-installer-safety-smoke.ps1 -Installer "dist/Passwatcher-Setup-*.exe"
```

Finish the build job by resolving exactly one installer, hashing it with `Get-FileHash -Algorithm SHA256`, writing `<installer>.sha256`, and uploading exactly those two files using `actions/upload-artifact@v4` with `if-no-files-found: error` and a seven-day retention.

The `publish` job uses `needs: build`, runs on `windows-2022`, downloads through `actions/download-artifact@v5`, sets `GH_TOKEN: ${{ github.token }}`, fails if `gh release view <tag>` succeeds, and otherwise runs:

```powershell
gh release create $tag $installer $checksum --verify-tag --generate-notes --title "Passwatcher $version"
```

Check that downloaded artifact resolution yields exactly one `.exe` and one `.sha256` before invoking `gh`.

- [ ] **Step 4: Run workflow contract and packaging tests**

Run: `python -m pytest tests/setup/test_release_workflow.py tests/setup/test_release_version.py tests/setup/test_packaging_files.py -q`

Expected: all tests pass with only the documented local-NSIS skip when applicable.

- [ ] **Step 5: Inspect workflow diff for secret and permission scope**

Run: `git diff --check`

Run: `git diff -- .github/workflows/release.yml tools/verify_release_version.py`

Confirm the diff contains no personal access token, no `pull-requests: write`, no tag creation, no force push, and no artifact wildcard broader than the installer plus checksum.

- [ ] **Step 6: Commit release automation**

```powershell
git add -- .github/workflows/release.yml tests/setup/test_release_workflow.py
git commit -m "ci: publish verified Windows releases"
```

---

### Task 4: Release Workstream Verification

**Files:**
- Modify only if verification finds a defect: files named in Tasks 1–3 and their tests.

**Interfaces:**
- Produces a clean, locally validated release workstream ready for the local-vault feature version bump.

- [ ] **Step 1: Run all release and packaging tests**

Run: `python -m pytest tests/setup/test_release_version.py tests/setup/test_installer_guide.py tests/setup/test_release_workflow.py tests/setup/test_packaging_files.py -q`

Expected: all executable tests pass; only environment-dependent compiler tests may skip when NSIS is absent.

- [ ] **Step 2: Run the complete suite**

Run: `python -m pytest -q`

Expected: zero failures and only declared platform/tool skips.

- [ ] **Step 3: Verify repository state and history**

Run: `git diff --check`

Run: `git status --short`

Run: `git log -4 --oneline`

Expected: a clean worktree and three focused implementation commits after the plan commit.

