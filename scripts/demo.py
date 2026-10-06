"""演示脚本：连续跑 3 个问题，用于录制作业演示视频（1 分钟内）。

    python scripts/demo.py                 # 离线模式，无需 API Key
    python scripts/demo.py --online        # 在线模式，需要配置 Key

离线模式也会完整展示「推理 → 工具调用 → 结果整合 → 输出」链路，
因此即使没有 Key 也能录出完整的演示视频。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_explain_agent.agent.core import CodeExplainAgent, Step  # noqa: E402
from code_explain_agent.cli import Printer  # noqa: E402
from code_explain_agent.config import Settings  # noqa: E402

QUESTIONS = [
    "解释 examples/sample_code.py 这个文件的作用",
    "process_order 这个函数为什么有时会返回 ok=False？",
    "这段代码有哪些可以改进的地方？",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Code Explain Agent 演示脚本")
    parser.add_argument("--online", action="store_true", help="使用在线模型（默认离线）")
    parser.add_argument("--plain", action="store_true", help="纯文本输出")
    args = parser.parse_args()

    printer = Printer(args.plain)
    settings = Settings.load(
        provider=None if args.online else "offline",
        workspace=str(ROOT),
    )
    mode = "在线" if args.online else "离线"
    print(f"\n{'=' * 60}\nCode Explain Agent 演示（{mode}模式）\n{'=' * 60}\n")

    def on_event(step: Step) -> None:
        printer.step(step, verbose=True)

    agent = CodeExplainAgent(settings, on_event=on_event)
    for index, question in enumerate(QUESTIONS, 1):
        printer.rule(f"演示 {index}/{len(QUESTIONS)}：{question}")
        result = agent.run(question)
        printer.markdown(result.answer)
        if result.usage:
            printer.info(f"\nToken 用量：{result.usage}  推理轮数：{result.rounds}")
        print()

    print("演示结束。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
