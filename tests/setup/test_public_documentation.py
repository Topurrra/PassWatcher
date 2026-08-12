"""Repository-level checks for the public documentation boundary."""

from pathlib import Path
import subprocess


def test_readme_is_the_only_tracked_public_document() -> None:
    """Catches internal documents being published from the Git index."""
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8").split("\0")
    document_suffixes = {".md", ".rst", ".adoc", ".txt"}
    public_documents = sorted(
        path for path in tracked if Path(path).suffix.lower() in document_suffixes
    )

    assert public_documents == ["README.md"]

    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "docs/private-documentation-probe.md"],
        check=False,
    )
    assert ignored.returncode == 0
