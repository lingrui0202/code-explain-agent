"""Code Explain Agent：一个面向代码解释场景的 LLM Agent。"""

__version__ = "1.0.0"

from .agent.core import AgentResult, CodeExplainAgent, Step
from .config import Settings
from .llm.factory import build_provider
from .tools.registry import build_registry

__all__ = [
    "__version__",
    "AgentResult",
    "CodeExplainAgent",
    "Settings",
    "Step",
    "build_provider",
    "build_registry",
]
