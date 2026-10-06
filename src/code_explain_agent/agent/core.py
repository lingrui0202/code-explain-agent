"""Agent 核心：ReAct 循环（输入 → 推理 → 工具调用 → 观察 → 输出）。

两种工具调用协议都支持，并按顺序降级：
1. **原生 function calling**（默认）：模型返回结构化 tool_calls，最可靠；
2. **ReAct 文本协议**：模型在正文里输出 JSON Action，由本模块解析；
   这样即使模型不支持/不稳定支持 function calling，Agent 依然能工作。

可靠性设计：
- 工具调用失败不抛异常，错误文本回喂模型让其自我纠正；
- 达到 max_rounds 仍未收敛时，强制进入"总结模式"产出答案，绝不空手而归；
- LLM 调用异常区分可重试/不可重试，可重试的已在 Provider 层退避重试。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Settings
from ..llm.base import LLMError, LLMProvider, Message, ToolCall
from ..llm.factory import build_provider
from ..tools.base import ToolRegistry
from ..tools.registry import build_registry
from .memory import ContextMemory
from .prompts import REFLECT_PROMPT, build_system_prompt

FINAL_PREFIX_RE = re.compile(r"^\s*Final\s*Answer\s*[:：]\s*", re.IGNORECASE)


@dataclass
class Step:
    """一次可观测的 Agent 动作，用于 CLI/Web 展示推理链路。"""

    kind: str            # thought | tool_call | tool_result | answer | error
    text: str
    tool: str | None = None
    ok: bool | None = None
    round: int = 0


@dataclass
class AgentResult:
    """一次 Agent 运行的完整结果。"""

    answer: str
    steps: list[Step] = field(default_factory=list)
    rounds: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def parse_text_action(text: str) -> ToolCall | None:
    """从模型正文中解析 ReAct 风格的 JSON 工具调用。

    支持：```json 代码块、裸 JSON 对象。键名兼容 tool/name、args/arguments/input。
    """
    decoder = json.JSONDecoder()
    candidates: list[str] = []

    fenced = re.findall(r"```(?:json|JSON)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates.extend(fenced)

    # 扫描所有 '{' 起点，用 raw_decode 找到第一个合法 JSON 对象
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(obj, dict):
            candidates.append(json.dumps(obj, ensure_ascii=False))

    for raw in candidates:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        name = obj.get("tool") or obj.get("name") or obj.get("action")
        args = obj.get("args") or obj.get("arguments") or obj.get("input") or obj.get("parameters")
        if isinstance(name, str) and name:
            if args is None:
                args = {k: v for k, v in obj.items() if k not in {"tool", "name", "action", "args", "arguments", "input", "parameters"}}
            if not isinstance(args, dict):
                args = {"value": args}
            return ToolCall(id="", name=name, arguments=args)
    return None


