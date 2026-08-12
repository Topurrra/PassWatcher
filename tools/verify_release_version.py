"""Verify that a release tag matches every Passwatcher version declaration."""

from __future__ import annotations

from pathlib import Path
import re
import runpy
import sys
import tomllib


class ReleaseVersionError(ValueError):
    """Raised when a release version is malformed or inconsistent."""


def release_version(tag: str, pyproject_path: Path, source_root: Path) -> str:
    """Return the numeric release version after validating all declarations."""
    if re.fullmatch(r"v\d+(?:\.\d+){1,3}", tag) is None:
        raise ReleaseVersionError(
            "release tag must be v followed by two to four numeric components"
        )

    metadata = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project_version = metadata["project"]["version"]
    source = runpy.run_path(str(source_root / "passwatcher" / "__init__.py"))
    source_version = source["__version__"]
    version = tag.removeprefix("v")
    if project_version != version or source_version != version:
        raise ReleaseVersionError(
            "release tag, project metadata, and source version must match"
        )
    return version


def main(arguments: list[str] | None = None) -> int:
    """Run the workflow-facing verifier with concise failure output."""
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 1:
        print("usage: verify_release_version.py v<version>", file=sys.stderr)
        return 1

    project_root = Path(__file__).resolve().parent.parent
    try:
        version = release_version(
            arguments[0], project_root / "pyproject.toml", project_root / "src"
        )
    except (ReleaseVersionError, OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        print(f"Release version error: {error}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
