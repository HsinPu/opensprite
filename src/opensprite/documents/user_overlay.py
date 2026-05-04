"""Stable cross-session user overlay store."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from ..context.paths import (
    get_user_overlay_file,
    get_user_overlay_index_file,
    get_user_overlay_state_file,
)
from .base import ConversationDocumentStore
from .safety import validate_durable_memory_text
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
        validate_durable_memory_text(content)
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


_SECTION_HEADING_RE = re.compile(r"^#+\s+(?P<title>.+?)\s*$")
_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}")
_PLACEHOLDER_BULLETS = {
    "no learned communication preferences yet.",
    "no learned work context yet.",
    "no learned stable constraints yet.",
    "no learned user profile details yet.",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_bullets(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        stripped = str(line or "").strip()
        if not stripped.startswith("-"):
            continue
        text = stripped[1:].strip()
        if not text or text.lower() in _PLACEHOLDER_BULLETS or text == "not set":
            continue
        if text not in items:
            items.append(text)
    return items


def _section_block(markdown: str, heading: str) -> str:
    current_heading = ""
    collected: list[str] = []
    for raw_line in str(markdown or "").splitlines():
        heading_match = _SECTION_HEADING_RE.match(raw_line)
        if heading_match:
            current_heading = heading_match.group("title").strip().lower()
            continue
        if current_heading == heading.strip().lower():
            collected.append(raw_line)
    return "\n".join(collected)


def _section_bullets(markdown: str, heading: str) -> list[str]:
    return _normalize_bullets(_section_block(markdown, heading).splitlines())


def _profile_bullets(profile_block: str) -> list[str]:
    return _normalize_bullets(str(profile_block or "").splitlines())


def _response_language(response_language_block: str) -> str | None:
    items = _normalize_bullets(str(response_language_block or "").splitlines())
    return items[0] if items else None


def _render_overlay(preferences: list[str], stable_facts: list[str], response_language: str | None) -> str:
    preference_lines = "\n".join(f"- {item}" for item in preferences) if preferences else "- "
    fact_lines = "\n".join(f"- {item}" for item in stable_facts) if stable_facts else "- "
    language_line = f"- {response_language}" if response_language else "- not set"
    return (
        "# Stable Preferences\n"
        f"{preference_lines}\n\n"
        "# Stable Facts\n"
        f"{fact_lines}\n\n"
        "# Response Language\n"
        f"{language_line}\n"
    )


def _merge_stable_lists(existing: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    for item in [*existing, *incoming]:
        text = str(item or "").strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return normalized or "item"


class UserOverlayPromotionService:
    """Deterministically promote stable session profile/facts into a cross-session overlay."""

    def __init__(self, *, overlay_store: UserOverlayStore, index_store: UserOverlayIndexStore):
        self.overlay_store = overlay_store
        self.index_store = index_store

    def update_from_session_documents(
        self,
        overlay_id: str,
        *,
        profile_block: str,
        response_language_block: str,
        memory_text: str,
        source_session_id: str,
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        current_overlay = self.overlay_store.read(overlay_id)
        existing_preferences = _section_bullets(current_overlay, "Stable Preferences")
        existing_facts = _section_bullets(current_overlay, "Stable Facts")
        existing_language = _response_language(_section_block(current_overlay, "Response Language"))

        profile_preferences = _profile_bullets(profile_block)
        memory_preferences = _section_bullets(memory_text, "User Preferences")
        memory_facts = _section_bullets(memory_text, "Important Facts")

        next_preferences = _merge_stable_lists(existing_preferences, [*profile_preferences, *memory_preferences])
        next_facts = _merge_stable_lists(existing_facts, memory_facts)
        next_language = _response_language(response_language_block) or existing_language

        rendered = _render_overlay(next_preferences, next_facts, next_language)
        changed = rendered.strip() != current_overlay.strip()
        if changed or not current_overlay:
            self.overlay_store.write(overlay_id, rendered)

        now = _now_iso()
        self.index_store.write(
            overlay_id,
            {
                "schema_version": 1,
                "overlay_id": overlay_id,
                "updated_at": now,
                "response_language": (
                    {
                        "text": next_language,
                        "confidence": 0.95,
                        "source_sessions": [source_session_id],
                        **({"source_runs": [source_run_id]} if source_run_id else {}),
                        "updated_at": now,
                    }
                    if next_language
                    else None
                ),
                "preferences": [
                    {
                        "id": f"pref:{_slug(item)}",
                        "text": item,
                        "confidence": 0.9,
                        "source_sessions": [source_session_id],
                        **({"source_runs": [source_run_id]} if source_run_id else {}),
                        "updated_at": now,
                    }
                    for item in next_preferences
                ],
                "stable_facts": [
                    {
                        "id": f"fact:{_slug(item)}",
                        "text": item,
                        "confidence": 0.85,
                        "source_sessions": [source_session_id],
                        **({"source_runs": [source_run_id]} if source_run_id else {}),
                        "updated_at": now,
                    }
                    for item in next_facts
                ],
            },
        )

        return {
            "changed": changed,
            "overlay_id": overlay_id,
            "preferences": next_preferences,
            "stable_facts": next_facts,
            "response_language": next_language,
        }


class UserOverlayRetrievalPlanner:
    """Select concise stable overlay context relevant to the current turn."""

    def __init__(self, *, index_store: UserOverlayIndexStore, item_limit: int = 4):
        self.index_store = index_store
        self.item_limit = max(1, item_limit)

    def build_context(self, overlay_id: str | None, current_message: str) -> str:
        normalized_overlay_id = str(overlay_id or "").strip()
        if not normalized_overlay_id:
            return ""
        payload = self.index_store.read(normalized_overlay_id)
        tokens = self._tokenize(current_message)
        response_language = payload.get("response_language") if isinstance(payload.get("response_language"), dict) else None
        preferences = [dict(item) for item in payload.get("preferences", []) if isinstance(item, dict)]
        stable_facts = [dict(item) for item in payload.get("stable_facts", []) if isinstance(item, dict)]

        ranked_preferences = self._rank_entries(preferences, tokens)
        ranked_facts = self._rank_entries(stable_facts, tokens)
        selected_preferences = ranked_preferences[: min(2, self.item_limit)]
        remaining = max(0, self.item_limit - len(selected_preferences))
        selected_facts = ranked_facts[:remaining]

        if not response_language and not selected_preferences and not selected_facts:
            return ""

        lines = ["# Relevant Stable User Overlay"]
        if response_language and str(response_language.get("text") or "").strip():
            lines.extend(["", "## Response Language", f"- {str(response_language.get('text') or '').strip()}"])
        if selected_preferences:
            lines.extend(["", "## Relevant Stable Preferences", *[f"- {item['text']}" for item in selected_preferences]])
        if selected_facts:
            lines.extend(["", "## Relevant Stable Facts", *[f"- {item['text']}" for item in selected_facts]])
        return "\n".join(lines).strip()

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        seen: list[str] = []
        for token in _TOKEN_PATTERN.findall(str(text or "").lower()):
            if token not in seen:
                seen.append(token)
        return seen

    @staticmethod
    def _score_entry(entry: dict[str, Any], tokens: list[str]) -> int:
        haystack = str(entry.get("text") or "").lower()
        score = 0
        for token in tokens:
            if token and token in haystack:
                score += 5
        score += int(float(entry.get("confidence") or 0) * 10)
        return score

    def _rank_entries(self, entries: list[dict[str, Any]], tokens: list[str]) -> list[dict[str, Any]]:
        ranked = sorted(
            entries,
            key=lambda entry: (
                self._score_entry(entry, tokens),
                str(entry.get("updated_at") or ""),
            ),
            reverse=True,
        )
        if tokens:
            matched = [entry for entry in ranked if self._score_entry(entry, tokens) > int(float(entry.get("confidence") or 0) * 10)]
            if matched:
                return matched
        return ranked
