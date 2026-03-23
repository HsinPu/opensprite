"""Search index providers."""

from .base import SearchHit, SearchStore

__all__ = ["SearchHit", "SearchStore", "LanceDBSearchStore"]


def __getattr__(name: str):
    """Lazily import optional search backends."""
    if name == "LanceDBSearchStore":
        from .lancedb_store import LanceDBSearchStore

        return LanceDBSearchStore
    raise AttributeError(f"module 'opensprite.search' has no attribute {name!r}")
