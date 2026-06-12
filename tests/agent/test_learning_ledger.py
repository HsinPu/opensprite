import asyncio
import json

from agent_test_helpers import make_agent_loop
from opensprite.context.message_history import LearningLedger
from opensprite.context.paths import get_session_learning_state_file


def test_learning_ledger_records_and_ranks_relevant_entries():
    ledger = LearningLedger()
    ledger.record_learning(
        "telegram:room-1",
        kind="skill",
        target_id="pytest-helper",
        summary="Reusable pytest workflow for updating assertions and running focused tests.",
        source_run_id="run-1",
    )
    ledger.record_learning(
        "telegram:room-1",
        kind="memory",
        target_id="memory",
        summary="Updated session memory.",
        source_run_id="run-2",
    )

    entries = ledger.relevant_entries("telegram:room-1", "Please update pytest assertions")

    assert entries
    assert entries[0]["kind"] == "skill"
    assert entries[0]["target_id"] == "pytest-helper"
    context = ledger.build_relevant_context("telegram:room-1", "Please update pytest assertions")
    assert "# Relevant Learned Context" in context
    assert "pytest-helper" in context


def test_agent_loop_marks_read_skill_reuse_in_learning_ledger(tmp_path):
    async def scenario():
        agent = make_agent_loop(tmp_path)
        hook = agent.agent_run_hooks.make_tool_result_hook(
            channel="telegram",
            external_chat_id="room-1",
            session_id="telegram:room-1",
            run_id="run-1",
            enabled=False,
        )
        assert hook is not None
        await hook("read_skill", {"skill_name": "pytest-helper"}, "Skill body")
        agent._finalize_learning_reuse("telegram:room-1", "run-1", True)
        return agent.learning_ledger.recent_entries("telegram:room-1", limit=1)

    entries = asyncio.run(scenario())

    assert entries[0]["kind"] == "skill"
    assert entries[0]["target_id"] == "pytest-helper"
    assert entries[0]["use_count"] == 1
    assert entries[0]["last_outcome"] == "success"


def test_agent_loop_ignores_failed_read_skill_for_learning_ledger(tmp_path):
    async def scenario():
        agent = make_agent_loop(tmp_path)
        hook = agent.agent_run_hooks.make_tool_result_hook(
            channel="telegram",
            external_chat_id="room-1",
            session_id="telegram:room-1",
            run_id="run-1",
            enabled=False,
        )
        assert hook is not None
        result = json.dumps({"ok": False, "error": "skill missing"})
        await hook("read_skill", {"skill_name": "pytest-helper"}, result)
        agent._finalize_learning_reuse("telegram:room-1", "run-1", True)
        return agent.learning_ledger.recent_entries("telegram:room-1", limit=1)

    entries = asyncio.run(scenario())

    assert entries == []


def test_learning_ledger_persists_per_session_file(tmp_path):
    app_home = tmp_path / "home"
    workspace_root = app_home / "workspace"
    ledger = LearningLedger(
        state_path_for_session=lambda session_id: get_session_learning_state_file(
            session_id,
            app_home=app_home,
            workspace_root=workspace_root,
        )
    )

    ledger.record_learning(
        "telegram:room-1",
        kind="skill",
        target_id="pytest-helper",
        summary="Reusable pytest workflow.",
        source_run_id="run-1",
    )

    reloaded = LearningLedger(
        state_path_for_session=lambda session_id: get_session_learning_state_file(
            session_id,
            app_home=app_home,
            workspace_root=workspace_root,
        )
    )
    entries = reloaded.recent_entries("telegram:room-1", limit=1)

    assert entries[0]["target_id"] == "pytest-helper"
    assert get_session_learning_state_file("telegram:room-1", app_home=app_home, workspace_root=workspace_root).exists()
