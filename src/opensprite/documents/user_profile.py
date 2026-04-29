"""Per-chat USER.md profile store and consolidator (session workspace root)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..config.schema import DocumentLlmConfig
from ..context.paths import get_bootstrap_dir, get_user_profile_file, get_user_profile_state_file
from ..storage import StoredMessage, StorageProvider
from ..storage.base import get_storage_message_count, get_storage_messages_slice
from ..utils.log import logger
from .base import ConversationConsolidator
from .managed import ManagedMarkdownDocument
from .state import JsonProgressStore


RESPONSE_LANGUAGE_HEADER = "## Response language"
RL_START_MARKER = "<!-- OPENSPRITE:RESPONSE_LANGUAGE:START -->"
RL_END_MARKER = "<!-- OPENSPRITE:RESPONSE_LANGUAGE:END -->"
DEFAULT_RESPONSE_LANGUAGE_CONTENT = "- not set"
RESPONSE_LANGUAGE_INTRO = "This section is maintained by OpenSprite."

AUTO_PROFILE_HEADER = "## Auto-managed Profile"
START_MARKER = "<!-- OPENSPRITE:USER_PROFILE:START -->"
END_MARKER = "<!-- OPENSPRITE:USER_PROFILE:END -->"
DEFAULT_MANAGED_CONTENT = "- No learned user profile details yet."
AUTO_PROFILE_INTRO = "This section is maintained by OpenSprite."


class UserProfileStore:
    """Persist one session's USER.md profile and its consolidation state."""

    def __init__(self, user_profile_file: Path, state_file: Path, *, bootstrap_text: str = "# User Profile\n\n"):
        self.user_profile_file = Path(user_profile_file).expanduser()
        self.state = JsonProgressStore(state_file)
        self.response_document = ManagedMarkdownDocument(
            self.user_profile_file,
            start_marker=RL_START_MARKER,
            end_marker=RL_END_MARKER,
            default_content=DEFAULT_RESPONSE_LANGUAGE_CONTENT,
            heading=RESPONSE_LANGUAGE_HEADER,
            intro=RESPONSE_LANGUAGE_INTRO,
            anchor_heading=AUTO_PROFILE_HEADER,
            bootstrap_text=bootstrap_text,
        )
        self.profile_document = ManagedMarkdownDocument(
            self.user_profile_file,
            start_marker=START_MARKER,
            end_marker=END_MARKER,
            default_content=DEFAULT_MANAGED_CONTENT,
            heading=AUTO_PROFILE_HEADER,
            intro=AUTO_PROFILE_INTRO,
            anchor_heading=None,
            bootstrap_text=bootstrap_text,
        )

    def read_text(self) -> str:
        # Ensure both managed regions exist (order: response language before profile).
        self.response_document.read_text()
        self.profile_document.read_text()
        return self.user_profile_file.read_text(encoding="utf-8")

    def read_response_language_block(self) -> str:
        return self.response_document.read_managed_block()

    def write_response_language_block(self, content: str) -> None:
        self.response_document.write_managed_block(content)

    def read_managed_block(self) -> str:
        return self.profile_document.read_managed_block()

    def write_managed_block(self, content: str) -> None:
        self.profile_document.write_managed_block(content)

    def load_state(self) -> dict[str, int]:
        return self.state.load_state()

    def save_state(self, state: dict[str, int]) -> None:
        self.state.save_state(state)

    def get_processed_index(self, session_id: str) -> int:
        return self.state.get_processed_index(session_id)

    def set_processed_index(self, session_id: str, index: int) -> None:
        self.state.set_processed_index(session_id, index)


_SAVE_USER_PROFILE_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_user_profile",
            "description": (
                "Update auto-managed USER.md blocks: the profile block (required) and optionally "
                "the Response language block."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "profile_update": {
                        "type": "string",
                        "description": (
                            "Replacement markdown for the auto-managed USER.md profile block "
                            "(under the Auto-managed Profile markers). "
                            "Keep it concise, stable, and free of secrets."
                        ),
                    },
                    "response_language_update": {
                        "type": "string",
                        "description": (
                            "Replacement markdown for the auto-managed Response language block only "
                            "(typically one bullet line, e.g. '- Traditional Chinese (Taiwan)' or '- not set'). "
                            "Omit this field entirely if that block should stay unchanged."
                        ),
                    },
                },
                "required": ["profile_update"],
            },
        },
    }
]


def _reset_between_markers(
    text: str, start_marker: str, end_marker: str, inner: str
) -> str:
    """Replace the inner content between markers, or return text unchanged if markers are missing."""
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start == -1 or end == -1 or end <= start:
        return text
    start += len(start_marker)
    return text[:start] + "\n" + inner + "\n" + text[end:]


def _reset_managed_block(content: str) -> str:
    """Reset auto-managed blocks so new profiles do not inherit another user's data."""
    text = content or ""
    text = _reset_between_markers(
        text, RL_START_MARKER, RL_END_MARKER, DEFAULT_RESPONSE_LANGUAGE_CONTENT
    )
    text = _reset_between_markers(text, START_MARKER, END_MARKER, DEFAULT_MANAGED_CONTENT)
    return text


