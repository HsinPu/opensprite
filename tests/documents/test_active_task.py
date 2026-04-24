import asyncio

from opensprite.config.schema import Config, DocumentLlmConfig
from opensprite.documents.active_task import (
    ActiveTaskConsolidator,
    DEFAULT_ACTIVE_TASK_CONTENT,
    build_initial_active_task_block,
    is_task_worthy_message,
    should_replace_active_task,
    create_active_task_store,
)
from opensprite.llms.base import LLMResponse
from opensprite.storage.base import StoredMessage


class FakeStorage:
    def __init__(self, messages_by_chat):
        self.messages_by_chat = messages_by_chat

    async def get_messages(self, chat_id, limit=None):
        messages = list(self.messages_by_chat[chat_id])
        return messages[-limit:] if limit is not None else messages

    async def get_message_count(self, chat_id):
        return len(self.messages_by_chat[chat_id])

    async def get_messages_slice(self, chat_id, *, start_index=0, end_index=None):
        return list(self.messages_by_chat[chat_id][start_index:end_index])


class FakeProvider:
    def __init__(self):
        self.prompts = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        prompt = messages[1]["content"]
        self.prompts.append(prompt)
        status = "waiting_user" if "Need the final target branch" in prompt else "active"
        content = (
            f"- Status: {status}\n"
            "- Goal: Ship the refactor safely\n"
            "- Deliverable: merged refactor\n"
            "- Definition of done:\n"
            "  - tests pass\n"
            "- Constraints:\n"
            "  - keep scope tight\n"
            "- Assumptions:\n"
            "  - user wants minimal changes\n"
            "- Plan:\n"
            "  1. inspect current implementation\n"
            "  2. apply the smallest correct fix\n"
            "  3. verify the behavior\n"
            "- Current step: 1. inspect current implementation\n"
            "- Next step: 2. apply the smallest correct fix\n"
            "- Completed steps:\n"
            "  - none\n"
            "- Open questions:\n"
            "  - none"
        )
        return LLMResponse(content=content, model=model or "fake-model")


def test_create_active_task_store_bootstraps_default_content(tmp_path):
    store = create_active_task_store(tmp_path / "home", "telegram:user-a")

    text = store.read_text()

    assert "# ACTIVE_TASK.md - Current Task Contract" in text
    assert DEFAULT_ACTIVE_TASK_CONTENT in text
    assert store.read_status() == "inactive"
    assert store.get_context("telegram:user-a") == ""


def test_active_task_consolidator_updates_per_session_files(tmp_path):
    storage = FakeStorage(
        {
            "telegram:user-a": [
                StoredMessage(role="user", content="Refactor the agent in small safe steps.", timestamp=1.0),
                StoredMessage(role="assistant", content="I will inspect, patch, and verify.", timestamp=2.0),
            ],
            "telegram:user-b": [
                StoredMessage(role="user", content="Need the final target branch before I can finish.", timestamp=1.0),
                StoredMessage(role="assistant", content="I am blocked pending that branch.", timestamp=2.0),
            ],
        }
    )
    provider = FakeProvider()
    consolidator = ActiveTaskConsolidator(
        storage=storage,
        provider=provider,
        model="fake-model",
        active_task_store_factory=lambda chat_id: create_active_task_store(tmp_path / "home", chat_id),
        threshold=2,
        lookback_messages=10,
        llm=DocumentLlmConfig(**Config.load_template_data()["active_task"]["llm"]),
    )

    async def scenario():
        await consolidator.maybe_update("telegram:user-a")
        await consolidator.maybe_update("telegram:user-b")

    asyncio.run(scenario())

    task_a = create_active_task_store(tmp_path / "home", "telegram:user-a")
    task_b = create_active_task_store(tmp_path / "home", "telegram:user-b")

    assert task_a.read_status() == "active"
    assert task_b.read_status() == "waiting_user"
    assert "Ship the refactor safely" in task_a.get_context("telegram:user-a")
    assert "Ship the refactor safely" in task_b.get_context("telegram:user-b")
    assert task_a.active_task_file != task_b.active_task_file


def test_active_task_switch_detection_requires_explicit_task_change_signal():
    current = build_initial_active_task_block("Refactor the agent in small safe steps.")
    assert current is not None

    assert should_replace_active_task(current, "改成先幫我檢查 MCP lifecycle") is True
    assert should_replace_active_task(current, "接下來幫我整理 web channel") is True
    assert should_replace_active_task(current, "continue with the refactor") is False


def test_task_worthy_classifier_skips_plain_chat_and_keeps_work_requests():
    assert is_task_worthy_message("hello") is False
    assert is_task_worthy_message("你覺得這樣可以嗎？") is False
    assert is_task_worthy_message("幫我解釋一下這是什麼") is False
    assert is_task_worthy_message("幫我分析 agent 核心流程") is True
    assert is_task_worthy_message("Refactor the agent in small safe steps.") is True
    assert is_task_worthy_message("Can you review this architecture and suggest fixes?") is True


def test_initial_active_task_seed_skips_non_task_messages():
    assert build_initial_active_task_block("hello") is None
    assert build_initial_active_task_block("你覺得這樣可以嗎？") is None
    assert build_initial_active_task_block("幫我解釋一下這是什麼") is None
