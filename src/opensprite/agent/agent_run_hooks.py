"""Agent-level run hook wrappers."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..context.message_history import LearningLedger
from ..runs.trace import RunHookService
from ..tool_names import READ_SKILL_TOOL_NAME
from ..tools.result_status import classify_tool_result_status


class AgentRunHookFactory:
    """Builds run hooks and records successful skill reads for learning reuse."""

    def __init__(
        self,
        *,
        run_hooks: RunHookService,
        run_skill_reads: dict[str, set[str]],
        learning_ledger_getter: Callable[[], LearningLedger | None],
    ):
        self.run_hooks = run_hooks
        self._run_skill_reads = run_skill_reads
        self._learning_ledger_getter = learning_ledger_getter

    def make_tool_progress_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, dict[str, Any]], Awaitable[None]] | None:
        """Publish run telemetry and a brief outbound status before selected tools run."""
        return self.run_hooks.make_tool_progress_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )

    def make_tool_result_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, dict[str, Any], str], Awaitable[None]] | None:
        """Publish tool result telemetry and record successful read_skill usage."""
        base_hook = self.run_hooks.make_tool_result_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )
        if run_id is None:
            return base_hook
        if base_hook is None and self._learning_ledger_getter() is None:
            return None

        async def _hook(
            tool_name: str,
            tool_args: dict[str, Any],
            result: str,
            tool_call_id: str | None = None,
            iteration: int | None = None,
            delegate_task_id: str | None = None,
            delegate_prompt_type: str | None = None,
            state: str | None = None,
            interrupted: bool = False,
        ) -> None:
            if base_hook is not None:
                await base_hook(
                    tool_name,
                    tool_args,
                    result,
                    tool_call_id,
                    iteration,
                    delegate_task_id,
                    delegate_prompt_type,
                    state,
                    interrupted,
                )
            if tool_name != READ_SKILL_TOOL_NAME:
                return
            skill_name = str((tool_args or {}).get("skill_name") or "").strip()
            if not skill_name or not classify_tool_result_status(result).ok:
                return
            self._run_skill_reads.setdefault(run_id, set()).add(skill_name)

        return _hook

    def make_llm_status_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[Any], Awaitable[None]] | None:
        """Publish run telemetry and interim outbound status during long LLM waits."""
        return self.run_hooks.make_llm_status_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )

    def make_llm_delta_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, str, str, int], Awaitable[None]] | None:
        """Publish visible assistant response chunks into the run event stream."""
        return self.run_hooks.make_llm_delta_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )

    def make_tool_input_delta_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, str, str, int], Awaitable[None]] | None:
        """Publish streamed tool-call argument chunks into the run event stream."""
        return self.run_hooks.make_tool_input_delta_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )

    def make_reasoning_delta_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, int], Awaitable[None]] | None:
        """Publish provider reasoning chunks into inspector-only run events."""
        return self.run_hooks.make_reasoning_delta_hook(
            channel=channel,
            external_chat_id=external_chat_id,
            session_id=session_id,
            run_id=run_id,
            enabled=enabled,
        )
