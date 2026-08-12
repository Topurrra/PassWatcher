from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from tools.verify_release_version import ReleaseVersionError, release_version


def _project(tmp_path: Path, metadata: str, source_version: str) -> tuple[Path, Path]:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        f'[project]\nname = "passwatcher"\nversion = "{metadata}"\n',
        encoding="utf-8",
    )
    package = tmp_path / "src" / "passwatcher"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        f'__version__ = "{source_version}"\n', encoding="utf-8"
    )
    return pyproject, tmp_path / "src"


def test_release_version_accepts_exact_v_tag(tmp_path: Path) -> None:
    """Catches a matching release tag being rejected by the build gate."""
    pyproject, source = _project(tmp_path, "1.2.3", "1.2.3")

    assert release_version("v1.2.3", pyproject, source) == "1.2.3"


@pytest.mark.parametrize(
    ("tag", "metadata", "source"),
    [
        ("1.2.3", "1.2.3", "1.2.3"),
        ("v1.2.4", "1.2.3", "1.2.3"),
        ("v1.2.3", "1.2.3", "1.2.4"),
    ],
)
def test_release_version_rejects_invalid_or_mismatched_versions(
    tmp_path: Path, tag: str, metadata: str, source: str
) -> None:
    """Catches releases built from an unversioned or inconsistently versioned tree."""
    pyproject, source_root = _project(tmp_path, metadata, source)

    with pytest.raises(ReleaseVersionError):
        release_version(tag, pyproject, source_root)


def test_release_version_cli_prints_only_verified_version() -> None:
    """Catches the workflow-facing command emitting unusable output."""
    completed = subprocess.run(
        [sys.executable, "tools/verify_release_version.py", "v0.1.0"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == "0.1.0\n"
    assert completed.stderr == ""


def test_release_version_cli_reports_mismatch_without_traceback() -> None:
    """Catches a bad tag exposing an implementation traceback in CI logs."""
    completed = subprocess.run(
        [sys.executable, "tools/verify_release_version.py", "v9.9.9"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "must match" in completed.stderr
    assert "Traceback" not in completed.stderr
