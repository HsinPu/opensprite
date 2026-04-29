"""Background skill persistence review — plain-text transcript + dedicated system prompt."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Sequence

from ..llms import ChatMessage
from ..storage import StorageProvider
from ..tools import ToolRegistry
from ..utils.log import logger

SKILL_REVIEW_SYSTEM = """You are OpenSprite's background skill curator. The main assistant already replied to the user; your work is invisible to them.

You may ONLY use these tools: `read_skill`, `configure_skill`.

Goal: decide whether the recent conversation contains a reusable procedural workflow worth saving as a skill (SKILL.md), or an update to an existing skill.

Rules:
- Prefer `action=upsert` on an existing skill when refining; use `action=add` only for a genuinely new skill id. Use `read_skill` with `skill-creator-design` before authoring a new skill.
- If nothing is worth persisting, reply with exactly this single line and stop (no tools): Nothing to save.
- Do not narrate, apologize, or mention this background pass.
- Use `configure_skill` for the session workspace `skills/` folder only. Bundled skills live read-only under `~/.opensprite/skills/<id>/`.
"""


def format_stored_messages_for_transcript(
    messages: Sequence[Any],
    *,
    per_message_max_chars: int = 6000,
    transcript_max_chars: int = 100_000,
) -> str:
    """Turn stored session rows into a plain-text transcript for the review model."""
    lines: list[str] = []
    total = 0
    for m in messages:
        role = str(getattr(m, "role", "") or "?").strip()
        tool_name = getattr(m, "tool_name", None)
        prefix = role.upper()
        if tool_name:
            prefix = f"{prefix} [tool:{tool_name}]"
        body = str(getattr(m, "content", "") or "").strip()
        if len(body) > per_message_max_chars:
            body = body[:per_message_max_chars] + "\n… (truncated)"
        block = f"{prefix}\n{body}\n"
        if total + len(block) > transcript_max_chars:
            lines.append("… (transcript truncated)")
            break
        lines.append(block)
        total += len(block)
    return "\n".join(lines).strip()


def build_skill_review_user_content(transcript: str) -> str:
    """User turn for the review-only LLM run."""
    return (
        "Below is a plain-text transcript of recent messages in this session (including tools when logged).\n\n"
        f"--- TRANSCRIPT ---\n{transcript}\n--- END TRANSCRIPT ---\n\n"
        "Review the transcript. If a reusable how-to should be saved or updated as a skill, use the tools. "
        "Otherwise reply with exactly: Nothing to save."
    )


class SkillReviewService:
    """Runs the background skill persistence review pass."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        tools: ToolRegistry,
        transcript_message_limit_getter: Callable[[], int],
        max_tool_iterations_getter: Callable[[], int],
        build_system_prompt: Callable[[str], str],
        execute_messages: Callable[..., Awaitable[Any]],
    ):
        self.storage = storage
        self.tools = tools
        self._transcript_message_limit_getter = transcript_message_limit_getter
        self._max_tool_iterations_getter = max_tool_iterations_getter
        self._build_system_prompt = build_system_prompt
        self._execute_messages = execute_messages

    def tool_registry(self) -> ToolRegistry | None:
        """Return the restricted tool registry allowed during background skill review."""
        allowed = frozenset({"read_skill", "configure_skill"})
        available = set(self.tools.tool_names)
        if not allowed.issubset(available):
            return None
        excluded = available - allowed
        return self.tools.filtered(exclude_names=excluded)

    async def run(self, session_id: str, *, tool_registry: ToolRegistry) -> None:
        """Execute one review pass for a session using the restricted skill tool registry."""
        stored = await self.storage.get_messages(session_id, limit=self._transcript_message_limit_getter())
        transcript = format_stored_messages_for_transcript(stored)
        if len(transcript) < 80:
            logger.info("[%s] skill.review.skip | reason=transcript-too-short", session_id)
            return

        user_content = build_skill_review_user_content(transcript)
        chat_messages = [
            ChatMessage(role="system", content=SKILL_REVIEW_SYSTEM),
            ChatMessage(role="user", content=user_content),
        ]
        await self._execute_messages(
            f"{session_id}:skill-review",
            chat_messages,
            allow_tools=True,
            tool_result_session_id=None,
            tool_registry=tool_registry,
            on_tool_before_execute=None,
            refresh_system_prompt=lambda: self._build_system_prompt(session_id),
            max_tool_iterations=self._max_tool_iterations_getter(),
        )
        logger.info("[%s] skill.review.done", session_id)
