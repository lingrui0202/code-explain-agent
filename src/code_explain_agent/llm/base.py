"""LLM 抽象层：定义与具体厂商无关的消息 / 工具调用 / 响应数据结构。

这样做的好处：上层 Agent 只依赖本模块的抽象，切换 DeepSeek / Qwen / OpenAI / 本地离线
实现时无需改动任何 Agent 代码（依赖倒置）。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class LLMError(RuntimeError):
    """LLM 调用失败。

    retryable=True 表示属于可重试错误（网络抖动、限流、5xx），Agent 会自动退避重试；
    retryable=False 表示参数错误 / 鉴权失败等，重试无意义，应直接暴露给用户。
    """

    def __init__(self, message: str, *, retryable: bool = True, cause: BaseException | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.cause = cause


@dataclass
class ToolCall:
    """模型请求调用的一次工具。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """模型返回的一次响应。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class Message:
    """统一消息格式，可无损映射为 OpenAI 兼容协议的 messages。"""

    role: str  # system | user | assistant | tool
    content: str = ""
    name: str | None = None          # tool 消息对应的工具名
    tool_call_id: str | None = None  # tool 消息对应的调用 id
    tool_calls: list[ToolCall] | None = None

    def to_api(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role, "content": self.content}

        if self.role == "assistant" and self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in self.tool_calls
            ]
        if self.role == "tool":
            msg["tool_call_id"] = self.tool_call_id or ""
            if self.name:
                msg["name"] = self.name
        return msg


class LLMProvider(ABC):
    """所有 LLM 后端需要实现的接口。"""

    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """发起一次对话，返回模型响应（可能包含工具调用请求）。"""

    def close(self) -> None:  # pragma: no cover - 默认无资源需要释放
        return None
