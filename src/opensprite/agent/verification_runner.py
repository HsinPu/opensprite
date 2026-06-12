"""Agent deterministic verification runner."""

from __future__ import annotations

from typing import Any

from ..tools.evidence import VERIFICATION_TOOL_NAME
from ..tools.result_status import tool_error_result
from ..tools.verify import classify_verification_result
from .execution import ExecutionResult


async def run_agent_verification(
    agent: Any,
    *,
    action: str = "auto",
    path: str = ".",
    pytest_args: tuple[str, ...] = (),
) -> ExecutionResult:
    """Run deterministic verification through the registered verify tool."""
    session_id = agent._get_current_session_id()
    run_id = agent.turn_context.current_run_id()
    if session_id is None or run_id is None:
        return ExecutionResult(
            content=tool_error_result(
                "No active run is available for deterministic verification.",
                error_type="VerifyToolError",
                category="missing_run_context",
                metadata={"tool_name": VERIFICATION_TOOL_NAME},
            ),
            had_tool_error=True,
        )

    tool_args: dict[str, Any] = {
        "action": str(action or "auto").strip() or "auto",
        "path": str(path or ".").strip() or ".",
    }
    if pytest_args:
        tool_args["pytest_args"] = [str(item) for item in pytest_args if str(item).strip()]

    before = agent.agent_run_hooks.make_tool_progress_hook(
        channel=agent.turn_context.current_channel(),
        external_chat_id=agent.turn_context.current_external_chat_id(),
        session_id=session_id,
        run_id=run_id,
        enabled=True,
    )
    after = agent.agent_run_hooks.make_tool_result_hook(
        channel=agent.turn_context.current_channel(),
        external_chat_id=agent.turn_context.current_external_chat_id(),
        session_id=session_id,
        run_id=run_id,
        enabled=True,
    )

    if before is not None:
        await before(VERIFICATION_TOOL_NAME, tool_args)
    result = await agent.tools.execute(VERIFICATION_TOOL_NAME, tool_args)
    if after is not None:
        await after(VERIFICATION_TOOL_NAME, tool_args, result)

    verification = classify_verification_result(result)
    return ExecutionResult(
        content=result,
        executed_tool_calls=1,
        had_tool_error=verification_result_is_tool_error(verification),
        verification_attempted=bool(verification["attempted"]),
        verification_passed=bool(verification["ok"]),
    )


def verification_result_is_tool_error(verification: dict[str, Any]) -> bool:
    """Return whether a verification status should count as a tool error."""
    return str(verification.get("status") or "").strip().lower() in {
        "error",
        "failed",
        "timed_out",
    }
