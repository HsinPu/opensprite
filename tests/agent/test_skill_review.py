import asyncio
import json

from agent_test_helpers import make_agent_loop
from opensprite.agent.execution import ExecutionResult
from opensprite.documents.curator import build_skill_review_user_content, format_stored_messages_for_transcript
from opensprite.storage.base import StoredMessage


def test_format_stored_messages_for_transcript_includes_tool_name():
    rows = [
        StoredMessage(role="user", content="hi", timestamp=1.0),
        StoredMessage(role="assistant", content="hello", timestamp=2.0),
        StoredMessage(role="tool", content="output", timestamp=3.0, tool_name="read_file"),
    ]
    text = format_stored_messages_for_transcript(rows)
    assert "USER" in text
    assert "ASSISTANT" in text
    assert "[tool:read_file]" in text
    assert "output" in text


def test_build_skill_review_user_content_wraps_transcript():
    body = build_skill_review_user_content("LINE1")
    assert "--- TRANSCRIPT ---" in body
    assert "LINE1" in body
    assert "Nothing to save" in body


def test_skill_review_collects_configured_skill_metadata():
    class Storage:
        async def get_messages(self, session_id, limit=None):
            return [
                StoredMessage(role="user", content="Please remember this workflow and make a skill.", timestamp=1.0),
                StoredMessage(role="assistant", content="Sure, I will save it.", timestamp=2.0),
            ]

    async def execute_messages(log_id, messages, **kwargs):
        await kwargs["on_tool_after_execute"](
            "configure_skill",
            {
                "action": "upsert",
                "skill_name": "pytest-helper",
                "description": "Reusable pytest workflow.",
            },
            "Updated skill 'pytest-helper'.",
        )

    from opensprite.documents.curator import SkillReviewService

    service = SkillReviewService(
        storage=Storage(),
        tools=None,
        transcript_message_limit_getter=lambda: 10,
        max_tool_iterations_getter=lambda: 2,
        build_system_prompt=lambda session_id: "system",
        execute_messages=execute_messages,
    )

    touched = asyncio.run(service.run("chat-a", tool_registry=object()))

    assert touched == [
        {
            "skill_name": "pytest-helper",
            "action": "upsert",
            "description": "Reusable pytest workflow.",
        }
    ]


def test_skill_review_ignores_structured_configure_skill_failure():
    class Storage:
        async def get_messages(self, session_id, limit=None):
            return [
                StoredMessage(role="user", content="Please remember this workflow and make a skill.", timestamp=1.0),
                StoredMessage(role="assistant", content="Sure, I will save it.", timestamp=2.0),
            ]

    async def execute_messages(log_id, messages, **kwargs):
        await kwargs["on_tool_after_execute"](
            "configure_skill",
            {
                "action": "upsert",
                "skill_name": "pytest-helper",
                "description": "Reusable pytest workflow.",
            },
            json.dumps({"ok": False, "error": "skill body was invalid"}),
        )

    from opensprite.documents.curator import SkillReviewService

    service = SkillReviewService(
        storage=Storage(),
        tools=None,
        transcript_message_limit_getter=lambda: 10,
        max_tool_iterations_getter=lambda: 2,
        build_system_prompt=lambda session_id: "system",
        execute_messages=execute_messages,
    )

    touched = asyncio.run(service.run("chat-a", tool_registry=object()))

    assert touched == []


def test_skill_review_scheduler_coalesces_same_session_into_rerun(tmp_path):
    async def scenario():
        agent = make_agent_loop(tmp_path)
        agent._skill_review_tool_registry = lambda: object()

        release = asyncio.Event()
        started = asyncio.Event()
        calls: list[str] = []

        async def fake_run(session_id: str) -> None:
            calls.append(session_id)
            started.set()
            if len(calls) == 1:
                await release.wait()

        agent._run_skill_review = fake_run
        result = ExecutionResult(content="done", executed_tool_calls=agent.config.skill_review_min_tool_calls)

        agent._maybe_schedule_skill_review("chat-a", result)
        await started.wait()
        agent._maybe_schedule_skill_review("chat-a", result)

        release.set()
        await agent.wait_for_background_skill_reviews()
        return calls

    calls = asyncio.run(scenario())

    assert calls == ["chat-a", "chat-a"]


def test_skill_review_scheduler_keeps_different_sessions_separate(tmp_path):
    async def scenario():
        agent = make_agent_loop(tmp_path)
        agent._skill_review_tool_registry = lambda: object()

        release = asyncio.Event()
        started = set()
        calls: list[str] = []

        async def fake_run(session_id: str) -> None:
            calls.append(session_id)
            started.add(session_id)
            if len(started) < 2:
                await asyncio.sleep(0)
            await release.wait()

        agent._run_skill_review = fake_run
        result = ExecutionResult(content="done", executed_tool_calls=agent.config.skill_review_min_tool_calls)

        agent._maybe_schedule_skill_review("chat-a", result)
        agent._maybe_schedule_skill_review("chat-b", result)
        await asyncio.sleep(0)
        assert sorted(agent._skill_review_tasks) == ["chat-a", "chat-b"]

        release.set()
        await agent.wait_for_background_skill_reviews()
        return calls

    calls = asyncio.run(scenario())

    assert sorted(calls) == ["chat-a", "chat-b"]
