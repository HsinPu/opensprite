"""Task artifact models and builders produced by tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...tool_names import (
    ANALYZE_IMAGE_TOOL_NAME,
    ANALYZE_VIDEO_TOOL_NAME,
    EXEC_TOOL_NAME,
    OCR_IMAGE_TOOL_NAME,
    TRANSCRIBE_AUDIO_TOOL_NAME,
)
from ...tools.evidence import (
    VERIFICATION_RESULT_ARTIFACT_KIND,
    VERIFICATION_TOOL_NAME,
    WEB_SOURCE_ARTIFACT_KIND,
    WEB_SOURCE_ARTIFACT_TOOLS,
    ToolEvidence,
    is_web_source_artifact_kind,
)


TASK_ARTIFACTS_NOT_PRODUCED_REASON = "required task artifacts were not produced"
_TOOL_ARTIFACT_KINDS: dict[str, str] = {
    OCR_IMAGE_TOOL_NAME: "image_text",
    ANALYZE_IMAGE_TOOL_NAME: "image_analysis",
    TRANSCRIBE_AUDIO_TOOL_NAME: "audio_transcript",
    ANALYZE_VIDEO_TOOL_NAME: "video_analysis",
    **{tool_name: WEB_SOURCE_ARTIFACT_KIND for tool_name in WEB_SOURCE_ARTIFACT_TOOLS},
    VERIFICATION_TOOL_NAME: VERIFICATION_RESULT_ARTIFACT_KIND,
    EXEC_TOOL_NAME: "command_result",
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
