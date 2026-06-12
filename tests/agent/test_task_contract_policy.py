import pytest

from opensprite.agent.task.contract import (
    AcceptanceCriterion,
    EvidenceRequirement,
    ITEMIZED_OUTPUT_CRITERION_KIND,
    MEDIA_ARTIFACT_CRITERION_KIND,
    OPERATION_REPORT_CRITERION_KIND,
    PLANNER_METADATA_REASON_FIELD,
    PLANNER_METADATA_STATUS_FIELD,
    PLANNER_INVALID_JSON_REASON,
    PLANNER_UNAVAILABLE_REASON,
    PLANNER_UNSUPPORTED_TASK_TYPE_REASON,
    PLANNER_VALIDATED_REASON,
    SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND,
    TaskContract,
    TaskPlannerError,
    VERIFICATION_OR_GAP_CRITERION_KIND,
    WORKSPACE_LOCATION_QUALITY_CHECK,
    WORKSPACE_LOCATION_CRITERION_KIND,
    _build_task_planner_prompt,
    _compact,
    _contract_from_task_planner_payload,
    _coerce_bool,
    _coerce_confidence,
    _normalize_planner_quality_checks,
    _normalize_planner_required_tools,
    _parse_json_object,
    _truncate,
    _truncate_middle,
    task_planner_reason,
    task_planner_status,
)
from opensprite.agent.task.resolution import (
    _resolver_compact,
    _resolver_coerce_bool,
    _resolver_coerce_confidence,
    _resolver_parse_json_object,
    _resolver_truncate,
    _resolver_truncate_middle,
    is_allowed_continuation_type,
    is_ambiguous_boundary_continuation_type,
    is_current_task_continuation_type,
    is_current_task_replacement_type,
    is_follow_up_continuation_type,
    is_new_task_continuation_type,
    is_objective_resolution_enrichable_type,
    is_objective_resolution_skip_type,
)
from opensprite.agent.task.evidence_policy import (
    contract_expects_file_change,
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
from opensprite.agent.task.planning_mode import build_planner_capability_catalog
from opensprite.tools.base import Tool
from opensprite.tools.evidence import ToolEvidence
from opensprite.tools.registry import ToolRegistry


class _CatalogTool(Tool):
    def __init__(
        self,
        name: str,
        *,
        description: str,
    ):
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def _execute(self, **kwargs) -> str:
        return "ok"


def test_task_planner_metadata_helpers_normalize_values():
    contract = TaskContract(
        objective="x",
        task_type="pure_answer",
        planner_metadata={
            PLANNER_METADATA_STATUS_FIELD: " validated ",
            PLANNER_METADATA_REASON_FIELD: " planner ok ",
        },
    )

    assert task_planner_status(contract) == "validated"
    assert task_planner_reason(contract) == "planner ok"
    assert task_planner_status(object()) == ""
    assert task_planner_reason(object()) == ""


def test_planner_error_reasons_are_centralized():
    assert PLANNER_UNAVAILABLE_REASON == "task planner unavailable: llm not configured"
    assert PLANNER_INVALID_JSON_REASON == "task planner returned invalid JSON"
    assert PLANNER_VALIDATED_REASON == "llm planner returned a task contract"

    with pytest.raises(TaskPlannerError, match=PLANNER_UNSUPPORTED_TASK_TYPE_REASON):
        _contract_from_task_planner_payload(
            {"task_type": "not_allowed"},
            fallback_objective="Plan this",
            current_message="Plan this",
            history=None,
            current_image_files=None,
            current_audio_files=None,
            current_video_files=None,
            task_context_decision=None,
        )

    validated_contract = _contract_from_task_planner_payload(
        {"task_type": "pure_answer"},
        fallback_objective="Answer this",
        current_message="Answer this",
        history=None,
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=None,
    )

    assert validated_contract.planner_metadata["reason"] == PLANNER_VALIDATED_REASON


def test_planner_payload_task_type_is_allowed_after_trim():
    contract = _contract_from_task_planner_payload(
        {"task_type": " pure_answer "},
        fallback_objective="Answer this",
        current_message="Answer this",
        history=None,
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=None,
    )

    assert contract.task_type == "pure_answer"
    assert contract.planner_metadata["raw_task_type"] == "pure_answer"


def test_policy_bool_helpers_keep_context_specific_truthy_values():
    assert _resolver_coerce_bool(" yes ") is True
    assert _resolver_coerce_bool(" 是 ") is False
    assert _coerce_bool(" 是 ") is True


def test_policy_confidence_helpers_share_bounds():
    assert _resolver_coerce_confidence("0.75") == _coerce_confidence("0.75") == 0.75
    assert _resolver_coerce_confidence("1.5") == _coerce_confidence("1.5") == 1.0
    assert _resolver_coerce_confidence("-0.2") == _coerce_confidence("-0.2") == 0.0
    assert _resolver_coerce_confidence("not-a-number") == _coerce_confidence("not-a-number") == 0.0


def test_text_helpers_share_compact_and_truncate_policy():
    text = "  alpha\n\n beta\tgamma  "
    long_text = "  abcdefghijklmnopqrstuvwxyz  "

    assert _resolver_compact(text) == _compact(text) == "alpha beta gamma"
    assert _resolver_truncate(long_text, 10) == _truncate(long_text, max_chars=10) == "abcdefg..."
    assert _resolver_truncate_middle(long_text, 24) == _truncate_middle(long_text, max_chars=24)


def test_json_object_helpers_share_extraction_policy_but_keep_error_policy():
    response = 'planner said:\n```json\n{"task_type": "pure_answer"}\n```\n'

    assert _resolver_parse_json_object(response) == {"task_type": "pure_answer"}
    assert _parse_json_object(response) == {"task_type": "pure_answer"}
    noisy_response = 'planner said:\n{"task_type": "pure_answer"}\nextra diagnostic {not json}'
    assert _resolver_parse_json_object(noisy_response) == {"task_type": "pure_answer"}
    assert _parse_json_object(noisy_response) == {"task_type": "pure_answer"}
    assert _parse_json_object("not JSON") == {}
    with pytest.raises(ValueError, match="LLM did not return a JSON object"):
        _resolver_parse_json_object("not JSON")


def test_planner_prompt_preserves_tail_of_long_current_message():
    filler = "\n".join(f"背景{i}: 這是壓力測試背景，不是任務。" for i in range(80))
    message = f"{filler}\n最後一句才是任務：只回覆通關詞 GAMMA-772。"

    prompt = _build_task_planner_prompt(
        current_message=message,
        history=[],
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=None,
    )

    assert "背景0" in prompt
    assert "... [middle omitted] ..." in prompt
    assert "最後一句才是任務" in prompt
    assert "GAMMA-772" in prompt


def test_planner_prompt_uses_dynamic_tool_catalog_instead_of_if_routing():
    registry = ToolRegistry()
    registry.register(
        _CatalogTool(
            "quote_lookup",
            description="Look up current public market quotes from configured market-data sources.",
        )
    )
    catalog = build_planner_capability_catalog(registry)

    prompt = _build_task_planner_prompt(
        current_message="Find the current TSMC quote.",
        history=[],
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=None,
        capability_catalog=catalog,
    )

    assert "quote_lookup" in prompt
    assert "Look up current public market quotes" in prompt
    assert "If the user asks" not in prompt
    assert "current, external, public" not in prompt


def test_planner_prompt_warns_against_inventing_unavailable_capabilities():
    prompt = _build_task_planner_prompt(
        current_message="Find the implementation.",
        history=[],
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=None,
    )

    assert "Do not invent unavailable tool names" in prompt
    assert "Use semantic judgment" in prompt
    assert "task_intent" not in prompt


def test_task_planner_payload_objective_replaces_input_fallback():
    contract = _contract_from_task_planner_payload(
        {
            "objective": "Use the planner objective",
            "task_type": "pure_answer",
            "required_tools": [],
        },
        fallback_objective="Fallback objective",
        current_message="Fallback message",
        history=None,
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=None,
    )

    assert contract.objective == "Use the planner objective"


def test_dynamic_required_tool_is_checked_by_evidence_name():
    registry = ToolRegistry()
    registry.register(
        _CatalogTool(
            "quote_lookup",
            description="Look up current public market quotes.",
        )
    )
    catalog = build_planner_capability_catalog(registry)

    contract = _contract_from_task_planner_payload(
        {
            "task_type": "task",
            "required_tools": ["quote_lookup"],
            "allow_no_tool_final": False,
            "reason": "The available market_data capability can gather quote evidence.",
        },
        fallback_objective="Find the current TSMC quote.",
        current_message="Find the current TSMC quote.",
        history=None,
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=None,
        capability_catalog=catalog,
    )

    assert contract.task_type == "task"
    assert contract.allow_no_tool_final is False
    assert contract.required_tools == ("quote_lookup",)
    assert contract.planner_metadata["required_tools"] == ["quote_lookup"]
    assert missing_evidence(contract, (), file_change_count=0, verification_passed=False)
    assert missing_evidence(
        contract,
        (ToolEvidence(name="quote_lookup", args={}, result_preview="ok", ok=True),),
        file_change_count=0,
        verification_passed=False,
    ) == ()


def test_planner_required_tools_are_normalized_against_available_tools():
    tools = _normalize_planner_required_tools(
        [" read_file ", "read_file", "apply_patch", "unknown"],
        allowed_tools=("read_file", "apply_patch"),
    )

    assert tools == ["read_file", "apply_patch"]


def test_planner_quality_check_values_are_normalized_without_duplicates():
    checks = _normalize_planner_quality_checks([" WORKSPACE_LOCATION ", "workspace_location", "unknown"])

    assert checks == [WORKSPACE_LOCATION_QUALITY_CHECK]


def test_missing_evidence_uses_requirement_kind_policy_helpers():
    contract = TaskContract(
        objective="Inspect two files and verify changes.",
        task_type="code_change",
        required_tools=("read_file",),
        requirements=(
            EvidenceRequirement(
                kind="resource_coverage",
                tools=("read_file",),
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

    assert "Use required tool: read_file." not in missing
    assert "Missing resource coverage for: file:b" in missing
    assert "Change a file." in missing
    assert "Verify the result." in missing


def test_contract_expects_file_change_uses_requirement_attrs():
    file_change_contract = TaskContract(
        objective="Change a file.",
        task_type="analysis",
        requirements=(EvidenceRequirement(kind="file_change"),),
    )
    workspace_write_contract = TaskContract(
        objective="Write to the workspace.",
        task_type="analysis",
        required_tools=("apply_patch",),
    )

    assert contract_expects_file_change(file_change_contract) is True
    assert contract_expects_file_change(workspace_write_contract) is True


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


def test_continuation_type_policy_helpers_normalize_values():
    assert is_allowed_continuation_type(" continue_active_task ") is True
    assert is_follow_up_continuation_type(" continue_active_task ") is True
    assert is_current_task_continuation_type(" continue_active_task ") is True
    assert is_new_task_continuation_type(" new_task ") is True
    assert is_current_task_replacement_type(" new_task ") is True
    assert is_objective_resolution_skip_type(" ack ") is True
    assert is_objective_resolution_enrichable_type(" continue_last_answer ") is True
    assert is_ambiguous_boundary_continuation_type(" ambiguous_boundary ") is True
    assert is_allowed_continuation_type("unknown") is False
