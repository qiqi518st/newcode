"""system-reminder 补充消息构造 + 规划模式按轮注入（spec F5 / F6）"""

from ..provider.base import Message

_SYSTEM_REMINDER_TAG = "system-reminder"


def system_reminder(content: str) -> Message:
    """构造一条 role=user、以 <system-reminder> 标签包裹的补充消息。

    标签语义让模型理解这是系统补充上下文而非用户提问，不针对它直接回复。
    每轮动态构造、不写入持久历史（由调用方保证）。
    """
    return Message(
        role="user",
        content=f"<{_SYSTEM_REMINDER_TAG}>{content}</{_SYSTEM_REMINDER_TAG}>",
    )


PLAN_MODE_FULL: str = (
    "【计划模式】当前处于只读计划模式。"
    "你只能使用只读工具（read_file、list_files、search_code）探查代码和理解项目结构。"
    "不能修改文件或执行命令。"
    "\n\n你的唯一任务是产出一份结构化的计划文档。"
    "你的输出就是计划本身，不是执行报告，不是操作日志。"
    "\n\n计划格式要求：\n"
    "- 用 Markdown 任务列表（- [ ] 条目）列出具体步骤\n"
    "- 每条包含：目标文件、修改内容、验证方式\n"
    "- 禁止使用 ✅ ❌ 等暗示执行完成的符号\n"
    "- 不要描述'我将要做...'或'我已经做了...'，直接写计划内容\n"
    "- 在计划开头用 HTML 注释声明一个简短的英文 slug 标识符，"
    "格式为 <!-- slug: 简短英文标识 -->，"
    "例如：<!-- slug: add-login-page -->\n"
    "\n记住：你只生成计划，不执行任何操作。"
)

PLAN_MODE_LEAN: str = (
    "【计划模式】只读：仅用只读工具探查并产出计划文档，不修改文件、不执行命令。"
)


def plan_mode_reminder(turn: int) -> Message:
    """按 ReAct 轮次返回规划模式提醒：turn 0 或每 5 轮（0,5,...）完整版，其余精简版。

    计数仅限当前一次请求内部的 turn，用完即弃，不跨请求累计（spec F6）。
    """
    content = PLAN_MODE_FULL if turn == 0 or turn % 5 == 0 else PLAN_MODE_LEAN
    return system_reminder(content)
