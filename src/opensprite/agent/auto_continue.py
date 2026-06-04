"""Bounded autonomous continuation decisions for user turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .auto_continue_prompt_policy import (
    existing_web_source_section,
    internal_only_response_follow_up_instruction,
    insufficient_source_detail_follow_up_instruction,
    missing_source_citation_follow_up_instruction,
    missing_tool_evidence_follow_up_instruction,
    source_traceability_follow_up_instruction,
    terse_final_answer_follow_up_instruction,
    web_research_coverage_gap_follow_up_instruction,
)
from .auto_continue_reason_policy import (
    COMPLETION_GATE_STATUS_NOT_CONTINUABLE_REASON,
    COMPLETION_GATE_TERMINAL_STATUS_REASON,
    MAX_AUTO_CONTINUES_REACHED_REASON,
    MAX_DETERMINISTIC_ACTIONS_REACHED_REASON,
    NO_PROGRESS_DURING_CONTINUATION_REASON,
    NO_TOOL_PROGRESS_AFTER_INCOMPLETE_RESPONSE_REASON,
    TOOL_ERROR_REQUIRES_BLOCKER_OR_USER_HANDOFF_REASON,
    completion_gate_continue_reason,
    review_follow_up_skip_reason,
)
from .command_version_policy import command_version_follow_up_instruction
from .completion_gate import CompletionGateResult
from .completion_status import (
    allows_workflow_resume,
    is_continuable_completion_status,
    is_incomplete_completion_status,
    is_terminal_completion_status,
    needs_review_completion_status,
    needs_verification_completion_status,
)
from .execution import ExecutionResult
from .harness_profile import (
    HarnessProfile,
    harness_profile_follow_up_instruction,
    is_coding_profile_name,
    is_media_profile_name,
    is_ops_profile_name,
    is_research_profile_name,
)
from .media_artifact_policy import media_artifact_gap_follow_up_instruction
from .quality_gate import (
    contract_requests_quality_check,
    media_artifact_gap_detail,
    source_artifact_traceability_gap_detail,
    source_material_gap_detail,
    source_material_satisfies_contract,
)
from .response_shape_policy import itemized_output_follow_up_instruction
from .stop_reasons import is_max_tool_iterations_stop_reason
from .task_contract import (
    contract_expects_file_change,
    contract_requests_itemized_output,
    contract_requests_source_material,
    contract_requests_source_reference,
    contract_requests_substantive_final_answer,
)
from .task_intent import TaskIntent
from .web_source_policy import is_web_source_artifact_kind
from .work_progress import WorkProgressUpdate


@dataclass(frozen=True)
class AutoContinueDecision:
    """Decision for whether the current run may perform one more LLM/tool pass."""

    should_continue: bool
    reason: str
    attempt: int
    max_attempts: int
    prompt: str | None = None
    direct_workflow: str | None = None
    direct_start_step: str | None = None
    direct_verify_action: str | None = None
    direct_verify_path: str | None = None
    direct_verify_pytest_args: tuple[str, ...] = ()
    harness_profile_name: str = ""
    allow_tools: bool = True
    emit_skipped_event: bool = False

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe run event payload."""
        payload: dict[str, Any] = {
            "schema_version": 1,
            "reason": self.reason,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "will_continue": self.should_continue,
        }
        if self.prompt:
            payload["prompt_len"] = len(self.prompt)
        if self.direct_workflow:
            payload["direct_workflow"] = self.direct_workflow
        if self.direct_start_step:
            payload["direct_start_step"] = self.direct_start_step
        if self.direct_verify_action:
            payload["direct_verify_action"] = self.direct_verify_action
        if self.direct_verify_path:
            payload["direct_verify_path"] = self.direct_verify_path
        if self.direct_verify_pytest_args:
            payload["direct_verify_pytest_args"] = list(self.direct_verify_pytest_args)
        if self.harness_profile_name:
            payload["harness_profile"] = self.harness_profile_name
        if not self.allow_tools:
            payload["allow_tools"] = False
        return payload


