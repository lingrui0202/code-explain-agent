"""文件类工具：读取源码、列目录、正则搜索。

这是 Agent 的"眼睛"——先看到真实代码，再解释，避免 LLM 凭空编造。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult
from .paths import PathError, detect_language, iter_files, resolve_path


class ReadFileTool(Tool):
    """读取文件内容（支持按行区间读取大文件）。"""

    name = "read_file"
    description = (
        "读取指定文件的内容，可指定行区间。用于查看代码实现细节。"
        "对超过 400 行的大文件，建议先用 code_outline 看结构，再用本工具按区间精读。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径，相对路径基于项目根目录"},
            "start_line": {"type": "integer", "description": "起始行号（含），从 1 开始，默认 1"},
            "end_line": {"type": "integer", "description": "结束行号（含），默认读到最后一行"},
        },
        "required": ["path"],
    }

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(self, path: str, start_line: int = 1, end_line: int | None = None, **_: Any) -> ToolResult:
        try:
            target = resolve_path(path, self.workspace)
        except PathError as exc:
            return ToolResult(tool=self.name, ok=False, output="", error=str(exc))

        if target.is_dir():
            return ToolResult(
                tool=self.name, ok=False, output="",
                error=f"{target} 是目录，请使用 list_dir 或 search_code。",
            )

        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                tool=self.name, ok=False, output="",
                error=f"无法以 UTF-8 解码 {target}，可能是二进制文件。",
            )
        except OSError as exc:
            return ToolResult(tool=self.name, ok=False, output="", error=f"读取失败：{exc}")

        lines = text.splitlines()
        total = len(lines)
        start = max(1, int(start_line or 1))
        end = total if end_line is None else min(total, int(end_line))

        if start > total:
            return ToolResult(
                tool=self.name, ok=False, output="",
                error=f"起始行 {start} 超出文件总行数 {total}。",
            )

        body = "\n".join(f"{i:>5} | {lines[i - 1]}" for i in range(start, end + 1))
        header = f"文件: {target}\n语言: {detect_language(target)} | 总行数: {total} | 本次读取: {start}-{end} 行"
        return ToolResult(
            tool=self.name,
            ok=True,
            output=f"{header}\n\n{body}",
            meta={"path": str(target), "total_lines": total},
        )


class ListDirTool(Tool):
    """列出目录结构，帮助 Agent 定位目标文件。"""

    name = "list_dir"
    description = "列出目录内容（默认一层，recursive=true 时递归）。用于定位要解释的文件。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径，默认项目根目录"},
            "recursive": {"type": "boolean", "description": "是否递归列出，默认 false"},
            "limit": {"type": "integer", "description": "最多显示多少条，默认 60"},
        },
        "required": [],
    }

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(self, path: str = "", recursive: bool = False, limit: int = 60, **_: Any) -> ToolResult:
        try:
            target = resolve_path(path or ".", self.workspace)
        except PathError as exc:
            return ToolResult(tool=self.name, ok=False, output="", error=str(exc))

        if not target.is_dir():
            return ToolResult(tool=self.name, ok=False, output="", error=f"{target} 不是目录。")

        if recursive:
            files = list(iter_files(target))[: limit]
            body = "\n".join(str(f.relative_to(target)) for f in files)
            extra = f"\n...（仅显示前 {limit} 个）" if len(list(iter_files(target))) > limit else ""
        else:
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
            shown = entries[:limit]
            body = "\n".join(f"{'[目录] ' if e.is_dir() else '[文件] '}{e.name}" for e in shown)
            extra = f"\n...（共 {len(entries)} 项，仅显示前 {limit} 个）" if len(entries) > limit else ""

        if not body:
            body = "（空目录）"
        return ToolResult(
            tool=self.name, ok=True,
            output=f"目录: {target}\n\n{body}{extra}",
            meta={"path": str(target)},
        )


class SearchCodeTool(Tool):
    """在代码中正则搜索，用于定位某个符号的定义/引用位置。"""

    name = "search_code"
    description = (
        "在项目或指定目录中按正则表达式搜索代码，返回 文件:行号:内容。"
        "用于定位某个函数/变量在哪些位置被定义或调用。会自动跳过 .git、node_modules 等目录。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式，例如 def\\s+login 或 TODO"},
            "path": {"type": "string", "description": "搜索根目录，默认项目根目录；也可直接给单个文件"},
            "file_glob": {"type": "string", "description": "文件名过滤，如 *.py，默认全部文件"},
            "max_results": {"type": "integer", "description": "最多返回多少条，默认 40"},
        },
        "required": ["pattern"],
    }

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(
        self,
        pattern: str,
        path: str = "",
        file_glob: str = "*",
        max_results: int = 40,
        **_: Any,
    ) -> ToolResult:
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return ToolResult(tool=self.name, ok=False, output="", error=f"正则表达式非法：{exc}")

        try:
            target = resolve_path(path or ".", self.workspace)
        except PathError as exc:
            return ToolResult(tool=self.name, ok=False, output="", error=str(exc))

        targets = [target] if target.is_file() else list(iter_files(target, file_glob))
        hits: list[str] = []
        for file_path in targets:
            try:
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    rel = file_path if file_path == target else file_path.relative_to(target)
                    hits.append(f"{rel}:{lineno}: {line.strip()[:160]}")
                    if len(hits) >= max_results:
                        break
            if len(hits) >= max_results:
                break

        if not hits:
            return ToolResult(
                tool=self.name, ok=True,
                output=f"未在 {target} 中匹配到 /{pattern}/。可尝试放宽正则或更换目录。",
                meta={"count": 0},
            )
        truncated = "\n...（结果已截断）" if len(hits) >= max_results else ""
        return ToolResult(
            tool=self.name, ok=True,
            output=f"匹配 /{pattern}/ 共 {len(hits)} 处：\n" + "\n".join(hits) + truncated,
            meta={"count": len(hits)},
        )
