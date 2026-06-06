from dataclasses import replace
import json

import pytest

from opensprite.agent.completion_gate import (
    CompletionJudgeVerdict,
    CompletionGateService,
    EvidenceGateService,
    TASK_CONTRACT_PLANNER_UNVALIDATED_REASON,
    _accepts_final_response_task_type,
    _completion_status_for_unsuccessful_workflow,
    _intent_supports_fallback_active_task_update,
    _is_blocking_planner_status,
    _is_analysis_response_intent_kind,
    _is_clean_structured_review_status,
    _is_completed_delegated_review_status,
    _is_generic_task_response_intent_kind,
    _is_one_turn_intent_kind,
    _is_history_retrieval_tool,
    _is_cancelled_workflow_status,
    _is_failed_workflow_status,
    _is_optional_web_discovery_failure_tool,
    _is_optional_web_fetch_failure_tool,
    _is_optional_workspace_batch_failure_tool,
    _is_plain_answer_task_type,
    _is_python_file_path,
    _is_python_test_path,
    _is_web_app_path,
    _path_requires_delegated_review,
    _is_read_only_blocking_requirement_kind,
    _is_read_only_blocking_tool_group,
    _is_read_only_task_type,
    _is_research_then_outline_workflow,
    _is_verification_requirement_kind,
    _is_verification_result_artifact_kind,
    _is_verification_tool,
    _is_verification_tool_group,
    _is_review_workflow,
    _is_unsuccessful_workflow_status,
    _is_workflow_completion_intent_kind,
    _workflow_fix_follow_up_fields,
    _workflow_gate_is_complete,
    _workflow_gate_needs_verification,
)
from opensprite.agent.task_contract import LLM_PLANNER_CONTRACT_SOURCES
from opensprite.tools.evidence import (
    GATHERED_SOURCE_REFERENCE_MISSING_REASON,
    SOURCE_ARTIFACTS_NOT_TRACEABLE_REASON,
    SOURCE_MATERIAL_INSUFFICIENT_REASON,
    UNGATHERED_SOURCE_REFERENCED_REASON,
    is_fetched_web_source_artifact_tool,
    is_web_discovery_tool,
    is_web_fetch_source_record_tool,
    is_web_research_source_artifact_tool,
    is_web_research_task_type,
    is_web_research_tool_group,
    is_web_source_artifact_kind,
    is_web_source_evidence_tool,
)
from opensprite.agent.completion_gate import AutoContinueService
from opensprite.agent.execution import ExecutionResult, is_max_tool_iterations_stop_reason
from opensprite.context.message_history import HISTORY_RECALLED_ITEMS_INSUFFICIENT_REASON
from opensprite.agent.completion_gate import OPERATION_VALIDATION_OR_RISK_MISSING_REASON
from opensprite.agent.completion_gate import QualityGateService
from opensprite.agent.completion_gate import ITEMIZED_OUTPUT_MISSING_REASON, TERSE_FINAL_ANSWER_REASON
from opensprite.agent.execution import TASK_ARTIFACTS_NOT_PRODUCED_REASON, TaskArtifact
from opensprite.agent.task_contract import (
    AcceptanceCriterion,
    COMMAND_VERSION_QUALITY_CHECK,
    EvidenceRequirement,
    ITEMIZED_OUTPUT_CRITERION_KIND,
    OPERATION_REPORT_CRITERION_KIND,
    PLANNER_INVALID_JSON_REASON,
    PLANNER_INVALID_STATUS,
    PLANNER_METADATA_REASON_FIELD,
    PLANNER_METADATA_STATUS_FIELD,
    REPOSITORY_STATUS_QUALITY_CHECK,
    SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND,
    TaskContract,
    WORKSPACE_LOCATION_CRITERION_KIND,
    _contract_from_task_planner_payload,
)
from opensprite.tools.evidence import VERIFICATION_OUTCOME_OR_GAP_MISSING_REASON, VERIFICATION_STATUS_METADATA_FIELD
from opensprite.agent.completion_gate import (
    WORKSPACE_CONTEXT_REFERENCE_MISSING_REASON,
    WORKSPACE_LOCATION_MISSING_REASON,
)
from opensprite.agent.task_contract import TaskContextDecision
from opensprite.agent.task_contract import TaskIntent, TaskIntentService
from opensprite.config import DocumentLlmConfig
from opensprite.storage.base import StoredDelegatedTask
from opensprite.tools.evidence import ToolEvidence
from opensprite.tools.result_status import tool_error_result
from tests.agent.task_contract_test_helpers import TaskContractService


class StaticCompletionJudge:
    def __init__(self, verdict: CompletionJudgeVerdict):
        self.verdict = verdict
        self.calls = []

    async def judge(self, **kwargs):
        self.calls.append(kwargs)
        return self.verdict


async def _evaluate_with_static_judge(
    *,
    status: str,
    reason: str,
    task_intent: TaskIntent,
    response_text: str,
    execution_result: ExecutionResult,
    progress_only_response: bool = False,
):
    judge = StaticCompletionJudge(
        CompletionJudgeVerdict(
            status=status,
            reason=reason,
            progress_only_response=progress_only_response,
        )
    )
    service = CompletionGateService(
        llm_config=DocumentLlmConfig(
            pass_decoding_params=False,
            temperature=0,
            max_tokens=700,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
        ),
        judge_service=judge,
    )
    return await service.evaluate_with_judge(
        task_intent=task_intent,
        response_text=response_text,
        execution_result=execution_result,
        provider=object(),
        model="test-model",
    )


@pytest.mark.anyio
async def test_completion_gate_downgrades_complete_judge_verdict_when_review_fails():
    intent = TaskIntentService().classify("Research the latest AI agent market trends.")
    response = "The answer cites domains but the review found unsupported source claims."
    judge = StaticCompletionJudge(
        CompletionJudgeVerdict(
            status="complete",
            reason="judge accepted answer",
            active_task_status="done",
            review_required=True,
            review_attempted=True,
            review_passed=False,
            review_summary="response cited sources that were not gathered",
            review_finding_count=1,
        )
    )
    service = CompletionGateService(
        llm_config=DocumentLlmConfig(
            pass_decoding_params=False,
            temperature=0,
            max_tokens=700,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
        ),
        judge_service=judge,
    )

    result = await service.evaluate_with_judge(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
        provider=object(),
        model="test-model",
    )

    assert result.status == "needs_review"
    assert result.active_task_status is None
    assert result.should_update_active_task is False
    assert result.review_required is True
    assert result.review_attempted is True
    assert result.review_passed is False
    assert result.review_finding_count == 1


def _web_source_artifact() -> TaskArtifact:
    return TaskArtifact(
        kind="web_source",
        source_tool="web_search",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_search",
                    "url": "https://www.reddit.com/dev/api/",
                    "title": "Reddit API docs",
                    "snippet": "Official Reddit API documentation for search and listings.",
                    "query": "reddit search api",
                    "provider": "duckduckgo",
                },
                {
                    "tool_name": "web_search",
                    "url": "https://www.reddit.com/wiki/api/",
                    "title": "Reddit API wiki",
                    "snippet": "Reddit API wiki with additional integration notes.",
                    "query": "reddit search api",
                    "provider": "duckduckgo",
                },
            ],
            "source_count": 2,
        },
    )


def _web_fetch_artifact(*, is_too_short: bool = False, blocked_or_challenge: bool = False) -> TaskArtifact:
    return TaskArtifact(
        kind="web_source",
        source_tool="web_fetch",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://www.reddit.com/dev/api/",
                    "title": "Reddit API docs",
                    "snippet": "Official Reddit API documentation for search, listings, authentication, and rate limits.",
                    "query": "https://www.reddit.com/dev/api/",
                    "provider": "web_fetch",
                    "content_chars": 120 if is_too_short else 1200,
                    "is_too_short": is_too_short,
                    "has_main_content": not is_too_short and not blocked_or_challenge,
                    "blocked_or_challenge": blocked_or_challenge,
                    "min_content_chars": 800,
                    "extractor": "trafilatura",
                    "truncated": False,
                }
            ],
            "source_count": 1,
        },
    )


def _web_research_artifacts() -> tuple[TaskArtifact, ...]:
    return (_web_source_artifact(), _web_fetch_artifact())


def test_completion_gate_blocks_unvalidated_task_contract():
    intent = TaskIntentService().classify("Find the latest stock price for TSMC")
    contract = TaskContract(
        objective=intent.objective,
        task_type="planning_error",
        allow_no_tool_final=False,
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
        planner_metadata={
            PLANNER_METADATA_STATUS_FIELD: PLANNER_INVALID_STATUS,
            PLANNER_METADATA_REASON_FIELD: PLANNER_INVALID_JSON_REASON,
        },
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="I could not select a reliable tool profile.",
        execution_result=ExecutionResult(content="I could not select a reliable tool profile.", task_contract=contract),
    )

    assert result.status == "blocked"
    assert result.reason == TASK_CONTRACT_PLANNER_UNVALIDATED_REASON
    assert result.active_task_detail == PLANNER_INVALID_JSON_REASON


def test_completion_gate_status_helpers_normalize_policy_values():
    assert _is_blocking_planner_status(" INVALID ") is True
    assert _is_blocking_planner_status("ready") is False
    assert is_max_tool_iterations_stop_reason("max_tool_iterations") is True
    assert is_max_tool_iterations_stop_reason("stop") is False
    assert _is_unsuccessful_workflow_status("CANCELLED") is True
    assert _is_unsuccessful_workflow_status("complete") is False


def test_completion_gate_task_type_policy_helpers_are_centralized():
    intent = TaskIntentService().classify("please answer")
    assert _is_read_only_task_type("web_research") is True
    assert _is_read_only_task_type("code_change") is False
    assert _is_plain_answer_task_type("pure_answer") is True
    assert _is_plain_answer_task_type("web_research") is False
    assert _intent_supports_fallback_active_task_update(intent, TaskContract(objective="x", task_type="web_research")) is True
    assert _intent_supports_fallback_active_task_update(intent, TaskContract(objective="x", task_type="pure_answer")) is False
    assert _is_one_turn_intent_kind("command") is True
    assert _is_one_turn_intent_kind("task") is False
    assert _is_analysis_response_intent_kind("analysis") is True
    assert _is_analysis_response_intent_kind("task") is False
    assert _is_generic_task_response_intent_kind("task") is True
    assert _is_generic_task_response_intent_kind("analysis") is False
    assert _is_workflow_completion_intent_kind("review") is True
    assert _is_workflow_completion_intent_kind("task") is False
    assert _accepts_final_response_task_type("planning") is True
    assert _accepts_final_response_task_type("web_research") is False
    assert _is_read_only_blocking_requirement_kind("file_change") is True
    assert _is_read_only_blocking_requirement_kind("tool_group") is False
    assert _is_read_only_blocking_tool_group("execution") is True
    assert _is_read_only_blocking_tool_group("workspace_read") is False
    assert _is_verification_requirement_kind("verification") is True
    assert _is_verification_requirement_kind("tool_group") is False
    assert _is_verification_tool_group("verification") is True
    assert _is_verification_tool_group("workspace_read") is False
    assert _is_verification_result_artifact_kind("verification_result") is True
    assert _is_verification_result_artifact_kind("web_source") is False
    assert _is_verification_tool("verify") is True
    assert _is_verification_tool("web_fetch") is False


def test_completion_gate_workflow_policy_helpers_are_centralized():
    assert _is_review_workflow("implement_then_review") is True
    assert _is_review_workflow("research_then_outline") is False
    assert _is_research_then_outline_workflow("research_then_outline") is True
    assert _is_research_then_outline_workflow("implement_then_review") is False
    assert _is_failed_workflow_status("failed") is True
    assert _is_failed_workflow_status("cancelled") is False
    assert _is_cancelled_workflow_status("cancelled") is True
    assert _is_cancelled_workflow_status("failed") is False
    assert _completion_status_for_unsuccessful_workflow("failed") == "blocked"
    assert _completion_status_for_unsuccessful_workflow("cancelled") == "incomplete"
    assert _workflow_gate_is_complete({"status": "complete"}) is True
    assert _workflow_gate_is_complete({"status": "needs_review"}) is False
    assert _workflow_gate_needs_verification({"status": "needs_verification"}) is True
    assert _workflow_gate_needs_verification({"status": "complete"}) is False
    assert _is_completed_delegated_review_status("completed") is True
    assert _is_completed_delegated_review_status("failed") is False
    assert _is_clean_structured_review_status("ok") is True
    assert _is_clean_structured_review_status("error") is False
    assert _workflow_fix_follow_up_fields("bugfix_then_test_then_review") == {
        "next_step_id": "bugfix",
        "next_step_label": "Bug fix",
        "next_step_prompt_type": "bug-fixer",
    }
    assert _workflow_fix_follow_up_fields("research_then_outline") == {}


def test_completion_gate_review_path_policy_helper_is_centralized():
    assert _path_requires_delegated_review("src/opensprite/runtime.py") is True
    assert _path_requires_delegated_review("package.json") is True
    assert _path_requires_delegated_review("snapshot_after/src/opensprite/app.vue") is True
    assert _path_requires_delegated_review("docs/usage.md") is False
    assert _is_web_app_path("apps/web/src/App.vue") is True
    assert _is_web_app_path("src/opensprite/channels/web.py") is False
    assert _is_python_file_path("src/opensprite/runtime.py") is True
    assert _is_python_file_path("apps/web/src/App.vue") is False
    assert _is_python_test_path("tests/agent/test_completion_gate.py") is True
    assert _is_python_test_path("src/opensprite/runtime.py") is False


