# Code Explain Agent —— 设计文档

> 对应课程作业：Homework 1 · Code Agent · 方向：**代码解释 Agent**
> 评审维度：功能完整性 40% / Agent 架构 30% / 代码质量 20% / 文档 10%

---

## 一、需求理解与技术选型

### 1.1 需求拆解

作业的硬性技术要求与本项目的一一对应：

| 作业要求 | 本项目的落地方式 |
|----------|------------------|
| Agent 循环：输入 → 推理 → 工具调用 → 输出 | `agent/core.py` 的 ReAct 循环 |
| 至少一种工具 | 6 个必启工具 + 1 个可选执行工具 |
| 命令行或 Web 界面 | **两者都有**（`cli.py` + `web.py`） |
| 主流框架或 LLM 原生 API | 原生 OpenAI 兼容 API（DeepSeek 等），未引入重型框架 |
| 上下文记忆 | `agent/memory.py` 滑动窗口 + 长结果截断 |
| 错误处理与重试 | Provider 层退避重试 + Agent 层工具错误回喂自纠 |
| 技术栈自由 | Python 3.10+，依赖仅 4 个且全部可选 |

### 1.2 为什么不直接用 LangChain / AutoGen

作业允许"框架或原生 API"二选一。本项目选择**原生 API + 自研薄封装**，理由：

1. **可解释性**：ReAct 循环的每一轮都在自己代码里，架构清晰可讲，而不是黑盒 `agent.run()`；
2. **可控性**：重试策略、上下文压缩、双协议降级都需要精细控制，框架反而增加绕行成本；
3. **依赖极轻**：4 个依赖，离线模式 0 依赖可跑，评审环境任何机器都能复现。

同时保留了框架接入能力：`LLMProvider` 是抽象接口，换成 LangChain 的 `BaseChatModel` 只需新增一个实现类。

### 1.3 语言与模型

- **语言**：Python 3.10+（AST 标准库做静态分析，天然适合代码理解场景）
- **默认模型**：`deepseek-chat`（OpenAI 兼容协议，中文代码解释能力强、成本低）
- **可切换**：`qwen` / `openai` / `moonshot` / `ollama` / 自定义端点 / `offline`

---

## 二、总体架构

```
┌──────────────────────────────────────────────────────────────┐
│                        入口层 (Interface)                     │
│     cli.py（REPL / 一次性问答）      web.py（Streamlit）        │
└───────────────────────┬──────────────────────────────────────┘
                        │  on_event(Step) 事件回调（解耦展示）
┌───────────────────────▼──────────────────────────────────────┐
│                       Agent 层 (Brain)                        │
│  core.py  ReAct 循环        memory.py 上下文记忆               │
│  prompts.py Prompt 工程                                        │
│   输入 → 推理 → [工具调用 → 观察]* → 输出                       │
└──────────┬──────────────────────────────────┬────────────────┘
           │ LLMProvider（抽象）               │ ToolRegistry（抽象）
┌──────────▼───────────────┐      ┌───────────▼────────────────┐
│        LLM 层             │      │         工具层              │
│  base.py     统一抽象     │      │  base.py   Tool 基类/注册表  │
│  openai_compat.py 在线    │      │  paths.py  路径安全         │
│  offline.py  离线兜底     │      │  file_tools / analysis_tools│
│  factory.py  工厂         │      │  exec_tools / registry      │
└──────────────────────────┘      └────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────┐
│             config.py  配置层（环境变量 / .env / CLI 覆盖）      │
└──────────────────────────────────────────────────────────────┘
```

**分层原则**：上层只依赖抽象，不依赖具体实现（依赖倒置）。
因此"换模型"和"加工具"都不需要改动 Agent 循环代码。

---

## 三、Agent 核心：ReAct 循环

### 3.1 循环流程

```
         ┌─────────────────────────────────────────┐
         │                                         │
  用户输入 ──▶ 记忆写入 ──▶ LLM 推理 ──▶ 响应判定 ───┼──▶ 最终答案 ──▶ 输出 + 写回记忆
         │                              │          │
         │                              ▼          │
         │                        需要调用工具？ ────┘
         │                              │ 是
         │                              ▼
         └──────────────── 工具执行 ──▶ 结果写回记忆（作为观察）
```

`agent/core.py::CodeExplainAgent.run()` 伪代码：

```python
for round in 1..max_rounds:
    if round == max_rounds:            # 最后一轮强制收敛
        messages.append(REFLECT_PROMPT); tools = None

    response = provider.chat(memory.render(), tools)

    if response.tool_calls:            # ① 原生 function calling
        memory.add_assistant(...)
        for call in response.tool_calls: execute_and_record(call)
        continue

    if action := parse_text_action(response.content):   # ② ReAct 文本协议
        execute_and_record(action)
        continue

    answer = strip_final(response.content)              # ③ 最终答案
    break
```

