"""F27 丢消息组：按 user 消息分界分组，整组丢弃保住 tool_use/tool_result 配对。"""

import math

from ..provider.base import Message


class MessageGroupDropper:
    """摘要请求自身 PTL 时的丢消息组工具（F27，三路径共用底层）。"""

    @staticmethod
    def group_by_user(messages: list[Message]) -> list[list[Message]]:
        """按 user 消息分界分组：一条 user + 其后到下一条 user 之前为一组。

        注意按 Message.role 原始值分组（role=="tool" 归其前的 user 组；
        assistant 归当前组）；第一条不是 user 时单独成组防丢失。
        """
        groups: list[list[Message]] = []
        for msg in messages:
            if msg.role == "user":
                groups.append([msg])
            else:
                if not groups:
                    groups.append([])
                groups[-1].append(msg)
        return groups

    @staticmethod
    def drop_oldest(groups: list[list[Message]], n: int) -> list[list[Message]]:
        """丢弃最旧 n 组。"""
        return groups[n:]

    @staticmethod
    def drop_ratio(groups: list[list[Message]], ratio: float) -> list[list[Message]]:
        """丢弃 ceil(剩余 × ratio) 组，至少 1 组（空列表返回空）。"""
        if not groups:
            return []
        n = max(1, math.ceil(len(groups) * ratio))
        return groups[n:]
