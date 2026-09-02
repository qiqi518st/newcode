"""explicit cwd 传递通道（ch14 F7.1）：contextvars.ContextVar + with_cwd / resolve_path。

机制为**新建**（实际代码库无 contextvars 先例）；不改 Tool.execute(arguments) 签名，
工具在调用时经 cwd_from_ctx() 取当前工作目录（缺省进程 cwd）。ctx 注入不改 schema →
工具列表稳定、prompt cache 不抖（N1/F7.4）。async 单循环内 await 天然透传。
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from pathlib import Path

_ctx_cwd: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cwd", default=None
)


@contextmanager
def with_cwd(directory: str):
    """设置 ctx cwd；空目录直接 yield 不变（F7.3 注入点）。

    复位容错：async 生成器若在**异 context** 被终结（aclose / GC finalizer —— 子 Agent
    后台任务与主对话异步交互时可能发生），token 不属于当前 context → reset 抛
    「created in a different Context」。此时该 context 的 cwd 已随其消亡，忽略即可，
    绝不让它炸成 Unhandled exception in event loop（N3：隔离通道尽力而为）。

    注意：被跨 context 终结的生成器，其原 context 的 cwd 值会残留到该任务消亡为止
    （create_task 复制的子任务会继承）。该异常场景只发生在回合末尾（生成器被遗弃），
    后续新回合从事件循环默认 context 起步、天然干净，影响有界。
    """
    if not directory:
        yield
        return
    token = _ctx_cwd.set(directory)
    try:
        yield
    finally:
        try:
            _ctx_cwd.reset(token)
        except (RuntimeError, ValueError):
            # 当前（终结者）context 尽力清为默认，减少可观察残留
            _ctx_cwd.set(None)


def cwd_from_ctx() -> str | None:
    """当前 ctx cwd；None = 未隔离（用进程 cwd）。"""
    return _ctx_cwd.get()


def resolve_path(p: str) -> str:
    """把路径解析为绝对路径：绝对原样；空→base；相对 = (ctx cwd 或进程 cwd) join。"""
    base = _ctx_cwd.get() or str(Path.cwd())
    if not p:
        return base
    pp = Path(p)
    if pp.is_absolute():
        return str(pp)
    return str(Path(base) / pp)