def test_completion_gate_web_source_evidence_helpers_are_centralized():
    assert _is_optional_web_discovery_failure_tool("web_search") is True
    assert _is_optional_web_discovery_failure_tool("web_fetch") is False
    assert _is_optional_web_fetch_failure_tool("web_fetch") is True
    assert _is_optional_web_fetch_failure_tool("web_search") is False
    assert is_web_discovery_tool("web_research") is True
    assert is_web_discovery_tool("web_fetch") is False
    assert is_fetched_web_source_artifact_tool("browser_snapshot") is True
    assert is_fetched_web_source_artifact_tool("web_research") is False
    assert is_web_research_source_artifact_tool("web_research") is True
    assert is_web_research_source_artifact_tool("web_fetch") is False
    assert is_web_fetch_source_record_tool("web_fetch") is True
    assert is_web_fetch_source_record_tool("web_search") is False
    assert is_web_source_evidence_tool("web_research") is True
    assert is_web_source_evidence_tool("read_file") is False
    assert is_web_research_task_type("web_research") is True
    assert is_web_research_task_type("workspace_read") is False
    assert is_web_research_tool_group("web_research") is True
    assert is_web_research_tool_group("workspace_read") is False
    assert is_web_source_artifact_kind("web_source") is True
    assert is_web_source_artifact_kind("verification_result") is False
    assert _is_optional_workspace_batch_failure_tool("batch") is True
    assert _is_optional_workspace_batch_failure_tool("read_file") is False
    assert _is_history_retrieval_tool("search_history") is True
    assert _is_history_retrieval_tool("web_search") is False


def _web_research_coverage_gap_artifact() -> TaskArtifact:
    return TaskArtifact(
        kind="web_source",
        source_tool="web_research",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://docs.test/browser",
                    "title": "AI Browser Docs",
                    "snippet": "Official AI browser documentation.",
                    "query": "ai browser",
                    "provider": "duckduckgo",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                    "min_content_chars": 800,
                    "extractor": "trafilatura",
                    "truncated": False,
                },
                {
                    "tool_name": "web_search",
                    "url": "https://pricing.test/browser",
                    "title": "AI Browser Pricing",
                    "snippet": "Pricing search result.",
                    "query": "ai browser pricing",
                    "provider": "duckduckgo",
                },
            ],
            "source_count": 2,
            "coverage": {
                "target_fetch_count": 2,
                "target_met": False,
                "search_result_count": 2,
                "fetched_count": 1,
                "failed_count": 1,
                "too_short_count": 1,
                "blocked_count": 0,
                "missing_url_count": 0,
                "fetched_domains": ["docs.test"],
                "fetched_domain_count": 1,
                "fetched_queries": ["ai browser"],
                "fetched_query_count": 1,
                "queries_with_search_results": ["ai browser", "ai browser pricing"],
                "queries_without_successful_fetch": ["ai browser pricing"],
            },
        },
    )


def _web_research_partial_query_artifact() -> TaskArtifact:
    return TaskArtifact(
        kind="web_source",
        source_tool="web_research",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://docs.test/browser",
                    "title": "AI Browser Docs",
                    "snippet": "Official AI browser documentation.",
                    "query": "ai browser",
                    "provider": "duckduckgo",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                    "min_content_chars": 800,
                },
                {
                    "tool_name": "web_fetch",
                    "url": "https://market.test/browser",
                    "title": "AI Browser Market",
                    "snippet": "Market research for AI browser tools.",
                    "query": "ai browser market",
                    "provider": "duckduckgo",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                    "min_content_chars": 800,
                },
            ],
            "source_count": 2,
            "coverage": {
                "target_fetch_count": 2,
                "target_met": True,
                "search_result_count": 4,
                "fetched_count": 2,
                "failed_count": 1,
                "too_short_count": 0,
                "blocked_count": 0,
                "missing_url_count": 0,
                "fetched_domains": ["docs.test", "market.test"],
                "fetched_domain_count": 2,
                "fetched_queries": ["ai browser", "ai browser market"],
                "fetched_query_count": 2,
                "queries_with_search_results": ["ai browser", "ai browser market", "ai browser pricing"],
                "queries_without_successful_fetch": ["ai browser pricing"],
            },
        },
    )


def _tool_group_contract(intent, task_type: str, tool_group: str) -> TaskContract:
    return TaskContract(
        objective=intent.objective,
        task_type=task_type,
        requirements=(EvidenceRequirement(kind="tool_group", tool_group=tool_group),),
        allow_no_tool_final=False,
        contract_sources=("test",),
    )


def _web_contract(intent) -> TaskContract:
    return _tool_group_contract(intent, "web_research", "web_research")


def _workspace_contract(intent) -> TaskContract:
    return _tool_group_contract(intent, "workspace_read", "workspace_read")


def _history_contract(intent) -> TaskContract:
    return _tool_group_contract(intent, "history_retrieval", "history_retrieval")


def _web_follow_up_context() -> TaskContextDecision:
    return TaskContextDecision(
        is_follow_up=True,
        should_inherit_active_task=True,
        inherited_task_type="web_research",
        inherited_tool_group="web_research",
        continuation_type="narrowing",
        confidence=0.86,
        method="llm",
        reason="LLM context resolver inherited the prior web research task.",
    )


def _verification_contract(intent) -> TaskContract:
    return TaskContract(
        objective=intent.objective,
        task_type="task",
        requirements=(EvidenceRequirement(kind="verification", tool_group="verification"),),
        allow_no_tool_final=False,
        contract_sources=("test",),
    )


def _itemized_contract(intent) -> TaskContract:
    return TaskContract(
        objective=intent.objective,
        task_type="pure_answer",
        acceptance_criteria=(AcceptanceCriterion(kind=ITEMIZED_OUTPUT_CRITERION_KIND, min_count=3, max_response_chars=260),),
        allow_no_tool_final=True,
        contract_sources=("test",),
    )


def test_completion_gate_requires_requested_verification_before_completion():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")
    contract = _verification_contract(intent)

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Completed the refactor.",
        execution_result=ExecutionResult(
            content="Completed the refactor.",
            file_change_count=1,
            touched_paths=("src/agent.py",),
            task_contract=contract,
        ),
    )

    assert result.status == "needs_verification"
    assert result.reason == "required verification was not recorded"
    assert result.should_update_active_task is False
    assert result.verification_action == "python_compile"
    assert result.verification_path == "src"


def test_completion_gate_does_not_run_pytest_for_non_code_test_notes():
    intent = TaskIntentService().classify("Please add a short test session note.")
    contract = _verification_contract(intent)

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Added the note.",
        execution_result=ExecutionResult(
            content="Added the note.",
            file_change_count=1,
            touched_paths=("repo/tests/search/SESSION_TOOL_TEST_NOTES.md",),
            task_contract=contract,
        ),
    )

    assert result.status == "needs_verification"
    assert result.verification_action == "auto"
    assert result.verification_path == "repo/tests/search"
    assert result.verification_pytest_args == ()


def test_completion_gate_accepts_reported_skipped_verification_for_note_change():
    intent = TaskIntentService().classify("Please add a short test session note.")
    contract = _verification_contract(intent)

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Completed. Added the note. Verification was recorded as skipped by the verify tool metadata.",
        execution_result=ExecutionResult(
            content="Added the note.",
            file_change_count=1,
            touched_paths=("flow-note.md",),
            verification_attempted=True,
            verification_passed=False,
            task_artifacts=(
                TaskArtifact(
                    kind="verification_result",
                    source_tool="verify",
                    content_preview="No supported Python or package.json build checks were detected.",
                    ok=True,
                    metadata={VERIFICATION_STATUS_METADATA_FIELD: "skipped"},
                ),
            ),
            task_contract=contract,
        ),
    )

    assert result.status == "complete"
    assert result.verification_required is True
    assert result.verification_attempted is True
    assert result.verification_passed is True
    assert result.review_required is False


def test_completion_gate_accepts_skipped_verification_for_non_code_note_change():
    intent = TaskIntentService().classify("Please add a short test session note.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Completed. Added the note.",
        execution_result=ExecutionResult(
            content="Added the note.",
            file_change_count=1,
            touched_paths=("flow-note.md",),
            verification_attempted=True,
            verification_passed=False,
            task_artifacts=(
                TaskArtifact(
                    kind="verification_result",
                    source_tool="verify",
                    content_preview="No supported Python or package.json build checks were detected.",
                    ok=True,
                    metadata={VERIFICATION_STATUS_METADATA_FIELD: "skipped"},
                ),
            ),
        ),
    )

    assert result.status == "complete"
    assert result.verification_passed is True
    assert result.review_required is False


def test_completion_gate_uses_skipped_verification_artifact_without_response_marker():
    intent = TaskIntentService().classify("Please update src/app.py and verify the change.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Updated src/app.py.",
        execution_result=ExecutionResult(
            content="Updated src/app.py.",
            file_change_count=1,
            touched_paths=("src/app.py",),
            verification_attempted=True,
            verification_passed=False,
            task_artifacts=(
                TaskArtifact(
                    kind="verification_result",
                    source_tool="verify",
                    content_preview="No supported Python or package.json build checks were detected.",
                    ok=True,
                    metadata={VERIFICATION_STATUS_METADATA_FIELD: "skipped"},
                ),
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.verification_passed is True


def test_completion_gate_accepts_successful_non_code_verification_without_artifact():
    intent = TaskIntentService().classify("Please add a short test session note.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Completed. Added the note and confirmed the content.",
        execution_result=ExecutionResult(
            content="Added the note.",
            file_change_count=1,
            touched_paths=("flow-note.md",),
            verification_attempted=True,
            verification_passed=False,
            had_tool_error=False,
        ),
    )

    assert result.status == "complete"
    assert result.verification_passed is True
    assert result.review_required is False


def test_completion_gate_uses_project_relative_pytest_args_for_repo_snapshot_tests():
    intent = TaskIntentService().classify("Please update the test and run tests.")
    contract = _verification_contract(intent)

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Updated the test.",
        execution_result=ExecutionResult(
            content="Updated the test.",
            file_change_count=1,
            touched_paths=("repo/tests/agent/test_sample.py",),
            task_contract=contract,
        ),
    )

    assert result.status == "needs_verification"
    assert result.verification_action == "pytest"
    assert result.verification_pytest_args == ("tests/agent/test_sample.py",)


def test_completion_gate_does_not_require_verification_for_read_only_workspace_analysis():
    intent = TaskIntent(
        kind="analysis",
        objective="Evaluate the current test coverage gaps.",
        expects_verification=True,
    )
    contract = TaskContract(
        objective=intent.objective,
        task_type="workspace_read",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="workspace_read"),),
        acceptance_criteria=(AcceptanceCriterion(kind=SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND, min_response_chars=40),),
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="I inspected the related files and found missing multi-turn trace coverage.",
        execution_result=ExecutionResult(
            content="I inspected the related files and found missing multi-turn trace coverage.",
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="read_file", ok=True, result_preview="trace coverage notes"),),
            task_contract=contract,
        ),
    )

    assert result.status == "complete"


def test_completion_gate_allows_read_only_batch_discovery_miss_after_workspace_evidence():
    intent = TaskIntent(
        kind="analysis",
        objective="Evaluate the current test coverage gaps.",
        expects_verification=True,
    )
    contract = TaskContract(
        objective=intent.objective,
        task_type="analysis",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="workspace_read"),),
        acceptance_criteria=(AcceptanceCriterion(kind=SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND, min_response_chars=40),),
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="I inspected the related files and found missing multi-turn trace coverage.",
        execution_result=ExecutionResult(
            content="I inspected the related files and found missing multi-turn trace coverage.",
            executed_tool_calls=3,
            had_tool_error=True,
            tool_evidence=(
                ToolEvidence(
                    name="batch",
                    ok=False,
                    result_preview=json.dumps(
                        {
                            "type": "batch",
                            "ok": False,
                            "summary": "Batch completed: 4 call(s), 1 failed.",
                            "total": 4,
                            "failed": 1,
                            "error": "Batch completed: 4 call(s), 1 failed.",
                            "error_type": "ToolFailure",
                            "category": "batch_failure",
                            "results": [],
                        }
                    ),
                ),
                ToolEvidence(name="grep_files", ok=True, result_preview="Found matching trace code."),
                ToolEvidence(name="read_file", ok=True, result_preview="trace coverage notes"),
            ),
            task_contract=contract,
        ),
    )

    assert result.status == "complete"


def test_completion_gate_accepts_run_file_change_listing_as_read_only_evidence():
    intent = TaskIntent(
        kind="analysis",
        objective="List what changed in this conversation.",
        expects_code_change=False,
    )
    contract = TaskContract(
        objective=intent.objective,
        task_type="workspace_read",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="workspace_read"),),
        acceptance_criteria=(AcceptanceCriterion(kind=SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND, min_response_chars=20),),
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="There is one changed file recorded in the current session.",
        execution_result=ExecutionResult(
            content="There is one changed file recorded in the current session.",
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(name="list_run_file_changes", ok=True, result_preview='{"count": 1}'),
            ),
            task_contract=contract,
        ),
    )

    assert result.status == "complete"


def test_completion_gate_accepts_run_file_change_listing_as_history_evidence():
    intent = TaskIntent(
        kind="analysis",
        objective="Does this session have file changes based on trace data?",
        expects_code_change=False,
    )
    contract = TaskContract(
        objective=intent.objective,
        task_type="history_retrieval",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="history_retrieval"),),
        acceptance_criteria=(AcceptanceCriterion(kind=SUBSTANTIVE_FINAL_ANSWER_CRITERION_KIND, min_response_chars=40),),
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=(
            "No. The current session trace shows list_run_file_changes scanned the recent runs "
            "and reported count 0, so there are no file changes visible in trace."
        ),
        execution_result=ExecutionResult(
            content="No file changes were recorded in trace.",
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="list_run_file_changes", ok=True, result_preview='{"count": 0}'),),
            task_contract=contract,
        ),
    )

    assert result.status == "complete"


def test_completion_gate_does_not_require_verification_for_operations_report():
    intent = TaskIntentService().classify("Remind me tomorrow to check the test report.")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="scheduling"),),
        acceptance_criteria=(AcceptanceCriterion(kind=OPERATION_REPORT_CRITERION_KIND),),
        allow_no_tool_final=False,
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Scheduled the reminder successfully. No rollback is needed; residual risk is low.",
        execution_result=ExecutionResult(
            content="Scheduled the reminder successfully. No rollback is needed; residual risk is low.",
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="cron", ok=True),),
            task_contract=contract,
        ),
    )

    assert result.status == "complete"
    assert result.verification_required is False


