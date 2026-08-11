"""Command-line entry point for the standalone Passwatcher server."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from .database import Vault
from .rpc import MAX_REQUEST_BYTES, handle_request


def main(argv: list[str] | None = None) -> int:
    """Run the one-request RPC process."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments != ["rpc"]:
        print("Usage: passwatcher-server.pyz rpc", file=sys.stderr)
        return 2

    data_dir = Path(os.environ.get("PASSWATCHER_DATA_DIR", "~/.local/share/passwatcher")).expanduser()
    vault = Vault(data_dir / "passwatcher.db", data_dir / "backups")
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    sys.stdout.buffer.write(handle_request(raw, vault) + b"\n")
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
