"""Per-chat ACTIVE_TASK.md store and consolidator."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from ..config.schema import DocumentLlmConfig
from ..context.paths import get_active_task_file, get_active_task_state_file
from ..storage import StoredMessage, StorageProvider
from ..storage.base import get_storage_message_count, get_storage_messages_slice
from ..utils import count_text_tokens
from ..utils.log import logger
from .base import ConversationConsolidator, ConversationDocumentStore
from .managed import ManagedMarkdownDocument
from .state import JsonProgressStore

ACTIVE_TASK_HEADER = "## Task State"
ACTIVE_TASK_START_MARKER = "<!-- OPENSPRITE:ACTIVE_TASK:START -->"
ACTIVE_TASK_END_MARKER = "<!-- OPENSPRITE:ACTIVE_TASK:END -->"
DEFAULT_ACTIVE_TASK_CONTENT = """- Status: inactive
- Goal: not set
- Deliverable: not set
- Definition of done:
  - not set
- Constraints:
  - none
- Assumptions:
  - none
- Plan:
  1. not set
- Current step: not set
- Next step: not set
- Completed steps:
  - none
- Open questions:
  - none"""
_ACTIVE_TASK_BOOTSTRAP = """# ACTIVE_TASK.md - Current Task Contract

This file stores the active multi-step task for this chat session.
It should stay concise, execution-oriented, and current.
If there is no active task, keep the status as `inactive`.

## Task State

This section is maintained by OpenSprite.

<!-- OPENSPRITE:ACTIVE_TASK:START -->
- Status: inactive
- Goal: not set
- Deliverable: not set
- Definition of done:
  - not set
- Constraints:
  - none
- Assumptions:
  - none
- Plan:
  1. not set
- Current step: not set
- Next step: not set
- Completed steps:
  - none
- Open questions:
  - none
<!-- OPENSPRITE:ACTIVE_TASK:END -->
"""
_ACTIVE_STATUSES_TO_INCLUDE = {"active", "blocked", "waiting_user"}
_NON_TASK_MESSAGES = {
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "ok",
    "okay",
    "got it",
    "cool",
    "nice",
}
_TASK_SWITCH_PREFIXES = (
    "new task",
    "new objective",
    "switch task",
    "switch to",
    "change of plan",
    "ignore previous",
    "forget previous",
    "instead",
    "actually, switch",
    "actually switch",
    "新的任務",
    "新任務",
    "換個任務",
    "換一個任務",
    "改成",
    "換成",
    "改做",
    "接下來幫我",
    "接著幫我",
    "另外一個任務",
    "不要做原本的",
    "先不要做原本的",
)
_TASK_REQUEST_MARKERS = (
    "help me",
    "can you",
    "could you",
    "please",
    "need you to",
    "i want you to",
    "let's",
    "幫我",
    "請",
    "麻煩",
)
_TASK_WORK_MARKERS = (
    "fix",
    "refactor",
    "implement",
    "add",
    "build",
    "create",
    "update",
    "review",
    "analyze",
    "analyse",
    "investigate",
    "audit",
    "check",
    "look into",
    "debug",
    "optimize",
    "write",
    "draft",
    "plan",
    "organize",
    "整理",
    "分析",
    "修正",
    "修復",
    "重構",
    "重寫",
    "新增",
    "建立",
    "更新",
    "調查",
    "檢查",
    "看一下",
    "除錯",
    "優化",
    "規劃",
    "實作",
    "修改",
)


class ActiveTaskStore(ConversationDocumentStore):
    """Persist one chat session's ACTIVE_TASK.md and its update state."""

    def __init__(self, active_task_file: Path, state_file: Path):
        self.active_task_file = Path(active_task_file).expanduser()
        self.state = JsonProgressStore(state_file)
        self.document = ManagedMarkdownDocument(
            self.active_task_file,
            start_marker=ACTIVE_TASK_START_MARKER,
            end_marker=ACTIVE_TASK_END_MARKER,
            default_content=DEFAULT_ACTIVE_TASK_CONTENT,
            heading=ACTIVE_TASK_HEADER,
            intro="This section is maintained by OpenSprite.",
            anchor_heading=None,
            bootstrap_text=_ACTIVE_TASK_BOOTSTRAP,
        )

    def read(self, chat_id: str) -> str:
        return self.document.read_text()

    def read_text(self) -> str:
        return self.document.read_text()

    def write(self, chat_id: str, content: str) -> None:
        self.document.write_managed_block(content)

    def read_managed_block(self) -> str:
        return self.document.read_managed_block()

    def write_managed_block(self, content: str) -> None:
        self.document.write_managed_block(content)

    def get_processed_index(self, chat_id: str) -> int:
        return self.state.get_processed_index(chat_id)

    def set_processed_index(self, chat_id: str, index: int) -> None:
        self.state.set_processed_index(chat_id, index)

    def clear(self, chat_id: str) -> None:
        self.document.write_managed_block(DEFAULT_ACTIVE_TASK_CONTENT)
        self.state.set_processed_index(chat_id, 0)

    def read_status(self) -> str:
        block = self.read_managed_block()
        match = re.search(r"^- Status:\s*(.+)$", block, re.MULTILINE)
        if not match:
            return "inactive"
        return match.group(1).strip().lower() or "inactive"

    def get_context(self, chat_id: str) -> str:
        status = self.read_status()
        if status not in _ACTIVE_STATUSES_TO_INCLUDE:
            return ""
        return f"# Active Task\n\n{self.read_managed_block()}"


