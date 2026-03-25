"""Shared markdown-backed document stores and consolidators."""

from .base import ConversationConsolidator, ConversationDocumentStore, IncrementalStateStore
from .managed import ManagedMarkdownDocument
from .memory import MemoryDocumentStore, MemoryStore, FileMemoryStorage, consolidate
from .state import JsonProgressStore
from .user_profile import (
    AUTO_PROFILE_HEADER,
    DEFAULT_MANAGED_CONTENT,
    END_MARKER,
    START_MARKER,
    UserProfileConsolidator,
    UserProfileStore,
    consolidate_user_profile,
)

__all__ = [
    "AUTO_PROFILE_HEADER",
    "ConversationConsolidator",
    "ConversationDocumentStore",
    "DEFAULT_MANAGED_CONTENT",
    "END_MARKER",
    "FileMemoryStorage",
    "IncrementalStateStore",
    "JsonProgressStore",
    "ManagedMarkdownDocument",
    "MemoryDocumentStore",
    "MemoryStore",
    "START_MARKER",
    "UserProfileConsolidator",
    "UserProfileStore",
    "consolidate",
    "consolidate_user_profile",
]
