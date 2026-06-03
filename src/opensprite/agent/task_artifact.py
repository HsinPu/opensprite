"""Typed task artifacts produced from successful tool executions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..tools.evidence import ToolEvidence
from .web_source_policy import (
    WEB_SOURCE_ARTIFACT_KIND,
    WEB_SOURCE_ARTIFACT_TOOLS,
    is_web_source_artifact_kind,
)


_TOOL_ARTIFACT_KINDS: dict[str, str] = {
    "ocr_image": "image_text",
    "analyze_image": "image_analysis",
    "transcribe_audio": "audio_transcript",
    "analyze_video": "video_analysis",
    **{tool_name: WEB_SOURCE_ARTIFACT_KIND for tool_name in WEB_SOURCE_ARTIFACT_TOOLS},
    "verify": "verification_result",
    "exec": "command_result",
}


@dataclass(frozen=True)
class TaskArtifact:
    """One structured output artifact available for completion quality checks."""

    kind: str
    source_tool: str
    resource_ids: tuple[str, ...] = ()
    content_preview: str = ""
    ok: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_tool": self.source_tool,
            "resource_ids": list(self.resource_ids),
            "content_preview": self.content_preview,
            "ok": self.ok,
            "metadata": dict(self.metadata),
        }


def build_task_artifact(evidence: ToolEvidence) -> TaskArtifact | None:
    """Create a typed artifact when a tool produced reusable task output."""
    if not evidence.ok:
        return None
    kind = _TOOL_ARTIFACT_KINDS.get(evidence.name)
    if kind is None:
        return None
    if is_web_source_artifact_kind(kind) and not _has_traceable_sources(evidence.metadata):
        return None
    metadata = {"tool_args": dict(evidence.args)}
    metadata.update(dict(evidence.metadata))
    return TaskArtifact(
        kind=kind,
        source_tool=evidence.name,
        resource_ids=tuple(evidence.resource_ids),
        content_preview=evidence.result_preview,
        ok=evidence.ok,
        metadata=metadata,
    )


def _has_traceable_sources(metadata: dict[str, Any]) -> bool:
    sources = metadata.get("sources") if isinstance(metadata, dict) else None
    if not isinstance(sources, list):
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        title = str(source.get("title") or "").strip()
        snippet = str(source.get("snippet") or "").strip()
        if url and (title or snippet):
            return True
    return False
