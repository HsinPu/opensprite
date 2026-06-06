"""Response quality checks for one agent turn."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..tool_names import EXECUTION_TOOL_NAMES
from .completion_status import COMPLETE_COMPLETION_STATUS, INCOMPLETE_COMPLETION_STATUS, NEEDS_VERIFICATION_COMPLETION_STATUS
from .task_resolver import (
    TaskIntent,
    is_history_retrieval_task_type,
    is_media_extraction_task_type,
    is_workspace_read_task_type,
)
from .execution import ExecutionResult, TASK_ARTIFACTS_NOT_PRODUCED_REASON
from .harness_profile import OPERATIONS_TASK_TYPE
from .retrieval import (
    HISTORY_RECALLED_ITEMS_INSUFFICIENT_REASON,
    history_retrieval_metadata_has_results,
    history_retrieval_metadata_reports_empty,
    is_history_retrieval_tool_name,
)
from .media import count_media_artifacts, is_media_artifact_kind
from .task_contract import (
    AcceptanceCriterion,
    COMMAND_VERSION_QUALITY_CHECK,
    ResourceIndex,
    TaskContract,
    is_itemized_output_criterion,
    is_media_artifact_criterion,
    is_operation_report_criterion,
    is_source_artifact_criterion,
    is_source_detail_criterion,
    is_source_reference_criterion,
    is_substantive_final_answer_criterion,
    is_verification_or_gap_criterion,
    is_workspace_location_criterion,
    neutral_task_contract,
)
from ..tools.evidence import VERIFICATION_OUTCOME_OR_GAP_MISSING_REASON
from ..tools.evidence import (
    SOURCE_MATERIAL_INSUFFICIENT_REASON,
    SOURCE_ARTIFACTS_NOT_TRACEABLE_REASON,
    GATHERED_SOURCE_REFERENCE_MISSING_REASON,
    UNGATHERED_SOURCE_REFERENCED_REASON,
    is_web_research_source_artifact_tool,
    is_web_source_artifact_kind,
    ungrounded_response_source_urls,
    web_source_is_referenced,
    web_source_has_substantive_detail,
)

ITEMIZED_RESPONSE_LINE_RE = re.compile(r"^(?:[-*]|\d+[.)]|\|)")
ITEMIZED_OUTPUT_MISSING_REASON = "assistant did not provide the requested itemized result"
TERSE_FINAL_ANSWER_REASON = "assistant final answer was too terse for the task"
MEANINGFUL_OVERLAP_MIN_PREVIEW_CHARS = 17
GROUNDING_TOKEN_MIN_CHARS = 3
VERSION_TOKEN_MIN_CHARS = 5
MEANINGFUL_OVERLAP_MAX_REQUIRED_MATCHES = 3
WORKSPACE_LOCATION_CODE_TOKEN_RE = re.compile(
    r"\b[A-Za-z_][\w:-]*(?:\.[A-Za-z_][\w:-]*|_[A-Za-z0-9_]+|\(\))\b"
)
WORKSPACE_LOCATION_QUOTED_TOKEN_RE = re.compile(r"[`'\"][\w.:-]+[`'\"]")
WORKSPACE_PATH_RE = re.compile(
    r"(?:[\w.-]+[\\/])+[\w.-]+|[\w.-]+\.(?:py|js|ts|tsx|jsx|vue|json|toml|yaml|yml|md|css|html|java|go|rs|sql)",
    flags=re.IGNORECASE,
)
WORKSPACE_CONTEXT_REFERENCE_MISSING_REASON = (
    "assistant final answer did not reference inspected workspace context"
)
WORKSPACE_LOCATION_MISSING_REASON = "assistant final answer did not identify the workspace location"
OPERATION_VALIDATION_OR_RISK_MISSING_REASON = "operation validation or risk was not reported"
REPOSITORY_STATE_GIT_SUBCOMMANDS = frozenset({"rev-parse", "status", "log", "show", "branch"})
COMMAND_VERSION_MISSING_REASON = "command version answer did not report a version"


def normalized_response_text(response_text: str | None) -> str:
    return re.sub(r"\s+", " ", str(response_text or "").strip())


def response_item_count(response_text: str | None) -> int:
    lines = [line.strip() for line in str(response_text or "").splitlines() if line.strip()]
    return sum(1 for line in lines if ITEMIZED_RESPONSE_LINE_RE.match(line))


def response_has_minimum_text_length(response_text: str | None, min_chars: int) -> bool:
    return len(normalized_response_text(response_text)) >= max(1, int(min_chars or 1))


def itemized_output_follow_up_instruction() -> str:
    return (
        "\n- Quality follow-up: provide the requested itemized result, not an acknowledgement or plan. "
        "Include enough list/table entries to satisfy the user's requested count or clearly explain any remaining blocker."
    )


def response_reports_tool_result_preview(response_text: str | None, preview: str | None) -> bool:
    normalized_response = _normalize_grounding_text(response_text)
    normalized_preview = _normalize_grounding_text(preview)
    if not normalized_response or not normalized_preview:
        return False
    if normalized_preview in normalized_response:
        return True
    if _version_token_overlap(normalized_preview, normalized_response):
        return True
    return (
        len(normalized_preview) >= MEANINGFUL_OVERLAP_MIN_PREVIEW_CHARS
        and _meaningful_overlap(normalized_preview, normalized_response)
    )


def _normalize_grounding_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _version_token_overlap(expected: str, actual: str) -> bool:
    if not expected or not actual:
        return False
    version_tokens = [
        token
        for token in re.split(r"[^0-9a-zA-Z._-]+", expected)
        if len(token) >= VERSION_TOKEN_MIN_CHARS and any(char.isdigit() for char in token) and "." in token
    ]
    actual_tokens = [
        token
        for token in re.split(r"[^0-9a-zA-Z._-]+", actual)
        if len(token) >= VERSION_TOKEN_MIN_CHARS and any(char.isdigit() for char in token) and "." in token
    ]
    return any(
        token in actual
        or any(token.startswith(actual_token) or actual_token.startswith(token) for actual_token in actual_tokens)
        for token in version_tokens
    )


def _meaningful_overlap(expected: str, actual: str) -> bool:
    tokens = [token for token in re.split(r"[^0-9a-zA-Z._-]+", expected) if len(token) >= GROUNDING_TOKEN_MIN_CHARS]
    if not tokens:
        return False
    matched = sum(1 for token in tokens if token in actual)
    return matched >= min(MEANINGFUL_OVERLAP_MAX_REQUIRED_MATCHES, len(tokens))


def contains_workspace_location_clue(response_text: str | None, *, has_workspace_path: bool = False) -> bool:
    """Return whether a final answer identifies a concrete workspace location."""
    if has_workspace_path:
        return True
    normalized = str(response_text or "").strip().lower()
    if not normalized:
        return False
    if WORKSPACE_LOCATION_CODE_TOKEN_RE.search(normalized):
        return True
    return bool(WORKSPACE_LOCATION_QUOTED_TOKEN_RE.search(normalized))


def workspace_paths(text: str | None) -> tuple[str, ...]:
    matches = WORKSPACE_PATH_RE.findall(str(text or ""))
    seen: set[str] = set()
    paths: list[str] = []
    for match in matches:
        normalized = match.strip().lower().replace("\\", "/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)
    return tuple(paths)


def response_references_workspace_path(path: str, normalized_response: str) -> bool:
    normalized_path = str(path or "").lower().replace("\\", "/")
    if normalized_path in str(normalized_response or "").replace("\\", "/"):
        return True
    filename = normalized_path.rsplit("/", 1)[-1]
    return bool(filename and filename in normalized_response)


def is_operations_task_type(task_type: str | None) -> bool:
    return str(task_type or "").strip() == OPERATIONS_TASK_TYPE


def is_command_execution_tool_name(tool_name: str | None) -> bool:
    return str(tool_name or "").strip() in EXECUTION_TOOL_NAMES


def execution_has_failed_command_evidence(execution_result: ExecutionResult) -> bool:
    return any(
        is_command_execution_tool_name(evidence.name) and not evidence.ok
        for evidence in execution_result.tool_evidence
    )


def execution_confuses_command_version_with_repo_state(execution_result: ExecutionResult) -> bool:
    for evidence in execution_result.tool_evidence:
        command = ""
        if isinstance(evidence.metadata, dict):
            args = evidence.metadata.get("tool_args")
            if isinstance(args, dict):
                command = str(args.get("command") or "").lower()
        if command_inspects_git_repository_state(command):
            return True
    return False


def command_inspects_git_repository_state(command: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", str(command or "").strip().lower())
    if not normalized.startswith("git "):
        return False
    return any(f"git {subcommand}" in normalized for subcommand in REPOSITORY_STATE_GIT_SUBCOMMANDS)


def command_version_follow_up_instruction() -> str:
    return (
        "\n- Quality follow-up: the user asked for the installed command/program version. "
        "Run the direct version command, such as `<command> --version`, and answer with the version value. "
        "Do not inspect `.git`, `HEAD`, repository commits, or package metadata unless the user asks for repository state."
    )


def command_version_missing_detail(*, inspected_repository_state: bool) -> str:
    if inspected_repository_state:
        return (
            "- The user asked for the installed command/program version. "
            "Run the direct version command, such as `<command> --version`, instead of inspecting `.git`, `HEAD`, or repository commits."
        )
    return "- Include the installed command/program version from the execution result, or clearly state that the command is unavailable."


@dataclass(frozen=True)
class QualityGateResult:
    """Verdict for deterministic response-quality checks."""

    passed: bool
    reason: str = ""
    status: str = COMPLETE_COMPLETION_STATUS
    active_task_detail: str | None = None


class QualityGateService:
    """Evaluate answer-shape quality rules that are independent of tool evidence."""

    def evaluate(
        self,
        *,
        task_intent: TaskIntent,
        response_text: str,
        execution_result: ExecutionResult,
        task_contract: TaskContract | None = None,
    ) -> QualityGateResult:
        contract = task_contract or execution_result.task_contract or neutral_task_contract(task_intent)
        artifact_result = _evaluate_media_artifacts(contract, execution_result)
        if artifact_result is not None:
            return artifact_result
        command_version_result = _evaluate_command_version_answer(contract, response_text, execution_result)
        if command_version_result is not None:
            return command_version_result
        if is_history_retrieval_task_type(contract.task_type) and _history_retrieval_was_empty(execution_result):
            history_result = _evaluate_history_grounding(contract, response_text, execution_result)
            if history_result is not None:
                return history_result
        for criterion in contract.acceptance_criteria:
            if is_itemized_output_criterion(criterion):
                result = _evaluate_itemized_output(criterion, response_text, execution_result)
                if result is not None:
                    return result
            elif is_substantive_final_answer_criterion(criterion):
                result = _evaluate_substantive_final_answer(criterion, response_text)
                if result is not None:
                    return result
            elif is_source_artifact_criterion(criterion):
                result = _evaluate_source_artifact(criterion, execution_result)
                if result is not None:
                    return result
            elif is_source_detail_criterion(criterion):
                result = _evaluate_source_detail(criterion, execution_result)
                if result is not None:
                    return result
            elif is_source_reference_criterion(criterion):
                result = _evaluate_source_reference(criterion, response_text, execution_result)
                if result is not None:
                    return result
            elif is_media_artifact_criterion(criterion):
                result = _evaluate_media_artifact_criterion(criterion, contract, execution_result)
                if result is not None:
                    return result
            elif is_verification_or_gap_criterion(criterion):
                result = _evaluate_verification_or_gap(criterion, response_text, execution_result)
                if result is not None:
                    return result
            elif is_operation_report_criterion(criterion):
                result = _evaluate_operation_report(criterion, response_text, execution_result)
                if result is not None:
                    return result
        workspace_result = _evaluate_workspace_grounding(contract, response_text)
        if workspace_result is not None:
            return workspace_result
        history_result = _evaluate_history_grounding(contract, response_text, execution_result)
        if history_result is not None:
            return history_result
        return QualityGateResult(passed=True)


def _evaluate_itemized_output(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if execution_result.executed_tool_calls > 0:
        return None
    normalized = normalized_response_text(response_text)
    max_response_chars = max(0, int(getattr(criterion, "max_response_chars", 0) or 0))
    if not normalized or (max_response_chars and len(normalized) > max_response_chars):
        return None
    if response_item_count(response_text) >= max(1, int(getattr(criterion, "min_count", 1) or 1)):
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=ITEMIZED_OUTPUT_MISSING_REASON,
    )


def _evaluate_media_artifacts(contract: TaskContract, execution_result: ExecutionResult) -> QualityGateResult | None:
    if not is_media_extraction_task_type(contract.task_type) or not contract.selected_resources:
        return None
    aliases = ResourceIndex.aliases_for(contract.selected_resources)
    covered = {
        alias
        for artifact in execution_result.task_artifacts
        if artifact.ok and is_media_artifact_kind(artifact.kind)
        for resource_id in artifact.resource_ids
        for alias in aliases.get(resource_id, {resource_id})
    }
    missing = tuple(resource.id for resource in contract.selected_resources if resource.id not in covered)
    if not missing:
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=TASK_ARTIFACTS_NOT_PRODUCED_REASON,
        active_task_detail="\n".join(f"- Missing artifact for {resource_id}" for resource_id in missing),
    )


def _evaluate_substantive_final_answer(
    criterion: AcceptanceCriterion,
    response_text: str,
) -> QualityGateResult | None:
    min_response_chars = max(1, int(getattr(criterion, "min_response_chars", 0) or 1))
    if response_has_minimum_text_length(response_text, min_response_chars):
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=TERSE_FINAL_ANSWER_REASON,
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def _evaluate_source_artifact(
    criterion: AcceptanceCriterion,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
    artifact_count = sum(
        1
        for artifact in execution_result.task_artifacts
        if artifact.ok and is_web_source_artifact_kind(artifact.kind)
    )
    traceable_count = len(_execution_web_sources(execution_result))
    if traceable_count >= min_count:
        return None
    if artifact_count > 0:
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=SOURCE_ARTIFACTS_NOT_TRACEABLE_REASON,
            active_task_detail=(
                "- Missing traceable source metadata: url plus title/snippet "
                f"(need {min_count}, found {traceable_count})"
            ),
        )
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=TASK_ARTIFACTS_NOT_PRODUCED_REASON,
        active_task_detail=f"- Missing source artifact: web_source (need {min_count}, found {artifact_count})",
    )


def _evaluate_source_detail(
    criterion: AcceptanceCriterion,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
    sources = _execution_web_sources(execution_result)
    if not sources:
        return None
    detailed_count = sum(1 for source in sources if _source_has_substantive_detail(source))
    if detailed_count >= min_count:
        coverage_detail = _web_research_coverage_gap_detail(execution_result)
        if coverage_detail is None:
            return None
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=SOURCE_MATERIAL_INSUFFICIENT_REASON,
            active_task_detail=coverage_detail,
        )
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=SOURCE_MATERIAL_INSUFFICIENT_REASON,
        active_task_detail=(
            "- Fetch or inspect at least one source page before finalizing; "
            "search snippets and too-short fetches do not count "
            f"(need {min_count}, found {detailed_count})"
        ),
    )


def _evaluate_source_reference(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    sources = _execution_web_sources(execution_result)
    if not sources:
        return None
    ungrounded_urls = ungrounded_response_source_urls(response_text, sources)
    if ungrounded_urls:
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=UNGATHERED_SOURCE_REFERENCED_REASON,
            active_task_detail=(
                "- Remove or verify source URLs that were not gathered in this run: "
                + ", ".join(ungrounded_urls[:3])
            ),
        )
    min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
    referenced_count = sum(1 for source in sources if web_source_is_referenced(source, response_text))
    if referenced_count >= min_count:
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=GATHERED_SOURCE_REFERENCE_MISSING_REASON,
        active_task_detail=(
            "- Reference at least one gathered source by URL, domain, or title "
            f"(need {min_count}, found {referenced_count})"
        ),
    )


def _evaluate_media_artifact_criterion(
    criterion: AcceptanceCriterion,
    contract: TaskContract,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if not contract.selected_resources:
        return None
    min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
    artifact_count = count_media_artifacts(execution_result.task_artifacts)
    if artifact_count >= min_count:
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=TASK_ARTIFACTS_NOT_PRODUCED_REASON,
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def _evaluate_verification_or_gap(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    del response_text
    if execution_result.file_change_count <= 0 or execution_result.verification_attempted:
        return None
    return QualityGateResult(
        passed=False,
        status=NEEDS_VERIFICATION_COMPLETION_STATUS,
        reason=VERIFICATION_OUTCOME_OR_GAP_MISSING_REASON,
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def _evaluate_operation_report(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if any(evidence.ok for evidence in execution_result.tool_evidence):
        return None
    if _response_reports_tool_result(response_text, execution_result):
        return None
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=OPERATION_VALIDATION_OR_RISK_MISSING_REASON,
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def contract_requests_quality_check(contract: TaskContract, check_name: str) -> bool:
    metadata = contract.planner_metadata or {}
    raw_checks = metadata.get("quality_checks")
    if isinstance(raw_checks, str):
        checks = (raw_checks,)
    elif isinstance(raw_checks, list | tuple | set):
        checks = tuple(str(item) for item in raw_checks)
    else:
        checks = ()
    return check_name in {item.strip() for item in checks if item.strip()}


def _evaluate_command_version_answer(
    contract: TaskContract,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if not is_operations_task_type(contract.task_type):
        return None
    if not contract_requests_quality_check(contract, COMMAND_VERSION_QUALITY_CHECK):
        return None
    normalized_response = re.sub(r"\s+", " ", str(response_text or "")).strip().lower()
    if not normalized_response:
        return None
    if _execution_has_failed_command_evidence(execution_result):
        return None
    if _response_reports_tool_result(response_text, execution_result):
        return None
    detail = command_version_missing_detail(
        inspected_repository_state=_execution_confuses_command_version_with_repo_state(execution_result)
    )
    return QualityGateResult(
        passed=False,
        status=INCOMPLETE_COMPLETION_STATUS,
        reason=COMMAND_VERSION_MISSING_REASON,
        active_task_detail=detail,
    )


def _execution_has_failed_command_evidence(execution_result: ExecutionResult) -> bool:
    return execution_has_failed_command_evidence(execution_result)


def _execution_confuses_command_version_with_repo_state(execution_result: ExecutionResult) -> bool:
    return execution_confuses_command_version_with_repo_state(execution_result)


def _response_reports_tool_result(response_text: str, execution_result: ExecutionResult) -> bool:
    if not str(response_text or "").strip():
        return False
    for evidence in execution_result.tool_evidence:
        if not evidence.ok:
            continue
        if response_reports_tool_result_preview(response_text, evidence.result_preview):
            return True
    for artifact in execution_result.task_artifacts:
        if not artifact.ok:
            continue
        if response_reports_tool_result_preview(response_text, artifact.content_preview):
            return True
    return False


def _evaluate_workspace_grounding(contract: TaskContract, response_text: str) -> QualityGateResult | None:
    if not is_workspace_read_task_type(contract.task_type):
        return None
    objective = str(contract.objective or "")
    normalized_response = re.sub(r"\s+", " ", str(response_text or "")).strip().lower()
    if not normalized_response:
        return None

    requested_paths = workspace_paths(objective)
    if requested_paths and not any(response_references_workspace_path(path, normalized_response) for path in requested_paths):
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=WORKSPACE_CONTEXT_REFERENCE_MISSING_REASON,
            active_task_detail="- Reference the inspected workspace path or filename in the final answer.",
        )

    requires_location = any(
        is_workspace_location_criterion(criterion) for criterion in contract.acceptance_criteria
    )
    if requires_location and not contains_workspace_location_clue(
        normalized_response,
        has_workspace_path=bool(workspace_paths(normalized_response)),
    ):
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=WORKSPACE_LOCATION_MISSING_REASON,
            active_task_detail="- Include a file path, symbol, or matching config/code clue from the workspace inspection.",
        )
    return None


def _evaluate_history_grounding(
    contract: TaskContract,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if not is_history_retrieval_task_type(contract.task_type):
        return None
    normalized_response = re.sub(r"\s+", " ", str(response_text or "")).strip().lower()
    if not normalized_response:
        return None

    if _history_retrieval_was_empty(execution_result):
        return None

    requested_count = _history_itemized_min_count(contract)
    if requested_count > 1 and response_item_count(response_text) < requested_count:
        return QualityGateResult(
            passed=False,
            status=INCOMPLETE_COMPLETION_STATUS,
            reason=HISTORY_RECALLED_ITEMS_INSUFFICIENT_REASON,
            active_task_detail=f"- Provide at least {requested_count} recalled item(s) from the retrieved context.",
        )
    return None


def _execution_web_sources(execution_result: ExecutionResult) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    for artifact in execution_result.task_artifacts:
        if artifact.ok and is_web_source_artifact_kind(artifact.kind):
            sources.extend(_artifact_web_sources(artifact.metadata, source_tool=artifact.source_tool))
    return sources


def source_material_satisfies_contract(contract: TaskContract, execution_result: ExecutionResult) -> bool:
    """Return whether gathered web source material satisfies source acceptance criteria."""
    for criterion in contract.acceptance_criteria:
        if is_source_artifact_criterion(criterion):
            min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
            if len(_execution_web_sources(execution_result)) < min_count:
                return False
        elif is_source_detail_criterion(criterion):
            min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
            if _substantive_source_detail_count(execution_result) < min_count:
                return False
            if _web_research_coverage_gap_detail(execution_result) is not None:
                return False
    return True


def source_material_gap_detail(execution_result: ExecutionResult) -> str | None:
    """Return structured web research coverage gap detail, when available."""
    return _web_research_coverage_gap_detail(execution_result)


def source_artifact_traceability_gap_detail(contract: TaskContract, execution_result: ExecutionResult) -> str | None:
    """Return detail when source artifacts exist but lack traceable source metadata."""
    for criterion in contract.acceptance_criteria:
        if not is_source_artifact_criterion(criterion):
            continue
        min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
        artifact_count = sum(
            1
            for artifact in execution_result.task_artifacts
            if artifact.ok and is_web_source_artifact_kind(artifact.kind)
        )
        traceable_count = len(_execution_web_sources(execution_result))
        if artifact_count > 0 and traceable_count < min_count:
            return (
                "- Missing traceable source metadata: url plus title/snippet "
                f"(need {min_count}, found {traceable_count})"
            )
    return None


def media_artifact_gap_detail(contract: TaskContract, execution_result: ExecutionResult) -> str | None:
    """Return the missing media artifact detail for a contract, when available."""
    result = _evaluate_media_artifacts(contract, execution_result)
    if result is not None:
        return result.active_task_detail or result.reason
    for criterion in contract.acceptance_criteria:
        if not is_media_artifact_criterion(criterion):
            continue
        result = _evaluate_media_artifact_criterion(criterion, contract, execution_result)
        if result is not None:
            return result.active_task_detail or result.reason
    return None


def _web_research_coverage_gap_detail(execution_result: ExecutionResult) -> str | None:
    for artifact in execution_result.task_artifacts:
        if not artifact.ok or not is_web_research_source_artifact_tool(artifact.source_tool):
            continue
        coverage = artifact.metadata.get("coverage") if isinstance(artifact.metadata, dict) else None
        if not isinstance(coverage, dict):
            continue
        missing_queries = _string_list(coverage.get("queries_without_successful_fetch"))
        target_met = _truthy(coverage.get("target_met"))
        if target_met:
            continue

        target_fetch_count = _coerce_int(coverage.get("target_fetch_count"), default=0)
        fetched_count = _coerce_int(coverage.get("fetched_count"), default=0)
        if target_fetch_count > 0 and _substantive_source_detail_count(execution_result) >= target_fetch_count:
            continue
        too_short_count = _coerce_int(coverage.get("too_short_count"), default=0)
        blocked_count = _coerce_int(coverage.get("blocked_count"), default=0)
        fetched_domains = _string_list(coverage.get("fetched_domains"))
        details = ["- Web research coverage gap: fetched source coverage did not satisfy the research pass."]
        if not target_met:
            details.append(f"- Target fetch count not met: need {target_fetch_count}, fetched {fetched_count}.")
        if missing_queries:
            details.append(
                "- Queries with search results but no successful fetch: "
                f"{', '.join(missing_queries[:5])}."
            )
        failure_details = []
        if too_short_count > 0:
            failure_details.append(f"{too_short_count} too short")
        if blocked_count > 0:
            failure_details.append(f"{blocked_count} blocked or challenged")
        if failure_details:
            details.append(f"- Failed source details: {', '.join(failure_details)}.")
        if fetched_domains:
            details.append(f"- Fetched domains so far: {', '.join(fetched_domains[:5])}.")
        details.append(
            "- Retry `web_research` with focused `queries` for the missing angles, "
            "or fetch alternate URLs/domains before finalizing."
        )
        return "\n".join(details)
    return None


def _substantive_source_detail_count(execution_result: ExecutionResult) -> int:
    seen: set[str] = set()
    count = 0
    for source in _execution_web_sources(execution_result):
        if not _source_has_substantive_detail(source):
            continue
        url = str(source.get("url") or "").strip().lower()
        key = url or f"{source.get('title') or ''}|{source.get('snippet') or ''}"
        if key in seen:
            continue
        seen.add(key)
        count += 1
    return count


def _artifact_web_sources(metadata: dict[str, object], *, source_tool: str = "") -> list[dict[str, object]]:
    raw_sources = metadata.get("sources") if isinstance(metadata, dict) else None
    if not isinstance(raw_sources, list):
        return []
    sources: list[dict[str, object]] = []
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            continue
        url = str(raw_source.get("url") or "").strip()
        title = str(raw_source.get("title") or "").strip()
        snippet = str(raw_source.get("snippet") or "").strip()
        if url and (title or snippet):
            source: dict[str, object] = {
                "url": url,
                "title": title,
                "snippet": snippet,
                "tool_name": str(raw_source.get("tool_name") or source_tool or "").strip(),
            }
            for key in (
                "content_chars",
                "is_too_short",
                "min_content_chars",
                "truncated",
                "extractor",
                "has_main_content",
                "blocked_or_challenge",
                "quality_score",
            ):
                if key in raw_source:
                    source[key] = raw_source[key]
            sources.append(source)
    return sources


def _source_has_substantive_detail(source: dict[str, object]) -> bool:
    return web_source_has_substantive_detail(source)


def _history_retrieval_was_empty(execution_result: ExecutionResult) -> bool:
    evidence = [
        item
        for item in execution_result.tool_evidence
        if item.ok and is_history_retrieval_tool_name(item.name)
    ]
    if not evidence:
        return False
    saw_explicit_empty = False
    for item in evidence:
        if history_retrieval_metadata_has_results(item.metadata):
            return False
        if history_retrieval_metadata_reports_empty(item.metadata):
            saw_explicit_empty = True
    return saw_explicit_empty


def _history_itemized_min_count(contract: TaskContract) -> int:
    counts = [
        _coerce_int(getattr(criterion, "min_count", 0), default=0)
        for criterion in contract.acceptance_criteria
        if is_itemized_output_criterion(criterion)
    ]
    return max(counts, default=0)


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _coerce_int(value: object, *, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        key = text.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out
