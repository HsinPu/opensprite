"""Deterministic completion checks for one agent turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import DocumentLlmConfig
from ..storage.base import StoredDelegatedTask
from ..tool_names import BATCH_TOOL_NAME
from .active_task_status import BLOCKED_ACTIVE_TASK_STATUS, DONE_ACTIVE_TASK_STATUS
from .evidence_gate import EvidenceGateService
from .execution import ExecutionResult
from .completion_judge import (
    CompletionJudgeError,
    CompletionJudgeService,
    COMPLETION_JUDGE_UNAVAILABLE_REASON,
    CompletionJudgeVerdict,
    build_completion_judge_facts,
)
from .completion_status import (
    BLOCKED_COMPLETION_STATUS,
    COMPLETE_COMPLETION_STATUS,
    INCOMPLETE_COMPLETION_STATUS,
    NEEDS_REVIEW_COMPLETION_STATUS,
    NEEDS_VERIFICATION_COMPLETION_STATUS,
    is_complete_completion_status,
    needs_verification_completion_status,
)
from .harness_profile import VERIFICATION_REQUIREMENT_KIND, VERIFICATION_TOOL_GROUP
from .completion_task_policy import (
    accepts_final_response_task_type,
    intent_supports_fallback_active_task_update,
    is_analysis_response_intent_kind,
    is_generic_task_response_intent_kind,
    is_one_turn_intent_kind,
    is_plain_answer_task_type,
    is_read_only_blocking_requirement_kind,
    is_read_only_blocking_tool_group,
    is_read_only_task_type,
)
from .quality_gate import QualityGateService
from .stop_reasons import is_max_tool_iterations_stop_reason
from .subagent_output import (
    STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD,
    STRUCTURED_SUBAGENT_ITEMS_FIELD,
    STRUCTURED_SUBAGENT_SECTIONS_FIELD,
    STRUCTURED_SUBAGENT_STATUS_FIELD,
    STRUCTURED_SUBAGENT_SUMMARY_FIELD,
    is_clean_structured_subagent_status,
)
from .subagent_policy import REVIEW_PROMPT_TYPES
from .task_contract import (
    PLANNER_BLOCKED_STATUS,
    PLANNER_INVALID_STATUS,
    PLANNER_METADATA_REASON_FIELD,
    PLANNER_METADATA_STATUS_FIELD,
    contract_expects_file_change,
)
from .task_intent import (
    WORKFLOW_COMPLETION_INTENT_KINDS,
    TaskIntent,
)
from .history_retrieval_policy import is_history_retrieval_tool_name
from .tool_groups import WORKSPACE_DISCOVERY_TOOLS
from .web_source_policy import (
    is_fetched_web_source_artifact_tool,
    is_web_discovery_tool,
    is_web_fetch_source_record_tool,
    is_web_research_task_type,
    is_web_research_source_artifact_tool,
    is_web_research_tool_group,
    is_web_source_artifact_kind,
)
from .verification_policy import (
    REQUIRED_VERIFICATION_FAILED_REASON,
    SKIPPED_VERIFICATION_STATUS,
    VERIFICATION_STATUS_METADATA_FIELD,
    is_verification_result_artifact_kind,
    is_verification_tool_name,
    required_verification_completion_reason,
)
from .workflow_status import (
    is_workflow_cancelled_status,
    is_workflow_completed_status,
    is_workflow_failed_status,
    is_workflow_unsuccessful_status,
)
from .workflow_fields import (
    WORKFLOW_ERROR_FIELD,
    WORKFLOW_ID_FIELD,
    WORKFLOW_NEXT_STEP_ID_FIELD,
    WORKFLOW_NEXT_STEP_LABEL_FIELD,
    WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD,
    WORKFLOW_REVIEW_ATTEMPTED_FIELD,
    WORKFLOW_REVIEW_FINDING_COUNT_FIELD,
    WORKFLOW_REVIEW_FIRST_FINDING_FIELD,
    WORKFLOW_REVIEW_PASSED_FIELD,
    WORKFLOW_REVIEW_SUMMARY_FIELD,
    WORKFLOW_STATUS_FIELD,
    WORKFLOW_SUMMARY_FIELD,
    WORKFLOW_VERIFICATION_ATTEMPTED_FIELD,
    WORKFLOW_VERIFICATION_PASSED_FIELD,
)
from .workflows import (
    BUGFIX_THEN_TEST_THEN_REVIEW_WORKFLOW_ID,
    IMPLEMENT_THEN_REVIEW_WORKFLOW_ID,
    RESEARCH_THEN_OUTLINE_WORKFLOW_ID,
    REVIEW_WORKFLOW_IDS,
)

_REVIEW_PROMPT_TYPES = REVIEW_PROMPT_TYPES
_BLOCKING_PLANNER_STATUSES = frozenset({PLANNER_BLOCKED_STATUS, PLANNER_INVALID_STATUS})
_SKIPPED_VERIFICATION_STATUS = SKIPPED_VERIFICATION_STATUS
_VERIFICATION_STATUS_METADATA_FIELD = VERIFICATION_STATUS_METADATA_FIELD
COMPLETION_JUDGE_MISSING_CONFIG_REASON = f"{COMPLETION_JUDGE_UNAVAILABLE_REASON}: missing llm config"
WEB_APP_ROOT_PATH = "apps/web"
TEST_PATH_PREFIX = "tests/"
PYTHON_FILE_SUFFIX = ".py"
DELEGATED_REVIEW_PATH_SUFFIXES = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".cs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".php",
    ".rb",
    ".swift",
    ".sql",
    ".sh",
    ".ps1",
    ".bat",
    ".cmd",
)
DELEGATED_REVIEW_EXACT_PATHS = frozenset(
    {
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "vite.config.js",
        "vite.config.ts",
    }
)
WORKFLOW_FIX_STEPS = {
    IMPLEMENT_THEN_REVIEW_WORKFLOW_ID: {
        WORKFLOW_NEXT_STEP_ID_FIELD: "implement",
        WORKFLOW_NEXT_STEP_LABEL_FIELD: "Implement",
        WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD: "implementer",
    },
    BUGFIX_THEN_TEST_THEN_REVIEW_WORKFLOW_ID: {
        WORKFLOW_NEXT_STEP_ID_FIELD: "bugfix",
        WORKFLOW_NEXT_STEP_LABEL_FIELD: "Bug fix",
        WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD: "bug-fixer",
    },
}
WORKFLOW_VERIFICATION_EVIDENCE_MISSING_REASON = "workflow completed but required verification evidence is still missing"
WORKFLOW_REVIEW_EVIDENCE_MISSING_DETAIL = (
    "Run or rerun a delegated review step for the changed code before treating the workflow as complete."
)
TASK_REVIEW_EVIDENCE_MISSING_DETAIL = (
    "Run or rerun a delegated review step for the changed code before treating the task as complete."
)
TASK_REVIEW_FINDINGS_FOLLOW_UP_DETAIL = (
    "Address the delegated review findings before treating the task as complete."
)
OPTIONAL_WORKSPACE_BATCH_FAILURE_TOOL = BATCH_TOOL_NAME
_WEB_APP_ROOT_PATH = WEB_APP_ROOT_PATH
_WORKFLOW_COMPLETION_INTENT_KINDS = WORKFLOW_COMPLETION_INTENT_KINDS
COMPLETION_RESULT_SCHEMA_VERSION_FIELD = "schema_version"
COMPLETION_RESULT_STATUS_FIELD = "status"
COMPLETION_RESULT_REASON_FIELD = "reason"
COMPLETION_RESULT_SHOULD_UPDATE_ACTIVE_TASK_FIELD = "should_update_active_task"
COMPLETION_RESULT_VERIFICATION_REQUIRED_FIELD = "verification_required"
COMPLETION_RESULT_VERIFICATION_ATTEMPTED_FIELD = "verification_attempted"
COMPLETION_RESULT_VERIFICATION_PASSED_FIELD = "verification_passed"
COMPLETION_RESULT_REVIEW_REQUIRED_FIELD = "review_required"
COMPLETION_RESULT_REVIEW_ATTEMPTED_FIELD = "review_attempted"
COMPLETION_RESULT_REVIEW_PASSED_FIELD = "review_passed"
COMPLETION_RESULT_REVIEW_SUMMARY_FIELD = "review_summary"
COMPLETION_RESULT_REVIEW_PROMPT_TYPES_FIELD = "review_prompt_types"
COMPLETION_RESULT_REVIEW_FINDING_COUNT_FIELD = "review_finding_count"
COMPLETION_RESULT_FILE_CHANGE_REQUIRED_FIELD = "file_change_required"
COMPLETION_RESULT_MISSING_EVIDENCE_FIELD = "missing_evidence"
COMPLETION_RESULT_PROGRESS_ONLY_RESPONSE_FIELD = "progress_only_response"
COMPLETION_RESULT_ACTIVE_TASK_STATUS_FIELD = "active_task_status"
COMPLETION_RESULT_ACTIVE_TASK_DETAIL_FIELD = "active_task_detail"
COMPLETION_RESULT_FOLLOW_UP_WORKFLOW_FIELD = "follow_up_workflow"
COMPLETION_RESULT_FOLLOW_UP_STEP_ID_FIELD = "follow_up_step_id"
COMPLETION_RESULT_FOLLOW_UP_STEP_LABEL_FIELD = "follow_up_step_label"
COMPLETION_RESULT_FOLLOW_UP_PROMPT_TYPE_FIELD = "follow_up_prompt_type"
COMPLETION_RESULT_VERIFICATION_ACTION_FIELD = "verification_action"
COMPLETION_RESULT_VERIFICATION_PATH_FIELD = "verification_path"
COMPLETION_RESULT_VERIFICATION_PYTEST_ARGS_FIELD = "verification_pytest_args"
COMPLETION_RESULT_JUDGE_FIELD = "judge"
REVIEW_EVIDENCE_ATTEMPTED_FIELD = "attempted"
REVIEW_EVIDENCE_PASSED_FIELD = "passed"
REVIEW_EVIDENCE_SUMMARY_FIELD = "summary"
REVIEW_EVIDENCE_PROMPT_TYPES_FIELD = "prompt_types"
REVIEW_EVIDENCE_FINDING_COUNT_FIELD = "finding_count"
REVIEW_EVIDENCE_FIRST_FINDING_FIELD = "first_finding"
MAX_TOOL_ITERATIONS_INCOMPLETE_REASON = "max tool iterations exhausted before completion"
MAX_TOOL_ITERATIONS_ACTIVE_TASK_DETAIL = (
    "The execution loop hit the configured max_tool_iterations limit and needs another bounded continuation pass."
)
INTERNAL_ONLY_RESPONSE_INCOMPLETE_REASON = "assistant only emitted internal control text"
TOOL_ERROR_WITHOUT_BLOCKER_REASON = "tool execution reported an error without a clear blocker handoff"
PLAIN_ANSWER_CONTRACT_COMPLETE_REASON = "plain-answer contract received a response"
TASK_CONTRACT_ACCEPTED_FINAL_RESPONSE_REASON = "task contract accepted final response"
REQUIRED_FILE_CHANGES_AND_EVIDENCE_RECORDED_REASON = "required file changes and evidence were recorded"
ASSISTANT_RESPONSE_DID_NOT_COMPLETE_REASON = "assistant response did not explicitly complete the task"
GENERIC_TASK_COMPLETE_REASON = "generic task returned a response"
ANALYSIS_TASK_COMPLETE_REASON = "analysis-style task returned a substantive response"
EXPECTED_CODE_CHANGES_MISSING_REASON = "expected code changes were not recorded"
ONE_TURN_RESPONSE_COMPLETE_REASON = "one-turn intent received a response"
EMPTY_ASSISTANT_RESPONSE_REASON = "assistant response was empty"
TASK_CONTRACT_SATISFIED_REASON = "task contract was satisfied"
TASK_CONTRACT_PLANNER_UNVALIDATED_REASON = "task planner did not produce a validated contract"
DELEGATED_REVIEW_FINDINGS_REQUIRE_FOLLOW_UP_REASON = "delegated review reported findings that require follow-up"
DELEGATED_REVIEW_NOT_RECORDED_REASON = "delegated review was not recorded for code changes"
COMPLETION_GATE_DID_NOT_PASS_REASON = "completion gate did not pass"


def one_turn_completion_reason(*, has_response: bool) -> str:
    return ONE_TURN_RESPONSE_COMPLETE_REASON if has_response else EMPTY_ASSISTANT_RESPONSE_REASON


def delegated_review_completion_reason(*, review_attempted: bool) -> str:
    return DELEGATED_REVIEW_FINDINGS_REQUIRE_FOLLOW_UP_REASON if review_attempted else DELEGATED_REVIEW_NOT_RECORDED_REASON


def normalized_touched_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    normalized = [str(path or "").replace("\\", "/").strip("/") for path in paths]
    return tuple(path for path in normalized if path)


def is_web_app_path(path: str | None) -> bool:
    normalized = str(path or "").replace("\\", "/").strip("/")
    return normalized == WEB_APP_ROOT_PATH or normalized.startswith(f"{WEB_APP_ROOT_PATH}/")


def is_python_file_path(path: str | None) -> bool:
    return str(path or "").replace("\\", "/").strip("/").endswith(PYTHON_FILE_SUFFIX)


def is_python_test_path(path: str | None) -> bool:
    normalized = str(path or "").replace("\\", "/").strip("/")
    return normalized.startswith(TEST_PATH_PREFIX) and is_python_file_path(normalized)


def strip_repo_snapshot_prefix(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip("/")
    if normalized.startswith("repo/"):
        return normalized[5:]
    return normalized


def path_requires_delegated_review(path: str) -> bool:
    normalized = strip_repo_snapshot_prefix(path).lower()
    if normalized.endswith(DELEGATED_REVIEW_PATH_SUFFIXES):
        return True
    return normalized in DELEGATED_REVIEW_EXACT_PATHS


def common_verification_path(paths: tuple[str, ...]) -> str | None:
    if not paths:
        return None
    parts_list = [path.split("/") for path in paths if path]
    if not parts_list:
        return None
    common: list[str] = []
    for segments in zip(*parts_list):
        if len(set(segments)) != 1:
            break
        common.append(segments[0])
    if not common:
        return "."
    if len(common) == len(parts_list[0]) and not paths[0].endswith("/"):
        return "/".join(common[:-1]) or "."
    return "/".join(common) or "."


def workflow_unsuccessful_reason(workflow_id: str | None) -> str:
    return f"workflow {str(workflow_id or '').strip()} did not complete successfully"


def workflow_review_evidence_missing_reason(workflow_id: str | None) -> str:
    return f"workflow {str(workflow_id or '').strip()} completed but review evidence is missing"


def workflow_review_findings_follow_up_reason(workflow_id: str | None) -> str:
    return f"workflow {str(workflow_id or '').strip()} completed but review findings still require follow-up"


def workflow_clean_review_reason(workflow_id: str | None) -> str:
    return f"workflow {str(workflow_id or '').strip()} completed with clean review evidence"


def workflow_completed_all_steps_reason(workflow_id: str | None) -> str:
    return f"workflow {str(workflow_id or '').strip()} completed all required steps"


def workflow_review_evidence_missing_detail() -> str:
    return WORKFLOW_REVIEW_EVIDENCE_MISSING_DETAIL


def task_review_evidence_missing_detail() -> str:
    return TASK_REVIEW_EVIDENCE_MISSING_DETAIL


def task_review_findings_follow_up_detail() -> str:
    return TASK_REVIEW_FINDINGS_FOLLOW_UP_DETAIL


def is_research_then_outline_workflow(workflow_id: str | None) -> bool:
    return str(workflow_id or "").strip() == RESEARCH_THEN_OUTLINE_WORKFLOW_ID


def is_review_workflow(workflow_id: str | None) -> bool:
    return str(workflow_id or "").strip() in REVIEW_WORKFLOW_IDS


def workflow_review_follow_up_fields(workflow_id: str | None) -> dict[str, str]:
    if is_review_workflow(workflow_id):
        return {
            WORKFLOW_NEXT_STEP_ID_FIELD: "review",
            WORKFLOW_NEXT_STEP_LABEL_FIELD: "Code review",
            WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD: "code-reviewer",
        }
    return {}


def workflow_fix_follow_up_fields(workflow_id: str | None) -> dict[str, str]:
    return dict(WORKFLOW_FIX_STEPS.get(str(workflow_id or "").strip(), {}))


@dataclass(frozen=True)
class CompletionBlockerMessages:
    intro: str
    reason_prefix: str
    detail_header: str
    missing_evidence_header: str
    stop_notice: str


@dataclass(frozen=True)
class CompletionGateResult:
    """Structured verdict about whether one turn completed the active objective."""

    status: str
    reason: str
    active_task_status: str | None = None
    active_task_detail: str | None = None
    follow_up_workflow: str | None = None
    follow_up_step_id: str | None = None
    follow_up_step_label: str | None = None
    follow_up_prompt_type: str | None = None
    verification_action: str | None = None
    verification_path: str | None = None
    verification_pytest_args: tuple[str, ...] = ()
    should_update_active_task: bool = False
    verification_required: bool = False
    verification_attempted: bool = False
    verification_passed: bool = False
    review_required: bool = False
    review_attempted: bool = False
    review_passed: bool = False
    review_summary: str = ""
    review_prompt_types: tuple[str, ...] = ()
    review_finding_count: int = 0
    file_change_required: bool = False
    missing_evidence: tuple[str, ...] = ()
    progress_only_response: bool = False
    judge_metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe run event payload."""
        payload: dict[str, Any] = {
            COMPLETION_RESULT_SCHEMA_VERSION_FIELD: 1,
            COMPLETION_RESULT_STATUS_FIELD: self.status,
            COMPLETION_RESULT_REASON_FIELD: self.reason,
            COMPLETION_RESULT_SHOULD_UPDATE_ACTIVE_TASK_FIELD: self.should_update_active_task,
            COMPLETION_RESULT_VERIFICATION_REQUIRED_FIELD: self.verification_required,
            COMPLETION_RESULT_VERIFICATION_ATTEMPTED_FIELD: self.verification_attempted,
            COMPLETION_RESULT_VERIFICATION_PASSED_FIELD: self.verification_passed,
            COMPLETION_RESULT_REVIEW_REQUIRED_FIELD: self.review_required,
            COMPLETION_RESULT_REVIEW_ATTEMPTED_FIELD: self.review_attempted,
            COMPLETION_RESULT_REVIEW_PASSED_FIELD: self.review_passed,
            COMPLETION_RESULT_REVIEW_SUMMARY_FIELD: self.review_summary,
            COMPLETION_RESULT_REVIEW_PROMPT_TYPES_FIELD: list(self.review_prompt_types),
            COMPLETION_RESULT_REVIEW_FINDING_COUNT_FIELD: self.review_finding_count,
            COMPLETION_RESULT_FILE_CHANGE_REQUIRED_FIELD: self.file_change_required,
            COMPLETION_RESULT_MISSING_EVIDENCE_FIELD: list(self.missing_evidence),
            COMPLETION_RESULT_PROGRESS_ONLY_RESPONSE_FIELD: self.progress_only_response,
        }
        if self.active_task_status:
            payload[COMPLETION_RESULT_ACTIVE_TASK_STATUS_FIELD] = self.active_task_status
        if self.active_task_detail:
            payload[COMPLETION_RESULT_ACTIVE_TASK_DETAIL_FIELD] = self.active_task_detail
        if self.follow_up_workflow:
            payload[COMPLETION_RESULT_FOLLOW_UP_WORKFLOW_FIELD] = self.follow_up_workflow
        if self.follow_up_step_id:
            payload[COMPLETION_RESULT_FOLLOW_UP_STEP_ID_FIELD] = self.follow_up_step_id
        if self.follow_up_step_label:
            payload[COMPLETION_RESULT_FOLLOW_UP_STEP_LABEL_FIELD] = self.follow_up_step_label
        if self.follow_up_prompt_type:
            payload[COMPLETION_RESULT_FOLLOW_UP_PROMPT_TYPE_FIELD] = self.follow_up_prompt_type
        if self.verification_action:
            payload[COMPLETION_RESULT_VERIFICATION_ACTION_FIELD] = self.verification_action
        if self.verification_path:
            payload[COMPLETION_RESULT_VERIFICATION_PATH_FIELD] = self.verification_path
        if self.verification_pytest_args:
            payload[COMPLETION_RESULT_VERIFICATION_PYTEST_ARGS_FIELD] = list(self.verification_pytest_args)
        if self.judge_metadata:
            payload[COMPLETION_RESULT_JUDGE_FIELD] = dict(self.judge_metadata)
        return payload


