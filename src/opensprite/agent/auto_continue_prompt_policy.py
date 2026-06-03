"""Prompt policy helpers for bounded auto-continuation."""

from __future__ import annotations


NO_TOOL_EXISTING_SOURCE_FINAL_ANSWER_INSTRUCTION = (
    "\nDo not reply with another progress-only promise or tool-use plan. "
    "Write the final answer now from these gathered sources."
)


def existing_web_source_section(source_context: str, *, allow_tools: bool) -> str:
    source_context = source_context.strip()
    if not source_context:
        return ""
    no_tool_instruction = "" if allow_tools else NO_TOOL_EXISTING_SOURCE_FINAL_ANSWER_INSTRUCTION
    return (
        "\n\nExisting gathered web sources from the previous pass:\n"
        f"{source_context}\n"
        "Use these sources for the final answer instead of repeating web research unless they are clearly insufficient."
        f"{no_tool_instruction}"
    )


def terse_final_answer_follow_up_instruction() -> str:
    return (
        "\n- Quality follow-up: the previous final answer was too terse. "
        "Do not reply with only 'done', 'completed', or another short acknowledgement. "
        "Use the available tool/artifact results to write a substantive final answer that covers each requested resource and deliverable."
    )
