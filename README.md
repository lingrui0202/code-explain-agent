# Code Explain Agent

一个面向**代码解释**场景的 LLM Agent：读取真实代码 → 推理 → 调用工具取证 → 输出结构化解释。
支持 **DeepSeek / 通义千问 / OpenAI / Moonshot / Ollama** 任意 OpenAI 兼容服务商，
并提供 **CLI 与 Web 双入口**，以及 **无 API Key 也能完整运行**的离线兜底模式。

> 课程作业：Homework 1 — Code Agent · 方向：**代码解释 Agent**

---

## 一、它能做什么

| 能力 | 说明 | 示例提问 |
|------|------|----------|
| 整体逻辑解释 | 讲清一个文件/模块解决什么问题、结构如何组织 | `解释 examples/sample_code.py 的作用` |
| 函数级解读 | 逐个讲清关键函数的入参、步骤、边界与异常 | `process_order 为什么有时返回 ok=False？` |
| 生成注释 | 为关键函数补充中文注释（只在要求时才输出代码） | `给 sample_code.py 的关键函数补充中文注释` |
| 代码问答 | 基于真实代码回答"为什么/在哪/会怎样" | `validate_order 会检查哪些非法情况？` |
| 质量提示 | 顺带指出过长函数、参数过多、过宽异常捕获等坏味道 | `这段代码有什么问题？` |
| 上下文追问 | 多轮对话，记住前文的讨论对象 | `那它的参数呢？` |

**核心原则：先取证，再回答。** 任何结论都来自工具返回的真实代码，从机制上抑制幻觉。

---

## 二、快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> 只需要 CLI 的话：`pip install openai python-dotenv rich`
> 完全没有网络 / 没有 Key：跳过安装，直接用离线模式（见 §4）。

### 2. 配置 API Key（以 DeepSeek 为例）

```bash
cp .env.example .env      # 然后编辑 .env，填入 DEEPSEEK_API_KEY=sk-xxx
```

也可以用环境变量，或命令行 `--api-key sk-xxx` 传入。

### 3. 运行

```bash
# 交互式会话（推荐，支持多轮追问）
python main.py

# 一次性问答
python main.py -q "解释 examples/sample_code.py 的作用"

# 聚焦某个文件
python main.py -f examples/sample_code.py -q "有哪些函数，各自负责什么"

# Web 界面
python main.py --web
```

CLI 会话内的命令：`/help` `/tools` `/config` `/clear` `/exit`。

---

## 三、CLI 参数一览

| 参数 | 说明 |
|------|------|
| `-q, --question` | 一次性提问，回答后退出 |
| `-f, --file` | 聚焦文件，自动在问题前附上路径 |
| `--provider` | `deepseek` / `qwen` / `openai` / `moonshot` / `ollama` / `offline` |
| `--model` `--base-url` `--api-key` | 覆盖模型与端点 |
| `--workspace` | 工具访问的根目录，默认当前目录 |
| `--temperature` | 采样温度，默认 `0.2` |
| `--max-rounds` | 最大推理轮数，默认 `8` |
| `--allow-exec` | 启用 `run_python` 工具（默认关闭，安全优先） |
| `--no-native-tools` | 改用 ReAct 文本协议，不依赖 function calling |
| `--offline` | 离线静态分析模式，无需 API Key |
| `--quiet` `--plain` | 不显示推理过程 / 纯文本输出 |
| `--web` | 启动 Streamlit Web 界面 |

---

## 四、离线模式（演示 / 无 Key 场景）

```bash
python main.py --offline -q "解释 examples/sample_code.py 的作用"
```

离线模式下，决策者由大模型换成**规则规划器**，但 Agent 链路完全一致：
`code_outline → read_file → code_metrics → 汇总输出`，
输出的是基于 AST 的真实静态分析报告。
**断网、无 Key 也能完整演示"推理 → 工具调用 → 结果整合 → 输出"全过程。**

---

## 五、工具清单

| 工具 | 作用 | 关键参数 |
|------|------|----------|
| `read_file` | 读取文件内容，带行号，支持按行区间精读 | `path` `start_line` `end_line` |
| `code_outline` | AST 提取结构：导入、模块变量、类、函数签名、装饰器、行号 | `path` |
| `code_metrics` | 行数/注释率/函数规模/圈复杂度 + 坏味道检测 | `path` |
| `search_code` | 正则搜索代码，定位符号定义与引用 | `pattern` `path` `file_glob` |
| `list_dir` | 列出目录，定位目标文件 | `path` `recursive` |
| `save_report` | 把解释报告写入 Markdown 文件 | `path` `content` |
| `run_python` | 在隔离临时目录执行代码片段（默认关闭） | `code` `timeout` |

新增工具只需继承 `Tool` 并在 `tools/registry.py` 注册，Agent 代码零改动。

---

## 六、目录结构

```
code-explain-agent/
├── main.py                     统一入口（CLI / Web）
├── requirements.txt
├── .env.example                配置模板
├── README.md                   使用说明（本文件）
├── Design.md                   设计文档
├── examples/
│   └── sample_code.py          演示用示例代码
├── scripts/
│   └── package.py              作业提交打包脚本
├── tests/
│   └── test_agent.py           单元测试（unittest，22 项）
└── src/code_explain_agent/
    ├── config.py               配置加载与校验
    ├── cli.py                  命令行 REPL / 一次性问答
    ├── web.py                  Streamlit Web 界面
    ├── agent/
    │   ├── core.py             ReAct 循环（核心）
    │   ├── memory.py           上下文记忆与压缩
    │   └── prompts.py          Prompt 工程
    ├── llm/
    │   ├── base.py             统一抽象（Message / ToolCall / LLMResponse）
    │   ├── openai_compat.py    OpenAI 兼容协议 + 退避重试
    │   ├── offline.py          离线静态分析兜底
    │   └── factory.py          Provider 工厂
    └── tools/
        ├── base.py             Tool 基类 + 注册表
        ├── paths.py            路径安全与越权防护
        ├── file_tools.py       读取 / 列目录 / 搜索
        ├── analysis_tools.py   AST 结构解析 + 度量
        ├── exec_tools.py       安全执行 + 报告保存
        └── registry.py         工具装配
```

---

## 七、测试

```bash
python -m unittest discover -s tests -v
```

覆盖：文件工具（含错误分支）、AST 解析与坏味道检测、ReAct 文本协议解析、
记忆窗口与截断、离线 Agent 端到端、配置校验与密钥脱敏。**22 项全部通过。**

---

## 八、常见问题

**Q：提示"未检测到 API Key"？**
A：三种解法：① 填 `.env`；② `--api-key sk-xxx`；③ 加 `--offline` 用离线模式。

**Q：想换通义千问？**
A：`python main.py --provider qwen`，并在 `.env` 填 `DASHSCOPE_API_KEY`。

**Q：想接本地 Ollama？**
A：先 `ollama run qwen2.5-coder:7b`，再 `python main.py --provider ollama --model qwen2.5-coder:7b`。

**Q：模型不支持 function calling 怎么办？**
A：加 `--no-native-tools`，Agent 会自动切换到 ReAct 文本协议（JSON Action）解析。

**Q：解释结果不准/太简略？**
A：在问题中带上具体文件名和函数名；或把 `--temperature` 调低、`--max-rounds` 调大。

---

## 九、打包提交

```bash
python scripts/package.py --id 10086 --name 张三
```

生成 `10086张三.zip`（自动排除 `.env`、`__pycache__`、`.venv` 等，体积远小于 200M）。