def completion_blocker_response(
    completion_result: CompletionGateResult,
    messages: CompletionBlockerMessages,
) -> str:
    reason = (completion_result.reason or completion_result.status or COMPLETION_GATE_DID_NOT_PASS_REASON).strip()
    detail = (completion_result.active_task_detail or "").strip()
    missing = [item.strip() for item in completion_result.missing_evidence if str(item).strip()]
    sections = [
        messages.intro,
        f"{messages.reason_prefix}{reason}",
    ]
    if detail:
        detail_lines = [line.strip("- ").strip() for line in detail.splitlines() if line.strip()]
        if detail_lines:
            sections.append(f"{messages.detail_header}\n" + "\n".join(f"- {line}" for line in detail_lines))
    if missing:
        sections.append(f"{messages.missing_evidence_header}\n" + "\n".join(f"- {item}" for item in missing))
    sections.append(messages.stop_notice)
    return "\n\n".join(sections)


class CompletionGateService:
    """Evaluate completion without calling the LLM or continuing autonomously."""

    def __init__(
        self,
        *,
        llm_config: DocumentLlmConfig | None = None,
        judge_service: CompletionJudgeService | None = None,
        evidence_gate: EvidenceGateService | None = None,
        quality_gate: QualityGateService | None = None,
    ):
        self.llm_config = llm_config
        self.judge_service = judge_service or (CompletionJudgeService(llm_config) if llm_config is not None else None)
        self.evidence_gate = evidence_gate or EvidenceGateService()
        self.quality_gate = quality_gate or QualityGateService()

    async def evaluate_with_judge(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
        provider: Any,
        model: str | None,
    ) -> CompletionGateResult:
        """Return the LLM judge verdict for the current turn."""
        judge = self.judge_service
        if judge is None:
            return _completion_judge_blocked_result(COMPLETION_JUDGE_MISSING_CONFIG_REASON)
        facts = build_completion_judge_facts(
            task_intent=task_intent,
            response_text=response_text,
            execution_result=execution_result,
        )
        try:
            verdict = await judge.judge(provider=provider, model=model, facts=facts)
        except CompletionJudgeError as exc:
            return _completion_judge_blocked_result(str(exc))
        except Exception as exc:
            return _completion_judge_blocked_result(f"completion judge failed: {type(exc).__name__}")
        result = _completion_result_from_judge_verdict(verdict, execution_result=execution_result)
        evidence_result = self.evidence_gate.evaluate(
            task_intent=task_intent,
            execution_result=execution_result,
            verification_passed=result.verification_passed,
        )
        if is_complete_completion_status(result.status) and not evidence_result.passed:
            return CompletionGateResult(
                status=INCOMPLETE_COMPLETION_STATUS,
                reason=evidence_result.reason,
                active_task_detail=evidence_result.active_task_detail,
                verification_required=result.verification_required,
                verification_attempted=result.verification_attempted,
                verification_passed=result.verification_passed,
                review_required=result.review_required,
                review_attempted=result.review_attempted,
                review_passed=result.review_passed,
                review_summary=result.review_summary,
                review_prompt_types=result.review_prompt_types,
                review_finding_count=result.review_finding_count,
                missing_evidence=evidence_result.missing_evidence,
                progress_only_response=result.progress_only_response,
                judge_metadata=result.judge_metadata,
            )
        if (
            result.status == BLOCKED_COMPLETION_STATUS
            and not evidence_result.passed
            and execution_result.executed_tool_calls <= 0
            and not execution_result.had_tool_error
        ):
            return CompletionGateResult(
                status=INCOMPLETE_COMPLETION_STATUS,
                reason=evidence_result.reason,
                active_task_detail=evidence_result.active_task_detail,
                verification_required=result.verification_required,
                verification_attempted=result.verification_attempted,
                verification_passed=result.verification_passed,
                review_required=result.review_required,
                review_attempted=result.review_attempted,
                review_passed=result.review_passed,
                review_summary=result.review_summary,
                review_prompt_types=result.review_prompt_types,
                review_finding_count=result.review_finding_count,
                missing_evidence=evidence_result.missing_evidence,
                progress_only_response=result.progress_only_response,
                judge_metadata=result.judge_metadata,
            )
        return result

    def evaluate(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
    ) -> CompletionGateResult:
        """Return the safest completion verdict for the current turn."""
        contract_allows_plain_answer = _contract_allows_plain_answer(execution_result.task_contract)
        verification_required = False if contract_allows_plain_answer else _requires_verification(execution_result.task_contract)
        expects_code_change = (
            False
            if contract_allows_plain_answer or _contract_is_read_only(execution_result.task_contract)
            else contract_expects_file_change(execution_result.task_contract) or execution_result.file_change_count > 0
        )
        verification_attempted = execution_result.verification_attempted
        verification_passed = execution_result.verification_passed or _verification_skipped_with_reported_gap(execution_result)
        verification_follow_up = _verification_follow_up(execution_result)
        review = _review_evidence(execution_result.delegated_tasks)
        review_required = (
            expects_code_change
            and execution_result.file_change_count > 0
            and _requires_delegated_review(execution_result.touched_paths)
        )
        workflow_gate = _workflow_gate_outcome(
            task_intent=task_intent,
            workflow_outcomes=execution_result.workflow_outcomes,
            verification_required=verification_required,
            verification_attempted=verification_attempted,
            verification_passed=verification_passed,
        )

        if execution_result.assistant_internal_only_response:
            return CompletionGateResult(
                status=INCOMPLETE_COMPLETION_STATUS,
                reason=INTERNAL_ONLY_RESPONSE_INCOMPLETE_REASON,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
                file_change_required=True,
            )

        planner_status = _task_planner_status(execution_result.task_contract)
        if _is_blocking_planner_status(planner_status):
            reason = TASK_CONTRACT_PLANNER_UNVALIDATED_REASON
            detail = _task_planner_reason(execution_result.task_contract) or reason
            return CompletionGateResult(
                status=BLOCKED_COMPLETION_STATUS,
                reason=reason,
                active_task_status=BLOCKED_ACTIVE_TASK_STATUS,
                active_task_detail=detail,
                should_update_active_task=_intent_supports_fallback_active_task_update(
                    task_intent,
                    execution_result.task_contract,
                ),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if is_max_tool_iterations_stop_reason(execution_result.stop_reason):
            return CompletionGateResult(
                status=INCOMPLETE_COMPLETION_STATUS,
                reason=MAX_TOOL_ITERATIONS_INCOMPLETE_REASON,
                active_task_detail=MAX_TOOL_ITERATIONS_ACTIVE_TASK_DETAIL,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if execution_result.had_tool_error:
            if verification_required and verification_attempted and not verification_passed:
                return CompletionGateResult(
                    status=NEEDS_VERIFICATION_COMPLETION_STATUS,
                    reason=REQUIRED_VERIFICATION_FAILED_REASON,
                    verification_required=True,
                    verification_attempted=True,
                    verification_passed=False,
                    verification_action=verification_follow_up["action"],
                    verification_path=verification_follow_up["path"],
                    verification_pytest_args=verification_follow_up["pytest_args"],
                    review_required=review_required,
                    review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                    review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                    review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                    review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                    review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
                )
            if not self._tool_errors_are_non_blocking(
                task_intent=task_intent,
                response_text=response_text,
                execution_result=execution_result,
                verification_passed=verification_passed,
            ):
                return CompletionGateResult(
                    status=INCOMPLETE_COMPLETION_STATUS,
                    reason=TOOL_ERROR_WITHOUT_BLOCKER_REASON,
                    verification_required=verification_required,
                    verification_attempted=verification_attempted,
                    verification_passed=verification_passed,
                    review_required=review_required,
                    review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                    review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                    review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                    review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                    review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
                )

        if workflow_gate is not None:
            workflow_verification_attempted = bool(workflow_gate.get(WORKFLOW_VERIFICATION_ATTEMPTED_FIELD, verification_attempted))
            workflow_verification_passed = bool(workflow_gate.get(WORKFLOW_VERIFICATION_PASSED_FIELD, verification_passed))
            workflow_review_attempted = bool(workflow_gate.get(WORKFLOW_REVIEW_ATTEMPTED_FIELD, review[REVIEW_EVIDENCE_ATTEMPTED_FIELD]))
            workflow_review_passed = bool(workflow_gate.get(WORKFLOW_REVIEW_PASSED_FIELD, review[REVIEW_EVIDENCE_PASSED_FIELD]))
            workflow_review_summary = str(workflow_gate.get(WORKFLOW_REVIEW_SUMMARY_FIELD) or review[REVIEW_EVIDENCE_SUMMARY_FIELD] or "").strip()
            workflow_review_finding_count = int(workflow_gate.get(WORKFLOW_REVIEW_FINDING_COUNT_FIELD, review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD]))
            return CompletionGateResult(
                status=workflow_gate[COMPLETION_RESULT_STATUS_FIELD],
                reason=workflow_gate[COMPLETION_RESULT_REASON_FIELD],
                active_task_status=DONE_ACTIVE_TASK_STATUS if _workflow_gate_is_complete(workflow_gate) else None,
                active_task_detail=workflow_gate.get("detail") or None,
                follow_up_workflow=_string_or_none(workflow_gate.get(WORKFLOW_ID_FIELD)),
                follow_up_step_id=_string_or_none(workflow_gate.get(WORKFLOW_NEXT_STEP_ID_FIELD)),
                follow_up_step_label=_string_or_none(workflow_gate.get(WORKFLOW_NEXT_STEP_LABEL_FIELD)),
                follow_up_prompt_type=_string_or_none(workflow_gate.get(WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD)),
                should_update_active_task=_workflow_gate_is_complete(workflow_gate)
                and _intent_supports_fallback_active_task_update(task_intent, execution_result.task_contract),
                verification_required=verification_required,
                verification_attempted=workflow_verification_attempted,
                verification_passed=workflow_verification_passed,
                verification_action=verification_follow_up["action"]
                if _workflow_gate_needs_verification(workflow_gate)
                else None,
                verification_path=verification_follow_up["path"]
                if _workflow_gate_needs_verification(workflow_gate)
                else None,
                verification_pytest_args=verification_follow_up["pytest_args"]
                if _workflow_gate_needs_verification(workflow_gate)
                else (),
                review_required=review_required,
                review_attempted=workflow_review_attempted,
                review_passed=workflow_review_passed,
                review_summary=workflow_review_summary,
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=workflow_review_finding_count,
            )

        if (
            contract_allows_plain_answer
            and not _contract_has_completion_criteria(execution_result.task_contract)
            and response_text.strip()
        ):
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=PLAIN_ANSWER_CONTRACT_COMPLETE_REASON,
                active_task_status=(
                    DONE_ACTIVE_TASK_STATUS
                    if _intent_supports_fallback_active_task_update(task_intent, execution_result.task_contract)
                    else None
                ),
                should_update_active_task=_intent_supports_fallback_active_task_update(
                    task_intent,
                    execution_result.task_contract,
                ),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if expects_code_change and execution_result.file_change_count <= 0:
            return CompletionGateResult(
                status=INCOMPLETE_COMPLETION_STATUS,
                reason=EXPECTED_CODE_CHANGES_MISSING_REASON,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=False,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if verification_required and not verification_passed:
            reason = required_verification_completion_reason(verification_attempted=verification_attempted)
            return CompletionGateResult(
                status=NEEDS_VERIFICATION_COMPLETION_STATUS,
                reason=reason,
                verification_required=True,
                verification_attempted=verification_attempted,
                verification_passed=False,
                verification_action=verification_follow_up["action"],
                verification_path=verification_follow_up["path"],
                verification_pytest_args=verification_follow_up["pytest_args"],
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if review_required and not review[REVIEW_EVIDENCE_PASSED_FIELD]:
            reason = delegated_review_completion_reason(review_attempted=bool(review[REVIEW_EVIDENCE_ATTEMPTED_FIELD]))
            return CompletionGateResult(
                status=NEEDS_REVIEW_COMPLETION_STATUS,
                reason=reason,
                active_task_detail=_review_follow_up_detail(review),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=True,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=False,
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        evidence_result = self.evidence_gate.evaluate(
            task_intent=task_intent,
            execution_result=execution_result,
            verification_passed=verification_passed,
        )
        if not evidence_result.passed:
            return CompletionGateResult(
                status=INCOMPLETE_COMPLETION_STATUS,
                reason=evidence_result.reason,
                active_task_detail=evidence_result.active_task_detail,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
                missing_evidence=evidence_result.missing_evidence,
            )

        quality_result = self.quality_gate.evaluate(
            task_intent=task_intent,
            response_text=response_text,
            execution_result=execution_result,
            task_contract=evidence_result.task_contract,
        )
        if not quality_result.passed:
            return CompletionGateResult(
                status=quality_result.status,
                reason=quality_result.reason,
                active_task_detail=quality_result.active_task_detail,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if _contract_accepts_final_response(evidence_result.task_contract) and response_text.strip():
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=TASK_CONTRACT_ACCEPTED_FINAL_RESPONSE_REASON,
                active_task_status=DONE_ACTIVE_TASK_STATUS,
                should_update_active_task=True,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if _is_one_turn_intent_kind(task_intent.kind):
            has_response = bool(response_text.strip())
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS if has_response else INCOMPLETE_COMPLETION_STATUS,
                reason=one_turn_completion_reason(has_response=has_response),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if _is_analysis_response_intent_kind(task_intent.kind) and response_text.strip():
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=ANALYSIS_TASK_COMPLETE_REASON,
                active_task_status=DONE_ACTIVE_TASK_STATUS,
                should_update_active_task=_intent_supports_fallback_active_task_update(
                    task_intent,
                    evidence_result.task_contract,
                ),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if _is_generic_task_response_intent_kind(task_intent.kind) and not expects_code_change and response_text.strip():
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=GENERIC_TASK_COMPLETE_REASON,
                active_task_status=(
                    DONE_ACTIVE_TASK_STATUS
                    if _intent_supports_fallback_active_task_update(task_intent, evidence_result.task_contract)
                    else None
                ),
                should_update_active_task=_intent_supports_fallback_active_task_update(
                    task_intent,
                    evidence_result.task_contract,
                ),
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if _contract_has_completion_criteria(evidence_result.task_contract) and response_text.strip():
            should_update_active_task = _intent_supports_fallback_active_task_update(
                task_intent,
                evidence_result.task_contract,
            )
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=TASK_CONTRACT_SATISFIED_REASON,
                active_task_status=DONE_ACTIVE_TASK_STATUS if should_update_active_task else None,
                should_update_active_task=should_update_active_task,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        if (
            expects_code_change
            and execution_result.file_change_count > 0
            and response_text.strip()
            and (not verification_required or verification_passed)
            and (not review_required or review[REVIEW_EVIDENCE_PASSED_FIELD])
        ):
            should_update_active_task = not task_intent.needs_clarification
            return CompletionGateResult(
                status=COMPLETE_COMPLETION_STATUS,
                reason=REQUIRED_FILE_CHANGES_AND_EVIDENCE_RECORDED_REASON,
                active_task_status=DONE_ACTIVE_TASK_STATUS if should_update_active_task else None,
                should_update_active_task=should_update_active_task,
                verification_required=verification_required,
                verification_attempted=verification_attempted,
                verification_passed=verification_passed,
                review_required=review_required,
                review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
                review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
                review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
                review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
                review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
            )

        return CompletionGateResult(
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=ASSISTANT_RESPONSE_DID_NOT_COMPLETE_REASON,
            verification_required=verification_required,
            verification_attempted=verification_attempted,
            verification_passed=verification_passed,
            review_required=review_required,
            review_attempted=review[REVIEW_EVIDENCE_ATTEMPTED_FIELD],
            review_passed=review[REVIEW_EVIDENCE_PASSED_FIELD],
            review_summary=review[REVIEW_EVIDENCE_SUMMARY_FIELD],
            review_prompt_types=review[REVIEW_EVIDENCE_PROMPT_TYPES_FIELD],
            review_finding_count=review[REVIEW_EVIDENCE_FINDING_COUNT_FIELD],
        )

    def _tool_errors_are_non_blocking(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
        verification_passed: bool,
    ) -> bool:
        """Allow exploratory discovery failures after required evidence is satisfied."""
        has_optional_web_failures = _has_only_optional_web_discovery_failures(execution_result)
        has_optional_workspace_failures = _has_only_optional_workspace_discovery_failures(execution_result)
        has_optional_history_failures = _has_only_optional_history_retrieval_failures(execution_result)
        if not (has_optional_web_failures or has_optional_workspace_failures or has_optional_history_failures):
            return False

        evidence_result = self.evidence_gate.evaluate(
            task_intent=task_intent,
            execution_result=execution_result,
            verification_passed=verification_passed,
        )
        if not evidence_result.passed:
            return False

        if has_optional_web_failures and not _has_successful_fetched_web_source_artifact(execution_result):
            return False

        quality_result = self.quality_gate.evaluate(
            task_intent=task_intent,
            response_text=response_text,
            execution_result=execution_result,
            task_contract=evidence_result.task_contract,
        )
        return quality_result.passed


def _completion_result_from_judge_verdict(
    verdict: CompletionJudgeVerdict,
    *,
    execution_result: ExecutionResult,
) -> CompletionGateResult:
    file_change_required = (
        contract_expects_file_change(execution_result.task_contract)
        and execution_result.file_change_count <= 0
    )
    status = verdict.status
    active_task_status = verdict.active_task_status
    should_update_active_task = bool(active_task_status)
    if verdict.review_required and not verdict.review_passed and is_complete_completion_status(status):
        status = NEEDS_REVIEW_COMPLETION_STATUS
        active_task_status = None
        should_update_active_task = False
    return CompletionGateResult(
        status=status,
        reason=verdict.reason,
        active_task_status=active_task_status,
        active_task_detail=verdict.active_task_detail,
        follow_up_workflow=verdict.follow_up_workflow,
        follow_up_step_id=verdict.follow_up_step_id,
        follow_up_step_label=verdict.follow_up_step_label,
        follow_up_prompt_type=verdict.follow_up_prompt_type,
        verification_action=verdict.verification_action,
        verification_path=verdict.verification_path,
        verification_pytest_args=verdict.verification_pytest_args,
        should_update_active_task=should_update_active_task,
        verification_required=verdict.verification_required,
        verification_attempted=verdict.verification_attempted,
        verification_passed=verdict.verification_passed,
        review_required=verdict.review_required,
        review_attempted=verdict.review_attempted,
        review_passed=verdict.review_passed,
        review_summary=verdict.review_summary,
        review_prompt_types=verdict.review_prompt_types,
        review_finding_count=verdict.review_finding_count,
        file_change_required=file_change_required,
        missing_evidence=verdict.missing_evidence,
        progress_only_response=verdict.progress_only_response,
        judge_metadata={
            **dict(verdict.metadata),
            "raw_response_preview": verdict.raw_response_preview,
        },
    )


def _completion_judge_blocked_result(reason: str) -> CompletionGateResult:
    detail = reason or COMPLETION_JUDGE_UNAVAILABLE_REASON
    return CompletionGateResult(
        status=BLOCKED_COMPLETION_STATUS,
        reason=detail,
        active_task_status=BLOCKED_ACTIVE_TASK_STATUS,
        active_task_detail=detail,
        should_update_active_task=True,
        judge_metadata={
            "method": "llm",
            "error": detail,
        },
    )


def _intent_supports_fallback_active_task_update(task_intent: TaskIntent, task_contract: Any) -> bool:
    return intent_supports_fallback_active_task_update(task_intent, task_contract)


def _requires_verification(task_contract: Any) -> bool:
    if _contract_requires_verification(task_contract):
        return True
    task_type = str(getattr(task_contract, "task_type", "") or "")
    if _is_read_only_task_type(task_type):
        return False
    return False


def _verification_skipped_with_reported_gap(execution_result: ExecutionResult) -> bool:
    if not execution_result.verification_attempted:
        return False
    if not _requires_delegated_review(execution_result.touched_paths) and not execution_result.had_tool_error:
        return True
    if not _has_skipped_verification_artifact(execution_result):
        return False
    return True


def _has_skipped_verification_artifact(execution_result: ExecutionResult) -> bool:
    for artifact in execution_result.task_artifacts:
        if not _is_verification_result_artifact_kind(artifact.kind) or not artifact.ok:
            continue
        if _verification_status_is_skipped(artifact.metadata):
            return True
    for evidence in execution_result.tool_evidence:
        if not _is_verification_tool(evidence.name) or not evidence.ok:
            continue
        if _verification_status_is_skipped(evidence.metadata):
            return True
    return False


def _verification_status_is_skipped(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    return str(metadata.get(_VERIFICATION_STATUS_METADATA_FIELD) or "").strip().lower() == _SKIPPED_VERIFICATION_STATUS


def _requires_delegated_review(touched_paths: tuple[str, ...]) -> bool:
    paths = _normalized_touched_paths(touched_paths)
    if not paths:
        return True
    return any(_path_requires_delegated_review(path) for path in paths)


def _path_requires_delegated_review(path: str) -> bool:
    return path_requires_delegated_review(path)


def _contract_requires_verification(task_contract: Any) -> bool:
    return any(
        _is_verification_requirement_kind(str(getattr(requirement, "kind", "") or ""))
        or _is_verification_tool_group(str(getattr(requirement, "tool_group", "") or ""))
        for requirement in getattr(task_contract, "requirements", ()) or ()
    )


def _contract_allows_plain_answer(task_contract: Any) -> bool:
    return bool(
        task_contract is not None
        and _is_plain_answer_task_type(getattr(task_contract, "task_type", None))
        and getattr(task_contract, "allow_no_tool_final", False)
        and not tuple(getattr(task_contract, "requirements", ()) or ())
    )


def _contract_is_read_only(task_contract: Any) -> bool:
    task_type = str(getattr(task_contract, "task_type", "") or "")
    if _is_read_only_task_type(task_type):
        return True
    for requirement in getattr(task_contract, "requirements", ()) or ():
        if _is_read_only_blocking_requirement_kind(str(getattr(requirement, "kind", "") or "")):
            return False
        tool_group = str(getattr(requirement, "tool_group", "") or "")
        if _is_read_only_blocking_tool_group(tool_group):
            return False
    return False


def _is_read_only_task_type(task_type: str | None) -> bool:
    return is_read_only_task_type(task_type)


def _is_plain_answer_task_type(task_type: str | None) -> bool:
    return is_plain_answer_task_type(task_type)


def _is_one_turn_intent_kind(kind: str | None) -> bool:
    return is_one_turn_intent_kind(kind)


def _is_analysis_response_intent_kind(kind: str | None) -> bool:
    return is_analysis_response_intent_kind(kind)


def _is_generic_task_response_intent_kind(kind: str | None) -> bool:
    return is_generic_task_response_intent_kind(kind)


def _is_read_only_blocking_requirement_kind(kind: str | None) -> bool:
    return is_read_only_blocking_requirement_kind(kind)


def _is_read_only_blocking_tool_group(tool_group: str | None) -> bool:
    return is_read_only_blocking_tool_group(tool_group)


def _task_planner_status(task_contract: Any) -> str:
    metadata = getattr(task_contract, "planner_metadata", None) or {}
    if isinstance(metadata, dict):
        return str(metadata.get(PLANNER_METADATA_STATUS_FIELD) or "").strip()
    return ""


def _is_blocking_planner_status(status: str | None) -> bool:
    return str(status or "").strip().lower() in _BLOCKING_PLANNER_STATUSES


def _task_planner_reason(task_contract: Any) -> str:
    metadata = getattr(task_contract, "planner_metadata", None) or {}
    if isinstance(metadata, dict):
        return str(metadata.get(PLANNER_METADATA_REASON_FIELD) or "").strip()
    return ""


def has_only_optional_web_discovery_failures(execution_result: ExecutionResult) -> bool:
    failed_evidence = tuple(item for item in execution_result.tool_evidence if not item.ok)
    if not failed_evidence:
        return False
    has_successful_fetch_sources = has_successful_fetched_web_source_artifact(execution_result)
    for item in failed_evidence:
        if is_optional_web_discovery_failure_tool(item.name):
            continue
        if is_optional_web_fetch_failure_tool(item.name) and has_successful_fetch_sources:
            continue
        if tool_failure_is_non_exposed_permission_block(item.metadata):
            continue
        return False
    return True


def has_only_optional_workspace_discovery_failures(execution_result: ExecutionResult) -> bool:
    failed_evidence = tuple(item for item in execution_result.tool_evidence if not item.ok)
    if not failed_evidence:
        return False
    if not any(item.ok and item.name in WORKSPACE_DISCOVERY_TOOLS for item in execution_result.tool_evidence):
        return False
    for item in failed_evidence:
        if item.name in WORKSPACE_DISCOVERY_TOOLS:
            continue
        if is_optional_workspace_batch_failure_tool(item.name) and execution_result.file_change_count <= 0:
            continue
        if tool_failure_is_non_exposed_permission_block(item.metadata):
            continue
        return False
    return True


def has_only_optional_history_retrieval_failures(execution_result: ExecutionResult) -> bool:
    failed_evidence = tuple(item for item in execution_result.tool_evidence if not item.ok)
    if not failed_evidence:
        return False
    if not any(item.ok and is_history_retrieval_tool_name(item.name) for item in execution_result.tool_evidence):
        return False
    for item in failed_evidence:
        if is_history_retrieval_tool_name(item.name):
            continue
        if tool_failure_is_non_exposed_permission_block(item.metadata):
            continue
        return False
    return True


def has_successful_fetched_web_source_artifact(execution_result: ExecutionResult) -> bool:
    for artifact in execution_result.task_artifacts:
        if not is_web_source_artifact_kind(artifact.kind) or not artifact.ok:
            continue
        sources = artifact.metadata.get("sources") if isinstance(artifact.metadata, dict) else None
        if is_fetched_web_source_artifact_tool(artifact.source_tool) and isinstance(sources, list) and sources:
            return True
        if (
            is_web_research_source_artifact_tool(artifact.source_tool)
            and web_research_artifact_has_successful_fetch(artifact)
        ):
            return True
    return False


def tool_failure_is_non_exposed_permission_block(metadata: Any) -> bool:
    permission = metadata.get("permission") if isinstance(metadata, dict) else None
    return bool(
        isinstance(permission, dict)
        and permission.get("blocked") is True
        and permission.get("exposed") is False
    )


def is_optional_web_discovery_failure_tool(tool_name: str | None) -> bool:
    return is_web_discovery_tool(tool_name)


def is_optional_web_fetch_failure_tool(tool_name: str | None) -> bool:
    return is_web_fetch_source_record_tool(tool_name)


def is_optional_workspace_batch_failure_tool(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() == OPTIONAL_WORKSPACE_BATCH_FAILURE_TOOL


def is_history_retrieval_failure_tool(tool_name: str | None) -> bool:
    return is_history_retrieval_tool_name(tool_name)


def web_research_artifact_has_successful_fetch(artifact: TaskArtifact) -> bool:
    metadata = artifact.metadata if isinstance(artifact.metadata, dict) else {}
    coverage = metadata.get("coverage") if isinstance(metadata.get("coverage"), dict) else {}
    if int(coverage.get("fetched_count") or 0) > 0:
        return True
    sources = metadata.get("sources")
    if not isinstance(sources, list):
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        if not is_web_fetch_source_record_tool(source.get("tool_name")):
            continue
        if source.get("blocked_or_challenge") or source.get("is_too_short"):
            continue
        if int(source.get("content_chars") or 0) > 0 or source.get("has_main_content"):
            return True
    return False


def _has_only_optional_web_discovery_failures(execution_result: ExecutionResult) -> bool:
    return has_only_optional_web_discovery_failures(execution_result)


def _has_only_optional_workspace_discovery_failures(execution_result: ExecutionResult) -> bool:
    return has_only_optional_workspace_discovery_failures(execution_result)


def _has_only_optional_history_retrieval_failures(execution_result: ExecutionResult) -> bool:
    return has_only_optional_history_retrieval_failures(execution_result)


def _requires_web_research_evidence(task_contract: Any) -> bool:
    if is_web_research_task_type(getattr(task_contract, "task_type", None)):
        return True
    return any(
        is_web_research_tool_group(getattr(requirement, "tool_group", None))
        for requirement in getattr(task_contract, "requirements", ())
    )


def _has_successful_fetched_web_source_artifact(execution_result: ExecutionResult) -> bool:
    return has_successful_fetched_web_source_artifact(execution_result)


def _is_optional_web_discovery_failure_tool(tool_name: str | None) -> bool:
    return is_optional_web_discovery_failure_tool(tool_name)


def _is_optional_web_fetch_failure_tool(tool_name: str | None) -> bool:
    return is_optional_web_fetch_failure_tool(tool_name)


def _is_optional_workspace_batch_failure_tool(tool_name: str | None) -> bool:
    return is_optional_workspace_batch_failure_tool(tool_name)


def _is_history_retrieval_tool(tool_name: str | None) -> bool:
    return is_history_retrieval_failure_tool(tool_name)


def _is_verification_result_artifact_kind(kind: str | None) -> bool:
    return is_verification_result_artifact_kind(kind)


def _is_verification_tool(tool_name: str | None) -> bool:
    return is_verification_tool_name(tool_name)


def _is_verification_requirement_kind(kind: str | None) -> bool:
    return str(kind or "").strip() == VERIFICATION_REQUIREMENT_KIND


def _is_verification_tool_group(tool_group: str | None) -> bool:
    return str(tool_group or "").strip() == VERIFICATION_TOOL_GROUP


def _web_research_artifact_has_successful_fetch(artifact: TaskArtifact) -> bool:
    return web_research_artifact_has_successful_fetch(artifact)


def _contract_has_completion_criteria(task_contract: Any) -> bool:
    return bool(getattr(task_contract, "requirements", ()) or getattr(task_contract, "acceptance_criteria", ()))


def _contract_accepts_final_response(task_contract: Any) -> bool:
    if task_contract is None or _contract_has_completion_criteria(task_contract):
        return False
    if not bool(getattr(task_contract, "final_answer_required", True)):
        return False
    if not bool(getattr(task_contract, "allow_no_tool_final", False)):
        return False
    task_type = str(getattr(task_contract, "task_type", "") or "").strip()
    return _accepts_final_response_task_type(task_type)


def _accepts_final_response_task_type(task_type: str | None) -> bool:
    return accepts_final_response_task_type(task_type)


def _review_evidence(delegated_tasks: tuple[StoredDelegatedTask, ...]) -> dict[str, Any]:
    prompt_types: list[str] = []
    finding_count = 0
    attempted = False
    clean_review_recorded = False
    problematic_review_recorded = False
    summary = ""
    first_finding = ""
    for task in delegated_tasks:
        prompt_type = str(task.prompt_type or "").strip()
        if prompt_type not in _REVIEW_PROMPT_TYPES:
            continue
        prompt_types.append(prompt_type)
        if not _is_completed_delegated_review_status(task.status):
            continue
        attempted = True
        structured = task.metadata.get("structured_output") if isinstance(task.metadata, dict) else None
        structured_status = str((structured or {}).get(STRUCTURED_SUBAGENT_STATUS_FIELD) or "").strip()
        task_findings = int((structured or {}).get(STRUCTURED_SUBAGENT_FINDING_COUNT_FIELD) or 0)
        finding_count += max(0, task_findings)
        task_summary = str((structured or {}).get(STRUCTURED_SUBAGENT_SUMMARY_FIELD) or task.summary or "").strip()
        if task_summary and not summary:
            summary = task_summary
        if not first_finding:
            first_finding = _first_review_finding(structured)
        if _is_clean_structured_review_status(structured_status) and task_findings == 0:
            clean_review_recorded = True
            continue
        problematic_review_recorded = True
    return {
        REVIEW_EVIDENCE_ATTEMPTED_FIELD: attempted,
        REVIEW_EVIDENCE_PASSED_FIELD: attempted and clean_review_recorded and not problematic_review_recorded and finding_count == 0,
        REVIEW_EVIDENCE_SUMMARY_FIELD: summary,
        REVIEW_EVIDENCE_PROMPT_TYPES_FIELD: tuple(dict.fromkeys(prompt_types)),
        REVIEW_EVIDENCE_FINDING_COUNT_FIELD: finding_count,
        REVIEW_EVIDENCE_FIRST_FINDING_FIELD: first_finding,
    }


def _workflow_gate_outcome(
    *,
    task_intent: TaskIntent,
    workflow_outcomes: tuple[dict[str, Any], ...],
    verification_required: bool,
    verification_attempted: bool,
    verification_passed: bool,
) -> dict[str, Any] | None:
    relevant_outcomes = [
        outcome
        for outcome in workflow_outcomes
        if isinstance(outcome, dict) and str(outcome.get(WORKFLOW_ID_FIELD) or "").strip()
    ]
    if not relevant_outcomes:
        return None
    workflow = relevant_outcomes[-1]
    workflow_id = str(workflow.get(WORKFLOW_ID_FIELD) or "").strip()
    workflow_status = str(workflow.get(WORKFLOW_STATUS_FIELD) or "").strip()
    review_attempted = bool(workflow.get(WORKFLOW_REVIEW_ATTEMPTED_FIELD))
    review_passed = bool(workflow.get(WORKFLOW_REVIEW_PASSED_FIELD))
    review_finding_count = int(workflow.get(WORKFLOW_REVIEW_FINDING_COUNT_FIELD) or 0)
    workflow_verification_attempted = bool(workflow.get(WORKFLOW_VERIFICATION_ATTEMPTED_FIELD))
    workflow_verification_passed = bool(workflow.get(WORKFLOW_VERIFICATION_PASSED_FIELD))
    workflow_review_summary = str(workflow.get(WORKFLOW_REVIEW_SUMMARY_FIELD) or "").strip()
    workflow_review_first_finding = str(workflow.get(WORKFLOW_REVIEW_FIRST_FINDING_FIELD) or "").strip()
    next_step_id = str(workflow.get(WORKFLOW_NEXT_STEP_ID_FIELD) or "").strip()
    next_step_label = str(workflow.get(WORKFLOW_NEXT_STEP_LABEL_FIELD) or "").strip()
    next_step_prompt_type = str(workflow.get(WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD) or "").strip()
    metadata = {
        WORKFLOW_ID_FIELD: workflow_id,
        WORKFLOW_REVIEW_ATTEMPTED_FIELD: review_attempted,
        WORKFLOW_REVIEW_PASSED_FIELD: review_passed,
        WORKFLOW_REVIEW_FINDING_COUNT_FIELD: review_finding_count,
        WORKFLOW_REVIEW_SUMMARY_FIELD: workflow_review_summary,
        WORKFLOW_VERIFICATION_ATTEMPTED_FIELD: workflow_verification_attempted,
        WORKFLOW_VERIFICATION_PASSED_FIELD: workflow_verification_passed,
        **(
            {
                WORKFLOW_NEXT_STEP_ID_FIELD: next_step_id,
                WORKFLOW_NEXT_STEP_LABEL_FIELD: next_step_label,
                WORKFLOW_NEXT_STEP_PROMPT_TYPE_FIELD: next_step_prompt_type,
            }
            if next_step_id or next_step_label or next_step_prompt_type
            else {}
        ),
    }

    if _is_unsuccessful_workflow_status(workflow_status):
        detail = _workflow_follow_up_detail(workflow_id, workflow_status, workflow)
        return {
            **metadata,
            COMPLETION_RESULT_STATUS_FIELD: _completion_status_for_unsuccessful_workflow(workflow_status),
            COMPLETION_RESULT_REASON_FIELD: workflow_unsuccessful_reason(workflow_id),
            "detail": detail,
        }

    if _is_research_then_outline_workflow(workflow_id):
        return {
            **metadata,
            COMPLETION_RESULT_STATUS_FIELD: COMPLETE_COMPLETION_STATUS,
            COMPLETION_RESULT_REASON_FIELD: workflow_completed_all_steps_reason(workflow_id),
        }

    if _is_review_workflow(workflow_id):
        if not review_attempted:
            review_step = _workflow_review_follow_up_fields(workflow_id)
            return {
                **metadata,
                COMPLETION_RESULT_STATUS_FIELD: NEEDS_REVIEW_COMPLETION_STATUS,
                COMPLETION_RESULT_REASON_FIELD: workflow_review_evidence_missing_reason(workflow_id),
                "detail": workflow_review_evidence_missing_detail(),
                **review_step,
            }
        if not review_passed or review_finding_count > 0:
            fix_step = _workflow_fix_follow_up_fields(workflow_id)
            return {
                **metadata,
                COMPLETION_RESULT_STATUS_FIELD: NEEDS_REVIEW_COMPLETION_STATUS,
                COMPLETION_RESULT_REASON_FIELD: workflow_review_findings_follow_up_reason(workflow_id),
                "detail": workflow_review_first_finding
                or workflow_review_summary
                or str(workflow.get(WORKFLOW_SUMMARY_FIELD) or "").strip(),
                **fix_step,
            }
        if verification_required and not (verification_passed or workflow_verification_passed):
            return {
                **metadata,
                COMPLETION_RESULT_STATUS_FIELD: NEEDS_VERIFICATION_COMPLETION_STATUS,
                COMPLETION_RESULT_REASON_FIELD: WORKFLOW_VERIFICATION_EVIDENCE_MISSING_REASON,
                "detail": str(workflow.get(WORKFLOW_SUMMARY_FIELD) or "").strip(),
            }
        return {
            **metadata,
            COMPLETION_RESULT_STATUS_FIELD: COMPLETE_COMPLETION_STATUS,
            COMPLETION_RESULT_REASON_FIELD: workflow_clean_review_reason(workflow_id),
        }

    if verification_required and not (verification_passed or workflow_verification_passed):
        return {
            **metadata,
            COMPLETION_RESULT_STATUS_FIELD: NEEDS_VERIFICATION_COMPLETION_STATUS,
            COMPLETION_RESULT_REASON_FIELD: WORKFLOW_VERIFICATION_EVIDENCE_MISSING_REASON,
            "detail": str(workflow.get(WORKFLOW_SUMMARY_FIELD) or "").strip(),
        }

    if _is_workflow_completion_intent_kind(task_intent.kind):
        return {
            **metadata,
            COMPLETION_RESULT_STATUS_FIELD: COMPLETE_COMPLETION_STATUS,
            COMPLETION_RESULT_REASON_FIELD: workflow_completed_all_steps_reason(workflow_id),
        }

    return None


def _is_unsuccessful_workflow_status(status: str | None) -> bool:
    return is_workflow_unsuccessful_status(status)


def _is_workflow_completion_intent_kind(kind: str | None) -> bool:
    return str(kind or "").strip() in _WORKFLOW_COMPLETION_INTENT_KINDS


def _completion_status_for_unsuccessful_workflow(workflow_status: str | None) -> str:
    if _is_failed_workflow_status(workflow_status):
        return BLOCKED_COMPLETION_STATUS
    return INCOMPLETE_COMPLETION_STATUS


def _is_failed_workflow_status(status: str | None) -> bool:
    return is_workflow_failed_status(status)


def _is_cancelled_workflow_status(status: str | None) -> bool:
    return is_workflow_cancelled_status(status)


def _is_research_then_outline_workflow(workflow_id: str | None) -> bool:
    return is_research_then_outline_workflow(workflow_id)


def _workflow_gate_is_complete(workflow_gate: dict[str, Any]) -> bool:
    return is_complete_completion_status(workflow_gate.get(COMPLETION_RESULT_STATUS_FIELD))


def _workflow_gate_needs_verification(workflow_gate: dict[str, Any]) -> bool:
    return needs_verification_completion_status(workflow_gate.get(COMPLETION_RESULT_STATUS_FIELD))


def _is_completed_delegated_review_status(status: str | None) -> bool:
    return is_workflow_completed_status(status)


def _is_clean_structured_review_status(status: str | None) -> bool:
    return is_clean_structured_subagent_status(status)


def _first_review_finding(structured_output: Any) -> str:
    sections = structured_output.get(STRUCTURED_SUBAGENT_SECTIONS_FIELD) if isinstance(structured_output, dict) else None
    if not isinstance(sections, list):
        return ""
    for section in sections:
        if not isinstance(section, dict):
            continue
        items = section.get(STRUCTURED_SUBAGENT_ITEMS_FIELD)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                detail = _format_review_finding(item)
                if detail:
                    return detail
            elif isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def _format_review_finding(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    path = str(item.get("path") or "").strip()
    fix = str(item.get("fix") or "").strip()
    why = str(item.get("why") or "").strip()
    subject = f"{path}: {title}" if path and title else title or path
    if fix:
        return f"{subject}: {fix}" if subject else fix
    if why:
        return f"{subject}: {why}" if subject else why
    return subject


def _review_follow_up_detail(review: dict[str, Any]) -> str | None:
    if not review.get("attempted"):
        return task_review_evidence_missing_detail()
    detail = str(review.get("first_finding") or review.get("summary") or "").strip()
    return detail or task_review_findings_follow_up_detail()


def _workflow_follow_up_detail(workflow_id: str, workflow_status: str, workflow: dict[str, Any]) -> str:
    step_label = str(workflow.get(WORKFLOW_NEXT_STEP_LABEL_FIELD) or workflow.get(WORKFLOW_NEXT_STEP_ID_FIELD) or "").strip()
    error = str(workflow.get(WORKFLOW_ERROR_FIELD) or "").strip()
    summary = str(workflow.get(WORKFLOW_SUMMARY_FIELD) or "").strip()
    if _is_cancelled_workflow_status(workflow_status):
        if step_label and summary:
            return f"Resume with the {step_label} step in {workflow_id}. {summary}"
        if step_label:
            return f"Resume with the {step_label} step in {workflow_id}."
        if summary:
            return f"Finish the remaining workflow steps for {workflow_id}. {summary}"
        return f"Finish the remaining workflow steps for {workflow_id}."
    if step_label and error:
        return f"Resolve the {step_label} step failure in {workflow_id}: {error}"
    if step_label:
        return f"Resolve the {step_label} step failure in {workflow_id}."
    return error or summary


def _workflow_review_follow_up_fields(workflow_id: str) -> dict[str, str]:
    return workflow_review_follow_up_fields(workflow_id)


def _workflow_fix_follow_up_fields(workflow_id: str) -> dict[str, str]:
    return workflow_fix_follow_up_fields(workflow_id)


def _is_review_workflow(workflow_id: str | None) -> bool:
    return is_review_workflow(workflow_id)


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _verification_follow_up(execution_result: ExecutionResult) -> dict[str, Any]:
    touched_paths = _normalized_touched_paths(execution_result.touched_paths)
    decision_paths = tuple(_strip_repo_snapshot_prefix(path) for path in touched_paths)
    test_paths = tuple(path for path in decision_paths if _is_python_test_path(path))
    has_web_touched = any(_is_web_app_path(path) for path in decision_paths)
    has_python_touched = any(_is_python_file_path(path) for path in decision_paths)
    if touched_paths and not has_web_touched and not has_python_touched:
        return {
            "action": "auto",
            "path": _common_verification_path(touched_paths) or ".",
            "pytest_args": (),
        }
    if has_web_touched:
        return {"action": "web_build", "path": _WEB_APP_ROOT_PATH, "pytest_args": ()}
    if test_paths:
        return {"action": "pytest", "path": ".", "pytest_args": test_paths}
    if has_python_touched:
        return {
            "action": "python_compile",
            "path": _common_verification_path(touched_paths) or ".",
            "pytest_args": (),
        }
    return {
        "action": "auto",
        "path": _common_verification_path(touched_paths) or ".",
        "pytest_args": (),
    }


def _normalized_touched_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    return normalized_touched_paths(paths)


def _is_web_app_path(path: str | None) -> bool:
    return is_web_app_path(path)


def _is_python_file_path(path: str | None) -> bool:
    return is_python_file_path(path)


def _is_python_test_path(path: str | None) -> bool:
    return is_python_test_path(path)


def _strip_repo_snapshot_prefix(path: str) -> str:
    return strip_repo_snapshot_prefix(path)


def _common_verification_path(paths: tuple[str, ...]) -> str | None:
    return common_verification_path(paths)
