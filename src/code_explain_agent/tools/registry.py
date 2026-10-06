"""工具装配：根据配置组装本次运行可用的工具集。"""

from __future__ import annotations

from ..config import Settings
from .analysis_tools import CodeMetricsTool, CodeOutlineTool
from .base import ToolRegistry
from .exec_tools import RunPythonTool, SaveReportTool
from .file_tools import ListDirTool, ReadFileTool, SearchCodeTool


def build_registry(settings: Settings) -> ToolRegistry:
    """按配置注册工具。是否启用代码执行由 allow_exec 决定（默认关闭）。"""
    registry = ToolRegistry()
    ws = settings.workspace

    registry.register(ReadFileTool(ws))
    registry.register(CodeOutlineTool(ws))
    registry.register(CodeMetricsTool(ws))
    registry.register(SearchCodeTool(ws))
    registry.register(ListDirTool(ws))
    registry.register(SaveReportTool(ws))
    if settings.allow_exec:
        registry.register(RunPythonTool())

    return registry
