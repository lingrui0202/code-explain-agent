"""单元测试（基于标准库 unittest，无需安装 pytest）。

运行：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from code_explain_agent.agent.core import parse_text_action  # noqa: E402
from code_explain_agent.agent.memory import ContextMemory  # noqa: E402
from code_explain_agent.config import Settings  # noqa: E402
from code_explain_agent.llm.base import Message  # noqa: E402
from code_explain_agent.tools.registry import build_registry  # noqa: E402

SAMPLE = ROOT / "examples" / "sample_code.py"


def make_registry():
    return build_registry(Settings.load(provider="offline", workspace=str(ROOT)))


class TestFileTools(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = make_registry()

    def test_read_file_with_line_numbers(self):
        result = self.registry.execute("read_file", {"path": "examples/sample_code.py", "start_line": 1, "end_line": 5})
        self.assertTrue(result.ok, result.error)
        self.assertIn("1 |", result.output)
        self.assertIn("sample_code.py", result.output)

    def test_read_file_missing_path(self):
        result = self.registry.execute("read_file", {"path": "not_exist.py"})
        self.assertFalse(result.ok)
        self.assertIn("路径不存在", result.error)

    def test_read_file_missing_argument(self):
        result = self.registry.execute("read_file", {})
        self.assertFalse(result.ok)
        self.assertIn("缺少必填参数", result.error)

    def test_unknown_tool(self):
        result = self.registry.execute("no_such_tool", {})
        self.assertFalse(result.ok)
        self.assertIn("未知工具", result.error)

    def test_list_dir(self):
        result = self.registry.execute("list_dir", {"path": "examples"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("sample_code.py", result.output)

    def test_search_code(self):
        result = self.registry.execute("search_code", {"pattern": r"def\s+process_order", "path": "examples"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("process_order", result.output)

    def test_search_code_invalid_regex(self):
        result = self.registry.execute("search_code", {"pattern": "([", "path": "examples"})
        self.assertFalse(result.ok)
        self.assertIn("正则表达式非法", result.error)


class TestAnalysisTools(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = make_registry()

    def test_outline_contains_classes_and_functions(self):
        result = self.registry.execute("code_outline", {"path": "examples/sample_code.py"})
        self.assertTrue(result.ok, result.error)
        for name in ("class OrderItem", "class Order", "def calculate_discount", "def process_order"):
            self.assertIn(name, result.output)

    def test_metrics_detects_smells(self):
        result = self.registry.execute("code_metrics", {"path": "examples/sample_code.py"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("process_order", result.output)
        self.assertIn("参数过多", result.output)
        self.assertIn("Exception", result.output)

    def test_metrics_counts_docstring_as_comment(self):
        result = self.registry.execute("code_metrics", {"path": "examples/sample_code.py"})
        self.assertRegex(result.output, r"注释 [1-9]\d*")

    def test_outline_on_directory_returns_error(self):
        result = self.registry.execute("code_outline", {"path": "examples"})
        self.assertFalse(result.ok)
        self.assertIn("是目录", result.error)


class TestActionParsing(unittest.TestCase):
    def test_parse_fenced_json(self):
        text = '我先看看结构。\n```json\n{"tool": "code_outline", "args": {"path": "a.py"}}\n```'
        call = parse_text_action(text)
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "code_outline")
        self.assertEqual(call.arguments, {"path": "a.py"})

    def test_parse_plain_json(self):
        call = parse_text_action('Action: {"tool": "read_file", "arguments": {"path": "a.py", "end_line": 20}}')
        self.assertIsNotNone(call)
        self.assertEqual(call.arguments["end_line"], 20)

    def test_parse_returns_none_without_action(self):
        self.assertIsNone(parse_text_action("这是一个纯文本回答，没有工具调用。"))


class TestMemory(unittest.TestCase):
    def test_system_prompt_always_first(self):
        memory = ContextMemory(system_prompt="SYS", window=2)
        memory.add_user("q1")
        memory.add_assistant("a1")
        memory.add_user("q2")
        rendered = memory.render()
        self.assertEqual(rendered[0].role, "system")
        self.assertEqual(rendered[0].content, "SYS")

    def test_window_eviction(self):
        memory = ContextMemory(system_prompt="SYS", window=2)
        for i in range(6):
            memory.add_user(f"q{i}")
        self.assertLessEqual(len(memory._messages), 2)

    def test_truncate_long_output(self):
        memory = ContextMemory(max_output_chars=100)
        memory.add_tool_result("id", "read_file", "x" * 500)
        self.assertIn("已截断", memory._messages[-1].content)
        self.assertLessEqual(len(memory._messages[-1].content), 400)


class TestOfflineAgent(unittest.TestCase):
    def test_end_to_end_run(self):
        from code_explain_agent.agent.core import CodeExplainAgent

        settings = Settings.load(provider="offline", workspace=str(ROOT))
        agent = CodeExplainAgent(settings)
        result = agent.run(f"解释 {SAMPLE.name} 的作用")

        kinds = [s.kind for s in result.steps]
        self.assertIn("tool_call", kinds)
        self.assertIn("answer", kinds)
        self.assertTrue(result.answer)
        self.assertIn("process_order", result.answer)
        # 三轮取证 + 一轮总结
        self.assertGreaterEqual(result.rounds, 4)

    def test_agent_survives_bad_tool_call(self):
        from code_explain_agent.agent.core import CodeExplainAgent

        settings = Settings.load(provider="offline", workspace=str(ROOT))
        agent = CodeExplainAgent(settings)
        result = agent.run("请解释一个根本不存在的文件 abc_xyz.py")
        self.assertTrue(result.answer)


class TestConfig(unittest.TestCase):
    def test_offline_provider_needs_no_key(self):
        settings = Settings.load(provider="offline", workspace=str(ROOT))
        settings.validate()  # 不应抛异常

    def test_online_provider_requires_key(self):
        from code_explain_agent.config import ConfigError

        with self.assertRaises(ConfigError):
            Settings.load(provider="deepseek", api_key="").validate()

    def test_masked_hides_secret(self):
        settings = Settings.load(provider="deepseek", api_key="sk-1234567890abcdefgh", workspace=str(ROOT))
        self.assertNotIn("1234567890", str(settings.masked()["api_key"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
