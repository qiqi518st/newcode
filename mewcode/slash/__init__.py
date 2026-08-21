"""SlashCommand 内置命令框架（ch10）：命令注册 / 解析 / 上下文 / UI 抽象 / 装配。

命令文件只依赖 UIController（F6.3），不 import prompt_toolkit / rich。
"""

from .context import CommandContext
from .parser import parse_command
from .registry import CommandDef, CommandKind, CommandRegistry
from .ui import NopUI, RecordingUI, UIController

__all__ = [
    "CommandContext",
    "CommandDef",
    "CommandKind",
    "CommandRegistry",
    "NopUI",
    "RecordingUI",
    "UIController",
    "parse_command",
]