def test_completion_gate_rejects_repo_state_answer_for_command_version_question():
    intent = TaskIntentService().classify("Confirm the current git version. Answer only the version number.")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="execution"),),
        acceptance_criteria=(AcceptanceCriterion(kind=OPERATION_REPORT_CRITERION_KIND),),
        planner_metadata={"quality_checks": [COMMAND_VERSION_QUALITY_CHECK]},
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Unable to answer because this repo is not a git repository and has no .git directory.",
        execution_result=ExecutionResult(
            content="Unable to answer because this repo is not a git repository and has no .git directory.",
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(
                    name="exec",
                    ok=True,
                    result_preview="abc123def456",
                    metadata={"tool_args": {"command": "git rev-parse HEAD"}},
                ),
            ),
            task_contract=contract,
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "command version answer did not report a version"
    assert "<command> --version" in (result.active_task_detail or "")


def test_completion_gate_accepts_shortened_command_version_from_tool_result():
    intent = TaskIntentService().classify("Confirm the current git version. Answer only the version number.")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="execution"),),
        acceptance_criteria=(AcceptanceCriterion(kind=OPERATION_REPORT_CRITERION_KIND),),
        planner_metadata={"quality_checks": [COMMAND_VERSION_QUALITY_CHECK]},
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="2.47.1",
        execution_result=ExecutionResult(
            content="2.47.1",
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(
                    name="exec",
                    ok=True,
                    result_preview="git version 2.47.1.windows.2",
                    metadata={"tool_args": {"command": "git --version"}},
                ),
            ),
            task_contract=contract,
        ),
    )

    assert result.status == "complete"


def test_completion_gate_rejects_ungrounded_command_version_number():
    intent = TaskIntentService().classify("Confirm the current git version. Answer only the version number.")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="execution"),),
        acceptance_criteria=(AcceptanceCriterion(kind=OPERATION_REPORT_CRITERION_KIND),),
        planner_metadata={"quality_checks": [COMMAND_VERSION_QUALITY_CHECK]},
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="9.9.9",
        execution_result=ExecutionResult(
            content="9.9.9",
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(
                    name="exec",
                    ok=True,
                    result_preview="git version 2.47.1.windows.2",
                    metadata={"tool_args": {"command": "git --version"}},
                ),
            ),
            task_contract=contract,
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "command version answer did not report a version"


def test_completion_gate_rejects_command_unavailable_claim_without_execution_evidence():
    intent = TaskIntentService().classify("Confirm the current missingtool version. Answer only the version number.")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="execution"),),
        acceptance_criteria=(AcceptanceCriterion(kind=OPERATION_REPORT_CRITERION_KIND),),
        planner_metadata={"quality_checks": [COMMAND_VERSION_QUALITY_CHECK]},
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="missingtool is not installed.",
        execution_result=ExecutionResult(
            content="missingtool is not installed.",
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="exec", ok=True, result_preview="git version 2.47.1.windows.2"),),
            task_contract=contract,
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "command version answer did not report a version"


def test_completion_gate_accepts_command_unavailable_from_failed_execution_evidence():
    intent = TaskIntentService().classify("Confirm the current missingtool version. Answer only the version number.")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        planner_metadata={"quality_checks": [COMMAND_VERSION_QUALITY_CHECK]},
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="missingtool is not installed.",
        execution_result=ExecutionResult(
            content="missingtool is not installed.",
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="exec", ok=False, result_preview="exit code 127"),),
            task_contract=contract,
        ),
    )

    assert result.status == "complete"


def test_quality_gate_does_not_infer_command_version_check_from_objective_text():
    intent = TaskIntentService().classify("Confirm the current git version. Answer only the version number.")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="execution"),),
    )

    result = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Unable to answer because this repo is not a git repository and has no .git directory.",
        execution_result=ExecutionResult(
            content="Unable to answer because this repo is not a git repository and has no .git directory.",
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(
                    name="exec",
                    ok=True,
                    result_preview="fatal: not a git repository (or any of the parent directories): .git",
                    metadata={"tool_args": {"command": "git rev-parse HEAD"}},
                ),
            ),
            task_contract=contract,
        ),
        task_contract=contract,
    )

    assert result.passed is True


def test_completion_gate_treats_max_tool_iterations_as_incomplete():
    intent = TaskIntentService().classify("Please implement the cleanup and run tests.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="I hit the iteration limit before finishing.",
        execution_result=ExecutionResult(
            content="I hit the iteration limit before finishing.",
            executed_tool_calls=1,
            file_change_count=1,
            stop_reason="max_tool_iterations",
            stop_metadata={"iteration_limit": 1},
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "max tool iterations exhausted before completion"
    assert "max_tool_iterations" in (result.active_task_detail or "")


def test_completion_gate_prefers_web_build_for_web_changes():
    intent = TaskIntentService().classify("Please update the web UI and verify the change.")
    contract = _verification_contract(intent)

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Updated the web UI.",
        execution_result=ExecutionResult(
            content="Updated the web UI.",
            file_change_count=1,
            touched_paths=("apps/web/src/App.vue",),
            task_contract=contract,
        ),
    )

    assert result.status == "needs_verification"
    assert result.verification_action == "web_build"
    assert result.verification_path == "apps/web"


def test_completion_gate_keeps_verification_status_when_verify_fails_with_tool_error():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")
    contract = _verification_contract(intent)

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Verification failed.",
        execution_result=ExecutionResult(
            content="Verification failed.",
            file_change_count=1,
            touched_paths=("src/agent.py",),
            verification_attempted=True,
            verification_passed=False,
            had_tool_error=True,
            task_contract=contract,
        ),
    )

    assert result.status == "needs_verification"
    assert result.reason == "required verification did not pass"
    assert result.verification_action == "python_compile"
    assert result.verification_path == "src"


def test_completion_gate_does_not_infer_blocker_from_tool_error_text():
    intent = TaskIntentService().classify("繼續驗證")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="目前無法繼續，測試環境失敗。",
        execution_result=ExecutionResult(
            content="目前無法繼續，測試環境失敗。",
            executed_tool_calls=1,
            had_tool_error=True,
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "tool execution reported an error without a clear blocker handoff"
    assert result.active_task_status is None
    assert result.should_update_active_task is False


def test_completion_gate_does_not_infer_chinese_missing_files_as_blocked():
    intent = TaskIntentService().classify(
        "\u8acb\u53ea\u8b80\u5fc5\u8981\u7684\u5c08\u6848\u6a94\u6848\uff0c\u627e\u51fa harness profile selection \u554f\u984c"
    )
    response = "\u7d50\u679c\uff1a\u6240\u9700\u6a94\u6848\u4e0d\u5b58\u5728\uff0c\u7121\u6cd5\u7e7c\u7e8c\u3002"

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(
            content=response,
            executed_tool_calls=1,
            had_tool_error=True,
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "tool execution reported an error without a clear blocker handoff"
    assert result.active_task_status is None


def test_completion_gate_does_not_infer_workspace_scope_read_error_as_blocked():
    intent = TaskIntentService().classify("請看目前工作區，找出 AGENTS.md 裡面跟 verification 有關的重點")
    response = (
        "根據工作區 runtime 環境的限制，我無法直接讀取 bootstrap 資料夾下的 `AGENTS.md`，"
        "因為該路徑不在工作區範圍內。"
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(
            content=response,
            executed_tool_calls=1,
            had_tool_error=True,
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "tool execution reported an error without a clear blocker handoff"
    assert result.active_task_status is None
    assert result.should_update_active_task is False


def test_completion_gate_does_not_infer_blocker_heading_as_blocked():
    intent = TaskIntentService().classify(
        "\u8acb\u53ea\u8b80\u5fc5\u8981\u7684\u5c08\u6848\u6a94\u6848\uff0c\u627e\u51fa harness profile selection \u554f\u984c"
    )
    response = "## Blocker\n\nRequired project files were not found."

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(
            content=response,
            executed_tool_calls=1,
            had_tool_error=True,
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "tool execution reported an error without a clear blocker handoff"
    assert result.active_task_status is None


def test_completion_gate_does_not_mark_preview_only_revert_as_blocked():
    intent = TaskIntentService().classify("Preview what would be reverted for flow-note.md; do not apply it.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=(
            "## Revert preview: flow-note.md\n\n"
            "If applied, this would delete the file because it was newly added. "
            "This is a read-only preview and has not been applied."
        ),
        execution_result=ExecutionResult(
            content="preview",
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="list_run_file_changes", ok=True),),
            task_contract=TaskContract(
                objective=intent.objective,
                task_type="workspace_read",
                requirements=(EvidenceRequirement(kind="tool_group", tool_group="workspace_read"),),
                acceptance_criteria=(AcceptanceCriterion(kind="substantive_final_answer", min_response_chars=80),),
            ),
        ),
    )

    assert result.status == "complete"


def test_completion_gate_does_not_infer_strong_chinese_blocker_without_tool_error():
    intent = TaskIntentService().classify(
        "\u5ef6\u7e8c\u525b\u525b\u7684\u7a0b\u5f0f\u78bc\u89c0\u5bdf\uff0c\u8acb\u8a2d\u8a08\u6700\u5c0f regression test \u6848\u4f8b\uff1b\u4e0d\u8981\u4fee\u6539\u6a94\u6848\u3001\u4e0d\u8981\u57f7\u884c\u6e2c\u8a66\u3002"
    )
    response = "\u5b8c\u6210\u72c0\u614b\uff1a\u5df2\u660e\u78ba\u963b\u64cb\uff0c\u7121\u6cd5\u7e7c\u7e8c\u3002"

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert result.status == "complete"
    assert result.active_task_status is None
    assert result.should_update_active_task is False


def test_completion_gate_does_not_mark_status_definition_as_blocked():
    intent = TaskIntentService().classify(
        "\u4e0d\u8981\u8b80\u6a94\u4e0d\u8981\u4e0a\u7db2\uff0c\u8acb\u7528\u5169\u53e5\u8a71\u89e3\u91cb blocked \u548c incomplete \u7684\u5dee\u7570\u3002"
    )
    response = (
        "- **Blocked**\uff1a\u4efb\u52d9\u56e0\u5916\u90e8\u4f9d\u8cf4\u3001\u7f3a\u5c11\u8cc7\u8a0a\u6216\u6b0a\u9650\u9650\u5236\u800c\u7121\u6cd5\u7e7c\u7e8c\u63a8\u9032\u3002\n"
        "- **Incomplete**\uff1a\u4efb\u52d9\u5c1a\u672a\u5b8c\u6210\uff0c\u53ea\u8868\u793a\u76ee\u6a19\u9084\u6c92\u6709\u9054\u6210\u3002"
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert result.status == "complete"
    assert result.reason == "generic task returned a response"


@pytest.mark.anyio
async def test_completion_gate_marks_retry_only_conversation_response_incomplete():
    intent = TaskIntentService().classify(
        "\u6839\u64da\u525b\u525b\u8b80\u5230\u7684\u5167\u5bb9\uff0c\u7528\u4e00\u53e5\u8a71\u8aaa\u660e API key header\uff0c\u4e0d\u8981\u518d\u4e0a\u7db2\u3002"
    )
    response = "\u62b1\u6b49\uff0c\u6211\u525b\u525b\u6c92\u6709\u7522\u751f\u53ef\u986f\u793a\u7684\u56de\u8986\uff0c\u8acb\u518d\u8a66\u4e00\u6b21\u3002"

    result = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected retry-only response",
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert result.status == "incomplete"
    assert result.reason == "judge rejected retry-only response"


@pytest.mark.anyio
async def test_completion_gate_uses_judge_for_blocked_status():
    intent = TaskIntentService().classify("繼續驗證")
    response = "目前無法繼續，測試環境失敗。"

    result = await _evaluate_with_static_judge(
        status="blocked",
        reason="judge identified an external blocker",
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response, had_tool_error=True),
    )

    assert result.status == "blocked"
    assert result.reason == "judge identified an external blocker"


@pytest.mark.anyio
async def test_completion_gate_uses_judge_for_waiting_user_status():
    intent = TaskIntentService().classify("繼續做")
    response = "請問你要用哪個 target branch？"

    result = await _evaluate_with_static_judge(
        status="waiting_user",
        reason="judge identified required user input",
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert result.status == "waiting_user"
    assert result.reason == "judge identified required user input"


def test_completion_gate_does_not_infer_waiting_from_response_question():
    intent = TaskIntentService().classify("What should I do next?")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="請問你要用哪個 target branch？",
        execution_result=ExecutionResult(content="請問你要用哪個 target branch？"),
    )

    assert result.status == "complete"
    assert result.active_task_status is None
    assert result.should_update_active_task is False


def test_completion_gate_completes_generic_task_with_substantive_answer():
    intent = TaskIntentService().classify("請用三句話介紹 OpenSprite，不要讀檔也不要上網。")
    answer = "OpenSprite 是一個本機 AI 助手。它可以協助處理對話、工具與任務流程。它重視 trace 與可驗證結果。"

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(content=answer),
    )

    assert result.status == "complete"
    assert result.reason == "generic task returned a response"


def test_completion_gate_completes_debug_answer_even_when_it_contains_questions():
    intent = TaskIntentService().classify("請幫我 debug Python ModuleNotFoundError 的常見原因，不要讀檔、不要上網。")
    answer = "常見原因包括套件未安裝、Python 環境不一致，以及模組路徑錯誤。可以先檢查 which python? 再確認 pip 安裝位置。"

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(content=answer),
    )

    assert result.status == "complete"
    assert result.reason == "generic task returned a response"


def test_completion_gate_requires_web_evidence_for_external_search_task():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = _web_contract(intent)

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="正在幫你搜尋 Reddit 搜尋相關的開源專案...",
        execution_result=ExecutionResult(content="正在幫你搜尋 Reddit 搜尋相關的開源專案...", task_contract=contract),
    )

    assert result.status == "incomplete"
    assert result.reason == "required task evidence was not produced"
    assert result.missing_evidence

def test_evidence_gate_reports_missing_contract_items():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = _web_contract(intent)

    result = EvidenceGateService().evaluate(
        task_intent=intent,
        execution_result=ExecutionResult(content="我會搜尋 Reddit。", task_contract=contract),
        verification_passed=False,
    )

    assert result.passed is False
    assert result.reason == "required task evidence was not produced"
    assert result.missing_evidence

