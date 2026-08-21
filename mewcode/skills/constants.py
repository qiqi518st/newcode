"""Skill 系统常量：token 预算、路径、加载器默认值。"""

from pathlib import Path

# 压缩时激活 Skill 共享的 token 预算（F8.1，固定 4k）
ACTIVE_SKILL_TOKEN_BUDGET: int = 4_000

# fork 模式 context: recent 未带 N 时的缺省条数（F3.2）
RECENT_DEFAULT_N: int = 5

# 三级搜索路径（F2.1）
PROJECT_SKILLS_DIR = ".mewcode/skills"  # 项目级（相对工作目录）
USER_SKILLS_DIR = "~/.mewcode/skills"  # 用户级
DISABLED_STATE_FILE = "~/.mewcode/skills/disabled.json"  # disabled 集合持久（F7.8）

# 内置 Skill 目录（编译进包，随 pip 安装分发）
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "builtin"
