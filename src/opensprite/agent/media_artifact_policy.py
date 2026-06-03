"""Shared policy for media task artifact coverage."""

from __future__ import annotations

from typing import Iterable, Protocol


MEDIA_ARTIFACT_KINDS = frozenset({"image_text", "image_analysis", "audio_transcript", "video_analysis"})


class MediaArtifactLike(Protocol):
    kind: str
    ok: bool


def is_media_artifact_kind(kind: str | None) -> bool:
    return str(kind or "").strip() in MEDIA_ARTIFACT_KINDS


def count_media_artifacts(artifacts: Iterable[MediaArtifactLike]) -> int:
    return sum(1 for artifact in artifacts if artifact.ok and is_media_artifact_kind(artifact.kind))


def media_artifact_gap_follow_up_instruction(media_gap: str) -> str:
    return (
        "\n- Quality follow-up: the previous pass did not produce typed artifacts for every required resource. "
        "Use the relevant media/source tools for each missing resource before finalizing. "
        "Do not claim completion until each required resource has a concrete tool-derived result.\n"
        f"{media_gap}"
    )