def test_task_contract_records_web_source_acceptance_criteria():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    kinds = [criterion.kind for criterion in contract.acceptance_criteria]
    assert "source_artifact" in kinds
    assert "source_detail" in kinds
    assert "substantive_final_answer" in kinds
    assert "source_reference" in kinds
    criteria = {criterion.kind: criterion for criterion in contract.acceptance_criteria}
    assert criteria["source_artifact"].min_count == 2
    assert criteria["source_detail"].min_count == 1


def test_task_contract_requires_web_research_for_chinese_market_lookup():
    intent = TaskIntentService().classify("幫我找 2330市值")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    assert contract.task_type == "web_research"
    assert contract.allow_no_tool_final is False
    assert any(requirement.tool_group == "web_research" for requirement in contract.requirements)
    assert {criterion.kind for criterion in contract.acceptance_criteria} >= {
        "source_artifact",
        "source_detail",
        "substantive_final_answer",
        "source_reference",
    }


def test_task_contract_requires_web_research_for_high_confidence_chinese_external_lookups():
    examples = [
        "今天台北天氣",
        "美元台幣匯率",
        "00981T 即時報價",
        "幫我搜尋 NVIDIA 新聞",
        "請上網找 OpenAI 來源連結",
    ]

    for message in examples:
        intent = TaskIntentService().classify(message)
        contract = TaskContractService.build(
            task_intent=intent,
            current_message=intent.objective,
        )

        assert contract.task_type == "web_research", message
        assert contract.allow_no_tool_final is False, message
        assert any(requirement.tool_group == "web_research" for requirement in contract.requirements), message


def test_task_contract_does_not_treat_ambiguous_chinese_lookup_words_as_web_research():
    examples = [
        "查一下這個檔案",
        "查詢設定",
        "整理最新進度",
        "搜尋目前專案裡的 TODO",
        "搜索目前專案裡的 TODO",
        "查找剛剛提到的內容",
        "search the repo for TODO",
    ]

    for message in examples:
        intent = TaskIntentService().classify(message)
        contract = TaskContractService.build_deterministic(
            task_intent=intent,
            current_message=intent.objective,
        )

        assert contract.task_type != "web_research", message
        assert not any(requirement.tool_group == "web_research" for requirement in contract.requirements), message


def test_task_contract_requires_workspace_read_for_direct_repo_lookup():
    intent = TaskIntentService().classify("search the repo for auth config")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    assert contract.task_type == "workspace_read"
    assert contract.allow_no_tool_final is False
    assert any(requirement.tool_group == "workspace_read" for requirement in contract.requirements)
    assert any(criterion.kind == "substantive_final_answer" for criterion in contract.acceptance_criteria)


def test_completion_gate_requires_workspace_evidence_for_direct_repo_lookup():
    intent = TaskIntentService().classify("search the repo for auth config")
    contract = _workspace_contract(intent)

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="I will inspect the repo for auth config.",
        execution_result=ExecutionResult(content="I will inspect the repo for auth config.", task_contract=contract),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert completion.missing_evidence

def test_completion_gate_rejects_terse_workspace_answer_after_reading():
    intent = TaskIntentService().classify("請看 src/opensprite/agent/task_contract.py")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="看過了。",
        execution_result=ExecutionResult(
            content="看過了。",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="read_file", ok=True),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == TERSE_FINAL_ANSWER_REASON


def test_completion_gate_requires_workspace_answer_to_reference_requested_path():
    intent = TaskIntentService().classify("請看 src/opensprite/agent/task_contract.py")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我看過這段邏輯了。它會先建立 deterministic contract，再依 task type 補 evidence requirement，"
        "最後把 acceptance criteria 一起帶進 completion gate。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="read_file", ok=True),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == WORKSPACE_CONTEXT_REFERENCE_MISSING_REASON


def test_completion_gate_completes_workspace_read_with_evidence_and_substantive_answer():
    intent = TaskIntentService().classify("請看 src/opensprite/agent/task_contract.py")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我看過 src/opensprite/agent/task_contract.py 了。這段邏輯會先建立 deterministic contract，"
        "再依 task type 補 evidence requirement，最後把 acceptance criteria 一起帶進 completion gate。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="read_file", ok=True),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_requires_workspace_location_for_where_question():
    intent = TaskIntentService().classify("auth config 在哪")
    contract = _workspace_contract(intent)
    contract = replace(
        contract,
        acceptance_criteria=contract.acceptance_criteria
        + (
            AcceptanceCriterion(
                kind=WORKSPACE_LOCATION_CRITERION_KIND,
                description="Identify the relevant workspace location.",
            ),
        ),
    )
    answer = "我查到 auth config 是在設定載入流程中處理，主要由設定服務負責解析與套用。"

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="grep_files", ok=True),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == WORKSPACE_LOCATION_MISSING_REASON

def test_completion_gate_does_not_infer_workspace_location_from_objective_text():
    intent = TaskIntentService().classify("auth config 在哪")
    contract = _workspace_contract(intent)
    answer = (
        "我查到 auth config 是在設定載入流程中處理，主要由設定服務負責解析與套用，"
        "並且這次回答使用了 workspace inspection 的結果，而不是只做一般推測。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="grep_files", ok=True),),
        ),
    )

    assert completion.status == "complete"

def test_completion_gate_completes_workspace_location_answer_with_path():
    intent = TaskIntentService().classify("auth config 在哪")
    contract = _workspace_contract(intent)
    contract = replace(
        contract,
        acceptance_criteria=contract.acceptance_criteria
        + (
            AcceptanceCriterion(
                kind=WORKSPACE_LOCATION_CRITERION_KIND,
                description="Identify the relevant workspace location.",
            ),
        ),
    )
    answer = (
        "我查到 auth config 相關邏輯在 src/opensprite/config/schema.py，"
        "其中 Config 與 provider settings 會處理認證與模型設定的載入。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="grep_files", ok=True),),
        ),
    )

    assert completion.status == "complete"

def test_task_contract_requires_history_retrieval_for_prior_context_lookup():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    assert contract.task_type == "history_retrieval"
    assert contract.allow_no_tool_final is False
    assert any(requirement.tool_group == "history_retrieval" for requirement in contract.requirements)
    assert any(criterion.kind == "substantive_final_answer" for criterion in contract.acceptance_criteria)


def test_task_contract_keeps_history_retrieval_when_planner_requires_it_for_follow_up():
    intent = TaskIntentService().classify("延續上一題，請用一句話說明你剛剛用了哪些來源類型，不要重新查。")

    contract = _contract_from_task_planner_payload(
        {
            "task_type": "history_retrieval",
            "required_tool_groups": ["history_retrieval"],
            "final_answer_required": True,
            "allow_no_tool_final": False,
            "reason": "planner thought prior conversation state needed retrieval",
        },
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "幫我查一下台積電目前股價，請列出來源網址。"},
            {
                "role": "assistant",
                "content": "我使用 Yahoo 股市與台積電投資人網站等來源，來源網址包含 https://tw.stock.yahoo.com/quote/2330.TW",
            },
        ],
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=None,
    )

    assert contract.task_type == "history_retrieval"
    assert any(requirement.tool_group == "history_retrieval" for requirement in contract.requirements)
    assert contract.allow_no_tool_final is False
    assert "override_reason" not in contract.planner_metadata


def test_task_contract_history_retrieval_drops_extra_web_research_group():
    intent = TaskIntentService().classify(
        "Which two previous questions in this session were about OpenRouter or TSMC?"
    )

    contract = _contract_from_task_planner_payload(
        {
            "task_type": "history_retrieval",
            "required_tool_groups": ["history_retrieval", "web_research"],
            "final_answer_required": True,
            "allow_no_tool_final": False,
            "reason": "The user asks about previous conversation state; OpenRouter and TSMC are only history keywords.",
        },
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[],
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=None,
    )

    assert contract.task_type == "history_retrieval"
    assert [requirement.tool_group for requirement in contract.requirements] == ["history_retrieval"]
    assert all(criterion.kind != "source_artifact" for criterion in contract.acceptance_criteria)


def test_completion_gate_requires_history_evidence_for_prior_context_lookup():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")
    contract = _history_contract(intent)

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="我先回頭查一下剛剛的內容。",
        execution_result=ExecutionResult(content="我先回頭查一下剛剛的內容。", task_contract=contract),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert completion.missing_evidence

def test_completion_gate_rejects_terse_history_answer_after_retrieval():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="找到了。",
        execution_result=ExecutionResult(
            content="找到了。",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == TERSE_FINAL_ANSWER_REASON


def test_completion_gate_completes_history_retrieval_with_evidence_and_answer():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我回頭查過前面的內容了。剛剛提到的三個方案是：\n"
        "1. 先收斂 deterministic regex。\n"
        "2. 再補 planner contract 的安全合併。\n"
        "3. 最後把 trace observability 顯示補齊。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True),),
        ),
    )

    assert completion.status == "complete", (contract.task_type, completion.reason, completion.active_task_detail)


def test_completion_gate_allows_partial_history_search_errors_after_successful_history():
    intent = TaskIntentService().classify(
        "Which two previous questions in this session were about OpenRouter or TSMC?"
    )
    contract = TaskContract(
        objective=intent.objective,
        task_type="history_retrieval",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="history_retrieval"),),
        acceptance_criteria=(AcceptanceCriterion(kind="substantive_final_answer", min_response_chars=80),),
    )
    answer = (
        "Based on the retrieved prior chat context, the two relevant questions were: "
        "1. OpenRouter API base URL, and 2. TSMC stock price or recent quote."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=3,
            had_tool_error=True,
            tool_evidence=(
                ToolEvidence(name="search_history", ok=True, metadata={"result_count": 2}),
                ToolEvidence(name="search_history", ok=False, metadata={"error": "history result serialization failed"}),
            ),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_accepts_chinese_history_grounding_phrase():
    intent = TaskIntentService().classify("請幫我檢查目前這段對話前面我問過什麼，列出兩點。")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "根據對話記錄，你前面問過：\n"
        "1. 你要求使用 web_research 搜尋 2026 AI agent tools market trends。\n"
        "2. 你要求整理成 CEO 可以看的五行摘要。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 2}),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_accepts_chinese_current_conversation_phrase():
    intent = TaskIntentService().classify("請幫我檢查目前這段對話前面我問過什麼，列出兩點。")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "根據這段對話的內容，我觀察到你問過：\n"
        "1. 你要求我不要讀檔不要上網，只回答 pong。\n"
        "2. 你要求我使用 web_search 搜尋 OpenRouter Authorization header。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 2}),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_accepts_chinese_previous_search_result_phrase():
    intent = TaskIntentService().classify("根據剛才的搜尋結果，用三句話說明 Authorization header 格式。")
    contract = _history_contract(intent)
    answer = (
        "根據剛才的搜尋結果，Authorization header 使用 Bearer token 格式。\n"
        "1. 格式是 `Authorization: Bearer YOUR_API_KEY`。\n"
        "2. Bearer 和 API key 中間要有空格。\n"
        "3. 每個 API request 都要帶這個 header。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 1}),),
        ),
    )

    assert completion.status == "complete"


@pytest.mark.anyio
async def test_completion_gate_uses_judge_for_ungrounded_history_answer():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "三個方案是：\n"
        "1. 收斂 deterministic regex。\n"
        "2. 補 planner contract。\n"
        "3. 補 trace observability。"
    )

    completion = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected history answer without retrieved context grounding",
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 2}),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "judge rejected history answer without retrieved context grounding"


def test_completion_gate_requires_enough_history_items():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")
    contract = TaskContract(
        objective=intent.objective,
        task_type="history_retrieval",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="history_retrieval"),),
        acceptance_criteria=(
            AcceptanceCriterion(kind="substantive_final_answer", min_response_chars=80),
            AcceptanceCriterion(kind=ITEMIZED_OUTPUT_CRITERION_KIND, min_count=3),
        ),
    )
    answer = (
        "我回頭查過前面的內容了，但目前只整理出兩個方案，還缺少第三個：\n"
        "1. 收斂 deterministic regex，避免模糊查詢直接被硬判成 web。\n"
        "2. 補 planner contract，讓不明確的請求可加上更嚴格 evidence。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 2}),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == HISTORY_RECALLED_ITEMS_INSUFFICIENT_REASON


def test_completion_gate_does_not_infer_history_count_from_objective_text():
    intent = TaskIntentService().classify(
        "\u8acb\u56de\u60f3\u524d\u9762\u63d0\u904e\u54ea\u4e09\u500b\u65b9\u6848"
    )
    contract = TaskContract(
        objective=intent.objective,
        task_type="history_retrieval",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="history_retrieval"),),
        acceptance_criteria=(AcceptanceCriterion(kind="substantive_final_answer", min_response_chars=80),),
    )
    answer = (
        "Based on the retrieved prior conversation, I found two concrete items in context and will avoid "
        "inventing a third item that was not actually retrieved:\n"
        "1. Consolidate completion decisions around the task contract.\n"
        "2. Keep trace evidence tied to real tool results."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 2}),),
        ),
    )

    assert completion.status == "complete"


@pytest.mark.anyio
async def test_completion_gate_uses_judge_for_answer_after_empty_history_retrieval():
    intent = TaskIntentService().classify("前面說過的 threshold 是多少")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "前面說過 threshold 是 0.7，這是 planner contract 的預設信心門檻，"
        "因此我會直接用這個數值作為目前設定的答案。"
    )

    completion = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected invented answer after empty history retrieval",
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 0}),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "judge rejected invented answer after empty history retrieval"


def test_completion_gate_allows_not_found_answer_after_empty_history_retrieval():
    intent = TaskIntentService().classify("前面說過的 threshold 是多少")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我查過前面的內容，但 not found matching prior threshold 討論，"
        "所以不能可靠回答數值；需要的話我可以再用目前設定檔重新查一次。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 0}),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_does_not_treat_history_as_empty_when_any_result_metadata_has_hits():
    intent = TaskIntentService().classify("What did we decide earlier about deployment?")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = "We previously decided to keep deployment manual until the health check is stable."

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(
                ToolEvidence(name="search_history", ok=True, metadata={"result_count": 0}),
                ToolEvidence(name="search_history", ok=True, metadata={"result_count": 1}),
            ),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_does_not_infer_empty_history_from_preview_text():
    intent = TaskIntentService().classify("What did we decide earlier about deployment?")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = "I did not find structured history metadata, so I cannot reliably report a prior deployment decision."

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, result_preview="no results"),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_requires_web_evidence_for_chinese_market_lookup():
    intent = TaskIntentService().classify("查一下 2330市值")
    contract = TaskContract(
        objective=intent.objective,
        task_type="web_research",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="web_research"),),
        allow_no_tool_final=False,
        contract_sources=("test",),
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="讓我查一下最新市值：",
        execution_result=ExecutionResult(content="讓我查一下最新市值：", task_contract=contract),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert completion.missing_evidence

