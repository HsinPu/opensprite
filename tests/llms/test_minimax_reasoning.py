import asyncio
from types import SimpleNamespace

from opensprite.context.message_history import MessageHistoryService
from opensprite.llms import ChatMessage
from opensprite.llms import minimax as minimax_module
from opensprite.llms.minimax import MiniMaxLLM, _is_minimax_overloaded_error
from opensprite.runs.events import SEARCH_INDEX_MESSAGE_FAILED_EVENT
from opensprite.storage import MemoryStorage, StoredMessage


def test_minimax_chat_preserves_history_reasoning_details_without_extra_body():
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id="response-id",
                model="MiniMax-M2.7",
                object="chat.completion",
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content="final answer",
                            tool_calls=None,
                            reasoning_details=[{"type": "reasoning.text", "text": "thinking"}],
                        ),
                    )
                ],
            )

    provider = MiniMaxLLM(api_key="secret-key", default_model="MiniMax-M2.7")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    response = asyncio.run(
        provider.chat(
            [
                ChatMessage(
                    role="assistant",
                    content="previous answer",
                    reasoning_details=[{"type": "reasoning.text", "text": "previous thinking"}],
                ),
                ChatMessage(role="user", content="continue"),
            ]
        )
    )

    assert response.content == "final answer"
    assert response.reasoning_details == [{"type": "reasoning.text", "text": "thinking"}]
    assert "extra_body" not in calls[0]
    assert calls[0]["messages"][0]["reasoning_details"] == [
        {"type": "reasoning.text", "text": "previous thinking"}
    ]


def test_minimax_uses_configured_base_url():
    provider = MiniMaxLLM(
        api_key="secret-key",
        default_model="MiniMax-M2.7",
        base_url="https://api.minimaxi.com/v1/",
    )

    assert provider.base_url == "https://api.minimaxi.com/v1"
    assert provider._client_kwargs["base_url"] == "https://api.minimaxi.com/v1"


def test_minimax_overload_detection_uses_shared_transient_classifier(monkeypatch):
    calls = []

    def fake_classifier(exc):
        calls.append(exc)
        return True

    error = RuntimeError("provider-specific transient signal")
    monkeypatch.setattr(minimax_module, "looks_like_transient_transport_error", fake_classifier)

    assert _is_minimax_overloaded_error(error) is True
    assert calls == [error]


def test_message_history_restores_reasoning_details_from_metadata():
    storage = MemoryStorage()
    asyncio.run(
        storage.add_message(
            "session-1",
            StoredMessage(
                role="assistant",
                content="final answer",
                timestamp=1,
                metadata={"llm_reasoning_details": [{"type": "reasoning.text", "text": "thinking"}]},
            ),
        )
    )
    service = MessageHistoryService(storage=storage, search_store=None, max_history_getter=lambda: 10)

    history = asyncio.run(service.load_history("session-1"))

    assert history == [
        ChatMessage(
            role="assistant",
            content="final answer",
            reasoning_details=[{"type": "reasoning.text", "text": "thinking"}],
        )
    ]


def test_message_history_emits_search_index_failure_event():
    class FailingSearchStore:
        async def index_message(self, **kwargs):
            raise RuntimeError("index down")

    async def scenario():
        storage = MemoryStorage()
        events = []

        async def emit_index_failure(session_id, event_type, payload):
            events.append((session_id, event_type, payload))

        service = MessageHistoryService(
            storage=storage,
            search_store=FailingSearchStore(),
            max_history_getter=lambda: 10,
            emit_index_failure=emit_index_failure,
        )
        await service.save_message("session-1", "user", "hello", tool_name=None)
        return events

    events = asyncio.run(scenario())

    assert len(events) == 1
    assert events[0][0] == "session-1"
    assert events[0][1] == SEARCH_INDEX_MESSAGE_FAILED_EVENT
    assert events[0][2]["role"] == "user"
    assert events[0][2]["content_len"] == 5
    assert events[0][2]["error"] == "index down"
