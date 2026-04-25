"""Per-chat recent summary document store and consolidator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config.schema import DocumentLlmConfig
from ..context.paths import get_recent_summary_file, get_recent_summary_state_file
from ..storage import StoredMessage, StorageProvider
from ..storage.base import get_storage_message_count, get_storage_messages_slice
from ..utils import count_messages_tokens, count_text_tokens
from ..utils.log import logger
from .base import ConversationConsolidator
from .state import JsonProgressStore

_RECENT_SUMMARY_TEMPLATE = """# Active Threads
- 

# Recent Progress
- 

# Current Focus
- 

# Follow-ups
- """


class RecentSummaryStore:
    """Persist RECENT_SUMMARY.md files and their incremental state."""

    def __init__(self, memory_dir: Path, state_file: Path | None = None):
        self.memory_dir = Path(memory_dir).expanduser()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.state = JsonProgressStore(state_file or get_recent_summary_state_file(self.memory_dir))

    def _get_summary_file(self, chat_id: str) -> Path:
        summary_file = get_recent_summary_file(self.memory_dir, chat_id)
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        return summary_file

    def read(self, chat_id: str) -> str:
        summary_file = self._get_summary_file(chat_id)
        if summary_file.exists():
            return summary_file.read_text(encoding="utf-8")
        return ""

    def write(self, chat_id: str, content: str) -> None:
        self._get_summary_file(chat_id).write_text(content, encoding="utf-8")

    def get_context(self, chat_id: str) -> str:
        summary = self.read(chat_id)
        if summary:
            return f"# Recent Summary\n\n{summary}"
        return ""

    def get_processed_index(self, chat_id: str) -> int:
        return self.state.get_processed_index(chat_id)

    def set_processed_index(self, chat_id: str, index: int) -> None:
        self.state.set_processed_index(chat_id, index)

    def clear(self, chat_id: str) -> None:
        summary_file = self._get_summary_file(chat_id)
        if summary_file.exists():
            summary_file.unlink()
        self.state.set_processed_index(chat_id, 0)


def _to_message_dict(message: StoredMessage | dict[str, Any]) -> dict[str, Any]:
    if isinstance(message, dict):
        return {
            "role": message.get("role", "?"),
            "content": message.get("content", ""),
            "timestamp": message.get("timestamp"),
            "metadata": dict(message.get("metadata", {}) or {}),
        }
    return {
        "role": message.role,
        "content": message.content,
        "timestamp": message.timestamp,
        "metadata": dict(message.metadata or {}),
    }


def _format_messages(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "?")).upper()
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        if len(content) > 800:
            content = content[:800] + f"... (truncated from {len(content)} chars)"
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


async def consolidate_recent_summary(
    summary_store: RecentSummaryStore,
    chat_id: str,
    messages: list[dict[str, Any]],
    provider,
    model: str,
    *,
    summary_llm: DocumentLlmConfig,
) -> bool:
    """Merge a recent conversation chunk into RECENT_SUMMARY.md."""
    if not messages:
        return True

    current_summary = summary_store.read(chat_id)
    transcript = _format_messages(messages)
    if not transcript:
        return True

    transcript_tokens = count_text_tokens(transcript, model=model)
    current_summary_tokens = count_text_tokens(current_summary, model=model) if current_summary else 0

    prompt = f"""Review this recent conversation chunk and update the recent summary.

Current recent summary:
{current_summary or _RECENT_SUMMARY_TEMPLATE}

Conversation chunk:
{transcript}

Rules:
- Keep the exact section order from the template below.
- Focus on medium-term context that still matters across the next several turns.
- Capture active threads, recent progress, current focus, and pending follow-ups.
- Remove items that are resolved or no longer relevant.
- Keep bullets concise, deduplicated, and concrete.
- Do not copy raw logs, long tool output, or full code blocks.
- Do not duplicate stable long-term preferences that belong in MEMORY.md unless they are directly affecting current work.
- If nothing meaningful changed, return the current recent summary unchanged.

