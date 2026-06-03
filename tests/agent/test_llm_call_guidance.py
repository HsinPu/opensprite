from opensprite.agent.llm_call import _format_acceptance_criterion
from opensprite.agent.task_contract import AcceptanceCriterion


def test_format_acceptance_criterion_uses_policy_helpers():
    assert "itemized result entries" in _format_acceptance_criterion(
        AcceptanceCriterion(kind="itemized_output", min_count=3)
    )
    assert "traceable source" in _format_acceptance_criterion(
        AcceptanceCriterion(kind="source_artifact", min_count=2)
    )
    assert "gathered source" in _format_acceptance_criterion(
        AcceptanceCriterion(kind="source_reference")
    )
    assert "verification gap" in _format_acceptance_criterion(
        AcceptanceCriterion(kind="verification_or_gap")
    )
