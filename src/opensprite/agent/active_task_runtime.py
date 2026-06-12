"""Agent active-task command runtime helpers."""

from __future__ import annotations

from typing import Any

from ..runs.events import ACTIVE_TASK_COMMAND_APPLIED_EVENT, ACTIVE_TASK_COMMAND_FAILED_EVENT
from .task.intent import TaskIntent


async def emit_active_task_command_event(
    agent: Any,
    session_id: str,
    command: str,
    *,
    applied: bool,
    detail: dict[str, Any] | None = None,
) -> None:
    """Record an active-task command event on the current run trace when available."""
    run_id = agent.turn_context.current_run_id()
    if run_id is None:
        return
    payload = {
        "command": command,
        "status": "applied" if applied else "failed",
        **(detail or {}),
    }
    await agent._emit_run_event(
        session_id,
        run_id,
        ACTIVE_TASK_COMMAND_APPLIED_EVENT if applied else ACTIVE_TASK_COMMAND_FAILED_EVENT,
        payload,
        channel=agent.turn_context.current_channel(),
        external_chat_id=agent.turn_context.current_external_chat_id(),
    )


async def set_active_task_from_text(agent: Any, session_id: str, task_text: str) -> str | None:
    """Create or replace the current ACTIVE_TASK from explicit user text."""
    rendered = await agent.active_task_commands.set_from_text(session_id, task_text)
    await emit_active_task_command_event(
        agent,
        session_id,
        "set_active_task",
        applied=rendered is not None,
        detail={"text_preview": agent._format_log_preview(task_text, 160)},
    )
    return rendered


async def set_goal_from_text(agent: Any, session_id: str, goal_text: str) -> str | None:
    """Create a resumable session goal backed by ACTIVE_TASK and work state."""
    goal = " ".join(str(goal_text or "").split()).strip()
    if not goal:
        return None
    rendered = await agent.active_task_commands.set_from_text(session_id, goal)
    if rendered is None:
        await emit_active_task_command_event(
            agent,
            session_id,
            "set_goal",
            applied=False,
            detail={"text_preview": agent._format_log_preview(goal, 160)},
        )
        return None

    task_intent = task_intent_for_explicit_goal(agent, goal)
    work_plan = agent.work_progress.create_plan(task_intent)
    state = agent.work_progress.build_initial_state(
        session_id=session_id,
        task_intent=task_intent,
        work_plan=work_plan,
    )
    if state is not None:
        state.metadata.update({"source": "goal_command", "schema_version": 1})
        await agent._save_work_state(state)
    await emit_active_task_command_event(
        agent,
        session_id,
        "set_goal",
        applied=True,
        detail={
            "text_preview": agent._format_log_preview(goal, 160),
            "work_state_created": state is not None,
        },
    )
    return rendered


def task_intent_for_explicit_goal(agent: Any, goal_text: str) -> TaskIntent:
    """Force an explicit `/goal` objective into actionable task state."""
    base_intent = agent.task_intents.classify(goal_text)
    done_criteria = base_intent.done_criteria or (
        "the goal is completed or a clear blocker is recorded",
    )
    return TaskIntent(
        kind="task",
        objective=goal_text,
        constraints=base_intent.constraints,
        done_criteria=done_criteria,
        needs_clarification=False,
        verification_hint=base_intent.verification_hint,
        long_running=True,
        expects_code_change=base_intent.expects_code_change,
        expects_verification=base_intent.expects_verification,
    )


async def activate_active_task(agent: Any, session_id: str) -> str | None:
    """Mark the current ACTIVE_TASK as active again."""
    rendered = await agent.active_task_commands.activate(session_id)
    await emit_active_task_command_event(agent, session_id, "activate", applied=rendered is not None)
    return rendered


async def reopen_active_task(agent: Any, session_id: str) -> str | None:
    """Reopen a terminal ACTIVE_TASK and resume it as active."""
    rendered = await agent.active_task_commands.reopen(session_id)
    await emit_active_task_command_event(agent, session_id, "reopen", applied=rendered is not None)
    return rendered


async def block_active_task(agent: Any, session_id: str, reason: str) -> str | None:
    """Mark the current ACTIVE_TASK as blocked with one explicit reason."""
    rendered = await agent.active_task_commands.block(session_id, reason)
    await emit_active_task_command_event(
        agent,
        session_id,
        "block",
        applied=rendered is not None,
        detail={"reason_preview": agent._format_log_preview(reason, 160)},
    )
    return rendered


async def wait_on_active_task(agent: Any, session_id: str, question: str) -> str | None:
    """Mark the current ACTIVE_TASK as waiting for user input."""
    rendered = await agent.active_task_commands.wait_on(session_id, question)
    await emit_active_task_command_event(
        agent,
        session_id,
        "wait_on",
        applied=rendered is not None,
        detail={"question_preview": agent._format_log_preview(question, 160)},
    )
    return rendered


async def set_active_task_current_step(agent: Any, session_id: str, step_text: str) -> str | None:
    """Replace the current step for the active task."""
    rendered = await agent.active_task_commands.set_current_step(session_id, step_text)
    await emit_active_task_command_event(
        agent,
        session_id,
        "set_current_step",
        applied=rendered is not None,
        detail={"step_preview": agent._format_log_preview(step_text, 160)},
    )
    return rendered


async def set_active_task_next_step(agent: Any, session_id: str, step_text: str) -> str | None:
    """Replace the planned next step for the active task."""
    rendered = await agent.active_task_commands.set_next_step(session_id, step_text)
    await emit_active_task_command_event(
        agent,
        session_id,
        "set_next_step",
        applied=rendered is not None,
        detail={"step_preview": agent._format_log_preview(step_text, 160)},
    )
    return rendered


async def advance_active_task(agent: Any, session_id: str) -> str | None:
    """Promote the next step into the current step and mark the previous step complete."""
    rendered = await agent.active_task_commands.advance(session_id)
    await emit_active_task_command_event(agent, session_id, "advance", applied=rendered is not None)
    return rendered


async def complete_active_task_step(agent: Any, session_id: str, next_step_override: str | None = None) -> str | None:
    """Complete the current step and either advance or finish the task."""
    rendered = await agent.active_task_commands.complete_step(session_id, next_step_override=next_step_override)
    detail = None
    if next_step_override is not None:
        detail = {"next_step_preview": agent._format_log_preview(next_step_override, 160)}
    await emit_active_task_command_event(
        agent,
        session_id,
        "complete_step",
        applied=rendered is not None,
        detail=detail,
    )
    return rendered


async def mark_active_task_status(agent: Any, session_id: str, status: str) -> str | None:
    """Set the current ACTIVE_TASK status when one exists."""
    rendered = await agent.active_task_commands.mark_status(session_id, status)
    await emit_active_task_command_event(
        agent,
        session_id,
        "mark_status",
        applied=rendered is not None,
        detail={"target_status": status},
    )
    return rendered


async def reset_active_task(agent: Any, session_id: str) -> None:
    """Clear the current ACTIVE_TASK state for one session."""
    await agent.active_task_commands.reset(session_id)
    await agent._clear_work_state(session_id)
    await emit_active_task_command_event(agent, session_id, "reset", applied=True)
