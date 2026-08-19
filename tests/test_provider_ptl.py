"""Provider PTL 哨兵包装单测（ch08 T33，spec F25/F29，AC 相关）。

防 bug：PTL 误判/漏判、__cause__ 丢失、非 PTL 错误被误触发紧急压缩。
直接测 _wrap_*_error 纯函数（不实际发请求，mock SDK 异常对象）。
"""

from mewcode.llm import PromptTooLongError
from mewcode.provider.anthropic import _wrap_anthropic_error
from mewcode.provider.openai import _wrap_openai_error
from mewcode.utils.error import ProviderError


class _FakeAnthropicError(Exception):
    """仿 anthropic.APIError：status_code + message，str() 返回 message。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message

    def __str__(self) -> str:
        return self.message


class _FakeOpenAIError(Exception):
    """仿 openai.APIError：status_code + code + message。"""

    def __init__(self, status_code: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code

    def __str__(self) -> str:
        return self.message


def _anthropic_err(status, msg):
    return _FakeAnthropicError(status, msg)


def _openai_err(status, msg, code=None):
    return _FakeOpenAIError(status, msg, code=code)


def test_anthropic_ptl_wrapped():
    """AC：Anthropic 400 + 'prompt is too long' → PromptTooLongError。"""
    e = _anthropic_err(400, "prompt is too long: 10000 tokens > 8192 maximum")
    wrapped = _wrap_anthropic_error(e)
    assert isinstance(wrapped, PromptTooLongError)
    assert wrapped.__cause__ is e, "__cause__ 应保留原异常"


def test_anthropic_ptl_context_length_keyword():
    """AC：Anthropic 400 + 'context length' 关键词 → PTL。"""
    e = _anthropic_err(400, "context length exceeded")
    wrapped = _wrap_anthropic_error(e)
    assert isinstance(wrapped, PromptTooLongError)


def test_openai_ptl_wrapped():
    """AC：OpenAI 400 + code context_length_exceeded → PTL。"""
    e = _openai_err(400, "maximum context length", code="context_length_exceeded")
    wrapped = _wrap_openai_error(e)
    assert isinstance(wrapped, PromptTooLongError)
    assert wrapped.__cause__ is e


def test_openai_ptl_keyword_fallback():
    """AC：OpenAI 400 + 'maximum context length' 文案 → PTL（code 缺失时兜底）。"""
    e = _openai_err(400, "This model's maximum context length is 8192 tokens", code=None)
    wrapped = _wrap_openai_error(e)
    assert isinstance(wrapped, PromptTooLongError)


def test_non_ptl_not_wrapped():
    """防 bug：其他 4xx/5xx 被误判 PTL → 触发无谓紧急压缩。

    非 PTL 的 400/500 应返回 ProviderError，不是 PromptTooLongError。
    """
    # Anthropic 400 但非 PTL 文案
    e1 = _anthropic_err(400, "invalid request: bad parameter")
    assert isinstance(_wrap_anthropic_error(e1), ProviderError)
    assert not isinstance(_wrap_anthropic_error(e1), PromptTooLongError)
    # Anthropic 500
    e2 = _anthropic_err(500, "internal server error")
    assert isinstance(_wrap_anthropic_error(e2), ProviderError)
    # OpenAI 400 非 PTL
    e3 = _openai_err(400, "invalid model", code="invalid_model")
    assert isinstance(_wrap_openai_error(e3), ProviderError)
    # OpenAI 500
    e4 = _openai_err(500, "server error")
    assert isinstance(_wrap_openai_error(e4), ProviderError)


def test_cause_preserved():
    """AC：wrapped.__cause__ 是原 SDK 异常（保留调试信息）。"""
    e = _anthropic_err(400, "prompt is too long")
    wrapped = _wrap_anthropic_error(e)
    assert wrapped.__cause__ is e
    e2 = _openai_err(400, "context length", code="context_length_exceeded")
    wrapped2 = _wrap_openai_error(e2)
    assert wrapped2.__cause__ is e2
