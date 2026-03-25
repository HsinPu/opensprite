"""Global USER.md profile store and consolidator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..storage import StoredMessage, StorageProvider
from ..utils.log import logger
from .base import ConversationConsolidator
from .managed import ManagedMarkdownDocument
from .state import JsonProgressStore


AUTO_PROFILE_HEADER = "## Auto-managed Profile"
START_MARKER = "<!-- OPENSPRITE:USER_PROFILE:START -->"
END_MARKER = "<!-- OPENSPRITE:USER_PROFILE:END -->"
DEFAULT_MANAGED_CONTENT = "- No learned user profile details yet."
AUTO_PROFILE_INTRO = (
    "This section is maintained by OpenSprite. Review it and edit the manual sections below if needed."
)


class UserProfileStore:
    """Persist the global USER.md profile and its consolidation state."""

    def __init__(self, user_profile_file: Path, state_file: Path):
        self.user_profile_file = Path(user_profile_file).expanduser()
        self.state = JsonProgressStore(state_file)
        self.document = ManagedMarkdownDocument(
            self.user_profile_file,
            start_marker=START_MARKER,
            end_marker=END_MARKER,
            default_content=DEFAULT_MANAGED_CONTENT,
            heading=AUTO_PROFILE_HEADER,
            intro=AUTO_PROFILE_INTRO,
            anchor_heading="## Identity",
            bootstrap_text="# User Profile\n\n",
        )

    def read_text(self) -> str:
        return self.document.read_text()

    def read_managed_block(self) -> str:
        return self.document.read_managed_block()

    def write_managed_block(self, content: str) -> None:
        self.document.write_managed_block(content)

    def load_state(self) -> dict[str, int]:
        return self.state.load_state()

    def save_state(self, state: dict[str, int]) -> None:
        self.state.save_state(state)

    def get_processed_index(self, chat_id: str) -> int:
        return self.state.get_processed_index(chat_id)

    def set_processed_index(self, chat_id: str, index: int) -> None:
        self.state.set_processed_index(chat_id, index)


_SAVE_USER_PROFILE_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_user_profile",
            "description": "Update the auto-managed USER.md profile block.",
            "parameters": {
                "type": "object",
                "properties": {
                    "profile_update": {
                        "type": "string",
                        "description": (
                            "Replacement markdown for the auto-managed USER.md block. "
                            "Keep it concise, stable, and free of secrets."
                        ),
                    }
                },
                "required": ["profile_update"],
            },
        },
    }
]


def _format_messages(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "?")).upper()
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


async def consolidate_user_profile(
    profile_store: UserProfileStore,
    messages: list[dict[str, Any]],
    provider,
    model: str,
) -> bool:
    """Update the global USER.md managed block from conversation history."""
    if not messages:
        return True

    current_profile = profile_store.read_managed_block()
    transcript = _format_messages(messages)
    if not transcript:
        return True

    prompt = f"""Review this conversation and update the global user profile.

Current auto-managed USER.md block:
{current_profile or '(empty)'}

Conversation to analyze:
{transcript}

Rules:
- Capture only stable preferences, work context, or repeated habits.
- Do not store secrets, API keys, access tokens, passwords, or private file contents.
- Do not store one-off tasks or temporary requests.
- Prefer explicit facts and durable preferences over guesses.
- Return concise markdown bullets or short sections suitable for USER.md.
- If nothing meaningful changed, return the current profile unchanged.
"""

    try:
        response = await provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You maintain the global USER.md profile for an assistant. "
                        "Call save_user_profile with the updated auto-managed block."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            tools=_SAVE_USER_PROFILE_TOOL,
            model=model,
            temperature=0.1,
            max_tokens=1200,
        )

        if not response.tool_calls:
            logger.warning("User profile consolidation: LLM did not call save_user_profile")
            return False

        args = response.tool_calls[0].arguments
        if isinstance(args, str):
            args = json.loads(args)

        update = str(args.get("profile_update", "")).strip()
        if not update:
            logger.warning("User profile consolidation: empty profile_update payload")
            return False

        if update != current_profile:
            profile_store.write_managed_block(update)
            logger.info("USER.md profile updated ({} chars)", len(update))

        return True
    except Exception as exc:
        logger.error("User profile consolidation failed: {}", exc)
        return False


class UserProfileConsolidator(ConversationConsolidator):
    """Manage incremental USER.md updates from stored chat history."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        provider,
        model: str,
        profile_store: UserProfileStore,
        threshold: int = 30,
        lookback_messages: int = 50,
        enabled: bool = True,
    ):
        self.storage = storage
        self.provider = provider
        self.model = model
        self.profile_store = profile_store
        self.threshold = max(1, threshold)
        self.lookback_messages = max(1, lookback_messages)
        self.enabled = enabled

    @staticmethod
    def _to_message_dict(message: StoredMessage) -> dict[str, Any]:
        return {
            "role": message.role,
            "content": message.content,
            "timestamp": message.timestamp,
            "metadata": dict(message.metadata or {}),
        }

    async def maybe_update(self, chat_id: str) -> None:
        if not self.enabled:
            return

        messages = await self.storage.get_messages(chat_id)
        message_count = len(messages)
        last_processed = self.profile_store.get_processed_index(chat_id)
        if last_processed > message_count:
            self.profile_store.set_processed_index(chat_id, message_count)
            return

        pending = message_count - last_processed
        if pending < self.threshold:
            return

        end_index = min(message_count, last_processed + self.lookback_messages)
        chunk = messages[last_processed:end_index]
        if not chunk:
            return

        logger.info("[{}] Updating USER.md profile from {} messages", chat_id, len(chunk))
        success = await consolidate_user_profile(
            profile_store=self.profile_store,
            messages=[self._to_message_dict(message) for message in chunk],
            provider=self.provider,
            model=self.model,
        )
        if success:
            self.profile_store.set_processed_index(chat_id, end_index)
