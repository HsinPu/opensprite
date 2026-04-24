"""Shared markdown-backed document stores and consolidators."""

from .base import ConversationConsolidator, ConversationDocumentStore, IncrementalStateStore
from .active_task import (
    ACTIVE_TASK_HEADER,
    ACTIVE_TASK_START_MARKER,
    ACTIVE_TASK_END_MARKER,
    DEFAULT_ACTIVE_TASK_CONTENT,
    ActiveTaskConsolidator,
    ActiveTaskStore,
    build_initial_active_task_block,
    is_task_worthy_message,
    should_replace_active_task,
    consolidate_active_task,
)
from .managed import ManagedMarkdownDocument
from .memory import MemoryDocumentStore, MemoryStore, FileMemoryStorage, consolidate
from .recent_summary import RecentSummaryConsolidator, RecentSummaryStore, consolidate_recent_summary
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
    "ACTIVE_TASK_END_MARKER",
    "ACTIVE_TASK_HEADER",
    "ACTIVE_TASK_START_MARKER",
    "ActiveTaskConsolidator",
    "ActiveTaskStore",
    "build_initial_active_task_block",
    "is_task_worthy_message",
    "should_replace_active_task",
    "ConversationConsolidator",
    "ConversationDocumentStore",
    "DEFAULT_ACTIVE_TASK_CONTENT",
    "DEFAULT_MANAGED_CONTENT",
    "END_MARKER",
    "FileMemoryStorage",
    "IncrementalStateStore",
    "JsonProgressStore",
    "ManagedMarkdownDocument",
    "MemoryDocumentStore",
    "MemoryStore",
    "RecentSummaryConsolidator",
    "RecentSummaryStore",
    "START_MARKER",
    "UserProfileConsolidator",
    "UserProfileStore",
    "consolidate",
    "consolidate_active_task",
    "consolidate_recent_summary",
    "consolidate_user_profile",
]
