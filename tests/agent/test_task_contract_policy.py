from opensprite.agent.task_contract import EvidenceRequirement, TaskContract, _ensure_task_type_tool_groups, _normalize_planner_tool_groups, missing_evidence
from opensprite.tools.evidence import ToolEvidence


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
