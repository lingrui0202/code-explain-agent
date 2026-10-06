"""代码分析工具：基于 AST 的结构提取与复杂度度量。

这些是"确定性工具"——结果不依赖 LLM，因此解释永远建立在真实代码之上，
而不是模型幻觉。离线模式下也靠它们生成解释。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult
from .paths import PathError, detect_language, resolve_path

# ---------------------------------------------------------------------------
# 纯函数：结构与度量（离线 Provider 也会复用）
# ---------------------------------------------------------------------------


def _format_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    parts: list[str] = []
    pos = list(args.posonlyargs) + list(args.args)
    defaults = list(args.defaults)
    offset = len(pos) - len(defaults)
    for idx, arg in enumerate(pos):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {ast.unparse(arg.annotation)}"
        if idx >= offset:
            text += f" = {ast.unparse(defaults[idx - offset])}"
        parts.append(text)
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        text = arg.arg
        if arg.annotation is not None:
            text += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            text += f" = {ast.unparse(default)}"
        parts.append(text)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return ", ".join(parts)


def _returns(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""


def _first_doc(node: ast.AST) -> str:
    doc = ast.get_docstring(node)  # type: ignore[arg-type]
    if not doc:
        return ""
    return "  # " + doc.strip().splitlines()[0][:70]


def build_outline(path: Path) -> str:
    """生成代码结构大纲：模块/类/函数/装饰器/行号。"""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"无法读取文件：{exc}"

    lines: list[str] = [f"# {path}  [{detect_language(path)}]"]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _regex_outline(path, source)

    module_doc = ast.get_docstring(tree)
    if module_doc:
        lines.append(f'""" {module_doc.strip().splitlines()[0]} """')

    # 导入
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or "."
            imports.extend(f"{module}.{alias.name}" for alias in node.names)
    if imports:
        lines.append("\n## 导入")
        lines.append("  " + ", ".join(sorted(set(imports))[:20]))

    # 顶层常量
    assigns = [
        f"{t.id}" if isinstance(t, ast.Name) else ast.unparse(t)
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
    ]
    if assigns:
        lines.append("\n## 模块级变量")
        lines.append("  " + ", ".join(assigns[:20]))

    def walk_body(body: list[ast.stmt], indent: str, prefix: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                deco = "".join(f"@{ast.unparse(d)}\n{indent}" for d in node.decorator_list)
                lines.append(
                    f"{indent}{deco}{kind} {prefix}{node.name}({_format_args(node)})"
                    f"{_returns(node)}   # L{node.lineno}-L{node.end_lineno}{_first_doc(node)}"
                )
            elif isinstance(node, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in node.bases) or "object"
                lines.append(f"\n{indent}class {node.name}({bases})   # L{node.lineno}-L{node.end_lineno}{_first_doc(node)}")
                walk_body(node.body, indent + "    ")
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)) and indent == "":
                # 顶层语句块只做提示，避免噪音
                lines.append(f"{indent}{type(node).__name__.lower()} block  # L{node.lineno}")

    walk_body(tree.body, "")
    return "\n".join(lines)


def _regex_outline(path: Path, source: str) -> str:
    """非 Python 文件的正则兜底大纲。"""
    lines = [f"# {path}  [{detect_language(path)}]（非 Python，使用正则粗略解析）"]
    for idx, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if re.match(r"^(export\s+)?(public|private|protected|static|final|async)?\s*"
                    r"(class|interface|struct|enum|def|func|function|fn)\s+\w+", stripped):
            lines.append(f"  L{idx}: {stripped[:110]}")
    return "\n".join(lines) if len(lines) > 1 else f"# {path}：未能解析出结构（可能是数据文件或空文件）"


def _count_comment_lines(source: str, raw: list[str]) -> int:
    """统计注释行：# 注释 + 文档字符串（中文项目主要靠 docstring）。"""
    count = 0
    try:
        import io
        import tokenize

        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                count += 1
    except Exception:  # noqa: BLE001 - tokenize 失败时退回简单统计
        count = sum(1 for line in raw if line.strip().startswith("#"))

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return count
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                count += doc.count("\n") + 1
    return count


def _cyclomatic(node: ast.AST) -> int:
    """圈复杂度近似值：决策点 + 1。"""
    score = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert)):
            score += 1
        elif isinstance(child, ast.BoolOp) and len(child.values) > 1:
            score += len(child.values) - 1
        elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            score += len(child.generators) + sum(len(g.ifs) for g in child.generators)
        elif hasattr(ast, "Match") and isinstance(child, getattr(ast, "Match")):
            score += len(getattr(child, "cases", []))
    return score


