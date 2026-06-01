"""Response quality checks for one agent turn."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .execution import ExecutionResult
from .resource_index import ResourceIndex
from .task_contract import AcceptanceCriterion, TaskContract, neutral_task_contract
from .task_intent import TaskIntent


_MEDIA_ARTIFACT_KINDS = frozenset({"image_text", "image_analysis", "audio_transcript", "video_analysis"})
_SOURCE_ARTIFACT_KINDS = frozenset({"web_source"})
_SOURCE_DETAIL_TOOLS = frozenset({"web_fetch", "browser_navigate", "browser_snapshot"})
_URL_RE = re.compile(r"https?://[^\s<>()\]\}\"']+", re.IGNORECASE)
_VERIFICATION_GAP_RE = re.compile(
    r"\b(?:tests? not run|not tested|not verified|could not verify|unable to verify|verification gap|untested)\b"
    r"|(?:未測試|沒有測試|尚未測試|未驗證|沒有驗證|尚未驗證|無法驗證)",
    re.IGNORECASE,
)
_OPERATION_REPORT_RE = re.compile(
    r"\b(?:approval|approved|denied|blocked|blocker|validation|validated|verified|rollback|risk|audit|permission|configured|deployed|restarted)\b"
    r"|(?:核准|拒絕|封鎖|阻礙|無法確認|無法判定|驗證|回滾|風險|稽核|權限|設定|部署|重啟)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QualityGateResult:
    """Verdict for deterministic response-quality checks."""

    passed: bool
    reason: str = ""
    status: str = "complete"
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
        if contract.task_type == "history_retrieval" and _history_retrieval_was_empty(execution_result):
            history_result = _evaluate_history_grounding(contract, response_text, execution_result)
            if history_result is not None:
                return history_result
        repo_status_result = _evaluate_repository_status_answer(contract, response_text, execution_result)
        if repo_status_result is not None:
            return repo_status_result
        for criterion in contract.acceptance_criteria:
            if criterion.kind == "itemized_output":
                result = _evaluate_itemized_output(criterion, response_text, execution_result)
                if result is not None:
                    return result
            elif criterion.kind == "substantive_final_answer":
                result = _evaluate_substantive_final_answer(criterion, response_text)
                if result is not None:
                    return result
            elif criterion.kind == "source_artifact":
                result = _evaluate_source_artifact(criterion, execution_result)
                if result is not None:
                    return result
            elif criterion.kind == "source_detail":
                result = _evaluate_source_detail(criterion, execution_result)
                if result is not None:
                    return result
            elif criterion.kind == "source_reference":
                result = _evaluate_source_reference(criterion, response_text, execution_result)
                if result is not None:
                    return result
            elif criterion.kind == "media_artifact":
                result = _evaluate_media_artifact_criterion(criterion, contract, execution_result)
                if result is not None:
                    return result
            elif criterion.kind == "verification_or_gap":
                result = _evaluate_verification_or_gap(criterion, response_text, execution_result)
                if result is not None:
                    return result
            elif criterion.kind == "operation_report":
                result = _evaluate_operation_report(criterion, response_text, execution_result)
                if result is not None:
                    return result
        workspace_result = _evaluate_workspace_grounding(contract, response_text)
        if workspace_result is not None:
            return workspace_result
        history_result = _evaluate_history_grounding(contract, response_text, execution_result)
        if history_result is not None:
            return history_result
        concrete_result = _evaluate_concrete_web_answer(contract, task_intent, response_text)
        if concrete_result is not None:
            return concrete_result
        return QualityGateResult(passed=True)


def _evaluate_itemized_output(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if execution_result.executed_tool_calls > 0:
        return None
    normalized = re.sub(r"\s+", " ", (response_text or "").strip())
    max_response_chars = max(0, int(getattr(criterion, "max_response_chars", 0) or 0))
    if not normalized or (max_response_chars and len(normalized) > max_response_chars):
        return None
    if _response_item_count(response_text) >= max(1, int(getattr(criterion, "min_count", 1) or 1)):
        return None
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="assistant did not provide the requested itemized result",
    )


def _evaluate_media_artifacts(contract: TaskContract, execution_result: ExecutionResult) -> QualityGateResult | None:
    if contract.task_type != "media_extraction" or not contract.selected_resources:
        return None
    aliases = ResourceIndex.aliases_for(contract.selected_resources)
    covered = {
        alias
        for artifact in execution_result.task_artifacts
        if artifact.ok and artifact.kind in _MEDIA_ARTIFACT_KINDS
        for resource_id in artifact.resource_ids
        for alias in aliases.get(resource_id, {resource_id})
    }
    missing = tuple(resource.id for resource in contract.selected_resources if resource.id not in covered)
    if not missing:
        return None
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="required task artifacts were not produced",
        active_task_detail="\n".join(f"- Missing artifact for {resource_id}" for resource_id in missing),
    )


def _evaluate_substantive_final_answer(
    criterion: AcceptanceCriterion,
    response_text: str,
) -> QualityGateResult | None:
    normalized = re.sub(r"\s+", " ", (response_text or "").strip())
    min_response_chars = max(1, int(getattr(criterion, "min_response_chars", 0) or 1))
    if len(normalized) >= min_response_chars:
        return None
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="assistant final answer was too terse for the task",
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def _evaluate_concrete_web_answer(
    contract: TaskContract,
    task_intent: TaskIntent,
    response_text: str,
) -> QualityGateResult | None:
    if contract.task_type != "web_research":
        return None
    objective = re.sub(r"\s+", " ", str(task_intent.objective or contract.objective or "").strip().lower())
    answer = re.sub(r"\s+", " ", str(response_text or "").strip().lower())
    if _looks_like_source_summary_fallback(answer):
        if "base url" in objective or "baseurl" in objective:
            if "api/v1" not in answer:
                return QualityGateResult(
                    passed=False,
                    status="incomplete",
                    reason="assistant summarized sources without answering the requested concrete fact",
                    active_task_detail="- State the API base URL directly before listing sources.",
                )
        if "authentication" in objective or "authorization" in objective or "認證" in objective:
            if "authorization" not in answer or "bearer" not in answer:
                return QualityGateResult(
                    passed=False,
                    status="incomplete",
                    reason="assistant summarized sources without answering the requested concrete fact",
                    active_task_detail="- State the Authorization Bearer header directly before listing sources.",
                )
        if _objective_requests_market_quote(objective):
            return QualityGateResult(
                passed=False,
                status="incomplete",
                reason="assistant summarized sources without answering the requested concrete fact",
                active_task_detail="- State the current or latest available quote directly before listing sources.",
            )
    return None


def _objective_requests_market_quote(normalized_objective: str) -> bool:
    return any(
        marker in normalized_objective
        for marker in (
            "stock price",
            "share price",
            "market price",
            "latest price",
            "current price",
            "quote",
            "股價",
            "報價",
        )
    )


def _looks_like_source_summary_fallback(normalized_answer: str) -> bool:
    if not normalized_answer:
        return False
    return (
        "重點摘要" in normalized_answer
        and "來源網址" in normalized_answer
        and (
            "我已根據本輪已成功蒐集到的來源整理如下" in normalized_answer
            or "based on the sources gathered" in normalized_answer
        )
    )


def _evaluate_source_artifact(
    criterion: AcceptanceCriterion,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
    artifact_count = sum(
        1
        for artifact in execution_result.task_artifacts
        if artifact.ok and artifact.kind in _SOURCE_ARTIFACT_KINDS
    )
    traceable_count = len(_execution_web_sources(execution_result))
    if traceable_count >= min_count:
        return None
    if artifact_count > 0:
        return QualityGateResult(
            passed=False,
            status="incomplete",
            reason="required task artifacts were not traceable",
            active_task_detail=(
                "- Missing traceable source metadata: url plus title/snippet "
                f"(need {min_count}, found {traceable_count})"
            ),
        )
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="required task artifacts were not produced",
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
            status="incomplete",
            reason="required source material was insufficient",
            active_task_detail=coverage_detail,
        )
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="required source material was insufficient",
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
    ungrounded_urls = _ungrounded_response_urls(response_text, sources)
    if ungrounded_urls:
        return QualityGateResult(
            passed=False,
            status="incomplete",
            reason="assistant final answer referenced ungathered sources",
            active_task_detail=(
                "- Remove or verify source URLs that were not gathered in this run: "
                + ", ".join(ungrounded_urls[:3])
            ),
        )
    min_count = max(1, int(getattr(criterion, "min_count", 1) or 1))
    referenced_count = sum(1 for source in sources if _source_is_referenced(source, response_text))
    if referenced_count >= min_count:
        return None
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="assistant final answer did not reference gathered sources",
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
    artifact_count = sum(
        1
        for artifact in execution_result.task_artifacts
        if artifact.ok and artifact.kind in _MEDIA_ARTIFACT_KINDS
    )
    if artifact_count >= min_count:
        return None
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="required task artifacts were not produced",
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def _evaluate_verification_or_gap(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if execution_result.file_change_count <= 0 or execution_result.verification_attempted:
        return None
    if _VERIFICATION_GAP_RE.search(response_text or ""):
        return None
    return QualityGateResult(
        passed=False,
        status="needs_verification",
        reason="verification outcome or gap was not reported",
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def _evaluate_operation_report(
    criterion: AcceptanceCriterion,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if _OPERATION_REPORT_RE.search(response_text or ""):
        return None
    if _response_reports_tool_result(response_text, execution_result):
        return None
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="operation validation or risk was not reported",
        active_task_detail=getattr(criterion, "description", "") or None,
    )


def _evaluate_command_version_answer(
    contract: TaskContract,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if contract.task_type != "operations":
        return None
    objective = re.sub(r"\s+", " ", str(contract.objective or "")).strip().lower()
    if not _asks_for_command_version(objective):
        return None
    normalized_response = re.sub(r"\s+", " ", str(response_text or "")).strip().lower()
    if not normalized_response:
        return None
    if _response_reports_command_unavailable(normalized_response):
        return None
    if _response_contains_version_like_value(normalized_response):
        return None
    if _response_confuses_command_version_with_repo_state(normalized_response, execution_result):
        detail = (
            "- The user asked for the installed command/program version. "
            "Run the direct version command, such as `<command> --version`, instead of inspecting `.git`, `HEAD`, or repository commits."
        )
    else:
        detail = "- Include the installed command/program version from the execution result, or clearly state that the command is unavailable."
    return QualityGateResult(
        passed=False,
        status="incomplete",
        reason="command version answer did not report a version",
        active_task_detail=detail,
    )


def _asks_for_command_version(normalized_objective: str) -> bool:
    if not any(marker in normalized_objective for marker in ("version", "版本")):
        return False
    return bool(re.search(r"\b(?:git|python|python3|node|npm|pnpm|yarn|docker|uv|pip|poetry)\b", normalized_objective))


def _response_contains_version_like_value(normalized_response: str) -> bool:
    return bool(re.search(r"\b\d+(?:\.\d+){1,}(?:[-+._a-z0-9]*)?\b", normalized_response))


def _response_reports_command_unavailable(normalized_response: str) -> bool:
    return any(
        marker in normalized_response
        for marker in (
            "not installed",
            "command not found",
            "not recognized",
            "找不到命令",
            "未安裝",
            "沒有安裝",
        )
    )


def _evaluate_repository_status_answer(
    contract: TaskContract,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if contract.task_type != "operations":
        return None
    objective = re.sub(r"\s+", " ", str(contract.objective or "")).strip().lower()
    if not _asks_for_repository_status(objective):
        return None
    normalized_response = re.sub(r"\s+", " ", str(response_text or "")).strip().lower()
    if not normalized_response:
        return None
    saw_no_git = any(
        marker in normalized_response
        for marker in ("no_git", "not a git repository", "not git repository", "不是 git repository", "不是 git repo", "沒有 .git")
    )
    if not saw_no_git:
        return None
    reports_clean = any(
        marker in normalized_response
        for marker in (
            "no uncommitted",
            "no changes",
            "clean working tree",
            "沒有未提交",
            "沒有未 commit",
            "沒有改動",
            "沒有變更",
        )
    )
    reports_blocker = any(
        marker in normalized_response
        for marker in (
            "blocked",
            "blocker",
            "cannot determine",
            "cannot verify",
            "unable to determine",
            "無法判定",
            "無法確認",
            "阻礙",
        )
    )
    if reports_clean and not reports_blocker:
        return QualityGateResult(
            passed=False,
            status="incomplete",
            reason="repository status answer treated missing git metadata as a clean working tree",
            active_task_detail=(
                "- If git metadata is missing, report a blocker or uncertainty; "
                "do not claim there are no uncommitted changes."
            ),
        )
    return None


def _asks_for_repository_status(objective: str) -> bool:
    if re.search(r"\bgit\s+(?:status|diff|branch|log|rev-parse|show|stash)\b", objective):
        return True
    repo_markers = ("repo", "repository", "working tree", "worktree", "git", "專案", "分支")
    status_markers = (
        "uncommitted",
        "unstaged",
        "staged",
        "dirty",
        "branch",
        "status",
        "diff",
        "未提交",
        "尚未提交",
        "沒 commit",
        "改動",
        "變更",
        "目前分支",
    )
    return any(marker in objective for marker in repo_markers) and any(marker in objective for marker in status_markers)


def _response_confuses_command_version_with_repo_state(
    normalized_response: str,
    execution_result: ExecutionResult,
) -> bool:
    repo_state_markers = (".git", "not a git repository", "head", "rev-parse", "commit")
    if any(marker in normalized_response for marker in repo_state_markers):
        return True
    for evidence in execution_result.tool_evidence:
        command = ""
        if isinstance(evidence.metadata, dict):
            args = evidence.metadata.get("tool_args")
            if isinstance(args, dict):
                command = str(args.get("command") or "").lower()
        preview = str(evidence.result_preview or "").lower()
        if "git rev-parse" in command or any(marker in preview for marker in repo_state_markers):
            return True
    return False


def _response_reports_tool_result(response_text: str, execution_result: ExecutionResult) -> bool:
    normalized_response = re.sub(r"\s+", " ", str(response_text or "")).strip().lower()
    if not normalized_response:
        return False
    for evidence in execution_result.tool_evidence:
        if not evidence.ok:
            continue
        preview = re.sub(r"\s+", " ", str(evidence.result_preview or "")).strip().lower()
        if preview and preview in normalized_response:
            return True
        if _version_token_overlap(preview, normalized_response):
            return True
        if preview and len(preview) > 16 and _meaningful_overlap(preview, normalized_response):
            return True
    for artifact in execution_result.task_artifacts:
        if not artifact.ok:
            continue
        preview = re.sub(r"\s+", " ", str(artifact.content_preview or "")).strip().lower()
        if preview and preview in normalized_response:
            return True
        if _version_token_overlap(preview, normalized_response):
            return True
        if preview and len(preview) > 16 and _meaningful_overlap(preview, normalized_response):
            return True
    return False


def _version_token_overlap(expected: str, actual: str) -> bool:
    if not expected or not actual:
        return False
    version_tokens = [
        token
        for token in re.split(r"[^0-9a-zA-Z._-]+", expected)
        if len(token) >= 5 and any(char.isdigit() for char in token) and "." in token
    ]
    actual_tokens = [
        token
        for token in re.split(r"[^0-9a-zA-Z._-]+", actual)
        if len(token) >= 5 and any(char.isdigit() for char in token) and "." in token
    ]
    return any(
        token in actual
        or any(token.startswith(actual_token) or actual_token.startswith(token) for actual_token in actual_tokens)
        for token in version_tokens
    )


def _meaningful_overlap(expected: str, actual: str) -> bool:
    tokens = [token for token in re.split(r"[^0-9a-zA-Z._-]+", expected) if len(token) >= 3]
    if not tokens:
        return False
    matched = sum(1 for token in tokens if token in actual)
    return matched >= min(3, len(tokens))


def _evaluate_workspace_grounding(contract: TaskContract, response_text: str) -> QualityGateResult | None:
    if contract.task_type != "workspace_read":
        return None
    objective = str(contract.objective or "")
    normalized_response = re.sub(r"\s+", " ", str(response_text or "")).strip().lower()
    if not normalized_response:
        return None

    requested_paths = _workspace_paths(objective)
    if requested_paths and not any(_path_referenced(path, normalized_response) for path in requested_paths):
        return QualityGateResult(
            passed=False,
            status="incomplete",
            reason="assistant final answer did not reference inspected workspace context",
            active_task_detail="- Reference the inspected workspace path or filename in the final answer.",
        )

    if _asks_for_workspace_location(objective) and not _contains_workspace_location_clue(normalized_response):
        return QualityGateResult(
            passed=False,
            status="incomplete",
            reason="assistant final answer did not identify the workspace location",
            active_task_detail="- Include a file path, symbol, or matching config/code clue from the workspace inspection.",
        )
    return None


def _evaluate_history_grounding(
    contract: TaskContract,
    response_text: str,
    execution_result: ExecutionResult,
) -> QualityGateResult | None:
    if contract.task_type != "history_retrieval":
        return None
    normalized_response = re.sub(r"\s+", " ", str(response_text or "")).strip().lower()
    if not normalized_response:
        return None

    if _history_retrieval_was_empty(execution_result):
        if _states_history_not_found(normalized_response):
            return None
        return QualityGateResult(
            passed=False,
            status="incomplete",
            reason="assistant answered despite empty history retrieval",
            active_task_detail="- State that no matching prior context was found instead of inventing recalled details.",
        )

    if not _references_prior_context(normalized_response):
        return QualityGateResult(
            passed=False,
            status="incomplete",
            reason="assistant final answer did not reference retrieved prior context",
            active_task_detail="- Make clear that the answer is based on retrieved prior chat context.",
        )

    requested_count = _requested_history_item_count(contract.objective)
    if requested_count > 1 and _response_item_count(response_text) < requested_count:
        return QualityGateResult(
            passed=False,
            status="incomplete",
            reason="assistant did not provide enough recalled items",
            active_task_detail=f"- Provide at least {requested_count} recalled item(s) from the retrieved context.",
        )
    return None


def _execution_web_sources(execution_result: ExecutionResult) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    for artifact in execution_result.task_artifacts:
        if artifact.ok and artifact.kind in _SOURCE_ARTIFACT_KINDS:
            sources.extend(_artifact_web_sources(artifact.metadata, source_tool=artifact.source_tool))
    return sources


def _web_research_coverage_gap_detail(execution_result: ExecutionResult) -> str | None:
    for artifact in execution_result.task_artifacts:
        if not artifact.ok or artifact.source_tool != "web_research":
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
    tool_name = str(source.get("tool_name") or "").strip()
    if tool_name not in _SOURCE_DETAIL_TOOLS:
        return False
    if tool_name == "web_fetch":
        if _truthy(source.get("blocked_or_challenge")):
            return False
        if "has_main_content" in source and not _truthy(source.get("has_main_content")):
            return False
        if _truthy(source.get("is_too_short")):
            return False
        content_chars = _coerce_int(source.get("content_chars"), default=0)
        min_content_chars = _coerce_int(source.get("min_content_chars"), default=0)
        if min_content_chars > 0 and content_chars < min_content_chars:
            return False
    return True


def _workspace_paths(text: str) -> tuple[str, ...]:
    matches = re.findall(
        r"(?:[\w.-]+[\\/])+[\w.-]+|[\w.-]+\.(?:py|js|ts|tsx|jsx|vue|json|toml|yaml|yml|md|css|html|java|go|rs|sql)",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    seen: set[str] = set()
    paths: list[str] = []
    for match in matches:
        normalized = match.strip().lower().replace("\\", "/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)
    return tuple(paths)


def _path_referenced(path: str, normalized_response: str) -> bool:
    normalized_path = path.lower().replace("\\", "/")
    if normalized_path in normalized_response.replace("\\", "/"):
        return True
    filename = normalized_path.rsplit("/", 1)[-1]
    return bool(filename and filename in normalized_response)


def _asks_for_workspace_location(text: str) -> bool:
    lowered = str(text or "").lower()
    return bool(
        re.search(r"\b(?:where|which|location|path|file|config|setting|function|class|symbol)\b", lowered)
        or any(
            marker in lowered
            for marker in (
                "哪裡",
                "哪個",
                "位置",
                "路徑",
                "檔案",
                "設定",
                "函式",
                "類別",
                "符號",
            )
        )
    )


def _contains_workspace_location_clue(normalized_response: str) -> bool:
    if _workspace_paths(normalized_response):
        return True
    if re.search(r"\b(?:function|class|method|symbol)\s+[`'\"]?[\w.:-]+", normalized_response):
        return True
    if re.search(r"[`'\"][\w.:-]+[`'\"]", normalized_response):
        return True
    return False


def _history_retrieval_was_empty(execution_result: ExecutionResult) -> bool:
    evidence = [
        item
        for item in execution_result.tool_evidence
        if item.ok and item.name == "search_history"
    ]
    if not evidence:
        return False
    saw_explicit_empty = False
    for item in evidence:
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        for key in ("result_count", "hit_count", "hits", "count"):
            if key in metadata:
                value = metadata.get(key)
                if isinstance(value, list) and len(value) > 0:
                    return False
                if _coerce_int(value, default=0) > 0:
                    return False
                saw_explicit_empty = True
        preview = str(item.result_preview or "").lower()
        if preview and any(
            marker in preview
            for marker in (
                "no results",
                "no matches",
                "not found",
                "[]",
                "沒有結果",
                "找不到",
            )
        ):
            saw_explicit_empty = True
        elif preview:
            return False
    return saw_explicit_empty


def _states_history_not_found(normalized_response: str) -> bool:
    return any(
        marker in normalized_response
        for marker in (
            "no matching prior",
            "no prior",
            "not found",
            "could not find",
            "沒有找到",
            "沒有符合",
            "找不到",
            "查不到",
        )
    )


def _references_prior_context(normalized_response: str) -> bool:
    return any(
        marker in normalized_response
        for marker in (
            "previous",
            "earlier",
            "prior",
            "retrieved",
            "history",
            "run trace",
            "list_run_file_changes",
            "對話記錄",
            "對話紀錄",
            "根據對話",
            "這段對話",
            "對話的內容",
            "前面",
            "剛剛",
            "剛才",
            "先前",
            "搜尋結果",
        )
    )


def _requested_history_item_count(objective: str) -> int:
    text = str(objective or "")
    digit_counts = [int(match) for match in re.findall(r"(?<!\d)\d{1,2}(?!\d)", text)]
    word_counts = []
    for marker, count in (
        ("一", 1),
        ("二", 2),
        ("兩", 2),
        ("三", 3),
        ("四", 4),
        ("五", 5),
    ):
        if marker in text:
            word_counts.append(count)
    return max([*digit_counts, *word_counts], default=0)


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


def _source_is_referenced(source: dict[str, object], response_text: str) -> bool:
    normalized_response = re.sub(r"\s+", " ", (response_text or "").strip().lower())
    if not normalized_response:
        return False

    url = str(source.get("url") or "").strip().lower()
    if url and url in normalized_response:
        return True

    domain = _source_domain(url)
    if domain and domain in normalized_response:
        return True

    title = re.sub(r"\s+", " ", str(source.get("title") or "").strip().lower())
    return len(title) >= 6 and title in normalized_response


def _ungrounded_response_urls(response_text: str, sources: list[dict[str, object]]) -> list[str]:
    source_urls = {
        normalized
        for source in sources
        if (normalized := _normalize_source_url(str(source.get("url") or "")))
    }
    if not source_urls:
        return []

    ungrounded: list[str] = []
    seen: set[str] = set()
    text = response_text or ""
    for match in _URL_RE.finditer(text):
        raw_url = match.group(0)
        url = raw_url.rstrip(".,;:!?，。；：！？`'\"*)]】")
        normalized = _normalize_source_url(url)
        if not normalized or normalized in source_urls:
            continue
        if _response_url_is_recommendation(text, match.start()):
            continue
        if not _response_url_looks_like_source_reference(normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        ungrounded.append(url)
    return ungrounded


def _response_url_looks_like_source_reference(normalized_url: str) -> bool:
    try:
        parsed = urlparse(normalized_url)
    except Exception:
        return True
    if parsed.netloc == "openrouter.ai" and parsed.path.startswith("/api/"):
        return False
    return True


def _response_url_is_recommendation(response_text: str, url_start: int) -> bool:
    prefix = response_text[max(0, url_start - 40) : url_start].lower()
    return any(
        marker in prefix
        for marker in (
            "建議",
            "可至",
            "請至",
            "參考",
            "see ",
            "check ",
            "visit ",
        )
    )


def _normalize_source_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except Exception:
        return text.rstrip("/").lower()
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/")
    if netloc == "openrouter.ai":
        path = path.replace("/docs/api-reference/", "/docs/api/reference/", 1)
        if path.endswith(".md"):
            path = path[:-3]
    normalized = f"{scheme}://{netloc}{path}"
    if parsed.params:
        normalized += f";{parsed.params}"
    return normalized.lower()


def _source_domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return domain[4:] if domain.startswith("www.") else domain


def _response_item_count(response_text: str) -> int:
    lines = [line.strip() for line in str(response_text or "").splitlines() if line.strip()]
    item_like = 0
    for line in lines:
        if re.match(r"^(?:[-*]|\d+[.)]|\|)", line):
            item_like += 1
    return item_like
