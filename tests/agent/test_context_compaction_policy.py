from opensprite.agent.execution_support.events import (
    COMPACTED_CONVERSATION_STATE_HEADING,
    COMPACTED_TASK_STATE_HEADING,
    contains_compaction_handoff,
)


def test_contains_compaction_handoff_detects_shared_headings():
    assert contains_compaction_handoff(f"{COMPACTED_CONVERSATION_STATE_HEADING}\nsummary")
    assert contains_compaction_handoff(f"{COMPACTED_TASK_STATE_HEADING}\nsummary")
    assert not contains_compaction_handoff("# Other State\nsummary")