def _normalize_goal_text(text: str, max_chars: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _infer_deliverable(goal_text: str) -> str:
    lower = goal_text.lower()
    if any(token in lower for token in ("fix", "correct", "repair", "solve")):
        return "a concrete fix and verification"
    if any(token in lower for token in ("refactor", "cleanup", "clean up")):
        return "a safe refactor and verification"
    if any(token in lower for token in ("add", "implement", "build", "create", "write")):
        return "a concrete implementation result"
    if any(token in lower for token in ("review", "analyze", "analyse", "check", "investigate", "look into")):
        return "clear findings and the next recommended action"
    if any(token in lower for token in ("explain", "why", "how", "summarize", "summary")):
        return "a clear explanation with the necessary supporting detail"
    return "a concrete result aligned with the user's request"


def build_initial_active_task_block(message_text: str) -> str | None:
    """Create a minimal first-turn task brief from the latest user message."""
    stripped = (message_text or "").strip()
    if not stripped:
        return None
    if stripped.startswith("/"):
        return None

    goal = _normalize_goal_text(stripped)
    if goal.lower() in _NON_TASK_MESSAGES:
        return None
    if not is_task_worthy_message(stripped):
        return None

    deliverable = _infer_deliverable(goal)
    assumptions = (
        "The initial request is brief and this task brief may be refined after more context."
        if len(goal.split()) <= 12
        else "This initial task brief was generated from the latest user request and may be refined after more context."
    )
    return (
        "- Status: active\n"
        f"- Goal: {goal}\n"
        f"- Deliverable: {deliverable}\n"
        "- Definition of done:\n"
        "  - the user request is addressed directly\n"
        "  - the result or blocker is explicit\n"
        "- Constraints:\n"
        "  - preserve user intent\n"
        "  - prefer the smallest correct next step\n"
        "- Assumptions:\n"
        f"  - {assumptions}\n"
        "- Plan:\n"
        "  1. inspect the relevant context and refine the task if needed\n"
        "  2. execute the highest-value next step toward the goal\n"
        "  3. verify the result or state the blocking gap\n"
        "- Current step: 1. inspect the relevant context and refine the task if needed\n"
        "- Next step: 2. execute the highest-value next step toward the goal\n"
        "- Completed steps:\n"
        "  - none\n"
        "- Open questions:\n"
        "  - none"
    )


def is_task_worthy_message(message_text: str) -> bool:
    """Return whether a user message looks like work that should create an active task."""
    stripped = (message_text or "").strip()
    if not stripped:
        return False
    if stripped.startswith("/"):
        return False

    normalized = re.sub(r"\s+", " ", stripped).strip().lower()
    if normalized in _NON_TASK_MESSAGES:
        return False
    if normalized.startswith(_TASK_SWITCH_PREFIXES):
        return True

    has_work_marker = any(marker in normalized for marker in _TASK_WORK_MARKERS)
    if not has_work_marker:
        return False

    has_request_marker = any(marker in normalized for marker in _TASK_REQUEST_MARKERS)
    if has_request_marker:
        return True

    if stripped.endswith(("?", "？")):
        return False

    return True


def should_replace_active_task(current_task_block: str, message_text: str) -> bool:
    """Return whether the latest user turn clearly indicates a task switch."""
    current_goal_match = re.search(r"^- Goal:\s*(.+)$", current_task_block, re.MULTILINE)
    current_goal = current_goal_match.group(1).strip().lower() if current_goal_match else ""
    candidate = re.sub(r"\s+", " ", (message_text or "").strip()).lower()
    if not candidate:
        return False
    if current_goal and (candidate == current_goal or current_goal in candidate):
        return False
    return candidate.startswith(_TASK_SWITCH_PREFIXES)


def _to_message_dict(message: StoredMessage | dict[str, Any]) -> dict[str, Any]:
    if isinstance(message, dict):
        return {
            "role": message.get("role", "?"),
            "content": message.get("content", ""),
            "metadata": dict(message.get("metadata", {}) or {}),
        }
    return {
        "role": message.role,
        "content": message.content,
        "metadata": dict(message.metadata or {}),
    }


def _format_messages(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "?")).upper()
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        if len(content) > 1200:
            content = content[:1200] + f"... (truncated from {len(content)} chars)"
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


