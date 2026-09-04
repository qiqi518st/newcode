"""Skill 正文渲染：把 body 变为最终注入文本（inline 与 fork 都先经此层，F1.3/F3.4）。

规则：
1. 替换所有 `$ARGUMENTS` 为调用时用户传入的 args（整段替换，F1.3）。
2. 无占位符且 args 非空 → 末尾追加 `\n\n## User Request\n\n<args>`（兜底规则，
   参考模板：显式 `/name args` 的 args 也要进入 prompt）。
3. allowed_tools 非空 → body 顶部插入「本 Skill 设计为只用这些工具」提示段
   （inline 不真过滤，仅渐进式提示引导模型选对工具，F3.4）。
"""

from .types import Skill

_ARGUMENTS_PLACEHOLDER = "$ARGUMENTS"

_TOOL_HINT_TEMPLATE = (
    "This skill is designed to use only these tools: {tools}. "
    "Prefer them over other tools when possible.\n\n---\n\n"
)


def render_body(skill: Skill, args: str) -> str:
    """渲染 Skill 正文：占位符替换 + 兜底 + 工具提示（顺序固定，F3.4）。"""
    body = skill.prompt_body
    has_placeholder = _ARGUMENTS_PLACEHOLDER in body

    if has_placeholder:
        body = body.replace(_ARGUMENTS_PLACEHOLDER, args)
    elif args:
        # 无占位符但调用带了 args → 末尾兜底追加（参考模板）
        body = f"{body}\n\n## User Request\n\n{args}"

    if skill.meta.allowed_tools:
        hint = _TOOL_HINT_TEMPLATE.format(tools=", ".join(skill.meta.allowed_tools))
        body = hint + body

    return body
