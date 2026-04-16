"""Search index providers."""

from .base import SearchHit, SearchStore

__all__ = ["SearchHit", "SearchStore", "SQLiteSearchStore"]


def __getattr__(name: str):
    """Lazily import optional search backends to avoid import cycles."""
    if name == "SQLiteSearchStore":
        from .sqlite_store import SQLiteSearchStore

        return SQLiteSearchStore
    raise AttributeError(f"module 'opensprite.search' has no attribute {name!r}")
