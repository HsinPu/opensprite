"""Stable cross-session user overlay store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..context.paths import (
    get_user_overlay_file,
    get_user_overlay_index_file,
    get_user_overlay_state_file,
)
from .base import ConversationDocumentStore
from .state import JsonProgressStore


USER_OVERLAY_TEMPLATE = """# Stable Preferences
- 

# Stable Facts
- 

# Response Language
- not set
"""


class UserOverlayStore(ConversationDocumentStore):
    """File-backed stable cross-session overlay for one user identity."""

    def __init__(self, *, app_home: str | Path | None = None):
        self.app_home = Path(app_home).expanduser() if app_home is not None else None

    def _overlay_file(self, overlay_id: str) -> Path:
        overlay_file = get_user_overlay_file(overlay_id, app_home=self.app_home)
        overlay_file.parent.mkdir(parents=True, exist_ok=True)
        return overlay_file

    def read(self, overlay_id: str) -> str:
        overlay_file = self._overlay_file(overlay_id)
        if overlay_file.exists():
            return overlay_file.read_text(encoding="utf-8")
        return ""

    def write(self, overlay_id: str, content: str) -> None:
        self._overlay_file(overlay_id).write_text(str(content or "").strip() + "\n", encoding="utf-8")

    def ensure_exists(self, overlay_id: str) -> str:
        current = self.read(overlay_id)
        if current:
            return current
        self.write(overlay_id, USER_OVERLAY_TEMPLATE)
        return self.read(overlay_id)

    def get_context(self, overlay_id: str) -> str:
        content = self.read(overlay_id)
        if not content:
            return ""
        return f"# Stable User Overlay\n\n{content}"


class UserOverlayIndexStore:
    """Structured sidecar for one overlay's stable preferences and facts."""

    def __init__(self, *, app_home: str | Path | None = None):
        self.app_home = Path(app_home).expanduser() if app_home is not None else None

    def _index_file(self, overlay_id: str) -> Path:
        index_file = get_user_overlay_index_file(overlay_id, app_home=self.app_home)
        index_file.parent.mkdir(parents=True, exist_ok=True)
        return index_file

    def read(self, overlay_id: str) -> dict[str, Any]:
        index_file = self._index_file(overlay_id)
        if not index_file.exists():
            return self.default_payload(overlay_id)
        try:
            payload = json.loads(index_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self.default_payload(overlay_id)
        if not isinstance(payload, dict):
            return self.default_payload(overlay_id)
        normalized = self.default_payload(overlay_id)
        normalized.update({
            "updated_at": str(payload.get("updated_at") or "").strip() or None,
            "response_language": payload.get("response_language") if isinstance(payload.get("response_language"), dict) else None,
            "preferences": [dict(item) for item in payload.get("preferences", []) if isinstance(item, dict)],
            "stable_facts": [dict(item) for item in payload.get("stable_facts", []) if isinstance(item, dict)],
        })
        return normalized

    def write(self, overlay_id: str, payload: dict[str, Any]) -> None:
        normalized = self.default_payload(overlay_id)
        if isinstance(payload, dict):
            normalized.update(payload)
        self._index_file(overlay_id).write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def default_payload(overlay_id: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "overlay_id": str(overlay_id or "").strip(),
            "updated_at": None,
            "response_language": None,
            "preferences": [],
            "stable_facts": [],
        }


class UserOverlayStateStore(JsonProgressStore):
    """Progress state for overlay promotion and rebuild workflows."""

    def __init__(self, overlay_id: str, *, app_home: str | Path | None = None):
        super().__init__(get_user_overlay_state_file(overlay_id, app_home=app_home))
