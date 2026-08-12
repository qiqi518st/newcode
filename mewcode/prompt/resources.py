"""内置资源：启动横幅、/do 执行指令（系统提示模块见 sections.py / reminders.py）"""

# /do 执行指令的用户消息模板
EXECUTE_DIRECTIVE: str = (
    "以下是已审批的计划文档内容。请按照计划执行所有操作，"
    "不要跳过任何步骤，不要询问确认。\n\n"
    "--- 计划开始 ---\n"
    "{plan}\n"
    "--- 计划结束 ---\n\n"
    "开始执行。"
)

# 小狗图案
DOG_BANNER: str = "૮₍ •᎔•₎ა"


def render_banner(version: str, cwd: str) -> str:
    """返回拼接后的启动横幅：ASCII 小狗 + 版本号 + 工作目录"""
    return f"""{DOG_BANNER}
  MewCode v{version}
  {cwd}
"""
