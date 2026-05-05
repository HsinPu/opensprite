import asyncio

import opensprite.agent.response_finalizer as response_finalizer_module
from opensprite.agent.response_finalizer import AgentResponseFinalizer
from opensprite.config.schema import LogConfig


class _RunTrace:
    async def record_assistant_message_part(self, *args, **kwargs):
        pass

    async def complete_run(self, *args, **kwargs):
        pass


async def _save_message(*args, **kwargs):
    pass


def _preview(text, *, max_chars=200):
    return text[:max_chars]


def _make_finalizer(*, log_reasoning_details=False):
    return AgentResponseFinalizer(
        run_trace=_RunTrace(),
        save_message=_save_message,
        format_log_preview=_preview,
        log_config=LogConfig(log_reasoning_details=log_reasoning_details),
    )


def test_reasoning_logging_writes_summary_without_full_details(monkeypatch):
    messages = []
    monkeypatch.setattr(response_finalizer_module.logger, "info", lambda *args: messages.append(args))

    asyncio.run(
        _make_finalizer().finalize(
            session_id="session-1",
            run_id="run-1",
            response="answer",
            channel="web",
            external_chat_id=None,
            assistant_metadata={},
            run_part_metadata={},
            run_event_payload={},
            persisted_assistant_metadata={
                "llm_reasoning_details": [
                    {"type": "reasoning.text", "text": "first thought"},
                    {"type": "reasoning.text", "summary": "short"},
                ]
            },
        )
    )

    rendered = [str(item) for message in messages for item in message]
    assert any("LLM reasoning summary" in item for item in rendered)
    assert any(2 in message for message in messages)
    assert not any("first thought" in item for item in rendered)


def test_reasoning_logging_can_write_full_details(monkeypatch):
    messages = []
    monkeypatch.setattr(response_finalizer_module.logger, "info", lambda *args: messages.append(args))

    asyncio.run(
        _make_finalizer(log_reasoning_details=True).finalize(
            session_id="session-1",
            run_id="run-1",
            response="answer",
            channel="web",
            external_chat_id=None,
            assistant_metadata={},
            run_part_metadata={},
            run_event_payload={},
            persisted_assistant_metadata={
                "llm_reasoning_details": [{"type": "reasoning.text", "text": "debug thought"}]
            },
        )
    )

    rendered = [str(item) for message in messages for item in message]
    assert any("LLM reasoning details" in item for item in rendered)
    assert any("debug thought" in item for item in rendered)
