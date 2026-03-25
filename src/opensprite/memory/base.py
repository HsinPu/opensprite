"""Compatibility shim for memory document abstractions."""

from ..documents.base import ConversationDocumentStore as MemoryStorage

__all__ = ["MemoryStorage"]
