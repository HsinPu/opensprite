"""
opensprite/context/builder.py - ContextBuilder 介面定義

定義 ContextBuilder Protocol，用於建構 Agent 的 prompt 上下文
不同實作方式可以從不同來源（檔案、資料庫、API 等）組裝 prompt
"""

from pathlib import Path
from typing import Protocol


class ContextBuilder(Protocol):
    """
    Context builder protocol.
    
    Implement this to create different ways of building context/prompts.
    """
    
    def build_system_prompt(self, session_id: str = "default") -> str:
        """Build the system prompt."""
        ...
    
    def build_messages(
        self,
        history: list[dict],
        current_message: str,
        current_images: list[str] | None = None,
        channel: str | None = None,
        session_id: str | None = None,
    ) -> list[dict]:
        """
        Build complete message list for LLM call.
        
        Args:
            history: Conversation history
            current_message: Current user message
            current_images: Current user images (base64 data URLs)
            channel: Channel name (e.g., "telegram", "discord")
            session_id: OpenSprite session ID
            
        Returns:
            List of message dicts with "role" and "content"
        """
        ...
    
    def add_tool_result(
        self,
        messages: list[dict],
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> list[dict]:
        """Add tool result to messages."""
        ...
    
    def add_assistant_message(
        self,
        messages: list[dict],
        content: str | None,
        tool_calls: list[dict] | None = None,
    ) -> list[dict]:
        """Add assistant message (with tool calls) to messages."""
        ...
