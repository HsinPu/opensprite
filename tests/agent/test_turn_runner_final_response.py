from opensprite.agent.completion_blocker_policy import CompletionBlockerMessages
from opensprite.agent.completion_gate import CompletionGateResult
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.quality_gate import TERSE_FINAL_ANSWER_REASON
from opensprite.agent.task_artifact import TaskArtifact
from opensprite.agent.task_contract import TaskContract
from opensprite.agent.turn_runner import (
    _final_response_after_exhausted_continuation,
    _message_with_runtime_context,
    _source_finalization_available,
)


COMPLETION_BLOCKER_MESSAGES = CompletionBlockerMessages(
    intro="TEST COMPLETION BLOCKER INTRO",
    reason_prefix="TEST REASON: ",
    detail_header="TEST DETAIL",
    missing_evidence_header="TEST MISSING",
    stop_notice="TEST STOP NOTICE",
)


def test_message_with_runtime_context_adds_cli_gateway_and_snapshot_details():
    message = _message_with_runtime_context(
        "check healthz",
        {
            "source": "cli_via_web",
            "gateway_url": "http://127.0.0.1:8765",
            "workspace_snapshot": {
                "path": "repo",
                "source": "C:\\Users\\win10\\Desktop\\HsinPuRepository\\opensprite",
            },
        },
    )

    assert "check healthz" in message
    assert "http://127.0.0.1:8765" in message
    assert "http://127.0.0.1:8765/healthz" in message
    assert "`repo/`" in message
    assert "omit VCS internals" in message


def test_exhausted_continuation_replaces_nonfinal_response_with_blocker():
    response = _final_response_after_exhausted_continuation(
        response="Let me keep checking that.",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason=TERSE_FINAL_ANSWER_REASON,
            active_task_detail="Provide a substantive final answer.",
            missing_evidence=("substantive final answer",),
        ),
        auto_continue_attempts=3,
        completion_blocker_messages=COMPLETION_BLOCKER_MESSAGES,
    )

    assert "TEST COMPLETION BLOCKER INTRO" in response
    assert f"TEST REASON: {TERSE_FINAL_ANSWER_REASON}" in response
    assert "TEST DETAIL" in response
    assert "Provide a substantive final answer." in response
    assert "TEST MISSING" in response
    assert "substantive final answer" in response


def test_exhausted_continuation_keeps_complete_response():
    response = _final_response_after_exhausted_continuation(
        response="Done with cited sources.",
        completion_result=CompletionGateResult(status="complete", reason="done"),
        auto_continue_attempts=0,
        completion_blocker_messages=COMPLETION_BLOCKER_MESSAGES,
    )

    assert response == "Done with cited sources."


def test_exhausted_continuation_does_not_build_source_template_answer():
    response = _final_response_after_exhausted_continuation(
        response="Sorry, I did not produce a displayable reply.",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason="Web research succeeded but no final answer was delivered.",
            missing_evidence=("Substantive final answer with source references",),
        ),
        auto_continue_attempts=0,
        completion_blocker_messages=COMPLETION_BLOCKER_MESSAGES,
    )

    assert "TEST COMPLETION BLOCKER INTRO" in response
    assert "TEST DETAILS" not in response
    assert "TEST SOURCES" not in response
    assert "https://example.com/agent-trends" not in response


def test_source_finalization_available_for_traceable_web_sources():
    result = ExecutionResult(
        content="Let me keep checking that.",
        task_contract=TaskContract(
            objective="Find 2026 AI agent tools market trends and cite sources.",
            task_type="web_research",
        ),
        task_artifacts=(
            TaskArtifact(
                kind="web_source",
                source_tool="web_research",
                metadata={
                    "sources": [
                        {
                            "tool_name": "web_fetch",
                            "url": "https://example.com/agent-trends",
                            "title": "AI Agent Trends",
                            "snippet": "Agent tools are moving from pilots into governed production workflows.",
                            "content_chars": 1200,
                            "has_main_content": True,
                            "is_too_short": False,
                        }
                    ]
                },
            ),
        ),
    )

    assert _source_finalization_available(
        CompletionGateResult(status="incomplete", reason=TERSE_FINAL_ANSWER_REASON),
        result,
    )


def test_source_finalization_not_available_without_traceable_sources():
    result = ExecutionResult(
        content="Let me keep checking that.",
        task_contract=TaskContract(
            objective="Find 2026 AI agent tools market trends and cite sources.",
            task_type="web_research",
        ),
    )

    assert not _source_finalization_available(
        CompletionGateResult(status="incomplete", reason=TERSE_FINAL_ANSWER_REASON),
        result,
    )