class AutoContinueService:
    """Allow at most a small number of safe self-continuations."""

    def __init__(
        self,
        *,
        max_auto_continues: int = 1,
        max_deterministic_actions: int = 4,
        max_same_target_verifications: int = 2,
    ):
        self.max_auto_continues = max(0, max_auto_continues)
        self.max_deterministic_actions = max(0, max_deterministic_actions)
        self.max_same_target_verifications = max(1, max_same_target_verifications)

    def decide(
        self,
        *,
        task_intent: TaskIntent,
        completion_result: CompletionGateResult,
        execution_result: ExecutionResult,
        attempts_used: int,
        previous_response: str,
        work_progress: WorkProgressUpdate | None = None,
        last_direct_workflow: str | None = None,
        last_direct_start_step: str | None = None,
        direct_actions_used: int = 0,
        last_direct_verify_action: str | None = None,
        last_direct_verify_path: str | None = None,
        last_direct_verify_pytest_args: tuple[str, ...] = (),
        same_target_verify_attempts: int = 0,
        verification_available: bool = True,
        compaction_handoff: str | None = None,
        harness_profile: HarnessProfile | None = None,
    ) -> AutoContinueDecision:
        """Return whether another bounded pass should run."""
        profile_name = harness_profile.name if harness_profile is not None else ""
        next_attempt = attempts_used + 1
        max_attempts = work_progress.continuation_budget if work_progress is not None else self.max_auto_continues
        if is_terminal_completion_status(completion_result.status):
            return self._skip(
                COMPLETION_GATE_TERMINAL_STATUS_REASON,
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=False,
            )
        if not is_continuable_completion_status(completion_result.status):
            return self._skip(
                COMPLETION_GATE_STATUS_NOT_CONTINUABLE_REASON,
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=False,
            )
        direct_workflow, direct_start_step = self._deterministic_workflow_resume_target(
            completion_result,
            attempts_used=attempts_used,
            last_direct_workflow=last_direct_workflow,
            last_direct_start_step=last_direct_start_step,
        )
        direct_verify_action, direct_verify_path, direct_verify_pytest_args = self._deterministic_verify_target(
            completion_result,
            attempts_used=attempts_used,
            verification_available=verification_available,
            last_direct_verify_action=last_direct_verify_action,
            last_direct_verify_path=last_direct_verify_path,
            last_direct_verify_pytest_args=last_direct_verify_pytest_args,
            same_target_verify_attempts=same_target_verify_attempts,
            max_same_target_verifications=self.max_same_target_verifications,
        )
        direct_action_available = bool((direct_workflow and direct_start_step) or direct_verify_action)
        if direct_action_available and direct_actions_used >= self.max_deterministic_actions:
            return self._skip(
                MAX_DETERMINISTIC_ACTIONS_REACHED_REASON,
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if attempts_used > 0 and work_progress is not None and not work_progress.has_progress and not direct_action_available:
            return self._skip(
                NO_PROGRESS_DURING_CONTINUATION_REASON,
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if not direct_action_available and attempts_used >= max_attempts:
            return self._skip(
                MAX_AUTO_CONTINUES_REACHED_REASON,
                attempt=attempts_used,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if execution_result.had_tool_error and not direct_action_available:
            return self._skip(
                TOOL_ERROR_REQUIRES_BLOCKER_OR_USER_HANDOFF_REASON,
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if (
            is_incomplete_completion_status(completion_result.status)
            and execution_result.executed_tool_calls == 0
            and not direct_action_available
            and not _can_continue_incomplete_without_prior_tool_progress(task_intent, completion_result, execution_result)
        ):
            return self._skip(
                NO_TOOL_PROGRESS_AFTER_INCOMPLETE_RESPONSE_REASON,
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if needs_review_completion_status(completion_result.status) and attempts_used > 0 and not (direct_workflow and direct_start_step):
            return self._skip(
                review_follow_up_skip_reason(review_attempted=completion_result.review_attempted),
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        allow_tools = not _should_answer_from_existing_web_sources(completion_result, execution_result)
        return AutoContinueDecision(
            should_continue=True,
            reason=completion_gate_continue_reason(completion_result.status),
            attempt=next_attempt,
            max_attempts=max_attempts,
            prompt=self.build_prompt(
                task_intent=task_intent,
                completion_result=completion_result,
                previous_response=previous_response,
                compaction_handoff=compaction_handoff,
                harness_profile=harness_profile,
                execution_result=execution_result,
                allow_tools=allow_tools,
            ),
            direct_workflow=direct_workflow,
            direct_start_step=direct_start_step,
            direct_verify_action=direct_verify_action,
            direct_verify_path=direct_verify_path,
            direct_verify_pytest_args=direct_verify_pytest_args,
            harness_profile_name=profile_name,
            allow_tools=allow_tools,
        )

    def build_prompt(
        self,
        *,
        task_intent: TaskIntent,
        completion_result: CompletionGateResult,
        previous_response: str,
        compaction_handoff: str | None = None,
        harness_profile: HarnessProfile | None = None,
        execution_result: ExecutionResult | None = None,
        allow_tools: bool = True,
    ) -> str:
        """Build the synthetic continuation instruction for the next pass."""
        previous = _truncate(previous_response, max_chars=1200) or "(no previous visible response)"
        follow_up_detail = str(completion_result.active_task_detail or "").strip()
        workflow_target = _workflow_follow_up_target(completion_result)
        follow_up_instruction = ""
        if follow_up_detail:
            follow_up_instruction = (
                f"\n- Required follow-up: {follow_up_detail}"
                "\n- Treat the required follow-up as the next concrete step instead of restarting the task broadly."
            )
        workflow_instruction = ""
        if workflow_target:
            workflow_instruction = f"\n- Workflow follow-up target: {workflow_target}"
            if completion_result.follow_up_workflow and completion_result.follow_up_step_id:
                workflow_instruction += (
                    "\n- If the task still fits the workflow, prefer calling "
                    f"`run_workflow(workflow=\"{completion_result.follow_up_workflow}\", task=<original objective>, start_step=\"{completion_result.follow_up_step_id}\")`."
                )
            if completion_result.follow_up_prompt_type:
                workflow_instruction += (
                    f"\n- Prefer a delegated `{completion_result.follow_up_prompt_type}` step or an equivalent focused step "
                    "before rerunning broader workflow work."
                )
            elif completion_result.follow_up_step_label:
                workflow_instruction += (
                    "\n- Prefer resuming this concrete workflow step instead of rerunning already completed workflow steps."
                )
        verification_instruction = ""
        if needs_verification_completion_status(completion_result.status):
            verification_instruction = (
                "\n- Verification is required. Use available verification tools or clearly state the blocker "
                "if verification cannot be run."
            )
            if completion_result.verification_action:
                verification_instruction += (
                    "\n- If the direct verification target still fits, prefer calling "
                    f"`verify(action=\"{completion_result.verification_action}\""
                    f"{_format_verify_path_hint(completion_result.verification_path)}"
                    f"{_format_verify_pytest_args_hint(completion_result.verification_pytest_args)})`."
                )
        review_instruction = ""
        if needs_review_completion_status(completion_result.status):
            if completion_result.review_attempted:
                review_instruction = (
                    "\n- Review findings already exist. Address the recorded findings first, "
                    "then rerun delegated review only if needed to confirm the fix."
                )
            else:
                review_instruction = (
                    "\n- Review evidence is required for the recorded code changes. Use delegated review workflows or review-focused subagents, "
                    "then summarize whether the review found issues that still need follow-up."
                )
        incomplete_instruction = ""
        if is_incomplete_completion_status(completion_result.status) and follow_up_detail:
            incomplete_instruction = (
                "\n- The missing work is already identified. Resume from the required follow-up detail below before doing broader new work."
            )
        if execution_result.assistant_internal_only_response:
            incomplete_instruction += internal_only_response_follow_up_instruction(allow_tools=allow_tools)
        handoff = _truncate(compaction_handoff or "", max_chars=2400).strip()
        handoff_section = ""
        if handoff:
            handoff_section = (
                "\n\nCompaction handoff from the previous context window:\n"
                f"{handoff}\n"
                "Use this as continuity context only. It does not satisfy missing verification, review, evidence, or quality requirements."
            )
        source_context = _existing_web_source_context(execution_result)
        source_section = existing_web_source_section(source_context, allow_tools=allow_tools)
        quality_instruction = _quality_follow_up_instruction(completion_result, execution_result)
        profile_instruction = _profile_follow_up_instruction(harness_profile)

        return (
            "Continue the current task without asking the user unless you are blocked.\n"
            f"- Original objective: {task_intent.objective}\n"
            f"- Completion gate status: {completion_result.status}\n"
            f"- Completion gate reason: {completion_result.reason}"
            f"{profile_instruction}\n"
            f"{verification_instruction}\n"
            f"{review_instruction}\n"
            f"{incomplete_instruction}\n"
            f"{quality_instruction}\n"
            f"{workflow_instruction}\n"
            f"{follow_up_instruction}\n"
            "- If the task is complete, provide the final answer with the evidence or verification result.\n"
            "- If the task cannot proceed, state the blocker clearly.\n\n"
            "Previous assistant response:\n"
            f"{previous}"
            f"{source_section}"
            f"{handoff_section}"
        )

    def build_post_workflow_resume_prompt(
        self,
        *,
        task_intent: TaskIntent,
        completion_result: CompletionGateResult,
        previous_response: str,
        workflow_result: str,
    ) -> str:
        previous = _truncate(previous_response, max_chars=800) or "(no previous visible response)"
        workflow_output = _truncate(workflow_result, max_chars=2000) or "(workflow returned no visible result)"
        workflow_target = _workflow_follow_up_target(completion_result)
        return (
            "The runtime already resumed the workflow follow-up step for you. Continue from that result instead of rerunning the same step unless you find a concrete reason.\n"
            f"- Original objective: {task_intent.objective}\n"
            f"- Prior completion gate status: {completion_result.status}\n"
            f"- Prior completion gate reason: {completion_result.reason}\n"
            f"- Workflow follow-up target: {workflow_target or 'workflow'}\n"
            "- Use the resumed workflow result below to finish the task, summarize the result, or state any remaining blocker clearly.\n\n"
            "Resumed workflow result:\n"
            f"{workflow_output}\n\n"
            "Previous assistant response:\n"
            f"{previous}"
        )

    def _skip(
        self,
        reason: str,
        *,
        attempt: int,
        emit_event: bool,
        max_attempts: int | None = None,
    ) -> AutoContinueDecision:
        return AutoContinueDecision(
            should_continue=False,
            reason=reason,
            attempt=attempt,
            max_attempts=self.max_auto_continues if max_attempts is None else max_attempts,
            emit_skipped_event=emit_event,
        )

    @staticmethod
    def _deterministic_workflow_resume_target(
        completion_result: CompletionGateResult,
        *,
        attempts_used: int,
        last_direct_workflow: str | None,
        last_direct_start_step: str | None,
    ) -> tuple[str | None, str | None]:
        if not allows_workflow_resume(completion_result.status):
            return None, None
        workflow = str(completion_result.follow_up_workflow or "").strip()
        start_step = str(completion_result.follow_up_step_id or "").strip()
        if not workflow or not start_step:
            return None, None
        if attempts_used <= 0:
            return workflow, start_step
        if workflow == str(last_direct_workflow or "").strip() and start_step == str(last_direct_start_step or "").strip():
            return None, None
        return workflow, start_step

    @staticmethod
    def _deterministic_verify_target(
        completion_result: CompletionGateResult,
        *,
        attempts_used: int,
        verification_available: bool,
        last_direct_verify_action: str | None,
        last_direct_verify_path: str | None,
        last_direct_verify_pytest_args: tuple[str, ...],
        same_target_verify_attempts: int,
        max_same_target_verifications: int,
    ) -> tuple[str | None, str | None, tuple[str, ...]]:
        if not needs_verification_completion_status(completion_result.status):
            return None, None, ()
        if not verification_available:
            return None, None, ()
        action = str(completion_result.verification_action or "").strip()
        if not action:
            return None, None, ()
        path = str(completion_result.verification_path or ".").strip() or "."
        pytest_args = tuple(str(item or "").strip() for item in completion_result.verification_pytest_args if str(item or "").strip())
        if attempts_used <= 0:
            return action, path, pytest_args
        if (
            action == str(last_direct_verify_action or "").strip()
            and path == str(last_direct_verify_path or "").strip()
            and pytest_args == tuple(last_direct_verify_pytest_args or ())
            and same_target_verify_attempts >= max_same_target_verifications
        ):
            return None, None, ()
        return action, path, pytest_args


def _truncate(text: str, *, max_chars: int) -> str:
    compact = str(text or "").strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _should_answer_from_existing_web_sources(
    completion_result: CompletionGateResult,
    execution_result: ExecutionResult,
) -> bool:
    if not is_incomplete_completion_status(completion_result.status):
        return False
    if completion_result.missing_evidence:
        return False
    if not _existing_web_source_context(execution_result):
        return False
    contract = execution_result.task_contract
    if contract is None:
        return False
    return source_material_satisfies_contract(contract, execution_result)


def _can_continue_incomplete_without_prior_tool_progress(
    task_intent: TaskIntent,
    completion_result: CompletionGateResult,
    execution_result: ExecutionResult,
) -> bool:
    if execution_result.assistant_internal_only_response:
        return True
    if is_max_tool_iterations_stop_reason(execution_result.stop_reason):
        return True
    if _media_artifacts_require_more_work(execution_result):
        return True
    if _file_changes_are_required_but_missing(task_intent, completion_result, execution_result):
        return True
    if _task_contract_requires_evidence(execution_result):
        return True
    if (
        contract_requests_itemized_output(execution_result.task_contract)
        or contract_requests_substantive_final_answer(execution_result.task_contract)
    ):
        return True
    if (
        contract_requests_source_reference(execution_result.task_contract)
        and _existing_web_source_context(execution_result)
    ):
        return True
    if _source_material_requires_more_detail(execution_result):
        return True
    if completion_result.missing_evidence:
        return True
    return completion_result.progress_only_response


def _task_contract_requires_evidence(execution_result: ExecutionResult) -> bool:
    contract = execution_result.task_contract
    if contract is None:
        return False
    return bool(getattr(contract, "requirements", ()) or ())


def _source_material_requires_more_detail(execution_result: ExecutionResult) -> bool:
    contract = execution_result.task_contract
    if contract is None:
        return False
    if not contract_requests_source_material(contract):
        return False
    return not source_material_satisfies_contract(contract, execution_result)


def _media_artifacts_require_more_work(execution_result: ExecutionResult) -> bool:
    contract = execution_result.task_contract
    if contract is None:
        return False
    return media_artifact_gap_detail(contract, execution_result) is not None


def _file_changes_are_required_but_missing(
    task_intent: TaskIntent,
    completion_result: CompletionGateResult,
    execution_result: ExecutionResult,
) -> bool:
    expects_file_change = (
        contract_expects_file_change(execution_result.task_contract)
        or bool(getattr(completion_result, "file_change_required", False))
        or bool(getattr(task_intent, "expects_code_change", False))
    )
    return expects_file_change and execution_result.file_change_count <= 0


def _existing_web_source_context(execution_result: ExecutionResult | None) -> str:
    if execution_result is None:
        return ""

    lines: list[str] = []
    seen_urls: set[str] = set()
    for artifact in execution_result.task_artifacts:
        if not artifact.ok or not is_web_source_artifact_kind(artifact.kind):
            continue
        sources = artifact.metadata.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            url = str(source.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = str(source.get("title") or "").strip()
            snippet = " ".join(str(source.get("snippet") or "").split())
            label = title or url
            line = f"- {label}: {url}"
            if snippet:
                line += f" — {snippet[:220]}"
            lines.append(line)
            if len(lines) >= 6:
                return "\n".join(lines)
    return "\n".join(lines)


def _quality_follow_up_instruction(
    completion_result: CompletionGateResult,
    execution_result: ExecutionResult | None = None,
) -> str:
    if execution_result is not None:
        media_gap = (
            media_artifact_gap_detail(execution_result.task_contract, execution_result)
            if execution_result.task_contract is not None
            else None
        )
        if media_gap:
            return media_artifact_gap_follow_up_instruction(media_gap)
        source_traceability_gap = (
            source_artifact_traceability_gap_detail(execution_result.task_contract, execution_result)
            if execution_result.task_contract is not None
            else None
        )
        if source_traceability_gap:
            return source_traceability_follow_up_instruction(source_traceability_gap)
    if execution_result is not None:
        coverage_gap = source_material_gap_detail(execution_result)
        if coverage_gap:
            return web_research_coverage_gap_follow_up_instruction(coverage_gap)
    if execution_result is not None and _source_material_requires_more_detail(execution_result):
        return insufficient_source_detail_follow_up_instruction()
    if (
        execution_result is not None
        and contract_requests_source_reference(execution_result.task_contract)
        and _existing_web_source_context(execution_result)
    ):
        return missing_source_citation_follow_up_instruction()
    if (
        execution_result is not None
        and contract_requests_substantive_final_answer(execution_result.task_contract)
    ):
        return terse_final_answer_follow_up_instruction()
    if (
        execution_result is not None
        and execution_result.task_contract is not None
        and contract_requests_quality_check(execution_result.task_contract, "command_version")
    ):
        return command_version_follow_up_instruction()
    if execution_result is not None and contract_requests_itemized_output(
        execution_result.task_contract
    ):
        return itemized_output_follow_up_instruction()
    if completion_result.missing_evidence:
        return missing_tool_evidence_follow_up_instruction()
    if execution_result is not None and _task_contract_requires_evidence(execution_result):
        return missing_tool_evidence_follow_up_instruction()
    return ""


def _profile_follow_up_instruction(harness_profile: HarnessProfile | None) -> str:
    if harness_profile is None:
        return ""
    return harness_profile_follow_up_instruction(harness_profile.name)


def _workflow_follow_up_target(completion_result: CompletionGateResult) -> str:
    workflow = str(completion_result.follow_up_workflow or "").strip()
    step_label = str(completion_result.follow_up_step_label or completion_result.follow_up_step_id or "").strip()
    if workflow and step_label:
        return f"{workflow} -> {step_label}"
    return workflow or step_label


def _format_verify_path_hint(path: str | None) -> str:
    normalized = str(path or "").strip()
    if not normalized:
        return ""
    return f", path=\"{normalized}\""


def _format_verify_pytest_args_hint(pytest_args: tuple[str, ...]) -> str:
    if not pytest_args:
        return ""
    rendered = ", ".join(f'\"{item}\"' for item in pytest_args)
    return f", pytest_args=[{rendered}]"
