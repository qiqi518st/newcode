"""第二层（L2）：LLM 全量摘要（spec F6–F14）。

T12 提供：SUMMARY_INSTRUCTION 九段指令、serialize_conversation 确定性序列化、
build_summary_prompt（单条 user）、extract_summary（<summary> 解析）。
T13 提供 pick_recent_tail / _join_after_summary；T14 提供 Summarizer.summarize 主体。
"""

import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from ..context.constants import (
    ESTIMATE_CHARS_PER_TOKEN,
    PTL_DIRECT_RETRY_LIMIT,
    PTL_DROP_RATIO,
    RECENT_COUNT_FLOOR,
    RECENT_TOKEN_FLOOR,
    SUMMARY_RESERVE_TOKENS,
)
from ..context.dropper import MessageGroupDropper
from ..context.files import FileTracker
from ..context.recovery import RecoveryBuilder
from ..context.tokens import estimate_messages, message_chars
from ..llm import PromptTooLongError
from ..prompt.assembler import PromptPayload
from ..provider.base import Message, Provider, ToolDefinition
from ..utils.error import ProviderError

logger = logging.getLogger(__name__)

_PLACEHOLDER_TEXT = "（已加载上下文摘要与恢复信息。请继续。）"

_SUMMARY_MAX_OUTPUT_TOKENS = 8192  # 九段摘要可能超 provider 默认 4096，摘要请求透传覆盖

SUMMARY_INSTRUCTION = """请将以下对话压缩为一份结构化摘要，保留所有关键信息。

分两阶段输出：
1. 先在 <analysis> 标签内写出分析草稿（该草稿会被丢弃，仅用于整理思路）。
2. 再在 <summary> 标签内写出正式摘要（唯一被保留的部分）。

正式摘要必须遵循固定的 9 部分结构：
1. 主要请求和意图
2. 关键技术概念
3. 文件和代码段
4. 错误和修复
5. 问题解决过程
6. 所有用户消息原文（逐条优先保留原文，尽量不改写）
7. 待办任务
8. 当前工作（最详细的一段：正在做什么、停在哪一步）
9. 可能的下一步

不要调用任何工具，输出纯文本。"""

# 工具结果 is_error 启发式标记：来自 mewcode/tools、mewcode/mcp 的实际错误文案前缀。
# 局限：Message 不携带 status 字段，只能靠 content 前缀推断；误判不影响摘要可用性。
_ERROR_MARKERS: tuple[str, ...] = (
    "文件不存在:",
    "不是普通文件:",
    "不是文本文件:",
    "读取失败:",
    "写入失败:",
    "未知工具:",
    "命令执行超时",
    "命令执行失败:",
    "命令退出码非零:",
    "正则表达式非法:",
    "遍历目录失败:",
    "列出文件失败:",
    "用户拒绝",
    "未连接",
    "调用超时",
    "返回错误",
    "返回非预期结果类型",
    "未找到",
    "无法确定替换",
)


def _is_error_content(content: str) -> bool:
    """启发式判断 tool 结果是否错误（前缀匹配 _ERROR_MARKERS）。"""
    return content.startswith(_ERROR_MARKERS)


