import asyncio

import pytest

from opensprite.questions import QUESTION_REJECT_ANSWER, QuestionRequestNotFound, QuestionRequestService
from opensprite.storage import MemoryStorage, StoredWorkState


def test_question_request_service_lists_waiting_questions_across_channels():
    async def scenario():
        storage = MemoryStorage()
        await storage.upsert_work_state(
            StoredWorkState(
                session_id="telegram:42",
                objective="clarify telegram task",
                kind="general",
                status="waiting_user",
                blockers=("Which branch should I use?",),
                created_at=10.0,
                updated_at=12.0,
            )
        )
        await storage.upsert_work_state(
            StoredWorkState(
                session_id="web:browser-1",
                objective="active web task",
                kind="general",
                status="active",
                blockers=("ignored",),
                created_at=10.0,
                updated_at=13.0,
            )
        )
        service = QuestionRequestService(storage=storage, enqueue=lambda message: None)
        return await service.list_pending()

    questions = asyncio.run(scenario())

    assert questions == [
        {
            "request_id": "work:telegram:42:12000",
            "session_id": "telegram:42",
            "channel": "telegram",
            "external_chat_id": "42",
            "question": "Which branch should I use?",
            "status": "pending",
            "objective": "clarify telegram task",
            "created_at": 10.0,
            "updated_at": 12.0,
        }
    ]


def test_question_request_service_replies_to_original_channel_session():
    async def scenario():
        storage = MemoryStorage()
        sent = []

        async def enqueue(message):
            sent.append(message)

        await storage.upsert_work_state(
            StoredWorkState(
                session_id="telegram:42",
                objective="clarify telegram task",
                kind="general",
                status="waiting_user",
                blockers=("Which branch should I use?",),
                created_at=10.0,
                updated_at=12.0,
            )
        )
        service = QuestionRequestService(
            storage=storage,
            enqueue=enqueue,
            sender_id="test-question",
            sender_name="Test question service",
        )
        question = await service.reply("work:telegram:42:12000", "Use main.")
        rejected = await service.reject("work:telegram:42:12000")
        return question, rejected, sent

    question, rejected, sent = asyncio.run(scenario())

    assert question["channel"] == "telegram"
    assert rejected["request_id"] == "work:telegram:42:12000"
    assert [message.text for message in sent] == ["Use main.", QUESTION_REJECT_ANSWER]
    assert [message.channel for message in sent] == ["telegram", "telegram"]
    assert [message.external_chat_id for message in sent] == ["42", "42"]
    assert [message.session_id for message in sent] == ["telegram:42", "telegram:42"]
    assert sent[0].metadata == {"question_request_id": "work:telegram:42:12000"}
    assert sent[1].metadata == {"question_request_id": "work:telegram:42:12000", "question_rejected": True}


def test_question_request_service_raises_for_missing_request():
    async def scenario():
        service = QuestionRequestService(storage=MemoryStorage(), enqueue=lambda message: None)
        await service.reply("missing", "answer")

    with pytest.raises(QuestionRequestNotFound):
        asyncio.run(scenario())
