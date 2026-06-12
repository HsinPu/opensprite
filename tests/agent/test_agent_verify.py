import asyncio

from agent_test_helpers import make_agent_loop
from opensprite.agent.verification_runner import verification_result_is_tool_error
from opensprite.tools.result_status import classify_tool_result_status


def test_verification_tool_error_uses_structured_status():
    assert verification_result_is_tool_error({"status": "failed"}) is True
    assert verification_result_is_tool_error({"status": "timed_out"}) is True
    assert verification_result_is_tool_error({"status": "error"}) is True
    assert verification_result_is_tool_error({"status": "skipped"}) is False
    assert verification_result_is_tool_error({"status": "unknown"}) is False
    assert verification_result_is_tool_error({"status": "passed"}) is False


def test_run_verify_reports_missing_run_context(tmp_path):
    agent = make_agent_loop(tmp_path)

    result = asyncio.run(agent.run_verify(action="pytest"))
    status = classify_tool_result_status(result.content)

    assert result.had_tool_error is True
    assert status.ok is False
    assert status.error_type == "VerifyToolError"
    assert status.category == "missing_run_context"
    assert "No active run is available" in status.error
