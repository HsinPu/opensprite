import asyncio

from agent_test_helpers import FakeContextBuilder, make_agent_loop
from opensprite.documents.active_task import create_active_task_store
from opensprite.documents.recent_summary import RecentSummaryStore
from opensprite.storage.base import StoredMessage
from opensprite.storage.memory import MemoryStorage


class FakeSearchStore:
    def __init__(self):
        self.cleared = []

    async def clear_session(self, session_id: str) -> None:
        self.cleared.append(session_id)


def test_reset_history_only_clears_target_session(tmp_path):
    async def scenario():
        storage = MemoryStorage()
        await storage.add_message("telegram:user-a", StoredMessage(role="user", content="A1", timestamp=1.0))
        await storage.add_message("telegram:user-b", StoredMessage(role="user", content="B1", timestamp=2.0))

        search_store = FakeSearchStore()
        agent = make_agent_loop(
            tmp_path,
            storage=storage,
            context_builder=FakeContextBuilder(
                tmp_path,
                app_home=tmp_path / "home",
                tool_workspace=tmp_path / "workspace",
            ),
            search_store=search_store,
        )

        summary_store = RecentSummaryStore(agent.memory.memory_base, app_home=agent.app_home, workspace_root=agent.tool_workspace)
        summary_store.write("telegram:user-a", "# Active Threads\n- stale context")
        summary_store.write("telegram:user-b", "# Active Threads\n- keep context")
        summary_store.set_processed_index("telegram:user-a", 5)
        summary_store.set_processed_index("telegram:user-b", 7)
        task_a = create_active_task_store(agent.app_home, "telegram:user-a", workspace_root=agent.tool_workspace)
        task_b = create_active_task_store(agent.app_home, "telegram:user-b", workspace_root=agent.tool_workspace)
        task_a.write_managed_block(
            "- Status: active\n- Goal: Fix chat A\n- Deliverable: patch\n- Definition of done:\n  - done\n- Constraints:\n  - none\n- Assumptions:\n  - none\n- Plan:\n  1. inspect\n- Current step: 1. inspect\n- Next step: 1. inspect\n- Completed steps:\n  - none\n- Open questions:\n  - none"
        )
        task_b.write_managed_block(
            "- Status: active\n- Goal: Keep chat B\n- Deliverable: notes\n- Definition of done:\n  - done\n- Constraints:\n  - none\n- Assumptions:\n  - none\n- Plan:\n  1. inspect\n- Current step: 1. inspect\n- Next step: 1. inspect\n- Completed steps:\n  - none\n- Open questions:\n  - none"
        )

        await agent.reset_history("telegram:user-a")

        messages_a = await storage.get_messages("telegram:user-a")
        messages_b = await storage.get_messages("telegram:user-b")
        return messages_a, messages_b, search_store.cleared, summary_store, task_a, task_b

    messages_a, messages_b, cleared, summary_store, task_a, task_b = asyncio.run(scenario())

    assert messages_a == []
    assert [message.content for message in messages_b] == ["B1"]
    assert cleared == ["telegram:user-a"]
    assert summary_store.read("telegram:user-a") == ""
    assert summary_store.read("telegram:user-b") == "# Active Threads\n- keep context"
    assert summary_store.get_processed_index("telegram:user-a") == 0
    assert summary_store.get_processed_index("telegram:user-b") == 7
    assert task_a.read_status() == "inactive"
    assert task_b.read_status() == "active"
