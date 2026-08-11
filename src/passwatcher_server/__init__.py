"""The server-side SQLite vault for Passwatcher."""

from .database import DatabaseError, NotFoundError, ValidationError, Vault
from .models import Credential

__all__ = ["Credential", "DatabaseError", "NotFoundError", "ValidationError", "Vault"]
