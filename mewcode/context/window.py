"""Context Window 四级解析（spec F29：env → [1m] 后缀 → 能力表 → 协议默认）。

永不抛：任一级异常落入第 4 级协议默认。
"""

import os

from ..context.capabilities import CAPABILITIES
from ..context.constants import (
    CAPABILITY_TABLE_FLOOR,
    DEFAULT_WINDOW_ANTHROPIC,
    DEFAULT_WINDOW_OPENAI,
    ONE_M_WINDOW,
)

_ENV_OVERRIDE = "CLAUDE_CODE_MAX_CONTEXT_TOKENS"


def get_context_window_for_model(model: str, protocol: str) -> int:
    """按当前模型解析上下文窗口上限（四级严格优先，前级命中即返回）。

    1. env CLAUDE_CODE_MAX_CONTEXT_TOKENS 已设置 → 取该值（解析失败跳下级）。
    2. 模型名带 [1m] 后缀 → 1,000,000。
    3. 能力表命中且 ≥100K → 取表值。
    4. 按 protocol 协议默认（anthropic 200000 / openai 128000 / 其余保守 200000）。
    """
    # 第 1 级：env 硬覆盖
    raw = os.environ.get(_ENV_OVERRIDE)
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass  # 非数字 → 跳下级

    # 第 2 级：[1m] 后缀
    if "[1m]" in model:
        return ONE_M_WINDOW

    # 第 3 级：能力表（仅 ≥100K）
    table_value = CAPABILITIES.get(model)
    if table_value is not None and table_value >= CAPABILITY_TABLE_FLOOR:
        return table_value

    # 第 4 级：协议默认
    if protocol == "openai":
        return DEFAULT_WINDOW_OPENAI
    return DEFAULT_WINDOW_ANTHROPIC
