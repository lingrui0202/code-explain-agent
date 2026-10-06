"""LLM 层：统一抽象 + 多后端实现。"""

from .base import LLMError, LLMProvider, LLMResponse, Message, ToolCall
from .factory import build_provider
from .offline import OfflineProvider

__all__ = [
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "ToolCall",
    "OfflineProvider",
    "build_provider",
]
