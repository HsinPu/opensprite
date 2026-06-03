from dataclasses import dataclass

from opensprite.agent.media_artifact_policy import (
    count_media_artifacts,
    is_media_artifact_kind,
    media_artifact_gap_follow_up_instruction,
)


@dataclass(frozen=True)
class Artifact:
    kind: str
    ok: bool = True


def test_media_artifact_policy_classifies_supported_artifact_kinds():
    assert is_media_artifact_kind("image_text") is True
    assert is_media_artifact_kind("image_analysis") is True
    assert is_media_artifact_kind("audio_transcript") is True
    assert is_media_artifact_kind("video_analysis") is True
    assert is_media_artifact_kind("web_source") is False


def test_media_artifact_policy_counts_only_ok_media_artifacts():
    artifacts = [
        Artifact("image_text", ok=True),
        Artifact("audio_transcript", ok=False),
        Artifact("web_source", ok=True),
        Artifact("video_analysis", ok=True),
    ]

    assert count_media_artifacts(artifacts) == 2


def test_media_artifact_gap_follow_up_instruction_includes_gap_detail():
    instruction = media_artifact_gap_follow_up_instruction("- Missing artifact for image:example.png")

    assert "typed artifacts for every required resource" in instruction
    assert "Use the relevant media/source tools" in instruction
    assert "- Missing artifact for image:example.png" in instruction
