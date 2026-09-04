"""第一层（L1）：大工具结果落盘 + 头部预览替换（spec F1/F2/F3）。

spill_single / _head_preview / build_preview 为 T8 基础件；offload_and_snip（T9）
遍历消息做三步原子替换：落盘（wx 幂等）→ 改写 content → 写账本，任一步失败则
三件都不做（保持原文 + 不写账本，下轮重试）。落盘 I/O 走 asyncio.to_thread 防阻塞
事件循环（N1）。
"""

import asyncio
from pathlib import Path

from ..context.constants import (
    AGGREGATE_LIMIT,
    PREVIEW_MAX_BYTES,
    PREVIEW_MAX_LINES,
    SINGLE_RESULT_THRESHOLD,
)
from ..context.replacement import ContentReplacementState
from ..context.session import SessionContext, SessionPaths
from ..provider.base import Message

_RELOAD_HINT = (
    "完整内容已保存到上述路径，如需查看请用文件读取工具读取该路径，"
    "不要凭头部预览猜测全文。"
)


def _spill_to_path(path: Path, content: str) -> None:
    """wx 幂等落盘到指定路径：已存在直接返回；OSError 自然抛出（交上层 skip）。"""
    if path.exists():
        return
    try:
        with open(path, "xb") as f:
            f.write(content.encode("utf-8"))
    except FileExistsError:
        pass  # 并发竞态下另一协程已落盘，视作幂等成功


def spill_single(session: SessionContext, tool_use_id: str, content: str) -> None:
    """把单条工具结果落盘到 <spill_dir>/<tool_use_id>（wx 幂等）。"""
    _spill_to_path(Path(session.spill_dir) / tool_use_id, content)


def _head_preview(content: str) -> str:
    """取内容头部预览：先按行截断，再做字节级二次截断。

    行截断：splitlines(keepends=True) 前 PREVIEW_MAX_LINES 行拼回；
    字节截断：头部仍超 PREVIEW_MAX_BYTES 时按 UTF-8 边界 errors="ignore"
    截断（spec F1，避免尾部半字节乱码）。
    """
    head = "".join(content.splitlines(keepends=True)[:PREVIEW_MAX_LINES])
    if len(head.encode("utf-8")) > PREVIEW_MAX_BYTES:
        head = head.encode("utf-8")[:PREVIEW_MAX_BYTES].decode("utf-8", errors="ignore")
    return head


def build_preview(original_bytes: int, head: str, spill_path: str) -> str:
    """拼固定格式替换预览（逐字节稳定，同输入必同输出）。

    格式：offloaded 标注 / 原始字节数 / saved to 路径 / head preview / 重读提示。
    用 "\n".join 保证拼接顺序与换行稳定（spec F1 可测试）。
    """
    return "\n".join(
        [
            f"[content offloaded] original size: {original_bytes} bytes",
            f"[saved to] {spill_path}",
            "[head preview]",
            head,
            _RELOAD_HINT,
        ]
    )


async def offload_and_snip(
    messages: list[Message],
    state: ContentReplacementState,
    session_paths: SessionPaths,
) -> int:
    """对 messages 原地执行 L1 替换，返回本次新替换的项数。

    两步：
      1. 遍历 role=="tool" 消息取 id（tool_use_id or tool_call_id or ""），
         decision_for(id) 已决策的直接复用——replaced→content=冻结预览；kept→跳过。
      2. 未决策项（unseen）按 assistant 回合分组（assistant 消息 tool_calls 开启新
         回合，其后 tool 消息归入该组），每项按字节倒序：先落单条 > SINGLE_RESULT_THRESHOLD
         的（F1），再按 AGGREGATE_LIMIT 继续落下一项直到该回合剩余聚合 ≤ 阈值（F2）。

    每项落盘经 asyncio.to_thread 走线程池（N1）；spill 抛 OSError 则该 id 不写账本、
    保持原文，下轮重试（F5b）。已 replaced 的冻结预览复用不重造（F5d）。
    """
    session_paths.ensure_dir()  # 防御：落盘目录缺失时保证可写（幂等）
    replaced = 0

    # 第一遍：已决策项直接复用 / 收集未决策候选（按回合分组）
    turn_groups: list[list[tuple[Message, str]]] = []
    current: list[tuple[Message, str]] | None = None

    for msg in messages:
        if msg.role == "tool":
            id_ = msg.tool_use_id or msg.tool_call_id or ""
            decision, preview = state.decision_for(id_)
            if decision == "replaced":
                if preview is not None:
                    msg.content = preview
                continue
            if decision == "kept":
                continue
            # unseen → 候选
            if current is None:
                current = []  # 无前置 assistant 的工具消息：自成一回合
            current.append((msg, id_))
        elif msg.role == "assistant" and msg.tool_calls:
            # assistant 工具声明开启新回合
            if current is not None:
                turn_groups.append(current)
            current = []
        else:
            # user / 无工具的 assistant → 终结当前回合
            if current is not None:
                turn_groups.append(current)
            current = None

    if current is not None:
        turn_groups.append(current)

    # 第二遍：逐回合做 F1/F2 阈值评估
    for group in turn_groups:
        if not group:
            continue
        sized = [(msg, id_, len(msg.content.encode("utf-8"))) for msg, id_ in group]
        sized.sort(key=lambda x: x[2], reverse=True)

        remaining: list[tuple[Message, str, int]] = []
        for msg, id_, nbytes in sized:
            if nbytes > SINGLE_RESULT_THRESHOLD:  # F1：单条超阈值必落
                if await _spill(msg, id_, session_paths, state):
                    replaced += 1
            else:
                remaining.append((msg, id_, nbytes))

        aggregate = sum(n for _, _, n in remaining)
        for msg, id_, nbytes in remaining:  # F2：剩余聚合超限继续落（从大到小）
            if aggregate <= AGGREGATE_LIMIT:
                break
            if await _spill(msg, id_, session_paths, state):
                replaced += 1
            aggregate -= nbytes

    return replaced


async def _spill(
    msg: Message,
    id_: str,
    session_paths: SessionPaths,
    state: ContentReplacementState,
) -> bool:
    """三步原子落盘：to_thread 落盘 → 改写 content → 写账本。

    spill 抛 OSError → 返回 False，不写账本（保持 unseen，下轮重试 F5b）。
    落盘成功后 decide_once 在同一临界区完成「改写 + 写账本」（N2 无中间态）。
    """
    content = msg.content
    path = session_paths.path_for(id_)
    try:
        await asyncio.to_thread(_spill_to_path, path, content)
    except OSError:
        return False

    def _decide() -> tuple[str, str]:
        preview = build_preview(
            len(content.encode("utf-8")), _head_preview(content), str(path)
        )
        return ("replaced", preview)

    msg.content = state.decide_once(id_, content, _decide)
    return True
