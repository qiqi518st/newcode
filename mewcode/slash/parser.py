"""命令解析器（F2）：parse_command(text) -> (name, args) | None。

纯函数，不依赖任何模块（T3）。切分规则：
- strip 后空串 → None（早返回，F2.3）
- 不以 "/" 开头 → None（非命令）
- 否则去掉前导 "/"，第一个空格前为 name（转小写），之后为 args（原样保留）

退化形态（约定写死）：
- "/" → ("", "")，name 为空串，dispatch 必须按"未命中"处理
- "//double" → ("/double", "")，name 含斜杠，永不匹配任何注册名 → 未命中
- "/ /help" → ("", "/help")，name 为空串 → 未命中
dispatch 对退化形态的引导文案不得拼 "/+name"（避免 `"未知命令: /, ..."` 悬空斜杠）。
"""

from __future__ import annotations


def parse_command(text: str) -> tuple[str, str] | None:
    """解析 "/name args" 形态；非命令/空输入返回 None。"""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if not stripped.startswith("/"):
        return None
    rest = stripped[1:]  # 去掉前导 /
    head, sep, tail = rest.partition(" ")
    if not sep:
        # 无空格：无参数（tail 为空串，head 为整个剩余串，可能含 "/"）
        return (head.lower(), "")
    # 有空格：head 为命令名（可能为空串），tail 保留原样（含多个空格）
    return (head.lower(), tail)