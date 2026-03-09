"""Memory consolidation - LLM-powered summarization."""

import json
from typing import Any

from minibot.utils.log import logger


# Tool definition for saving memory
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
                        "description": "Updated long-term memory as markdown. Include all existing facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["memory_update"],
            },
        },
    }
]


async def consolidate(
    memory_store: "MemoryStore",
    chat_id: str,
    messages: list[dict[str, Any]],
    provider: "LLMProvider",
    model: str,
) -> bool:
    """
    Consolidate old messages into memory via LLM.
    
    Args:
        memory_store: MemoryStore instance
        chat_id: Chat ID for per-chat memory
        messages: List of conversation messages to process
        provider: LLM provider
        model: Model to use
        
    Returns:
        True on success, False on failure
    """
    if not messages:
        return True

    # Build prompt with messages
    lines = []
    for m in messages:
        # Support both dict and object formats
        if isinstance(m, dict):
            content = m.get("content", "")
            role = m.get("role", "?").upper()
        else:
            content = getattr(m, "content", "")
            role = getattr(m, "role", "?").upper()
        
        if not content:
            continue
        lines.append(f"[{role}]: {content}")

    current_memory = memory_store.read(chat_id)
    prompt = f"""Process this conversation and call the save_memory tool with important information to remember.

Current memory:
{current_memory or "(empty)"}

Conversation:
{chr(10).join(lines[-20:])}  # Last 20 messages

Extract key facts, preferences, decisions, and important information. Update the memory accordingly."""

    try:
        response = await provider.chat(
            messages=[
                {"role": "system", "content": "You are a memory consolidation agent. Call the save_memory tool to update long-term memory with important information from the conversation."},
                {"role": "user", "content": prompt},
            ],
            tools=_SAVE_MEMORY_TOOL,
            model=model,
        )

        if not response.tool_calls:
            logger.warning("Memory consolidation: LLM did not call save_memory")
            return False

        args = response.tool_calls[0].arguments
        if isinstance(args, str):
            args = json.loads(args)

        if update := args.get("memory_update"):
            if update != current_memory:
                memory_store.write(chat_id, update)
                logger.info("Memory consolidated for chat {}: {} chars", chat_id, len(update))

        return True
    except Exception as e:
        import traceback
        logger.error(f"Memory consolidation failed: {e}\n{traceback.format_exc()}")
        return False
