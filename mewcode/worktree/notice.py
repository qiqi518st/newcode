"""worktree notice（ch14 F8.3）：注入隔离子 Agent 的上下文说明模板。

让子 Agent 知道自己在一个独立副本中工作：父目录、工作目录、绝对路径需翻译、
编辑前重新 read_file（避免用父 Agent 提到但已过时的内容）。
"""

from __future__ import annotations

_WORKTREE_NOTICE_TEMPLATE = """<worktree-context>
你当前在一个独立的 Git Worktree 副本中工作，与父 Agent 隔离。
- 父目录：{parent_cwd}
- 你的工作目录：{wt_path}
- 父 Agent 提到的绝对路径基于父目录，你需要翻译成本地路径（替换前缀）再读写
- 编辑文件前，必须先在本 Worktree 重新 read_file 一次，避免使用过时内容
</worktree-context>"""


def build_worktree_notice(parent_cwd: str, wt_path: str) -> str:
    """构造 worktree 隔离说明文本（F8.3，唯一实现处）。"""
    return _WORKTREE_NOTICE_TEMPLATE.format(parent_cwd=parent_cwd, wt_path=wt_path)
