"""Delegated subagent task runner for AgentLoop."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from ..llms import ChatMessage
from ..storage import StorageProvider
from ..subagent_prompts import get_all_subagents
from ..subagent_session import (
    build_child_subagent_session_id,
    extract_subagent_prompt_type,
    new_subagent_task_id,
    validate_subagent_task_id,
)
from ..tools import ToolRegistry
from ..utils.log import logger
from .subagent_builder import SubagentMessageBuilder
from .subagent_policy import build_subagent_tool_registry, profile_for_subagent


class SubagentRunService:
    """Runs and resumes delegated subagent sessions."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        tools: ToolRegistry,
        max_history_getter: Callable[[], int],
        app_home_getter: Callable[[], Path | None],
        workspace_getter: Callable[[], Path],
        current_session_id_getter: Callable[[], str | None],
        skills_loader_getter: Callable[[], Any],
        save_message: Callable[[str, str, str, str | None, dict[str, Any] | None], Awaitable[None]],
        execute_messages: Callable[..., Awaitable[Any]],
        log_prepared_messages: Callable[[str, list[dict[str, Any]]], None],
        format_log_preview: Callable[..., str],
    ):
        self.storage = storage
        self.tools = tools
        self._max_history_getter = max_history_getter
        self._app_home_getter = app_home_getter
        self._workspace_getter = workspace_getter
        self._current_session_id_getter = current_session_id_getter
        self._skills_loader_getter = skills_loader_getter
        self._save_message = save_message
        self._execute_messages = execute_messages
        self._log_prepared_messages = log_prepared_messages
        self._format_log_preview = format_log_preview

    def build_tools(self, prompt_type: str, *, workspace: Path | None = None) -> ToolRegistry:
        """Build the tool registry exposed to one subagent profile."""
        return build_subagent_tool_registry(
            self.tools,
            prompt_type,
            app_home=self._app_home_getter(),
            session_workspace=workspace or self._workspace_getter(),
        )

    @staticmethod
    def _message_role_and_content(message: Any) -> tuple[str, str]:
        if isinstance(message, dict):
            return message.get("role", "?"), message.get("content", "")
        return getattr(message, "role", "?"), getattr(message, "content", "")

    async def run(
        self,
        task: str,
        prompt_type: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Run or resume a delegated subagent task through a child storage session."""
        task_text = str(task or "").strip()
        if not task_text:
            return "Error: subagent task must be a non-empty string."

        app_home = self._app_home_getter()
        workspace = self._workspace_getter()
        subagents = get_all_subagents(app_home, session_workspace=workspace)
        parent_session_id = self._current_session_id_getter() or "default"

        resume_task_id = str(task_id or "").strip() or None
        is_resume = resume_task_id is not None
        if resume_task_id:
            validation_error = validate_subagent_task_id(resume_task_id)
            if validation_error:
                return validation_error
            child_task_id = resume_task_id
        else:
            child_task_id = new_subagent_task_id()

        child_session_id = build_child_subagent_session_id(parent_session_id, child_task_id)
        existing_child_messages = await self.storage.get_messages(child_session_id)
        if is_resume and not existing_child_messages:
            return f"Error: unknown task_id '{child_task_id}' for current session. Start a new delegate task instead."

        stored_prompt_type = extract_subagent_prompt_type(existing_child_messages)
        requested_prompt_type = str(prompt_type).strip() if prompt_type is not None else ""
        effective_prompt_type = requested_prompt_type or stored_prompt_type or "writer"
        if stored_prompt_type and requested_prompt_type and requested_prompt_type != stored_prompt_type:
            return (
                f"Error: task_id '{child_task_id}' was created with prompt_type '{stored_prompt_type}', "
                f"not '{requested_prompt_type}'. Omit prompt_type or use the original prompt_type to resume."
            )
        if effective_prompt_type not in subagents:
            available = ", ".join(subagents)
            return f"Error: unknown subagent type '{effective_prompt_type}'. Available: {available}"

        try:
            subagent_tools = self.build_tools(effective_prompt_type, workspace=workspace)
            subagent_profile = profile_for_subagent(
                effective_prompt_type,
                app_home=app_home,
                session_workspace=workspace,
            )
        except ValueError as e:
            return f"Error: {str(e)}"

        await self._save_message(
            child_session_id,
            "user",
            task_text,
            None,
            {
                "kind": "subagent_task",
                "task_id": child_task_id,
                "parent_session_id": parent_session_id,
                "prompt_type": effective_prompt_type,
                "resume": is_resume,
            },
        )

        log_id = f"{parent_session_id}:subagent:{effective_prompt_type}:{child_task_id}"
        subagent_builder = SubagentMessageBuilder(skills_loader=self._skills_loader_getter())
        chat_messages = [
            ChatMessage(
                role="system",
                content=subagent_builder.build_system_prompt(
                    effective_prompt_type,
                    workspace=workspace,
                    app_home=app_home,
                ),
            )
        ]
        stored_child_messages = await self.storage.get_messages(child_session_id, limit=self._max_history_getter())
        for message in stored_child_messages:
            role, content = self._message_role_and_content(message)
            if role == "tool":
                continue
            chat_messages.append(ChatMessage(role=role, content=content))

        self._log_prepared_messages(
            log_id,
            [{"role": msg.role, "content": msg.content} for msg in chat_messages],
        )
        logger.info(
            f"[{log_id}] subagent.run | child_session_id={child_session_id} resume={is_resume} "
            f"workspace={workspace} task={self._format_log_preview(task_text, max_chars=200)}"
        )
        logger.info(
            f"[{log_id}] subagent.tools | profile={subagent_profile.name} names={', '.join(subagent_tools.tool_names) or '<none>'}"
        )
        sub_result = await self._execute_messages(
            log_id,
            chat_messages,
            allow_tools=bool(subagent_tools.tool_names),
            tool_result_session_id=child_session_id,
            tool_registry=subagent_tools,
        )
        await self._save_message(
            child_session_id,
            "assistant",
            sub_result.content,
            None,
            {
                "kind": "subagent_result",
                "task_id": child_task_id,
                "parent_session_id": parent_session_id,
                "prompt_type": effective_prompt_type,
            },
        )
        return (
            f"Task ID: {child_task_id}\n"
            f"Subagent: {effective_prompt_type}\n\n"
            f"Result:\n{sub_result.content}"
        )