def test_task_contract_inherits_web_research_for_short_follow_up():
    intent = TaskIntentService().classify("那00981t呢")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        task_context_decision=_web_follow_up_context(),
        history=[
            {"role": "user", "content": "幫我查 00980A 這檔 ETF 的股價和基本資料"},
            {"role": "assistant", "content": "我查到 00980A 的公開資訊來源。"},
        ],
    )

    assert contract.task_type == "web_research"
    assert contract.allow_no_tool_final is False
    assert any(requirement.tool_group == "web_research" for requirement in contract.requirements)
    assert {criterion.kind for criterion in contract.acceptance_criteria} >= {
        "source_artifact",
        "source_detail",
        "substantive_final_answer",
        "source_reference",
    }


def test_completion_gate_requires_web_evidence_for_short_follow_up():
    intent = TaskIntentService().classify("那00981t呢")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        task_context_decision=_web_follow_up_context(),
        history=[
            {"role": "user", "content": "幫我查 00980A 這檔 ETF 的股價和基本資料"},
            {"role": "assistant", "content": "我查到 00980A 的公開資訊來源。"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="## 查詢 00981T\n\n讓我搜尋一下這個代碼，請稍候。",
        execution_result=ExecutionResult(
            content="## 查詢 00981T\n\n讓我搜尋一下這個代碼，請稍候。",
            task_contract=contract,
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert completion.missing_evidence


def test_task_contract_allows_one_source_for_explicit_url_web_tasks():
    intent = TaskIntentService().classify("Please summarize https://example.com/docs")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    criteria = {criterion.kind: criterion for criterion in contract.acceptance_criteria}
    assert criteria["source_artifact"].min_count == 1
    assert criteria["source_detail"].min_count == 1


def test_completion_gate_requires_web_source_artifacts_after_evidence():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="找到幾個可能方向，包括 Reddit 搜尋工具與相關 API，可以再依需求挑選整合方式。",
        execution_result=ExecutionResult(
            content="找到幾個可能方向，包括 Reddit 搜尋工具與相關 API，可以再依需求挑選整合方式。",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == TASK_ARTIFACTS_NOT_PRODUCED_REASON
    assert "web_source" in (completion.active_task_detail or "")


def test_completion_gate_requires_traceable_web_source_metadata():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=(
            "我找到 Reddit 官方 API 文件與第三方搜尋方向，可以先看 reddit.com 的官方文件，"
            "再評估是否需要補充歷史資料來源。這樣能先確認授權與查詢限制，再決定整合方式。"
        ),
        execution_result=ExecutionResult(
            content="done",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
            task_artifacts=(TaskArtifact(kind="web_source", source_tool="web_search", content_preview="source"),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == SOURCE_ARTIFACTS_NOT_TRACEABLE_REASON
    assert "source metadata" in (completion.active_task_detail or "")


def test_completion_gate_rejects_search_only_web_source_artifacts():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Reddit 官方 API 文件在 reddit.com 提供搜尋與列表相關端點，Reddit API wiki 也補充整合注意事項。"
        "這些來源可先用來判斷授權、速率限制與資料保留策略，再決定是否需要第三方歷史資料。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
            task_artifacts=(_web_source_artifact(),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == SOURCE_MATERIAL_INSUFFICIENT_REASON


def test_completion_gate_rejects_ungathered_source_urls():
    intent = TaskIntentService().classify("Find current Reddit API search documentation and cite sources")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "I checked the Reddit API docs at https://www.reddit.com/dev/api/ and also found "
        "a useful changelog at https://example.com/fake-reddit-api-news. The docs cover "
        "authentication, listings, and search-related endpoints."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(ToolEvidence(name="web_search", ok=True), ToolEvidence(name="web_fetch", ok=True)),
            task_artifacts=_web_research_artifacts(),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == UNGATHERED_SOURCE_REFERENCED_REASON
    assert "https://example.com/fake-reddit-api-news" in (completion.active_task_detail or "")


def test_completion_gate_allows_gathered_source_urls_with_punctuation():
    intent = TaskIntentService().classify("Find current Reddit API search documentation and cite sources")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "I checked the Reddit API docs (https://www.reddit.com/dev/api/). The docs cover "
        "authentication, listings, and search-related endpoints."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(ToolEvidence(name="web_search", ok=True), ToolEvidence(name="web_fetch", ok=True)),
            task_artifacts=_web_research_artifacts(),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_normalizes_openrouter_docs_legacy_api_reference_urls():
    intent = TaskIntentService().classify("Check OpenRouter API parameters and cite sources")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    contract = replace(
        contract,
        acceptance_criteria=contract.acceptance_criteria
        + (AcceptanceCriterion(kind="source_reference", min_count=1, description="Cite source URLs."),),
    )
    answer = (
        "OpenRouter documents `max_tokens` on its parameters page: "
        "https://openrouter.ai/docs/api-reference/parameters. The page says it limits generated output "
        "and that the maximum depends on context length minus prompt length."
    )
    artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_fetch",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_search",
                    "url": "https://openrouter.ai/docs/api/reference/overview",
                    "title": "OpenRouter API Reference",
                    "snippet": "OpenRouter API reference overview.",
                },
                {
                    "tool_name": "web_fetch",
                    "url": "https://openrouter.ai/docs/api/reference/parameters.md",
                    "title": "API Parameters | OpenRouter",
                    "snippet": "Max Tokens sets the upper limit for generated tokens.",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                }
            ],
            "source_count": 2,
        },
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_fetch", ok=True),),
            task_artifacts=(artifact,),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_allows_openrouter_api_endpoint_in_answer():
    intent = TaskIntentService().classify("Check OpenRouter API base URL and cite sources")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    contract = replace(
        contract,
        acceptance_criteria=contract.acceptance_criteria
        + (AcceptanceCriterion(kind="source_reference", min_count=1, description="Cite source URLs."),),
    )
    answer = (
        "OpenRouter's API base URL is `https://openrouter.ai/api/v1`; the Responses endpoint is "
        "`https://openrouter.ai/api/v1/responses`. Source: "
        "https://openrouter.ai/docs/api/reference/overview."
    )
    artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_fetch",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://openrouter.ai/docs/api/reference/overview.md",
                    "title": "OpenRouter API Reference",
                    "snippet": "The OpenRouter API reference documents the API base URL and endpoints.",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                },
                {
                    "tool_name": "web_search",
                    "url": "https://openrouter.ai/docs/quickstart",
                    "title": "OpenRouter Quickstart",
                    "snippet": "OpenRouter quickstart links the API reference and setup flow.",
                }
            ],
            "source_count": 2,
        },
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_fetch", ok=True),),
            task_artifacts=(artifact,),
        ),
    )

    assert completion.status == "complete"


@pytest.mark.anyio
async def test_completion_gate_rejects_openrouter_base_url_source_summary_without_answer():
    intent = TaskIntentService().classify("幫我查目前 OpenRouter 官方文件中 API base URL 是什麼，附來源網址。")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我已根據本輪已成功蒐集到的來源整理如下，避免停在只有進度句的狀態。\n\n"
        "重點摘要：\n"
        "1. https://openrouter.ai/docs/quickstart: For clean Markdown of any page...\n\n"
        "來源網址：\n"
        "1. https://openrouter.ai/docs/quickstart.md"
    )
    artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_research",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://openrouter.ai/docs/api/reference/overview.md",
                    "title": "OpenRouter API Reference",
                    "snippet": "The OpenRouter API reference documents API endpoints.",
                    "content_chars": 1200,
                    "min_content_chars": 800,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                },
                {
                    "tool_name": "web_fetch",
                    "url": "https://openrouter.ai/docs/quickstart.md",
                    "title": "OpenRouter Quickstart",
                    "snippet": "The OpenRouter quickstart links setup and API usage docs.",
                    "content_chars": 1200,
                    "min_content_chars": 800,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                },
                {
                    "tool_name": "web_fetch",
                    "url": "https://openrouter.ai/docs/api/reference/parameters.md",
                    "title": "OpenRouter API Parameters",
                    "snippet": "The OpenRouter parameters page documents request options.",
                    "content_chars": 1200,
                    "min_content_chars": 800,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                }
            ],
            "source_count": 3,
            "coverage": {"target_met": True, "target_fetch_count": 3, "fetched_count": 3},
        },
    )

    completion = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected source summary without requested concrete fact",
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            executed_tool_calls=1,
            task_contract=contract,
            tool_evidence=(ToolEvidence(name="web_research", ok=True),),
            task_artifacts=(artifact,),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "judge rejected source summary without requested concrete fact"


@pytest.mark.anyio
async def test_completion_gate_rejects_market_quote_source_summary_without_price():
    intent = TaskIntentService().classify("幫我找一下台積電 ADR 目前最新股價或最接近可查到的報價，並附來源。")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我已根據本輪已成功蒐集到的來源整理如下，避免停在只有進度句的狀態。\n\n"
        "重點摘要：\n"
        "1. TSMC - Wikipedia: 台灣積體電路製造股份有限公司...\n\n"
        "來源網址：\n"
        "1. https://en.wikipedia.org/wiki/TSMC"
    )
    artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_research",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://finance.yahoo.com/quote/TSM/",
                    "title": "TSM Stock Price",
                    "snippet": "Yahoo Finance quote page for Taiwan Semiconductor Manufacturing Company.",
                    "content_chars": 1200,
                    "min_content_chars": 800,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                },
                {
                    "tool_name": "web_fetch",
                    "url": "https://www.marketwatch.com/investing/stock/tsm",
                    "title": "TSM Overview",
                    "snippet": "MarketWatch quote overview for TSM.",
                    "content_chars": 1200,
                    "min_content_chars": 800,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                },
                {
                    "tool_name": "web_fetch",
                    "url": "https://en.wikipedia.org/wiki/TSMC",
                    "title": "TSMC",
                    "snippet": "Background information about TSMC.",
                    "content_chars": 1200,
                    "min_content_chars": 800,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                },
            ],
            "source_count": 3,
            "coverage": {"target_met": True, "target_fetch_count": 3, "fetched_count": 3},
        },
    )

    completion = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected source summary without requested quote",
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            executed_tool_calls=1,
            task_contract=contract,
            tool_evidence=(ToolEvidence(name="web_research", ok=True),),
            task_artifacts=(artifact,),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "judge rejected source summary without requested quote"


def test_completion_gate_rejects_recommended_ungathered_quote_url():
    intent = TaskIntentService().classify("Find the latest available TSMC quote and cite sources.")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    contract = replace(
        contract,
        acceptance_criteria=contract.acceptance_criteria
        + (AcceptanceCriterion(kind="source_reference", min_count=1, description="Cite source URLs."),),
    )
    answer = (
        "I found the latest available ADR quote from Yahoo Finance at "
        "https://finance.yahoo.com/quote/TSM/. This is the US ADR quote, not the Taiwan-listed 2330 quote. "
        "If you need the Taiwan listing, 建議至 https://finance.yahoo.com/quote/2330.TW/ check the local quote page."
    )
    artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_fetch",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://finance.yahoo.com/quote/TSM/",
                    "title": "Taiwan Semiconductor Manufacturing Company Limited (TSM)",
                    "snippet": "Yahoo Finance quote page for TSM ADR.",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                },
                {
                    "tool_name": "web_search",
                    "url": "https://finance.yahoo.com/quote/TSM/history/",
                    "title": "TSM Historical Data",
                    "snippet": "Yahoo Finance historical data page for TSM.",
                },
            ],
            "source_count": 2,
        },
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_fetch", ok=True),),
            task_artifacts=(artifact,),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == UNGATHERED_SOURCE_REFERENCED_REASON
    assert "https://finance.yahoo.com/quote/2330.TW/" in (completion.active_task_detail or "")


def test_completion_gate_rejects_too_short_web_fetch_source_detail():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Reddit 官方 API 文件在 reddit.com 提供搜尋與列表相關端點，Reddit API wiki 也補充整合注意事項。"
        "這些來源可先用來判斷授權、速率限制與資料保留策略，再決定是否需要第三方歷史資料。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(ToolEvidence(name="web_search", ok=True), ToolEvidence(name="web_fetch", ok=True)),
            task_artifacts=(_web_source_artifact(), _web_fetch_artifact(is_too_short=True)),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == SOURCE_MATERIAL_INSUFFICIENT_REASON


