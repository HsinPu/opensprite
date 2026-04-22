"""Per-chat long-term memory document store and consolidator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config.schema import MemoryLlmConfig
from ..context.paths import get_memory_file
from ..utils import count_text_tokens
from ..utils.log import logger
from .base import ConversationDocumentStore


class MemoryDocumentStore(ConversationDocumentStore):
    """File-based per-chat markdown memory stored under the memory directory."""

    def __init__(self, memory_dir: Path):
        base_path = Path(memory_dir).expanduser()
        self.memory_base = base_path if base_path.name == "memory" else base_path / "memory"
        self.memory_base.mkdir(parents=True, exist_ok=True)

    def _get_memory_file(self, chat_id: str) -> Path:
        memory_file = get_memory_file(self.memory_base, chat_id)
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        return memory_file

    def read(self, chat_id: str) -> str:
        memory_file = self._get_memory_file(chat_id)
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        if chat_id == "default":
            legacy_memory_file = self.memory_base / "MEMORY.md"
            if legacy_memory_file.exists():
                return legacy_memory_file.read_text(encoding="utf-8")
        return ""

    def write(self, chat_id: str, content: str) -> None:
        memory_file = self._get_memory_file(chat_id)
        memory_file.write_text(content, encoding="utf-8")

    def get_context(self, chat_id: str) -> str:
        memory = self.read(chat_id)
        if memory:
            return f"# Long-term Memory\n\n{memory}"
        return ""


FileMemoryStorage = MemoryDocumentStore
MemoryStore = MemoryDocumentStore

_CONSOLIDATION_MESSAGE_TOKEN_BUDGET = 6000
_MAX_MESSAGE_CHARS = 800
_MEMORY_TEMPLATE = """# User Preferences
- 

# Ongoing Tasks
- 

# Decisions
- 

# Important Facts
- 

# Open Issues
- """


_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save important information to long-term memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_update": {
                        "type": "string",
                        "description": (
                            "Updated long-term memory as markdown. Include all existing facts plus new ones. "
                            "Return unchanged if nothing new."
                        ),
                    },
                },
                "required": ["memory_update"],
            },
        },
    }
]


def _normalize_message_line(message: dict[str, Any] | Any) -> str | None:
    if isinstance(message, dict):
        content = str(message.get("content", "")).strip()
        role = str(message.get("role", "?")).upper()
    else:
        content = str(getattr(message, "content", "")).strip()
        role = str(getattr(message, "role", "?")).upper()

    if not content:
        return None

    if len(content) > _MAX_MESSAGE_CHARS:
        content = content[:_MAX_MESSAGE_CHARS] + f"... (truncated from {len(content)} chars)"

    return f"[{role}]: {content}"


def _select_consolidation_lines(messages: list[dict[str, Any] | Any], model: str) -> list[str]:
    selected_reversed: list[str] = []
    running_tokens = 0

    for message in reversed(messages):
        line = _normalize_message_line(message)
        if line is None:
            continue

        line_tokens = count_text_tokens(line, model=model)
        if selected_reversed and running_tokens + line_tokens > _CONSOLIDATION_MESSAGE_TOKEN_BUDGET:
            break
        if not selected_reversed and line_tokens > _CONSOLIDATION_MESSAGE_TOKEN_BUDGET:
            selected_reversed.append(line)
            break

        selected_reversed.append(line)
        running_tokens += line_tokens

    return list(reversed(selected_reversed))


async def consolidate(
    memory_store: MemoryStore,
    chat_id: str,
    messages: list[dict[str, Any]],
    provider,
    model: str,
    *,
    memory_llm: MemoryLlmConfig | None = None,
) -> bool:
    """Consolidate old messages into per-chat memory via the active LLM."""
    if not messages:
        return True

    lines = _select_consolidation_lines(messages, model)
    if not lines:
        return True

    current_memory = memory_store.read(chat_id)
    memory_seed = current_memory or _MEMORY_TEMPLATE
    conversation_block = "\n".join(lines)
    prompt = f"""Review the new conversation segment and update the chat memory.

Current memory:
{memory_seed}

New conversation segment:
{conversation_block}

Return the full updated memory as markdown via the save_memory tool.

Rules:
- Keep the exact section order from the template below.
- Merge new durable information into the existing memory instead of rewriting everything from scratch.
- Keep bullets concise and deduplicated.
- Remove items that are no longer true or have been completed.
- Prefer stable preferences, ongoing tasks, important decisions, important facts, and unresolved issues.
- Skip temporary chatter, verbose tool output, raw logs, and details that can be recomputed later.
- If nothing meaningful changed, return the current memory unchanged.

Required memory template:
{_MEMORY_TEMPLATE}"""

    llm = memory_llm or MemoryLlmConfig()
    if llm.pass_decoding_params:
        dec_kw: dict[str, Any] = {
            "temperature": llm.temperature,
            "max_tokens": llm.max_tokens,
            "top_p": llm.top_p,
            "frequency_penalty": llm.frequency_penalty,
            "presence_penalty": llm.presence_penalty,
        }
    else:
        dec_kw = {
            "temperature": None,
            "max_tokens": None,
            "top_p": None,
            "frequency_penalty": None,
            "presence_penalty": None,
        }

    try:
        response = await provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a memory consolidation agent. Update long-term memory as structured markdown "
                        "using the provided template and call the save_memory tool with the full merged result."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            tools=_SAVE_MEMORY_TOOL,
            model=model,
            **dec_kw,
        )

        if not response.tool_calls:
            logger.warning("Memory consolidation: LLM did not call save_memory")
            return False

        args = response.tool_calls[0].arguments
        if isinstance(args, str):
            args = json.loads(args)

        update = args.get("memory_update")
        if update and update != current_memory:
            memory_store.write(chat_id, update)
            logger.info("Memory consolidated for chat {}: {} chars", chat_id, len(update))

        return True
    except Exception as exc:
        import traceback

        logger.error(f"Memory consolidation failed: {exc}\n{traceback.format_exc()}")
        return False
