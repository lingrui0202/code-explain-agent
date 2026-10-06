"""全局配置：从环境变量 / .env / 命令行覆盖中加载，并做合法性校验。"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 内置服务商预设（统一走 OpenAI 兼容协议，切换服务商只需改 provider）
# ---------------------------------------------------------------------------
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
        "label": "DeepSeek",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "env_key": "DASHSCOPE_API_KEY",
        "label": "通义千问",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
        "label": "OpenAI",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "env_key": "MOONSHOT_API_KEY",
        "label": "Moonshot",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5-coder:7b",
        "env_key": "",
        "label": "Ollama 本地模型",
    },
    "offline": {
        "base_url": "",
        "model": "offline-static-analysis",
        "env_key": "",
        "label": "离线静态分析兜底",
    },
}


class ConfigError(ValueError):
    """配置不合法时抛出。"""


def _load_dotenv() -> None:
    """尽量加载 .env；未安装 python-dotenv 时静默跳过。"""
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:  # pragma: no cover - dotenv 是可选依赖
        pass


@dataclass
class Settings:
    """Agent 运行所需的所有可调参数。"""

    # --- LLM ---
    provider: str = "deepseek"
    api_key: str = ""
    api_key_explicit: bool = False  # True=调用方显式给出了 api_key（哪怕是空串），此时不再回退到环境变量
    base_url: str = ""
    model: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout: float = 60.0

    # --- 可靠性 ---
    max_retries: int = 3          # LLM 调用失败重试次数
    retry_backoff: float = 1.8    # 指数退避基数（秒）
    tool_timeout: float = 15.0    # 单个工具执行超时（秒）

    # --- Agent 循环 ---
    max_rounds: int = 8           # 推理-工具调用的最大轮数，防止死循环
    max_tool_output_chars: int = 6000   # 单条工具返回结果截断长度，防上下文爆炸
    history_window: int = 12      # 上下文记忆窗口（保留最近 N 条消息）
    native_tools: bool = True     # True=使用原生 function calling；False=纯 ReAct 文本协议

    # --- 工具 ---
    workspace: str = ""           # 工具可访问的根目录，默认当前工作目录
    allow_exec: bool = False      # 是否允许执行 Python 代码（默认关闭，安全优先）

    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        self.provider = (self.provider or "deepseek").strip().lower()
        if self.provider in PROVIDER_PRESETS:
            preset = PROVIDER_PRESETS[self.provider]
            self.base_url = self.base_url or preset["base_url"]
            self.model = self.model or preset["model"]
            if not self.api_key and not self.api_key_explicit and preset["env_key"]:
                self.api_key = os.getenv(preset["env_key"], "")
        if not self.workspace:
            self.workspace = os.getcwd()
        self.workspace = str(Path(self.workspace).resolve())

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, **overrides: Any) -> "Settings":
        """环境变量 < 显式覆盖。None 值表示"未指定"，会被忽略。"""
        _load_dotenv()
        env_map = {
            "provider": os.getenv("PROVIDER"),
            "api_key": os.getenv("API_KEY"),
            "base_url": os.getenv("BASE_URL"),
            "model": os.getenv("MODEL"),
            "temperature": os.getenv("TEMPERATURE"),
            "max_tokens": os.getenv("MAX_TOKENS"),
            "workspace": os.getenv("WORKSPACE"),
        }
        data: dict[str, Any] = {k: v for k, v in env_map.items() if v not in (None, "")}

        for key, value in overrides.items():
            if value is None:
                continue
            if key == "api_key":
                # 空串是"显式清空"的语义，不能被当作未指定而回退到环境变量
                data["api_key"] = value
                data["api_key_explicit"] = True
                continue
            if isinstance(value, str) and value == "":
                continue
            data[key] = value

        # 数值型字段需要转换
        for numeric in ("temperature", "max_tokens"):
            if numeric in data and isinstance(data[numeric], str):
                try:
                    data[numeric] = float(data[numeric]) if numeric == "temperature" else int(data[numeric])
                except ValueError as exc:  # pragma: no cover - 防御性
                    raise ConfigError(f"环境变量 {numeric.upper()} 不是合法数字：{data[numeric]}") from exc

        unknown = set(data) - set(cls().__dict__)
        if unknown:
            data["extra"] = {**cls().extra, **{k: data.pop(k) for k in list(unknown)}}

        settings = cls(**data)
        settings.validate()
        return settings

    # ------------------------------------------------------------------
    def validate(self) -> None:
        if self.provider == "offline":
            return
        if self.provider == "ollama":
            return
        if not self.api_key:
            preset = PROVIDER_PRESETS.get(self.provider, {})
            hint = preset.get("env_key") or "API_KEY"
            raise ConfigError(
                f"未检测到 provider='{self.provider}' 的 API Key。\n"
                f"  方式一：复制 .env.example 为 .env 并填写 {hint}\n"
                f"  方式二：终端设置环境变量后重试\n"
                f"  方式三：加 --offline 使用离线静态分析模式（无需 Key 即可完整演示 Agent 循环）"
            )
        if not self.base_url:
            raise ConfigError("缺少 base_url，请通过 --base-url 或 BASE_URL 指定。")

    @property
    def provider_label(self) -> str:
        return PROVIDER_PRESETS.get(self.provider, {}).get("label", self.provider)

    def masked(self) -> dict[str, Any]:
        """用于日志/界面展示的配置快照（隐藏密钥）。"""
        data = asdict(self)
        if data.get("api_key"):
            key = str(data["api_key"])
            data["api_key"] = key[:6] + "***" + key[-4:] if len(key) > 12 else "***"
        return data
