"""Hook 引擎（ch12 F2/F7/F9）：统一事件分派 + once 集合 + 后台任务跟踪。

调用方（agent/tui/main）在事件节点调 dispatch(event, payload)；引擎内部决定
同步/异步执行、拦截判定与 prompt 收集，错误一律内部消化（stderr 日志），
保证错误隔离与无侵入（N1/N10）。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from .conditions import eval_condition
from .executor import Executor
from .types import DispatchResult, Event, is_blocking

if TYPE_CHECKING:
    from .types import Hook, Payload


class Engine:
    """Hook 引擎：规则列表（按声明顺序，优先级高者在前）+ 统一 dispatch。"""

    def __init__(self, rules: list[Hook], sources: list[str]) -> None:
        self._rules = rules
        self._sources = sources
        self._once_fired: set[str] = set()
        self._dispatching: set[Event] = set()  # F9.2 防重入（同事件 dispatch 期间不重入自身）
        self._lock = asyncio.Lock()
        self._executor = Executor()
        self._tasks: set[asyncio.Task[None]] = set()
        # 通用字段取值源（main.py 装配后注入；hooks 不反向依赖 session/permission）
        self._get_session_id: Callable[[], str] | None = None
        self._get_mode: Callable[[], str] | None = None

    def set_context_providers(
        self,
        get_session_id: Callable[[], str],
        get_mode: Callable[[], str],
    ) -> None:
        """注入 session_id / mode 取值函数（F3.4 通用字段，AC26）。"""
        self._get_session_id = get_session_id
        self._get_mode = get_mode

    def _base_payload(self) -> Payload:
        """通用字段补充：session_id / mode（event 由 dispatch 补全）。"""
        payload: Payload = {}
        if self._get_session_id is not None:
            payload["session_id"] = self._get_session_id() or ""
        if self._get_mode is not None:
            payload["mode"] = self._get_mode() or ""
        return payload

    async def dispatch(self, event: Event, payload: Payload) -> DispatchResult:
        """统一分派接口（blocking 由 is_blocking(event) 内部判定）。

        1) 过滤匹配 event 的 hook（按声明顺序）
        2) once 过滤（_once_fired 命中跳过）
        3) 串行求值条件（cond=None → 无条件触发）
        4) asyncio_mode → create_task 后台执行（记 _tasks、once 立即标记，不等结果）
        5) 同步执行：err → stderr 日志 continue；prompt → injected_prompts；
           blocked 且 is_blocking → 设 blocked/reason/blocking_hook_name 并 break（F7.3）
        6) 同步执行成功后 once 标记（F2.2：成功并执行后）
        """
        if not self._rules:
            return DispatchResult()
        if event in self._dispatching:
            # F9.2 防重入：Hook 动作触发的同事件二次 dispatch 不重入自身
            return DispatchResult()

        result = DispatchResult()
        base = self._base_payload()
        self._dispatching.add(event)
        try:
            for hook in self._rules:
                if hook.event is not event:
                    continue
                if hook.once and hook.name in self._once_fired:
                    continue
                full_payload: Payload = {"event": event.value, **base, **payload}
                if hook.condition is not None and not eval_condition(
                    hook.condition, full_payload
                ):
                    continue

                if hook.asyncio_mode:
                    # 后台执行：不等结果、不参与 block/inject（拦截类事件不允许 async，加载期已拦截）
                    if hook.once:
                        self._once_fired.add(hook.name)
                    task = asyncio.create_task(
                        self._run_background(hook, full_payload, event)
                    )
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                    continue

                try:
                    er = await self._executor.run(
                        hook, full_payload, blocking=is_blocking(event)
                    )
                except asyncio.CancelledError:
                    raise  # F9.3：拦截同步等待中取消 → 传播退出
                except Exception as e:  # noqa: BLE001 —— 引擎层兜底，绝不让 hook 破坏主流程
                    print(f"[hook {hook.name}] {event.value} failed: {e}", file=sys.stderr)
                    continue

                if er.err is not None:
                    # F9.1：hook 自身失败只记日志，不中断主流程
                    print(
                        f"[hook {hook.name}] {event.value} failed: {er.err}",
                        file=sys.stderr,
                    )
                    continue
                if er.prompt:
                    result.injected_prompts.append(er.prompt)
                if er.blocked and is_blocking(event):
                    # F7.3：任一个表达拦截即拦截，后面 Hook 不执行
                    result.blocked = True
                    result.reason = er.reason
                    result.blocking_hook_name = hook.name
                    break
                if hook.once:
                    self._once_fired.add(hook.name)
            return result
        finally:
            self._dispatching.discard(event)

    async def _run_background(
        self, hook: Hook, payload: Payload, event: Event
    ) -> None:
        """async hook 后台执行（F2.2/F9.1）：失败 stderr 不重试，尽力而为。"""
        try:
            await self._executor.run(hook, payload, blocking=False)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 后台失败不中断主流程
            print(f"[hook {hook.name}] {event.value} failed: {e}", file=sys.stderr)

    async def reset_for_new_session(self) -> None:
        """清空 once 集合（/clear、/resume、/session_new 调，F2.2/N8）。"""
        self._once_fired.clear()

    @property
    def sources(self) -> list[str]:
        return list(self._sources)

    @property
    def rules(self) -> list[Hook]:
        return list(self._rules)

    async def close(self) -> None:
        """shutdown 收尾：记录未完成后台任务，不强制等待（F9.5）；关 http 连接池。"""
        pending = [t for t in self._tasks if not t.done()]
        if pending:
            print(
                f"[hooks] {len(pending)} background task(s) unfinished at shutdown",
                file=sys.stderr,
            )
        await self._executor.aclose()