def test_completion_gate_rejects_failed_web_fetch_source_artifact():
    intent = TaskIntentService().classify("幫我找 2330 市值")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = "台積電市值資料來源是 Yahoo Finance。這個回答故意引用失敗 fetch，應被 gate 擋下。"

    fetch_error = tool_error_result(
        "HTTP Error: 404 Not Found",
        error_type="ToolExecutionError",
        metadata={"tool_name": "web_fetch"},
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_fetch", ok=False),),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_fetch",
                    ok=False,
                    content_preview=fetch_error,
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://finance.yahoo.com/quote/2330.TW/",
                                "snippet": fetch_error,
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"


def test_completion_gate_rejects_blocked_web_fetch_source_detail():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Reddit 官方 API 文件在 reddit.com 提供搜尋與列表相關端點，Reddit API wiki 也補充整合注意事項。"
        "這些來源可先用來判斷授權、速率限制與資料保留策略，再決定是否需要第三方歷史資料。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(ToolEvidence(name="web_search", ok=True), ToolEvidence(name="web_fetch", ok=True)),
            task_artifacts=(_web_source_artifact(), _web_fetch_artifact(blocked_or_challenge=True)),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == SOURCE_MATERIAL_INSUFFICIENT_REASON


def test_completion_gate_rejects_web_research_coverage_gaps():
    intent = TaskIntentService().classify("Please search online for current AI browser pricing.")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "AI Browser Docs at docs.test explains the official browser documentation, while AI Browser Pricing at pricing.test "
        "indicates pricing information may need separate verification before a final recommendation."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_research", ok=True),),
            task_artifacts=(_web_research_coverage_gap_artifact(),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == SOURCE_MATERIAL_INSUFFICIENT_REASON
    assert "Web research coverage gap" in (completion.active_task_detail or "")
    assert "Target fetch count not met: need 2, fetched 1" in (completion.active_task_detail or "")
    assert "ai browser pricing" in (completion.active_task_detail or "")


def test_completion_gate_accepts_web_research_gap_after_supplemental_fetches():
    intent = TaskIntentService().classify("Please search online for current AI browser pricing.")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "AI Browser Docs at https://docs.test/browser explains the official documentation, "
        "AI Browser Market at https://market.test/browser adds current market context, and "
        "AI Browser Pricing at https://pricing.test/browser covers pricing details."
    )
    supplemental_fetch = TaskArtifact(
        kind="web_source",
        source_tool="web_fetch",
        content_preview="pricing source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://pricing.test/browser",
                    "title": "AI Browser Pricing",
                    "snippet": "Current pricing details for AI Browser." * 30,
                    "content_chars": 1200,
                    "has_main_content": True,
                    "is_too_short": False,
                    "blocked_or_challenge": False,
                    "min_content_chars": 800,
                }
            ],
            "source_count": 1,
        },
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            had_tool_error=True,
            tool_evidence=(
                ToolEvidence(name="web_research", ok=True),
                ToolEvidence(name="web_fetch", ok=True),
                ToolEvidence(name="web_fetch", ok=False, metadata={"error": "HTTP 404"}),
            ),
            task_artifacts=(_web_research_coverage_gap_artifact(), supplemental_fetch),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_accepts_web_research_when_fetch_target_met_with_partial_query_gap():
    intent = TaskIntentService().classify("Please search online for current AI browser pricing.")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Two useful sources are AI Browser Docs at https://docs.test/browser and "
        "AI Browser Market at https://market.test/browser."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_research", ok=True),),
            task_artifacts=(_web_research_partial_query_artifact(),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_rejects_empty_web_research_even_when_assistant_asks_user():
    intent = TaskIntentService().classify("你再去網路上找更多 GPT Image 影片流程資料")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    response = "網路搜尋被阻擋，請提供可存取的網頁連結，我再幫你分析。"

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(
            content=response,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(
                    name="web_research",
                    ok=False,
                    metadata={
                        "source_count": 0,
                        "fetched_count": 0,
                        "coverage": {"target_fetch_count": 4, "target_met": False, "fetched_count": 0},
                    },
                ),
            ),
            task_artifacts=(),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert completion.missing_evidence


def test_completion_gate_rejects_terse_web_research_final_answer():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="找到了。",
        execution_result=ExecutionResult(
            content="找到了。",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
            task_artifacts=_web_research_artifacts(),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == TERSE_FINAL_ANSWER_REASON


def test_completion_gate_requires_web_source_reference_in_final_answer():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我找到幾個可行方向：官方 API 適合授權後搜尋與抓取討論串，第三方歷史資料源可作補充，"
        "也可以用一般網頁搜尋搭配站內查詢。建議先用官方 API，若需要大量歷史查詢再評估第三方資料源。"
        "整合時要先確認授權限制、查詢速率、資料保留政策，以及是否需要補充快取與去重機制。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
            task_artifacts=_web_research_artifacts(),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == GATHERED_SOURCE_REFERENCE_MISSING_REASON


def test_completion_gate_completes_web_research_with_source_artifact_and_answer():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我找到幾個可行方向：Reddit 官方 API 文件在 reddit.com，適合授權後搜尋與抓取討論串，"
        "Pushshift 類資料源可作歷史資料補充，也可以用一般 web search 搭配 site:reddit.com 查詢。"
        "建議先用官方 API，若需要大量歷史查詢再評估第三方資料源。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
            task_artifacts=_web_research_artifacts(),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_completes_explicit_url_with_substantive_fetch_source():
    intent = TaskIntentService().classify("Please summarize https://www.reddit.com/dev/api/")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "The Reddit API docs at reddit.com describe official API access for listings, search-adjacent endpoints, "
        "authentication, and rate-limit considerations. That is enough source material to summarize the requested URL "
        "and recommend checking auth and rate policies before implementation."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_fetch", ok=True),),
            task_artifacts=(_web_fetch_artifact(),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_allows_optional_search_errors_after_successful_fetch_sources():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Reddit API docs at reddit.com describe official API access for listings, search-adjacent endpoints, "
        "authentication, and rate-limit considerations. A Reddit search guide at example.com adds practical query "
        "guidance, so these fetched sources are enough even though a broad search provider returned no results."
    )
    second_fetch_artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_fetch",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://example.com/reddit-search-guide",
                    "title": "Reddit Search Guide",
                    "snippet": "Practical guidance for searching Reddit content from web sources.",
                    "query": "https://example.com/reddit-search-guide",
                    "provider": "web_fetch",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                    "min_content_chars": 800,
                    "extractor": "trafilatura",
                    "truncated": False,
                }
            ],
            "source_count": 1,
        },
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            had_tool_error=True,
            tool_evidence=(
                ToolEvidence(name="web_search", ok=False, metadata={"error": "DuckDuckGo returned no results"}),
                ToolEvidence(name="web_fetch", ok=True),
            ),
            task_artifacts=(_web_fetch_artifact(), second_fetch_artifact),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_allows_optional_workspace_discovery_errors_after_successful_read():
    intent = TaskIntentService().classify("請看目前 OpenSprite 的 trace CLI 怎麼用，只給我測試指令與用途。")
    contract = TaskContract(
        objective=intent.objective,
        task_type="workspace_read",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="workspace_read"),),
        allow_no_tool_final=False,
    )
    answer = (
        "OpenSprite trace CLI 用法：`opensprite trace <run_id> --session-id <session_id>`。"
        "常用測試指令包含 `opensprite trace run_xxx --session-id web:chat --json` "
        "以及 `opensprite trace run_xxx --session-id web:chat --full --json`，"
        "用途是檢查 run 狀態、工具呼叫、completion gate 和完整事件。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            had_tool_error=True,
            tool_evidence=(
                ToolEvidence(name="grep_files", ok=False, metadata={"error": "no matches"}),
                ToolEvidence(name="read_file", ok=True),
            ),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_allows_optional_fetch_errors_after_successful_fetch_sources():
    intent = TaskIntentService().classify("Find current Qwen model releases and summarize them with sources.")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Qwen's current model line includes Qwen3, with details from qwenlm.github.io, and a recent "
        "Qwen3 checkpoint listed on Hugging Face. These successful sources are enough to answer the request "
        "even though one extra model URL returned 404."
    )
    second_fetch_artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_fetch",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507",
                    "title": "Qwen3-30B-A3B-Instruct-2507",
                    "snippet": "Model card for a recent Qwen3 instruct checkpoint.",
                    "query": "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507",
                    "provider": "web_fetch",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                    "min_content_chars": 800,
                    "extractor": "trafilatura",
                    "truncated": False,
                }
            ],
            "source_count": 1,
        },
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=3,
            had_tool_error=True,
            tool_evidence=(
                ToolEvidence(name="web_fetch", ok=True),
                ToolEvidence(name="web_fetch", ok=False, metadata={"error": "HTTP 404"}),
            ),
            task_artifacts=(_web_fetch_artifact(), second_fetch_artifact),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_allows_optional_fetch_errors_after_web_research_fetches_sources():
    intent = TaskIntentService().classify("Find current Qwen model releases and summarize them with sources.")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Qwen's current model line includes Qwen3, with details from qwenlm.github.io and a fetched "
        "Hugging Face model card. These sources satisfy the request even though one extra URL returned 404."
    )
    web_research_artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_research",
        content_preview="source",
        metadata={
            "coverage": {"fetched_count": 2, "target_met": True},
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://qwenlm.github.io/blog/qwen3/",
                    "title": "Qwen3",
                    "snippet": "Qwen3 release notes and model details.",
                    "content_chars": 1200,
                    "has_main_content": True,
                    "is_too_short": False,
                    "blocked_or_challenge": False,
                },
                {
                    "tool_name": "web_fetch",
                    "url": "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507",
                    "title": "Qwen3 model card",
                    "snippet": "Model card for a recent Qwen3 instruct checkpoint.",
                    "content_chars": 1200,
                    "has_main_content": True,
                    "is_too_short": False,
                    "blocked_or_challenge": False,
                },
            ],
            "source_count": 2,
        },
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            had_tool_error=True,
            tool_evidence=(
                ToolEvidence(name="web_research", ok=True),
                ToolEvidence(name="web_fetch", ok=False, metadata={"error": "HTTP 404"}),
            ),
            task_artifacts=(web_research_artifact,),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_still_blocks_failed_fetch_tool_errors():
    intent = TaskIntentService().classify("Please summarize https://www.reddit.com/dev/api/")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = "I could not fetch reddit.com, so I cannot summarize the source."

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            had_tool_error=True,
            tool_evidence=(ToolEvidence(name="web_fetch", ok=False, metadata={"error": "HTTP 404"}),),
            task_artifacts=(),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "tool execution reported an error without a clear blocker handoff"


def test_completion_gate_allows_non_exposed_permission_block_after_successful_web_sources():
    intent = TaskIntentService().classify("Find current Qwen model releases and summarize them with sources.")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Qwen's current model line includes Qwen3, with details from qwenlm.github.io and a fetched "
        "Hugging Face model card. These successful sources are enough to answer the request."
    )
    second_fetch_artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_fetch",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507",
                    "title": "Qwen3 model card",
                    "snippet": "Model card for a recent Qwen3 instruct checkpoint.",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                }
            ],
            "source_count": 1,
        },
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=3,
            had_tool_error=True,
            tool_evidence=(
                ToolEvidence(name="web_fetch", ok=True),
                ToolEvidence(
                    name="task_update",
                    ok=False,
                    metadata={
                        "permission": {
                            "blocked": True,
                            "exposed": False,
                            "reason": "risk level(s) not allowed: memory, write",
                        }
                    },
                ),
            ),
            task_artifacts=(_web_fetch_artifact(), second_fetch_artifact),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_accepts_browser_source_artifact_for_web_research():
    intent = TaskIntentService().classify("Open https://example.com/docs and summarize the source")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    artifact = TaskArtifact(
        kind="web_source",
        source_tool="browser_navigate",
        metadata={
            "sources": [
                {
                    "url": "https://example.com/docs",
                    "title": "Example Docs",
                    "snippet": "Example browser automation documentation.",
                }
            ]
        },
    )
    answer = (
        "Example Docs at example.com says the page is documentation for browser automation. "
        "That source is enough to summarize the requested page, and no separate search result is needed."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="browser_navigate", ok=True),),
            task_artifacts=(artifact,),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_marks_progress_only_fetch_response_incomplete():
    intent = TaskIntentService().classify("看一下 ai 版 幫我抓20 筆")
    contract = _itemized_contract(intent)

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="好，幫你抓 r/taiwan 熱門文章 20 筆！",
        execution_result=ExecutionResult(content="好，幫你抓 r/taiwan 熱門文章 20 筆！", task_contract=contract),
    )

    assert result.status == "incomplete"
    assert result.reason == ITEMIZED_OUTPUT_MISSING_REASON


@pytest.mark.anyio
async def test_completion_gate_marks_pending_search_response_incomplete():
    intent = TaskIntentService().classify("幫我查一下今天台積電股價，請列出來源網址。")
    response = "Let我先透过网路搜寻来查今天台积电（TSMC）美股即时股价。"

    result = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected progress-only search response",
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response, executed_tool_calls=1),
        progress_only_response=True,
    )

    assert result.status == "incomplete"
    assert result.reason == "judge rejected progress-only search response"
    assert result.progress_only_response is True


@pytest.mark.anyio
async def test_completion_gate_marks_chinese_fetch_progress_response_incomplete():
    intent = TaskIntentService().classify("幫我查一下今天台積電最近可取得的股價資訊，請附來源。")
    response = "搜尋結果已取得，讓我抓取實質內容頁面來確認股價數據。"

    result = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected progress-only fetch response",
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response, executed_tool_calls=1),
    )

    assert result.status == "incomplete"
    assert result.reason == "judge rejected progress-only fetch response"