class CodeExplainAgent:
    """代码解释 Agent。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        provider: LLMProvider | None = None,
        registry: ToolRegistry | None = None,
        on_event: Callable[[Step], None] | None = None,
    ) -> None:
        self.settings = settings or Settings.load()
        self.provider = provider or build_provider(self.settings)
        self.registry = registry or build_registry(self.settings)
        self.on_event = on_event
        self.memory = ContextMemory(
            window=self.settings.history_window,
            max_output_chars=self.settings.max_tool_output_chars,
        )
        self.memory.set_system(
            build_system_prompt(
                tool_descriptions=self.registry.describe(),
                workspace=self.settings.workspace,
                max_rounds=self.settings.max_rounds,
            )
        )
        self.usage: dict[str, int] = {}

    # ------------------------------------------------------------------
    def _emit(self, step: Step) -> None:
        if self.on_event:
            try:
                self.on_event(step)
            except Exception:  # noqa: BLE001 - 展示层异常不影响 Agent
                pass

    # ------------------------------------------------------------------
    def run(self, user_input: str) -> AgentResult:
        """执行一次完整的 Agent 循环。"""
        steps: list[Step] = []
        self.memory.add_user(user_input)

        tools_payload = self.registry.specs() if self.settings.native_tools else None
        answer = ""
        error: str | None = None
        used_rounds = 0

        for round_index in range(1, self.settings.max_rounds + 1):
            used_rounds = round_index
            messages = self.memory.render()
            is_last = round_index == self.settings.max_rounds
            if is_last:
                # 最后一轮强制收敛：只总结，不再调用工具
                messages.append(Message(role="user", content=REFLECT_PROMPT))
                tools_payload = None

            try:
                response = self.provider.chat(messages, tools=tools_payload)
            except LLMError as exc:
                error = f"LLM 调用失败：{exc}"
                steps.append(Step(kind="error", text=error, round=round_index))
                self._emit(steps[-1])
                answer = self._fallback_answer(error)
                break
            except Exception as exc:  # noqa: BLE001 - 兜底，避免整个会话崩溃
                error = f"未预期的错误：{type(exc).__name__}: {exc}"
                steps.append(Step(kind="error", text=error, round=round_index))
                self._emit(steps[-1])
                answer = self._fallback_answer(error)
                break

            self._accumulate_usage(response.usage)

            if response.content.strip():
                steps.append(Step(kind="thought", text=response.content.strip(), round=round_index))
                self._emit(steps[-1])

            # --- 路径 1：原生 function calling ---
            if response.tool_calls and not is_last:
                self.memory.add_assistant(response.content, response.tool_calls)
                for call in response.tool_calls:
                    self._run_tool(call, round_index, steps)
                continue

            # --- 路径 2：文本 ReAct 协议 ---
            parsed = parse_text_action(response.content)
            if parsed and not is_last:
                self.memory.add_assistant(response.content)
                self._run_tool(parsed, round_index, steps)
                continue

            # --- 路径 3：最终答案 ---
            answer = FINAL_PREFIX_RE.sub("", response.content).strip()
            self.memory.add_assistant(response.content)
            break

        if not answer:
            answer = self._fallback_answer("已达到最大推理轮数，以下为已取证内容的汇总。")
            steps.append(Step(kind="error", text="达到最大轮数，转入总结模式", round=used_rounds))

        steps.append(Step(kind="answer", text=answer, round=used_rounds))
        self._emit(steps[-1])

        return AgentResult(
            answer=answer,
            steps=steps,
            rounds=used_rounds,
            usage=dict(self.usage),
            error=error,
        )

    # ------------------------------------------------------------------
    def _run_tool(self, call: ToolCall, round_index: int, steps: list[Step]) -> None:
        args_text = ", ".join(f"{k}={v!r}" for k, v in list(call.arguments.items())[:4])
        call_step = Step(
            kind="tool_call",
            text=f"{call.name}({args_text})" if args_text else f"{call.name}()",
            tool=call.name,
            round=round_index,
        )
        steps.append(call_step)
        self._emit(call_step)

        result = self.registry.execute(call.name, call.arguments)

        result_step = Step(
            kind="tool_result",
            text=result.as_tool_message(),
            tool=call.name,
            ok=result.ok,
            round=round_index,
        )
        steps.append(result_step)
        self._emit(result_step)

        self.memory.add_tool_result(call.id or f"text_call_{round_index}", call.name, result.as_tool_message())

    # ------------------------------------------------------------------
    def _accumulate_usage(self, usage: dict[str, int]) -> None:
        for key, value in usage.items():
            self.usage[key] = self.usage.get(key, 0) + value

    def _fallback_answer(self, reason: str) -> str:
        """出错或超轮时，用已取证的工具结果兜底，保证用户总能拿到有用信息。"""
        gathered = [
            f"### {m.name}\n{m.content[:1200]}"
            for m in self.memory.render()
            if m.role == "tool" and m.content.strip()
        ]
        body = "\n\n".join(gathered) if gathered else "（本次运行未取得任何工具结果）"
        return (
            f"> ⚠ {reason}\n\n"
            f"以下是本次运行已经获取到的代码信息，供你参考：\n\n{body}"
        )

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """清空对话记忆（保留系统提示词）。"""
        self.memory.clear()

    @property
    def tools(self) -> list[str]:
        return self.registry.names()

    def tool_signatures(self) -> list[str]:
        return [self.registry.get(name).signature() for name in self.registry.names() if self.registry.get(name)]  # type: ignore[union-attr]

    def describe_config(self) -> dict[str, Any]:
        return {
            "provider": self.settings.provider,
            "provider_label": self.settings.provider_label,
            "model": self.settings.model,
            "base_url": self.settings.base_url,
            "workspace": self.settings.workspace,
            "tools": self.tools,
            "max_rounds": self.settings.max_rounds,
        }
