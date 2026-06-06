from opensprite.documents.curator import SKILL_REVIEW_TRANSCRIPT_TOO_SHORT_REASON


def test_skill_review_reason_markers_are_stable():
    assert SKILL_REVIEW_TRANSCRIPT_TOO_SHORT_REASON == "transcript-too-short"
