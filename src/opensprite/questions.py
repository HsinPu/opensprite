"""Channel-agnostic pending question request handling."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .bus.message import UserMessage


QUESTION_REJECT_ANSWER = (
    "I cannot answer that question right now. Please continue with the safest available assumption "
    "or explain what you need next."
)


class QuestionRequestNotFound(KeyError):
    """Raised when a pending question request no longer exists."""


class QuestionRequestService:
    """Find and answer waiting-user questions across any channel session."""

    def __init__(
        self,
        *,
        storage: Any,
        enqueue: Callable[[UserMessage], Awaitable[None]],
        sender_id: str = "question-service",
        sender_name: str = "Question service",
        allowed_channels: set[str] | None = None,
    ):
        self.storage = storage
        self.enqueue = enqueue
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.allowed_channels = {str(channel).strip() for channel in allowed_channels or set() if str(channel).strip()}

    @staticmethod
    def channel_from_session(session_id: str) -> str:
        return str(session_id or "").split(":", 1)[0] or "unknown"

    @staticmethod
    def external_chat_id_from_session(session_id: str) -> str | None:
        parts = str(session_id or "").split(":", 1)
        return parts[1] if len(parts) == 2 and parts[1] else None

    @staticmethod
    def request_id(state: Any) -> str:
        updated_at = int(float(getattr(state, "updated_at", 0) or 0) * 1000)
        return f"work:{state.session_id}:{updated_at}"

    def serialize(self, state: Any) -> dict[str, Any]:
        blockers = [str(item).strip() for item in getattr(state, "blockers", ()) if str(item).strip()]
        question = blockers[0] if blockers else str(getattr(state, "last_next_action", "") or "").strip()
        return {
            "request_id": self.request_id(state),
            "session_id": state.session_id,
            "channel": self.channel_from_session(state.session_id),
            "external_chat_id": self.external_chat_id_from_session(state.session_id),
            "question": question,
            "status": "pending",
            "objective": str(getattr(state, "objective", "") or ""),
            "created_at": float(getattr(state, "created_at", 0) or 0),
            "updated_at": float(getattr(state, "updated_at", 0) or 0),
        }

    async def pending_states(self) -> list[Any]:
        get_work_state = getattr(self.storage, "get_work_state", None)
        get_all_sessions = getattr(self.storage, "get_all_sessions", None)
        if not callable(get_work_state) or not callable(get_all_sessions):
            return []

        states = []
        for session_id in await get_all_sessions():
            if self.allowed_channels and self.channel_from_session(session_id) not in self.allowed_channels:
                continue
            state = await get_work_state(session_id)
            if state is None or getattr(state, "status", "") != "waiting_user":
                continue
            if self.serialize(state)["question"]:
                states.append(state)
        return sorted(states, key=lambda item: float(getattr(item, "updated_at", 0) or 0), reverse=True)

    async def list_pending(self) -> list[dict[str, Any]]:
        return [self.serialize(state) for state in await self.pending_states()]

    async def find_state(self, request_id: str) -> Any | None:
        for state in await self.pending_states():
            if self.request_id(state) == request_id:
                return state
        return None

    async def reply(self, request_id: str, answer: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        state = await self.find_state(request_id)
        if state is None:
            raise QuestionRequestNotFound(request_id)
        external_chat_id = self.external_chat_id_from_session(state.session_id) or state.session_id
        await self.enqueue(
            UserMessage(
                text=answer,
                channel=self.channel_from_session(state.session_id),
                external_chat_id=external_chat_id,
                session_id=state.session_id,
                sender_id=self.sender_id,
                sender_name=self.sender_name,
                metadata={"question_request_id": request_id, **dict(metadata or {})},
            )
        )
        return self.serialize(state)

    async def reject(self, request_id: str) -> dict[str, Any]:
        return await self.reply(request_id, QUESTION_REJECT_ANSWER, metadata={"question_rejected": True})
