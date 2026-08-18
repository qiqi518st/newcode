"""Token 估算——纯函数模型（spec F13/F14：锚定真实 usage + 字符/3.5 增量）。

锚点（usage_anchor）与 anchor_msg_len 由调用方（ContextManager）外部跟踪，本模块无状态。
"""

import json
import math

from ..context.constants import ESTIMATE_CHARS_PER_TOKEN
from ..provider.base import Message, TokenUsage


def usage_to_anchor(usage: TokenUsage) -> int:
    """单次调用 usage 合并为锚点值：input+output+cache_creation+cache_read。

    spec F14 锚点**替换不累加**——每次用最新 usage 直接替换锚点。
    """
    return (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_creation_input_tokens
        + usage.cache_read_input_tokens
    )


def message_chars(msgs: list[Message]) -> int:
    """单段消息列表的字节总量：content 字节 + tool_calls 序列化字节。"""
    total = 0
    for msg in msgs:
        total += len(msg.content.encode("utf-8"))
        if msg.tool_calls:
            total += len(
                json.dumps(msg.tool_calls, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
    return total


def estimate_tokens(anchor: int, all_msgs: list[Message], anchor_msg_len: int) -> int:
    """锚定最近 usage + 之后新增消息的字符增量。

    入参：anchor = 上次主对话 stream 真实 usage 之和；anchor_msg_len = 锚点记录时
    conv 的消息条数。只把 all_msgs[anchor_msg_len:] 的字符算进去，避免重复计算已含
    在 anchor 里的历史（spec F13/F14）。
    注意：all_msgs 必须是 L1（offload_and_snip）之后的消息列表，否则估算偏高、
    过早触发 L2。anchor=0 且 anchor_msg_len=0（首轮/摘要后重置）时退化为纯字符估算。
    """
    tail = all_msgs[max(0, anchor_msg_len):]
    return anchor + math.ceil(message_chars(tail) / ESTIMATE_CHARS_PER_TOKEN)


def estimate_messages(messages: list[Message]) -> int:
    """纯按字符/3.5 估一批消息（摘要请求自检用，spec F23）。"""
    return math.ceil(message_chars(messages) / ESTIMATE_CHARS_PER_TOKEN)
