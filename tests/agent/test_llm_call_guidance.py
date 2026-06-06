from opensprite.agent.execution import _format_acceptance_criterion
from opensprite.agent.task_contract import (
    AcceptanceCriterion,
    ITEMIZED_OUTPUT_CRITERION_KIND,
    SOURCE_ARTIFACT_CRITERION_KIND,
    SOURCE_REFERENCE_CRITERION_KIND,
    VERIFICATION_OR_GAP_CRITERION_KIND,
    WORKSPACE_LOCATION_CRITERION_KIND,
)


def test_format_acceptance_criterion_uses_policy_helpers():
    assert "itemized result entries" in _format_acceptance_criterion(
        AcceptanceCriterion(kind=ITEMIZED_OUTPUT_CRITERION_KIND, min_count=3)
    )
    assert "traceable source" in _format_acceptance_criterion(
        AcceptanceCriterion(kind=SOURCE_ARTIFACT_CRITERION_KIND, min_count=2)
    )
    assert "gathered source" in _format_acceptance_criterion(
        AcceptanceCriterion(kind=SOURCE_REFERENCE_CRITERION_KIND)
    )
    assert "verification gap" in _format_acceptance_criterion(
        AcceptanceCriterion(kind=VERIFICATION_OR_GAP_CRITERION_KIND)
    )
    workspace_guidance = _format_acceptance_criterion(
        AcceptanceCriterion(kind=WORKSPACE_LOCATION_CRITERION_KIND)
    )
    assert "workspace file path" in workspace_guidance
    assert "shown by workspace tool output" in workspace_guidance
    assert "verify uncertain symbol names" in workspace_guidance
