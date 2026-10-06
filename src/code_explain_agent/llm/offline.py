"""离线兜底 Provider：没有 API Key 时也能完整跑通 Agent 的"推理 → 工具调用 → 输出"循环。

它扮演一个"规则驱动的规划者"：
- 第 0 轮：调用 code_outline 建立结构认知；
- 第 1 轮：调用 read_file 精读实现；
- 第 2 轮：调用 code_metrics 取得度量；
- 第 3 轮：汇总工具结果，输出基于真实静态分析的解释报告。

因此演示时即使完全断网，也能看到与在线模式完全一致的 Agent 行为链路。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from ..config import Settings
from ..tools.analysis_tools import build_metrics, build_outline
from ..tools.paths import iter_files
from .base import LLMProvider, LLMResponse, Message, ToolCall

PATH_RE = re.compile(
    r"[\w./\\:<>-]+\.(?:py|js|jsx|ts|tsx|java|go|rs|c|h|cpp|cc|hpp|cs|rb|php|kt|swift|scala|sh|sql|md|json|ya?ml|toml)\b",
    re.IGNORECASE,
)
QUOTED_RE = re.compile(r"[\"'`]([^\"'`\s]+)[\"'`]")


class OfflineProvider(LLMProvider):
    """基于本地静态分析的确定性"模型"。"""

    name = "offline"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.workspace = Path(settings.workspace)

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        available = {t["function"]["name"] for t in (tools or [])}
        user_text = self._latest_user_text(messages)
        done = sum(1 for m in messages if m.role == "tool")
        target = self._find_target(user_text)

        if target is None:
            if done == 0 and "list_dir" in available:
                return self._call(0, "list_dir", {"path": ".", "limit": 40})
            return LLMResponse(content=self._no_target_answer(user_text), finish_reason="stop")

        plan: list[tuple[str, dict[str, Any]]] = [
            ("code_outline", {"path": target}),
            ("read_file", {"path": target, "end_line": 150}),
            ("code_metrics", {"path": target}),
        ]
        plan = [step for step in plan if step[0] in available]

        if done < len(plan):
            name, args = plan[done]
            return self._call(done, name, args)

        return LLMResponse(content=self._final_answer(target, user_text), finish_reason="stop")

    # ------------------------------------------------------------------
    @staticmethod
    def _call(index: int, name: str, args: dict[str, Any]) -> LLMResponse:
        return LLMResponse(
            content=f"（离线规划）接下来调用 {name} 获取真实代码信息。",
            tool_calls=[ToolCall(id=f"offline_call_{index}", name=name, arguments=args)],
            finish_reason="tool_calls",
        )

    @staticmethod
    def _latest_user_text(messages: list[Message]) -> str:
        for message in reversed(messages):
            if message.role == "user":
                return message.content
        return ""

    def _find_target(self, text: str) -> str | None:
        """从用户问题里找出一个真实存在的文件。

        两级查找：先按原样解析（支持相对/绝对路径），失败再用文件名在项目内递归搜索，
        这样用户只说 "sample_code.py" 也能命中 examples/sample_code.py。
        """
        candidates: list[str] = []
        candidates.extend(PATH_RE.findall(text))
        candidates.extend(QUOTED_RE.findall(text))

        basenames: list[str] = []
        for raw in candidates:
            raw = raw.strip().strip("<>")
            probe = Path(raw)
            if not probe.is_absolute():
                probe = self.workspace / raw
            try:
                resolved = probe.resolve()
            except OSError:
                continue
            if resolved.is_file():
                return str(resolved)
            if probe.name:
                basenames.append(probe.name)

        return self._search_by_name(basenames)

    def _search_by_name(self, basenames: list[str]) -> str | None:
        """在项目目录内按文件名递归查找（跳过 .git / node_modules 等）。"""
        if not basenames:
            return None
        wanted = {name.lower() for name in basenames}
        for path in iter_files(self.workspace):
            if path.name.lower() in wanted:
                return str(path)
        return None

    # ------------------------------------------------------------------
    def _final_answer(self, target: str, question: str) -> str:
        path = Path(target)
        parts = [
            f"## 文件概览\n\n- 路径：`{path}`\n- 语言：{path.suffix or '未知'}",
            "## 代码结构\n\n```text\n" + build_outline(path) + "\n```",
        ]

        if path.suffix.lower() == ".py":
            parts.append("## 逐块解读\n\n" + self._explain_python(path))
        else:
            preview = self._preview(path)
            parts.append("## 内容预览\n\n```text\n" + preview + "\n```")

        parts.append("## 度量与潜在问题\n\n" + build_metrics(path))
        parts.append(self._offline_note(question))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    def _explain_python(self, path: Path) -> str:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError) as exc:
            return f"无法解析：{exc}"

        blocks: list[str] = []
        doc = ast.get_docstring(tree)
        if doc:
            blocks.append(f"**模块职责**：{doc.strip().splitlines()[0]}\n")

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                blocks.append(self._describe_function(node, prefix=""))
            elif isinstance(node, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in node.bases)
                head = f"**class `{node.name}`**" + (f"（继承 {bases}）" if bases and bases != "object" else "")
                doc_line = ast.get_docstring(node)
                blocks.append(f"{head}  —— L{node.lineno}-L{node.end_lineno}\n" + (f"  {doc_line.strip().splitlines()[0]}\n" if doc_line else ""))
                methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if methods:
                    blocks.append("  方法：")
                    blocks.extend("    " + self._describe_function(m, prefix="").replace("\n", "\n    ") for m in methods)
                else:
                    blocks.append("  方法：无（可能是纯数据类或占位类）")
            elif isinstance(node, ast.If) and ast.unparse(node.test).startswith("__name__"):
                blocks.append(f"**入口**：`if __name__ == '__main__'` 位于 L{node.lineno}，说明该文件可直接运行。")
        return "\n".join(blocks) if blocks else "未解析出顶层定义。"

    @staticmethod
    def _describe_function(node: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str) -> str:
        name = node.name
        args = ", ".join(arg.arg for arg in node.args.args)
        lineno, end = node.lineno, node.end_lineno
        doc = ast.get_docstring(node)
        calls = sorted({
            ast.unparse(n.func) for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
        })
        branches = sum(
            1 for n in ast.walk(node)
            if isinstance(n, (ast.If, ast.For, ast.While, ast.Try, ast.With))
        )
        raises = sorted({
            ast.unparse(n.exc) for n in ast.walk(node)
            if isinstance(n, ast.Raise) and n.exc is not None
        })

        desc = [f"**{prefix}def `{name}({args})`**  —— L{lineno}-L{end}"]
        if doc:
            desc.append(f"  作用：{doc.strip().splitlines()[0]}")
        if calls:
            desc.append("  调用了：" + "、".join(f"`{c}()`" for c in calls[:8]))
        desc.append(f"  控制流块 {branches} 处" + (f"；可能抛出：{raises[0]}" if raises else ""))
        body_lines = (end or lineno) - lineno + 1
        if body_lines > 60:
            desc.append(f"  ⚠ 函数体 {body_lines} 行，偏长，建议拆分")
        return "\n".join(desc)

    @staticmethod
    def _preview(path: Path) -> str:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[:40]
        except (OSError, UnicodeDecodeError):
            return "（无法读取）"
        return "\n".join(f"{i:>4} | {line}" for i, line in enumerate(lines, 1))

    @staticmethod
    def _no_target_answer(question: str) -> str:
        return (
            "## 离线模式\n\n"
            f"未能从问题「{question[:60]}」中定位到本地文件，因此无法做静态分析。\n\n"
            "可以这样做：\n"
            "1. 在问题里带上具体文件名，例如：`解释 examples/sample_code.py 的作用`；\n"
            "2. 或先运行 `list_dir` 看看当前目录下有哪些文件；\n"
            "3. 或配置 API Key（`PROVIDER=deepseek` + `DEEPSEEK_API_KEY`）后切换到在线模式，"
            "由大模型直接回答开放式问题。\n\n"
            "> 当前处于离线兜底模式：Agent 的推理—工具调用—输出链路完整可用，"
            "但解释内容来自本地静态分析而非大模型生成。"
        )

    @staticmethod
    def _offline_note(question: str) -> str:
        return (
            "> **说明**：本次回答由**离线静态分析兜底模式**生成（未检测到可用的 LLM API Key）。\n"
            "> Agent 的「推理 → 工具调用 → 结果整合 → 输出」链路与在线模式完全一致，\n"
            "> 只是决策者由大模型换成了规则规划器。配置 `DEEPSEEK_API_KEY` 后即可得到大模型的自然语言解释。"
        )
