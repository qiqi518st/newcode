"""parse_command 解析器单测（T3）：表驱动覆盖完整边界。

防的 bug：strip/大小写/参数保留/退化形态（"/"、"/ /help"）解析错误，
以及 dispatch 消费退化形态时拼出 `"未知命令: /, ..."` 悬空斜杠文案。
"""

from mewcode.slash.parser import parse_command

CASES = [
    # (输入, 期望 (name, args) 或 None)
    ("", None),
    ("   ", None),
    ("hello", None),
    ("hello world", None),
    ("/", ("", "")),  # 退化：单独斜杠
    ("/  ", ("", "")),  # 退化：斜杠+空白
    ("/help", ("help", "")),
    ("  /HELP  ", ("help", "")),  # strip 前导/尾随空白，name 小写
    ("/help xx", ("help", "xx")),  # 参数保留
    ("/help  ", ("help", "")),  # 尾随空白视为无参
    ("/help   xx  ", ("help", "  xx")),  # 参数保留（第一个空格后的原样剩余）
    ("//double", ("/double", "")),  # 永不匹配任何注册名 → 未命中
    ("/ /help", ("", "/help")),  # name 空 → 未命中
    ("/MEMORY add x", ("memory", "add x")),
    (
        "/session_resume 20260820-120000-abcd",
        ("session_resume", "20260820-120000-abcd"),
    ),
    (
        "/memory_add user_preference 记住 tea",
        ("memory_add", "user_preference 记住 tea"),
    ),
]


def test_parse_cases():
    for text, expected in CASES:
        assert parse_command(text) == expected, (
            f"{text!r} → {parse_command(text)!r}, 期望 {expected!r}"
        )


def test_parse_non_str_returns_none():
    assert parse_command(None) is None
    assert parse_command(123) is None