def test_completion_gate_does_not_mark_source_reliability_answer_as_pending():
    intent = TaskIntentService().classify("剛剛那些來源可靠嗎？請根據你看到的來源說明，不要重新搜尋。")
    response = (
        "根據剛才 fetch 的來源網址，我來說明其可靠性：TechNews 是台灣科技財經媒體，"
        "可作新聞來源參考；但若要即時股價，仍應優先看交易所、券商或 Yahoo 股市報價頁。"
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert result.status == "complete"
    assert result.reason == "generic task returned a response"


def test_completion_gate_completes_planning_answer_without_tools():
    intent = TaskIntentService().classify("幫我規劃明天 30 分鐘學 Python 的安排，不要上網。")
    response = (
        "明天 30 分鐘安排：0-5 分鐘確認環境，5-15 分鐘練變數與基本型別，"
        "15-25 分鐘完成一個小練習，25-30 分鐘回顧並記錄明天要接續的主題。"
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert result.status == "complete"
    assert result.reason == "generic task returned a response"


def test_completion_gate_uses_contract_for_no_tool_final_response():
    intent = TaskIntentService().classify("幫我規劃明天 30 分鐘學 Python 的安排，不要上網。")
    response = (
        "明天 30 分鐘安排：0-5 分鐘確認環境，5-15 分鐘練變數與基本型別，"
        "15-25 分鐘完成一個小練習，25-30 分鐘回顧並記錄明天要接續的主題。"
    )
    contract = TaskContract(
        objective=intent.objective,
        task_type="planning",
        final_answer_required=True,
        allow_no_tool_final=True,
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response, task_contract=contract),
    )

    assert result.status == "complete"
    assert result.reason == "task contract accepted final response"
    assert result.should_update_active_task is True


def test_task_contract_trusts_task_context_inherited_tool_group_without_message_override():
    intent = TaskIntentService().classify("剛剛那些來源可靠嗎？請根據你看到的來源說明，不要重新搜尋。")

    contract = _contract_from_task_planner_payload(
        {
            "task_type": "pure_answer",
            "required_tool_groups": [],
            "final_answer_required": True,
            "allow_no_tool_final": True,
            "reason": "Answer from previous sources.",
        },
        fallback_objective=intent.objective,
        current_message=intent.objective,
        history=[],
        current_image_files=None,
        current_audio_files=None,
        current_video_files=None,
        task_context_decision=TaskContextDecision(
            is_follow_up=True,
            should_inherit_active_task=True,
            inherited_task_type="web_research",
            inherited_tool_group="web_research",
            continuation_type="continue_active_task",
        ),
    )

    assert contract.task_type == "pure_answer"
    assert any(requirement.tool_group == "web_research" for requirement in contract.requirements)


def test_quality_gate_reports_missing_requested_items():
    intent = TaskIntentService().classify("看一下 ai 版 幫我抓20 筆")
    contract = _itemized_contract(intent)

    result = QualityGateService().evaluate(
        task_intent=intent,
        response_text="好，幫你抓 r/taiwan 熱門文章 20 筆！",
        execution_result=ExecutionResult(content="好，幫你抓 r/taiwan 熱門文章 20 筆！", task_contract=contract),
    )

    assert result.passed is False
    assert result.status == "incomplete"
    assert result.reason == ITEMIZED_OUTPUT_MISSING_REASON

def test_task_contract_records_itemized_acceptance_criterion():
    intent = TaskIntentService().classify("看一下 ai 版 幫我抓20 筆")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    assert len(contract.acceptance_criteria) == 1
    criterion = contract.acceptance_criteria[0]
    assert criterion.kind == ITEMIZED_OUTPUT_CRITERION_KIND
    assert criterion.min_count == 3
    assert criterion.max_response_chars == 260
    assert contract.to_metadata()["acceptance_criteria"][0]["kind"] == ITEMIZED_OUTPUT_CRITERION_KIND


def test_task_contract_ignores_numbers_inside_identifiers_for_itemized_count():
    intent = TaskIntentService().classify("Please answer in one sentence: ORCHID-924 是什麼？")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    assert not any(criterion.kind == ITEMIZED_OUTPUT_CRITERION_KIND for criterion in contract.acceptance_criteria)


def test_completion_gate_accepts_one_sentence_identifier_recall():
    intent = TaskIntentService().classify("Please answer in one sentence: ORCHID-924 是什麼？")
    answer = "ORCHID-924 是你之前要我記住的代號。"

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(content=answer),
    )

    assert result.status == "complete"


def test_quality_gate_uses_contract_acceptance_criteria():
    intent = TaskIntentService().classify("請整理結果")
    contract = TaskContract(
        objective=intent.objective,
        task_type="task",
        acceptance_criteria=(
            AcceptanceCriterion(
                kind=ITEMIZED_OUTPUT_CRITERION_KIND,
                min_count=3,
                max_response_chars=260,
                description="Provide at least three listed items.",
            ),
        ),
    )

    result = QualityGateService().evaluate(
        task_intent=intent,
        response_text="我會整理三筆結果。",
        execution_result=ExecutionResult(content="我會整理三筆結果。"),
        task_contract=contract,
    )

    assert result.passed is False
    assert result.reason == ITEMIZED_OUTPUT_MISSING_REASON


def test_quality_gate_requires_recorded_verification_attempt_after_code_changes():
    intent = TaskIntentService().classify("Please fix src/app.py")
    contract = TaskContract(
        objective=intent.objective,
        task_type="code_change",
        acceptance_criteria=(
            AcceptanceCriterion(
                kind="verification_or_gap",
                description="State verification outcome or gap.",
            ),
        ),
    )

    missing_gap = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Updated src/app.py.",
        execution_result=ExecutionResult(content="Updated src/app.py.", file_change_count=1),
        task_contract=contract,
    )
    reported_gap_without_artifact = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Updated src/app.py. Tests not run because pytest is unavailable.",
        execution_result=ExecutionResult(content="Updated src/app.py.", file_change_count=1),
        task_contract=contract,
    )
    recorded_gap = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Updated src/app.py.",
        execution_result=ExecutionResult(
            content="Updated src/app.py.",
            file_change_count=1,
            verification_attempted=True,
            verification_passed=False,
            task_artifacts=(
                TaskArtifact(
                    kind="verification_result",
                    source_tool="verify",
                    content_preview="No supported Python or package.json build checks were detected.",
                    ok=True,
                    metadata={VERIFICATION_STATUS_METADATA_FIELD: "skipped"},
                ),
            ),
        ),
        task_contract=contract,
    )

    assert missing_gap.passed is False
    assert missing_gap.status == "needs_verification"
    assert missing_gap.reason == VERIFICATION_OUTCOME_OR_GAP_MISSING_REASON
    assert reported_gap_without_artifact.passed is False
    assert recorded_gap.passed is True


def test_quality_gate_requires_operation_validation_or_risk_report():
    intent = TaskIntentService().classify("Update the MCP server configuration")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        acceptance_criteria=(
            AcceptanceCriterion(kind="operation_report", description="Report validation or risk."),
        ),
    )

    missing_report = QualityGateService().evaluate(
        task_intent=intent,
        response_text="I changed the setting and finished the task.",
        execution_result=ExecutionResult(content="I changed the setting and finished the task."),
        task_contract=contract,
    )
    reported_validation_without_evidence = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Configuration validation passed; residual risk is low.",
        execution_result=ExecutionResult(content="Configuration validation passed; residual risk is low."),
        task_contract=contract,
    )
    reported_tool_result = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Configuration validation passed.",
        execution_result=ExecutionResult(
            content="Configuration validation passed.",
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="exec", ok=True, result_preview="configuration validation passed"),),
        ),
        task_contract=contract,
    )

    assert missing_report.passed is False
    assert missing_report.reason == OPERATION_VALIDATION_OR_RISK_MISSING_REASON
    assert reported_validation_without_evidence.passed is False
    assert reported_tool_result.passed is True


def test_quality_gate_accepts_operation_report_with_successful_tool_result():
    intent = TaskIntentService().classify("Check whether git is available")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        acceptance_criteria=(
            AcceptanceCriterion(kind="operation_report", description="Report the operation result."),
        ),
    )

    result = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Git 可以執行，版本是 git version 2.47.1.windows.2。",
        execution_result=ExecutionResult(
            content="Git 可以執行，版本是 git version 2.47.1.windows.2。",
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(name="exec", ok=True, result_preview="git version 2.47.1.windows.2"),
            ),
        ),
        task_contract=contract,
    )

    assert result.passed is True


@pytest.mark.anyio
async def test_completion_gate_uses_judge_for_missing_git_metadata_claimed_clean():
    intent = TaskIntentService().classify("幫我看目前 repo 是否有未提交的 source 改動")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        acceptance_criteria=(
            AcceptanceCriterion(kind="operation_report", description="Report the operation result."),
        ),
        planner_metadata={"quality_checks": [REPOSITORY_STATUS_QUALITY_CHECK]},
    )

    result = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected clean working tree claim with missing git metadata",
        task_intent=intent,
        response_text="No uncommitted source changes; exec confirmed the workspace is clean.",
        execution_result=ExecutionResult(
            content="No uncommitted source changes; exec confirmed the workspace is clean.",
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="exec", ok=True, result_preview='"NO_GIT"'),),
            task_contract=contract,
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "judge rejected clean working tree claim with missing git metadata"


def test_quality_gate_accepts_missing_git_metadata_as_blocker():
    intent = TaskIntentService().classify("幫我看目前 repo 是否有未提交的 source 改動")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        acceptance_criteria=(
            AcceptanceCriterion(kind="operation_report", description="Report the operation result."),
        ),
        planner_metadata={"quality_checks": [REPOSITORY_STATUS_QUALITY_CHECK]},
    )

    result = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Blocked: exec 回傳 NO_GIT，active workspace 不是 git repository，因此無法確認未提交改動。",
        execution_result=ExecutionResult(
            content="Blocked: exec 回傳 NO_GIT，active workspace 不是 git repository，因此無法確認未提交改動。",
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="exec", ok=True, result_preview='"NO_GIT"'),),
        ),
        task_contract=contract,
    )

    assert result.passed is True


def test_quality_gate_accepts_operation_blocker_report():
    intent = TaskIntentService().classify("Check repository status")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        acceptance_criteria=(
            AcceptanceCriterion(kind="operation_report", description="Report the operation result."),
        ),
    )

    result = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Blocker: the active workspace is not a git repository, so status cannot be verified.",
        execution_result=ExecutionResult(
            content="Blocker: the active workspace is not a git repository, so status cannot be verified.",
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="exec", ok=True, result_preview="fatal: not a git repository"),),
        ),
        task_contract=contract,
    )

    assert result.passed is True


def test_quality_gate_accepts_version_only_operation_answer():
    intent = TaskIntentService().classify("幫我確認這台環境的 git 版本，回答版本號即可。")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        acceptance_criteria=(
            AcceptanceCriterion(kind="operation_report", description="Report the operation result."),
        ),
    )

    result = QualityGateService().evaluate(
        task_intent=intent,
        response_text="`2.47.1.windows.2`",
        execution_result=ExecutionResult(
            content="`2.47.1.windows.2`",
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(name="exec", ok=True, result_preview="git version 2.47.1.windows.2"),
            ),
        ),
        task_contract=contract,
    )

    assert result.passed is True


def test_completion_gate_marks_simple_task_response_complete_without_marker():
    intent = TaskIntentService().classify("請只回覆這三個英文詞，且不要加入其他文字：alpha beta gamma")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="alpha beta gamma",
        execution_result=ExecutionResult(content="alpha beta gamma"),
    )

    assert intent.kind == "task"
    assert result.status == "complete"
    assert result.reason == "generic task returned a response"


def test_auto_continue_allows_first_retry_after_missing_web_evidence():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = _web_contract(intent)
    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="正在幫你搜尋 Reddit 搜尋相關的開源專案...",
        execution_result=ExecutionResult(content="正在幫你搜尋 Reddit 搜尋相關的開源專案...", task_contract=contract),
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="正在幫你搜尋 Reddit 搜尋相關的開源專案...", task_contract=contract),
        attempts_used=0,
        previous_response="正在幫你搜尋 Reddit 搜尋相關的開源專案...",
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_incomplete"
    assert "Continue the current task" in (decision.prompt or "")
    assert "Required follow-up" in (decision.prompt or "")

def test_auto_continue_allows_first_retry_after_progress_only_fetch_response():
    intent = TaskIntentService().classify("看一下 ai 版 幫我抓20 筆")
    contract = _itemized_contract(intent)
    response = "好，幫你抓 r/taiwan 熱門文章 20 筆！"
    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response, task_contract=contract),
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content=response, task_contract=contract),
        attempts_used=0,
        previous_response=response,
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_incomplete"
    assert "Continue the current task" in (decision.prompt or "")

def test_completion_gate_marks_chinese_action_ack_response_incomplete():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理"
    )
    response = "好，我來分析全部 4 張圖片並整理 Prompt！"
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/a.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/b.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/c.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/d.jpg"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response, task_contract=contract),
    )

    exec_result = ExecutionResult(content=response, task_contract=contract)
    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=exec_result,
        attempts_used=0,
        previous_response=response,
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert "image:images/a.jpg" in "\n".join(completion.missing_evidence)
    assert decision.should_continue is True
    assert "Continue the current task" in (decision.prompt or "")


def test_completion_gate_marks_generic_chinese_intent_to_act_response_incomplete():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理"
    )
    response = "好，我馬上處理這 4 張圖片。"
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/a.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/b.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/c.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/d.jpg"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response, task_contract=contract),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"


def test_completion_gate_completes_media_contract_when_all_images_have_evidence():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理"
    )
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/a.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/b.jpg"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=(
            "Prompt 1: Create a clean product hero image with soft light and clear typography.\n\n"
            "Prompt 2: Render the same subject from a wider angle with consistent styling.\n\n"
            "整合版：保留共同主題、光線、構圖與文字重點，整理成可直接使用的完整 prompt。"
        ),
        execution_result=ExecutionResult(
            content=(
                "Prompt 1: Create a clean product hero image with soft light and clear typography.\n\n"
                "Prompt 2: Render the same subject from a wider angle with consistent styling.\n\n"
                "整合版：保留共同主題、光線、構圖與文字重點，整理成可直接使用的完整 prompt。"
            ),
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(
                ToolEvidence(name="ocr_image", resource_ids=("image:images/a.jpg",), ok=True),
                ToolEvidence(name="analyze_image", resource_ids=("image:images/b.jpg",), ok=True),
            ),
            task_artifacts=(
                TaskArtifact(kind="image_text", source_tool="ocr_image", resource_ids=("image:images/a.jpg",)),
                TaskArtifact(kind="image_analysis", source_tool="analyze_image", resource_ids=("image:images/b.jpg",)),
            ),
        ),
    )

    assert completion.status == "complete"
    assert completion.missing_evidence == ()


def test_completion_gate_rejects_terse_media_final_answer():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理"
    )
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/a.jpg"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="已完成。",
        execution_result=ExecutionResult(
            content="已完成。",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(name="ocr_image", resource_ids=("image:images/a.jpg",), ok=True),
            ),
            task_artifacts=(
                TaskArtifact(kind="image_text", source_tool="ocr_image", resource_ids=("image:images/a.jpg",)),
            ),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == TERSE_FINAL_ANSWER_REASON


def test_completion_gate_requires_media_artifacts_after_evidence():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理"
    )
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/a.jpg"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Prompt 1: Extracted prompt details from the uploaded image with enough content to produce a usable merged output.",
        execution_result=ExecutionResult(
            content="Prompt 1: Extracted prompt details from the uploaded image with enough content to produce a usable merged output.",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(name="ocr_image", resource_ids=("image:images/a.jpg",), ok=True),
            ),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == TASK_ARTIFACTS_NOT_PRODUCED_REASON
    assert "image:images/a.jpg" in (completion.active_task_detail or "")


