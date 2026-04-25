import asyncio

from opensprite.config.schema import Config, DocumentLlmConfig
from opensprite.documents.active_task import (
    ActiveTaskConsolidator,
    DEFAULT_ACTIVE_TASK_CONTENT,
    build_active_task_execution_guidance,
    build_initial_active_task_block,
    is_task_worthy_message,
    normalize_active_task_block,
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
        enabled=True,
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


def test_active_task_consolidator_prompt_includes_tool_evidence(tmp_path):
    storage = FakeStorage(
        {
            "telegram:user-a": [
                StoredMessage(role="user", content="Run the tests and confirm the refactor is safe.", timestamp=1.0),
                StoredMessage(
                    role="tool",
                    content="================ 12 passed in 1.23s ================",
                    timestamp=2.0,
                    tool_name="exec",
                    metadata={"tool_args": {"command": "python -m pytest tests/agent"}},
                ),
            ]
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
        enabled=True,
        llm=DocumentLlmConfig(**Config.load_template_data()["active_task"]["llm"]),
    )

    asyncio.run(consolidator.maybe_update("telegram:user-a"))

    prompt = provider.prompts[0]
    assert "[TOOL:EXEC command=python -m pytest tests/agent]" in prompt
    assert "12 passed" in prompt


def test_normalize_active_task_block_clears_steps_when_done():
    normalized = normalize_active_task_block(
        "- Status: done\n"
        "- Goal: Finish the refactor\n"
        "- Deliverable: merged refactor\n"
        "- Definition of done:\n"
        "  - tests pass\n"
        "- Constraints:\n"
        "  - none\n"
        "- Assumptions:\n"
        "  - none\n"
        "- Plan:\n"
        "  1. inspect\n"
        "- Current step: 1. inspect\n"
        "- Next step: 2. verify\n"
        "- Completed steps:\n"
        "  - inspect\n"
        "- Open questions:\n"
        "  - waiting for review"
    )

    assert "- Current step: not set" in normalized
    assert "- Next step: not set" in normalized
    assert "- Open questions:\n  - none" in normalized


def test_normalize_active_task_block_preserves_terminal_manual_status():
    normalized = normalize_active_task_block(
        "- Status: active\n"
        "- Goal: New goal\n"
        "- Deliverable: something\n"
        "- Definition of done:\n"
        "  - done\n"
        "- Constraints:\n"
        "  - none\n"
        "- Assumptions:\n"
        "  - none\n"
        "- Plan:\n"
        "  1. inspect\n"
        "- Current step: 1. inspect\n"
        "- Next step: 2. verify\n"
        "- Completed steps:\n"
        "  - none\n"
        "- Open questions:\n"
        "  - none",
        previous_block=(
            "- Status: done\n"
            "- Goal: Old goal\n"
            "- Deliverable: old deliverable\n"
            "- Definition of done:\n"
            "  - done\n"
            "- Constraints:\n"
            "  - none\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "- Current step: not set\n"
            "- Next step: not set\n"
            "- Completed steps:\n"
            "  - inspect\n"
            "- Open questions:\n"
            "  - none"
        ),
    )

    assert "- Status: done" in normalized
    assert "- Current step: not set" in normalized


def test_normalize_active_task_block_disallows_auto_cancel_from_active_state():
    normalized = normalize_active_task_block(
        "- Status: cancelled\n"
        "- Goal: Keep the agent on task\n"
        "- Deliverable: output\n"
        "- Definition of done:\n"
        "  - done\n"
        "- Constraints:\n"
        "  - none\n"
        "- Assumptions:\n"
        "  - none\n"
        "- Plan:\n"
        "  1. inspect\n"
        "- Current step: inspect\n"
        "- Next step: verify\n"
        "- Completed steps:\n"
        "  - none\n"
        "- Open questions:\n"
        "  - none",
        previous_block=(
            "- Status: active\n"
            "- Goal: Keep the agent on task\n"
            "- Deliverable: output\n"
            "- Definition of done:\n"
            "  - done\n"
            "- Constraints:\n"
            "  - none\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "- Current step: inspect\n"
            "- Next step: verify\n"
            "- Completed steps:\n"
            "  - none\n"
            "- Open questions:\n"
            "  - none"
        ),
    )

    assert "- Status: active" in normalized


def test_normalize_active_task_block_allows_manual_cancel_override():
    normalized = normalize_active_task_block(
        "- Status: cancelled\n"
        "- Goal: Keep the agent on task\n"
        "- Deliverable: output\n"
        "- Definition of done:\n"
        "  - done\n"
        "- Constraints:\n"
        "  - none\n"
        "- Assumptions:\n"
        "  - none\n"
        "- Plan:\n"
        "  1. inspect\n"
        "- Current step: inspect\n"
        "- Next step: verify\n"
        "- Completed steps:\n"
        "  - none\n"
        "- Open questions:\n"
        "  - none",
        previous_block=(
            "- Status: active\n"
            "- Goal: Keep the agent on task\n"
            "- Deliverable: output\n"
            "- Definition of done:\n"
            "  - done\n"
            "- Constraints:\n"
            "  - none\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "- Current step: inspect\n"
            "- Next step: verify\n"
            "- Completed steps:\n"
            "  - none\n"
            "- Open questions:\n"
            "  - none"
        ),
        allow_terminal_override=True,
    )

    assert "- Status: cancelled" in normalized


def test_normalize_active_task_block_allows_auto_return_from_blocked_to_active():
    normalized = normalize_active_task_block(
        "- Status: active\n"
        "- Goal: Keep the agent on task\n"
        "- Deliverable: output\n"
        "- Definition of done:\n"
        "  - done\n"
        "- Constraints:\n"
        "  - none\n"
        "- Assumptions:\n"
        "  - none\n"
        "- Plan:\n"
        "  1. inspect\n"
        "- Current step: verify\n"
        "- Next step: not set\n"
        "- Completed steps:\n"
        "  - inspect\n"
        "- Open questions:\n"
        "  - old blocker",
        previous_block=(
            "- Status: blocked\n"
            "- Goal: Keep the agent on task\n"
            "- Deliverable: output\n"
            "- Definition of done:\n"
            "  - done\n"
            "- Constraints:\n"
            "  - none\n"
            "- Assumptions:\n"
            "  - none\n"
            "- Plan:\n"
            "  1. inspect\n"
            "- Current step: inspect\n"
            "- Next step: verify\n"
            "- Completed steps:\n"
            "  - none\n"
            "- Open questions:\n"
            "  - old blocker"
        ),
    )

    assert "- Status: active" in normalized
    assert "- Open questions:\n  - none" in normalized


def test_active_task_execution_guidance_expands_waiting_user_state():
    guidance = build_active_task_execution_guidance(
        "- Status: waiting_user\n"
        "- Goal: Finish the refactor\n"
        "- Deliverable: merged refactor\n"
        "- Definition of done:\n"
        "  - tests pass\n"
        "- Constraints:\n"
        "  - none\n"
        "- Assumptions:\n"
        "  - none\n"
        "- Plan:\n"
        "  1. inspect\n"
        "- Current step: 2. apply the fix\n"
        "- Next step: 3. run tests\n"
        "- Completed steps:\n"
        "  - inspect\n"
        "- Open questions:\n"
        "  - which target branch should be used?"
    )

    assert "Current task status: waiting_user" in guidance
    assert "do not continue execution until the user provides the missing input" in guidance


def test_active_task_store_records_and_renders_event_history(tmp_path):
    store = create_active_task_store(tmp_path / "home", "telegram:user-a")
    store.write_managed_block(
        "- Status: active\n"
        "- Goal: Keep the agent on task\n"
        "- Deliverable: stable execution\n"
        "- Definition of done:\n"
        "  - task stays focused\n"
        "- Constraints:\n"
        "  - minimal changes\n"
        "- Assumptions:\n"
        "  - none\n"
        "- Plan:\n"
        "  1. inspect\n"
        "- Current step: inspect\n"
        "- Next step: verify\n"
        "- Completed steps:\n"
        "  - none\n"
        "- Open questions:\n"
        "  - none"
    )
    store.append_event("set", "user", details={"task": "Keep the agent on task"})
    store.append_event("advance", "user", details={"completed_step": "inspect", "new_current_step": "verify"})

    history = store.render_history(limit=10)

    assert history is not None
    assert "# Active Task History" in history
    assert "set (manual)" in history
    assert "advance (manual)" in history
    assert "completed_step: inspect" in history


def test_active_task_history_uses_readable_source_labels(tmp_path):
    store = create_active_task_store(tmp_path / "home", "telegram:user-a")
    store.write_managed_block(
        "- Status: active\n"
        "- Goal: Keep the agent on task\n"
        "- Deliverable: stable execution\n"
        "- Definition of done:\n"
        "  - task stays focused\n"
        "- Constraints:\n"
        "  - minimal changes\n"
        "- Assumptions:\n"
        "  - none\n"
        "- Plan:\n"
        "  1. inspect\n"
        "- Current step: inspect\n"
        "- Next step: verify\n"
        "- Completed steps:\n"
        "  - none\n"
        "- Open questions:\n"
        "  - none"
    )
    store.append_event("set", "user")
    store.append_event("auto_update", "auto")
    store.append_event("auto_direct_transition", "immediate")

    history = store.render_history(limit=10)

    assert "set (manual)" in history
    assert "auto_update (auto)" in history
    assert "auto_direct_transition (immediate)" in history


def test_active_task_consolidator_records_auto_update_event(tmp_path):
    storage = FakeStorage(
        {
            "telegram:user-a": [
                StoredMessage(role="user", content="Need the final target branch before I can finish.", timestamp=1.0),
                StoredMessage(role="assistant", content="I am blocked pending that branch.", timestamp=2.0),
            ]
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
        enabled=True,
        llm=DocumentLlmConfig(**Config.load_template_data()["active_task"]["llm"]),
    )

    asyncio.run(consolidator.maybe_update("telegram:user-a"))

    store = create_active_task_store(tmp_path / "home", "telegram:user-a")
    events = store.read_events()

    assert events[-1]["event_type"] == "auto_update"
    assert events[-1]["source"] == "auto"
    assert events[-1]["details"]["new_status"] == "waiting_user"
