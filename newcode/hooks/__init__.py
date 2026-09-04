"""ch12 Hook 生命周期挂钩系统：三层 YAML 加载 + 事件分派引擎 + 四类动作执行器。

对外主入口：`load(project_root) -> Engine`（loader），Engine.dispatch 由
agent/tui/main 在 18 个事件节点调用。
"""

from .engine import Engine
from .loader import load
from .types import DispatchResult, Event, Hook

__all__ = ["DispatchResult", "Engine", "Event", "Hook", "load"]
