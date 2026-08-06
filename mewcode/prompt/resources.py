"""内置 system prompt 和启动横幅资源"""

# 内置默认 system prompt
SYSTEM_PROMPT: str = (
    "你是一个 AI 编程助手 MewCode，运行在终端中。"
    "请用中文回复，回答简洁清晰。"
)

# 小狗图案
DOG_BANNER: str = "૮₍ •᎔•₎ა"


def render_banner(version: str, cwd: str) -> str:
    """返回拼接后的启动横幅：ASCII 小狗 + 版本号 + 工作目录"""
    return f"""{DOG_BANNER}
  MewCode v{version}
  {cwd}
"""