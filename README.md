# Code Explain Agent

面向代码理解场景的 LLM Agent。它按 ReAct 循环工作：先用工具读取真实代码，再结合模型推理给出结构化解释，因此结论始终以实际代码为依据，而不是模型的推测。

- 支持 DeepSeek、通义千问、OpenAI、Moonshot、Ollama 等任意 OpenAI 兼容接口
- 命令行与 Web 两种交互方式
- 无 API Key 时可切换到离线模式，由本地静态分析生成报告

设计说明见 [Design.md](Design.md)。

## 功能

| 能力 | 说明 | 示例提问 |
|------|------|----------|
| 整体逻辑解释 | 说明一个文件或模块解决什么问题、结构如何组织 | `解释 examples/sample_code.py 的作用` |
| 函数级解读 | 讲清关键函数的入参、步骤、边界与异常 | `process_order 为什么有时返回 ok=False？` |
| 生成注释 | 为关键函数补充中文注释，仅在明确要求时输出代码 | `给 sample_code.py 的关键函数补充注释` |
| 代码问答 | 基于真实代码回答“为什么 / 在哪 / 会怎样” | `validate_order 会检查哪些非法情况？` |
| 质量提示 | 指出过长函数、参数过多、过宽异常捕获等问题 | `这段代码有什么问题？` |
| 上下文追问 | 多轮对话，记住前文讨论的对象 | `那它的参数呢？` |

## 安装与运行

### 安装

```bash
pip install -r requirements.txt
```

如果只用命令行，可以不装 Web 相关依赖：

```bash
pip install openai python-dotenv rich
```

### 配置

复制 `.env.example` 为 `.env` 并填入密钥：

```bash
cp .env.example .env
```

```ini
PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxxxxxxx
```

也可以通过环境变量或 `--api-key` 参数传入，并非必须依赖 `.env`。

### 运行

```bash
# 交互式会话
python main.py

# 一次性问答
python main.py -q "解释 examples/sample_code.py 的作用"

# 指定文件后提问
python main.py -f examples/sample_code.py -q "有哪些函数，各自负责什么"

# Web 界面
python main.py --web
```

会话内可用 `/help` `/tools` `/config` `/clear` `/exit`。

## 命令行参数

| 参数 | 说明 |
|------|------|
| `-q, --question` | 一次性提问，回答后退出 |
| `-f, --file` | 指定文件，自动在问题前附上路径 |
| `--provider` | `deepseek` / `qwen` / `openai` / `moonshot` / `ollama` / `offline` |
| `--model` `--base-url` `--api-key` | 覆盖模型、端点与密钥 |
| `--workspace` | 工具访问的根目录，默认为当前目录 |
| `--temperature` | 采样温度，默认 `0.2` |
| `--max-rounds` | 最大推理轮数，默认 `8` |
| `--allow-exec` | 启用 `run_python` 工具，默认关闭 |
| `--no-native-tools` | 改用 ReAct 文本协议，不依赖 function calling |
| `--offline` | 离线静态分析模式，无需 API Key |
| `--quiet` `--plain` | 隐藏推理过程 / 纯文本输出 |
| `--web` | 启动 Streamlit Web 界面 |

## 离线模式

未配置 API Key，或代码不便外传时，可使用离线模式。此时由规则规划器代替模型做决策，工具调用流程不变，最终报告基于 AST 静态分析生成：

```bash
python main.py --offline -q "解释 examples/sample_code.py 的作用"
```

流程为 `code_outline → read_file → code_metrics → 汇总输出`。

离线模式只能分析本地文件，无法回答开放式问题，解释的详细程度也不及在线模式。

## 工具

| 工具 | 作用 | 主要参数 |
|------|------|----------|
| `read_file` | 读取文件内容，带行号，支持按行区间精读 | `path` `start_line` `end_line` |
| `code_outline` | AST 提取结构：导入、模块变量、类、函数签名、装饰器、行号 | `path` |
| `code_metrics` | 行数 / 注释率 / 函数规模 / 圈复杂度，并检测坏味道 | `path` |
| `search_code` | 正则搜索，定位符号的定义与引用 | `pattern` `path` `file_glob` |
| `list_dir` | 列出目录，用于定位目标文件 | `path` `recursive` |
| `save_report` | 将解释结果写入 Markdown 文件 | `path` `content` |
| `run_python` | 在临时目录隔离执行代码片段，默认不启用 | `code` `timeout` |

扩展工具只需继承 `Tool` 并在 `src/code_explain_agent/tools/registry.py` 中注册，Agent 循环无需改动。

## 项目结构

```
code-explain-agent/
├── main.py                     统一入口（CLI / Web）
├── requirements.txt
├── .env.example                配置模板
├── README.md
├── Design.md                   设计文档
├── examples/
│   └── sample_code.py          示例代码
├── scripts/
│   ├── demo.py                 演示脚本
│   └── package.py              打包脚本
├── tests/
│   └── test_agent.py           单元测试
└── src/code_explain_agent/
    ├── config.py               配置加载与校验
    ├── cli.py                  命令行交互
    ├── web.py                  Streamlit Web 界面
    ├── agent/
    │   ├── core.py             ReAct 循环
    │   ├── memory.py           上下文记忆与压缩
    │   └── prompts.py          Prompt 模板
    ├── llm/
    │   ├── base.py             统一抽象（Message / ToolCall / LLMResponse）
    │   ├── openai_compat.py    OpenAI 兼容协议接入与退避重试
    │   ├── offline.py          离线静态分析实现
    │   └── factory.py          Provider 工厂
    └── tools/
        ├── base.py             Tool 基类与注册表
        ├── paths.py            路径解析与越权防护
        ├── file_tools.py       文件读取 / 列目录 / 搜索
        ├── analysis_tools.py   AST 结构解析与度量
        ├── exec_tools.py       代码执行与报告保存
        └── registry.py         工具装配
```

## 测试

```bash
python -m unittest discover -s tests -v
```

覆盖文件工具的错误分支、AST 解析与坏味道检测、ReAct 文本协议解析、记忆窗口与截断、离线模式端到端流程、配置校验与密钥脱敏。

## 常见问题

**提示“未检测到 API Key”？**

检查 `.env` 是否填写，或改用 `--api-key sk-xxx` 传入；也可以加 `--offline` 使用离线模式。

**想换用通义千问？**

`python main.py --provider qwen`，并在 `.env` 中填写 `DASHSCOPE_API_KEY`。

**想接本地 Ollama？**

先 `ollama run qwen2.5-coder:7b`，再 `python main.py --provider ollama --model qwen2.5-coder:7b`。

**模型不支持 function calling？**

加 `--no-native-tools`，Agent 会改用 ReAct 文本协议解析工具调用。

**解释结果不够准确或太简略？**

在问题中写明具体文件名和函数名；也可调低 `--temperature`、调大 `--max-rounds`。
