"""LLM 层公共原语。

ch08 新增 PromptTooLongError 哨兵异常：provider 适配层把 prompt_too_long 类错误
包装成该异常经 StreamEvent.err 投递，Agent / Summarizer 用 isinstance 判定，
用于识别「上下文超窗」并走 ForceCompact / F27 丢组重试路径。
"""


class PromptTooLongError(Exception):
    """Provider 上报上下文超出窗口时统一抛出的哨兵异常。

    __cause__ 保留原始 SDK 异常（provider 适配层 wrap 时设置）。
    """
