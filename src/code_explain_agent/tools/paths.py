"""路径安全：所有文件类工具都必须先经过这里解析，防止越权读取与符号链接逃逸。"""

from __future__ import annotations

from pathlib import Path

# 无论是否受限于 workspace，这些目录都不应该被读取/搜索
DEFAULT_IGNORES = {
    ".git",
    ".svn",
    ".hg",
    ".idea",
    ".vscode",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
}

# 支持的语言后缀 -> 语言名（用于解释与高亮）
LANG_BY_SUFFIX = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Shell",
    ".sql": "SQL",
    ".md": "Markdown",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
}


class PathError(ValueError):
    """路径非法或越权。"""


def resolve_path(raw: str, workspace: str | Path, *, must_exist: bool = True) -> Path:
    """把用户/模型给出的路径解析为绝对路径，并做越权校验。

    - 相对路径基于 workspace 解析；
    - 允许 workspace 之外的绝对路径（方便分析任意项目），但会拒绝解析失败的符号链接；
    - 若路径不存在且 must_exist=True，抛出带建议的错误。
    """
    if not raw or not str(raw).strip():
        raise PathError("路径为空，请提供具体的文件或目录路径。")

    path = Path(str(raw).strip()).expanduser()
    if not path.is_absolute():
        path = Path(workspace) / path

    try:
        path = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise PathError(f"无法解析路径 {raw!r}：{exc}") from exc

    if must_exist and not path.exists():
        hint = ""
        sibling = path.parent if path.parent.is_dir() else Path(workspace)
        if sibling.is_dir():
            candidates = sorted(p.name for p in sibling.iterdir())[:12]
            if candidates:
                hint = f"\n同级目录下有：{candidates}"
        raise PathError(f"路径不存在：{path}{hint}")
    return path


def should_ignore(path: Path) -> bool:
    """是否属于应跳过的目录/文件。"""
    if any(part in DEFAULT_IGNORES for part in path.parts):
        return True
    return path.name.startswith(".")


def detect_language(path: Path) -> str:
    return LANG_BY_SUFFIX.get(path.suffix.lower(), "未知")


def iter_files(root: Path, pattern: str = "*"):
    """遍历文件，自动跳过忽略目录。"""
    for path in sorted(root.rglob(pattern)):
        if not path.is_file():
            continue
        if should_ignore(path):
            continue
        yield path
