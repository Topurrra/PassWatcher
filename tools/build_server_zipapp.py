"""Build the standalone, standard-library-only Passwatcher server zipapp."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import secrets
import shutil
import zipapp


PROJECT_ROOT = Path(__file__).parents[1]
SOURCE_PACKAGE = PROJECT_ROOT / "src" / "passwatcher_server"
OUTPUT_PATH = PROJECT_ROOT / "src" / "passwatcher" / "assets" / "passwatcher-server.pyz"
ZIP_TIMESTAMP = int(datetime(1980, 1, 1).timestamp())


def main() -> int:
    """Stage source files with stable timestamps and create the zipapp."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = _create_staging_dir()
    try:
        _copy_package(staging_dir)
        entry_point = staging_dir / "__main__.py"
        entry_point.write_text(
            "from passwatcher_server.__main__ import main\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n",
            encoding="utf-8",
            newline="\n",
        )
        _normalize_timestamp(entry_point)
        zipapp.create_archive(
            staging_dir,
            target=OUTPUT_PATH,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )
    finally:
        shutil.rmtree(staging_dir)
    return 0


def _create_staging_dir() -> Path:
    """Create a private, collision-resistant staging directory in the project."""
    while True:
        staging_dir = PROJECT_ROOT / f".passwatcher-zipapp-{secrets.token_hex(8)}"
        try:
            staging_dir.mkdir()
        except FileExistsError:
            continue
        return staging_dir


def _copy_package(staging_dir: Path) -> None:
    """Copy server source files in a fixed order into the build stage."""
    source_paths = sorted(path for path in SOURCE_PACKAGE.rglob("*.py") if path.is_file())
    for source_path in source_paths:
        relative_path = source_path.relative_to(SOURCE_PACKAGE.parent)
        target_path = staging_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _normalize_timestamp(target_path.parent)
        target_path.write_text(
            source_path.read_text(encoding="utf-8"),
            encoding="utf-8",
            newline="\n",
        )
        _normalize_timestamp(target_path)
    _normalize_timestamp(staging_dir / SOURCE_PACKAGE.name)


def _normalize_timestamp(path: Path) -> None:
    os.utime(path, (ZIP_TIMESTAMP, ZIP_TIMESTAMP))


if __name__ == "__main__":
    raise SystemExit(main())
