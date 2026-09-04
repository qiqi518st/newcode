"""Context Window 四级解析（spec F29：env → [<数字><K|M>] 后缀 → 能力表 → 协议默认）。

永不抛：任一级异常落入第 4 级协议默认。
"""

import os
import re

from ..context.capabilities import CAPABILITIES
from ..context.constants import (
    CAPABILITY_TABLE_FLOOR,
    DEFAULT_WINDOW_ANTHROPIC,
    DEFAULT_WINDOW_OPENAI,
    ONE_M_WINDOW,
)

_ENV_OVERRIDE = "CLAUDE_CODE_MAX_CONTEXT_TOKENS"

# 模型名上下文后缀，如 deepseek-v4-flash[1M] / model[512k] / model[2M]。
# 单位大小写不敏感（[1M] 与 [1m] 等价），长度不写死 1M。
_SUFFIX_RE = re.compile(r"\[(\d+)\s*([km])\]", re.IGNORECASE)


def get_context_window_for_model(model: str, protocol: str) -> int:
    """按当前模型解析上下文窗口上限（四级严格优先，前级命中即返回）。

    1. env CLAUDE_CODE_MAX_CONTEXT_TOKENS 已设置 → 取该值（解析失败跳下级）。
    2. 模型名带 [<数字><K|M>] 上下文后缀（大小写不敏感）→ 按单位换算，如 [1M]→1,000,000、[512k]→512,000。
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

    # 第 2 级：[<数字><K|M>] 上下文后缀，按单位换算（K=1,000 / M=1,000,000）
    suffix = _SUFFIX_RE.search(model)
    if suffix:
        n = int(suffix.group(1))
        unit = suffix.group(2).lower()
        return n * (ONE_M_WINDOW if unit == "m" else 1_000)

    # 第 3 级：能力表（仅 ≥100K）
    table_value = CAPABILITIES.get(model)
    if table_value is not None and table_value >= CAPABILITY_TABLE_FLOOR:
        return table_value

    # 第 4 级：协议默认
    if protocol == "openai":
        return DEFAULT_WINDOW_OPENAI
    return DEFAULT_WINDOW_ANTHROPIC
