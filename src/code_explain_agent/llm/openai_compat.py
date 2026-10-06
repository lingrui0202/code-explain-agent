"""OpenAI 兼容协议的 LLM 实现。

DeepSeek / 通义千问 / OpenAI / Moonshot / Ollama 都提供 OpenAI 兼容接口，
因此只需一个实现 + 不同的 base_url / model 即可覆盖全部服务商。

关键工程点：
1. 带指数退避 + 抖动的重试（只对可重试错误重试）；
2. 区分可重试 / 不可重试错误，避免鉴权失败时无效重试 3 次；
3. 原生 function calling 失败时不影响上层——上层还有 ReAct 文本协议兜底。
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

from ..config import Settings
from .base import LLMError, LLMProvider, LLMResponse, Message, ToolCall


class OpenAICompatProvider(LLMProvider):
    """通过 openai SDK 访问任何 OpenAI 兼容端点。"""

    name = "openai-compat"

    def __init__(self, settings: Settings):
        try:
            from openai import OpenAI  # 延迟导入：离线模式下不需要本依赖
        except ImportError as exc:  # pragma: no cover
            raise LLMError(
                "缺少 openai 依赖，请先执行：pip install -r requirements.txt",
                retryable=False,
                cause=exc,
            ) from exc

        self.settings = settings
        self.client = OpenAI(
            api_key=settings.api_key or "EMPTY",
            base_url=settings.base_url,
            timeout=settings.timeout,
            max_retries=0,  # 重试由本类自己控制，便于统一日志与退避策略
        )

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [m.to_api() for m in messages],
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_tokens": self.settings.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        last_error: BaseException | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                response = self.client.chat.completions.create(**payload)
                return self._to_response(response)
            except Exception as exc:  # noqa: BLE001 - 统一转换各类 SDK 异常
                last_error = exc
                err = self._classify(exc)
                if not err.retryable or attempt == self.settings.max_retries:
                    raise err from exc
                delay = self.settings.retry_backoff**attempt + random.uniform(0, 0.4)
                time.sleep(delay)

        raise LLMError(f"LLM 调用失败（已重试 {self.settings.max_retries} 次）：{last_error}")

    # ------------------------------------------------------------------
    @staticmethod
    def _classify(exc: BaseException) -> LLMError:
        """把 openai 的异常类型映射为可重试 / 不可重试。"""
        name = type(exc).__name__
        text = str(exc)
        status = getattr(exc, "status_code", None)

        non_retryable = {
            "AuthenticationError",
            "PermissionDeniedError",
            "BadRequestError",
            "NotFoundError",
            "UnprocessableEntityError",
            "ConflictError",
        }
        if name in non_retryable or (isinstance(status, int) and 400 <= status < 500 and status != 429):
            return LLMError(f"LLM 请求被拒绝（{name}）：{text}", retryable=False, cause=exc)
        if name == "RateLimitError" or status == 429:
            return LLMError(f"触发限流：{text}", retryable=True, cause=exc)
        if name in {"APITimeoutError", "APIConnectionError", "InternalServerError"} or (
            isinstance(status, int) and status >= 500
        ):
            return LLMError(f"服务端/网络异常（{name}）：{text}", retryable=True, cause=exc)
        return LLMError(f"LLM 调用异常（{name}）：{text}", retryable=True, cause=exc)

    # ------------------------------------------------------------------
    @staticmethod
    def _to_response(response: Any) -> LLMResponse:
        choice = response.choices[0]
        message = choice.message
        content = message.content or ""

        tool_calls: list[ToolCall] = []
        for raw in getattr(message, "tool_calls", None) or []:
            fn = raw.function
            try:
                arguments = json.loads(fn.arguments or "{}")
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
            except json.JSONDecodeError:
                # 模型偶尔会输出非法 JSON，这里兜底为空参数，让工具层返回可读错误
                arguments = {"__parse_error__": fn.arguments}
            tool_calls.append(ToolCall(id=raw.id, name=fn.name, arguments=arguments))

        usage: dict[str, int] = {}
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = getattr(raw_usage, key, None)
                if isinstance(value, int):
                    usage[key] = value

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=getattr(choice, "finish_reason", "") or "",
            usage=usage,
        )
