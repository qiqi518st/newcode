"""Skill 数据模型：SkillMeta / Skill / SkillSource / ActiveEntry / ToolSchema 与异常。

设计要点：
- SkillMeta 记录 frontmatter 解析结果；mode/fork_context 用 Literal 限定合法枚举，
  parser 对非法值 warning 降级为缺省后再构造（不阻断加载，F1.2）。
- Skill 是可重载元数据 + 源路径；正文热更新时由 Catalog 重读源文件覆盖 prompt_body。
- ToolSchema 是目录型 Skill 的 tool.json 声明（注册用），与 allowedTools（可见性）职责分离（F9.2）。
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


class SkillSource(Enum):
    """来源层级（F2.1 三级路径，优先级 项目级 > 用户级 > 内置级）。"""

    USER = "user"
    PROJECT = "project"
    BUILTIN = "builtin"


class SkillParseError(Exception):
    """Skill 文件解析失败（frontmatter 缺失/非法/必填缺失/名字非法）。

    调用方（Catalog）捕获后跳过该文件并记 warning（N3 失败隔离，不阻断整体加载）。
    """


class SkillDependencyError(Exception):
    """Skill 依赖不满足（如 allowedTools 白名单引用主环境不存在的工具，F2.7）。"""


@dataclass
class SkillMeta:
    """frontmatter 元信息（解析/校验后）。

    allowed_tools 为空 = 不限制（主环境全部工具可见，与未激活行为一致）。
    """

    name: str  # 归一化唯一名字（小写、字母数字与 -）
    description: str  # 一句话说明（阶段一摘要 / /skill list）
    allowed_tools: list[str] = field(default_factory=list)  # 工具白名单；空 = 不限制
    mode: Literal["inline", "fork"] = "inline"  # 缺省 inline
    fork_context: Literal["none", "recent", "full"] = "none"  # 仅 fork 生效，缺省 none
    model: str | None = None  # fork 指定模型；None = 当前会话模型

    def is_fork(self) -> bool:
        return self.mode == "fork"


@dataclass
class Skill:
    """一个已解析的 Skill（元数据 + 启动缓存正文 + 源路径）。

    prompt_body 是启动扫描时缓存的正文；Catalog.get 每次调用重读 source_path 覆盖它（N7 热更新）。
    tools 为目录型 Skill 的 tool.json 声明的专属工具（F9.2）。
    """

    meta: SkillMeta
    prompt_body: str
    source_dir: Path  # 绝对路径：单文件为所在目录，目录型为 Skill 目录
    source: SkillSource
    tools: tuple["ToolSchema", ...] = ()
    source_path: Path = field(default_factory=Path)  # 源文件绝对路径（热重载用，F2.3）

    def __post_init__(self) -> None:
        if not self.source_path:
            self.source_path = self.source_dir / (
                "SKILL.md" if self.is_directory else f"{self.meta.name}.md"
            )

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def is_directory(self) -> bool:
        """目录型 Skill 标志（源文件为 SKILL.md，含 tool.json/references/，F9.1）。"""
        return self.source_path.name == "SKILL.md"


@dataclass(frozen=True)
class ToolSchema:
    """tool.json 声明（目录型 Skill 专属工具，F9.2）。

    entrypoint 为 references/ 下实现脚本的相对路径，执行时以子进程运行（N4）。
    """

    name: str
    description: str
    parameters: dict  # JSON Schema object
    entrypoint: str  # references/ 下相对路径


@dataclass
class ActiveEntry:
    """激活态条目（ActiveSkills 中的单条，F5.1）。

    body 是激活那一刻磁盘上 SKILL.md 的正文（经 render_body 渲染后）。
    """

    name: str
    body: str
