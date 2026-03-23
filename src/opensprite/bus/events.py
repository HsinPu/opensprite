"""
opensprite/bus/events.py - 訊息匯流排的事件類型

定義 MessageBus 使用的訊息結構：
- InboundMessage：從聊天頻道收到的訊息
- OutboundMessage：要發送到聊天頻道的訊息
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # User identifier
    chat_id: str  # Transport chat/channel identifier
    content: str  # Message text
    session_chat_id: str | None = None  # Internal normalized chat/session identifier
    sender_name: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    images: list[str] = field(default_factory=list)  # base64 image data URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    raw: Any = None

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        return self.session_chat_id or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel."""

    channel: str
    chat_id: str  # Transport chat/channel identifier
    content: str
    session_chat_id: str | None = None
    reply_to: str | None = None
    images: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: Any = None