def build_metrics(path: Path) -> str:
    """生成代码度量：行数、注释率、函数规模、圈复杂度、坏味道。"""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"无法读取文件：{exc}"

    raw = source.splitlines()
    total = len(raw)
    blank = sum(1 for line in raw if not line.strip())
    comment = _count_comment_lines(source, raw)
    code = max(0, total - blank - comment)

    lines = [
        f"# 度量报告: {path}",
        f"- 总行数 {total}（代码 {code} / 注释 {comment} / 空行 {blank}）",
    ]

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        lines.append(f"- 语法解析失败，无法做结构度量：{exc}")
        return "\n".join(lines)

    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    lines.append(f"- 类 {len(classes)} 个，函数/方法 {len(funcs)} 个")

    if funcs:
        lengths = [(n.end_lineno or n.lineno) - n.lineno + 1 for n in funcs]
        lines.append(f"- 函数平均长度 {sum(lengths) / len(lengths):.1f} 行，最长 {max(lengths)} 行")

        ranked = sorted(funcs, key=lambda n: _cyclomatic(n), reverse=True)[:3]
        lines.append("- 复杂度最高的函数：")
        for node in ranked:
            lines.append(f"    · {node.name}()  圈复杂度≈{_cyclomatic(node)}  行数 L{node.lineno}-L{node.end_lineno}")

    # 坏味道检测
    smells: list[str] = []
    for node in funcs:
        length = (node.end_lineno or node.lineno) - node.lineno + 1
        if length > 60:
            smells.append(f"函数 {node.name}() 过长（{length} 行，L{node.lineno}）")
        param_count = len(node.args.posonlyargs) + len(node.args.args) + len(node.args.kwonlyargs)
        if param_count > 5:
            smells.append(f"函数 {node.name}() 参数过多（{param_count} 个，L{node.lineno}）")
        if _cyclomatic(node) > 10:
            smells.append(f"函数 {node.name}() 圈复杂度过高（≈{_cyclomatic(node)}，L{node.lineno}）")
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                smells.append(f"裸 except（L{node.lineno}）：会吞掉 KeyboardInterrupt / SystemExit")
            elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
                smells.append(f"过宽异常捕获 except Exception（L{node.lineno}）：建议收窄到具体异常类型")
        if isinstance(node, ast.Global):
            smells.append(f"使用 global 语句（L{node.lineno}）：存在隐式副作用")
    if total > 500:
        smells.append(f"单文件 {total} 行：建议按职责拆分模块")

    if smells:
        lines.append("\n## 潜在问题")
        lines.extend(f"- {s}" for s in smells[:10])
    else:
        lines.append("\n## 潜在问题\n- 未发现明显坏味道")

    todos = [f"L{i}: {l.strip()[:80]}" for i, l in enumerate(raw, 1) if re.search(r"#\s*(TODO|FIXME|HACK|XXX)", l)]
    if todos:
        lines.append("\n## 待办标记")
        lines.extend(f"- {t}" for t in todos[:8])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 封装
# ---------------------------------------------------------------------------


class CodeOutlineTool(Tool):
    """提取代码结构大纲。"""

    name = "code_outline"
    description = (
        "提取文件的结构大纲：导入、模块级变量、类、函数签名、装饰器与行号区间。"
        "解释代码时应先用本工具建立整体结构认知，再决定是否精读某个函数。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要分析的文件路径"}
        },
        "required": ["path"],
    }

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(self, path: str, **_: Any) -> ToolResult:
        try:
            target = resolve_path(path, self.workspace)
        except PathError as exc:
            return ToolResult(tool=self.name, ok=False, output="", error=str(exc))
        if target.is_dir():
            return ToolResult(tool=self.name, ok=False, output="", error=f"{target} 是目录，请给出文件路径。")
        return ToolResult(tool=self.name, ok=True, output=build_outline(target), meta={"path": str(target)})


class CodeMetricsTool(Tool):
    """代码度量与坏味道检测。"""

    name = "code_metrics"
    description = (
        "统计文件行数/注释率/函数规模/圈复杂度，并检测过长函数、参数过多、裸 except 等坏味道。"
        "适合在给出改进建议前调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要分析的文件路径"}
        },
        "required": ["path"],
    }

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def run(self, path: str, **_: Any) -> ToolResult:
        try:
            target = resolve_path(path, self.workspace)
        except PathError as exc:
            return ToolResult(tool=self.name, ok=False, output="", error=str(exc))
        if target.is_dir():
            return ToolResult(tool=self.name, ok=False, output="", error=f"{target} 是目录，请给出文件路径。")
        return ToolResult(tool=self.name, ok=True, output=build_metrics(target), meta={"path": str(target)})
