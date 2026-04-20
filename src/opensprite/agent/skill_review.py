"""Background skill persistence review — plain-text transcript + dedicated system prompt."""

from __future__ import annotations

from typing import Any, Sequence

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
    """Turn stored chat rows into a plain-text transcript for the review model."""
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
        "Below is a plain-text transcript of recent messages in this chat (including tools when logged).\n\n"
        f"--- TRANSCRIPT ---\n{transcript}\n--- END TRANSCRIPT ---\n\n"
        "Review the transcript. If a reusable how-to should be saved or updated as a skill, use the tools. "
        "Otherwise reply with exactly: Nothing to save."
    )
