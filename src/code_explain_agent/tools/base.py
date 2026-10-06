"""工具抽象层：Tool 基类 + 参数校验 + 统一结果封装 + 注册表。

Agent 只认识"工具名 + JSON Schema 参数"，不关心工具内部实现，
新增工具只需继承 Tool 并注册，无需改动 Agent 代码（开闭原则）。
"""

from __future__ import annotations

import json
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """工具执行的统一返回。失败也不抛异常，而是把错误文本回喂给 LLM 让其自我纠正。"""

    tool: str
    ok: bool
    output: str
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_tool_message(self) -> str:
        """转成喂回 LLM 的文本。失败时附带可操作的修复提示。"""
        if self.ok:
            return self.output
        return f"[工具 {self.tool} 执行失败] {self.error}\n请修正参数后重试，或换用其他工具完成任务。"

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        return self.as_tool_message()


class Tool(ABC):
    """工具基类。"""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    def run(self, **kwargs: Any) -> ToolResult:
        """执行工具逻辑。任何异常都应被捕获并转为 ok=False 的结果。"""

    # ------------------------------------------------------------------
    def schema(self) -> dict[str, Any]:
        """OpenAI function calling 所需的 JSON Schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def invoke(self, arguments: dict[str, Any]) -> ToolResult:
        """带异常捕获与参数校验的执行入口。"""
        if "__parse_error__" in arguments:
            return ToolResult(
                tool=self.name,
                ok=False,
                output="",
                error=f"参数不是合法 JSON：{arguments['__parse_error__']!r}",
            )

        missing = [
            key
            for key in self.parameters.get("required", [])
            if key not in arguments or arguments[key] in (None, "")
        ]
        if missing:
            return ToolResult(
                tool=self.name,
                ok=False,
                output="",
                error=f"缺少必填参数：{missing}。该工具的参数 schema 为 {json.dumps(self.parameters, ensure_ascii=False)}",
            )

        unknown = set(arguments) - set(self.parameters.get("properties", {}))
        try:
            result = self.run(**arguments)
        except Exception as exc:  # noqa: BLE001 - 工具失败不能让 Agent 崩溃
            result = ToolResult(
                tool=self.name,
                ok=False,
                output="",
                error=f"{type(exc).__name__}: {exc}",
                meta={"traceback": traceback.format_exc(limit=3)},
            )
        if unknown:
            result.meta["ignored_args"] = sorted(unknown)
        return result

    def signature(self) -> str:
        props = self.parameters.get("properties", {})
        parts = []
        for key, spec in props.items():
            required = key in self.parameters.get("required", [])
            parts.append(f"{key}: {spec.get('type', 'any')}" + ("" if required else " = ?"))
        return f"{self.name}({', '.join(parts)})"


class ToolRegistry:
    """工具注册表：负责注册、查找、生成 schema、分发执行。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if not tool.name:
            raise ValueError("工具必须声明 name")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                tool=name,
                ok=False,
                output="",
                error=f"未知工具 '{name}'。可用工具：{', '.join(self.names())}",
            )
        return tool.invoke(arguments)

    def describe(self) -> str:
        return "\n".join(f"- {t.signature()}  # {t.description}" for t in self._tools.values())
