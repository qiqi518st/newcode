"""工具调度器：按安全性分批，保序并发/串行执行"""

import asyncio
from dataclasses import dataclass

from ..provider.base import ToolCall, ToolResult
from ..tools.registry import Registry


@dataclass
class ScheduledResult:
    """单个工具调度结果"""

    tool_call: ToolCall
    result: ToolResult


class ToolScheduler:
    """按 read_only 属性分批执行工具调用，保序返回结果"""

    def __init__(self, registry: Registry) -> None:
        self._registry = registry

    async def schedule(
        self,
        tool_calls: list[ToolCall],
    ) -> list[ScheduledResult]:
        """按原始顺序返回结果；并发组 asyncio.gather，串行组逐个执行"""
        if not tool_calls:
            return []

        # 预分配结果数组，按原始下标写入
        results: list[ScheduledResult | None] = [None] * len(tool_calls)

        # 分组：只读 → 并发，读写 → 串行
        concurrent_items: list[tuple[int, ToolCall]] = []
        serial_items: list[tuple[int, ToolCall]] = []

        for idx, tc in enumerate(tool_calls):
            if self._registry.is_read_only(tc.tool_name):
                concurrent_items.append((idx, tc))
            else:
                serial_items.append((idx, tc))

        # 并发执行只读组
        if concurrent_items:
            tasks = [
                self._execute_one(idx, tc, results) for idx, tc in concurrent_items
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        # 串行执行读写组
        for idx, tc in serial_items:
            await self._execute_one(idx, tc, results)

        # 过滤 None 并返回
        return [r for r in results if r is not None]

    async def _execute_one(
        self,
        idx: int,
        tc: ToolCall,
        results: list[ScheduledResult | None],
    ) -> None:
        """执行单个工具，结果写入 results[idx]"""
        try:
            result = await self._registry.execute(tc.tool_name, tc.arguments)
        except Exception as e:
            result = ToolResult(status="error", error=str(e))
        results[idx] = ScheduledResult(tool_call=tc, result=result)
