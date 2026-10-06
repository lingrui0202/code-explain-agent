"""执行类工具：安全运行 Python 片段 + 保存解释报告。

安全策略（默认关闭执行）：
- 只有 Settings.allow_exec=True（或 CLI --allow-exec）时才注册 run_python；
- 在独立临时目录以子进程运行，设置硬超时，超时即 kill；
- 捕获 stdout/stderr，限制输出长度，绝不让子进程拖垮主进程。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult
from .paths import PathError, resolve_path

MAX_OUTPUT_CHARS = 4000


class RunPythonTool(Tool):
    """执行一小段 Python 代码，用于验证对代码行为的推断（例如"这个函数输入 X 会返回什么"）。"""

    name = "run_python"
    description = (
        "在隔离的临时目录中执行一段 Python 代码，返回 stdout/stderr，超时 10 秒。"
        "适合用来验证对函数行为的推断，例如调用目标函数观察真实返回值。"
        "注意：仅用于可信的本地代码片段。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 代码"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 10，最大 30"},
        },
        "required": ["code"],
    }

    def run(self, code: str, timeout: int = 10, **_: Any) -> ToolResult:
        timeout = max(1, min(int(timeout or 10), 30))
        with tempfile.TemporaryDirectory(prefix="cea-run-") as tmp:
            script = Path(tmp) / "snippet.py"
            script.write_text(code, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [sys.executable, str(script)],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=tmp,
                    env={"PATH": "", "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"},
                )
            except subprocess.TimeoutExpired:
                return ToolResult(tool=self.name, ok=False, output="", error=f"执行超时（>{timeout}s），已终止。")
            except OSError as exc:
                return ToolResult(tool=self.name, ok=False, output="", error=f"启动子进程失败：{exc}")

        stdout = completed.stdout[:MAX_OUTPUT_CHARS]
        stderr = completed.stderr[:MAX_OUTPUT_CHARS]
        body = f"exit_code={completed.returncode}\n--- stdout ---\n{stdout or '(空)'}\n--- stderr ---\n{stderr or '(空)'}"
        return ToolResult(tool=self.name, ok=completed.returncode == 0, output=body, error=stderr or None)


class SaveReportTool(Tool):
    """把解释报告写入 Markdown 文件，便于归档或分享。"""

    name = "save_report"
    description = "把最终的代码解释报告写入 Markdown 文件（相对项目根目录），返回文件路径。仅在用户要求保存时调用。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "输出文件路径，如 docs/explain-demo.md"},
            "content": {"type": "string", "description": "Markdown 正文"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(self, path: str, content: str, **_: Any) -> ToolResult:
        try:
            workspace = resolve_path(".", self.workspace)
        except PathError as exc:
            return ToolResult(tool=self.name, ok=False, output="", error=str(exc))

        target = (Path(path) if Path(path).is_absolute() else workspace / path)
        try:
            target = target.resolve()
        except OSError as exc:
            return ToolResult(tool=self.name, ok=False, output="", error=f"路径解析失败：{exc}")

        # 防目录穿越：必须落在 workspace 内
        if workspace not in target.parents and target != workspace:
            return ToolResult(
                tool=self.name, ok=False, output="",
                error=f"拒绝写入 workspace 之外的路径：{target}",
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(tool=self.name, ok=False, output="", error=f"写入失败：{exc}")

        return ToolResult(
            tool=self.name, ok=True,
            output=f"报告已写入：{target}（{len(content)} 字符）",
            meta={"path": str(target)},
        )
