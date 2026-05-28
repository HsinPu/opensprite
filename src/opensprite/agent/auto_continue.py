"""Bounded autonomous continuation decisions for user turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .completion_gate import CompletionGateResult
from .execution import ExecutionResult
from .harness_profile import HarnessProfile
from .task_intent import TaskIntent
from .work_progress import WorkProgressUpdate


_CONTINUABLE_STATUSES = {"incomplete", "needs_verification", "needs_review"}
_TERMINAL_STATUSES = {"blocked", "complete", "waiting_user"}
_INCOMPLETE_PENDING_WORK_REASON = "assistant response did not explicitly complete the task"
_PENDING_WORK_ACTION_MARKERS = (
    "let me check",
    "let me look",
    "let me search",
    "let me fetch",
    "let me research",
    "i will check",
    "i will look",
    "i will search",
    "i will fetch",
    "i will research",
    "i'll check",
    "i'll look",
    "i'll search",
    "i'll fetch",
    "i'll research",
    "checking",
    "searching",
    "looking up",
    "fetching",
    "讓我查",
    "我查",
    "我來查",
    "我去查",
    "查一下",
    "查詢",
    "搜尋",
    "搜索",
    "找一下",
    "我來找",
    "我去找",
)
_PENDING_WORK_BLOCKER_MARKERS = (
    "cannot",
    "can't",
    "could not",
    "unable",
    "blocked",
    "disabled",
    "無法",
    "不能",
    "沒辦法",
)
_GENERIC_PENDING_RESPONSES = frozenset(
    {
        "i will do that",
        "i'll do that",
        "i will handle it",
        "i'll handle it",
        "i will take care of it",
        "i'll take care of it",
        "let me handle that",
        "我會處理",
        "我來處理",
    }
)
_EXISTING_WEB_SOURCE_FINAL_RETRY_REASONS = frozenset(
    {
        "assistant only emitted internal control text",
        "assistant final answer did not reference gathered sources",
        "assistant final answer was too terse for the task",
        "assistant did not provide the requested itemized result",
    }
)


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
        if completion_result.status in _TERMINAL_STATUSES:
            return self._skip(
                "completion_gate_terminal_status",
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=False,
            )
        if completion_result.status not in _CONTINUABLE_STATUSES:
            return self._skip(
                "completion_gate_status_not_continuable",
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
                "max_deterministic_actions_reached",
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if not direct_action_available and attempts_used >= max_attempts:
            return self._skip(
                "max_auto_continues_reached",
                attempt=attempts_used,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if attempts_used > 0 and work_progress is not None and not work_progress.has_progress and not direct_action_available:
            return self._skip(
                "no_progress_during_continuation",
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if execution_result.had_tool_error and not direct_action_available:
            return self._skip(
                "tool_error_requires_blocker_or_user_handoff",
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if (
            completion_result.status == "incomplete"
            and execution_result.executed_tool_calls == 0
            and not task_intent.expects_code_change
            and not direct_action_available
            and not _can_continue_pending_work_response(
                task_intent=task_intent,
                completion_result=completion_result,
                previous_response=previous_response,
                attempts_used=attempts_used,
            )
            and completion_result.reason
            not in {
                "assistant only reported progress without performing requested work",
                "assistant did not provide the requested itemized result",
                "assistant only emitted internal control text",
                "required task evidence was not produced",
                "required task artifacts were not produced",
                "required task artifacts were not traceable",
                "required source material was insufficient",
                "assistant final answer did not reference gathered sources",
                "assistant final answer was too terse for the task",
                "max tool iterations exhausted before completion",
            }
        ):
            return self._skip(
                "no_tool_progress_after_incomplete_response",
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        if completion_result.status == "needs_review" and attempts_used > 0 and not (direct_workflow and direct_start_step):
            reason = "review_findings_require_follow_up" if completion_result.review_attempted else "review_evidence_still_missing"
            return self._skip(
                reason,
                attempt=next_attempt,
                max_attempts=max_attempts,
                emit_event=True,
            )
        allow_tools = not _should_answer_from_existing_web_sources(completion_result, execution_result)
        return AutoContinueDecision(
            should_continue=True,
            reason=f"completion_gate_{completion_result.status}",
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
        if completion_result.status == "needs_verification":
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
        if completion_result.status == "needs_review":
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
        if completion_result.status == "incomplete" and follow_up_detail:
            incomplete_instruction = (
                "\n- The missing work is already identified. Resume from the required follow-up detail below before doing broader new work."
            )
        if completion_result.reason == "assistant only emitted internal control text":
            incomplete_instruction += (
                "\n- The previous response only contained internal control text and no user-visible work. "
                "Do not repeat internal tags such as <system-reminder> or <think>. "
                "Continue the user's task by calling tools when needed, or provide a clear blocker if you cannot proceed."
            )
            if not allow_tools:
                incomplete_instruction += (
                    "\n- Do not call tools again in this continuation. The runtime already gathered traceable sources; "
                    "answer directly using those sources."
                )
        if completion_result.reason == _INCOMPLETE_PENDING_WORK_REASON and _looks_like_concrete_pending_work(previous_response):
            incomplete_instruction += (
                "\n- The previous response announced a concrete next action but did not perform it. "
                "Perform that work now before giving the final answer."
            )
        handoff = _truncate(compaction_handoff or "", max_chars=2400).strip()
        handoff_section = ""
        if handoff:
            handoff_section = (
                "\n\nCompaction handoff from the previous context window:\n"
                f"{handoff}\n"
                "Use this as continuity context only. It does not satisfy missing verification, review, evidence, or quality requirements."
            )
        source_context = _existing_web_source_context(execution_result)
        source_section = ""
        if source_context:
            no_tool_source_instruction = ""
            if not allow_tools:
                no_tool_source_instruction = (
                    "\nDo not describe that you will search, fetch, inspect history, or use another tool. "
                    "Write the final answer now from these gathered sources."
                )
            source_section = (
                "\n\nExisting gathered web sources from the previous pass:\n"
                f"{source_context}\n"
                "Use these sources for the final answer instead of repeating web research unless they are clearly insufficient."
                f"{no_tool_source_instruction}"
            )
        quality_instruction = _quality_follow_up_instruction(completion_result)
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
        if completion_result.status not in {"incomplete", "needs_review"}:
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
        if completion_result.status != "needs_verification":
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


def _can_continue_pending_work_response(
    *,
    task_intent: TaskIntent,
    completion_result: CompletionGateResult,
    previous_response: str,
    attempts_used: int,
) -> bool:
    if attempts_used > 0:
        return False
    if not task_intent.should_seed_active_task:
        return False
    if completion_result.reason != _INCOMPLETE_PENDING_WORK_REASON:
        return False
    return _looks_like_concrete_pending_work(previous_response)


def _looks_like_concrete_pending_work(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    generic = normalized.strip(" .:;!?。！？：；")
    if generic in _GENERIC_PENDING_RESPONSES:
        return False
    if any(marker in normalized for marker in _PENDING_WORK_BLOCKER_MARKERS):
        return False
    return any(marker in normalized for marker in _PENDING_WORK_ACTION_MARKERS)


def _should_answer_from_existing_web_sources(
    completion_result: CompletionGateResult,
    execution_result: ExecutionResult,
) -> bool:
    if completion_result.reason not in _EXISTING_WEB_SOURCE_FINAL_RETRY_REASONS:
        return False
    return bool(_existing_web_source_context(execution_result))


def _existing_web_source_context(execution_result: ExecutionResult | None) -> str:
    if execution_result is None:
        return ""

    lines: list[str] = []
    seen_urls: set[str] = set()
    for artifact in execution_result.task_artifacts:
        if not artifact.ok or artifact.kind != "web_source":
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


def _quality_follow_up_instruction(completion_result: CompletionGateResult) -> str:
    reason = str(completion_result.reason or "").strip()
    detail = str(completion_result.active_task_detail or "").strip()
    if reason == "required task artifacts were not produced":
        return (
            "\n- Quality follow-up: the previous pass did not produce typed artifacts for every required resource. "
            "Use the relevant media/source tools for each missing resource before finalizing. "
            "Do not claim completion until each required resource has a concrete tool-derived result."
        )
    if reason == "required task artifacts were not traceable":
        return (
            "\n- Source follow-up: the previous pass produced a source artifact without traceable source metadata. "
            "Use `web_research`, `web_search`, or `web_fetch` again so the result includes at least one source with a URL plus title or snippet. "
            "Do not finalize from an untraceable source artifact."
        )
    if reason == "required source material was insufficient":
        if "Web research coverage gap" in detail:
            return (
                "\n- Source follow-up: `web_research` reported coverage gaps. "
                "Retry `web_research` with focused `queries` for the missing angles, prefer alternate URLs/domains for too-short or blocked pages, "
                "and do not finalize until the coverage target is met or a concrete fetch blocker is stated."
            )
        return (
            "\n- Source follow-up: the previous pass did not inspect enough source material. "
            "Use `web_research` or `web_fetch` on promising search results, fetch at least one substantial page from a reliable source, "
            "and switch to another URL or browser tools if a page extracts too little content. Do not finalize from search snippets alone."
        )
    if reason == "assistant final answer did not reference gathered sources":
        return (
            "\n- Source follow-up: gathered sources are available, but the previous final answer did not cite them. "
            "Do not rerun tools unless the sources are insufficient. Write the final answer using the gathered results and reference at least one source by URL, domain, or title."
        )
    if reason == "assistant final answer was too terse for the task":
        return (
            "\n- Quality follow-up: the previous final answer was too terse. "
            "Do not reply with only 'done', 'completed', '已完成', or another short acknowledgement. "
            "Use the available tool/artifact results to write a substantive final answer that covers each requested resource and deliverable."
        )
    if reason == "assistant did not provide the requested itemized result":
        return (
            "\n- Quality follow-up: provide the requested itemized result, not an acknowledgement or plan. "
            "Include enough list/table entries to satisfy the user's requested count or clearly explain any remaining blocker."
        )
    if reason == "required task evidence was not produced":
        return (
            "\n- Evidence follow-up: required tool evidence is missing. "
            "Call the appropriate tools for the requested resources or external information before giving the final answer."
        )
    return ""


def _profile_follow_up_instruction(harness_profile: HarnessProfile | None) -> str:
    if harness_profile is None:
        return ""
    if harness_profile.name == "research":
        return (
            "\n- Harness profile: research. Gather source evidence first, fetch or inspect at least one substantive source, "
            "and reference gathered sources in the final answer."
        )
    if harness_profile.name == "coding":
        return (
            "\n- Harness profile: coding. Inspect workspace context before changing files, make the smallest safe change, "
            "and run focused verification when possible."
        )
    if harness_profile.name == "media":
        return (
            "\n- Harness profile: media. Use the relevant media tool to produce the required artifact before finalizing."
        )
    if harness_profile.name == "ops":
        return (
            "\n- Harness profile: ops. Do not perform external side effects without required approval; report validation or blockers explicitly."
        )
    return ""


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
