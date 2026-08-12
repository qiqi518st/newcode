"""缓存字段解析测试（ch05，spec F8 / N1 健壮解析）"""

from types import SimpleNamespace

from mewcode.provider.anthropic import _to_usage as anthropic_usage
from mewcode.provider.base import TokenUsage
from mewcode.provider.openai import _to_usage as openai_usage


def _anthropic_usage(has_cache=True):
    """构造 Anthropic 风格 usage 对象"""
    kw = {"input_tokens": 10, "output_tokens": 5}
    if has_cache:
        kw.update(
            {
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 200,
            }
        )
    return SimpleNamespace(**kw)


def _openai_usage(has_details=True):
    """构造 OpenAI 风格 usage 对象"""
    kw = {"prompt_tokens": 10, "completion_tokens": 5}
    if has_details:
        kw["prompt_tokens_details"] = SimpleNamespace(cached_tokens=7)
    return SimpleNamespace(**kw)


class TestAnthropicCacheParse:
    """Anthropic 缓存创建/读取字段解析"""

    def test_parses_cache_fields(self):
        u = anthropic_usage(_anthropic_usage(has_cache=True))
        assert u == TokenUsage(
            10, 5, cache_creation_input_tokens=100, cache_read_input_tokens=200
        )

    def test_missing_cache_fields_zero(self):
        """兼容端点缺缓存字段 → 按 0 处理，不抛异常（N1）"""
        u = anthropic_usage(_anthropic_usage(has_cache=False))
        assert u == TokenUsage(10, 5)
        assert u.cache_read_input_tokens == 0

    def test_none_input_degrades(self):
        """字段为 None → 按 0"""
        u = anthropic_usage(SimpleNamespace(input_tokens=None, output_tokens=None))
        assert u.input_tokens == 0 and u.output_tokens == 0


class TestOpenAICacheParse:
    """OpenAI cached_tokens 解析"""

    def test_parses_cached_tokens(self):
        u = openai_usage(_openai_usage(has_details=True))
        assert u == TokenUsage(10, 5, cache_read_input_tokens=7)

    def test_missing_details_zero(self):
        """端点不返回 prompt_tokens_details → cache_read=0（N1）"""
        u = openai_usage(_openai_usage(has_details=False))
        assert u == TokenUsage(10, 5)
        assert u.cache_read_input_tokens == 0

    def test_creation_not_exposed(self):
        """OpenAI 不暴露缓存写入字段，cache_creation 恒 0"""
        u = openai_usage(_openai_usage(has_details=True))
        assert u.cache_creation_input_tokens == 0
