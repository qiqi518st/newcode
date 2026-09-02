"""后台任务管理器（ch13 F7）：BackgroundTask + TaskManager。

- 子 Agent run 始终是 Manager 名下 asyncio 任务（前台=工具 await、后台=立即返回、
  超时移交=adopt_running 只转移所有权不杀重来——spec F7.3）
- 续派复用同 task_id（状态语义=同一 worker 继续，round 递增、result 覆盖，F7.10）
- 完成经 done 队列通知（maxsize=32，满则丢弃 + stderr 警告，F7.6）
- 空闲清理 / 保留上限 / 排队上限（F7.7/F7.8）
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from ..agent.events import EventType
from ..provider.base import TokenUsage
from .errors import MaxTurnsReached
from .types import NOTIFICATION_XML, RESULT_TRUNCATE_CHARS

if TYPE_CHECKING:
    from ..agent.agent import Agent


class Status(IntEnum):
    RUNNING = 0
    COMPLETED = 1
    FAILED = 2
    CANCELLED = 3


class TaskNotFound(Exception):
    """目标任务不存在（SendMessage / /tasks send 错误路径，F7.10）。"""


class TaskBusy(Exception):
    """目标任务不可续派（已取消 / 排队已满，F7.8/F7.10）。"""


class TaskCapReached(Exception):
    """目标任务已达 max_tasks_per_agent（F7.7）。"""


@dataclass
class BackgroundTask:
    """一个后台子 Agent 的完整状态快照（spec F7.4/F7.5）。"""

    id: str  # agent-<hex>（续派复用同 id，F7.10）
    name: str | None  # spawn 时 name（SendMessage 寻址）
    sub_agent: Agent
    task_text: str  # 当前轮任务文本
    role: str = ""  # 角色名（notification summary 用，F7.6）
    status: Status = Status.RUNNING
    result: str = ""  # 本轮跑完的最终文本（续派覆盖）
    err: BaseException | None = None
    start_time: float = field(default_factory=time.monotonic)  # 首轮启动
    end_time: float | None = None  # 每轮结束更新
    usage: TokenUsage = field(default_factory=lambda: TokenUsage(0, 0))  # 本轮
    total_usage: TokenUsage = field(default_factory=lambda: TokenUsage(0, 0))  # 累计
    tool_count: int = 0  # 本轮工具调用次数
    last_activity: str = ""  # 最近一次工具名
    round: int = 1  # 已执行轮数（首轮=1，续派+1，上限 max_tasks_per_agent）
    queue: deque[str] = field(
        default_factory=deque
    )  # 排队续派任务（≤max_queue_per_agent）
    idle_since: float | None = None  # completed/failed 后时间戳（空闲清理）
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    run_task: asyncio.Task | None = None  # 当前轮 run 任务（Manager 持有）
    adopted: bool = False  # 已移交后台（前台→后台标记，预留 ESC 扩展）
    background: bool = False  # 后台启动：完成才推 done 通知；前台内联完成不推（F7.6）


@dataclass
class ForegroundHandle:
    """前台子 Agent 句柄（AgentTool 前台分支 await 用，F7.3）。"""

    task_id: str
    run_task: asyncio.Task
    task: BackgroundTask


def build_task_notification(task: BackgroundTask) -> str:
    """组 `<task-notification>` XML（spec F7.6）：result 截断 RESULT_TRUNCATE_CHARS。"""
    status = task.status.name.lower()
    result = (
        (task.err and str(task.err)) if (task.err and not task.result) else task.result
    )
    result = result or ""
    if len(result) > RESULT_TRUNCATE_CHARS:
        result = result[:RESULT_TRUNCATE_CHARS] + "…"
    label = task.role or task.name or task.id
    summary = f'Agent "{label}" {status}'
    return NOTIFICATION_XML.format(
        task_id=task.id,
        status=status,
        summary=summary,
        result=result,
    )


class TaskManager:
    """管理后台任务（协程安全，单事件循环，spec F7.4）。"""

    def __init__(
        self,
        *,
        max_tasks_per_agent: int = 10,
        max_queue_per_agent: int = 2,
        max_idle_agents: int = 10,
        idle_cleanup_minutes: float = 15.0,
    ) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, BackgroundTask] = {}
        self._by_name: dict[str, str] = {}  # name → id（弱引用，后启动覆盖）
        self._done: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._max_tasks_per_agent = max_tasks_per_agent
        self._max_queue_per_agent = max_queue_per_agent
        self._max_idle_agents = max_idle_agents
        self._idle_cleanup_seconds = idle_cleanup_minutes * 60.0

    # ── 查询 ──────────────────────────────────────────────
    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def get_by_name(self, name: str) -> BackgroundTask | None:
        task_id = self._by_name.get(name)
        return self._tasks.get(task_id) if task_id else None

    def _resolve(self, task_id_or_name: str) -> BackgroundTask | None:
        return self.get(task_id_or_name) or self.get_by_name(task_id_or_name)

    def list(self) -> list[BackgroundTask]:
        return sorted(self._tasks.values(), key=lambda t: t.start_time)

    def running_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == Status.RUNNING)

    def drain_done(self) -> list[str]:
        ids: list[str] = []
        while True:
            try:
                ids.append(self._done.get_nowait())
            except asyncio.QueueEmpty:
                break
        return ids

    # ── 启动（后台）───────────────────────────────────────
    def launch(
        self,
        agent: Agent,
        task_text: str,
        *,
        name: str | None = None,
        already_injected: bool = False,
        role_name: str | None = None,
    ) -> str:
        """后台启动：立即返回 task_id（spec F7.4）。"""
        task_id = self._next_id()
        bt = BackgroundTask(
            id=task_id,
            name=name,
            role=role_name or "",
            sub_agent=agent,
            task_text=task_text,
            background=True,
        )
        self._register(bt)
        self._start_round(bt, task_text, already_injected=already_injected)
        return task_id

    # ── 启动（前台，供 AgentTool await + 超时移交）───────────
    def launch_foreground(
        self,
        agent: Agent,
        task_text: str,
        *,
        name: str | None = None,
        role_name: str | None = None,
    ) -> ForegroundHandle:
        """前台启动：注册运行中任务，返回 handle 供工具 await（F7.3）。"""
        task_id = self._next_id()
        bt = BackgroundTask(
            id=task_id,
            name=name,
            role=role_name or "",
            sub_agent=agent,
            task_text=task_text,
            background=False,
        )
        self._register(bt)
        self._start_round(bt, task_text, already_injected=False)
        return ForegroundHandle(task_id=task_id, run_task=bt.run_task, task=bt)  # type: ignore[arg-type]

    def adopt_running(self, task_id: str) -> bool:
        """前台→后台移交：任务所有权已在 Manager，仅打标记（不杀、不重算，F7.3）。"""
        bt = self._tasks.get(task_id)
        if bt is None:
            return False
        bt.adopted = True
        return True

    # ── 终止 ──────────────────────────────────────────────
    def stop(self, task_id: str) -> bool:
        """终止任务（spec F7.4/F8.1/F8.3，kill=关闭该子 Agent）。

        - 运行中 → 取消（status→cancelled，保留可见让用户看到被终止）
        - 已结束（completed/failed/cancelled）→ **从列表移除**（kill 已结束任务 = 清理，
          否则 /tasks 显示「已请求终止」但任务原地不动）
        - 不存在 → False
        """
        bt = self._tasks.get(task_id)
        if bt is None:
            return False
        if bt.status == Status.RUNNING:
            if bt.run_task is not None and not bt.run_task.done():
                bt.cancel_event.set()
                bt.run_task.cancel()
        else:
            self._drop(task_id)
        return True

    # ── 续派（同 id 复用，F7.10）───────────────────────────
    def continue_agent(self, task_id_or_name: str, message: str) -> str:
        """续派：给仍存活的子 Agent 追加新任务，返回同 task_id。

        - 不存在 → TaskNotFound；已取消 / 排队满 → TaskBusy；达任务上限 → TaskCapReached
        - 空闲（completed/failed）→ 立即新轮；运行中 → 入队（≤max_queue_per_agent）
        """
        bt = self._resolve(task_id_or_name)
        if bt is None:
            raise TaskNotFound(f"task not found: {task_id_or_name}")
        if bt.status == Status.CANCELLED:
            raise TaskBusy(f"task {bt.id} is cancelled")
        if bt.round >= self._max_tasks_per_agent:
            raise TaskCapReached(
                f"task {bt.id} reached max {self._max_tasks_per_agent} tasks"
            )
        if bt.status == Status.RUNNING:
            if len(bt.queue) >= self._max_queue_per_agent:
                raise TaskBusy(f"task {bt.id} queue full ({self._max_queue_per_agent})")
            bt.queue.append(message)
            return bt.id
        # 空闲 → 立即新轮（同 id，status 重置，result 覆盖，round 递增，F7.10）
        bt.round += 1
        bt.background = True  # 续派轮次按后台语义推通知
        self._start_round(bt, message, already_injected=True)
        return bt.id

    # ── 生命周期 ──────────────────────────────────────────
    def clear_all(self) -> None:
        """/clear /resume /session_new / 退出：取消运行中任务、清空全部（F7.9）。"""
        for bt in list(self._tasks.values()):
            if bt.run_task is not None and not bt.run_task.done():
                bt.run_task.cancel()
        self._tasks.clear()
        self._by_name.clear()

    async def run(self) -> None:
        """常驻：空闲清理扫描（~30s 周期，F7.7）；单次清扫失败不终止循环（N3）。"""
        while True:
            await asyncio.sleep(30.0)
            try:
                self._sweep_idle()
            except Exception:
                logger.exception("task manager idle sweep failed")

    def _sweep_idle(self) -> None:
        """清理：空闲超时 / 达任务上限 / 保留上限超限（关最旧）。

        终态集合含 CANCELLED——被取消的任务无续派价值，也纳入清扫（防泄漏）。
        """
        now = time.monotonic()
        idle: list[BackgroundTask] = []
        for task_id in list(self._tasks.keys()):
            bt = self._tasks[task_id]
            if bt.status not in (Status.COMPLETED, Status.FAILED, Status.CANCELLED):
                continue
            # 达任务上限：无续派价值，直接清理
            if bt.round >= self._max_tasks_per_agent:
                self._drop(task_id)
                continue
            # 空闲超时
            if (
                bt.idle_since is not None
                and now - bt.idle_since > self._idle_cleanup_seconds
            ):
                self._drop(task_id)
                continue
            idle.append(bt)
        # 保留上限：超出关最旧
        if len(idle) > self._max_idle_agents:
            for bt in sorted(idle, key=lambda t: t.end_time or 0)[
                : len(idle) - self._max_idle_agents
            ]:
                self._drop(bt.id)

    # ── 内部 ──────────────────────────────────────────────
    def _next_id(self) -> str:
        return f"agent-{secrets.token_hex(4)}"

    def _register(self, bt: BackgroundTask) -> None:
        self._tasks[bt.id] = bt
        if bt.name:
            self._by_name[bt.name] = bt.id  # 后启动覆盖前（弱引用）

    def _drop(self, task_id: str) -> None:
        bt = self._tasks.pop(task_id, None)
        if bt is None:
            return
        if bt.name and self._by_name.get(bt.name) == task_id:
            self._by_name.pop(bt.name, None)

    def _start_round(
        self, bt: BackgroundTask, task_text: str, *, already_injected: bool
    ) -> None:
        """启动一轮跑动（首轮或续派；续派复用同 task_id，F7.10）。"""
        if bt.round > 1:
            bt.status = Status.RUNNING
            bt.result = ""
            bt.err = None
            bt.end_time = None
            bt.idle_since = None
            bt.usage = TokenUsage(0, 0)
            bt.tool_count = 0
            bt.last_activity = ""
            bt.task_text = task_text
        # 续派的任务消息由调用方（continue_agent）注入 conv
        bt.run_task = asyncio.create_task(
            self._drive(bt, task_text, already_injected=already_injected)
        )

    def _make_observer(self, bt: BackgroundTask) -> Callable[[object], None]:
        def observer(event: object) -> None:
            if event.type == EventType.TOOL_CALL:
                bt.tool_count += 1
                bt.last_activity = getattr(event.payload, "tool_name", "") or ""
            elif event.type == EventType.TOKEN_USAGE:
                tu = event.payload
                bt.usage = TokenUsage(
                    bt.usage.input_tokens + tu.input_tokens,
                    bt.usage.output_tokens + tu.output_tokens,
                    bt.usage.cache_creation_input_tokens
                    + tu.cache_creation_input_tokens,
                    bt.usage.cache_read_input_tokens + tu.cache_read_input_tokens,
                )

        return observer

    async def _drive(
        self, bt: BackgroundTask, task_text: str, *, already_injected: bool
    ) -> None:
        """驱动一轮 run_to_completion，包 try/except BaseException（N3：不崩主程序）。"""
        try:
            text = await bt.sub_agent.run_to_completion(
                task_text,
                already_injected=already_injected,
                observer=self._make_observer(bt),
            )
            bt.result = text
            bt.status = Status.COMPLETED
        except asyncio.CancelledError:
            bt.status = Status.CANCELLED
            raise
        except MaxTurnsReached as exc:
            bt.result = exc.text
            bt.err = exc
            bt.status = Status.FAILED
        except BaseException as exc:  # noqa: BLE001 —— 任务失败不崩主程序（N3）
            bt.err = exc
            bt.status = Status.FAILED
        finally:
            bt.end_time = time.monotonic()
            bt.idle_since = time.monotonic()
            bt.total_usage = TokenUsage(
                bt.total_usage.input_tokens + bt.usage.input_tokens,
                bt.total_usage.output_tokens + bt.usage.output_tokens,
                bt.total_usage.cache_creation_input_tokens
                + bt.usage.cache_creation_input_tokens,
                bt.total_usage.cache_read_input_tokens
                + bt.usage.cache_read_input_tokens,
            )
            # 仅后台语义任务推完成通知（前台内联完成结果已回主 Agent，F7.6）
            if bt.background or bt.adopted or bt.round > 1:
                try:
                    self._done.put_nowait(bt.id)
                except asyncio.QueueFull:
                    print(
                        f"[task manager] done queue full, dropping notification for {bt.id}",
                        file=sys.stderr,
                    )
            # 排队续派：本轮结束后从队列取下一个（F7.8），不达上限
            if (
                bt.status != Status.CANCELLED
                and bt.queue
                and bt.round < self._max_tasks_per_agent
            ):
                next_task = bt.queue.popleft()
                bt.round += 1
                self._start_round(bt, next_task, already_injected=True)
