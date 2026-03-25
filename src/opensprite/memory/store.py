"""Compatibility shim for memory document stores."""

from ..documents.memory import FileMemoryStorage, MemoryDocumentStore, MemoryStore

__all__ = ["FileMemoryStorage", "MemoryDocumentStore", "MemoryStore"]