def test_completion_gate_accepts_current_turn_media_index_evidence_for_saved_files():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理",
        images=["data:image/jpeg;base64,abc", "data:image/jpeg;base64,def"],
    )
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        current_image_files=["images/a.jpg", "images/b.jpg"],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=(
            "Prompt 1: Extract the full visible prompt from the first image and preserve wording.\n"
            "Prompt 2: Extract the full visible prompt from the second image and preserve wording.\n\n"
            "整合版：合併兩張圖中的共同主題、構圖、風格與細節，移除重複段落。"
        ),
        execution_result=ExecutionResult(
            content=(
                "Prompt 1: Extract the full visible prompt from the first image and preserve wording.\n"
                "Prompt 2: Extract the full visible prompt from the second image and preserve wording.\n\n"
                "整合版：合併兩張圖中的共同主題、構圖、風格與細節，移除重複段落。"
            ),
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(
                ToolEvidence(name="ocr_image", resource_ids=("image_index:0",), ok=True),
                ToolEvidence(name="ocr_image", resource_ids=("image_index:1",), ok=True),
            ),
            task_artifacts=(
                TaskArtifact(kind="image_text", source_tool="ocr_image", resource_ids=("image_index:0",)),
                TaskArtifact(kind="image_text", source_tool="ocr_image", resource_ids=("image_index:1",)),
            ),
        ),
    )

    assert completion.status == "complete"
    assert completion.missing_evidence == ()


def test_completion_gate_does_not_mark_short_answer_as_progress_only():
    intent = TaskIntentService().classify("你建議哪個方案？")
    response = "我建議用 RSS，因為不需要申請 API key。"

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert completion.status == "complete"
    assert completion.reason == "generic task returned a response"


def test_completion_gate_accepts_plain_answer_that_mentions_follow_up_handling():
    intent = TaskIntentService().classify(
        "我等等會問你一個暗號，暗號是 ORCHID-728。請先記住，順便用兩句話說明你會怎麼處理後續追問。"
    )
    contract = TaskContract(
        objective=intent.objective,
        task_type="pure_answer",
        allow_no_tool_final=True,
    )
    response = (
        "已記住暗號：**ORCHID-728**。\n\n"
        "後續若有人以此暗號追問，我會先確認對方是否為你本人或經你授權，再根據你預設的處置方式回應。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response, task_contract=contract),
    )

    assert completion.status == "complete"
    assert completion.reason == "plain-answer contract received a response"


@pytest.mark.anyio
async def test_completion_gate_marks_parallel_fetch_progress_response_incomplete():
    intent = TaskIntentService().classify(
        "查一下台積電股價或最近可取得的報價，附來源網址。"
    )
    response = (
        "需要同時抓台股和美股報價，先並行 fetch 幾個主要來源。\n\n"
        "**Fetching in parallel...**\n\n"
        "等待所有來源回應後整合結果。"
    )

    completion = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected progress-only parallel fetch response",
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "judge rejected progress-only parallel fetch response"


@pytest.mark.anyio
async def test_completion_gate_marks_shell_style_fetch_control_response_incomplete():
    intent = TaskIntentService().classify("查一下台積電股價或最近可取得的報價，附來源網址。")
    response = (
        '$TYPE = "fetch"\n'
        '$URL = "https://tickzen.app/stocks/tsm/overview"\n'
        '$INSTRUCTION = "Extract the current stock price."\n'
    )

    completion = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected tool-control response",
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "judge rejected tool-control response"


@pytest.mark.anyio
async def test_completion_gate_marks_xml_toolcall_control_response_incomplete():
    intent = TaskIntentService().classify("幫我查目前 OpenRouter 官方文件中 API base URL 是什麼，附來源網址。")
    response = (
        "<toolcall>\n"
        '<tool name="web_fetch">\n'
        '<parameter name="url">https://openrouter.ai/docs/api-reference/overview.md</parameter>\n'
        "</tool>\n"
        "</toolcall>"
    )

    completion = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected tool-control response",
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "judge rejected tool-control response"


def test_completion_gate_marks_internal_only_response_incomplete():
    intent = TaskIntentService().classify("幫我抓 Reddit ai 版 20 筆")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="",
        execution_result=ExecutionResult(
            content="",
            assistant_internal_only_response=True,
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "assistant only emitted internal control text"


@pytest.mark.anyio
async def test_completion_gate_rejects_short_chinese_fetch_progress_as_incomplete():
    intent = TaskIntentService().classify("查一下 OpenRouter 官方文件裡 Authentication header 怎麼寫，附來源網址。")
    contract = TaskContract(
        objective=intent.objective,
        task_type="web_research",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="web_research"),),
        acceptance_criteria=(AcceptanceCriterion(kind="substantive_final_answer", min_response_chars=20),),
    )

    result = await _evaluate_with_static_judge(
        status="incomplete",
        reason="judge rejected progress-only web research response",
        task_intent=intent,
        response_text="讓我直接抓 OpenRouter 的官方 API 文件來確認。",
        execution_result=ExecutionResult(
            content="讓我直接抓 OpenRouter 的官方 API 文件來確認。",
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_research", ok=True),),
            task_contract=contract,
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "judge rejected progress-only web research response"


def test_auto_continue_guides_retry_after_internal_only_response():
    intent = TaskIntentService().classify("幫我抓 Reddit ai 版 20 筆")
    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="",
        execution_result=ExecutionResult(
            content="",
            assistant_internal_only_response=True,
        ),
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(
            content="",
            assistant_internal_only_response=True,
        ),
        attempts_used=0,
        previous_response="",
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_incomplete"
    assert "only contained internal control text" in (decision.prompt or "")
    assert "Do not repeat internal tags" in (decision.prompt or "")


def test_completion_gate_marks_explicit_task_completion_done():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="No major findings.",
                    metadata={
                        "structured_output": {
                            "status": "ok",
                            "summary": "No major findings.",
                            "finding_count": 0,
                        }
                    },
                ),
            ),
        ),
    )

    assert result.status == "complete"
    assert result.active_task_status == "done"
    assert result.should_update_active_task is True


def test_completion_gate_requires_structured_clean_review_evidence():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="No major findings.",
                ),
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.review_attempted is True
    assert result.review_passed is False


def test_completion_gate_requires_recorded_code_changes_for_implementation():
    intent = TaskIntentService().classify("Please implement the final cleanup.")
    contract = TaskContract(
        objective=intent.objective,
        task_type="code_change",
        requirements=(EvidenceRequirement(kind="file_change", min_count=1),),
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(content="Implemented the final cleanup successfully.", task_contract=contract),
    )

    assert result.status == "incomplete"
    assert result.reason == "expected code changes were not recorded"


def test_completion_gate_respects_pure_answer_contract_over_code_words():
    intent = TaskIntentService().classify(
        "請幫我規劃一個安全的三階段修正流程，要包含每階段驗證方式；不要呼叫工具。"
    )
    contract = TaskContract(
        objective=intent.objective,
        task_type="pure_answer",
        requirements=(),
        acceptance_criteria=(),
        allow_no_tool_final=True,
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="第一階段先隔離問題，第二階段做最小修正，第三階段驗證結果。",
        execution_result=ExecutionResult(
            content="第一階段先隔離問題，第二階段做最小修正，第三階段驗證結果。",
            task_contract=contract,
        ),
    )

    assert intent.expects_code_change is False
    assert intent.expects_verification is False
    assert result.status == "complete"
    assert result.verification_required is False


def test_completion_gate_respects_workspace_read_contract_over_code_words():
    intent = TaskIntentService().classify("Find where web_research is implemented and answer with the path.")
    contract = TaskContract(
        objective=intent.objective,
        task_type="workspace_read",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="workspace_read"),),
        acceptance_criteria=(AcceptanceCriterion(kind="substantive_final_answer", min_response_chars=20),),
    )
    answer = "The implementation is in `src/opensprite/tools/web_research.py`, which orchestrates search and fetch."

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="read_file", ok=True),),
        ),
    )

    assert intent.expects_code_change is False
    assert result.status == "complete"


def test_completion_gate_requires_review_for_code_changes_without_review_evidence():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
        ),
    )

    assert result.status == "needs_review"
    assert result.reason == "delegated review was not recorded for code changes"
    assert result.review_required is True
    assert result.review_attempted is False
    assert "delegated review step" in (result.active_task_detail or "")


def test_completion_gate_requires_follow_up_when_review_reports_findings():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="One correctness risk found.",
                    metadata={
                        "structured_output": {
                            "status": "ok",
                            "summary": "One correctness risk found.",
                            "finding_count": 1,
                        }
                    },
                ),
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.reason == "delegated review reported findings that require follow-up"
    assert result.review_attempted is True
    assert result.review_passed is False
    assert result.review_finding_count == 1
    assert "correctness risk" in (result.active_task_detail or "")


def test_completion_gate_prefers_structured_review_fix_for_follow_up_detail():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="One high-risk bug found.",
                    metadata={
                        "structured_output": {
                            "status": "ok",
                            "summary": "One high-risk bug found.",
                            "finding_count": 1,
                            "sections": [
                                {
                                    "key": "findings",
                                    "title": "Review Findings",
                                    "type": "finding_list",
                                    "items": [
                                        {
                                            "title": "Null handling bug",
                                            "path": "src/foo.py",
                                            "why": "Empty input can raise an exception.",
                                            "fix": "Guard the null path before dereference.",
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                ),
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.active_task_detail == "src/foo.py: Null handling bug: Guard the null path before dereference."


def test_completion_gate_allows_workflow_completion_with_clean_review():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow: implement_then_review\nStatus: completed",
        execution_result=ExecutionResult(
            content="Workflow: implement_then_review\nStatus: completed",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="No major findings.",
                    metadata={"structured_output": {"status": "ok", "summary": "No major findings.", "finding_count": 0}},
                ),
            ),
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "review_attempted": True,
                    "review_passed": True,
                    "review_finding_count": 0,
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "complete"
    assert result.reason == "workflow implement_then_review completed with clean review evidence"


def test_completion_gate_uses_workflow_review_finding_detail_without_delegated_tasks():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow: implement_then_review\nStatus: completed",
        execution_result=ExecutionResult(
            content="Workflow: implement_then_review\nStatus: completed",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "review_attempted": True,
                    "review_passed": False,
                    "review_finding_count": 1,
                    "review_summary": "One high-risk bug found.",
                    "review_first_finding": "src/foo.py: Null handling bug: Guard the null path before dereference.",
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.reason == "workflow implement_then_review completed but review findings still require follow-up"
    assert result.active_task_detail == "src/foo.py: Null handling bug: Guard the null path before dereference."
    assert result.follow_up_workflow == "implement_then_review"
    assert result.follow_up_step_id == "implement"
    assert result.follow_up_step_label == "Implement"
    assert result.follow_up_prompt_type == "implementer"
    assert result.review_attempted is True
    assert result.review_finding_count == 1


def test_completion_gate_prioritizes_workflow_review_follow_up_before_verification():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow result attached.",
        execution_result=ExecutionResult(
            content="Workflow result attached.",
            file_change_count=1,
            touched_paths=("src/agent.py",),
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "review_attempted": True,
                    "review_passed": False,
                    "review_finding_count": 1,
                    "review_summary": "One high-risk bug found.",
                    "review_first_finding": "src/foo.py: Null handling bug: Guard the null path before dereference.",
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.follow_up_step_id == "implement"


def test_completion_gate_sets_workflow_review_step_target_when_review_is_missing():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow: implement_then_review\nStatus: completed",
        execution_result=ExecutionResult(
            content="Workflow: implement_then_review\nStatus: completed",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "review_attempted": False,
                    "review_passed": False,
                    "review_finding_count": 0,
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.follow_up_workflow == "implement_then_review"
    assert result.follow_up_step_id == "review"
    assert result.follow_up_step_label == "Code review"
    assert result.follow_up_prompt_type == "code-reviewer"


def test_completion_gate_marks_blocked_when_workflow_fails():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow failed.",
        execution_result=ExecutionResult(
            content="Workflow failed.",
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "failed",
                    "next_step_id": "review",
                    "next_step_label": "Code review",
                    "next_step_prompt_type": "code-reviewer",
                    "error": "review step failed",
                },
            ),
        ),
    )

    assert result.status == "blocked"
    assert result.reason == "workflow implement_then_review did not complete successfully"
    assert result.active_task_detail == "Resolve the Code review step failure in implement_then_review: review step failed"
    assert result.follow_up_prompt_type == "code-reviewer"


def test_completion_gate_marks_incomplete_when_workflow_is_cancelled():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow cancelled.",
        execution_result=ExecutionResult(
            content="Workflow cancelled.",
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "cancelled",
                    "next_step_id": "review",
                    "next_step_label": "Code review",
                    "next_step_prompt_type": "code-reviewer",
                    "error": "cancelled",
                    "summary": "Workflow stopped after 1/2 completed step(s).",
                },
            ),
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "workflow implement_then_review did not complete successfully"
    assert result.active_task_detail == (
        "Resume with the Code review step in implement_then_review. "
        "Workflow stopped after 1/2 completed step(s)."
    )
    assert result.follow_up_prompt_type == "code-reviewer"


def test_completion_gate_allows_research_then_outline_without_completion_phrase():
    intent = TaskIntentService().classify("Help me research and outline this topic.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow result attached below.",
        execution_result=ExecutionResult(
            content="Workflow result attached below.",
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_outline",
                    "workflow": "research_then_outline",
                    "status": "completed",
                    "review_attempted": False,
                    "review_passed": False,
                    "review_finding_count": 0,
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "complete"
    assert result.reason == "workflow research_then_outline completed all required steps"


def test_completion_gate_allows_review_without_code_changes():
    intent = TaskIntentService().classify("Please review the recent changes for regressions.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Found two regressions tied to src/app.py and tests/test_app.py.",
        execution_result=ExecutionResult(content="Found two regressions tied to src/app.py and tests/test_app.py."),
    )

    assert result.status == "complete"
    assert result.reason == "generic task returned a response"


def test_completion_gate_allows_debug_diagnosis_without_code_changes():
    intent = TaskIntentService().classify("Please investigate why the build is failing.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="The build fails because the generated config file is missing at startup.",
        execution_result=ExecutionResult(content="The build fails because the generated config file is missing at startup."),
    )

    assert result.status == "complete"
    assert result.reason == "generic task returned a response"
