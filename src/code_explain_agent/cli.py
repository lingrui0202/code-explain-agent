"""命令行入口：交互式 REPL + 一次性问答。

依赖 rich 做终端渲染；若未安装 rich，自动降级为纯文本输出（不会因此崩溃）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

from .agent.core import CodeExplainAgent, Step
from .config import ConfigError, Settings

BANNER = r"""
   ___          _             _____ _                        _
  / __|___ __ _| |___ _ _    | ____|_ ___ __ __ _ _ __  ___ / |
 | (_ / _ \ _` | / -_) ' \   |  _|/ _ \ V  V / _` | '_ \/ -_) |
  \___\___/\__,_|_\___|_||_| |_____\___/\_/\_/\__,_| .__/\___|_|
                                                   |_|
                    代码解释 Agent  ·  v1.0
"""

HELP = """\
可用命令：
  /help              显示本帮助
  /tools             列出当前可用工具及其签名
  /config            显示当前运行配置（密钥已脱敏）
  /clear             清空对话记忆，重新开始
  /exit  /quit       退出

直接输入问题即可，例如：
  解释 examples/sample_code.py 的作用
  sample_code.py 里 calculate_discount 函数为什么返回值可能是 None？
  帮我给 examples/sample_code.py 加上中文注释

提示：在问题中带上具体文件名，Agent 的取证会更精准。
"""


# ---------------------------------------------------------------------------
# 渲染层
# ---------------------------------------------------------------------------
class Printer:
    """根据 rich 是否可用来选择渲染方式。"""

    def __init__(self, plain: bool = False) -> None:
        self.plain = plain
        self.console = None
        if not plain:
            try:
                from rich.console import Console  # noqa: PLC0415

                self.console = Console()
            except Exception:  # pragma: no cover
                self.console = None

    def rule(self, title: str) -> None:
        if self.console:
            self.console.rule(title, style="cyan")
        else:
            print(f"\n===== {title} =====")

    def text(self, content: str) -> None:
        print(content)

    def markdown(self, content: str) -> None:
        if self.console:
            try:
                from rich.markdown import Markdown  # noqa: PLC0415

                self.console.print(Markdown(content))
                return
            except Exception:  # pragma: no cover
                pass
        print(content)

    def step(self, step: Step, verbose: bool) -> None:
        if not verbose:
            return
        if step.kind == "tool_call":
            mark = "→"
            line = f"{mark} 调用工具 [b]{step.tool}[/b] {step.text}" if self.console else f"{mark} 调用工具 {step.tool}: {step.text}"
        elif step.kind == "tool_result":
            mark = "✔" if step.ok else "✘"
            preview = step.text.replace("\n", " ")[:100]
            line = f"{mark} {step.tool} 返回：{preview}..." if not self.console else f"{mark} {step.tool} 返回 {preview}..."
        elif step.kind == "error":
            line = f"! {step.text}"
        else:
            return
        if self.console:
            self.console.print(f"[dim]{line}[/dim]")
        else:
            print(line)

    def info(self, message: str) -> None:
        if self.console:
            self.console.print(f"[yellow]{message}[/yellow]")
        else:
            print(message)


# ---------------------------------------------------------------------------
# 运行逻辑
# ---------------------------------------------------------------------------
def _build_agent(settings: Settings, verbose: bool, printer: Printer) -> CodeExplainAgent:
    def on_event(step: Step) -> None:
        printer.step(step, verbose)

    return CodeExplainAgent(settings, on_event=on_event)


def run_once(question: str, settings: Settings, *, verbose: bool = True, plain: bool = False) -> int:
    """一次性问答：问完即退出，适合脚本化与演示录屏。"""
    printer = Printer(plain)
    agent = _build_agent(settings, verbose, printer)
    printer.rule("Agent 运行")
    result = agent.run(question)
    printer.rule("解释结果")
    printer.markdown(result.answer)
    if result.usage:
        printer.info(f"\nToken 用量：{result.usage}  推理轮数：{result.rounds}")
    return 0 if result.ok else 1


def run_repl(settings: Settings, *, verbose: bool = True, plain: bool = False) -> int:
    """交互式会话：支持多轮追问，记忆上下文。"""
    printer = Printer(plain)
    printer.text(BANNER)
    info = settings.masked()
    printer.text(
        f"模型：{info['model']}  |  服务商：{settings.provider_label}  |  "
        f"工具：{len(build_tool_names(settings))} 个  |  根目录：{settings.workspace}"
    )
    printer.text("输入 /help 查看命令，/exit 退出。\n")

    agent = _build_agent(settings, verbose, printer)

    while True:
        try:
            question = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            printer.text("\n再见！")
            return 0

        if not question:
            continue
        if question in {"/exit", "/quit", "exit", "quit"}:
            printer.text("再见！")
            return 0
        if question == "/help":
            printer.text(HELP)
            continue
        if question == "/tools":
            printer.text("\n".join(f"  · {sig}" for sig in agent.tool_signatures()))
            continue
        if question == "/config":
            for key, value in settings.masked().items():
                printer.text(f"  {key:>12}: {value}")
            continue
        if question == "/clear":
            agent.reset()
            printer.info("已清空对话记忆。")
            continue

        printer.rule("Agent 运行")
        result = agent.run(question)
        printer.rule("解释结果")
        printer.markdown(result.answer)
        if result.usage:
            printer.info(f"Token 用量：{result.usage}  推理轮数：{result.rounds}")
        printer.text("")


def build_tool_names(settings: Settings) -> list[str]:
    from .tools.registry import build_registry  # 局部导入避免循环

    return build_registry(settings).names()


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-explain-agent",
        description="代码解释 Agent：读取真实代码 → 推理 → 调用工具取证 → 结构化解释",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python main.py                                  进入交互式会话\n"
            "  python main.py -q \"解释 examples/sample_code.py\"  一次性问答\n"
            "  python main.py --offline -q \"解释 sample_code.py\"  无需 API Key 的离线演示\n"
            "  python main.py --web                           启动 Web 界面\n"
        ),
    )
    parser.add_argument("-q", "--question", help="一次性提问，回答后退出")
    parser.add_argument("-f", "--file", help="聚焦某个文件：自动在问题前附上该文件路径")
    parser.add_argument("--provider", help="服务商：deepseek/qwen/openai/moonshot/ollama/offline")
    parser.add_argument("--model", help="模型名，如 deepseek-chat")
    parser.add_argument("--base-url", dest="base_url", help="自定义 OpenAI 兼容端点")
    parser.add_argument("--api-key", dest="api_key", help="API Key（也可写入 .env）")
    parser.add_argument("--workspace", help="项目根目录，工具访问的基准路径")
    parser.add_argument("--temperature", type=float, help="采样温度，默认 0.2")
    parser.add_argument("--max-rounds", type=int, help="最大推理轮数，默认 8")
    parser.add_argument("--allow-exec", action="store_true", help="启用 run_python 工具（默认关闭）")
    parser.add_argument("--no-native-tools", action="store_true", help="禁用原生 function calling，改用 ReAct 文本协议")
    parser.add_argument("--offline", action="store_true", help="离线静态分析模式，无需 API Key")
    parser.add_argument("--quiet", action="store_true", help="不打印中间的工具调用过程")
    parser.add_argument("--plain", action="store_true", help="纯文本输出，不使用 rich 渲染")
    parser.add_argument("--web", action="store_true", help="启动 Streamlit Web 界面")
    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    overrides = {
        "provider": ("offline" if args.offline else args.provider),
        "api_key": args.api_key,
        "base_url": args.base_url,
        "model": args.model,
        "workspace": args.workspace,
        "temperature": args.temperature,
        "max_rounds": args.max_rounds,
        "allow_exec": args.allow_exec or None,
        "native_tools": (False if args.no_native_tools else None),
    }
    return Settings.load(**overrides)


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.web:
        return launch_web(args)

    try:
        settings = settings_from_args(args)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    verbose = not args.quiet
    question = args.question
    if args.file:
        prefix = f"请解释文件 {args.file}"
        question = f"{prefix}。{question}" if question else prefix

    if question:
        return run_once(question, settings, verbose=verbose, plain=args.plain)
    return run_repl(settings, verbose=verbose, plain=args.plain)


def launch_web(args: argparse.Namespace) -> int:
    """拉起 Streamlit。"""
    import subprocess  # noqa: PLC0415

    web_module = Path(__file__).with_name("web.py")
    cmd = [sys.executable, "-m", "streamlit", "run", str(web_module)]
    print(f"启动 Web 界面：{' '.join(cmd)}")
    return subprocess.run(cmd).returncode