### 3.2 双协议降级设计

| 协议 | 触发条件 | 优点 | 风险 |
|------|----------|------|------|
| ① 原生 function calling | 默认启用 | 结构化、无需解析、最可靠 | 依赖模型支持 |
| ② ReAct 文本协议 | 模型未返回 tool_calls，但正文含 JSON Action | 兼容不支持 function calling 的模型 | 需解析，已做健壮处理 |
| ③ 纯文本 | 以上都没有 | 兜底 | —— |

`parse_text_action()` 的实现刻意做得健壮：
- 支持 ```json 代码块与裸 JSON 两种写法；
- 用 `json.JSONDecoder().raw_decode()` 从每个 `{` 起点尝试解析，**天然支持嵌套对象**，比正则更可靠；
- 键名兼容 `tool/name/action` 与 `args/arguments/input/parameters`；
- 解析失败返回 `None`，流程自然落到"最终答案"分支，不会崩溃。

### 3.3 防死循环：三轮保险

1. `max_rounds`（默认 8）硬上限；
2. 最后一轮自动注入 `REFLECT_PROMPT` 并**移除 tools 参数**，物理上断掉工具调用可能，强制模型总结；
3. 即使仍未产出答案，`_fallback_answer()` 会把**已取证的工具结果**汇总输出——用户永远拿到有用信息，绝不空手而归。

---

## 四、Prompt 设计

`agent/prompts.py` 把系统提示词拆成 6 个可独立维护的片段：

| 片段 | 作用 |
|------|------|
| `ROLE` | 角色收敛：只做解释，不主动重构写代码 |
| `WORKFLOW` | **工具优先**原则 + "由粗到细"的取证顺序 + 失败重试指引 |
| 可用工具清单 | 由 `ToolRegistry.describe()` 动态生成，工具变动时提示词自动同步 |
| `OUTPUT_FORMAT` | 强制 Markdown 模板，保证多轮回答风格一致 |
| `CONSTRAINTS` | 禁止编造、精确引用、中文回答、长度克制 |
| `FEW_SHOT` | 2 个"正确做法 vs 错误做法"对照示例 |

**关键设计：把"先取证再回答"写进提示词。**
这是抑制幻觉最有效的一招——模型若还没调用工具就被告知"禁止凭猜测描述代码"，
会先去调 `code_outline`，显著提升解释的真实性。

少样本示例放在**系统提示词内部**而非构造 user/assistant 消息对，是为了避免
伪造的历史消息干扰 function calling 的消息序列约束。

---

## 五、工具设计

### 5.1 抽象

```python
class Tool(ABC):
    name: str
    description: str
    parameters: dict            # JSON Schema
    def run(self, **kwargs) -> ToolResult
    def invoke(self, args) -> ToolResult   # 模板方法：校验 + 异常捕获
```

`invoke()` 是模板方法，统一处理三件事，子类只关心 `run()`：
1. 必填参数缺失 → 返回带 schema 提示的错误；
2. `run()` 抛异常 → 捕获并转成 `ok=False` 的 `ToolResult`（**工具失败绝不让 Agent 崩溃**）；
3. 未知参数记录到 `meta["ignored_args"]`，不静默吞掉。

### 5.2 为什么失败不抛异常

工具报错时，错误文本会通过 `ToolResult.as_tool_message()` 回喂给 LLM，
并附上一句"请修正参数后重试，或换用其他工具"。
这样模型具备**自我纠错**能力：路径写错会自己改，目录当文件会自己换 `list_dir`。
这比直接终止会话更符合 Agent 的设计理念。

### 5.3 确定性工具的价值

`code_outline` / `code_metrics` 基于 AST，**结果不依赖 LLM**：
- 解释永远建立在真实结构之上；
- 离线模式下它们就是答案的来源；
- 圈复杂度、坏味道等指标可复现、可测试（已有单测覆盖）。

### 5.4 安全

`tools/paths.py` 是所有文件访问的唯一入口：
- 相对路径统一基于 workspace 解析；
- `save_report` 校验目标必须落在 workspace 内（防目录穿越写入）；
- `run_python` 默认**不注册**，需显式 `--allow-exec`；启用后在临时目录子进程运行、
  硬超时、清空环境变量、`PYTHONHASHSEED=0`；
- 搜索/遍历自动跳过 `.git`、`node_modules`、`.venv` 等噪声目录。

---

## 六、上下文记忆设计

`ContextMemory` 解决三个真实问题：

| 问题 | 方案 |
|------|------|
| 多轮追问需要历史 | 保留消息序列，`render()` 时系统提示词永远置顶 |
| 大文件读进来撑爆上下文 | 单条工具结果超过 `max_output_chars` 时保留**首 70% + 尾 25%**，中间插入截断说明 |
| 滑窗可能切断"调用—结果"配对 | 淘汰时遇到 `assistant(tool_calls)` 就停止，避免留下孤立的 tool 消息（否则 API 会报 400） |

第三点是容易被忽略的坑：OpenAI 兼容协议要求 `tool` 消息必须紧跟对应的
`assistant(tool_calls)` 消息，截断时必须成对处理。

---

## 七、可靠性设计

| 层级 | 机制 |
|------|------|
| LLM 调用 | 指数退避 + 随机抖动重试（`backoff^attempt + jitter`），默认 3 次 |
| 错误分类 | 鉴权/参数错误（4xx，不含 429）**立即失败**不重试；限流/5xx/网络错误才重试 |
| 工具调用 | 异常捕获 → 结构化错误 → 回喂模型自纠 |
| 循环收敛 | `max_rounds` + 最后一轮强制总结 + 兜底汇总 |
| 上下文 | 滑动窗口 + 长结果截断 + 工具消息配对保护 |
| 配置 | 启动即校验，缺 Key 时给出三种可操作的解决方案 |
| 展示层 | `on_event` 回调异常被吞掉，界面问题不影响 Agent 运行 |

`LLMError.retryable` 字段把"该不该重试"的判断显式化，
避免鉴权失败时傻等 3 次退避——这是 LLM 应用里很常见的体验问题。

---

## 八、可观测性

每次运行都会产出 `Step` 序列（`thought` / `tool_call` / `tool_result` / `answer` / `error`），
通过 `on_event` 回调实时推送给展示层：

- **CLI**：`→ 调用工具 code_outline` / `✔ 返回 N 字符`（`--quiet` 可关闭）
- **Web**：`st.status` 实时展开推理链路，回答下方"查看 Agent 推理链路"可回看

这让 Agent 不再是黑盒——评审时能直观看到"它到底读了哪些文件、调用了什么"。

---

## 九、扩展性

| 想做什么 | 改哪里 |
|----------|--------|
| 加工具 | 继承 `Tool`，在 `tools/registry.py` 注册一行 |
| 换模型/服务商 | 改 `--provider`，或加一条 `PROVIDER_PRESETS` |
| 换 Agent 框架 | 实现 `LLMProvider` 接口的新类 |
| 换交互方式 | 复用 `CodeExplainAgent`，新写一个入口 |
| 改回答风格 | 只改 `agent/prompts.py` |

全程无需触碰 `core.py` 的循环逻辑。

---

## 十、测试

22 项单元测试（标准库 `unittest`，无需额外依赖）：

- 文件工具：正常读取、行号、路径不存在、缺参数、未知工具、非法正则
- 分析工具：结构提取、坏味道检测、docstring 计为注释、目录传入报错
- 协议解析：代码块 JSON、裸 JSON、无动作返回 None
- 记忆：系统提示词置顶、窗口淘汰、长结果截断
- Agent：离线端到端跑通、异常输入不崩溃
- 配置：离线无需 Key、在线缺 Key 报错、密钥脱敏

```bash
python -m unittest discover -s tests -v
```

---

## 十一、与评分标准的对照

| 维度 | 权重 | 本项目的对应实现 |
|------|------|------------------|
| 功能完整性 | 40% | 6 类解释能力；7 个工具；CLI + Web 双入口；离线模式保证任何环境都能跑通；边界情况（路径不存在/目录当文件/参数缺失/正则非法/超轮数）全部有处理与测试 |
| Agent 架构 | 30% | 清晰四层架构；标准 ReAct 循环；双协议降级；依赖倒置（Provider / Tool 双抽象）；记忆、安全、可观测性各成模块；加工具换模型零侵入 |
| 代码质量 | 20% | 类型注解全覆盖；每个模块单一职责；模板方法消除重复；异常不裸抛；配置集中管理；22 项单测 |
| 文档 | 10% | README（快速开始 / 参数表 / 工具表 / 目录结构 / FAQ）+ 本设计文档 + 代码注释与 docstring |

---

## 十二、已知局限与后续改进

1. **流式输出**：目前是"跑完再展示"，下一步可改为 token 级流式，Web 体验更好；
2. **长文件理解**：当前靠 outline + 行区间精读，可引入 AST 分块 + 向量检索做语义定位；
3. **跨文件分析**：现在主要单文件，可基于 import 图做跨文件调用链追踪；
4. **结果缓存**：同一文件重复解释可缓存 outline/metrics，省 token；
5. **多语言深度**：非 Python 仅有正则级大纲，可按语言扩展解析器；
6. **`run_python` 沙箱**：目前是子进程 + 超时，更严格场景可用 Docker 隔离。
