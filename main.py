#!/usr/bin/env python
"""Code Explain Agent —— 统一入口。

    python main.py                      交互式会话
    python main.py -q "解释 xxx.py"      一次性问答
    python main.py --offline -q "..."    离线演示（无需 API Key）
    python main.py --web                 Web 界面
    python main.py --help                查看全部参数
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from code_explain_agent.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
