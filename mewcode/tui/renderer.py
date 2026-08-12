"""Rich 流式 Markdown 渲染器"""

from collections.abc import AsyncIterator

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from ..provider.base import StreamEvent


class RichRenderer:
    """基于 Rich 的流式 Markdown 渲染器"""

    def __init__(self) -> None:
        self._console = Console()
        self._stream_buffer: str = ""

    async def render_stream(self, token_stream: AsyncIterator[StreamEvent]) -> str:
        """流式渲染 StreamEvent 序列，返回完整响应文本"""
        self._stream_buffer = ""

        with Live(
            Markdown(""),
            console=self._console,
            refresh_per_second=10,
            vertical_overflow="visible",
        ) as live:
            async for event in token_stream:
                if event.text:
                    self._stream_buffer += event.text
                    live.update(Markdown(self._stream_buffer))
                elif event.done:
                    # 最终渲染并结束
                    live.update(Markdown(self._stream_buffer))
                    return self._stream_buffer
                elif event.err:
                    # 错误：退出 Live，由调用方处理
                    raise event.err

        return self._stream_buffer

    def render_static(self, content: str) -> None:
        """静态渲染 Markdown（用于定型展示）"""
        self._console.print(Markdown(content))
