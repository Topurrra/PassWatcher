"""Command-line entry point for the standalone Passwatcher server."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from .database import DatabaseError, Vault
from .rpc import MAX_REQUEST_BYTES, handle_request


def main(argv: list[str] | None = None) -> int:
    """Run the one-request RPC process."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments not in (["rpc"], ["backup"]):
        print("Usage: passwatcher-server.pyz {rpc|backup}", file=sys.stderr)
        return 2

    data_dir = Path(os.environ.get("PASSWATCHER_DATA_DIR", "~/.local/share/passwatcher")).expanduser()
    vault = Vault(data_dir / "passwatcher.db", data_dir / "backups")
    if arguments == ["backup"]:
        try:
            vault.backup()
        except DatabaseError:
            return 1
        return 0
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    sys.stdout.buffer.write(handle_request(raw, vault) + b"\n")
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