def load_user_profile_bootstrap_text(
    app_home: str | Path | None = None,
    *,
    bootstrap_dir: str | Path | None = None,
) -> str:
    """Load the bootstrap USER.md template used to seed a new per-session profile file."""
    template_root = Path(bootstrap_dir).expanduser() if bootstrap_dir is not None else get_bootstrap_dir(app_home)
    template_file = template_root / "USER.md"
    if not template_file.exists():
        return "# User Profile\n\n"
    return _reset_managed_block(template_file.read_text(encoding="utf-8"))


def create_user_profile_store(
    app_home: str | Path | None,
    session_id: str | None,
    *,
    bootstrap_dir: str | Path | None = None,
    workspace_root: str | Path | None = None,
) -> UserProfileStore:
    """Create the per-session USER.md store for the given user/session scope."""
    return UserProfileStore(
        user_profile_file=get_user_profile_file(app_home, session_id=session_id, workspace_root=workspace_root),
        state_file=get_user_profile_state_file(app_home, session_id=session_id, workspace_root=workspace_root),
        bootstrap_text=load_user_profile_bootstrap_text(app_home, bootstrap_dir=bootstrap_dir),
    )


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
    *,
    profile_llm: DocumentLlmConfig,
) -> bool:
    """Update this session's USER.md managed blocks from conversation history."""
    if not messages:
        return True

    current_profile = profile_store.read_managed_block()
    current_response_language = profile_store.read_response_language_block()
    transcript = _format_messages(messages)
    if not transcript:
        return True

    prompt = f"""Review this conversation and update this user's profile.

Current auto-managed Response language block:
{current_response_language or '(empty)'}

Current auto-managed USER.md profile block:
{current_profile or '(empty)'}

Conversation to analyze:
{transcript}

Rules:
- Capture only stable preferences, work context, or repeated habits in the profile block.
- Update the Response language block when the user clearly states a preferred assistant language or consistently uses one language for requests (e.g. one bullet line: `- Traditional Chinese (Taiwan)` or `- English`). Use `- not set` when preference should follow each message's language.
- Do not store secrets, API keys, access tokens, passwords, or private file contents.
- Do not store one-off tasks or temporary requests.
- Prefer explicit facts and durable preferences over guesses.
- Return concise markdown bullets or short sections suitable for USER.md profile.
- Write profile content in clear, concise English unless the user explicitly prefers another language for that block.
- If nothing meaningful changed in a block, return that block unchanged.
"""

    llm = profile_llm
    try:
        response = await provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You maintain one user's USER.md for an assistant: the Response language block "
                        "and the Auto-managed Profile block. "
                        "Call save_user_profile with profile_update (required) and, when needed, "
                        "response_language_update for the Response language section only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            tools=_SAVE_USER_PROFILE_TOOL,
            model=model,
            **llm.decoding_kwargs(),
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

        lang_raw = args.get("response_language_update", None)
        if lang_raw is not None:
            lang_stripped = str(lang_raw).strip()
            if lang_stripped and lang_stripped != current_response_language:
                profile_store.write_response_language_block(lang_stripped)
                logger.info("USER.md response language updated ({} chars)", len(lang_stripped))

        return True
    except Exception as exc:
        logger.error("User profile consolidation failed: {}", exc)
        return False


class UserProfileConsolidator(ConversationConsolidator):
    """Manage incremental USER.md updates from stored session history."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        provider,
        model: str,
        profile_store_factory: Callable[[str], UserProfileStore],
        threshold: int,
        lookback_messages: int,
        enabled: bool,
        llm: DocumentLlmConfig,
    ):
        self.storage = storage
        self.provider = provider
        self.model = model
        self.profile_store_factory = profile_store_factory
        self.threshold = max(1, threshold)
        self.lookback_messages = max(1, lookback_messages)
        self.enabled = enabled
        self.llm = llm

    @staticmethod
    def _to_message_dict(message: StoredMessage) -> dict[str, Any]:
        return {
            "role": message.role,
            "content": message.content,
            "timestamp": message.timestamp,
            "metadata": dict(message.metadata or {}),
        }

    async def maybe_update(self, session_id: str) -> None:
        if not self.enabled:
            return

        profile_store = self.profile_store_factory(session_id)
        message_count = await get_storage_message_count(self.storage, session_id)
        last_processed = profile_store.get_processed_index(session_id)
        if last_processed > message_count:
            profile_store.set_processed_index(session_id, message_count)
            return

        pending = message_count - last_processed
        if pending < self.threshold:
            return

        end_index = min(message_count, last_processed + self.lookback_messages)
        chunk = await get_storage_messages_slice(
            self.storage,
            session_id,
            start_index=last_processed,
            end_index=end_index,
        )
        if not chunk:
            return

        logger.info("[{}] Updating USER.md profile from {} messages", session_id, len(chunk))
        success = await consolidate_user_profile(
            profile_store=profile_store,
            messages=[self._to_message_dict(message) for message in chunk],
            provider=self.provider,
            model=self.model,
            profile_llm=self.llm,
        )
        if success:
            profile_store.set_processed_index(session_id, end_index)
