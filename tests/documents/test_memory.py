import asyncio

from opensprite.documents import memory as memory_module
from opensprite.llms.base import LLMResponse, ToolCall


class FakeMemoryStore:
    def __init__(self, current: str = ""):
        self.current = current
        self.written = None

    def read(self, chat_id: str) -> str:
        return self.current

    def write(self, chat_id: str, content: str) -> None:
        self.written = (chat_id, content)


class FakeProvider:
    def __init__(self, memory_update: str):
        self.memory_update = memory_update
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=0.7, max_tokens=2048, **kwargs):
        self.calls.append({"messages": messages, "tools": tools, "model": model})
        return LLMResponse(
            content="",
            model=model or "fake-model",
            tool_calls=[ToolCall(id="call-1", name="save_memory", arguments={"memory_update": self.memory_update})],
        )


def test_select_consolidation_lines_uses_token_budget(monkeypatch):
    monkeypatch.setattr(memory_module, "_CONSOLIDATION_MESSAGE_TOKEN_BUDGET", 80)
    monkeypatch.setattr(memory_module, "count_text_tokens", lambda text, model=None, encoding_name=None: len(text))

    lines = memory_module._select_consolidation_lines(
        [
            {"role": "user", "content": "old " * 20},
            {"role": "assistant", "content": "recent"},
        ],
        model="fake-model",
    )

    assert lines == ["[ASSISTANT]: recent"]


def test_consolidate_uses_structured_merge_prompt():
    memory_store = FakeMemoryStore(current="# User Preferences\n- likes concise replies")
    provider = FakeProvider(memory_update="# User Preferences\n- likes concise replies\n\n# Ongoing Tasks\n- refactor memory")

    result = asyncio.run(
        memory_module.consolidate(
            memory_store=memory_store,
            chat_id="chat-1",
            messages=[
                {"role": "user", "content": "Please remember that we are refactoring memory handling."},
                {"role": "assistant", "content": "I will keep the memory format structured."},
            ],
            provider=provider,
            model="fake-model",
        )
    )

    assert result is True
    assert memory_store.written is not None

    prompt = provider.calls[0]["messages"][1]["content"]
    assert "Keep the exact section order from the template below." in prompt
    assert "Merge new durable information into the existing memory" in prompt
    assert "# User Preferences" in prompt
    assert "# Ongoing Tasks" in prompt
    assert "New conversation segment:" in prompt
    assert "Last 20 messages" not in prompt
