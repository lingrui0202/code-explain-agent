"""Streamlit Web 界面：可视化展示 Agent 的推理链路与解释结果。

启动方式（二选一）：
    python main.py --web
    streamlit run src/code_explain_agent/web.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许直接 `streamlit run src/code_explain_agent/web.py` 启动
_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import streamlit as st  # noqa: E402

from code_explain_agent.agent.core import CodeExplainAgent, Step  # noqa: E402
from code_explain_agent.config import PROVIDER_PRESETS, ConfigError, Settings  # noqa: E402

PAGE_CSS = """
<style>
    .block-container { padding-top: 2rem; max-width: 1080px; }
    .cea-badge {
        display:inline-block; padding:2px 10px; border-radius:999px;
        font-size:12px; background:#EEF2FF; color:#4338CA; margin-right:6px;
    }
    .cea-step { font-size: 13px; color:#4B5563; padding:2px 0; }
    .cea-hero {
        padding: 18px 22px; border-radius: 14px; margin-bottom: 18px;
        background: linear-gradient(120deg, #1E2761 0%, #2F3C7E 60%, #028090 100%);
        color: #FFFFFF;
    }
    .cea-hero h1 { margin:0; font-size: 26px; font-weight: 700; color:#FFFFFF; }
    .cea-hero p { margin: 6px 0 0; font-size: 14px; color: #D7DEFF; }
</style>
"""


def render_sidebar() -> dict:
    """渲染侧边栏配置，返回配置字典。"""
    st.header("⚙️ 运行配置")

    provider = st.selectbox(
        "服务商",
        options=list(PROVIDER_PRESETS.keys()),
        index=0,
        format_func=lambda k: f"{PROVIDER_PRESETS[k]['label']} ({k})",
        help="所有在线服务商都通过 OpenAI 兼容协议接入",
    )
    preset = PROVIDER_PRESETS[provider]

    api_key = ""
    if provider not in {"offline", "ollama"}:
        api_key = st.text_input(
            "API Key",
            type="password",
            placeholder=preset.get("env_key", "API_KEY"),
            help="也可以写入项目根目录的 .env 文件，避免每次输入",
        )

    col1, col2 = st.columns(2)
    with col1:
        model = st.text_input("模型", value=preset["model"])
    with col2:
        temperature = st.slider("温度", 0.0, 1.0, 0.2, 0.05)

    base_url = st.text_input("Base URL", value=preset["base_url"] or "")
    workspace = st.text_input("项目根目录", value=str(Path.cwd()))
    max_rounds = st.slider("最大推理轮数", 2, 12, 8)

    st.markdown("---")
    st.checkbox("显示推理链路", value=True, key="show_steps")
    st.checkbox("启用代码执行工具", value=False, key="allow_exec",
                help="允许 Agent 用 run_python 验证行为，默认关闭")

    return {
        "provider": provider,
        "api_key": api_key or None,
        "model": model or None,
        "base_url": base_url or None,
        "workspace": workspace or None,
        "temperature": temperature,
        "max_rounds": max_rounds,
        "allow_exec": st.session_state.get("allow_exec", False) or None,
    }


def make_agent(cfg: dict) -> CodeExplainAgent:
    settings = Settings.load(**cfg)
    return CodeExplainAgent(settings)


def step_line(step: Step) -> str:
    if step.kind == "tool_call":
        return f"🔧 **调用工具** `{step.tool}` — `{step.text}`"
    if step.kind == "tool_result":
        mark = "✅" if step.ok else "❌"
        return f"{mark} `{step.tool}` 返回 {len(step.text)} 字符"
    if step.kind == "error":
        return f"⚠️ {step.text}"
    return ""


def main() -> None:
    st.set_page_config(page_title="Code Explain Agent", page_icon="🧠", layout="wide")
    st.markdown(PAGE_CSS, unsafe_allow_html=True)

    st.markdown(
        """
        <div class="cea-hero">
            <h1>🧠 Code Explain Agent</h1>
            <p>读取真实代码 → 推理 → 调用工具取证 → 结构化解释。支持 DeepSeek / 通义千问 / OpenAI / Ollama / 离线模式。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        cfg = render_sidebar()
        if st.button("🗑 清空会话", use_container_width=True):
            st.session_state.pop("agent", None)
            st.session_state.pop("history", None)
            st.rerun()

    cfg_key = repr(sorted((k, v) for k, v in cfg.items()))
    if st.session_state.get("cfg_key") != cfg_key:
        st.session_state["cfg_key"] = cfg_key
        try:
            st.session_state["agent"] = make_agent(cfg)
            st.session_state["history"] = []
            st.session_state["cfg_error"] = None
        except ConfigError as exc:
            st.session_state["agent"] = None
            st.session_state["cfg_error"] = str(exc)

    agent: CodeExplainAgent | None = st.session_state.get("agent")
    history: list[dict] = st.session_state.get("history", [])

    if agent is None:
        st.warning("当前配置不可用，请在左侧补全配置。\n\n" + str(st.session_state.get("cfg_error") or ""))
        if st.button("改用离线模式（无需 Key）"):
            st.session_state["cfg_key"] = None
            st.session_state["agent"] = make_agent({**cfg, "provider": "offline", "api_key": None})
            st.session_state["history"] = []
            st.rerun()
        return

    badges = "".join(
        f"<span class='cea-badge'>{label}</span>"
        for label in [
            f"模型 {agent.settings.model}",
            f"服务商 {agent.settings.provider_label}",
            f"工具 {len(agent.tools)} 个",
        ]
    )
    st.markdown(badges, unsafe_allow_html=True)
    st.caption(f"项目根目录：`{agent.settings.workspace}`")
    st.markdown("---")

    # 历史消息
    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("steps"):
                with st.expander("🔍 查看 Agent 推理链路", expanded=False):
                    for line in msg["steps"]:
                        st.markdown(f"<div class='cea-step'>{line}</div>", unsafe_allow_html=True)

    # 快捷提问
    st.markdown("")
    quick = st.columns(3)
    quick_prompts = [
        "解释 examples/sample_code.py 这个文件的作用",
        "sample_code.py 里有哪些函数？各自负责什么？",
        "给 examples/sample_code.py 的关键函数补充中文注释",
    ]
    for col, prompt in zip(quick, quick_prompts):
        if col.button(prompt, use_container_width=True):
            st.session_state["pending"] = prompt

    user_input = st.chat_input("输入你的代码问题，例如：解释 examples/sample_code.py 的作用")
    pending = st.session_state.pop("pending", None)
    user_input = pending or user_input

    if not user_input:
        return

    with st.chat_message("user"):
        st.markdown(user_input)
    history.append({"role": "user", "content": user_input})

    steps: list[str] = []
    with st.chat_message("assistant"):
        show_steps = st.session_state.get("show_steps", True)
        status_box = st.status("🤔 Agent 推理中…", expanded=show_steps) if show_steps else None

        def on_event(step: Step) -> None:
            line = step_line(step)
            if line:
                steps.append(line)
                if status_box is not None:
                    with status_box:
                        st.markdown(f"<div class='cea-step'>{line}</div>", unsafe_allow_html=True)

        agent.on_event = on_event
        result = agent.run(user_input)

        if status_box is not None:
            status_box.update(label=f"✅ 完成（{result.rounds} 轮推理）", state="complete")

        st.markdown(result.answer)
        if result.usage:
            st.caption(f"Token 用量：{result.usage}")

    history.append({"role": "assistant", "content": result.answer, "steps": steps})
    st.session_state["history"] = history


if __name__ == "__main__":
    main()
