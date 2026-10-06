"""上下文记忆：多轮对话的短期记忆 + 长工具结果的自动压缩。

解决的问题：
1. 多轮追问（"那这个函数的参数呢？"）需要保留历史；
2. 工具返回的大段代码会迅速撑爆上下文，需要滑动窗口 + 截断；
3. 系统提示词必须永远保留在最前面，不能被窗口裁掉。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..llm.base import Message


@dataclass
class ContextMemory:
    """滑动窗口式对话记忆。"""

    system_prompt: str = ""
    window: int = 12
    max_output_chars: int = 6000
    _messages: list[Message] = field(default_factory=list)

    # ------------------------------------------------------------------
    def set_system(self, prompt: str) -> None:
        self.system_prompt = prompt

    def add(self, message: Message) -> None:
        self._messages.append(message)
        self._evict()

    def add_user(self, content: str) -> None:
        self.add(Message(role="user", content=content))

    def add_assistant(self, content: str, tool_calls: list | None = None) -> None:
        self.add(Message(role="assistant", content=content, tool_calls=tool_calls))

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.add(Message(role="tool", content=self._truncate(content), name=name, tool_call_id=tool_call_id))

    # ------------------------------------------------------------------
    def _truncate(self, content: str) -> str:
        if len(content) <= self.max_output_chars:
            return content
        keep = self.max_output_chars
        head = content[: int(keep * 0.7)]
        tail = content[-int(keep * 0.25):]
        return f"{head}\n\n... [已截断 {len(content) - keep} 字符，保留首尾关键部分] ...\n\n{tail}"

    def _evict(self) -> None:
        """滑出窗口的消息直接丢弃，但保证 tool 消息与其调用消息成对存活。"""
        if len(self._messages) <= self.window:
            return
        # 找到第一个安全的截断点：不切断 assistant(tool_calls) 与其后续 tool 消息
        overflow = len(self._messages) - self.window
        cut = 0
        while cut < overflow:
            if self._messages[cut].role == "assistant" and self._messages[cut].tool_calls:
                break  # 停在这条调用之前，避免留下孤立的 tool 结果
            cut += 1
        self._messages = self._messages[cut:]

    # ------------------------------------------------------------------
    def render(self) -> list[Message]:
        """渲染为可发送给 LLM 的完整消息序列。"""
        messages: list[Message] = []
        if self.system_prompt:
            messages.append(Message(role="system", content=self.system_prompt))
        messages.extend(self._messages)
        return messages

    @property
    def turns(self) -> int:
        return sum(1 for m in self._messages if m.role == "user")

    def clear(self) -> None:
        self._messages.clear()

    def stats(self) -> dict[str, int]:
        return {
            "messages": len(self._messages),
            "user_turns": self.turns,
            "chars": sum(len(m.content) for m in self._messages),
        }
