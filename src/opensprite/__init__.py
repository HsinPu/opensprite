"""OpenSprite - Ultra-lightweight personal AI assistant"""

from .agent import AgentLoop
from .config import AgentConfig
from .bus.message import UserMessage, AssistantMessage, MessageAdapter
from .llms import LLMProvider, ChatMessage, LLMResponse, OpenAILLM
from .storage import StorageProvider, StoredMessage, MemoryStorage
from .bus.dispatcher import MessageQueue, Conversation

__version__ = "0.1.0"
__all__ = [
    "AgentLoop", 
    "AgentConfig", 
    "UserMessage", 
    "AssistantMessage",
    "MessageAdapter",
    "LLMProvider",
    "ChatMessage",
    "LLMResponse",
    "OpenAILLM",
    "StorageProvider",
    "StoredMessage",
    "MemoryStorage",
    "MessageQueue",
    "Conversation"
]
