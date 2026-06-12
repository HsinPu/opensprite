"""Media resource indexing for task contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ...media import MEDIA_ONLY_HISTORY_MARKER

_MEDIA_HISTORY_RE = re.compile(r"^(Images|Audios|Videos):\s*(?P<paths>.+)$", re.IGNORECASE | re.MULTILINE)
_CURRENT_IMAGE_RE = re.compile(r"User attached (?P<count>\d+) image", re.IGNORECASE)
_CURRENT_AUDIO_RE = re.compile(r"User attached (?P<count>\d+) audio", re.IGNORECASE)
_CURRENT_VIDEO_RE = re.compile(r"User attached (?P<count>\d+) video", re.IGNORECASE)
_RESOURCE_INDEX_PREFIX = {"image": "image_index", "audio": "audio_index", "video": "video_index"}


@dataclass(frozen=True)
class ResourceRef:
    """A resource that the task may need to cover."""

    id: str
    kind: str
    path: str = ""
    source: str = "history"

    def to_metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "source": self.source,
        }


class ResourceIndex:
    """Normalized resource view for current-turn and recent-history media."""

    def __init__(self, resources: list[ResourceRef] | tuple[ResourceRef, ...]):
        self.resources = tuple(_dedupe_resources(list(resources)))

    @classmethod
    def from_turn_and_history(
        cls,
        *,
        current_message: str,
        history: list[dict[str, Any]] | None = None,
        current_image_files: list[str] | None = None,
        current_audio_files: list[str] | None = None,
        current_video_files: list[str] | None = None,
    ) -> "ResourceIndex":
        resources = cls._resources_from_turn(
            current_message=current_message,
            current_image_files=current_image_files,
            current_audio_files=current_audio_files,
            current_video_files=current_video_files,
        )
        resources.extend(cls._recent_media_resources(history or []))
        return cls(resources)

    def by_kind(self, kind: str) -> list[ResourceRef]:
        return [item for item in self.resources if item.kind == kind]

    @staticmethod
    def aliases_for(resources: tuple[ResourceRef, ...] | list[ResourceRef]) -> dict[str, set[str]]:
        """Return equivalent path/index IDs for current-turn media resources."""
        aliases: dict[str, set[str]] = {}
        current_by_kind: dict[str, list[ResourceRef]] = {"image": [], "audio": [], "video": []}
        for resource in resources:
            if resource.source == "current_turn" and resource.kind in current_by_kind:
                current_by_kind[resource.kind].append(resource)

        for kind, kind_resources in current_by_kind.items():
            for index, resource in enumerate(kind_resources):
                equivalent_ids = {resource.id, f"{_RESOURCE_INDEX_PREFIX[kind]}:{index}"}
                if resource.path:
                    equivalent_ids.add(f"{kind}:{resource.path}")
                for resource_id in equivalent_ids:
                    aliases.setdefault(resource_id, set()).update(equivalent_ids)
        return aliases

    @staticmethod
    def _resources_from_turn(
        *,
        current_message: str,
        current_image_files: list[str] | None,
        current_audio_files: list[str] | None,
        current_video_files: list[str] | None,
    ) -> list[ResourceRef]:
        resources: list[ResourceRef] = []
        for index, path in enumerate(current_image_files or []):
            normalized = str(path or "").strip().replace("\\", "/")
            resource_id = f"image:{normalized}" if normalized else f"image_index:{index}"
            resources.append(ResourceRef(id=resource_id, kind="image", path=normalized, source="current_turn"))
        for index, path in enumerate(current_audio_files or []):
            normalized = str(path or "").strip().replace("\\", "/")
            resource_id = f"audio:{normalized}" if normalized else f"audio_index:{index}"
            resources.append(ResourceRef(id=resource_id, kind="audio", path=normalized, source="current_turn"))
        for index, path in enumerate(current_video_files or []):
            normalized = str(path or "").strip().replace("\\", "/")
            resource_id = f"video:{normalized}" if normalized else f"video_index:{index}"
            resources.append(ResourceRef(id=resource_id, kind="video", path=normalized, source="current_turn"))

        if not resources:
            resources.extend(_current_index_resources(current_message, _CURRENT_IMAGE_RE, "image"))
            resources.extend(_current_index_resources(current_message, _CURRENT_AUDIO_RE, "audio"))
            resources.extend(_current_index_resources(current_message, _CURRENT_VIDEO_RE, "video"))
        return resources

    @staticmethod
    def _recent_media_resources(history: list[dict[str, Any]]) -> list[ResourceRef]:
        resources: list[ResourceRef] = []
        found_recent_batch = False
        for message in reversed(history[-20:]):
            role = str(message.get("role") or "")
            if role != "user":
                continue
            content = str(message.get("content") or "")
            if MEDIA_ONLY_HISTORY_MARKER not in content:
                if found_recent_batch:
                    break
                continue
            found_recent_batch = True
            for match in _MEDIA_HISTORY_RE.finditer(content):
                label = match.group(1).lower()
                kind = {"images": "image", "audios": "audio", "videos": "video"}.get(label, "")
                for raw_path in match.group("paths").split(","):
                    path = raw_path.strip().replace("\\", "/")
                    if path:
                        resources.append(ResourceRef(id=f"{kind}:{path}", kind=kind, path=path, source="recent_media"))
        return resources


def _current_index_resources(current_message: str, pattern: re.Pattern[str], kind: str) -> list[ResourceRef]:
    match = pattern.search(current_message or "")
    if not match:
        return []
    count = int(match.group("count") or 0)
    index_prefix = _RESOURCE_INDEX_PREFIX[kind]
    return [ResourceRef(id=f"{index_prefix}:{index}", kind=kind, source="current_turn") for index in range(max(0, count))]


def _dedupe_resources(resources: list[ResourceRef]) -> list[ResourceRef]:
    by_id: dict[str, ResourceRef] = {}
    order: list[str] = []
    for item in resources:
        if not item.id or item.id in by_id:
            continue
        by_id[item.id] = item
        order.append(item.id)
    return [by_id[item_id] for item_id in order]