def serialize_conversation(msgs: list[Message]) -> str:
    """确定性序列化对话为纯文本（同输入必同输出，供摘要模型阅读）。

    格式：user/assistant → `role: <content>`；assistant 工具调用 →
    `[call <name> id=<id> args=<json>]`；tool 结果 →
    `[result id=<id> is_error=<bool>] <content>`。
    """
    lines: list[str] = []
    for msg in msgs:
        if msg.role == "tool":
            id_ = msg.tool_use_id or msg.tool_call_id or ""
            lines.append(
                f"[result id={id_} is_error={_is_error_content(msg.content)}] {msg.content}"
            )
        else:
            lines.append(f"{msg.role}: {msg.content}")
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    args = json.dumps(
                        tc.get("arguments", {}),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    lines.append(
                        f"[call {tc.get('name', '')} id={tc.get('id', '')} args={args}]"
                    )
    return "\n".join(lines)


def build_summary_prompt(msgs: list[Message]) -> list[Message]:
    """构造摘要请求（单条 user 消息，spec F15 合并决策防连续 user 400）。"""
    serialized = serialize_conversation(msgs)
    content = SUMMARY_INSTRUCTION + "\n\n[conversation]\n" + serialized
    return [Message(role="user", content=content)]


def extract_summary(raw: str) -> str:
    """从摘要响应提取 <summary> 正文；找不到则退回原始文本（不硬失败）。"""
    matches = re.findall(r"<summary>(.*?)</summary>", raw, re.DOTALL)
    if matches:
        return matches[-1].strip()
    logger.warning("摘要响应中未找到 <summary> 标签，退回原始文本")
    return raw


def pick_recent_tail(msgs: list[Message]) -> list[Message]:
    """从尾部选近期原文：双下界（token ≥ 下界 且 条数 ≥ 下界）都满足才停，择宽（F11）。

    从尾倒序累加，第一个同时满足两个下界的截断点即返回（截断点尽量靠后=保留尽量少，
    但必须双下界全满足）。截断点落在 role=="tool"（落单 tool_result）时前推到归属的
    assistant 工具调用之前，不拆 tool_use/tool_result 对（F12）。退化情形（整段都
    不满足双下界）返回整段（择宽不放宽为「任一满足即停」）。
    """
    n = len(msgs)
    if n == 0:
        return []
    start = 0  # 默认整段（极端退化）
    acc_chars = 0
    for i in range(n - 1, -1, -1):
        acc_chars += message_chars(msgs[i : i + 1])
        if (n - i) >= RECENT_COUNT_FLOOR and math.ceil(
            acc_chars / ESTIMATE_CHARS_PER_TOKEN
        ) >= RECENT_TOKEN_FLOOR:
            start = i
            break
    # F12：截断点夹在 tool_use/tool_result 中间 → 前推到归属 assistant 之前
    if start < n and msgs[start].role == "tool":
        j = start
        while j > 0:
            j -= 1
            if msgs[j].role == "assistant" and msgs[j].tool_calls:
                start = j
                break
        else:
            # 找不到归属 assistant：防御性丢弃落单 tool 消息（前移一位）
            start = start + 1
    return list(msgs[start:])


def _join_after_summary(
    summary_and_recovery: Message, recent: list[Message]
) -> list[Message]:
    """把「摘要+恢复」单条 user 消息与近期原文拼接，保证无连续 user（spec F15）。

    - recent 空 → 只有摘要消息。
    - recent 首条是 user → 插一条 assistant 占位衔接（防 user/user 连续 400）。
    - recent 首条是 tool（落单结果）→ 防御性前移到首个非 tool 或丢弃。
    - 否则（首条 assistant）直接拼接。
    """
    if not recent:
        return [summary_and_recovery]
    if recent[0].role == "user":
        placeholder = Message(role="assistant", content=_PLACEHOLDER_TEXT)
        return [summary_and_recovery, placeholder] + recent
    if recent[0].role == "tool":
        idx = 0
        while idx < len(recent) and recent[idx].role == "tool":
            idx += 1
        if idx < len(recent):
            return [summary_and_recovery] + recent[idx:]
        return [summary_and_recovery]
    return [summary_and_recovery] + recent


@dataclass
class SummarizeConfig:
    """单次摘要行动的参数（自动/手动/紧急各有不同）。"""

    safety_margin: int  # 自动 13000 / 手动 3000 / 紧急 3000
    keep_recent_turns: int  # 保留最近轮数：自动/手动 6 / 紧急 3


@dataclass
class CompactOutcome:
    """一次压缩行动的结果（供 TUI 展示与主循环决策）。"""

    triggered: bool  # 是否真的执行了压缩（手动/紧急总是 True）
    before_tokens: int  # 压缩前估算
    after_tokens: int  # 压缩后估算
    replaced_results: int  # 被第一层替换的工具结果数（由 ContextManager 在 L1 后回填）
    success: bool  # 本次行动是否成功
    failure_reason: str  # 失败原因分类（"network"/"api"/"prompt_too_long"/"exception"）
    messages: list[Message] | None  # 成功时的新消息列表；失败时为 None


def _expand_to_turns(
    messages: list[Message], recent_tail: list[Message], keep_recent_turns: int
) -> list[Message]:
    """把近期原文从「双下界尾部」扩展为「至少 keep_recent_turns 轮」。

    起点取双下界起点与「倒数第 keep_recent_turns 条 user 消息」起点中的更早者，
    保证最近 N 轮原文整体保留、中间无消息丢失（丢消息会丢信息，违反「不丢内容」）。
    """
    tail_start = len(messages) - len(recent_tail)
    i = len(messages) - 1
    user_count = 0
    while i >= 0:
        if messages[i].role == "user":
            user_count += 1
            if user_count == keep_recent_turns:
                break
        i -= 1
    turn_start = max(0, i)
    return list(messages[min(tail_start, turn_start) :])


def _classify_error(err: Exception) -> str:
    """失败原因分类（启发式：provider 错误文本含网络关键词判 network）。"""
    if isinstance(err, PromptTooLongError):
        return "prompt_too_long"
    if isinstance(err, ProviderError):
        msg = str(err)
        if any(
            k in msg
            for k in ("超时", "timeout", "连接", "connection", "network", "Network")
        ):
            return "network"
        return "api"
    return "exception"


class Summarizer:
    """第二层 LLM 全量摘要（spec F6–F14），含 F23 自检与 F27 丢组重试。

    注：构造多收 file_tracker——恢复段三块需要文件快照数据，而 summarize 签名
    不含 file_tracker（task T14 定义 __init__(provider, recovery_builder)，
    此处补第三参使恢复段可构造，属实现层必要偏差）。
    """

    def __init__(
        self,
        provider: Provider,
        recovery_builder: RecoveryBuilder,
        file_tracker: FileTracker,
        trace_factory: Callable[[list[Message]], dict[str, object] | None]
        | None = None,
    ) -> None:
        self._provider = provider
        self._recovery_builder = recovery_builder
        self._file_tracker = file_tracker
        self._trace_factory = trace_factory

    async def summarize(
        self,
        messages: list[Message],
        config: SummarizeConfig,
        context_window: int,
        tool_defs: list[ToolDefinition],
    ) -> CompactOutcome:
        """对 messages 执行一次摘要压缩，返回新消息列表（成功时）。

        待摘要旧块 = 全量消息去掉近期原文；近期原文 = 双下界尾部（F11/F12）扩展
        到至少 keep_recent_turns 轮。摘要请求不传工具（F8）、输出上限 8192。
        """
        before = estimate_messages(messages)
        try:
            recent_tail = pick_recent_tail(messages)
            recent_tail = _expand_to_turns(
                messages, recent_tail, config.keep_recent_turns
            )
            old_block = messages[: len(messages) - len(recent_tail)]
            if not old_block:
                # 退化：全对话都在「近期原文」范围内（< RECENT_TOKEN_FLOOR），
                # 无需摘要——直接以近期原文为新历史，不塞空摘要消息（F11 语义）。
                return CompactOutcome(
                    True,
                    before,
                    estimate_messages(recent_tail),
                    0,
                    True,
                    "",
                    recent_tail,
                )
            summary_text = await self._run_with_retry(old_block, context_window, config)
            new_msgs = await self._assemble_new_messages(
                summary_text, recent_tail, tool_defs
            )
            return CompactOutcome(
                True, before, estimate_messages(new_msgs), 0, True, "", new_msgs
            )
        except Exception as e:  # 单次摘要失败不崩进程（N11）
            logger.exception("摘要行动失败")
            return CompactOutcome(
                True, before, before, 0, False, _classify_error(e), None
            )

    async def _run_with_retry(
        self, old_block: list[Message], context_window: int, config: SummarizeConfig
    ) -> str:
        """发一次摘要请求；撞 PTL 走 F27；非 PTL 错误上抛（外层转失败 outcome）。"""
        prompt_msgs = build_summary_prompt(old_block)
        if estimate_messages(prompt_msgs) > (
            context_window - SUMMARY_RESERVE_TOKENS - config.safety_margin
        ):
            # F23 自检：预估必然超窗 → 直接进丢组重试，不白白撞墙
            return await self._ptl_retry(old_block, None)
        text, err = await self._run_once(old_block)
        if err is not None:
            if isinstance(err, PromptTooLongError):
                return await self._ptl_retry(old_block, err)
            raise err
        return text

    async def _run_once(self, msgs: list[Message]) -> tuple[str, Exception | None]:
        """发一次摘要请求，返回 (extract_summary 文本, 错误)。错误为 None 表示成功。

        请求体：stable_prompt=指令、messages=旧块原始消息（不传工具 F8）、
        max_output_tokens=8192（F9 九段摘要超 provider 默认 4096）。
        """
        payload = PromptPayload(
            stable_prompt=SUMMARY_INSTRUCTION,
            env_segment="",
            messages=msgs,
            tools=None,
            max_output_tokens=_SUMMARY_MAX_OUTPUT_TOKENS,
            trace_context=(self._trace_factory(msgs) if self._trace_factory else None),
        )
        buffer = ""
        stream = self._provider.stream(payload)
        async for se in stream:
            if se.text:
                buffer += se.text
            elif se.err:
                return ("", se.err)
            elif se.done:
                break
        return (extract_summary(buffer), None)

    async def _ptl_retry(
        self, messages: list[Message], first_err: Exception | None
    ) -> str:
        """F27：摘要请求自身 PTL 的统一处理（自动/手动/紧急三路径共用）。

        按 user 分界分组 → 最多 PTL_DIRECT_RETRY_LIMIT 次每次丢最旧 1 组直接重试 →
        之后每次丢 ceil(剩余×PTL_DROP_RATIO)（至少 1 组）直到成功或无组可丢。
        不发送空 messages 的摘要请求；非 PTL 错误立即上抛；耗尽抛最近 err。
        """
        groups = MessageGroupDropper.group_by_user(messages)
        last_err = first_err

        # 阶段一：3 次直接重试，每次丢最旧 1 组
        for _ in range(PTL_DIRECT_RETRY_LIMIT):
            if len(groups) <= 1:
                break
            groups = MessageGroupDropper.drop_oldest(groups, 1)
            flat = [m for g in groups for m in g]
            text, err = await self._run_once(flat)
            if err is None:
                return text
            if not isinstance(err, PromptTooLongError):
                raise err
            last_err = err

        # 阶段二：比例丢弃（每次 ceil(剩余×0.2)，至少 1 组）直到成功或无组可丢
        while len(groups) > 1:
            groups = MessageGroupDropper.drop_ratio(groups, PTL_DROP_RATIO)
            if not groups:
                break
            flat = [m for g in groups for m in g]
            text, err = await self._run_once(flat)
            if err is None:
                return text
            if not isinstance(err, PromptTooLongError):
                raise err
            last_err = err

        if last_err is None:
            last_err = PromptTooLongError("摘要请求持续超长")
        raise last_err

    async def _assemble_new_messages(
        self,
        summary_text: str,
        recent_tail: list[Message],
        tool_defs: list[ToolDefinition],
    ) -> list[Message]:
        """摘要正文 + 恢复三块拼成单条 user 消息，再接近期原文（role 衔接修正，F15）。"""
        bundle = await self._recovery_builder.build(self._file_tracker, tool_defs)
        recovery_text = (
            f"{bundle.file_snapshots_text}\n\n"
            f"{bundle.tools_declaration_text}\n\n"
            f"{bundle.boundary_notice_text}"
        )
        if bundle.skill_activation_text:
            recovery_text = f"{recovery_text}\n\n{bundle.skill_activation_text}"
        combined = summary_text + "\n\n" + recovery_text
        return _join_after_summary(Message(role="user", content=combined), recent_tail)