Required template:
{_RECENT_SUMMARY_TEMPLATE}
"""

    try:
        logger.info(
            "[{}] recent_summary.prompt | current_chars={} current_tokens={} transcript_chars={} transcript_tokens={} messages={}",
            chat_id,
            len(current_summary),
            current_summary_tokens,
            len(transcript),
            transcript_tokens,
            len(messages),
        )
        llm = summary_llm
        response = await provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You maintain a RECENT_SUMMARY.md file for an assistant. "
                        "Return updated structured markdown only, with no extra commentary."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            model=model,
            **llm.decoding_kwargs(),
        )

        update = str(response.content or "").strip()
        if not update:
            logger.warning("Recent summary consolidation: empty response content")
            return False

        update_tokens = count_text_tokens(update, model=model)
        if update != current_summary:
            summary_store.write(chat_id, update)
            logger.info(
                "Recent summary updated for chat {}: {} chars ({} tokens, delta_chars={})",
                chat_id,
                len(update),
                update_tokens,
                len(update) - len(current_summary),
            )
        else:
            logger.info(
                "Recent summary unchanged for chat {}: {} chars ({} tokens)",
                chat_id,
                len(update),
                update_tokens,
            )
        return True
    except Exception as exc:
        logger.error("Recent summary consolidation failed: {}", exc)
        return False


class RecentSummaryConsolidator(ConversationConsolidator):
    """Manage incremental RECENT_SUMMARY.md updates from stored chat history."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        provider,
        model: str,
        summary_store: RecentSummaryStore,
        threshold: int,
        token_threshold: int,
        lookback_messages: int,
        keep_last_messages: int,
        enabled: bool,
        llm: DocumentLlmConfig,
    ):
        self.storage = storage
        self.provider = provider
        self.model = model
        self.summary_store = summary_store
        self.threshold = max(1, threshold)
        self.token_threshold = max(0, token_threshold)
        self.lookback_messages = max(1, lookback_messages)
        self.keep_last_messages = max(1, keep_last_messages)
        self.enabled = enabled
        self.llm = llm

    async def maybe_update(self, chat_id: str) -> None:
        if not self.enabled:
            return

        message_count = await get_storage_message_count(self.storage, chat_id)
        cutoff_index = max(0, message_count - self.keep_last_messages)
        if cutoff_index <= 0:
            return

        last_processed = self.summary_store.get_processed_index(chat_id)
        if last_processed > cutoff_index:
            self.summary_store.set_processed_index(chat_id, cutoff_index)
            return

        pending = cutoff_index - last_processed
        if pending <= 0:
            return

        end_index = min(cutoff_index, last_processed + self.lookback_messages)
        chunk = [
            _to_message_dict(message)
            for message in await get_storage_messages_slice(
                self.storage,
                chat_id,
                start_index=last_processed,
                end_index=end_index,
            )
        ]
        if not chunk:
            return

        chunk_tokens = count_messages_tokens(chunk, model=self.model)
        logger.info(
            "[{}] recent_summary.check | total_messages={} processed_index={} cutoff_index={} pending_messages={} chunk_messages={} chunk_tokens={} threshold={} token_threshold={} keep_last_messages={}",
            chat_id,
            message_count,
            last_processed,
            cutoff_index,
            pending,
            len(chunk),
            chunk_tokens,
            self.threshold,
            self.token_threshold,
            self.keep_last_messages,
        )
        if pending < self.threshold and (self.token_threshold <= 0 or chunk_tokens < self.token_threshold):
            return

        logger.info(
            "[{}] Updating RECENT_SUMMARY.md from {} messages ({} tokens)",
            chat_id,
            len(chunk),
            chunk_tokens,
        )
        success = await consolidate_recent_summary(
            summary_store=self.summary_store,
            chat_id=chat_id,
            messages=chunk,
            provider=self.provider,
            model=self.model,
            summary_llm=self.llm,
        )
        if success:
            self.summary_store.set_processed_index(chat_id, end_index)
