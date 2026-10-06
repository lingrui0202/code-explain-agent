"""Provider 工厂：根据配置选择具体实现。"""

from __future__ import annotations

from ..config import Settings
from .base import LLMProvider
from .offline import OfflineProvider


def build_provider(settings: Settings) -> LLMProvider:
    """按 provider 名称创建 LLM 后端。

    所有在线服务商都是 OpenAI 兼容协议，统一由 OpenAICompatProvider 处理；
    offline 走本地静态分析。
    """
    if settings.provider == "offline":
        return OfflineProvider(settings)
    from .openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(settings)