async def consolidate_active_task(
    active_task_store: ActiveTaskStore,
    chat_id: str,
    messages: list[dict[str, Any]],
    provider,
    model: str,
    *,
    active_task_llm: DocumentLlmConfig,
) -> bool:
    """Update ACTIVE_TASK.md from a recent conversation chunk."""
    if not messages:
        return True

    current_task = active_task_store.read_managed_block()
    transcript = _format_messages(messages)
    if not transcript:
        return True

    prompt = f"""Review this recent conversation chunk and update ACTIVE_TASK.md for the current chat session.

Current ACTIVE_TASK state:
{current_task}

Conversation chunk:
{transcript}

Rules:
- Keep the exact field order from the required template below.
- Use one of these statuses only: inactive, active, blocked, waiting_user, done, cancelled.
- Use `inactive` when there is no meaningful multi-step task that should stay active.
- Keep Goal and Deliverable to one concise line each.
- Keep Definition of done, Constraints, Assumptions, Completed steps, and Open questions concise bullet lists.
- Keep Plan concise and practical; prefer 3-7 steps when a task is active.
- `Current step` should describe the current step or `not set`.
- `Next step` should describe the single next action or `not set`.
- Mark steps as completed only when the transcript clearly shows they were completed.
- If the task changed materially, update Goal, Deliverable, Plan, and step tracking to match the latest agreed direction.
- If the assistant drifted but the user's task is still clear, restore the task to the user's actual goal instead of preserving the drift.
- Do not copy raw logs or large tool output into ACTIVE_TASK.
- Return markdown only, with no extra commentary.

Required template:
{DEFAULT_ACTIVE_TASK_CONTENT}
"""

    try:
        logger.info(
            "[{}] active_task.prompt | current_chars={} transcript_chars={} messages={}",
            chat_id,
            len(current_task),
            len(transcript),
            len(messages),
        )
        llm = active_task_llm
        response = await provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You maintain one session's ACTIVE_TASK.md. Return only the updated markdown block, "
                        "keeping the required structure exactly."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            model=model,
            **llm.decoding_kwargs(),
        )
        update = str(response.content or "").strip()
        if not update:
            logger.warning("Active task consolidation: empty response content")
            return False

        if update != current_task:
            active_task_store.write_managed_block(update)
            logger.info(
                "Active task updated for chat {}: {} chars ({} tokens)",
                chat_id,
                len(update),
                count_text_tokens(update, model=model),
            )
        else:
            logger.info("Active task unchanged for chat {}", chat_id)
        return True
    except Exception as exc:
        logger.error("Active task consolidation failed: {}", exc)
        return False


class ActiveTaskConsolidator(ConversationConsolidator):
    """Manage incremental ACTIVE_TASK.md updates from stored chat history."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        provider,
        model: str,
        active_task_store_factory: Callable[[str], ActiveTaskStore],
        threshold: int = 2,
        lookback_messages: int = 40,
        enabled: bool = True,
        llm: DocumentLlmConfig,
    ):
        self.storage = storage
        self.provider = provider
        self.model = model
        self.active_task_store_factory = active_task_store_factory
        self.threshold = max(1, threshold)
        self.lookback_messages = max(1, lookback_messages)
        self.enabled = enabled
        self.llm = llm

    async def maybe_update(self, chat_id: str) -> None:
        if not self.enabled:
            return

        active_task_store = self.active_task_store_factory(chat_id)
        message_count = await get_storage_message_count(self.storage, chat_id)
        last_processed = active_task_store.get_processed_index(chat_id)
        if last_processed > message_count:
            active_task_store.set_processed_index(chat_id, message_count)
            return

        pending = message_count - last_processed
        if pending < self.threshold:
            return

        end_index = min(message_count, last_processed + self.lookback_messages)
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

        logger.info("[{}] Updating ACTIVE_TASK.md from {} messages", chat_id, len(chunk))
        success = await consolidate_active_task(
            active_task_store=active_task_store,
            chat_id=chat_id,
            messages=chunk,
            provider=self.provider,
            model=self.model,
            active_task_llm=self.llm,
        )
        if success:
            active_task_store.set_processed_index(chat_id, end_index)


def create_active_task_store(
    app_home: str | Path | None,
    chat_id: str | None,
    *,
    workspace_root: str | Path | None = None,
) -> ActiveTaskStore:
    """Create the per-chat ACTIVE_TASK.md store for the given session scope."""
    return ActiveTaskStore(
        active_task_file=get_active_task_file(app_home, chat_id=chat_id, workspace_root=workspace_root),
        state_file=get_active_task_state_file(app_home, chat_id=chat_id, workspace_root=workspace_root),
    )
