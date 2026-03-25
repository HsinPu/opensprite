"""Compatibility shim for USER.md profile storage."""

from ..documents.user_profile import (
    AUTO_PROFILE_HEADER,
    DEFAULT_MANAGED_CONTENT,
    END_MARKER,
    START_MARKER,
    UserProfileStore,
)

__all__ = [
    "AUTO_PROFILE_HEADER",
    "DEFAULT_MANAGED_CONTENT",
    "END_MARKER",
    "START_MARKER",
    "UserProfileStore",
]
