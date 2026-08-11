"""The narrow clipboard boundary used by the command-line client."""

from __future__ import annotations

import pyperclip


class Clipboard:
    """Copy text to the local operating system clipboard."""

    def copy(self, text: str) -> None:
        pyperclip.copy(text)
