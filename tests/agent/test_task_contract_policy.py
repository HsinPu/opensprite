from opensprite.agent.task_contract import (
    AcceptanceCriterion,
    EvidenceRequirement,
    ITEMIZED_OUTPUT_CRITERION_KIND,
    MEDIA_ARTIFACT_CRITERION_KIND,
    OPERATION_REPORT_CRITERION_KIND,
    PLANNER_INVALID_JSON_REASON,
    PLANNER_UNAVAILABLE_REASON,
    PLANNER_UNSUPPORTED_TASK_TYPE_REASON,
    PLANNER_VALIDATED_REASON,
    SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND,
    TaskContract,
    VERIFICATION_OR_GAP_CRITERION_KIND,
    WORKSPACE_LOCATION_CRITERION_KIND,
    _contract_from_planner_payload,
    _ensure_task_type_tool_groups,
    _normalize_planner_tool_groups,
    contract_requests_itemized_output,
    contract_requests_source_material,
    contract_requests_source_reference,
    contract_requests_substantive_final_answer,
    is_itemized_output_criterion,
    is_media_artifact_criterion,
    is_operation_report_criterion,
    is_source_artifact_criterion,
    is_source_detail_criterion,
    is_source_reference_criterion,
    is_substantive_final_answer_criterion,
    is_verification_or_gap_criterion,
    is_workspace_location_criterion,
    missing_evidence,
)
from opensprite.tools.evidence import ToolEvidence


def test_planner_fallback_reasons_are_centralized():
    assert PLANNER_UNAVAILABLE_REASON == "task contract planner unavailable: llm not configured"
    assert PLANNER_INVALID_JSON_REASON == "task contract planner returned invalid JSON"
    assert PLANNER_VALIDATED_REASON == "llm planner returned a task contract"

    contract = _contract_from_planner_payload(
        {"task_type": "not_allowed"},
        task_intent=type("Intent", (), {"objective": "Plan this"})(),
        current_message="Plan this",
        history=None,
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=None,
    )

    assert contract.planner_metadata["reason"] == PLANNER_UNSUPPORTED_TASK_TYPE_REASON

    validated_contract = _contract_from_planner_payload(
        {"task_type": "pure_answer"},
        task_intent=type("Intent", (), {"objective": "Answer this"})(),
        current_message="Answer this",
        history=None,
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=None,
    )

    assert validated_contract.planner_metadata["reason"] == PLANNER_VALIDATED_REASON


def test_planner_tool_group_aliases_are_normalized_without_duplicates():
    groups = _normalize_planner_tool_groups(["workspace_change", "workspace_write", "media_analysis", "ops", "unknown"])

    assert groups == ["workspace_write", "media", "verification"]


def test_task_type_required_tool_groups_are_added_from_policy_map():
    groups = ["workspace_write"]

    _ensure_task_type_tool_groups("code_change", groups)

    assert groups == ["workspace_write", "workspace_read"]


def test_task_type_required_tool_groups_preserve_existing_order_for_unknown_task_type():
    groups = ["execution"]

    _ensure_task_type_tool_groups("operations", groups)

    assert groups == ["execution"]


def test_missing_evidence_uses_requirement_kind_policy_helpers():
    contract = TaskContract(
        objective="Inspect two files and verify changes.",
        task_type="workspace_change",
        requirements=(
            EvidenceRequirement(kind="tool_group", tool_group="workspace_read", description="Read the workspace."),
            EvidenceRequirement(
                kind="resource_coverage",
                tool_group="workspace_read",
                resource_ids=("file:a", "file:b"),
                coverage="all",
            ),
            EvidenceRequirement(kind="file_change", description="Change a file."),
            EvidenceRequirement(kind="verification", description="Verify the result."),
        ),
    )

    missing = missing_evidence(
        contract,
        (ToolEvidence(name="read_file", resource_ids=("file:a",)),),
        file_change_count=0,
        verification_passed=False,
    )

    assert "Read the workspace." not in missing
    assert "Missing workspace_read coverage for: file:b" in missing
    assert "Change a file." in missing
    assert "Verify the result." in missing


def test_acceptance_criterion_policy_helpers():
    contract = TaskContract(
        objective="Summarize source material.",
        task_type="web_research",
        acceptance_criteria=(
            AcceptanceCriterion(kind="source_reference"),
            AcceptanceCriterion(kind="source_detail"),
            AcceptanceCriterion(kind=ITEMIZED_OUTPUT_CRITERION_KIND),
            AcceptanceCriterion(kind=SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND),
        ),
    )

    assert contract_requests_source_reference(contract) is True
    assert contract_requests_source_material(contract) is True
    assert contract_requests_itemized_output(contract) is True
    assert contract_requests_substantive_final_answer(contract) is True
    assert contract_requests_source_reference(None) is False
    assert is_source_reference_criterion(contract.acceptance_criteria[0]) is True
    assert is_source_detail_criterion(contract.acceptance_criteria[1]) is True
    assert is_itemized_output_criterion(contract.acceptance_criteria[2]) is True
    assert is_substantive_final_answer_criterion(contract.acceptance_criteria[3]) is True
    assert is_source_artifact_criterion(AcceptanceCriterion(kind="source_artifact")) is True
    assert is_workspace_location_criterion(AcceptanceCriterion(kind=WORKSPACE_LOCATION_CRITERION_KIND)) is True
    assert is_media_artifact_criterion(AcceptanceCriterion(kind=MEDIA_ARTIFACT_CRITERION_KIND)) is True
    assert is_verification_or_gap_criterion(AcceptanceCriterion(kind=VERIFICATION_OR_GAP_CRITERION_KIND)) is True
    assert is_operation_report_criterion(AcceptanceCriterion(kind=OPERATION_REPORT_CRITERION_KIND)) is True
